import re
import time
import threading

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable

from .models import (
    ApprovedAction,
    IncidentContext,
    PolicyDenied,
    PolicyLimits,
    ResourceContext,
)

if TYPE_CHECKING:
    from investigator.executor.models import ResolvedRead


TOOLS = frozenset({"search_run_logs", "list_run_objects", "read_run_object"})


def require(condition: bool, code: str) -> None:
    if not condition:
        raise PolicyDenied(code)


def integer(value: object, minimum: int, maximum: int, code: str) -> int:
    # bool is a subclass of int. It is not a valid count or timestamp.
    if type(value) is not int or not minimum <= value <= maximum:
        raise PolicyDenied(code)

    return value


def fields(value: object, required: set[str], optional: set[str]) -> dict[str, Any]:
    """Return validated request fields without coercing untrusted values."""
    if type(value) is not dict:
        raise PolicyDenied("invalid_arguments")

    require(all(type(key) is str for key in value), "invalid_arguments")
    require(required <= value.keys(), "missing_arguments")
    require(value.keys() <= required | optional, "unexpected_arguments")
    return value


def validate_context(context: IncidentContext, limits: PolicyLimits) -> None:
    require(type(context) is IncidentContext, "invalid_context")

    for value, pattern in (
        (context.account_id, r"[0-9]{12}"),
        (context.region, r"[a-z]{2}(?:-[a-z]+)+-[0-9]+"),
        (context.bucket, r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]"),
        (context.log_group, r"[./A-Za-z0-9_#-]{1,512}"),
        (context.run_id, r"[a-f0-9]{24}"),
    ):
        require(
            type(value) is str and re.fullmatch(pattern, value) is not None,
            "invalid_context",
        )

    require(type(context.attempt_ids) is tuple, "invalid_context")
    require(
        all(
            type(value) is str and re.fullmatch(r"[A-Za-z0-9-]{1,80}", value)
            for value in context.attempt_ids
        ),
        "invalid_context",
    )

    for value in (context.incident_time_ms, context.expires_at_ms):
        require(type(value) is int and value > 0, "invalid_context")

    require(type(limits) is PolicyLimits, "invalid_limits")

    for value in vars(limits).values():
        require(type(value) is int and value > 0, "invalid_limits")

    require(
        limits.default_bytes <= limits.max_bytes <= limits.total_bytes,
        "invalid_limits",
    )


def wall_time_ms() -> int:
    return time.time_ns() // 1_000_000


class ReadPolicy[Context: IncidentContext | ResourceContext](ABC):
    """Share authorization locking and budgets across trusted context types.

    Authorized calls reserve their full byte allowance even when AWS fails
    or returns less data. Process-local counters and session tokens expire
    with the executor process rather than silently renewing on restart.
    """

    def _initialize(
        self,
        context: Context,
        limits: PolicyLimits,
        clock: Callable[[], int],
    ) -> None:
        self._context = context
        self._limits = limits
        self._clock = clock
        self._calls = 0
        self._reserved_bytes = 0
        self._lock = threading.Lock()

    def authorize(self, request: object) -> ApprovedAction:
        """Deny invalid requests or policy failures before any execution."""
        with self._lock:
            try:
                return self._authorize(request)
            except PolicyDenied:
                raise
            except Exception:
                raise PolicyDenied("policy_error") from None

    @abstractmethod
    def _authorize(self, request: object) -> ApprovedAction:
        """Resolve only the tools supported by this policy's context."""

    def _reserve(
        self, tool: str, resolved: ResolvedRead, max_bytes: int, now: int
    ) -> ApprovedAction:
        context = self._context
        limits = self._limits
        service, operation, parameters, exact_key, key_prefix = resolved

        require(self._calls < limits.max_calls, "call_budget_exhausted")
        require(
            self._reserved_bytes + max_bytes <= limits.total_bytes,
            "byte_budget_exhausted",
        )

        action = ApprovedAction(
            tool=tool,
            run_id=context.run_id,
            account_id=context.account_id,
            region=context.region,
            service=service,
            operation=operation,
            parameters=parameters,
            max_bytes=max_bytes,
            timeout_seconds=min(
                limits.timeout_seconds, (context.expires_at_ms - now) / 1000
            ),
            expires_at_ms=context.expires_at_ms,
            exact_key=exact_key,
            key_prefix=key_prefix,
        )

        self._calls += 1
        self._reserved_bytes += max_bytes

        return action


