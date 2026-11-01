from typing import TYPE_CHECKING, Any, Callable

from tool_boundary.models import PolicyDenied, PolicyLimits, ResourceContext
from tool_boundary.resource_policy import ResourcePolicy
from tool_boundary.validation import fields, integer, require, wall_time_ms

from .adapters import ADAPTERS
from .definition import Expectation, InvalidDefinition, Pipeline
from .schedules import Occurrence

if TYPE_CHECKING:
    from tool_boundary.models import ApprovedAction
    from tool_boundary.registry import ConfiguredTools

    from .evaluator import Capability
    from .observations import Target

TOOLS = frozenset(
    {
        "list_pipeline_expectations",
        "check_pipeline_expectations",
        "get_pipeline_check_history",
    }
)


class HealthPolicy(ResourcePolicy):
    """Bind checks and supporting reads to configured deliveries."""

    def __init__(
        self,
        context: ResourceContext,
        capability: Capability,
        clock: Callable[[], int] = wall_time_ms,
        selected: tuple[str, str, str] | None = None,
        incident_id: str = "",
        providers: ConfiguredTools | None = None,
    ) -> None:
        if selected is None:
            super().__init__(context, clock=clock, providers=providers)
        else:
            self._initialize(context, PolicyLimits(), clock)

        self.capability = capability
        self.selected = selected
        self.incident_id = incident_id

    def delivery(
        self,
        arguments: dict[str, Any],
    ) -> tuple[Pipeline, Expectation, Occurrence]:
        pipeline_id = arguments["pipeline_id"]
        require(
            type(pipeline_id) is str
            and pipeline_id in self.capability.pipelines,
            "pipeline_not_allowed",
        )
        pipeline = self.capability.pipelines[pipeline_id]

        try:
            expectation = pipeline.expectation(arguments["expectation_id"])
            occurrence = pipeline.occurrence(arguments["occurrence"])
        except InvalidDefinition:
            raise PolicyDenied("invalid_delivery") from None

        if self.selected is not None:
            require(
                self.selected
                == (pipeline_id, expectation.name, occurrence.identifier),
                "wrong_delivery",
            )
        else:
            require(
                self._context.start_time_ms
                <= occurrence.scheduled_at * 1000
                <= min(self._clock(), self._context.end_time_ms),
                "outside_delivery_window",
            )

        return pipeline, expectation, occurrence

    def _authorize(self, request: object) -> ApprovedAction:
        require(self.capability.consumer.enabled, "consumer_disabled")
        request = fields(request, {"tool", "arguments"}, set())
        tool = request["tool"]

        if type(tool) is not str or tool not in TOOLS:
            scoped = self.selected is not None
            targets: tuple[Target, ...] = ()
            arguments = request["arguments"]

            if self.selected is not None:
                require(type(tool) is str, "unknown_tool")
                arguments = request["arguments"]
                require(type(arguments) is dict, "invalid_arguments")
                pipeline, expectation, occurrence = self.delivery(
                    {
                        "pipeline_id": self.selected[0],
                        "expectation_id": self.selected[1],
                        "occurrence": self.selected[2],
                    }
                )
                targets = self.capability.targets(
                    pipeline, expectation, occurrence
                )
                require(
                    any(
                        ADAPTERS[target.adapter].permits_read(
                            target, tool, arguments
                        )
                        for target in targets
                    ),
                    "resource_not_allowed",
                )

            action = super()._authorize(request)

            if scoped:
                for target in targets:
                    adapter = ADAPTERS[target.adapter]

                    if adapter.permits_read(target, tool, arguments):
                        return adapter.constrain_read(target, action)

            return action

        now = self._clock()
        require(now < self._context.expires_at_ms, "context_expired")
        arguments = fields(
            request["arguments"],
            set()
            if tool == "list_pipeline_expectations"
            else {"pipeline_id", "expectation_id", "occurrence"},
            {"max_bytes", "pipeline_id"}
            if tool == "list_pipeline_expectations"
            else {"max_bytes"},
        )
        maximum = integer(
            arguments.get("max_bytes", 65536),
            1024,
            262144,
            "invalid_byte_limit",
        )

        if tool == "list_pipeline_expectations":
            parameters: dict[str, Any] = {}
            operation = "DescribePipelineExpectations"

            if "pipeline_id" in arguments:
                pipeline_id = arguments["pipeline_id"]
                require(
                    type(pipeline_id) is str
                    and pipeline_id in self.capability.pipelines,
                    "pipeline_not_allowed",
                )
                require(
                    self.selected is None or pipeline_id == self.selected[0],
                    "wrong_delivery",
                )
                parameters["Pipeline"] = pipeline_id
        else:
            pipeline, expectation, occurrence = self.delivery(arguments)
            parameters = {
                "Pipeline": pipeline.name,
                "Expectation": expectation.name,
                "Occurrence": occurrence.identifier,
                "Revision": pipeline.revision,
                "SourceScope": self.capability.target(
                    pipeline, expectation, occurrence
                ).scope,
            }
            operation = (
                "CheckPipelineExpectations"
                if tool == "check_pipeline_expectations"
                else "GetPipelineCheckHistory"
            )

        return self._reserve(
            tool,
            ("pipeline_health", operation, parameters, None, None),
            maximum,
            now,
        )
