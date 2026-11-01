import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from tool_boundary.catalog import ToolPackages
from tool_boundary.models import PolicyDenied, ResourceContext
from tool_boundary.providers.aws_identity import AwsIdentity
from tool_boundary.validation import fields, integer, require

from . import dispatch, monitor, scopes
from .collection import with_collection
from .definition import InvalidDefinition, parse
from .evaluator import Capability
from .history import History
from .policy import HealthPolicy

if TYPE_CHECKING:
    from tool_boundary.contracts import ReadBackend
    from tool_boundary.executor import Executor, Session
    from tool_boundary.models import ApprovedAction
    from tool_boundary.process import Deadline
    from tool_boundary.registry import ConfiguredTools
    from tool_boundary.resource_policy import ResourcePolicy
    from tool_boundary.services.base import Scope


class Extension:
    service = "pipeline_health"

    def validate_scope(self, scope: Scope) -> None:
        AwsIdentity.validate_scope(scope.context, self.config)

    def __init__(self, config: dict[str, Any], capability: Capability) -> None:
        self.config = config
        self.capability = capability
        self.enabled = (
            "hermes-pipeline-health"
            in ToolPackages.from_executor_config(config).enabled
            and capability.consumer.enabled
        )

    def accepts_context(self, request: object) -> bool:
        return type(request) is dict and "pipeline_id" in request

    def context(
        self, request: object, clock: Callable[[], int]
    ) -> tuple[ResourcePolicy, ResourceContext, int]:
        request = fields(
            request,
            {
                "pipeline_id",
                "expectation_id",
                "occurrence",
                "revision",
                "source_scope",
            },
            {"ttl_seconds"},
        )
        require(self.enabled, "tool_disabled")
        name = request["pipeline_id"]
        require(
            type(name) is str and name in self.capability.pipelines,
            "pipeline_not_allowed",
        )
        pipeline = self.capability.pipelines[name]
        require(
            request["revision"] == pipeline.revision,
            "pipeline_definition_changed",
        )

        try:
            expectation = pipeline.expectation(request["expectation_id"])
            occurrence = pipeline.occurrence(request["occurrence"])
        except InvalidDefinition:
            raise PolicyDenied("invalid_delivery") from None

        targets = self.capability.targets(pipeline, expectation, occurrence)
        require(
            request["source_scope"] == targets[0].scope, "source_scope_changed"
        )
        scheduled = occurrence.scheduled_at * 1000
        require(scheduled <= clock(), "future_delivery")
        ttl = integer(request.get("ttl_seconds", 300), 1, 300, "invalid_ttl")
        context = scopes.context(
            self.config,
            targets,
            max(1, scheduled - 300000),
            pipeline.deadline(occurrence) * 1000 + 300000,
            clock() + ttl * 1000,
        )
        policy = HealthPolicy(
            context,
            self.capability,
            clock,
            (name, expectation.name, occurrence.identifier),
            self.capability.identifier(pipeline, expectation, occurrence),
        )
        return policy, context, ttl

    def resource_policy(
        self,
        context: ResourceContext,
        clock: Callable[[], int],
        providers: ConfiguredTools,
    ) -> ResourcePolicy:
        return HealthPolicy(
            context,
            self.capability,
            clock,
            providers=providers,
        )

    def execute(
        self,
        session: Session,
        action: ApprovedAction,
        backend: ReadBackend,
        deadline: Deadline,
        now: int,
    ) -> dict[str, Any]:
        return dispatch.execute(
            self.capability, session, action, backend, deadline, now
        )

    def references(self, data: dict[str, Any]) -> list[dict[str, str]]:
        return dispatch.references(data)

    def control(
        self, executor: Executor, request: dict[str, Any]
    ) -> dict[str, Any] | None:
        if request.get("operation") == "poll_pipeline_health":
            fields(request, {"operation"}, set())
            return monitor.poll(executor, self.capability)
        if request.get("operation") == "acknowledge_pipeline_health":
            return monitor.acknowledge(self.capability, request)

        return None


def load(config: dict[str, Any], state: Path, source: Path) -> Extension:
    definition = parse(json.loads(source.read_bytes()))
    plan = json.loads(Path(config["collection_plan"]).read_bytes())
    config = with_collection(config, definition, plan)
    return Extension(
        config,
        Capability(
            definition,
            History(state / "pipeline-health.sqlite3"),
            config,
            Path(config["observation_store"]),
        ),
    )
