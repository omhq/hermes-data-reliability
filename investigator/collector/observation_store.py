import json
import re
import sqlite3
from typing import Any

from .definition import SEVERITIES, fingerprint, validate_investigation


def source_key(source_id: str, source: dict[str, Any], account_id: str) -> str:
    # Tuning cadence/overlap preserves progress. Changing rules replays the
    # initial lookback under a new cursor; old progress remains recoverable.
    return fingerprint(
        {
            "id": source_id,
            "account_id": account_id,
            "region": source["region"],
            "log_group": source["log_group"],
            "rules": source["rules"],
        }
    )


def run_evidence(event: dict[str, Any]) -> dict[str, Any] | None:
    """Recognize current lab run identifiers for scoped S3 investigations."""
    try:
        value = json.loads(event["message"])
    except ValueError, RecursionError:
        return None

    if type(value) is not dict:
        return None
    if not (
        type(value.get("run_id")) is str
        and re.fullmatch(r"[a-f0-9]{24}", value["run_id"])
        and type(value.get("attempt_id")) is str
        and re.fullmatch(r"[A-Za-z0-9-]{1,80}", value["attempt_id"])
    ):
        return None

    return value


def record_matches(
    db: sqlite3.Connection,
    source_id: str,
    source: dict[str, Any],
    key: str,
    event: dict[str, Any],
    matches: dict[str, list[list[int]]],
    now: int,
    *,
    account_id: str,
) -> None:
    if not matches:
        return

    validate_investigation(source["investigation"], defaults=True)

    db.execute(
        "INSERT OR IGNORE INTO matched_log_events VALUES (?, ?, ?)",
        (key, event["eventId"], json.dumps(event)),
    )

    revision = fingerprint(source)
    db.execute(
        "INSERT OR IGNORE INTO observation_definitions VALUES (?, ?)",
        (revision, json.dumps(source)),
    )

    run = run_evidence(event)

    if run:
        existing = db.execute(
            "SELECT account_id FROM incidents WHERE id = ?",
            (run["run_id"],),
        ).fetchone()
        if existing is not None and existing[0] != account_id:
            run = None

    identity = fingerprint(
        [
            account_id,
            source_id,
            source["region"],
            source["log_group"],
            event["eventId"],
        ]
    )
    incident_id = run["run_id"] if run else "log-" + identity[:32]
    severity = max(
        (source["rules"][rule]["severity"] for rule in matches),
        key=SEVERITIES.__getitem__,
    )

    observation = {
        "source_id": source_id,
        "account_id": account_id,
        "region": source["region"],
        "log_group": source["log_group"],
        "event": event,
        "matched_rules": list(matches),
    }
    evidence = {**(run or {}), "observation": observation}

    db.execute(
        """
        INSERT OR IGNORE INTO incidents
            (id, evidence, detected_at, kind, source_id, severity,
             definition_revision, account_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            incident_id,
            json.dumps(evidence),
            now,
            "run" if run else "log",
            source_id,
            severity,
            revision,
            account_id,
        ),
    )

    previous = db.execute(
        "SELECT severity FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()[0]

    if SEVERITIES.get(previous, 0) < SEVERITIES[severity]:
        db.execute(
            "UPDATE incidents SET severity = ? WHERE id = ?",
            (severity, incident_id),
        )

    for rule_id, spans in matches.items():
        rule = source["rules"][rule_id]
        db.execute(
            "INSERT OR IGNORE INTO log_matches VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                event["eventId"],
                rule_id,
                fingerprint(rule),
                incident_id,
                rule["severity"],
                json.dumps(spans),
            ),
        )
