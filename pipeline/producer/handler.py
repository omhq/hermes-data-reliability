import os
import uuid
import json
import random
import hashlib
import logging

from datetime import datetime, timezone
from typing import Any, Callable, Protocol, TypedDict

import boto3

from pipeline.logging_config import configure_logging
from pipeline.logging_config import log_event as emit_event

LOGGER = logging.getLogger(__name__)


class LambdaContext(Protocol):
    """Only the request identifier is needed from the Lambda runtime."""

    aws_request_id: str


class ProducerContext(TypedDict):
    """Identifiers carried by producer progress events."""

    run_id: str
    attempt_id: str


def log_event(
    event_name: str,
    message: str,
    *,
    level: int = logging.INFO,
    **details: object,
) -> None:
    emit_event(
        LOGGER,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pipeline": "synthetic-producer",
            "component": "producer",
            "event": event_name,
            "message": message,
            **details,
        },
        level=level,
    )


def produce(
    event: dict[str, Any],
    attempt_id: str,
    put_input: Callable[[str, list[dict[str, Any]]], bool],
    probability: float = 0.2,
) -> dict[str, Any]:
    """Write one deterministic input without processing or overwriting it."""
    occurrence = event["scheduled_at"]

    if not isinstance(occurrence, str) or not occurrence:
        raise ValueError("A scheduled occurrence is required")
    if not 0 <= probability <= 1:
        raise ValueError("Failure probability must be between zero and one")

    scenario = event.get("scenario", "random")

    if scenario not in {"random", "success", "malformed"}:
        raise ValueError("Unsupported test scenario")

    run_id = hashlib.sha256(occurrence.encode()).hexdigest()[:24]
    context: ProducerContext = {"run_id": run_id, "attempt_id": attempt_id}
    key = f"input/{run_id}.json"

    log_event(
        "invocation_received",
        "Accepted scheduled producer invocation.",
        scheduled_at=occurrence,
        **context,
    )

    records: list[dict[str, int | str]] = [
        {"record_id": number, "amount": number * 10} for number in range(1, 6)
    ]
    malformed = scenario == "malformed" or (
        scenario == "random" and random.Random(run_id).random() < probability
    )

    if malformed:
        records[2]["amount"] = "not-a-number"

    log_event(
        "input_generated",
        "Generated input records.",
        record_count=len(records),
        **context,
    )

    log_event(
        "input_write_started",
        "Uploading input file to S3.",
        key=key,
        **context,
    )

    try:
        created = put_input(key, records)
    except Exception as error:
        log_event(
            "input_write_failed",
            "Input upload failed.",
            level=logging.ERROR,
            status="failed",
            error_type=type(error).__name__,
            key=key,
            **context,
        )
        raise RuntimeError("Producer input upload failed") from None

    log_event(
        "input_written" if created else "input_already_exists",
        (
            "Finished uploading input file to S3."
            if created
            else "Input already exists; left the original file unchanged."
        ),
        key=key,
        **context,
    )

    result = {
        **context,
        "input_key": key,
        "record_count": len(records),
        "created": created,
    }

    log_event(
        "producer_finished",
        "Producer finished; downstream processing is asynchronous.",
        status="succeeded",
        **result,
    )

    return result


def handler(event: dict[str, Any], context: LambdaContext | None) -> dict[str, Any]:
    configure_logging()

    client = boto3.client("s3")
    bucket = os.environ["EVIDENCE_BUCKET"]

    def put_input(key: str, records: list[dict[str, Any]]) -> bool:
        try:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=json.dumps(records, sort_keys=True).encode(),
                ContentType="application/json",
                IfNoneMatch="*",
            )
        except Exception as error:
            response = getattr(error, "response", {})

            if response.get("Error", {}).get("Code") == "PreconditionFailed":
                return False

            raise

        return True

    return produce(
        event,
        context.aws_request_id if context else uuid.uuid4().hex,
        put_input,
        float(os.environ.get("FAILURE_PROBABILITY", "0.2")),
    )
