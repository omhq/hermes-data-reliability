import json
import sqlite3
from typing import Any


def matches_for(
    db: sqlite3.Connection, incident_id: str
) -> list[dict[str, Any]]:
    return [
        {**dict(row), "spans": json.loads(row["spans"])}
        for row in db.execute(
            "SELECT * FROM log_matches WHERE incident_id = ? ORDER BY rule_id",
            (incident_id,),
        )
    ]
