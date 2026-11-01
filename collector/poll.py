import argparse
import fcntl
import json
import logging
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from observations import store
from observations.files import install_bytes
from observations.models import Reader

from .audit import CollectionAudit
from .cli import configure_logging
from .collection import collect_source
from .definition import feeds
from .reader import ConnectorReader
from .registered import CONNECTORS

LOGGER = logging.getLogger(__name__)


def poll(
    compiled: dict[str, Any],
    database: sqlite3.Connection,
    now: int,
    reader: Reader,
) -> dict[str, dict[str, Any]]:
    with database:
        store.prune(database, now)

    results = {}

    for feed in feeds(compiled):
        state = collect_source(database, feed, reader, now)
        results[feed.source.key] = {
            key: state[key]
            for key in (
                "status",
                "cursor_ms",
                "next_poll_at",
                "last_error",
                "pages_read",
                "events_read",
                "events_added",
            )
        }
        results[feed.source.key]["aliases"] = list(feed.aliases)

    return results


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--findings", type=Path, default=Path("/srv/hermes-lab/findings")
    )
    args = parser.parse_args()
    connectors = CONNECTORS.configure(json.loads(args.config.read_text()))
    compiled = json.loads(args.plan.read_text())

    for feed in feeds(compiled):
        connectors.authorize(feed.source)

    if args.validate_only:
        LOGGER.info("Collection plan is valid.")
        return

    os.umask(0o027)
    args.store.parent.mkdir(parents=True, exist_ok=True)
    args.findings.mkdir(parents=True, exist_ok=True)

    with (args.findings / ".poll.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return

        with closing(store.connect(args.store)) as database:
            reader = ConnectorReader(
                connectors,
                CollectionAudit(args.findings / "collection-audit.jsonl"),
            )
            sources = poll(compiled, database, int(time.time()), reader)

        completed_at = int(time.time())

        for source in sources.values():
            source["lag_seconds"] = max(
                0, completed_at - source["cursor_ms"] / 1000
            )

        status = {"completed_at": completed_at, "sources": sources}
        install_bytes(
            args.findings / "last-poll.json",
            json.dumps(status).encode(),
            0o600,
        )
        LOGGER.info("Collector poll completed. %s", json.dumps(status))


if __name__ == "__main__":
    main()
