import json
from typing import Any

from collector.execution import CollectionError, Deadline
from collector.providers.aws.client import AwsClient
from collector.providers.aws.process import decode_json
from observations.cloudtrail import (
    InvalidEvent,
    cloudtrail_events,
    utc_timestamp,
)
from observations.models import Event, Page, Source

from .aws import AwsConnector


class CloudTrailConnector(AwsConnector):
    name = "cloudtrail"

    def __init__(
        self, configuration: dict[str, Any], client: AwsClient | None = None
    ) -> None:
        super().__init__(configuration, client)
        resources = configuration.get("cloudtrail_resource_names", [])

        if (
            type(resources) is not list
            or len(resources) > 16
            or any(not self.valid_location(value) for value in resources)
        ):
            raise CollectionError("invalid_configuration")

        self.resources = tuple(resources)
        self.enabled = "hermes-cloudtrail" in configuration.get(
            "tool_packages", []
        )

    @staticmethod
    def valid_location(value: object) -> bool:
        return (
            type(value) is str
            and 1 <= len(value) <= 512
            and all(ord(character) >= 32 for character in value)
        )

    def authorize_location(self, location: str) -> None:
        if not self.enabled or location not in self.resources:
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
            "--lookup-attributes",
            json.dumps(
                [
                    {
                        "AttributeKey": "ResourceName",
                        "AttributeValue": source.location,
                    }
                ]
            ),
            "--start-time",
            utc_timestamp(start),
            "--end-time",
            utc_timestamp(end),
            "--max-results",
            "50",
        ]

        if token is not None:
            arguments.extend(["--next-token", token])

        response = self.client.call(
            "cloudtrail", "lookup-events", arguments, deadline
        )

        try:
            events, _ = cloudtrail_events(
                response,
                source.location,
                source.account,
                source.region,
                start,
                end,
                decode_json,
            )
            continuation = response.get("NextToken")

            if (
                len(events) > 50
                or len(events) != len(response["Events"])
                or continuation is not None
                and (
                    type(continuation) is not str
                    or not 1 <= len(continuation) <= 4096
                )
            ):
                raise InvalidEvent("invalid_aws_response")
        except InvalidEvent:
            raise CollectionError("invalid_aws_response") from None

        return Page(
            tuple(
                Event(item["event_id"], item["timestamp"], item["event"])
                for item in events
            ),
            continuation,
        )
