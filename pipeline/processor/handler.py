import hashlib
import json
import logging
import os
import uuid
from contextlib import closing
from typing import TYPE_CHECKING, Callable, Protocol, TypedDict

import boto3

from pipeline.logging_config import configure_logging

from .events import InvalidNotification, input_references
from .logs import log_event
from .processing import MAX_INPUT_BYTES, process_object

if TYPE_CHECKING:
    from .processing import ObjectPayload


class LambdaContext(Protocol):
    """Only the request identifier is needed from the Lambda runtime."""

    aws_request_id: str


class MessageContext(TypedDict):
    """Identifiers carried by SQS message progress events."""

    message_id: str
    request_id: str


def process_batch(
    event: object,
    request_id: str,
    bucket: str,
    queue_arn: str,
    read_object: Callable[[str, str], bytes],
    put_object: Callable[[str, ObjectPayload], None],
) -> dict[str, list[dict[str, str]]]:
    """Return failed message IDs so successful messages are acknowledged."""
    messages = event.get("Records") if isinstance(event, dict) else None

    if not isinstance(messages, list) or not messages:
        raise RuntimeError("An SQS batch is required")

    identifiers: list[str] = []

    for message in messages:
        identifier = (
            message.get("messageId") if isinstance(message, dict) else None
        )

        if not isinstance(identifier, str) or not identifier:
            raise RuntimeError("SQS message identifiers are required")

        identifiers.append(identifier)

    if len(set(identifiers)) != len(identifiers):
        raise RuntimeError("SQS message identifiers must be unique")

    failures = []

    for message, message_id in zip(messages, identifiers):
        context: MessageContext = {
            "message_id": message_id,
            "request_id": request_id,
        }

        try:
            if (
                message.get("eventSource") != "aws:sqs"
                or message.get("eventSourceARN") != queue_arn
            ):
                raise InvalidNotification("Unapproved SQS source")

            log_event("message_received", "Accepted SQS message.", **context)

            references = input_references(message.get("body"), bucket)

            if not references:
                log_event(
                    "s3_test_event",
                    "Acknowledged S3 notification configuration test.",
                    **context,
                )

            failed = False

            for index, reference in enumerate(references):
                attempt_id = hashlib.sha256(
                    f"{request_id}:{message_id}:{index}".encode()
                ).hexdigest()[:32]
                evidence = process_object(
                    reference,
                    attempt_id,
                    message_id,
                    read_object,
                    put_object,
                )
                failed = failed or evidence["status"] != "succeeded"

            if failed:
                failures.append({"itemIdentifier": message_id})

            log_event(
                "message_finished",
                (
                    "SQS message failed; returning it for retry."
                    if failed
                    else "Finished processing SQS message."
                ),
                outcome="failed" if failed else "succeeded",
                level=logging.WARNING if failed else logging.INFO,
                **context,
            )
        except Exception as error:
            failures.append({"itemIdentifier": message_id})
            log_event(
                "message_failed",
                "Could not process SQS message; returning it for retry.",
                level=logging.ERROR,
                error_type=type(error).__name__,
                **context,
            )

    return {"batchItemFailures": failures}


def handler(
    event: object, context: LambdaContext | None
) -> dict[str, list[dict[str, str]]]:
    configure_logging()

    client = boto3.client("s3")
    bucket = os.environ["EVIDENCE_BUCKET"]

    def read_object(key: str, etag: str) -> bytes:
        response = client.get_object(
            Bucket=bucket, Key=key, IfMatch=f'"{etag}"'
        )

        with closing(response["Body"]) as body:
            return body.read(MAX_INPUT_BYTES + 1)

    def put_object(key: str, payload: ObjectPayload) -> None:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(payload, sort_keys=True, allow_nan=False).encode(),
            ContentType="application/json",
        )

    return process_batch(
        event,
        context.aws_request_id if context else uuid.uuid4().hex,
        bucket,
        os.environ["PROCESSOR_QUEUE_ARN"],
        read_object,
        put_object,
    )
