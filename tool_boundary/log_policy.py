import re
from typing import TYPE_CHECKING, Any, Callable

from .models import PolicyLimits, ResourceContext
from .resource_policy import ResourcePolicy
from .validation import (
    fields,
    integer,
    require,
    wall_time_ms,
)

if TYPE_CHECKING:
    from .registry import ConfiguredTools


class LogPolicy(ResourcePolicy):
    def __init__(
        self,
        context: ResourceContext,
        incident_id: str,
        clock: Callable[[], int] = wall_time_ms,
        providers: ConfiguredTools | None = None,
    ) -> None:
        limits = PolicyLimits()
        require(type(context) is ResourceContext, "invalid_context")
        self.incident_id = incident_id
        self._initialize(context, limits, clock, providers, mode="log")


def log_context(
    config: dict[str, Any], request: object, now: int
) -> tuple[ResourceContext, int]:
    request = fields(
        request, {"observation_id", "incident_time_ms"}, {"ttl_seconds"}
    )
    incident_id = request["observation_id"]

    require(
        type(incident_id) is str
        and re.fullmatch(r"log-[a-f0-9]{32}", incident_id) is not None,
        "invalid_context",
    )

    timestamp = integer(
        request["incident_time_ms"], 1, 2**63 - 1, "invalid_timestamp"
    )
    ttl = integer(request.get("ttl_seconds", 300), 1, 300, "invalid_ttl")
    context = ResourceContext(
        profile="collector_logs",
        account_id=config["account_id"],
        region=config["region"],
        bucket=config["bucket"],
        log_group=config["log_group"],
        prefixes=(),
        start_time_ms=max(1, timestamp - 300000),
        end_time_ms=timestamp + 300000,
        expires_at_ms=now + ttl * 1000,
    )

    return context, ttl
