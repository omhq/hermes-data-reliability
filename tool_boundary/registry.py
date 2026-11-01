import inspect
import re
from typing import TYPE_CHECKING, Any

from .models import PolicyDenied
from .services.base import Scope, ToolProvider
from .validation import require

if TYPE_CHECKING:
    from .catalog import ToolPackages
    from .contracts import ToolExtension
    from .models import ResolvedRead


def _validate_provider(provider: type[ToolProvider]) -> None:
    if not issubclass(provider, ToolProvider) or inspect.isabstract(provider):
        raise ValueError("Incomplete tool provider implementation")

    for field in ("package", "service", "toolset"):
        value = getattr(provider, field, None)

        if (
            type(value) is not str
            or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is None
        ):
            raise ValueError("Invalid tool provider identifier")

    modes = provider.modes

    if (
        type(modes) is not dict
        or not modes
        or not modes.keys() <= {"run", "log", "resources"}
    ):
        raise ValueError("Invalid tool provider modes")

    for tools in modes.values():
        if type(tools) is not frozenset or not tools:
            raise ValueError("Invalid tool provider tools")

        for tool in tools:
            if (
                type(tool) is not str
                or re.fullmatch(r"[a-z][a-z0-9_]{0,95}", tool) is None
            ):
                raise ValueError("Invalid tool provider tool name")


class ToolRegistry:
    def __init__(self, *providers: type[ToolProvider]) -> None:
        self.packages: dict[str, type[ToolProvider]] = {}
        self.tools: dict[str, str] = {}
        self.services: dict[str, str] = {}

        for provider in providers:
            _validate_provider(provider)

            if (
                provider.package in self.packages
                or provider.service in self.services
            ):
                raise ValueError("Duplicate tool provider registration")

            self.packages[provider.package] = provider
            self.services[provider.service] = provider.package

            for tool in provider.tools():
                if tool in self.tools:
                    raise ValueError("Duplicate tool registration")

                self.tools[tool] = provider.package

    def package(self, name: str) -> type[ToolProvider]:
        if name not in self.packages:
            raise ValueError("Unknown registered tool package")

        return self.packages[name]


class ConfiguredTools:
    def __init__(
        self,
        registry: ToolRegistry,
        selection: ToolPackages,
        configuration: dict[str, Any],
        extension: ToolExtension | None = None,
    ) -> None:
        self.registry = registry
        self.selection = selection
        self.configuration = configuration
        self.extension = extension
        self.instances: dict[str, ToolProvider] = {}

        for name in selection.enabled:
            self.package(name).resources()

    def package(self, name: str) -> ToolProvider:
        if name not in self.instances:
            provider = self.registry.package(name)
            instance = provider(self.configuration, self.extension)
            instance.catalog = self
            self.instances[name] = instance

        return self.instances[name]

    def tool(self, name: object) -> ToolProvider:
        if type(name) is not str or name not in self.registry.tools:
            raise PolicyDenied("unknown_tool")
        package = self.registry.tools[name]
        require(package in self.selection.enabled, "tool_disabled")
        return self.package(package)

    def service(self, name: str) -> ToolProvider:
        """Resolve trusted internal plans; public requests use tool()."""
        if name not in self.registry.services:
            raise PolicyDenied("invalid_execution_plan")

        return self.package(self.registry.services[name])

    def validate_scope(self, scope: Scope) -> None:
        for name in self.selection.enabled:
            provider = self.package(name)

            if provider.modes.get(scope.mode):
                provider.validate_scope(scope)

    def authorize(
        self,
        scope: Scope,
        tool: str,
        arguments: dict[str, Any],
        maximum: int,
        now: int,
    ) -> ResolvedRead:
        provider = self.tool(tool)
        require(
            tool in provider.modes.get(scope.mode, frozenset()), "unknown_tool"
        )
        provider.validate_scope(scope)
        return provider.authorize(scope, tool, arguments, maximum, now)

    def describe(self) -> dict[str, Any]:
        result = {}

        for name in self.selection.enabled:
            result.update(self.package(name).resources())

        return result
