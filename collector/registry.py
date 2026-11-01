import inspect
from typing import Any

from observations.configuration import InvalidConfiguration
from observations.models import Source

from .connectors.base import Connector


class ConnectorRegistry:
    def __init__(self, *connectors: type[Connector]) -> None:
        self.classes: dict[str, type[Connector]] = {}

        for connector in connectors:
            name = getattr(connector, "name", None)

            if (
                not issubclass(connector, Connector)
                or inspect.isabstract(connector)
                or not isinstance(name, str)
                or not name
                or name in self.classes
            ):
                raise InvalidConfiguration("Invalid connector registration")

            self.classes[name] = connector

    def get(self, name: object) -> type[Connector]:
        if type(name) is not str or name not in self.classes:
            raise InvalidConfiguration("Unknown registered connector")

        return self.classes[name]

    def validate(self, value: object) -> dict[str, Any]:
        if type(value) is not dict:
            raise InvalidConfiguration("Source requires a connector")

        return self.get(value.get("connector")).validate_definition(value)

    def configure(self, configuration: dict[str, Any]) -> ConfiguredConnectors:
        return ConfiguredConnectors(self, configuration)


class ConfiguredConnectors:
    def __init__(
        self, registry: ConnectorRegistry, configuration: dict[str, Any]
    ) -> None:
        self.registry = registry
        self.configuration = configuration
        self.instances: dict[str, Connector] = {}

    def get(self, name: str) -> Connector:
        connector = self.registry.get(name)

        if name not in self.instances:
            self.instances[name] = connector(self.configuration)

        return self.instances[name]

    def resolve(self, value: object) -> Source:
        definition = self.registry.validate(value)
        return self.get(definition["connector"]).resolve(definition)

    def authorize(self, source: Source) -> None:
        self.get(source.connector).authorize(source)
