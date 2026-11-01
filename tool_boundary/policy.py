import threading
from typing import TYPE_CHECKING, Callable, Literal

from .models import (
    ApprovedAction,
    IncidentContext,
    PolicyDenied,
    PolicyLimits,
    ResourceContext,
)
from .services.base import Scope
from .validation import (
    fields,
    integer,
    require,
    wall_time_ms,
)

if TYPE_CHECKING:
    from .models import ResolvedRead
    from .registry import ConfiguredTools


class ReadPolicy[Context: IncidentContext | ResourceContext]:
    def _initialize(
        self,
        context: Context,
        limits: PolicyLimits,
        clock: Callable[[], int],
        providers: ConfiguredTools | None = None,
        *,
        mode: Literal["run", "log", "resources"] = "resources",
    ) -> None:
        require(
            type(context) in (IncidentContext, ResourceContext),
            "invalid_context",
        )
        require(type(limits) is PolicyLimits, "invalid_limits")
        context.validate()
        limits.validate()
        self._context = context
        self._limits = limits
        self._clock = clock
        self.providers: ConfiguredTools | None = None
        self.mode = mode
        self._calls = 0
        self._reserved_bytes = 0
        self._lock = threading.Lock()

        if providers is not None:
            self.bind(providers)

    def bind(self, providers: ConfiguredTools) -> None:
        providers.validate_scope(self.scope())
        self.providers = providers

    def scope(self) -> Scope:
        return Scope(self._context, self._limits, self.mode)

    def authorize(self, request: object) -> ApprovedAction:
        with self._lock:
            try:
                return self._authorize(request)
            except PolicyDenied:
                raise
            except Exception:
                raise PolicyDenied("policy_error") from None

    def _authorize(self, request: object) -> ApprovedAction:
        now = self._clock()
        require(type(now) is int and now > 0, "invalid_clock")
        require(now < self._context.expires_at_ms, "context_expired")
        request = fields(request, {"tool", "arguments"}, set())
        require(type(request["tool"]) is str, "unknown_tool")
        arguments = request["arguments"]
        require(type(arguments) is dict, "invalid_arguments")
        maximum = integer(
            arguments.get("max_bytes", self._limits.default_bytes),
            1,
            self._limits.max_bytes,
            "invalid_byte_limit",
        )
        providers = self.providers
        if providers is None:
            raise PolicyDenied("unconfigured_tools")

        resolved = providers.authorize(
            self.scope(), request["tool"], arguments, maximum, now
        )
        return self._reserve(request["tool"], resolved, maximum, now)

    def _reserve(
        self, tool: str, resolved: ResolvedRead, max_bytes: int, now: int
    ) -> ApprovedAction:
        """Reserve the full allowance; failed reads remain charged."""
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
    def __init__(
        self,
        context: IncidentContext,
        limits: PolicyLimits = PolicyLimits(),
        clock: Callable[[], int] = wall_time_ms,
        providers: ConfiguredTools | None = None,
    ) -> None:
        require(type(context) is IncidentContext, "invalid_context")
        self._initialize(context, limits, clock, providers, mode="run")
