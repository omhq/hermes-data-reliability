import json
import secrets
import sqlite3
import time
from typing import Any

ATTEMPTS_PER_HOUR = 3
ATTEMPTS_PER_INCIDENT = 3
BUDGET_WINDOW_SECONDS = 3600
RETRY_SECONDS = 600
# Longer than the 300-second authorization and bounded model worker lifetime.
LEASE_SECONDS = 600


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


class LostClaim(RuntimeError):
    """An expired or replaced attempt cannot publish a result."""


def claim(
    db: sqlite3.Connection,
    now: int,
    hourly_limit: int = ATTEMPTS_PER_HOUR,
    incident_id: str | None = None,
    *,
    skip_backoff: bool = False,
) -> dict[str, Any] | None:
    """Commit ownership and budget before any external work can begin."""
    if skip_backoff and incident_id is None:
        raise ValueError("Skipping backoff requires a specific incident")

    with db:
        # Serialize recovery, selection and accounting across processes.
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """
            UPDATE incidents
            SET status = 'pending', claim_token = NULL,
                lease_expires_at = NULL, last_error = 'attempt_interrupted'
            WHERE status = 'running' AND lease_expires_at <= ?
            """,
            (now,),
        )
        # Resetting collection retains attempt history. Re-detected incidents
        # inherit that history before they become eligible for another try.
        db.execute(
            """
            UPDATE incidents
            SET attempts = (SELECT COUNT(*) FROM attempts
                            WHERE incident_id = incidents.id),
                next_attempt = MAX(
                    next_attempt,
                    COALESCE((SELECT MAX(started_at) + ? FROM attempts
                              WHERE incident_id = incidents.id), 0)
                )
            WHERE status = 'pending' AND attempts < (
                SELECT COUNT(*) FROM attempts WHERE incident_id = incidents.id
            )
            """,
            (RETRY_SECONDS,),
        )
        db.execute(
            """
            UPDATE incidents SET status = 'exhausted'
            WHERE status = 'pending' AND attempts >= ?
            """,
            (ATTEMPTS_PER_INCIDENT,),
        )

        if db.execute(
            "SELECT 1 FROM incidents WHERE status = 'running' LIMIT 1"
        ).fetchone():
            return None
        if budget_status(db, now, hourly_limit)["remaining"] == 0:
            return None

        row = db.execute(
            """
            SELECT * FROM incidents
            WHERE status = 'pending' AND attempts < ?
                AND (next_attempt <= ? OR ?)
                AND (? IS NULL OR id = ?)
            ORDER BY detected_at, id
            LIMIT 1
            """,
            (
                ATTEMPTS_PER_INCIDENT,
                now,
                skip_backoff,
                incident_id,
                incident_id,
            ),
        ).fetchone()

        if row is None:
            return None

        token = secrets.token_hex(16)
        db.execute(
            """
            UPDATE incidents
            SET status = 'running', attempts = attempts + 1,
                next_attempt = ?, claim_token = ?, lease_expires_at = ?
            WHERE id = ?
            """,
            (now + RETRY_SECONDS, token, now + LEASE_SECONDS, row["id"]),
        )
        db.execute("INSERT INTO attempts VALUES (?, ?)", (row["id"], now))
        claimed = db.execute(
            "SELECT * FROM incidents WHERE id = ?", (row["id"],)
        ).fetchone()

    return dict(claimed)


def finish(
    db: sqlite3.Connection,
    incident_id: str,
    *,
    claim_token: str,
    finding: dict[str, Any] | None = None,
    error: str | None = None,
    now: int | None = None,
) -> None:
    """Publish exactly one result for the currently owned, unexpired claim."""
    now = int(time.time()) if now is None else now
    payload = (
        json.dumps(finding, allow_nan=False) if finding is not None else None
    )

    with db:
        updated = db.execute(
            """
            UPDATE incidents
            SET status = CASE
                    WHEN ? IS NOT NULL THEN 'complete'
                    WHEN attempts >= ? THEN 'exhausted'
                    ELSE 'pending'
                END,
                finding = ?, last_error = ?, next_attempt = ?,
                claim_token = NULL, lease_expires_at = NULL
            WHERE id = ? AND status = 'running' AND claim_token = ?
                AND lease_expires_at > ?
            """,
            (
                payload,
                ATTEMPTS_PER_INCIDENT,
                payload,
                None if finding is not None else error,
                now + RETRY_SECONDS,
                incident_id,
                claim_token,
                now,
            ),
        )

        if updated.rowcount != 1:
            raise LostClaim("Investigation claim expired or was replaced")
