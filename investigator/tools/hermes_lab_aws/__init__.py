from hermes_cloud_tools.plugin import PluginContext, register_tools

from . import schemas, tools

TOOLSET = "hermes_lab_aws"


def register(ctx: PluginContext) -> None:
    register_tools(
        ctx,
        TOOLSET,
        (
            (schemas.LIST_RUN_OBJECTS, tools.list_run_objects),
            (schemas.READ_RUN_OBJECT, tools.read_run_object),
            (schemas.LIST_AWS_RESOURCES, tools.list_aws_resources),
            (schemas.LIST_S3_OBJECTS, tools.list_s3_objects),
            (schemas.READ_S3_OBJECT, tools.read_s3_object),
        ),
    )
