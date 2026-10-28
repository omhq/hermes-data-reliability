from collections.abc import Callable
from copy import deepcopy
from typing import Any, Protocol

from . import schemas, tools

TOOLSET = "hermes_lab_aws"


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
        (schemas.LIST_RUN_OBJECTS, tools.list_run_objects),
        (schemas.READ_RUN_OBJECT, tools.read_run_object),
        (schemas.LIST_AWS_RESOURCES, tools.list_aws_resources),
        (schemas.LIST_S3_OBJECTS, tools.list_s3_objects),
        (schemas.READ_S3_OBJECT, tools.read_s3_object),
    )

    for schema, handler in definitions:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=deepcopy(schema),
            handler=handler,
        )
