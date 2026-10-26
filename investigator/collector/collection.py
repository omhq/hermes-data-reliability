import sqlite3

from typing import TYPE_CHECKING, Any, Callable

from investigator.executor.wire import ExecutionError

from .cloudwatch import MAX_PAGES, MAX_WINDOW_MS
from .definition import duration
from .matching import Matcher, MatchError
from .observation_store import record_matches, source_key

if TYPE_CHECKING:
    from investigator.collector.cloudwatch import CloudWatchReader


def collect_source(
    db: sqlite3.Connection,
    source_id: str,
    source: dict[str, Any],
    reader: CloudWatchReader,
    now: int,
    *,
    account_id: str,
    matcher_factory: Callable[[dict[str, dict[str, Any]]], Matcher] = Matcher,
) -> dict[str, Any]:
    """Advance only past pages whose evaluation and matches were committed."""
    statistics = {"pages_read": 0, "events_read": 0, "message_bytes_read": 0}
    key = source_key(source_id, source, account_id)
    settings = source["collection"]
    overlap = duration(settings["overlap"], 0, 3600) * 1000
    interval = duration(settings["poll_interval"], 15, 3600)
    initial = duration(settings["initial_lookback"], 1, 86400) * 1000

    with db:
        db.execute(
            """
            INSERT OR IGNORE INTO source_progress
                (source_key, source_id, cursor_ms)
            VALUES (?, ?, ?)
            """,
            (key, source_id, max(0, now * 1000 - initial)),
        )

    state = db.execute(
        "SELECT * FROM source_progress WHERE source_key = ?", (key,)
    ).fetchone()
    boundary = db.execute(
        "SELECT start_ms FROM collection_starts WHERE source_key = ?", (key,)
    ).fetchone()
    earliest_ms = boundary[0] if boundary is not None else 0
    due = (
        state["cursor_ms"] // 1000 + interval
        if state["status"] == "ready"
        else state["next_poll_at"]
    )

    if due > now:
        if state["next_poll_at"] != due:
            with db:
                db.execute(
                    """
                    UPDATE source_progress SET next_poll_at = ?
                    WHERE source_key = ?
                    """,
                    (due, key),
                )

            state = dict(state)
            state["next_poll_at"] = due

        return {**dict(state), **statistics}

    try:
        with matcher_factory(source["rules"]) as matcher:
            for _ in range(MAX_PAGES):
                state = db.execute(
                    "SELECT * FROM source_progress WHERE source_key = ?",
                    (key,),
                ).fetchone()

                if state["window_end_ms"] is None:
                    start = max(earliest_ms, state["cursor_ms"] - overlap)

                    if state["status"] == "pending":
                        start = state["cursor_ms"]

                    end = min(now * 1000, start + MAX_WINDOW_MS)

                    if end <= state["cursor_ms"]:
                        end = min(
                            now * 1000, state["cursor_ms"] + MAX_WINDOW_MS
                        )

                    with db:
                        db.execute(
                            """
                            UPDATE source_progress
                            SET window_start_ms = ?, window_end_ms = ?
                            WHERE source_key = ?
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

                events, continuation = reader.read(source, start, end, token)
                statistics["pages_read"] += 1
                statistics["events_read"] += len(events)
                statistics["message_bytes_read"] += sum(
                    len(event["message"].encode("utf-8")) for event in events
                )

                if continuation is not None and continuation == token:
                    raise ExecutionError("pagination_not_advancing")

                matches = matcher.match([event["message"] for event in events])

                with db:
                    for event, matched in zip(events, matches):
                        record_matches(
                            db,
                            source_id,
                            source,
                            key,
                            event,
                            matched,
                            now,
                            account_id=account_id,
                        )

                    if continuation is not None:
                        db.execute(
                            """
                            UPDATE source_progress
                            SET continuation = ?, status = 'partial',
                                last_error = NULL, next_poll_at = ?
                            WHERE source_key = ?
                            """,
                            (continuation, now + 15, key),
                        )
                    else:
                        caught_up = end >= now * 1000

                        db.execute(
                            """
                            UPDATE source_progress
                            SET cursor_ms = ?, window_start_ms = NULL,
                                window_end_ms = NULL, continuation = NULL,
                                status = ?, next_poll_at = ?, last_error = NULL
                            WHERE source_key = ?
                            """,
                            (
                                end,
                                "ready" if caught_up else "catching_up",
                                now + (interval if caught_up else 15),
                                key,
                            ),
                        )

                if continuation is None and end >= now * 1000:
                    break
    except Exception as error:
        # Retry the same fixed range from its beginning, safely deduplicating
        # pages already committed. This also recovers expired provider tokens.
        safe_error = (
            error.code
            if isinstance(error, ExecutionError)
            else str(error)
            if isinstance(error, MatchError)
            else type(error).__name__
        )

        with db:
            db.execute(
                """
                UPDATE source_progress
                SET status = 'failed', last_error = ?, next_poll_at = ?,
                    continuation = NULL
                WHERE source_key = ?
                """,
                (safe_error, now + 15, key),
            )

    state = db.execute(
        "SELECT * FROM source_progress WHERE source_key = ?", (key,)
    ).fetchone()

    return {**dict(state), **statistics}
