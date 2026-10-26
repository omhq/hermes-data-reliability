import re
import json

from typing import TypedDict
from urllib.parse import unquote_plus

MAX_MESSAGE_BYTES = 262144


class InputReference(TypedDict):
    """An approved object identity extracted from an S3 notification."""

    run_id: str
    input_key: str
    etag: str


class InvalidNotification(ValueError):
    """A message cannot identify approved input for this processor."""


def input_references(body: object, bucket: str) -> list[InputReference]:
    """Decode native S3 notifications, including its configuration test."""
    if not isinstance(body, str) or len(body.encode()) > MAX_MESSAGE_BYTES:
        raise InvalidNotification("Invalid notification body")

    try:
        notification = json.loads(body)

        if (
            notification.get("Event") == "s3:TestEvent"
            and notification.get("Service") == "Amazon S3"
            and notification.get("Bucket") == bucket
        ):
            return []

        records = notification["Records"]

        if not isinstance(records, list) or not records:
            raise InvalidNotification("Missing S3 records")

        references = []

        for record in records:
            source = record["s3"]
            key = unquote_plus(source["object"]["key"])
            match = re.fullmatch(r"input/([a-f0-9]{24})\.json", key)
            etag = source["object"]["eTag"]

            if (
                record["eventSource"] != "aws:s3"
                or not record["eventName"].startswith("ObjectCreated:")
                or source["bucket"]["name"] != bucket
                or match is None
                or not isinstance(etag, str)
                or not re.fullmatch(r"[a-fA-F0-9]{32}(?:-[0-9]+)?", etag)
            ):
                raise InvalidNotification("Unapproved S3 input notification")

            references.append({"run_id": match[1], "input_key": key, "etag": etag})

        return references
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        RecursionError,
    ):
        raise InvalidNotification("Invalid S3 input notification") from None
