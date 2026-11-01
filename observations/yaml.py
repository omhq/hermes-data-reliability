from pathlib import Path
from typing import Any, Hashable

import yaml

from .configuration import InvalidConfiguration


class Loader(yaml.SafeLoader):
    depth = 0
    nodes = 0

    def compose_node(
        self, parent: yaml.Node | None, index: Any
    ) -> yaml.Node | None:
        self.nodes += 1

        if (
            self.check_event(yaml.AliasEvent)
            or self.depth >= 16
            or self.nodes > 20000
        ):
            raise InvalidConfiguration("YAML aliases or excessive depth/size")

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
                raise InvalidConfiguration("Duplicate or invalid YAML key")

            result[key] = self.construct_object(value_node, deep=deep)

        return result


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.load(path.read_bytes(), Loader=Loader)
    except yaml.YAMLError, UnicodeError, RecursionError:
        raise InvalidConfiguration("Invalid configuration YAML") from None

    if type(value) is not dict:
        raise InvalidConfiguration("Configuration must be a mapping")

    return value


class Dumper(yaml.SafeDumper):
    pass


def text(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    return dumper.represent_scalar(
        "tag:yaml.org,2002:str", value, "|" if "\n" in value else None
    )


def dump_yaml(value: dict[str, Any]) -> bytes:
    Dumper.add_representer(str, text)
    return yaml.dump(value, Dumper=Dumper, sort_keys=False).encode()
