import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .presentation import describe_observation
from .validation import utc_text

RETENTION_SECONDS = 7 * 86400
MAX_OBSERVATIONS = 200000
MAX_DELIVERIES = 100000


class History:
    """Keep check evidence and an acknowledged incident outbox on the host."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self.path, timeout=5)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA synchronous = EXTRA")
        database.executescript("""
            CREATE TABLE IF NOT EXISTS health_observations (
                id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL,
                pipeline TEXT NOT NULL,
                revision TEXT NOT NULL,
                occurrence TEXT NOT NULL,
                observed_at INTEGER NOT NULL,
                result TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS health_observation_delivery
            ON health_observations(
                incident_id, observed_at
            );
            CREATE TABLE IF NOT EXISTS health_deliveries (
                incident_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                pipeline TEXT NOT NULL,
                revision TEXT NOT NULL,
                occurrence TEXT NOT NULL,
                next_check_at INTEGER NOT NULL,
                status TEXT NOT NULL,
                first_failure_at INTEGER,
                recovered_at INTEGER,
                event TEXT,
                acknowledged INTEGER NOT NULL DEFAULT 0
            );
        """)
        return database

    def states(self, scopes: tuple[str, ...]) -> dict[str, tuple[int, str]]:
        if not scopes:
            return {}

        placeholders = ",".join("?" for _ in scopes)

        with closing(self.connect()) as database:
            return {
                row[0]: (row[1], row[2])
                for row in database.execute(
                    "SELECT incident_id, next_check_at, status "
                    f"FROM health_deliveries WHERE source IN ({placeholders})",
                    scopes,
                )
            }

    def save(self, result: dict[str, Any], event: dict[str, Any]) -> None:
        now = result["observed_at"]

        with closing(self.connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            database.execute(
                "DELETE FROM health_observations WHERE observed_at < ?",
                (now - RETENTION_SECONDS,),
            )
            database.execute(
                "DELETE FROM health_deliveries WHERE next_check_at < ? "
                "AND (event IS NULL OR acknowledged = 1)",
                (now - RETENTION_SECONDS,),
            )

            if (
                database.execute(
                    "SELECT COUNT(*) FROM health_observations"
                ).fetchone()[0]
                >= MAX_OBSERVATIONS
            ):
                raise RuntimeError("Pipeline-health history limit reached")

            known = database.execute(
                "SELECT 1 FROM health_deliveries WHERE incident_id = ?",
                (result["incident_id"],),
            ).fetchone()

            if (
                known is None
                and database.execute(
                    "SELECT COUNT(*) FROM health_deliveries"
                ).fetchone()[0]
                >= MAX_DELIVERIES
            ):
                raise RuntimeError("Pipeline-health delivery limit reached")

            database.execute(
                "INSERT INTO health_observations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    result["observation_id"],
                    result["incident_id"],
                    result["pipeline_id"],
                    result["revision"],
                    result["occurrence"],
                    now,
                    json.dumps(result),
                ),
            )
            database.execute(
                """
                INSERT INTO health_deliveries (
                    incident_id, source, pipeline, revision, occurrence,
                    next_check_at, status, first_failure_at, event
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(incident_id) DO UPDATE SET
                    next_check_at = excluded.next_check_at,
                    status = excluded.status,
                    first_failure_at = COALESCE(
                        health_deliveries.first_failure_at,
                        excluded.first_failure_at
                    ),
                    recovered_at = CASE
                        WHEN excluded.status = 'pass'
                             AND health_deliveries.first_failure_at IS NOT NULL
                        THEN COALESCE(health_deliveries.recovered_at, ?)
                        ELSE health_deliveries.recovered_at
                    END,
                    event = COALESCE(health_deliveries.event, excluded.event),
                    acknowledged = CASE
                        WHEN excluded.status IN ('fail', 'unknown') THEN 0
                        ELSE health_deliveries.acknowledged
                    END
                """,
                (
                    result["incident_id"],
                    result["source"],
                    result["pipeline_id"],
                    result["revision"],
                    result["occurrence"],
                    now + 60,
                    result["status"],
                    now if result["status"] == "fail" else None,
                    json.dumps(event)
                    if result["status"] in {"fail", "unknown"}
                    else None,
                    now,
                ),
            )

    def read(self, incident_id: str) -> dict[str, Any]:
        with closing(self.connect()) as database:
            rows = database.execute(
                """
                SELECT result FROM health_observations
                WHERE incident_id = ?
                ORDER BY observed_at DESC, rowid DESC LIMIT 20
                """,
                (incident_id,),
            ).fetchall()
            delivery = database.execute(
                """
                SELECT first_failure_at, recovered_at FROM health_deliveries
                WHERE incident_id = ?
                """,
                (incident_id,),
            ).fetchone()

        return {
            "observations": [
                describe_observation(json.loads(row[0])) for row in rows
            ],
            "first_failure_at": delivery[0] if delivery else None,
            "recovered_at": delivery[1] if delivery else None,
            "first_failure_at_utc": utc_text(delivery[0])
            if delivery and delivery[0] is not None
            else None,
            "recovered_at_utc": utc_text(delivery[1])
            if delivery and delivery[1] is not None
            else None,
            "history_limit": 20,
            "retention_seconds": RETENTION_SECONDS,
            "coverage": "recorded_observations_only" if rows else "unknown",
        }

    def pending(
        self, scopes: tuple[str, ...], revisions: dict[str, str]
    ) -> list[dict[str, Any]]:
        if not scopes or not revisions:
            return []

        placeholders = ",".join("?" for _ in scopes)
        selected = " OR ".join(
            "(pipeline = ? AND revision = ?)" for _ in revisions
        )
        values = (
            *scopes,
            *(value for pair in revisions.items() for value in pair),
        )

        with closing(self.connect()) as database:
            return [
                json.loads(row[0])
                for row in database.execute(
                    "SELECT event FROM health_deliveries "
                    f"WHERE source IN ({placeholders}) AND ({selected}) "
                    "AND event IS NOT NULL "
                    "AND acknowledged = 0 "
                    "ORDER BY next_check_at, incident_id LIMIT 8",
                    values,
                )
            ]

    def acknowledge(self, incident_id: str) -> None:
        with closing(self.connect()) as database, database:
            database.execute(
                "UPDATE health_deliveries SET acknowledged = 1 "
                "WHERE incident_id = ?",
                (incident_id,),
            )
