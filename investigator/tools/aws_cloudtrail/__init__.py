from collections.abc import Callable
from copy import deepcopy
from typing import Any, Protocol

from . import schemas, tools

TOOLSET = "aws_cloudtrail"


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
            schemas.DESCRIBE_CLOUDTRAIL_SCOPE,
            tools.describe_cloudtrail_scope,
        ),
        (schemas.LOOKUP_CLOUDTRAIL_EVENTS, tools.lookup_cloudtrail_events),
    )

    for schema, handler in definitions:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=deepcopy(schema),
            handler=handler,
        )
