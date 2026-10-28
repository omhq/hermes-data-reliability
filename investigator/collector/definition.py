import hashlib
import json
import re
from pathlib import Path
from typing import Any, Collection, Hashable

import yaml

MAX_DEFINITION_BYTES = 65536
MAX_RULES = 64
MAX_CONDITIONS = 128
SEVERITIES = {"warning": 1, "error": 2, "critical": 3}
DEFAULT_OBSERVATIONS = (
    Path(__file__).resolve().parents[1] / "runtime/observations.yaml"
)


class DefinitionError(ValueError):
    """Configuration errors exclude raw YAML and log contents."""


class Loader(yaml.SafeLoader):
    depth = 0
    nodes = 0

    def compose_node(
        self,
        parent: yaml.Node | None,
        # PyYAML passes nodes and None here beyond its int-only type stub.
        index: Any,
    ) -> yaml.Node | None:
        self.nodes += 1

        if (
            self.check_event(yaml.AliasEvent)
            or self.depth >= 16
            or self.nodes > 5000
        ):
            raise DefinitionError("YAML aliases or excessive depth/size")

        self.depth += 1

        try:
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Hashable, Any]:
        result = {}

        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)

            if type(key) is not str or key in result:
                raise DefinitionError("Duplicate or invalid YAML key")

            result[key] = self.construct_object(value_node, deep=deep)

        return result


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


def duration(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not str:
        raise DefinitionError("Duration must use s, m, or h")

    matched = re.fullmatch(r"(0|[1-9][0-9]{0,5})([smh])", value)

    if not matched:
        raise DefinitionError("Duration must use s, m, or h")

    seconds = int(matched[1]) * {"s": 1, "m": 60, "h": 3600}[matched[2]]

    if not minimum <= seconds <= maximum:
        raise DefinitionError("Duration is outside the supported limits")

    return seconds


def name(value: object) -> None:
    if type(value) is not str or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_-]{0,79}", value
    ):
        raise DefinitionError("Invalid source or rule name")


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


def fingerprint(value: object) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


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


def investigation_settings(
    definition: dict[str, Any], source: dict[str, Any]
) -> dict[str, str]:
    """Resolve shared YAML defaults and optional source-specific overrides."""
    return {**definition["investigation"], **source.get("investigation", {})}


def validate(
    value: object, approved: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Reject unsupported sources and locations before any provider reads."""
    value = fields(value, {"version", "sources", "investigation"})

    if type(value["version"]) is not int or value["version"] != 1:
        raise DefinitionError("Unsupported observation version")

    validate_investigation(value["investigation"], defaults=True)

    sources = value["sources"]

    if type(sources) is not dict or not 1 <= len(sources) <= 4:
        raise DefinitionError("Define between one and four sources")

    locations = set()

    for source_id, source in sources.items():
        name(source_id)
        fields(
            source,
            {"connector", "region", "log_group", "collection", "rules"},
            {"investigation"},
        )

        if source["connector"] != "cloudwatch_logs":
            raise DefinitionError("Only cloudwatch_logs is supported")

        for key, pattern in (
            ("region", r"[a-z]{2}(?:-[a-z]+)+-[0-9]+"),
            ("log_group", r"[./A-Za-z0-9_#-]{1,512}"),
        ):
            if type(source[key]) is not str or not re.fullmatch(
                pattern, source[key]
            ):
                raise DefinitionError("Invalid CloudWatch location")
            if approved is not None and source[key] != approved.get(key):
                raise DefinitionError("CloudWatch location is not authorized")

        location = (source["region"], source["log_group"])

        if location in locations:
            raise DefinitionError("A CloudWatch location may appear only once")

        locations.add(location)

        collection = source["collection"]

        fields(
            collection,
            {"mode", "poll_interval", "initial_lookback", "overlap"},
        )

        if collection["mode"] != "incremental":
            raise DefinitionError("Only incremental collection is supported")

        duration(collection["poll_interval"], 15, 3600)
        duration(collection["initial_lookback"], 1, 86400)
        duration(collection["overlap"], 0, 3600)

        rules = source["rules"]

        if type(rules) is not dict or not 1 <= len(rules) <= MAX_RULES:
            raise DefinitionError("Define between one and 64 rules")

        for rule_id, rule in rules.items():
            name(rule_id)
            fields(rule, {"severity", "match"})

            if (
                type(rule["severity"]) is not str
                or rule["severity"] not in SEVERITIES
            ):
                raise DefinitionError("Unsupported severity")

            condition(rule["match"])

        if "investigation" in source:
            validate_investigation(source["investigation"])

    return value


def load(
    path: str | Path, approved: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Use safe YAML, rejecting aliases, duplicate keys and deep documents."""
    with Path(path).open("rb") as stream:
        payload = stream.read(MAX_DEFINITION_BYTES + 1)

    if len(payload) > MAX_DEFINITION_BYTES:
        raise DefinitionError("Observation definition exceeds 64 KiB")

    try:
        value = yaml.load(payload, Loader=Loader)
    except yaml.YAMLError, UnicodeError, RecursionError:
        raise DefinitionError("Invalid observation YAML") from None

    return validate(value, approved)
