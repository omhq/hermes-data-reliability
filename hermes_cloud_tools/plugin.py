from collections.abc import Callable
from copy import deepcopy
from typing import Any, Protocol


class PluginContext(Protocol):
    def register_tool(
        self,
        *,
        name: str,
        toolset: str,
        schema: dict[str, Any],
        handler: Callable[..., str],
    ) -> object: ...


def register_tools(
    context: PluginContext,
    toolset: str,
    definitions: tuple[tuple[dict[str, Any], Callable[..., str]], ...],
) -> None:
    for schema, handler in definitions:
        context.register_tool(
            name=schema["name"],
            toolset=toolset,
            schema=deepcopy(schema),
            handler=handler,
        )
