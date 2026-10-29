import json
import math
from typing import Any, NoReturn


def unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}

    for key, value in pairs:
        if key in result:
            raise ValueError("Invalid JSON")

        result[key] = value

    return result


def reject_constant(value: str) -> NoReturn:
    raise ValueError("Invalid JSON")


def finite_float(value: str) -> float:
    result = float(value)

    if not math.isfinite(result):
        raise ValueError("Invalid JSON")

    return result


def decode(payload: str | bytes | bytearray) -> Any:
    """Parse external JSON without ambiguous fields or raw error payloads."""
    try:
        return json.loads(
            payload,
            object_pairs_hook=unique_fields,
            parse_constant=reject_constant,
            parse_float=finite_float,
        )
    except ValueError, UnicodeError, RecursionError:
        raise ValueError("Invalid JSON") from None
