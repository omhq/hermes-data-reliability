import json
from typing import Any, Collection


class DefinitionError(ValueError):
    """Configuration errors exclude raw YAML and log contents."""


def fields(
    value: object, required: Collection[str], optional: Collection[str] = ()
) -> dict[str, Any]:
    """Return the mapping after checking its keys and required fields."""
    if (
        type(value) is not dict
        or not set(required) <= value.keys()
        or not value.keys() <= set(required) | set(optional)
        or any(type(key) is not str for key in value)
    ):
        raise DefinitionError("Unexpected or missing configuration fields")

    return value


def validate_investigation(value: object, *, defaults: bool = False) -> None:
    limits = {"objective": 4000, "instructions": 8000}
    value = fields(
        value, limits if defaults else (), () if defaults else limits
    )

    for key, text in value.items():
        maximum = limits[key]

        if type(text) is not str or not text.strip() or len(text) > maximum:
            raise DefinitionError("Invalid investigation " + key)
        if len(json.dumps(text, ensure_ascii=False).encode()) > maximum * 2:
            raise DefinitionError(
                "Investigation " + key + " exceeds byte limit"
            )
