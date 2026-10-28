import re
import time
import uuid
import secrets
import threading
import hashlib

from _thread import LockType
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from hermes_cloud_tools.packages import ALL_TOOLS, ToolPackages

from .log_policy import LogPolicy, log_context
from .models import IncidentContext, PolicyDenied, ResourceContext
from .policy import RequestPolicy, fields, integer, require, wall_time_ms
from .process import Deadline
from .receipts import references_for, select_receipts
from .resource_policy import ResourcePolicy, profile_context
from .results import bounded_result
from .wire import ExecutionError, encode

if TYPE_CHECKING:
    from investigator.executor.audit import AuditLog
    from investigator.executor.aws import AwsBackend


@dataclass
class Session:
    policy: RequestPolicy | ResourcePolicy
    context: IncidentContext | ResourceContext
    monotonic_end: float
    owner_uid: int = 0
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    environment: dict[str, str] = field(default_factory=dict, repr=False)
    credentials_until: float = field(default=0, repr=False)
    receipts: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    failures: list[dict[str, Any]] = field(default_factory=list, repr=False)
    lock: LockType = field(default_factory=threading.Lock, repr=False)


class Executor:
    """Contexts exist only in this process; restart revokes every token."""

    def __init__(
        self,
        config: dict[str, Any],
        backend: AwsBackend,
        audit: AuditLog,
        clock: Callable[[], int] = wall_time_ms,
    ) -> None:
        self.config = dict(config)
        self.packages = ToolPackages.from_executor_config(config)
        self.cloudtrail_resources = (
            self.packages.cloudtrail_resources
            if "hermes-cloudtrail" in self.packages.enabled
            else ()
        )
        self.backend = backend
        self.audit = audit
        self.clock = clock
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()

    def register(self, request: object, owner_uid: int = 0) -> dict[str, Any]:
        if type(request) is dict and "expected_source" in request:
            expected = request["expected_source"]

            fields(expected, {"account_id", "region", "log_group"}, set())
            require(
                all(expected[key] == self.config[key] for key in expected),
                "source_scope_changed",
            )

            request = {
                key: value for key, value in request.items() if key != "expected_source"
            }

        if type(request) is dict and "observation_id" in request:
            context, ttl = log_context(self.config, request, self.clock())
            return self._store(
                LogPolicy(
                    context,
                    request["observation_id"],
                    clock=self.clock,
                    cloudtrail_resources=self.cloudtrail_resources,
                ),
                context,
                ttl,
                owner_uid,
            )

        if type(request) is dict and "profile" in request:
            require(owner_uid == 0, "resource_scope_forbidden")
            context, ttl = profile_context(self.config, request, self.clock())
            return self._store(
                ResourcePolicy(
                    context,
                    clock=self.clock,
                    cloudtrail_resources=self.cloudtrail_resources,
                ),
                context,
                ttl,
                owner_uid,
            )

        request = fields(
            request,
            {"run_id", "attempt_ids", "incident_time_ms"},
            {"ttl_seconds"},
        )

        attempts = request["attempt_ids"]

        require(type(attempts) is list and len(attempts) <= 20, "invalid_context")

        ttl = integer(request.get("ttl_seconds", 300), 1, 300, "invalid_ttl")
        context = IncidentContext(
            account_id=self.config["account_id"],
            region=self.config["region"],
            bucket=self.config["bucket"],
            log_group=self.config["log_group"],
            run_id=request["run_id"],
            attempt_ids=tuple(attempts),
            incident_time_ms=request["incident_time_ms"],
            expires_at_ms=self.clock() + ttl * 1000,
        )

        policy = RequestPolicy(
            context,
            clock=self.clock,
            cloudtrail_resources=self.cloudtrail_resources,
        )
        return self._store(policy, context, ttl, owner_uid)

    def _store(
        self,
        policy: RequestPolicy | ResourcePolicy,
        context: IncidentContext | ResourceContext,
        ttl: int,
        owner_uid: int,
    ) -> dict[str, Any]:
        with self.lock:
            for token, session in list(self.sessions.items()):
                if self._expired(session):
                    del self.sessions[token]

            require(len(self.sessions) < 8, "context_limit")

            token = secrets.token_hex(32)
            session = Session(policy, context, time.monotonic() + ttl, owner_uid)

            self.audit.write(
                {
                    "time_ms": self.clock(),
                    "status": "context_opened",
                    "run_id": getattr(policy, "incident_id", context.run_id),
                    "session_id": session.session_id,
                }
            )

            self.sessions[token] = session

        return {
            "context_id": token,
            "expires_at_ms": context.expires_at_ms,
            "session_id": self.sessions[token].session_id,
        }

    def _expired(self, session: Session) -> bool:
        return (
            self.clock() >= session.context.expires_at_ms
            or time.monotonic() >= session.monotonic_end
        )

    def revoke(self, token: str, owner_uid: int | None = None) -> dict[str, bool]:
        session = self._session(token, allow_expired=True, owner_uid=owner_uid)
        # Wait for an in-flight call before acknowledging revocation.
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

    def status(self, token: str, owner_uid: int | None = None) -> dict[str, bool | int]:
        session = self._session(token, owner_uid=owner_uid)

        return {"active": True, "expires_at_ms": session.context.expires_at_ms}

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
            type(token) is str and re.fullmatch(r"[a-f0-9]{64}", token) is not None,
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
                request = fields(envelope["request"], {"tool", "arguments"}, set())
                tool = request["tool"]

                if type(tool) is str and tool in ALL_TOOLS:
                    require(tool in self.packages.tools, "tool_disabled")

                action = session.policy.authorize(request)

                receipt.update({"tool": action.tool, "operation": action.operation})

                source = {
                    "account_id": action.account_id,
                    "region": action.region,
                    **dict(action.parameters),
                }

                self.audit.write({**receipt, "status": "authorized", "source": source})

                authorized = True
                deadline = Deadline(
                    min(
                        action.timeout_seconds,
                        session.monotonic_end - time.monotonic(),
                    ),
                    action.expires_at_ms,
                    self.clock,
                )

                deadline.remaining()

                if action.operation == "DescribeResources":
                    if not isinstance(session.policy, ResourcePolicy):
                        raise ExecutionError("invalid_execution_plan")

                    response = session.policy.describe_resources(action.tool)
                    response["enabled_tools"] = sorted(self.packages.tools)
                    body = None
                else:
                    if (
                        not session.environment
                        or time.monotonic() >= session.credentials_until
                    ):
                        session.environment = self.backend.assume(deadline)
                        # Refresh credentials before their 15-minute expiry;
                        # the policy and its reservations remain unchanged.
                        session.credentials_until = time.monotonic() + 840

                    deadline.remaining()

                    response, body = self.backend.execute(
                        action, session.environment, deadline
                    )

                data, truncated = bounded_result(action, response, body)

                deadline.remaining()

                encoded = encode(data)

                if len(encoded) > action.max_bytes:
                    raise ExecutionError("response_budget_exceeded")

                digest = hashlib.sha256(encoded).hexdigest()
                references, limited = references_for(action, data, truncated)
                result = {
                    "ok": True,
                    "request_id": request_id,
                    "evidence_id": request_id,
                    "observed_at_ms": self.clock(),
                    "source": source,
                    "data": data,
                    "data_bytes": len(encoded),
                    "sha256": digest,
                    "truncated": bool(truncated or limited),
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

                session.receipts[request_id] = {
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
                    session.failures.append({"request_id": request_id, "code": code})

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
