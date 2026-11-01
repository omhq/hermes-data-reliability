import copy
import hashlib
import re
import secrets
import threading
import time
import uuid
from _thread import LockType
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from .loader import ContextLoader, load_tools
from .models import IncidentContext, PolicyDenied, ResourceContext
from .policy import RequestPolicy
from .process import Deadline
from .receipts import select_receipts
from .registered import REGISTRY
from .registry import ToolRegistry
from .resource_policy import ResourcePolicy
from .validation import fields, require, wall_time_ms
from .wire import ExecutionError, encode

if TYPE_CHECKING:
    from .contracts import AuditSink, ReadBackend, ToolExtension


@dataclass
class Session:
    policy: RequestPolicy | ResourcePolicy
    context: IncidentContext | ResourceContext
    monotonic_end: float
    owner_uid: int = 0
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    environment: dict[str, str] = field(default_factory=dict, repr=False)
    credentials_until: float = field(default=0, repr=False)
    receipts: dict[str, dict[str, Any]] = field(
        default_factory=dict, repr=False
    )
    failures: list[dict[str, Any]] = field(default_factory=list, repr=False)
    lock: LockType = field(default_factory=threading.Lock, repr=False)
    revoked: threading.Event = field(
        default_factory=threading.Event, repr=False
    )


