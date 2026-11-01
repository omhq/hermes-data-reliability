import json
from contextlib import closing
from pathlib import Path
from typing import Any

from .store import connect, coverage

MAX_QUERY_EVENTS = 1000
MAX_QUERY_BYTES = 2 * 1024 * 1024


def event_presence(
    path: Path,
    source: str,
    start: int,
    end: int,
    correlation_field: str,
    correlation_value: str,
    event_field: str,
    event_value: str,
    settle_seconds: int = 0,
) -> dict[str, Any]:
    """Evaluate top-level JSON fields within one trusted source and window."""
    with closing(connect(path, readonly=True)) as database:
        with database:
            database.execute("BEGIN")
            availability = coverage(
                database, source, start, end, settle_seconds
            )
            rows = database.execute(
                """
                SELECT event_id, timestamp_ms, payload, payload_bytes
                FROM events WHERE source_key = ? AND timestamp_ms >= ?
                    AND timestamp_ms < ?
                ORDER BY sequence LIMIT ?
                """,
                (source, start, end, MAX_QUERY_EVENTS + 1),
            )
            read_bytes = 0

            for index, row in enumerate(rows):
                read_bytes += row["payload_bytes"]

                if index >= MAX_QUERY_EVENTS or read_bytes > MAX_QUERY_BYTES:
                    return {
                        **availability,
                        "exists": None,
                        "status": "partial",
                        "reason": "query_limit_reached",
                    }

                payload = json.loads(row["payload"])
                message = payload.get("message")

                try:
                    record = (
                        json.loads(message)
                        if type(message) is str
                        else payload
                    )
                except ValueError, RecursionError:
                    continue

                if (
                    type(record) is dict
                    and record.get(correlation_field) == correlation_value
                    and record.get(event_field) == event_value
                ):
                    return {
                        "exists": True,
                        **availability,
                        "event_id": row["event_id"],
                        "event_timestamp_ms": row["timestamp_ms"],
                    }

    return {
        **availability,
        "exists": False if availability["status"] == "complete" else None,
    }
