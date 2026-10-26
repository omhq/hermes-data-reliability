import re
import json

from typing import TYPE_CHECKING, Any

from .wire import ExecutionError, encode

if TYPE_CHECKING:
    from investigator.executor.models import ApprovedAction


def integer_parameter(action: ApprovedAction, name: str, minimum: int) -> int:
    """Read a response bound from the trusted plan without coercing it."""
    value = action.parameters.get(name)

    if type(value) is not int or value < minimum:
        raise ExecutionError("invalid_execution_plan")

    return value


def fit_items(
    name: str, items: list[dict[str, Any]], maximum: int
) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    data = {name: []}

    if len(encode(data)) > maximum:
        raise ExecutionError("response_budget_too_small")

    truncated = False
    for item in items:
        candidate = {name: data[name] + [item]}

        if len(encode(candidate)) > maximum:
            truncated = True
            break

        data = candidate

    return data, truncated


def object_result(
    action: ApprovedAction, metadata: dict[str, Any], body: bytes | None
) -> tuple[dict[str, str], bool]:
    if type(body) is not bytes or len(body) > action.max_bytes:
        raise ExecutionError("invalid_aws_response")

    text = body.decode("utf-8", errors="replace")

    if len(encode({"text": ""})) > action.max_bytes:
        raise ExecutionError("response_budget_too_small")

    low, high = 0, len(text)

    while low < high:
        middle = (low + high + 1) // 2

        if len(encode({"text": text[:middle]})) <= action.max_bytes:
            low = middle
        else:
            high = middle - 1

    content_range = metadata.get("ContentRange", "")
    match = re.fullmatch(r"bytes [0-9]+-([0-9]+)/([0-9]+)", content_range)
    partial = (
        int(match[1]) + 1 < int(match[2]) if match else len(body) >= action.max_bytes
    )

    # Unknown total length at the range limit is conservatively partial.
    return {"text": text[:low]}, partial or low < len(text)


def listing_result(
    action: ApprovedAction, response: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    contents = response.get("Contents", [])

    if (
        type(contents) is not list
        or type(response.get("IsTruncated", False)) is not bool
    ):
        raise ExecutionError("invalid_aws_response")

    scope = action.exact_key or action.key_prefix

    if scope is None:
        raise ExecutionError("invalid_execution_plan")

    maximum = integer_parameter(action, "MaxKeys", 1)
    items = []
    filtered = False

    for entry in contents:
        key = entry.get("Key")
        scoped = type(key) is str and (
            key == scope if action.exact_key else key.startswith(scope)
        )

        if not scoped:
            filtered = True
            continue

        if len(key) > 1024 or type(entry.get("Size")) is not int or entry["Size"] < 0:
            raise ExecutionError("invalid_aws_response")

        items.append({"key": key, "size": entry["Size"]})

    data, partial = fit_items("objects", items[:maximum], action.max_bytes)

    return data, (
        partial
        or filtered
        or len(items) > maximum
        or response.get("IsTruncated", False)
        or bool(response.get("NextContinuationToken"))
    )


def logs_result(
    action: ApprovedAction, response: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    events = response.get("events", [])

    if type(events) is not list:
        raise ExecutionError("invalid_aws_response")

    start = integer_parameter(action, "startTime", 0)
    end = integer_parameter(action, "endTime", start)
    maximum = integer_parameter(action, "limit", 1)
    items = []
    filtered = False

    for event in events:
        timestamp = event.get("timestamp")
        message = event.get("message")

        if type(timestamp) is not int or type(message) is not str:
            raise ExecutionError("invalid_aws_response")

        try:
            structured = json.loads(message)
        except ValueError, RecursionError:
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

        items.append({"event_id": event_id, "timestamp": timestamp, "message": message})

    data, partial = fit_items("events", items[:maximum], action.max_bytes)

    return data, partial or filtered or len(items) > maximum or bool(
        response.get("nextToken")
    )


def bounded_result(
    action: ApprovedAction, response: object, body: bytes | None
) -> tuple[dict[str, Any], bool]:
    if type(response) is not dict:
        raise ExecutionError("invalid_aws_response")

    if action.operation == "DescribeResources":
        if len(encode(response)) > action.max_bytes:
            raise ExecutionError("response_budget_too_small")
        return response, False
    if action.operation == "GetObject":
        return object_result(action, response, body)
    if action.operation == "ListObjectsV2":
        return listing_result(action, response)

    return logs_result(action, response)
