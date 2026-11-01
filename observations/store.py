import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import Event, Source

RETENTION_MS = 7 * 86400 * 1000
MAX_EVENTS = 200000
MAX_PAYLOAD_BYTES = 256 * 1024 * 1024
SCHEMA_VERSION = 1


class StoreError(ValueError):
    pass


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        database = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5
        )
    else:
        database = sqlite3.connect(path, timeout=5)

    database.row_factory = sqlite3.Row

    try:
        validate_schema(database)
        database.execute("PRAGMA foreign_keys = ON")
    except BaseException:
        database.close()
        raise

    if readonly:
        database.execute("PRAGMA query_only = ON")
        return database

    database.execute("PRAGMA synchronous = EXTRA")
    database.executescript("""
        CREATE TABLE IF NOT EXISTS sources (
            source_key TEXT PRIMARY KEY,
            definition TEXT NOT NULL,
            cursor_ms INTEGER NOT NULL,
            window_start_ms INTEGER,
            window_end_ms INTEGER,
            continuation TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            next_poll_at INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            retained_since_ms INTEGER NOT NULL DEFAULT 0,
            pruned_sequence INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT NOT NULL REFERENCES sources(source_key),
            event_id TEXT NOT NULL,
            timestamp_ms INTEGER NOT NULL,
            collected_at INTEGER NOT NULL,
            payload TEXT NOT NULL,
            payload_bytes INTEGER NOT NULL,
            UNIQUE (source_key, event_id)
        );
        CREATE INDEX IF NOT EXISTS events_source_time
            ON events(source_key, timestamp_ms);
        CREATE INDEX IF NOT EXISTS events_source_sequence
            ON events(source_key, sequence);
        CREATE TABLE IF NOT EXISTS coverage (
            source_key TEXT NOT NULL REFERENCES sources(source_key),
            start_ms INTEGER NOT NULL,
            end_ms INTEGER NOT NULL,
            completed_at INTEGER NOT NULL,
            PRIMARY KEY (source_key, start_ms, end_ms)
        );
        CREATE TABLE IF NOT EXISTS consumer_progress (
            consumer_id TEXT NOT NULL,
            revision TEXT NOT NULL,
            source_key TEXT NOT NULL REFERENCES sources(source_key),
            sequence INTEGER NOT NULL DEFAULT 0,
            start_ms INTEGER NOT NULL,
            last_error TEXT,
            coverage_gap INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (consumer_id, revision, source_key)
        );
        PRAGMA user_version = 1;
    """)
    return database


def validate_schema(database: sqlite3.Connection) -> None:
    version = database.execute("PRAGMA user_version").fetchone()[0]
    populated = database.execute(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type = 'table')"
    ).fetchone()[0]

    if version not in (0, SCHEMA_VERSION) or version == 0 and populated:
        raise StoreError("unsupported_observation_database")


def register(database: sqlite3.Connection, source: Source, start: int) -> None:
    definition = json.dumps(
        {
            "connector": source.connector,
            "account": source.account,
            "region": source.region,
            "location": source.location,
            "options": source.options,
        },
        sort_keys=True,
    )
    database.execute(
        """
        INSERT OR IGNORE INTO sources(source_key, definition, cursor_ms)
        VALUES (?, ?, ?)
        """,
        (source.key, definition, start),
    )
    previous = database.execute(
        "SELECT definition FROM sources WHERE source_key = ?", (source.key,)
    ).fetchone()

    if previous[0] != definition:
        raise StoreError("source_identity_conflict")


def append(
    database: sqlite3.Connection,
    source: Source,
    events: tuple[Event, ...],
    now: int,
) -> int:
    count, size = database.execute(
        "SELECT COUNT(*), COALESCE(SUM(payload_bytes), 0) FROM events"
    ).fetchone()
    added = 0

    for event in events:
        payload = json.dumps(
            event.payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        existing = database.execute(
            """
            SELECT timestamp_ms, payload FROM events
            WHERE source_key = ? AND event_id = ?
            """,
            (source.key, event.identifier),
        ).fetchone()

        if existing is not None:
            if existing[0] != event.timestamp or existing[1] != payload:
                raise StoreError("event_identity_conflict")

            continue

        payload_bytes = len(payload.encode())

        if (
            count + added + 1 > MAX_EVENTS
            or size + payload_bytes > MAX_PAYLOAD_BYTES
        ):
            raise StoreError("observation_capacity_exhausted")

        database.execute(
            """
            INSERT INTO events
                (source_key, event_id, timestamp_ms, collected_at,
                 payload, payload_bytes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                source.key,
                event.identifier,
                event.timestamp,
                now,
                payload,
                payload_bytes,
            ),
        )
        size += payload_bytes
        added += 1

    return added


def prune(database: sqlite3.Connection, now: int) -> None:
    cutoff = max(0, now * 1000 - RETENTION_MS)
    database.execute(
        """
        UPDATE sources SET retained_since_ms = MAX(retained_since_ms, ?),
            pruned_sequence = MAX(pruned_sequence, COALESCE((
                SELECT MAX(sequence) FROM events
                WHERE events.source_key = sources.source_key
                    AND timestamp_ms < ?
            ), 0))
        """,
        (cutoff, cutoff),
    )
    database.execute("DELETE FROM events WHERE timestamp_ms < ?", (cutoff,))
    database.execute("DELETE FROM coverage WHERE end_ms <= ?", (cutoff,))


def complete_window(
    database: sqlite3.Connection, source: str, start: int, end: int, now: int
) -> None:
    database.execute(
        """
        INSERT INTO coverage VALUES (?, ?, ?, ?)
        ON CONFLICT(source_key, start_ms, end_ms)
        DO UPDATE SET completed_at = excluded.completed_at
        """,
        (source, start, end, now),
    )


def coverage(
    database: sqlite3.Connection,
    source: str,
    start: int,
    end: int,
    settle_seconds: int = 0,
) -> dict[str, Any]:
    state = database.execute(
        "SELECT * FROM sources WHERE source_key = ?", (source,)
    ).fetchone()

    if state is None:
        return {"status": "unavailable", "reason": "source_not_collected"}

    ranges = database.execute(
        """
        SELECT start_ms, end_ms, completed_at FROM coverage
        WHERE source_key = ? AND start_ms <= ? AND end_ms >= ?
        ORDER BY start_ms, end_ms
        """,
        (source, end, start),
    ).fetchall()
    through = start

    for row in ranges:
        safe_end = min(row[1], row[2] * 1000 - settle_seconds * 1000)

        if row[0] <= through:
            through = max(through, safe_end)

    complete = through >= end and start >= state["retained_since_ms"]
    return {
        "status": "complete" if complete else "partial",
        "reason": "covered_window" if complete else "collection_gap",
        "collected_through_ms": state["cursor_ms"],
        "source_status": state["status"],
        "last_error": state["last_error"],
    }
