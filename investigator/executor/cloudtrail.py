import json
import math
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .wire import ExecutionError

if TYPE_CHECKING:
    from investigator.executor.models import ApprovedAction


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
        raise ExecutionError("invalid_aws_response") from None


def utc_timestamp(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, UTC).isoformat(
        timespec="milliseconds"
    )


def cloudtrail_events(
    action: ApprovedAction, response: dict[str, Any]
) -> tuple[list[dict[str, Any]], bool]:
    events = response.get("Events")
    token = response.get("NextToken")

    if (
        type(events) is not list
        or token is not None
        and type(token) is not str
    ):
        raise ExecutionError("invalid_aws_response")

    attributes = action.parameters["LookupAttributes"]

    if (
        type(attributes) is not list
        or len(attributes) != 1
        or type(attributes[0]) is not dict
        or attributes[0].get("AttributeKey") != "ResourceName"
        or type(attributes[0].get("AttributeValue")) is not str
    ):
        raise ExecutionError("invalid_execution_plan")

    resource = attributes[0]["AttributeValue"]
    start = timestamp_ms(action.parameters["StartTime"])
    end = timestamp_ms(action.parameters["EndTime"])
    items = []
    filtered = False

    for event in events:
        if type(event) is not dict:
            raise ExecutionError("invalid_aws_response")

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
            raise ExecutionError("invalid_aws_response")

        timestamp = timestamp_ms(event.get("EventTime"))

        if not start <= timestamp <= end or not any(
            item.get("ResourceName") == resource for item in resources
        ):
            filtered = True
            continue

        try:
            original = json.loads(event["CloudTrailEvent"])
        except KeyError, TypeError, ValueError, RecursionError:
            raise ExecutionError("invalid_aws_response") from None

        if (
            type(original) is not dict
            or original.get("recipientAccountId") != action.account_id
            or original.get("awsRegion") != action.region
            or original.get("eventID") != identifier
            or original.get("eventCategory", "Management") != "Management"
            or timestamp_ms(original.get("eventTime")) != timestamp
        ):
            raise ExecutionError("invalid_aws_response")

        items.append(
            {"event_id": identifier, "timestamp": timestamp, "event": original}
        )

    return items, filtered or bool(token)
