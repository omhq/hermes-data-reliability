from collections.abc import Callable
from copy import deepcopy
from typing import Any, Protocol

from . import schemas, tools

TOOLSET = "pipeline_health"


class PluginContext(Protocol):
    def register_tool(
        self,
        *,
        name: str,
        toolset: str,
        schema: dict[str, Any],
        handler: Callable[..., str],
    ) -> object: ...


def register(ctx: PluginContext) -> None:
    definitions = (
        (schemas.LIST_PIPELINE_EXPECTATIONS, tools.list_pipeline_expectations),
        (
            schemas.CHECK_PIPELINE_EXPECTATIONS,
            tools.check_pipeline_expectations,
        ),
        (schemas.GET_PIPELINE_CHECK_HISTORY, tools.get_pipeline_check_history),
    )

    for schema, handler in definitions:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=deepcopy(schema),
            handler=handler,
        )
