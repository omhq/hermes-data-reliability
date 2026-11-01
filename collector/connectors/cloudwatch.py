import json
import re
from typing import Any

from collector.execution import CollectionError, Deadline
from collector.providers.aws.client import AwsClient
from observations.models import Event, Page, Source

from .aws import AwsConnector

PAGE_EVENTS = 100


class CloudWatchConnector(AwsConnector):
    name = "cloudwatch_logs"

    def __init__(
        self, configuration: dict[str, Any], client: AwsClient | None = None
    ) -> None:
        super().__init__(configuration, client)
        primary = configuration.get("log_group")
        groups = configuration.get("collection_log_groups", [])

        if (
            not self.valid_location(primary)
            or type(groups) is not list
            or len(groups) > 16
            or any(not self.valid_location(group) for group in groups)
        ):
            raise CollectionError("invalid_configuration")

        self.groups = tuple(dict.fromkeys([primary, *groups]))

        if len(self.groups) > 16:
            raise CollectionError("invalid_configuration")

    @staticmethod
    def valid_location(value: object) -> bool:
        return (
            type(value) is str
            and re.fullmatch(r"[./A-Za-z0-9_#-]{1,512}", value) is not None
        )

    @classmethod
    def prepare_template(
        cls, value: dict[str, Any], configuration: dict[str, Any]
    ) -> dict[str, Any]:
        definition = super().prepare_template(value, configuration)

        if definition.get("location") == "${processor_log_group}":
            definition["location"] = configuration["log_group"]

        return definition

    def authorize_location(self, location: str) -> None:
        if location not in self.groups:
            raise CollectionError("resource_not_allowed")

    def _read(
        self,
        source: Source,
        start: int,
        end: int,
        token: str | None,
        deadline: Deadline,
    ) -> Page:
        arguments = [
            "--log-group-name",
            source.location,
            "--start-time",
            str(start),
            "--end-time",
            str(end),
            "--filter-pattern",
            "",
            "--limit",
            str(PAGE_EVENTS),
            "--no-unmask",
        ]

        if token is not None:
            arguments.extend(["--next-token", token])

        response = self.client.call(
            "logs", "filter-log-events", arguments, deadline
        )
        events, continuation = validate_page(response, start, end)
        return Page(
            tuple(
                Event(item["eventId"], item["timestamp"], item)
                for item in events
            ),
            continuation,
        )


def validate_page(
    response: dict[str, Any], start: int, end: int
) -> tuple[list[dict[str, Any]], str | None]:
    if type(response) is not dict or type(response.get("events")) is not list:
        raise CollectionError("invalid_aws_response")

    events = response["events"]
    serialized = json.dumps(
        response, separators=(",", ":"), allow_nan=False
    ).encode("ascii")

    if len(events) > PAGE_EVENTS or len(serialized) > 2 * 1024 * 1024:
        raise CollectionError("upstream_response_too_large")

    for event in events:
        if (
            type(event) is not dict
            or type(event.get("eventId")) is not str
            or not 1 <= len(event["eventId"]) <= 256
            or type(event.get("timestamp")) is not int
            or not start <= event["timestamp"] <= end
            or type(event.get("message")) is not str
            or type(event.get("logStreamName")) is not str
        ):
            raise CollectionError("invalid_aws_response")

    token = response.get("nextToken")

    if token is not None and (
        type(token) is not str or not 1 <= len(token) <= 4096
    ):
        raise CollectionError("invalid_aws_response")

    return events, token
