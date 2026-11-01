from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from observations import cloudtrail
from observations.cloudtrail import utc_timestamp
from tool_boundary.models import IncidentContext
from tool_boundary.results import Evidence, fit_items, integer_parameter
from tool_boundary.validation import fields, integer, require
from tool_boundary.wire import ExecutionError, decode

from .aws import AwsToolProvider
from .base import CliRequest, Scope

if TYPE_CHECKING:
    from tool_boundary.contracts import ToolExtension
    from tool_boundary.models import ApprovedAction, ResolvedRead


def resource_names(value: object) -> tuple[str, ...]:
    if type(value) is not list or len(value) > 50:
        raise ValueError("Invalid CloudTrail resource names")

    selected = []

    for item in value:
        if (
            type(item) is not str
            or not 1 <= len(item) <= 2048
            or any(ord(character) < 32 for character in item)
            or item in selected
        ):
            raise ValueError("Invalid CloudTrail resource names")

        selected.append(item)

    return tuple(selected)


class CloudTrailProvider(AwsToolProvider):
    package = "hermes-cloudtrail"
    toolset = "aws_cloudtrail"
    service = "cloudtrail"
    modes = {
        "run": frozenset({"lookup_cloudtrail_events"}),
        "log": frozenset({"lookup_cloudtrail_events"}),
        "resources": frozenset(
            {"describe_cloudtrail_scope", "lookup_cloudtrail_events"}
        ),
    }

    def __init__(
        self,
        configuration: dict[str, Any],
        extension: ToolExtension | None = None,
    ) -> None:
        super().__init__(configuration)
        self.resource_names = resource_names(
            configuration.get("cloudtrail_resource_names", [])
        )

    @classmethod
    def agent_options(cls, entry: object, enabled: bool) -> dict[str, Any]:
        if type(entry) is not dict or not entry.keys() <= {"resource_names"}:
            raise ValueError("Invalid CloudTrail package options")

        resources = resource_names(entry.get("resource_names", []))

        if enabled and not resources:
            raise ValueError("CloudTrail requires explicit resource_names")

        return {"resource_names": list(resources)}

    @classmethod
    def host_options(
        cls, configuration: dict[str, Any], enabled: bool
    ) -> dict[str, Any]:
        return cls.agent_options(
            {
                "resource_names": configuration.get(
                    "cloudtrail_resource_names", []
                )
            },
            enabled,
        )

    def resources(self) -> dict[str, Any]:
        return {"cloudtrail_resource_names": list(self.resource_names)}

    def authorize(
        self,
        scope: Scope,
        tool: str,
        arguments: dict[str, Any],
        maximum: int,
        now: int,
    ) -> ResolvedRead:
        if tool == "describe_cloudtrail_scope":
            return self._description(scope, arguments)

        context = scope.context

        if isinstance(context, IncidentContext):
            window = scope.limits.log_window_seconds * 1000
            start, end = (
                context.incident_time_ms - window,
                context.incident_time_ms + window,
            )
        else:
            start, end = context.start_time_ms, context.end_time_ms

        return self._cloudtrail(scope, arguments, start, end, now)

    def _cloudtrail(
        self,
        scope: Scope,
        arguments: dict[str, Any],
        start: int,
        end: int,
        now: int,
    ) -> ResolvedRead:
        fields(
            arguments,
            {"resource_name"},
            {"start_time_ms", "end_time_ms", "max_events", "max_bytes"},
        )
        resource = arguments["resource_name"]
        require(
            type(resource) is str and resource in self.resource_names,
            "resource_not_allowed",
        )

        start = max(start, now - 90 * 86400 * 1000, 1)
        end = min(end, now)
        requested_start = integer(
            arguments.get("start_time_ms", start),
            start,
            end - 1,
            "outside_cloudtrail_window",
        )
        requested_end = integer(
            arguments.get("end_time_ms", end),
            requested_start + 1,
            end,
            "outside_cloudtrail_window",
        )
        count = integer(
            arguments.get("max_events", 20),
            1,
            min(50, scope.limits.max_events),
            "invalid_event_limit",
        )

        return (
            "cloudtrail",
            "LookupEvents",
            {
                "LookupAttributes": [
                    {
                        "AttributeKey": "ResourceName",
                        "AttributeValue": resource,
                    }
                ],
                "StartTime": utc_timestamp(requested_start),
                "EndTime": utc_timestamp(requested_end),
                "MaxResults": count,
            },
            None,
            None,
        )

    def request(self, action: ApprovedAction) -> CliRequest:
        require(action.operation == "LookupEvents", "invalid_execution_plan")
        parameters = action.parameters_dict()
        require(
            parameters.get("LookupAttributes")
            in [
                [{"AttributeKey": "ResourceName", "AttributeValue": value}]
                for value in self.resource_names
            ],
            "invalid_execution_plan",
        )
        return self.command(
            action,
            "lookup-events",
            {
                "LookupAttributes": "--lookup-attributes",
                "StartTime": "--start-time",
                "EndTime": "--end-time",
                "MaxResults": "--max-results",
            },
            json_fields=frozenset({"LookupAttributes"}),
        )

    def normalize(
        self,
        action: ApprovedAction,
        response: dict[str, Any],
        body: bytes | None,
    ) -> Evidence:
        require(action.operation == "LookupEvents", "invalid_execution_plan")
        items, incomplete = cloudtrail_events(action, response)
        maximum = integer_parameter(action, "MaxResults", 1)
        data, partial = fit_items("events", items[:maximum], action.max_bytes)
        references = [
            {
                "reference": (
                    f"cloudtrail-event:{action.account_id}:{action.region}:"
                )
                + item["event_id"],
                "observation": "cloudtrail_event",
            }
            for item in data["events"]
        ]
        return Evidence(
            data,
            references,
            incomplete or partial or len(items) > maximum,
            "events",
        )


def timestamp_ms(value: object) -> int:
    try:
        return cloudtrail.timestamp_ms(value)
    except cloudtrail.InvalidEvent:
        raise ExecutionError("invalid_aws_response") from None


def cloudtrail_events(
    action: ApprovedAction, response: dict[str, Any]
) -> tuple[list[dict[str, Any]], bool]:
    attributes = action.parameters["LookupAttributes"]

    if (
        type(attributes) is not tuple
        or len(attributes) != 1
        or not isinstance(attributes[0], Mapping)
        or attributes[0].get("AttributeKey") != "ResourceName"
        or type(attributes[0].get("AttributeValue")) is not str
    ):
        raise ExecutionError("invalid_execution_plan")

    try:
        return cloudtrail.cloudtrail_events(
            response,
            attributes[0]["AttributeValue"],
            action.account_id,
            action.region,
            timestamp_ms(action.parameters["StartTime"]),
            timestamp_ms(action.parameters["EndTime"]),
            decode,
        )
    except cloudtrail.InvalidEvent, ExecutionError:
        raise ExecutionError("invalid_aws_response") from None
