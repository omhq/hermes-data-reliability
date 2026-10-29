import json
from typing import TYPE_CHECKING, Any

from host_aws.cli import AwsCallError, Deadline

if TYPE_CHECKING:
    from .audit import CollectionAudit
    from .aws import CloudWatchClient


MAX_PAGES = 5
MAX_WINDOW_MS = 2 * 60 * 60 * 1000
PAGE_EVENTS = 100


class CloudWatchReader:
    def __init__(
        self, client: CloudWatchClient, audit: CollectionAudit
    ) -> None:
        self.client = client
        self.account_id = client.access.account_id
        self.audit = audit
        self.deadline = Deadline(60)

    def read(
        self,
        source: dict[str, Any],
        start: int,
        end: int,
        token: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        approved = self.client.access

        if any(
            source[key] != getattr(approved, key)
            for key in ("region", "log_group")
        ):
            raise AwsCallError("resource_not_allowed")

        self.audit.write(
            {
                "status": "collection_read",
                "source": {
                    "account_id": approved.account_id,
                    "region": approved.region,
                    "log_group": source["log_group"],
                    "start_ms": start,
                    "end_ms": end,
                },
            }
        )

        try:
            response = self.client.filter_log_events(
                start, end, PAGE_EVENTS, token, self.deadline
            )
            events, continuation = validate_page(response, start, end)
        except Exception:
            self.audit.write({"status": "collection_failed"})
            raise

        self.audit.write(
            {
                "status": "collection_completed",
                "events": len(events),
                "partial": continuation is not None,
            }
        )

        return events, continuation


def validate_page(
    response: dict[str, Any], start: int, end: int
) -> tuple[list[dict[str, Any]], str | None]:
    if type(response) is not dict or type(response.get("events")) is not list:
        raise AwsCallError("invalid_aws_response")

    events = response["events"]
    serialized = json.dumps(
        response, separators=(",", ":"), allow_nan=False
    ).encode("ascii")

    if len(events) > PAGE_EVENTS or len(serialized) > 2 * 1024 * 1024:
        raise AwsCallError("upstream_response_too_large")

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
            raise AwsCallError("invalid_aws_response")

    token = response.get("nextToken")

    if token is not None and (
        type(token) is not str or not 1 <= len(token) <= 4096
    ):
        raise AwsCallError("invalid_aws_response")

    return events, token
