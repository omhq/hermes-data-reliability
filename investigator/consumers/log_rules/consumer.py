import json
import sqlite3
from typing import Any

from observations.configuration import fingerprint
from observations.delivery import read_batch
from observations.subscriptions import Subscription, bindings, manifest

from .matching import MAX_BATCH_BYTES, Matcher
from .publication import record_matches

MAX_EVENTS_PER_TICK = 500
BATCH_EVENTS = 20


def consume(
    definition: dict[str, Any],
    plan: dict[str, Any],
    observations: sqlite3.Connection,
    incidents: sqlite3.Connection,
    now: int,
) -> dict[str, Any]:
    consumer = manifest(definition)

    if not consumer.enabled:
        return {"status": "disabled", "events_processed": 0, "sources": {}}

    source_bindings = bindings(consumer, plan)

    revision = fingerprint(
        {
            "id": consumer.identifier,
            "rules": definition["rules"],
            "investigation": definition["investigation"],
            "sources": source_bindings,
        }
    )
    states = {}
    processed = 0
    subscriptions: dict[str, list[Subscription]] = {}

    for subscription in consumer.subscriptions:
        key = source_bindings[subscription.source]
        subscriptions.setdefault(key, []).append(subscription)

    for source_key, requests in subscriptions.items():
        subscription = requests[0]
        aliases = {item.source for item in requests}
        lookback = max(item.lookback for item in requests)
        rules = {
            identifier: rule
            for identifier, rule in definition["rules"].items()
            if rule["source"] in aliases
        }
        source = plan["feeds"][source_key]["source"]
        state = observations.execute(
            "SELECT * FROM sources WHERE source_key = ?", (source_key,)
        ).fetchone()

        if state is None:
            states[subscription.source] = "awaiting_collection"
            continue

        with observations:
            observations.execute(
                """
                INSERT OR IGNORE INTO consumer_progress
                    (consumer_id, revision, source_key, start_ms)
                VALUES (?, ?, ?, ?)
                """,
                (
                    consumer.identifier,
                    revision,
                    source_key,
                    max(0, now * 1000 - lookback * 1000),
                ),
            )

        progress = observations.execute(
            """
            SELECT * FROM consumer_progress
            WHERE consumer_id = ? AND revision = ? AND source_key = ?
            """,
            (consumer.identifier, revision, source_key),
        ).fetchone()
        gap = bool(progress["coverage_gap"]) or (
            progress["start_ms"] < state["retained_since_ms"]
            and progress["sequence"] <= state["pruned_sequence"]
        )

        if gap:
            with observations:
                observations.execute(
                    """
                    UPDATE consumer_progress SET coverage_gap = 1
                    WHERE consumer_id = ? AND revision = ? AND source_key = ?
                    """,
                    (consumer.identifier, revision, source_key),
                )

        position = progress["sequence"]
        allowance = MAX_EVENTS_PER_TICK // len(subscriptions)
        source_processed = 0

        try:
            with Matcher(rules) as matcher:
                while source_processed < allowance:
                    batch = read_batch(
                        observations,
                        source_key,
                        position,
                        progress["start_ms"],
                        min(BATCH_EVENTS, allowance - source_processed),
                        MAX_BATCH_BYTES,
                        MAX_BATCH_BYTES,
                    )

                    if not batch:
                        break

                    events = [json.loads(row["payload"]) for row in batch]
                    matches = matcher.match(
                        [event["message"] for event in events]
                    )
                    snapshot = {
                        "connector": source["connector"],
                        "region": source["region"],
                        "log_group": source["location"],
                        "source_key": source_key,
                        "consumer_id": consumer.identifier,
                        "investigation": definition["investigation"],
                        "rules": rules,
                    }
                    identity = fingerprint(
                        [consumer.identifier, revision, source_key]
                    )

                    with incidents:
                        for event, matched in zip(events, matches):
                            record_matches(
                                incidents,
                                subscription.source,
                                snapshot,
                                identity,
                                event,
                                matched,
                                now,
                                account_id=source["account"],
                            )

                    # Publication commits first. Retrying a page is idempotent.
                    with observations:
                        observations.execute(
                            """
                            UPDATE consumer_progress SET sequence = ?,
                                last_error = NULL WHERE consumer_id = ?
                                AND revision = ? AND source_key = ?
                            """,
                            (
                                batch[-1]["sequence"],
                                consumer.identifier,
                                revision,
                                source_key,
                            ),
                        )

                    position = batch[-1]["sequence"]
                    source_processed += len(batch)
                    processed += len(batch)

            states[subscription.source] = "retention_gap" if gap else "ready"
        except Exception as error:
            states[subscription.source] = "failed"

            with observations:
                observations.execute(
                    """
                    UPDATE consumer_progress SET last_error = ?
                    WHERE consumer_id = ? AND revision = ? AND source_key = ?
                    """,
                    (
                        type(error).__name__,
                        consumer.identifier,
                        revision,
                        source_key,
                    ),
                )

    return {
        "status": "failed"
        if "failed" in states.values()
        else "degraded"
        if any(state != "ready" for state in states.values())
        else "ready",
        "events_processed": processed,
        "sources": states,
    }
