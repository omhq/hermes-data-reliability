import re
from datetime import UTC, datetime


class InvalidDefinition(ValueError):
    pass


def mapping(
    value: object,
    required: set[str],
    optional: frozenset[str] | set[str] = frozenset(),
) -> dict[str, object]:
    if (
        type(value) is not dict
        or not required <= value.keys()
        or not value.keys() <= required | optional
    ):
        raise InvalidDefinition("Unexpected pipeline-health fields")

    return value


def name(value: object) -> str:
    if type(value) is not str or not re.fullmatch(
        r"[a-z][a-z0-9_]{0,47}", value
    ):
        raise InvalidDefinition("Use a named pipeline-health identifier")

    return value


def duration(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not str or not re.fullmatch(
        r"[1-9][0-9]{0,5}[smh]", value
    ):
        raise InvalidDefinition("Use integer s, m, or h durations")

    seconds = int(value[:-1]) * {"s": 1, "m": 60, "h": 3600}[value[-1]]

    if not minimum <= seconds <= maximum:
        raise InvalidDefinition("Duration exceeds pipeline-health limits")

    return seconds


def timestamp(value: object) -> int:
    if type(value) is not str or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value
    ):
        raise InvalidDefinition("Use a UTC timestamp with seconds and Z")

    try:
        result = int(datetime.fromisoformat(value).timestamp())
    except ValueError, OverflowError:
        raise InvalidDefinition("Invalid UTC timestamp") from None

    if result < 1:
        raise InvalidDefinition("Timestamp must follow the Unix epoch")

    return result


def utc_text(value: int) -> str:
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
