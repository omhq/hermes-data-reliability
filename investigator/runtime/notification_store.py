import sqlite3
from pathlib import Path
from typing import TypedDict

SCHEMA_VERSION = 1
MAX_ATTEMPTS = 3
RETRY_SECONDS = 300
MIN_SEND_INTERVAL = 60
DATABASE_NAME = "notifications.sqlite3"
LOCK_NAME = ".notifications.lock"
PRESERVED_FILES = {DATABASE_NAME, LOCK_NAME}


class Message(TypedDict):
    incident_id: str
    subject: str
    message: str


def validate_schema(db: sqlite3.Connection) -> None:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    tables = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1"
    ).fetchone()

    if version not in (0, SCHEMA_VERSION) or (version == 0 and tables):
        raise ValueError("Unsupported notification database; state unchanged")


def connect(path: Path) -> sqlite3.Connection:
    """Open delivery state without adopting an unsupported database."""
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row

    try:
        validate_schema(db)
    except Exception:
        db.close()
        raise

    db.executescript(f"""
        BEGIN;
        CREATE TABLE IF NOT EXISTS destinations (
            topic_arn TEXT PRIMARY KEY,
            initialized_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS deliveries (
            topic_arn TEXT NOT NULL,
            incident_id TEXT NOT NULL,
            subject TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt INTEGER NOT NULL DEFAULT 0,
            last_attempt INTEGER,
            message_id TEXT,
            last_error TEXT,
            PRIMARY KEY (topic_arn, incident_id)
        );
        PRAGMA user_version = {SCHEMA_VERSION};
        COMMIT;
    """)

    return db


def enqueue(
    db: sqlite3.Connection,
    topic_arn: str,
    messages: list[Message],
    now: int,
) -> None:
    """Baseline old reports once and retain new payloads until delivery."""
    with db:
        initialized = db.execute(
            "SELECT 1 FROM destinations WHERE topic_arn = ?", (topic_arn,)
        ).fetchone()
        status = "pending" if initialized else "historical"

        db.executemany(
            """
            INSERT OR IGNORE INTO deliveries
                (topic_arn, incident_id, subject, message, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    topic_arn,
                    item["incident_id"],
                    item["subject"],
                    item["message"],
                    status,
                )
                for item in messages
            ],
        )
        db.execute(
            "INSERT OR IGNORE INTO destinations VALUES (?, ?)",
            (topic_arn, now),
        )


def claim(
    db: sqlite3.Connection, topic_arn: str, now: int
) -> sqlite3.Row | None:
    """Spend attempts before publishing and retain them after crashes."""
    with db:
        last = db.execute(
            "SELECT MAX(last_attempt) FROM deliveries"
        ).fetchone()[0]

        if last is not None and now < last + MIN_SEND_INTERVAL:
            return None

        row = db.execute(
            """
            SELECT * FROM deliveries
            WHERE topic_arn = ? AND status = 'pending'
                AND attempts < ? AND next_attempt <= ?
            ORDER BY rowid LIMIT 1
            """,
            (topic_arn, MAX_ATTEMPTS, now),
        ).fetchone()

        if row is None:
            return None

        db.execute(
            """
            UPDATE deliveries
            SET attempts = attempts + 1, last_attempt = ?, next_attempt = ?,
                status = CASE WHEN attempts + 1 >= ? THEN 'exhausted'
                              ELSE 'pending' END
            WHERE topic_arn = ? AND incident_id = ?
            """,
            (
                now,
                now + RETRY_SECONDS,
                MAX_ATTEMPTS,
                topic_arn,
                row["incident_id"],
            ),
        )

        return row


def finish(
    db: sqlite3.Connection,
    topic_arn: str,
    incident_id: str,
    message_id: str | None = None,
    error: str | None = None,
) -> None:
    with db:
        db.execute(
            """
            UPDATE deliveries
            SET status = CASE WHEN ? IS NOT NULL THEN 'sent' ELSE status END,
                message_id = ?, last_error = ?
            WHERE topic_arn = ? AND incident_id = ?
            """,
            (message_id, message_id, error, topic_arn, incident_id),
        )
