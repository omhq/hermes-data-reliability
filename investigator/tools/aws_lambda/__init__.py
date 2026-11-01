from collections.abc import Callable
from copy import deepcopy
from typing import Any, Protocol

from . import schemas, tools

TOOLSET = "aws_lambda"


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
        (schemas.DESCRIBE_LAMBDA_SCOPE, tools.describe_lambda_scope),
        (
            schemas.GET_LAMBDA_FUNCTION_CONFIGURATION,
            tools.get_lambda_function_configuration,
        ),
        (
            schemas.GET_LAMBDA_FUNCTION_CONCURRENCY,
            tools.get_lambda_function_concurrency,
        ),
        (
            schemas.GET_LAMBDA_EVENT_SOURCE_MAPPING,
            tools.get_lambda_event_source_mapping,
        ),
    )

    for schema, handler in definitions:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=deepcopy(schema),
            handler=handler,
        )
