import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, NotRequired, TypedDict

from pipeline.logging_config import log_event as emit_event

from .logs import log_event

if TYPE_CHECKING:
    from .events import InputReference


MAX_INPUT_BYTES = 1024 * 1024
LOGGER = logging.getLogger(__name__)


class AttemptContext(TypedDict):
    """Identifiers shared by a file's progress logs and saved evidence."""

    run_id: str
    attempt_id: str
    message_id: str


class AttemptEvidence(AttemptContext):
    """Per-file evidence, including fields added as processing progresses."""

    pipeline: str
    timestamp: str
    input_key: str
    input_etag: str
    output_key: str
    stage: str
    status: str
    record_count: NotRequired[int]
    error_type: NotRequired[str]
    error: NotRequired[str]
    elapsed_ms: NotRequired[int]
    log_key: NotRequired[str]
    s3_log_write_failed: NotRequired[bool]


type ObjectPayload = AttemptEvidence | list[dict[str, Any]]


class InvalidInput(ValueError):
    """Expose only reviewed input-validation messages in evidence."""


def transform(body: bytes) -> list[dict[str, Any]]:
    """Reject invalid input without logging its contents."""
    if not isinstance(body, bytes) or len(body) > MAX_INPUT_BYTES:
        raise InvalidInput("Input file exceeds the supported size")

    try:
        records = json.loads(body)
    except ValueError, UnicodeDecodeError, RecursionError:
        raise InvalidInput("Input file must contain valid JSON") from None

    if not isinstance(records, list) or not records:
        raise InvalidInput("Input must contain a nonempty array of records")

    for index, record in enumerate(records):
        amount = record.get("amount") if isinstance(record, dict) else None

        if type(amount) not in (int, float) or (
            isinstance(amount, float) and not math.isfinite(amount)
        ):
            raise InvalidInput(f"record[{index}].amount must be numeric")
        if isinstance(amount, float) and not math.isfinite(amount * 2):
            raise InvalidInput(
                f"record[{index}].amount cannot be doubled finitely"
            )

    return [
        {**record, "amount_doubled": record["amount"] * 2}
        for record in records
    ]


def process_object(
    reference: InputReference,
    attempt_id: str,
    message_id: str,
    read_object: Callable[[str, str], bytes],
    put_object: Callable[[str, ObjectPayload], None],
) -> AttemptEvidence:
    """Keep existing incident IDs and evidence keys across SQS retries."""
    run_id = reference["run_id"]
    context: AttemptContext = {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "message_id": message_id,
    }
    evidence: AttemptEvidence = {
        "pipeline": "synthetic-loader",
        **context,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input_key": reference["input_key"],
        "input_etag": reference["etag"],
        "output_key": f"output/{run_id}.json",
        "stage": "read_input",
        "status": "failed",
    }
    started = time.monotonic()

    try:
        log_event(
            "input_read_started",
            "Loading input file from S3.",
            key=evidence["input_key"],
            **context,
        )

        body = read_object(evidence["input_key"], reference["etag"])

        log_event(
            "input_loaded",
            "Finished loading input file from S3.",
            **context,
        )

        evidence["stage"] = "validate"

        log_event(
            "validation_started",
            "Validating and transforming input records.",
            **context,
        )

        output = transform(body)

        log_event(
            "validation_completed",
            "Validated and transformed input records.",
            record_count=len(output),
            **context,
        )

        evidence["stage"] = "write_output"

        log_event(
            "output_write_started",
            "Writing processed output file to S3.",
            key=evidence["output_key"],
            **context,
        )
        put_object(evidence["output_key"], output)
        log_event(
            "output_written",
            "Finished writing output file to S3.",
            key=evidence["output_key"],
            **context,
        )
        evidence.update(
            status="succeeded", stage="complete", record_count=len(output)
        )
    except Exception as error:
        evidence["error_type"] = type(error).__name__
        evidence["error"] = (
            str(error)
            if isinstance(error, InvalidInput)
            else "AWS operation failed"
        )

        log_event(
            "step_failed",
            evidence["error"],
            level=logging.ERROR,
            stage=evidence["stage"],
            error_type=evidence["error_type"],
            **context,
        )

    evidence["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    evidence["log_key"] = f"runs/{run_id}/{attempt_id}.json"

    log_event(
        "attempt_log_write_started",
        "Saving processor attempt evidence to S3.",
        key=evidence["log_key"],
        **context,
    )

    try:
        put_object(evidence["log_key"], evidence)
    except Exception:
        evidence["s3_log_write_failed"] = True

        if evidence["status"] == "succeeded":
            evidence.update(
                status="failed",
                stage="write_evidence",
                error="Could not persist run evidence",
                error_type="EvidenceWriteError",
            )

        log_event(
            "attempt_log_write_failed",
            "Could not persist processor attempt evidence.",
            level=logging.ERROR,
            **context,
        )
    else:
        log_event(
            "attempt_log_written",
            "Finished saving processor attempt evidence to S3.",
            **context,
        )

    log_event(
        "run_finished",
        "Finished processing file.",
        outcome=evidence["status"],
        **context,
    )

    emit_event(
        LOGGER,
        evidence,
        level=logging.ERROR
        if evidence["status"] == "failed"
        else logging.INFO,
    )

    return evidence
