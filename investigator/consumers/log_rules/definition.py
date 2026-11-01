from pathlib import Path
from typing import Any

from incidents.specification import DefinitionError, validate_investigation
from observations.configuration import fields, name
from observations.subscriptions import manifest
from observations.yaml import load_yaml

SEVERITIES = {"warning": 1, "error": 2, "critical": 3}
MAX_CONDITIONS = 128


def condition(
    value: object, depth: int = 0, count: list[int] | None = None
) -> None:
    count = [0] if count is None else count
    count[0] += 1

    if depth > 8 or count[0] > MAX_CONDITIONS:
        raise DefinitionError("Match expression is too complex")
    if type(value) is not dict or len(value) != 1:
        raise DefinitionError("Each match needs exactly one operator")

    operator, operand = next(iter(value.items()))

    if operator in {"contains", "regex"}:
        if type(operand) is not str or not 1 <= len(operand) <= 1024:
            raise DefinitionError(
                "Match text must contain 1 to 1024 characters"
            )
    elif operator in {"any", "all"}:
        if type(operand) is not list or not 1 <= len(operand) <= 32:
            raise DefinitionError("any/all needs 1 to 32 conditions")

        for child in operand:
            condition(child, depth + 1, count)
    else:
        raise DefinitionError("Unsupported match operator")


def validate(value: object) -> dict[str, Any]:
    definition = fields(
        value, {"version", "consumer", "investigation", "rules"}
    )

    if type(definition["version"]) is not int or definition["version"] != 1:
        raise DefinitionError("Log rules require version 1")

    consumer = manifest(definition)
    validate_investigation(definition["investigation"], defaults=True)
    rules = definition["rules"]

    if type(rules) is not dict or not 1 <= len(rules) <= 64:
        raise DefinitionError("Define one to sixty-four log rules")

    subscribed = {item.source for item in consumer.subscriptions}

    for identifier, rule in rules.items():
        name(identifier)
        fields(rule, {"source", "severity", "match"})

        if rule["source"] not in subscribed:
            raise DefinitionError("A rule must use a subscribed source")
        if (
            type(rule["severity"]) is not str
            or rule["severity"] not in SEVERITIES
        ):
            raise DefinitionError("Unsupported severity")

        condition(rule["match"])

    return definition


def load(path: Path) -> dict[str, Any]:
    return validate(load_yaml(path))
