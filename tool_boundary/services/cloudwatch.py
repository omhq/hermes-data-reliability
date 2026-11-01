import re
from typing import TYPE_CHECKING, Any

from tool_boundary.results import Evidence, fit_items, integer_parameter
from tool_boundary.validation import fields, integer, require
from tool_boundary.wire import ExecutionError, decode

from .aws import AwsToolProvider
from .base import CliRequest, Scope

if TYPE_CHECKING:
    from tool_boundary.models import ApprovedAction, ResolvedRead


class CloudWatchProvider(AwsToolProvider):
    package = "hermes-cloudwatch"
    toolset = "aws_cloudwatch"
    service = "logs"
    modes = {
        "run": frozenset({"search_run_logs"}),
        "log": frozenset({"search_log_events"}),
        "resources": frozenset(
            {"describe_cloudwatch_scope", "search_log_events"}
        ),
    }

    @staticmethod
    def valid_log_group(value: object) -> bool:
        return (
            type(value) is str
            and re.fullmatch(r"[./A-Za-z0-9_#-]{1,512}", value) is not None
        )

    def validate_scope(self, scope: Scope) -> None:
        super().validate_scope(scope)
        require(
            self.valid_log_group(scope.context.log_group)
            and scope.context.log_group
            in self.collection_log_groups(self.configuration),
            "invalid_context",
        )

    @classmethod
    def collection_log_groups(cls, config: dict[str, Any]) -> tuple[str, ...]:
        groups = config.get("collection_log_groups", [])
        require(
            type(groups) is list and len(groups) <= 16, "invalid_configuration"
        )
        selected = [config["log_group"], *groups]
        require(
            all(cls.valid_log_group(group) for group in selected),
            "invalid_configuration",
        )
        result = tuple(dict.fromkeys(selected))
        require(len(result) <= 16, "invalid_configuration")
        return result

    def resources(self) -> dict[str, Any]:
        return {
            "cloudwatch_log_groups": list(
                self.collection_log_groups(self.configuration)
            )
        }

    def describe(self, scope: Scope) -> dict[str, Any]:
        return {
            **scope.describe(),
            "cloudwatch_log_groups": [scope.context.log_group],
        }

    def authorize(
        self,
        scope: Scope,
        tool: str,
        arguments: dict[str, Any],
        maximum: int,
        now: int,
    ) -> ResolvedRead:
        if tool == "describe_cloudwatch_scope":
            return self._description(scope, arguments)
        if scope.mode == "run":
            context = scope.incident()
            require(type(arguments.get("run_id")) is str, "invalid_arguments")
            require(arguments["run_id"] == context.run_id, "wrong_incident")
            return self._run_logs(scope, arguments)

        return self._logs(scope, arguments, now)

    def _run_logs(
        self, scope: Scope, arguments: dict[str, Any]
    ) -> ResolvedRead:
        fields(
            arguments,
            {"run_id"},
            {"before_seconds", "after_seconds", "max_events", "max_bytes"},
        )

        context = scope.incident()
        limits = scope.limits
        before = integer(
            arguments.get(
                "before_seconds", min(300, limits.log_window_seconds)
            ),
            0,
            limits.log_window_seconds,
            "outside_log_window",
        )
        after = integer(
            arguments.get(
                "after_seconds", min(300, limits.log_window_seconds)
            ),
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

    def _logs(
        self, scope: Scope, arguments: dict[str, Any], now: int
    ) -> ResolvedRead:
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

        context = scope.resources()

        require(
            arguments["log_group"] == context.log_group, "resource_not_allowed"
        )

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
            scope.limits.max_events,
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

    def request(self, action: ApprovedAction) -> CliRequest:
        require(
            action.operation == "FilterLogEvents", "invalid_execution_plan"
        )
        require(
            action.parameters.get("unmask") is False, "invalid_execution_plan"
        )
        require(
            action.parameters.get("logGroupName")
            in self.collection_log_groups(self.configuration),
            "invalid_execution_plan",
        )
        return self.command(
            action,
            "filter-log-events",
            {
                "logGroupName": "--log-group-name",
                "startTime": "--start-time",
                "endTime": "--end-time",
                "filterPattern": "--filter-pattern",
                "limit": "--limit",
            },
            excluded=frozenset({"unmask"}),
            fixed=("--no-unmask",),
        )

    def normalize(
        self,
        action: ApprovedAction,
        response: dict[str, Any],
        body: bytes | None,
    ) -> Evidence:
        require(
            action.operation == "FilterLogEvents", "invalid_execution_plan"
        )
        data, partial = logs_result(action, response)
        references = [
            {
                "reference": "cloudwatch-event:" + item["event_id"],
                "observation": "log_event",
            }
            for item in data["events"]
        ]
        return Evidence(data, references, partial, "events")


def logs_result(
    action: ApprovedAction, response: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    events = response.get("events")

    if type(events) is not list:
        raise ExecutionError("invalid_aws_response")

    start = integer_parameter(action, "startTime", 0)
    end = integer_parameter(action, "endTime", start)
    maximum = integer_parameter(action, "limit", 1)
    items = []
    filtered = False

    for event in events:
        if type(event) is not dict:
            raise ExecutionError("invalid_aws_response")

        timestamp = event.get("timestamp")
        message = event.get("message")

        if type(timestamp) is not int or type(message) is not str:
            raise ExecutionError("invalid_aws_response")

        try:
            structured = decode(message)
        except ExecutionError:
            structured = None

        if (
            action.run_id
            and (
                type(structured) is not dict
                or structured.get("run_id") != action.run_id
            )
        ) or not start <= timestamp <= end:
            filtered = True
            continue

        event_id = event.get("eventId")

        if type(event_id) is not str or len(event_id) > 256:
            raise ExecutionError("invalid_aws_response")

        items.append(
            {"event_id": event_id, "timestamp": timestamp, "message": message}
        )

    data, partial = fit_items("events", items[:maximum], action.max_bytes)

    return data, partial or filtered or len(items) > maximum or bool(
        response.get("nextToken")
    )