class Executor:
    """Contexts exist only in this process; restart revokes every token."""

    def __init__(
        self,
        config: dict[str, Any],
        backend: ReadBackend,
        audit: AuditSink,
        clock: Callable[[], int] = wall_time_ms,
        extension: ToolExtension | None = None,
        registry: ToolRegistry = REGISTRY,
    ) -> None:
        self.config = copy.deepcopy(config)
        self.providers = load_tools(self.config, extension, registry=registry)
        self.packages = self.providers.selection
        self.backend = backend
        self.audit = audit
        self.clock = clock
        self.extension = extension
        self.contexts = ContextLoader(
            self.config, self.providers, self.clock, extension
        )
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()

    def register(self, request: object, owner_uid: int = 0) -> dict[str, Any]:
        grant = self.contexts.open(request, owner_uid)
        return self._store(grant.policy, grant.context, grant.ttl, owner_uid)

    def _store(
        self,
        policy: RequestPolicy | ResourcePolicy,
        context: IncidentContext | ResourceContext,
        ttl: int,
        owner_uid: int,
    ) -> dict[str, Any]:
        policy.bind(self.providers)

        with self.lock:
            for token, session in list(self.sessions.items()):
                if self._expired(session):
                    del self.sessions[token]

            require(len(self.sessions) < 8, "context_limit")

            token = secrets.token_hex(32)
            session = Session(
                policy, context, time.monotonic() + ttl, owner_uid
            )

            self.audit.write(
                {
                    "time_ms": self.clock(),
                    "status": "context_opened",
                    "run_id": getattr(policy, "incident_id", context.run_id),
                    "session_id": session.session_id,
                    "owner_uid": owner_uid,
                }
            )

            self.sessions[token] = session

        return {
            "context_id": token,
            "expires_at_ms": context.expires_at_ms,
            "session_id": session.session_id,
        }

    def _expired(self, session: Session) -> bool:
        return (
            session.revoked.is_set()
            or self.clock() >= session.context.expires_at_ms
            or time.monotonic() >= session.monotonic_end
        )

    def revoke(
        self, token: str, owner_uid: int | None = None
    ) -> dict[str, bool]:
        session = self._session(token, allow_expired=True, owner_uid=owner_uid)
        # Stop queued calls immediately; wait for the active call to drain.
        session.revoked.set()

        with session.lock, self.lock:
            self.sessions.pop(token, None)
            session.environment.clear()
            session.receipts.clear()
            session.monotonic_end = 0

            self.audit.write(
                {
                    "time_ms": self.clock(),
                    "status": "context_closed",
                    "session_id": session.session_id,
                }
            )

        return {"revoked": True}

    def status(
        self, token: str, owner_uid: int | None = None
    ) -> dict[str, bool | int]:
        session = self._session(token, owner_uid=owner_uid)

        with session.lock:
            require(not self._expired(session), "context_expired")
            return {
                "active": True,
                "expires_at_ms": session.context.expires_at_ms,
            }

    def evidence(
        self, token: str, citations: object, owner_uid: int | None = None
    ) -> dict[str, Any]:
        session = self._session(token, owner_uid=owner_uid)

        with session.lock:
            require(not self._expired(session), "context_expired")
            return select_receipts(session, citations)

    def _session(
        self,
        token: str,
        allow_expired: bool = False,
        owner_uid: int | None = None,
    ) -> Session:
        require(
            type(token) is str
            and re.fullmatch(r"[a-f0-9]{64}", token) is not None,
            "unknown_context",
        )

        with self.lock:
            session = self.sessions.get(token)

        if session is None:
            raise PolicyDenied("unknown_context")

        require(
            owner_uid is None or session.owner_uid == owner_uid,
            "context_owner_mismatch",
        )
        require(allow_expired or not self._expired(session), "context_expired")

        return session

    def execute(self, envelope: object) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        receipt = {"request_id": request_id, "time_ms": self.clock()}
        authorized = False
        session = None

        try:
            envelope = fields(envelope, {"context_id", "request"}, set())
            session = self._session(envelope["context_id"])

            with session.lock:
                require(not self._expired(session), "context_expired")

                receipt["run_id"] = getattr(
                    session.policy, "incident_id", session.context.run_id
                )
                receipt["session_id"] = session.session_id
                receipt["owner_uid"] = session.owner_uid
                request = fields(
                    envelope["request"], {"tool", "arguments"}, set()
                )
                tool = request["tool"]

                provider = self.providers.tool(tool)

                action = session.policy.authorize(request)

                receipt.update(
                    {"tool": action.tool, "operation": action.operation}
                )

                source = {
                    "account_id": action.account_id,
                    "region": action.region,
                    **action.parameters_dict(),
                }

                self.audit.write(
                    {**receipt, "status": "authorized", "source": source}
                )

                authorized = True
                deadline = Deadline(
                    min(
                        action.timeout_seconds,
                        session.monotonic_end - time.monotonic(),
                    ),
                    action.expires_at_ms,
                    self.clock,
                    cancelled=session.revoked.is_set,
                )

                deadline.remaining()

                evidence = provider.read(
                    session,
                    action,
                    self.backend,
                    deadline,
                    self.clock() // 1000,
                ).bounded()
                data = evidence.data
                truncated = evidence.truncated
                references = evidence.references

                deadline.remaining()

                encoded = encode(data)

                if len(encoded) > action.max_bytes:
                    raise ExecutionError("response_budget_exceeded")

                digest = hashlib.sha256(encoded).hexdigest()
                result = {
                    "ok": True,
                    "request_id": request_id,
                    "evidence_id": request_id,
                    "observed_at_ms": self.clock(),
                    "source": source,
                    "data": data,
                    "data_bytes": len(encoded),
                    "sha256": digest,
                    "truncated": truncated,
                    "untrusted_evidence": True,
                    "references": references,
                }

                if len(encode(result)) > 270336:
                    raise ExecutionError("response_budget_exceeded")

                self.audit.write(
                    {
                        **receipt,
                        "status": "completed",
                        "evidence_id": request_id,
                        "sha256": digest,
                        "data_bytes": len(encoded),
                        "truncated": result["truncated"],
                    }
                )

                deadline.remaining()

                session.receipts[request_id] = copy.deepcopy(
                    {
                        key: result[key]
                        for key in (
                            "evidence_id",
                            "observed_at_ms",
                            "source",
                            "sha256",
                            "data_bytes",
                            "truncated",
                            "references",
                        )
                    }
                )
                session.receipts[request_id].update(
                    session_id=session.session_id,
                    run_id=getattr(
                        session.policy, "incident_id", session.context.run_id
                    ),
                )

                return result
        except (PolicyDenied, ExecutionError) as error:
            code = error.code
        except Exception:
            code = "executor_failure"

        if session is not None:
            with session.lock:
                if len(session.failures) < 20:
                    session.failures.append(
                        {"request_id": request_id, "code": code}
                    )

        # Audit failure also fails closed. No successful evidence is released.
        try:
            self.audit.write(
                {
                    **receipt,
                    "status": "failed" if authorized else "denied",
                    "code": code,
                }
            )
        except Exception:
            code = "audit_unavailable"

        return {"ok": False, "request_id": request_id, "error": code}
