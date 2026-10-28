from hermes_cloud_tools.plugin import PluginContext, register_tools

from . import schemas, tools

TOOLSET = "aws_cloudwatch"


def register(ctx: PluginContext) -> None:
    register_tools(
        ctx,
        TOOLSET,
        (
            (
                schemas.DESCRIBE_CLOUDWATCH_SCOPE,
                tools.describe_cloudwatch_scope,
            ),
            (schemas.SEARCH_RUN_LOGS, tools.search_run_logs),
            (schemas.SEARCH_LOG_EVENTS, tools.search_log_events),
        ),
    )
