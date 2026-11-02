import sqlite3
import time
from typing import TYPE_CHECKING, Any

from observations.query import event_presence
from tool_boundary.wire import ExecutionError

from .policy import HealthPolicy

if TYPE_CHECKING:
    from tool_boundary.contracts import ReadBackend
    from tool_boundary.executor import Session
    from tool_boundary.models import ApprovedAction
    from tool_boundary.process import Deadline

    from .evaluator import Capability


def execute(
    capability: Capability,
    session: Session,
    action: ApprovedAction,
    backend: ReadBackend,
    deadline: Deadline,
    now: int,
) -> dict[str, Any]:
    policy = session.policy

    if not isinstance(policy, HealthPolicy):
        raise ExecutionError("pipeline_scope_required")

    if not capability.lock.acquire(timeout=deadline.remaining()):
        raise ExecutionError("capability_busy")

    try:
        deadline.remaining()

        if action.operation == "DescribePipelineExpectations":
            selected = policy.selected
            requested = action.parameters.get("Pipeline")
            pipelines = [
                pipeline
                for pipeline in capability.pipelines.values()
                if (selected is None or pipeline.name == selected[0])
                and (requested is None or pipeline.name == requested)
            ]
            source_names = {
                source.name
                for pipeline in pipelines
                for source in (
                    *(item.source for item in pipeline.expectations),
                    *pipeline.investigation_sources,
                )
            }
            descriptions: dict[str, Any] = {
                "pipelines": [
                    pipeline.describe(
                        include_instructions=(
                            selected is not None or requested is not None
                        )
                    )
                    for pipeline in pipelines
                ],
                "sources": {
                    name: {
                        **capability.sources[name].describe(),
                        "approved_resource": dict(
                            capability.resources[
                                capability.sources[name].resource
                            ]
                        ),
                    }
                    for name in sorted(source_names)
                },
            }

            if selected is not None:
                pipeline, expectation, occurrence = policy.delivery(
                    {
                        "pipeline_id": selected[0],
                        "expectation_id": selected[1],
                        "occurrence": selected[2],
                    }
                )
                descriptions["targets"] = [
                    target.describe()
                    for target in capability.targets(
                        pipeline, expectation, occurrence
                    )
                ]

            return descriptions

        pipeline = capability.pipelines[str(action.parameters["Pipeline"])]
        expectation = pipeline.expectation(action.parameters["Expectation"])
        occurrence = pipeline.occurrence(action.parameters["Occurrence"])

        if action.operation == "GetPipelineCheckHistory":
            return capability.history.read(
                capability.identifier(pipeline, expectation, occurrence)
            )
        if action.operation != "CheckPipelineExpectations":
            raise ExecutionError("invalid_execution_plan")

        reads = 0

        def read(listing: ApprovedAction) -> dict[str, Any]:
            nonlocal reads

            if reads >= 1:
                raise ExecutionError("check_read_limit")

            reads += 1
            deadline.remaining()

            if listing.service == "observations":
                if capability.observation_store is None:
                    raise ExecutionError("collected_evidence_unavailable")

                parameters = listing.parameters

                try:
                    result = event_presence(
                        capability.observation_store,
                        str(parameters["source"]),
                        int(str(parameters["start"])),
                        int(str(parameters["end"])),
                        str(parameters["correlation_field"]),
                        str(parameters["correlation_value"]),
                        str(parameters["event_field"]),
                        str(parameters["event_value"]),
                        int(str(parameters["settle_seconds"])),
                    )
                except OSError, sqlite3.Error, ValueError:
                    raise ExecutionError(
                        "collected_evidence_unavailable"
                    ) from None

                deadline.remaining()
                return result

            if (
                not session.environment
                or time.monotonic() >= session.credentials_until
            ):
                session.environment = backend.authenticate(deadline)
                session.credentials_until = time.monotonic() + 840

            response, _ = backend.execute(
                listing, session.environment, deadline
            )
            deadline.remaining()
            return response

        return capability.check(
            action, pipeline, expectation, occurrence, read, now
        )
    finally:
        capability.lock.release()


def references(data: dict[str, Any]) -> list[dict[str, str]]:
    if "observation_id" in data:
        identifiers = [data["observation_id"]]
    else:
        identifiers = [
            item["observation_id"] for item in data.get("observations", [])
        ]

    return [
        {
            "reference": "pipeline-health:" + identifier,
            "observation": "pipeline_check",
        }
        for identifier in identifiers
    ]
