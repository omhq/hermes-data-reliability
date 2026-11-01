from pathlib import Path

from .registry import ToolRegistry
from .scopes import (
    CapabilityContextFactory,
    LogContextFactory,
    ResourceContextFactory,
    RunContextFactory,
)
from .services.capability import CapabilityProvider
from .services.cloudtrail import CloudTrailProvider
from .services.cloudwatch import CloudWatchProvider
from .services.lambda_service import LambdaProvider
from .services.s3 import S3Provider
from .services.sqs import SqsProvider


class PipelineHealthProvider(CapabilityProvider):
    package = "hermes-pipeline-health"
    toolset = "pipeline_health"
    service = "pipeline_health"
    modes = {
        "resources": frozenset(
            {
                "list_pipeline_expectations",
                "check_pipeline_expectations",
                "get_pipeline_check_history",
            }
        )
    }

    @classmethod
    def plugin_directory(cls) -> Path:
        return Path("investigator/capabilities/pipeline_health/tools")


REGISTRY = ToolRegistry(
    S3Provider,
    CloudWatchProvider,
    CloudTrailProvider,
    SqsProvider,
    LambdaProvider,
    PipelineHealthProvider,
)

CONTEXT_FACTORIES = (
    CapabilityContextFactory,
    LogContextFactory,
    ResourceContextFactory,
    RunContextFactory,
)
