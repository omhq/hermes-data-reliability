import hashlib
import threading
import uuid
from pathlib import Path
from typing import Any

from tool_boundary.models import ApprovedAction

from .adapters import ADAPTERS
from .checks import CHECKS
from .definition import Definition, Expectation, Pipeline, bind
from .history import History
from .observations import Observation, ReadEvidence, Target
from .presentation import describe_observation
from .schedules import Occurrence

LOOKBACK_SECONDS = 86400
MAX_CHECKS_PER_TICK = 8


def incident_id(
    scope: str,
    pipeline: Pipeline,
    expectation: Expectation,
    occurrence: Occurrence,
) -> str:
    identity = (
        f"v2:{scope}:{pipeline.name}:{pipeline.revision}:"
        f"{expectation.name}:{occurrence.identifier}"
    )
    return "health-" + hashlib.sha256(identity.encode()).hexdigest()[:32]


class Capability:
    def __init__(
        self,
        definition: Definition,
        history: History,
        config: dict[str, Any],
        observation_store: Path | None = None,
    ) -> None:
        self.consumer = definition.consumer
        self.observation_store = observation_store
        self.pipelines = {
            pipeline.name: pipeline
            for pipeline in definition.pipelines
            if definition.consumer.enabled
        }
        self.sources = (
            definition.sources if definition.consumer.enabled else {}
        )
        self.history = history
        self.account = config["account_id"]
        self.resources = bind(definition, config)
        self.lock = threading.RLock()
        scopes = set()

        for source in self.sources.values():
            target = source.target(self.resources, "scope-probe", self.account)
            scopes.add(target.scope)

        self.scopes = tuple(sorted(scopes))

    def target(
        self,
        pipeline: Pipeline,
        expectation: Expectation,
        occurrence: Occurrence,
    ) -> Target:
        return expectation.source.target(
            self.resources, pipeline.identity.resolve(occurrence), self.account
        )

    def targets(
        self,
        pipeline: Pipeline,
        expectation: Expectation,
        occurrence: Occurrence,
    ) -> tuple[Target, ...]:
        primary = self.target(pipeline, expectation, occurrence)
        supporting = tuple(
            source.target(
                self.resources,
                pipeline.identity.resolve(occurrence),
                self.account,
            )
            for source in pipeline.investigation_sources
            if source != expectation.source
        )
        return (primary, *supporting)

    def identifier(
        self,
        pipeline: Pipeline,
        expectation: Expectation,
        occurrence: Occurrence,
    ) -> str:
        return incident_id(
            self.target(pipeline, expectation, occurrence).scope,
            pipeline,
            expectation,
            occurrence,
        )

    def due(self, now: int) -> list[tuple[Pipeline, Expectation, Occurrence]]:
        if not self.consumer.enabled:
            return []

        states = self.history.states(self.scopes)
        candidates = []

        for pipeline in self.pipelines.values():
            start = now - LOOKBACK_SECONDS - pipeline.grace_seconds
            end = now - pipeline.grace_seconds

            for occurrence in pipeline.schedule.occurrences(start, end):
                for expectation in pipeline.expectations:
                    identifier = self.identifier(
                        pipeline, expectation, occurrence
                    )
                    next_check, status = states.get(identifier, (0, "new"))

                    if status == "pass" or next_check > now:
                        continue

                    candidates.append(
                        (
                            next_check,
                            pipeline.deadline(occurrence),
                            pipeline.name,
                            expectation.name,
                            pipeline,
                            expectation,
                            occurrence,
                        )
                    )

        candidates.sort(key=lambda item: item[:4])
        return [
            (item[4], item[5], item[6])
            for item in candidates[:MAX_CHECKS_PER_TICK]
        ]

    def pending(self) -> list[dict[str, Any]]:
        return self.history.pending(
            self.scopes,
            {item.name: item.revision for item in self.pipelines.values()},
        )

    def check(
        self,
        action: ApprovedAction,
        pipeline: Pipeline,
        expectation: Expectation,
        occurrence: Occurrence,
        read: ReadEvidence,
        now: int,
    ) -> dict[str, Any]:
        target = self.target(pipeline, expectation, occurrence)
        deadline = pipeline.deadline(occurrence)

        if now < deadline:
            observation = Observation(
                {},
                "unavailable",
                "delivery_deadline_not_reached",
                "No evidence read before the deadline.",
            )
            status, reason = "pending", observation.reason
        else:
            observation = ADAPTERS[target.adapter].observe(
                target,
                action,
                read,
                start=occurrence.scheduled_at * 1000,
                end=deadline * 1000,
                now=now * 1000,
            )
            status, reason = CHECKS[expectation.check].evaluate(observation)

        result = {
            "observation_id": uuid.uuid4().hex,
            "incident_id": self.identifier(pipeline, expectation, occurrence),
            "source": target.scope,
            "pipeline_id": pipeline.name,
            "expectation_id": expectation.name,
            "revision": pipeline.revision,
            "occurrence": occurrence.identifier,
            "delivery_id": pipeline.identity.resolve(occurrence),
            "scheduled_at": occurrence.scheduled_at,
            "deadline": deadline,
            "observed_at": now,
            "check": expectation.check,
            "evidence_source": expectation.source.name,
            "target": target.describe(),
            "status": status,
            "reason": reason,
            "observation": {
                "facts": dict(observation.facts),
                "coverage": observation.coverage,
            },
            "limitation": observation.limitation,
        }
        result = describe_observation(result)
        event = {
            "account_id": self.account,
            "incident_id": result["incident_id"],
            "investigation": dict(pipeline.investigation),
            "result": result,
        }
        self.history.save(result, event)
        return result
