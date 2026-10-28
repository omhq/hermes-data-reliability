import json
import sqlite3
import time
from pathlib import Path
from typing import Any

ATTEMPTS_PER_HOUR = 3
BUDGET_WINDOW_SECONDS = 3600
SCHEMA_VERSION = 1


class UnsupportedLedger(ValueError):
    """The installed ledger cannot be used by this collector release."""


def validate_schema(db: sqlite3.Connection) -> None:
    """Check format without modifying the database or adopting old records."""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    has_tables = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1"
    ).fetchone()

    if version not in (0, SCHEMA_VERSION) or (version == 0 and has_tables):
        raise UnsupportedLedger(
            "Unsupported collector ledger format; "
            "existing data was not changed"
        )


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the current schema; reject unsupported ledgers without changes."""
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row

    try:
        validate_schema(db)
    except Exception:
        db.close()
        raise

    db.executescript(f"""
        BEGIN;
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY,
            evidence TEXT NOT NULL,
            detected_at INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('run', 'log')),
            source_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            definition_revision TEXT NOT NULL,
            account_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt INTEGER NOT NULL DEFAULT 0,
            finding TEXT,
            last_error TEXT
        );
        CREATE TABLE IF NOT EXISTS attempts (
            incident_id TEXT,
            started_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS source_progress (
            source_key TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            cursor_ms INTEGER NOT NULL,
            window_start_ms INTEGER,
            window_end_ms INTEGER,
            continuation TEXT,
            next_poll_at INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            last_error TEXT
        );
        CREATE TABLE IF NOT EXISTS observation_definitions (
            revision TEXT PRIMARY KEY,
            definition TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS collection_starts (
            source_key TEXT PRIMARY KEY,
            start_ms INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS log_matches (
            source_key TEXT NOT NULL,
            event_id TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            rule_revision TEXT NOT NULL,
            incident_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            spans TEXT NOT NULL,
            PRIMARY KEY(source_key, event_id, rule_id, rule_revision)
        );
        CREATE TABLE IF NOT EXISTS matched_log_events (
            source_key TEXT NOT NULL,
            event_id TEXT NOT NULL,
            event TEXT NOT NULL,
            PRIMARY KEY(source_key, event_id)
        );
        CREATE INDEX IF NOT EXISTS log_matches_incident
        ON log_matches(incident_id);
        CREATE INDEX IF NOT EXISTS attempts_incident
        ON attempts(incident_id, started_at);
        PRAGMA user_version = {SCHEMA_VERSION};
        COMMIT;
    """)

    return db


def budget_status(
    db: sqlite3.Connection, now: int, hourly_limit: int = ATTEMPTS_PER_HOUR
) -> dict[str, int | str]:
    """Read rolling usage without changing attempt history."""
    if type(hourly_limit) is not int or hourly_limit < 1:
        raise ValueError("Hourly limit must be a positive integer")

    starts = [
        row[0]
        for row in db.execute(
            """
            SELECT started_at FROM attempts
            WHERE started_at>? ORDER BY started_at
            """,
            (now - BUDGET_WINDOW_SECONDS,),
        )
    ]
    next_available = (
        starts[-hourly_limit] + BUDGET_WINDOW_SECONDS
        if len(starts) >= hourly_limit
        else now
    )

    return {
        "limit": hourly_limit,
        "window_seconds": BUDGET_WINDOW_SECONDS,
        "used": len(starts),
        "remaining": max(0, hourly_limit - len(starts)),
        "next_available_at": next_available,
        "next_available_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(next_available)
        ),
    }


def claim(
    db: sqlite3.Connection,
    now: int,
    hourly_limit: int = ATTEMPTS_PER_HOUR,
    incident_id: str | None = None,
) -> dict[str, Any] | None:
    """Lease one incident within a rolling-hour and per-incident budget."""
    with db:
        db.execute(
            """
            UPDATE incidents
            SET status = 'pending'
            WHERE status = 'running' AND next_attempt <= ?
            """,
            (now,),
        )
        db.execute("""
            UPDATE incidents
            SET attempts = MAX(
                    attempts,
                    (SELECT COUNT(*) FROM attempts
                     WHERE incident_id = incidents.id)
                ),
                next_attempt = MAX(
                    next_attempt,
                    COALESCE(
                        (SELECT MAX(started_at) + 600 FROM attempts
                         WHERE incident_id = incidents.id),
                        0
                    )
                )
            WHERE status = 'pending' AND attempts < (
                SELECT COUNT(*) FROM attempts WHERE incident_id = incidents.id
            )
            """)
        db.execute("""
            UPDATE incidents
            SET status = 'exhausted'
            WHERE status = 'pending' AND attempts >= 3
            """)

        if budget_status(db, now, hourly_limit)["remaining"] == 0:
            return None

        row = db.execute(
            """
            SELECT * FROM incidents
            WHERE status = 'pending' AND attempts < 3 AND next_attempt <= ?
                AND (? IS NULL OR id = ?)
            ORDER BY detected_at, id
            LIMIT 1
            """,
            (now, incident_id, incident_id),
        ).fetchone()

        if row is None:
            return None

        db.execute(
            """
            UPDATE incidents
            SET status = 'running', attempts = attempts + 1, next_attempt = ?
            WHERE id = ?
            """,
            (now + 600, row["id"]),
        )
        db.execute("INSERT INTO attempts VALUES(?,?)", (row["id"], now))

        return dict(row)


def finish(
    db: sqlite3.Connection,
    run_id: str,
    finding: dict[str, Any] | None = None,
    error: str | None = None,
    now: int | None = None,
) -> None:
    now = int(time.time()) if now is None else now

    with db:
        if finding is not None:
            db.execute(
                """
                UPDATE incidents
                SET status = 'complete', finding = ?, last_error = NULL
                WHERE id = ?
                """,
                (json.dumps(finding), run_id),
            )
        else:
            db.execute(
                """
                UPDATE incidents
                SET status = CASE
                        WHEN attempts >= 3 THEN 'exhausted'
                        ELSE 'pending'
                    END,
                    last_error = ?,
                    next_attempt = ?
                WHERE id = ?
                """,
                (error, now + 600, run_id),
            )
