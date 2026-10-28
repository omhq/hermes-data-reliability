from collections.abc import Callable
from copy import deepcopy
from typing import Any, Protocol

from . import schemas, tools

TOOLSET = "aws_cloudwatch"


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
        (
            schemas.DESCRIBE_CLOUDWATCH_SCOPE,
            tools.describe_cloudwatch_scope,
        ),
        (schemas.SEARCH_RUN_LOGS, tools.search_run_logs),
        (schemas.SEARCH_LOG_EVENTS, tools.search_log_events),
    )

    for schema, handler in definitions:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=deepcopy(schema),
            handler=handler,
        )
