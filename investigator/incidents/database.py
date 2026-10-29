import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2


class UnsupportedLedger(ValueError):
    """The installed ledger cannot be used by this release."""


def validate_schema(db: sqlite3.Connection) -> None:
    """Check format without modifying the database or adopting old records."""
    version, has_tables = db.execute(
        """
        SELECT user_version,
            EXISTS(SELECT 1 FROM sqlite_master WHERE type = 'table')
        FROM pragma_user_version
        """
    ).fetchone()

    if version not in (0, SCHEMA_VERSION) or (version == 0 and has_tables):
        raise UnsupportedLedger(
            "Unsupported incident database format; "
            "existing data was not changed"
        )


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the current schema; reject unsupported ledgers without changes."""
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row

    try:
        validate_schema(db)
        # EXTRA also syncs the directory after removing a rollback journal.
        # Short transactions keep collection independent of model execution.
        db.execute("PRAGMA journal_mode = DELETE")
        db.execute("PRAGMA synchronous = EXTRA")

        db.executescript(f"""
            BEGIN IMMEDIATE;
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
                last_error TEXT,
                claim_token TEXT,
                lease_expires_at INTEGER,
                CHECK (
                    status IN ('pending', 'running', 'complete', 'exhausted')
                ),
                CHECK (
                    (status = 'running' AND claim_token IS NOT NULL
                     AND lease_expires_at IS NOT NULL)
                    OR (status != 'running' AND claim_token IS NULL
                        AND lease_expires_at IS NULL)
                )
            );
            CREATE TABLE IF NOT EXISTS attempts (
                incident_id TEXT NOT NULL,
                started_at INTEGER NOT NULL
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
            CREATE INDEX IF NOT EXISTS incidents_pending
            ON incidents(status, next_attempt, detected_at);
            CREATE INDEX IF NOT EXISTS attempts_started
            ON attempts(started_at);
            PRAGMA user_version = {SCHEMA_VERSION};
            COMMIT;
        """)
    except BaseException:
        db.close()
        raise

    return db
