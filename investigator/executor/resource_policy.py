import re

from typing import TYPE_CHECKING, Any, Callable

from .models import IncidentContext, PolicyLimits, ResourceContext
from .policy import (
    ReadPolicy,
    fields,
    integer,
    require,
    validate_context,
    wall_time_ms,
)

if TYPE_CHECKING:
    from investigator.executor.models import ApprovedAction, ResolvedRead


RESOURCE_TOOLS = frozenset(
    {
        "list_aws_resources",
        "list_s3_objects",
        "read_s3_object",
        "search_log_events",
        "lookup_cloudtrail_events",
        "describe_cloudwatch_scope",
        "describe_cloudtrail_scope",
    }
)
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
        cloudtrail_resources: tuple[str, ...] = (),
    ) -> None:
        require(type(context) is ResourceContext, "invalid_context")
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
        require(context.profile == "lab", "invalid_context")
        require(
            type(context.end_time_ms) is int
            and context.start_time_ms < context.end_time_ms <= context.expires_at_ms,
            "invalid_context",
        )
        require(
            context.prefixes == ("input/", "output/", "runs/"),
            "invalid_context",
        )
        self._initialize(context, limits, clock, cloudtrail_resources)

    def describe_resources(self, tool: str = "list_aws_resources") -> dict[str, Any]:
        context = self._context

        result = {
            "profile": context.profile,
            "account_id": context.account_id,
            "region": context.region,
            "s3": [{"bucket": context.bucket, "prefixes": list(context.prefixes)}],
            "cloudwatch_log_groups": [context.log_group],
            "cloudtrail_resource_names": list(self._cloudtrail_resources),
            "start_time_ms": context.start_time_ms,
            "end_time_ms": context.end_time_ms,
            "expires_at_ms": context.expires_at_ms,
            "max_calls": self._limits.max_calls,
            "total_bytes": self._limits.total_bytes,
        }

        if tool == "describe_cloudwatch_scope":
            result.pop("s3")
            result.pop("cloudtrail_resource_names")
        elif tool == "describe_cloudtrail_scope":
            result.pop("s3")
            result.pop("cloudwatch_log_groups")

        return result

    def _authorize(self, request: object) -> ApprovedAction:
        now = self._clock()

        require(type(now) is int and now > 0, "invalid_clock")
        require(now < self._context.expires_at_ms, "context_expired")

        request = fields(request, {"tool", "arguments"}, set())
        tool = request["tool"]

        require(type(tool) is str and tool in RESOURCE_TOOLS, "unknown_tool")

        arguments = request["arguments"]

        require(type(arguments) is dict, "invalid_arguments")

        maximum = integer(
            arguments.get("max_bytes", self._limits.default_bytes),
            1,
            self._limits.max_bytes,
            "invalid_byte_limit",
        )

        if tool in {
            "list_aws_resources",
            "describe_cloudwatch_scope",
            "describe_cloudtrail_scope",
        }:
            fields(arguments, set(), {"max_bytes"})

            resolved: ResolvedRead = (
                "local",
                "DescribeResources",
                {"Profile": "lab"},
                None,
                None,
            )
        elif tool == "search_log_events":
            resolved = self._logs(arguments, now)
        elif tool == "lookup_cloudtrail_events":
            resolved = self._cloudtrail(
                arguments,
                self._context.start_time_ms,
                self._context.end_time_ms,
                now,
            )
        else:
            resolved = self._s3(tool, arguments, maximum)

        return self._reserve(tool, resolved, maximum, now)

    def _s3(self, tool: str, arguments: dict[str, Any], maximum: int) -> ResolvedRead:
        listing = tool == "list_s3_objects"
        key_field = "prefix" if listing else "key"
        optional = {"max_bytes", "max_keys"} if listing else {"max_bytes"}

        fields(arguments, {"bucket", key_field}, optional)
        require(arguments["bucket"] == self._context.bucket, "resource_not_allowed")

        key = arguments[key_field]

        require(
            type(key) is str
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,1023}", key) is not None,
            "invalid_object_key",
        )
        require(
            not any(part in {".", ".."} for part in key.split("/")) and "//" not in key,
            "invalid_object_key",
        )
        require(
            any(key.startswith(prefix) for prefix in self._context.prefixes),
            "resource_not_allowed",
        )

        if listing:
            count = integer(
                arguments.get("max_keys", 20),
                1,
                self._limits.max_keys,
                "invalid_key_limit",
            )

            return (
                "s3",
                "ListObjectsV2",
                {
                    "Bucket": self._context.bucket,
                    "Prefix": key,
                    "MaxKeys": count,
                },
                None,
                key,
            )

        require(not key.endswith("/"), "invalid_object_key")

        return (
            "s3",
            "GetObject",
            {
                "Bucket": self._context.bucket,
                "Key": key,
                "Range": f"bytes=0-{maximum - 1}",
            },
            key,
            None,
        )

    def _logs(self, arguments: dict[str, Any], now: int) -> ResolvedRead:
        fields(
            arguments,
            {"log_group"},
            {
                "start_time_ms",
                "end_time_ms",
                "filter_pattern",
                "max_events",
                "max_bytes",
            },
        )

        context = self._context

        require(arguments["log_group"] == context.log_group, "resource_not_allowed")

        start = integer(
            arguments.get("start_time_ms", context.start_time_ms),
            context.start_time_ms,
            context.end_time_ms,
            "outside_log_window",
        )
        end = integer(
            arguments.get("end_time_ms", min(now, context.end_time_ms)),
            start + 1,
            context.end_time_ms,
            "outside_log_window",
        )
        pattern = arguments.get("filter_pattern", "")

        require(
            type(pattern) is str
            and len(pattern) <= 512
            and not pattern.startswith("-")
            and all(ord(character) >= 32 for character in pattern),
            "invalid_log_filter",
        )

        count = integer(
            arguments.get("max_events", 50),
            1,
            self._limits.max_events,
            "invalid_event_limit",
        )

        return (
            "logs",
            "FilterLogEvents",
            {
                "logGroupName": context.log_group,
                "startTime": start,
                "endTime": end,
                "filterPattern": pattern,
                "limit": count,
                "unmask": False,
            },
            None,
            None,
        )
