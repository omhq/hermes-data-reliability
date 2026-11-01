import math
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any


class InvalidEvent(ValueError):
    pass


def timestamp_ms(value: object) -> int:
    try:
        if type(value) is str:
            parsed = datetime.fromisoformat(value)

            if parsed.tzinfo is None:
                raise ValueError

            seconds = parsed.timestamp()
        elif type(value) is int or type(value) is float:
            seconds = value
        else:
            raise ValueError

        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError

        return round(seconds * 1000)
    except ValueError, OverflowError, OSError:
        raise InvalidEvent("invalid_aws_response") from None


def utc_timestamp(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, UTC).isoformat(
        timespec="milliseconds"
    )


def cloudtrail_events(
    response: dict[str, Any],
    resource: str,
    account: str,
    region: str,
    start: int,
    end: int,
    decoder: Callable[[str], Any],
) -> tuple[list[dict[str, Any]], bool]:
    events = response.get("Events")
    token = response.get("NextToken")

    if (
        type(events) is not list
        or token is not None
        and type(token) is not str
    ):
        raise InvalidEvent("invalid_aws_response")

    items = []
    filtered = False

    for event in events:
        if type(event) is not dict:
            raise InvalidEvent("invalid_aws_response")

        identifier = event.get("EventId")
        resources = event.get("Resources")

        if (
            type(identifier) is not str
            or not re.fullmatch(
                r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}",
                identifier,
            )
            or type(resources) is not list
            or any(type(item) is not dict for item in resources)
        ):
            raise InvalidEvent("invalid_aws_response")

        timestamp = timestamp_ms(event.get("EventTime"))

        if not start <= timestamp <= end or not any(
            item.get("ResourceName") == resource for item in resources
        ):
            filtered = True
            continue

        try:
            original = decoder(event["CloudTrailEvent"])
        except KeyError, TypeError, ValueError, RecursionError:
            raise InvalidEvent("invalid_aws_response") from None

        if (
            type(original) is not dict
            or original.get("recipientAccountId") != account
            or original.get("awsRegion") != region
            or original.get("eventID") != identifier
            or original.get("eventCategory", "Management") != "Management"
            or timestamp_ms(original.get("eventTime")) != timestamp
        ):
            raise InvalidEvent("invalid_aws_response")

        items.append(
            {"event_id": identifier, "timestamp": timestamp, "event": original}
        )

    return items, filtered or bool(token)
