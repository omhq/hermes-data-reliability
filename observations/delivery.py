import sqlite3

from .store import StoreError


def read_batch(
    database: sqlite3.Connection,
    source: str,
    after: int,
    start: int,
    limit: int,
    max_bytes: int,
    max_record_bytes: int,
) -> list[sqlite3.Row]:
    """Release the read transaction before a consumer evaluates any records."""
    with database:
        database.execute("BEGIN")
        candidates = database.execute(
            """
            SELECT sequence, payload_bytes FROM events WHERE source_key = ?
                AND sequence > ? AND timestamp_ms >= ?
            ORDER BY sequence LIMIT ?
            """,
            (source, after, start, limit),
        ).fetchall()
        selected = []
        size = 0

        for row in candidates:
            if row["payload_bytes"] > max_record_bytes:
                if selected:
                    break

                raise StoreError("consumer_record_limit_exceeded")
            if size + row["payload_bytes"] > max_bytes:
                break

            selected.append(row["sequence"])
            size += row["payload_bytes"]

        if not selected:
            return []

        placeholders = ", ".join("?" for _ in selected)
        return database.execute(
            f"SELECT * FROM events WHERE sequence IN ({placeholders}) "
            "ORDER BY sequence",
            selected,
        ).fetchall()
