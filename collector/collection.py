import sqlite3
from typing import Any

from observations import store
from observations.models import Feed, Reader

from .execution import CollectionError

MAX_PAGES = 5
MAX_WINDOW_MS = 2 * 3600 * 1000


def collect_source(
    database: sqlite3.Connection, feed: Feed, reader: Reader, now: int
) -> dict[str, Any]:
    source = feed.source
    key = source.key
    statistics = {"pages_read": 0, "events_read": 0, "events_added": 0}
    initial = max(0, now * 1000 - feed.lookback * 1000)

    with database:
        store.register(database, source, initial)

    state = database.execute(
        "SELECT * FROM sources WHERE source_key = ?", (key,)
    ).fetchone()
    initial = max(initial, state["retained_since_ms"])
    earliest = database.execute(
        "SELECT MIN(start_ms) FROM coverage WHERE source_key = ?", (key,)
    ).fetchone()[0]
    earliest = state["cursor_ms"] if earliest is None else earliest

    if initial < earliest:
        with database:
            database.execute(
                """
                UPDATE sources SET cursor_ms = ?, window_start_ms = NULL,
                    window_end_ms = NULL, continuation = NULL,
                    next_poll_at = 0, status = 'pending'
                WHERE source_key = ?
                """,
                (initial, key),
            )

        state = database.execute(
            "SELECT * FROM sources WHERE source_key = ?", (key,)
        ).fetchone()

    if state["status"] == "ready":
        due = state["cursor_ms"] // 1000 + feed.interval

        if due != state["next_poll_at"]:
            with database:
                database.execute(
                    "UPDATE sources SET next_poll_at = ? WHERE source_key = ?",
                    (due, key),
                )

            state = database.execute(
                "SELECT * FROM sources WHERE source_key = ?", (key,)
            ).fetchone()

    if state["next_poll_at"] > now:
        return {**dict(state), **statistics}

    try:
        for _ in range(MAX_PAGES):
            state = database.execute(
                "SELECT * FROM sources WHERE source_key = ?", (key,)
            ).fetchone()

            if state["window_end_ms"] is None:
                start = max(0, state["cursor_ms"] - feed.overlap * 1000)

                if state["status"] == "pending":
                    start = state["cursor_ms"]

                end = min(
                    now * 1000,
                    max(start + MAX_WINDOW_MS, state["cursor_ms"] + 1),
                )

                with database:
                    database.execute(
                        """
                        UPDATE sources SET window_start_ms = ?,
                            window_end_ms = ? WHERE source_key = ?
                        """,
                        (start, end, key),
                    )

                token = None
            else:
                start = state["window_start_ms"]
                end = state["window_end_ms"]
                token = state["continuation"]

            if end <= start:
                break

            page = reader.read(source, start, end, token)

            if page.continuation is not None and page.continuation == token:
                raise CollectionError("pagination_not_advancing")

            with database:
                added = store.append(database, source, page.events, now)

                if page.continuation is not None:
                    database.execute(
                        """
                        UPDATE sources SET continuation = ?,
                            status = 'partial',
                            last_error = NULL, next_poll_at = ?
                        WHERE source_key = ?
                        """,
                        (page.continuation, now + 15, key),
                    )
                else:
                    store.complete_window(database, key, start, end, now)
                    caught_up = end >= now * 1000
                    database.execute(
                        """
                        UPDATE sources SET cursor_ms = ?,
                            window_start_ms = NULL, window_end_ms = NULL,
                            continuation = NULL, status = ?, next_poll_at = ?,
                            last_error = NULL WHERE source_key = ?
                        """,
                        (
                            end,
                            "ready" if caught_up else "catching_up",
                            now + (feed.interval if caught_up else 15),
                            key,
                        ),
                    )

            statistics["pages_read"] += 1
            statistics["events_read"] += len(page.events)
            statistics["events_added"] += added

            if page.continuation is None and end >= now * 1000:
                break
    except Exception as error:
        safe_error = (
            error.code
            if isinstance(error, CollectionError)
            else str(error)
            if isinstance(error, store.StoreError)
            else type(error).__name__
        )

        with database:
            database.execute(
                """
                UPDATE sources SET status = 'failed', last_error = ?,
                    next_poll_at = ?, continuation = CASE
                        WHEN ? THEN continuation ELSE NULL END
                WHERE source_key = ?
                """,
                (
                    safe_error,
                    now + 15,
                    isinstance(error, CollectionError) and error.resume,
                    key,
                ),
            )

    state = database.execute(
        "SELECT * FROM sources WHERE source_key = ?", (key,)
    ).fetchone()
    return {**dict(state), **statistics}
