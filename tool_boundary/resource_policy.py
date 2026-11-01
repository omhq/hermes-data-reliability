from typing import TYPE_CHECKING, Any, Callable

from .models import PolicyLimits, ResourceContext
from .policy import ReadPolicy
from .validation import (
    fields,
    integer,
    require,
    wall_time_ms,
)

if TYPE_CHECKING:
    from .registry import ConfiguredTools

CHAT_LIMITS = PolicyLimits(max_calls=60, total_bytes=4 * 1024 * 1024)


def profile_context(
    config: dict[str, Any], request: object, now: int
) -> tuple[ResourceContext, int]:
    """The lab profile derives targets from Terraform's investigator output."""
    request = fields(request, {"profile"}, {"ttl_seconds", "lookback_seconds"})

    require(type(now) is int and now > 0, "invalid_clock")
    require(request["profile"] == "lab", "unknown_profile")

    ttl = integer(request.get("ttl_seconds", 1800), 1, 1800, "invalid_ttl")
    lookback = integer(
        request.get("lookback_seconds", 7200), 1, 86400, "invalid_lookback"
    )

    return (
        ResourceContext(
            profile="lab",
            account_id=config["account_id"],
            region=config["region"],
            bucket=config["bucket"],
            log_group=config["log_group"],
            prefixes=("input/", "output/", "runs/"),
            start_time_ms=max(1, now - lookback * 1000),
            end_time_ms=now + ttl * 1000,
            expires_at_ms=now + ttl * 1000,
        ),
        ttl,
    )


class ResourcePolicy(ReadPolicy[ResourceContext]):
    def __init__(
        self,
        context: ResourceContext,
        limits: PolicyLimits = CHAT_LIMITS,
        clock: Callable[[], int] = wall_time_ms,
        providers: ConfiguredTools | None = None,
    ) -> None:
        require(type(context) is ResourceContext, "invalid_context")
        self._initialize(context, limits, clock, providers)
        require(context.profile == "lab", "invalid_context")
        require(
            context.end_time_ms <= context.expires_at_ms,
            "invalid_context",
        )
