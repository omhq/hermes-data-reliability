from typing import TYPE_CHECKING, Any, Callable

from .catalog import ToolPackages
from .registered import CONTEXT_FACTORIES, REGISTRY
from .registry import ConfiguredTools, ToolRegistry
from .scopes import ContextGrant

if TYPE_CHECKING:
    from .contracts import ToolExtension


def load_tools(
    configuration: dict[str, Any],
    extension: ToolExtension | None = None,
    *,
    registry: ToolRegistry = REGISTRY,
) -> ConfiguredTools:
    """Bind registered service classes to a validated host selection."""
    selection = ToolPackages.from_executor_config(configuration, registry)
    return ConfiguredTools(registry, selection, configuration, extension)


class ContextLoader:
    def __init__(
        self,
        configuration: dict[str, Any],
        providers: ConfiguredTools,
        clock: Callable[[], int],
        extension: ToolExtension | None,
    ) -> None:
        self.factories = tuple(
            factory(configuration, providers, clock, extension)
            for factory in CONTEXT_FACTORIES
        )

    def open(self, request: object, owner_uid: int) -> ContextGrant:
        for factory in self.factories:
            if factory.accepts(request):
                return factory.open(request, owner_uid)

        raise ValueError("Unsupported investigation context")
