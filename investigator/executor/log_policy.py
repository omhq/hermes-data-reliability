import re
from typing import TYPE_CHECKING, Any, Callable

from .models import IncidentContext, PolicyLimits, ResourceContext
from .policy import fields, integer, require, validate_context, wall_time_ms
from .resource_policy import ResourcePolicy

if TYPE_CHECKING:
    from investigator.executor.models import ApprovedAction


class LogPolicy(ResourcePolicy):
    """Authorize the generic log tool within one host-approved time range."""

    def __init__(
        self,
        context: ResourceContext,
        incident_id: str,
        clock: Callable[[], int] = wall_time_ms,
        cloudtrail_resources: tuple[str, ...] = (),
    ) -> None:
        limits = PolicyLimits()

        validate_context(
            IncidentContext(
                context.account_id,
                context.region,
                context.bucket,
                context.log_group,
                "0" * 24,
                (),
                context.start_time_ms,
                context.expires_at_ms,
            ),
            limits,
        )

        self.incident_id = incident_id
        self._initialize(context, limits, clock, cloudtrail_resources)

    def _authorize(self, request: object) -> ApprovedAction:
        now = self._clock()

        require(type(now) is int and now > 0, "invalid_clock")
        require(now < self._context.expires_at_ms, "context_expired")

        request = fields(request, {"tool", "arguments"}, set())
        require(
            type(request["tool"]) is str
            and request["tool"]
            in {"search_log_events", "lookup_cloudtrail_events"},
            "unknown_tool",
        )

        arguments = request["arguments"]

        require(type(arguments) is dict, "invalid_arguments")

        maximum = integer(
            arguments.get("max_bytes", self._limits.default_bytes),
            1,
            self._limits.max_bytes,
            "invalid_byte_limit",
        )

        if request["tool"] == "lookup_cloudtrail_events":
            resolved = self._cloudtrail(
                arguments,
                self._context.start_time_ms,
                self._context.end_time_ms,
                now,
            )
        else:
            resolved = self._logs(arguments, now)

        return self._reserve(request["tool"], resolved, maximum, now)


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
