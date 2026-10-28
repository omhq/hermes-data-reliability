from hermes_cloud_tools.plugin import PluginContext, register_tools

from . import schemas, tools

TOOLSET = "aws_cloudtrail"


def register(ctx: PluginContext) -> None:
    register_tools(
        ctx,
        TOOLSET,
        (
            (
                schemas.DESCRIBE_CLOUDTRAIL_SCOPE,
                tools.describe_cloudtrail_scope,
            ),
            (schemas.LOOKUP_CLOUDTRAIL_EVENTS, tools.lookup_cloudtrail_events),
        ),
    )
