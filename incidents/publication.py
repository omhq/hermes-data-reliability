import hashlib
import json
import sqlite3
from typing import Any, Literal

from .specification import validate_investigation


def publish(
    database: sqlite3.Connection,
    *,
    identifier: str,
    evidence: dict[str, Any],
    detected_at: int,
    kind: Literal["run", "log", "health"],
    source: str,
    severity: str,
    definition: dict[str, Any],
    account: str,
) -> str:
    """Publish an idempotent incident and its immutable prompt snapshot."""
    validate_investigation(definition["investigation"], defaults=True)
    serialized = json.dumps(definition, sort_keys=True)
    revision = hashlib.sha256(serialized.encode()).hexdigest()
    database.execute(
        "INSERT OR IGNORE INTO observation_definitions VALUES (?, ?)",
        (revision, serialized),
    )
    database.execute(
        """
        INSERT OR IGNORE INTO incidents
            (id, evidence, detected_at, kind, source_id, severity,
             definition_revision, account_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            identifier,
            json.dumps(evidence),
            detected_at,
            kind,
            source,
            severity,
            revision,
            account,
        ),
    )
    return revision
