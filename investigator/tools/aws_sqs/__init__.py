from collections.abc import Callable
from copy import deepcopy
from typing import Any, Protocol

from . import schemas, tools

TOOLSET = "aws_sqs"


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
        (schemas.DESCRIBE_SQS_SCOPE, tools.describe_sqs_scope),
        (schemas.GET_SQS_QUEUE_ATTRIBUTES, tools.get_sqs_queue_attributes),
    )

    for schema, handler in definitions:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=deepcopy(schema),
            handler=handler,
        )
