import argparse
import fcntl
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from incidents.database import connect

from .access import CollectionAccess
from .audit import CollectionAudit
from .aws import CloudWatchClient
from .cli import configure_logging
from .cloudwatch import CloudWatchReader
from .collection import collect_source
from .definition import investigation_settings, load
from .matching import Matcher

LOGGER = logging.getLogger(__name__)


def poll(
    definition: dict[str, Any],
    db: sqlite3.Connection,
    now: int,
    reader: CloudWatchReader,
) -> dict[str, dict[str, Any]]:
    results = {}

    for source_id, source in definition["sources"].items():
        source = {
            **source,
            "investigation": investigation_settings(definition, source),
        }

        state = collect_source(
            db, source_id, source, reader, now, account_id=reader.account_id
        )

        results[source_id] = {
            key: state[key]
            for key in (
                "status",
                "cursor_ms",
                "next_poll_at",
                "last_error",
                "pages_read",
                "events_read",
                "message_bytes_read",
            )
        }

    return results


def main() -> None:
    configure_logging()

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Trusted host investigator JSON with account and role allowlist",
    )
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument(
        "--findings", type=Path, default=Path("/srv/hermes-lab/findings")
    )
    parser.add_argument("--validate-only", action="store_true")

    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    definition = load(args.observations, config)
    access = CollectionAccess.load(config)

    if args.validate_only:
        for source in definition["sources"].values():
            with Matcher(source["rules"]):
                pass

        LOGGER.info("Observation configuration and patterns are valid.")
        return

    os.umask(0o077)
    args.findings.mkdir(parents=True, exist_ok=True)

    with (args.findings / ".poll.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return

        db = connect(args.findings / "incidents.sqlite3")

        try:
            reader = CloudWatchReader(
                CloudWatchClient(access),
                CollectionAudit(args.findings / "collection-audit.jsonl"),
            )
            sources = poll(definition, db, int(time.time()), reader)

            completed_at = int(time.time())

            for source in sources.values():
                source["lag_seconds"] = max(
                    0, completed_at - source["cursor_ms"] / 1000
                )

            status = {
                "completed_at": completed_at,
                "sources": sources,
            }
            temporary = args.findings / ".last-poll.json.tmp"
            temporary.write_text(json.dumps(status))
            temporary.replace(args.findings / "last-poll.json")

            LOGGER.info("Collector poll completed. %s", json.dumps(status))
        finally:
            db.close()


if __name__ == "__main__":
    main()