class RequestPolicy(ReadPolicy[IncidentContext]):
    """Authorize only one incident's objects, attempts, and log window."""

    def __init__(
        self,
        context: IncidentContext,
        limits: PolicyLimits = PolicyLimits(),
        clock: Callable[[], int] = wall_time_ms,
    ) -> None:
        validate_context(context, limits)
        self._initialize(context, limits, clock)

    def _authorize(self, request: object) -> ApprovedAction:
        now = self._clock()
        require(type(now) is int and now > 0, "invalid_clock")

        context = self._context
        limits = self._limits

        require(now < context.expires_at_ms, "context_expired")

        request = fields(request, {"tool", "arguments"}, set())
        tool = request["tool"]

        require(type(tool) is str and tool in TOOLS, "unknown_tool")

        arguments = request["arguments"]

        require(type(arguments) is dict, "invalid_arguments")

        run_id = arguments.get("run_id")

        require(type(run_id) is str, "invalid_arguments")
        require(run_id == context.run_id, "wrong_incident")

        max_bytes = integer(
            arguments.get("max_bytes", limits.default_bytes),
            1,
            limits.max_bytes,
            "invalid_byte_limit",
        )
        service, operation, parameters, exact_key, key_prefix = self._resolve(
            tool, arguments
        )

        return self._reserve(
            tool,
            (service, operation, parameters, exact_key, key_prefix),
            max_bytes,
            now,
        )

    def _resolve(self, tool: str, arguments: dict[str, Any]) -> ResolvedRead:
        if tool == "search_run_logs":
            return self._logs(arguments)
        if tool == "list_run_objects":
            return self._listing(arguments)

        return self._object(arguments)

    def _logs(self, arguments: dict[str, Any]) -> ResolvedRead:
        fields(
            arguments,
            {"run_id"},
            {"before_seconds", "after_seconds", "max_events", "max_bytes"},
        )

        context = self._context
        limits = self._limits
        before = integer(
            arguments.get("before_seconds", min(300, limits.log_window_seconds)),
            0,
            limits.log_window_seconds,
            "outside_log_window",
        )
        after = integer(
            arguments.get("after_seconds", min(300, limits.log_window_seconds)),
            0,
            limits.log_window_seconds,
            "outside_log_window",
        )

        require(before + after > 0, "empty_log_window")

        events = integer(
            arguments.get("max_events", min(50, limits.max_events)),
            1,
            limits.max_events,
            "invalid_event_limit",
        )

        return (
            "logs",
            "FilterLogEvents",
            {
                "logGroupName": context.log_group,
                "startTime": max(0, context.incident_time_ms - before * 1000),
                "endTime": context.incident_time_ms + after * 1000,
                "filterPattern": '{ $.run_id = "' + context.run_id + '" }',
                "limit": events,
                "unmask": False,
            },
            None,
            None,
        )

    def _listing(self, arguments: dict[str, Any]) -> ResolvedRead:
        fields(arguments, {"run_id", "kind"}, {"max_keys", "max_bytes"})

        context = self._context
        kind = arguments["kind"]

        require(
            type(kind) is str and kind in {"input", "output", "runs"},
            "invalid_object_kind",
        )

        count = integer(
            arguments.get("max_keys", self._limits.max_keys),
            1,
            self._limits.max_keys,
            "invalid_key_limit",
        )
        exact_key = None
        key_prefix = None

        if kind == "runs":
            prefix = f"runs/{context.run_id}/"
            key_prefix = prefix
        else:
            prefix = f"{kind}/{context.run_id}.json"
            exact_key = prefix

        return (
            "s3",
            "ListObjectsV2",
            {
                "Bucket": context.bucket,
                "Prefix": prefix,
                "MaxKeys": count,
            },
            exact_key,
            key_prefix,
        )

    def _object(self, arguments: dict[str, Any]) -> ResolvedRead:
        fields(arguments, {"run_id", "kind"}, {"attempt_id", "max_bytes"})

        context = self._context
        kind = arguments["kind"]

        require(
            type(kind) is str and kind in {"input", "output", "log"},
            "invalid_object_kind",
        )

        if kind == "log":
            attempt = arguments.get("attempt_id")
            require(
                type(attempt) is str and attempt in context.attempt_ids,
                "unapproved_attempt",
            )

            key = f"runs/{context.run_id}/{attempt}.json"
        else:
            require("attempt_id" not in arguments, "unexpected_arguments")
            key = f"{kind}/{context.run_id}.json"

        max_bytes = arguments.get("max_bytes", self._limits.default_bytes)

        return (
            "s3",
            "GetObject",
            {
                "Bucket": context.bucket,
                "Key": key,
                "Range": f"bytes=0-{max_bytes - 1}",
            },
            key,
            None,
        )
