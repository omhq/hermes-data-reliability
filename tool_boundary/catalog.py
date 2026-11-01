from dataclasses import dataclass
from typing import Any, Self

from .registered import REGISTRY
from .registry import ToolRegistry


def names(value: object, allowed: set[str]) -> tuple[str, ...]:
    if type(value) is not list or len(value) > 50:
        raise ValueError("Invalid tool package configuration")

    result = []

    for item in value:
        if type(item) is not str or item not in allowed or item in result:
            raise ValueError("Invalid tool package configuration")

        result.append(item)

    return tuple(result)


@dataclass(frozen=True)
class ToolPackages:
    enabled: tuple[str, ...]
    options: dict[str, dict[str, Any]]
    registry: ToolRegistry = REGISTRY

    @classmethod
    def from_agent_config(
        cls, config: object, registry: ToolRegistry = REGISTRY
    ) -> Self:
        if type(config) is not dict or type(config.get("plugins")) is not dict:
            raise ValueError("Agent configuration requires plugins")

        plugins = config["plugins"]
        allowed = set(registry.packages)
        active = names(plugins.get("enabled", []), allowed)
        disabled = names(plugins.get("disabled", []), allowed)
        selected = tuple(name for name in active if name not in disabled)
        entries = plugins.get("entries", {})

        if type(entries) is not dict or not entries.keys() <= allowed:
            raise ValueError("Invalid plugin entries")

        options = {}

        for name in set(selected) | set(entries):
            provider = registry.package(name)
            parsed = provider.agent_options(
                entries.get(name, {}), name in selected
            )

            if name in selected:
                options[name] = parsed

        return cls(selected, options, registry)

    @classmethod
    def from_executor_config(
        cls, config: object, registry: ToolRegistry = REGISTRY
    ) -> Self:
        if type(config) is not dict:
            raise ValueError("Invalid executor tool package configuration")

        selected = names(config.get("tool_packages"), set(registry.packages))
        options = {
            name: registry.package(name).host_options(config, True)
            for name in selected
        }
        return cls(selected, options, registry)

    @property
    def tools(self) -> frozenset[str]:
        return frozenset().union(
            *(self.registry.package(name).tools() for name in self.enabled)
        )

    @property
    def toolsets(self) -> tuple[str, ...]:
        return tuple(
            self.registry.package(name).toolset for name in self.enabled
        )

    def tools_for(self, mode: str) -> frozenset[str]:
        return frozenset().union(
            *(
                self.registry.package(name).modes.get(mode, frozenset())
                for name in self.enabled
            )
        )

    def manifest(self) -> dict[str, Any]:
        """Export selection metadata without serializing host configuration."""
        return {
            "version": 1,
            "packages": {
                name: {
                    "enabled": name in self.enabled,
                    "toolset": provider.toolset,
                    "options": self.options.get(name, {}),
                }
                for name, provider in self.registry.packages.items()
            },
        }
