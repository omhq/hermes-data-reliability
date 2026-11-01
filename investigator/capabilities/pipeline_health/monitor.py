import time
from typing import TYPE_CHECKING, Any

from tool_boundary.models import PolicyDenied
from tool_boundary.validation import fields, require
from tool_boundary.wire import MAX_RESPONSE_BYTES, ExecutionError, encode

from .evaluator import LOOKBACK_SECONDS, MAX_CHECKS_PER_TICK

if TYPE_CHECKING:
    from tool_boundary.executor import Executor

    from .evaluator import Capability

POLL_SECONDS = 25


def poll(executor: Executor, health: Capability) -> dict[str, Any]:
    require(
        "hermes-pipeline-health" in executor.packages.enabled, "tool_disabled"
    )
    end = time.monotonic() + POLL_SECONDS
    completed = 0
    errors = 0

    require(health.lock.acquire(timeout=POLL_SECONDS), "capability_busy")

    try:
        due = health.due(executor.clock() // 1000)

        for pipeline, expectation, occurrence in due:
            remaining = int(end - time.monotonic())

            if remaining < 1:
                break

            opened = executor.register(
                {
                    "pipeline_id": pipeline.name,
                    "expectation_id": expectation.name,
                    "occurrence": occurrence.identifier,
                    "revision": pipeline.revision,
                    "source_scope": health.target(
                        pipeline, expectation, occurrence
                    ).scope,
                    "ttl_seconds": remaining,
                },
                owner_uid=1000,
            )
            token = opened["context_id"]

            try:
                result = executor.execute(
                    {
                        "context_id": token,
                        "request": {
                            "tool": "check_pipeline_expectations",
                            "arguments": {
                                "pipeline_id": pipeline.name,
                                "expectation_id": expectation.name,
                                "occurrence": occurrence.identifier,
                            },
                        },
                    }
                )
                completed += int(result["ok"])
                errors += int(not result["ok"])
            finally:
                executor.revoke(token, owner_uid=1000)

        events: list[dict[str, Any]] = []
        response = {
            "events": events,
            "lookback_seconds": LOOKBACK_SECONDS,
            "checks_per_tick": MAX_CHECKS_PER_TICK,
            "checks_completed": completed,
            "check_errors": errors,
            "time_budget_seconds": POLL_SECONDS,
            "configured_pipelines": len(health.pipelines),
        }

        for event in health.pending():
            events.append(event)

            if len(encode({"ok": True, **response})) + 1 > MAX_RESPONSE_BYTES:
                events.pop()

                if not events:
                    raise ExecutionError("response_budget_exceeded")

                break

        return response
    finally:
        health.lock.release()


def acknowledge(health: Capability, request: object) -> dict[str, bool]:
    request = fields(request, {"operation", "incident_id"}, set())
    require(type(request["incident_id"]) is str, "invalid_arguments")

    with health.lock:
        if request["incident_id"] not in {
            event["incident_id"] for event in health.pending()
        }:
            raise PolicyDenied("unknown_delivery")

        health.history.acknowledge(request["incident_id"])

    return {"acknowledged": True}
