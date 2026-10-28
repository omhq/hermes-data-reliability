"""Exercise pipeline handlers against in-memory objects and notifications."""

import io
import json
from contextlib import redirect_stdout
from typing import Any, Callable, TypeVar
from urllib.parse import quote_plus

from pipeline.logging_config import configure_logging
from pipeline.processor.handler import process_batch
from pipeline.producer.handler import produce

BUCKET = "test-pipeline-bucket"
QUEUE_ARN = "arn:aws:sqs:us-east-1:123456789012:test-input"
ETAG = "a" * 32
T = TypeVar("T")


class PipelineFixture:
    def __init__(self) -> None:
        self.objects = {}
        self.entries = []
        self.reads = []
        self.fail_prefix: str | None = None
        self.fail_read = False

    def capture(self, operation: Callable[..., T], *arguments: Any) -> T:
        stdout = io.StringIO()

        try:
            with redirect_stdout(stdout):
                configure_logging()
                return operation(*arguments)
        finally:
            self.entries.extend(
                json.loads(line) for line in stdout.getvalue().splitlines()
            )

    def put(
        self, key: str, payload: dict[str, Any] | list[dict[str, Any]]
    ) -> None:
        if self.fail_prefix is not None and key.startswith(self.fail_prefix):
            raise OSError("private provider response")

        self.objects[key] = json.loads(json.dumps(payload))

    def put_input(self, key: str, payload: list[dict[str, Any]]) -> bool:
        if key in self.objects:
            return False

        self.put(key, payload)
        return True

    def read(self, key: str, etag: str) -> bytes:
        self.reads.append((key, etag))

        if self.fail_read:
            raise OSError("private provider response")

        return json.dumps(self.objects[key], sort_keys=True).encode()

    def produce(
        self,
        scenario: str = "success",
        occurrence: str = "2026-01-01T00:00:00Z",
    ) -> dict[str, Any]:
        return self.capture(
            produce,
            {"scheduled_at": occurrence, "scenario": scenario},
            "producer-attempt",
            self.put_input,
        )

    def message(
        self, key: str, message_id: str = "message-1", bucket: str = BUCKET
    ) -> dict[str, str]:
        return {
            "eventSource": "aws:sqs",
            "eventSourceARN": QUEUE_ARN,
            "messageId": message_id,
            "body": json.dumps(
                {
                    "Records": [
                        {
                            "eventSource": "aws:s3",
                            "eventName": "ObjectCreated:Put",
                            "s3": {
                                "bucket": {"name": bucket},
                                "object": {
                                    "key": quote_plus(key),
                                    "eTag": ETAG,
                                },
                            },
                        }
                    ]
                }
            ),
        }

    def process(
        self,
        messages: list[dict[str, Any]],
        request_id: str = "processor-attempt",
    ) -> dict[str, Any]:
        return self.capture(
            process_batch,
            {"Records": messages},
            request_id,
            BUCKET,
            QUEUE_ARN,
            self.read,
            self.put,
        )
