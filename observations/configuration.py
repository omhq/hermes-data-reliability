import hashlib
import json
import re
from typing import Any


class InvalidConfiguration(ValueError):
    pass


def fields(
    value: object,
    required: set[str],
    optional: frozenset[str] | set[str] = frozenset(),
) -> dict[str, Any]:
    if (
        type(value) is not dict
        or not required <= value.keys()
        or not value.keys() <= required | optional
    ):
        raise InvalidConfiguration("Unexpected configuration fields")

    return value


def name(value: object) -> str:
    if type(value) is not str or not re.fullmatch(
        r"[a-z][a-z0-9_]{0,47}", value
    ):
        raise InvalidConfiguration("Use a named configuration identifier")

    return value


def duration(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not str or not re.fullmatch(
        r"(0|[1-9][0-9]{0,5})[smh]", value
    ):
        raise InvalidConfiguration("Use integer s, m, or h durations")

    seconds = int(value[:-1]) * {"s": 1, "m": 60, "h": 3600}[value[-1]]

    if not minimum <= seconds <= maximum:
        raise InvalidConfiguration("Duration is outside supported limits")

    return seconds


def fingerprint(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
