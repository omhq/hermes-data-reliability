import time
from typing import Any

from .models import PolicyDenied


def require(condition: bool, code: str) -> None:
    if not condition:
        raise PolicyDenied(code)


def integer(value: object, minimum: int, maximum: int, code: str) -> int:
    # bool is a subclass of int. It is not a valid count or timestamp.
    if type(value) is not int or not minimum <= value <= maximum:
        raise PolicyDenied(code)

    return value


def fields(
    value: object, required: set[str], optional: set[str]
) -> dict[str, Any]:
    """Return validated request fields without coercing untrusted values."""
    if type(value) is not dict:
        raise PolicyDenied("invalid_arguments")

    require(all(type(key) is str for key in value), "invalid_arguments")
    require(required <= value.keys(), "missing_arguments")
    require(value.keys() <= required | optional, "unexpected_arguments")
    return value


def wall_time_ms() -> int:
    return time.time_ns() // 1_000_000
