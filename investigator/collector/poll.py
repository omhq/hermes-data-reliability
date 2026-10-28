import argparse
import fcntl
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from investigator.executor.audit import AuditLog
from investigator.executor.aws import AwsBackend
from investigator.logging_config import configure_logging
from investigator.runtime.sessions import investigation_lock

from .cloudwatch import CloudWatchReader
from .collection import collect_source
from .definition import investigation_settings, load
from .investigation import investigate
from .ledger import budget_status, claim, connect, finish
from .matching import Matcher
from .observation_store import matches_for
from .reports import export

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


def investigate_next(
    db: sqlite3.Connection,
    config: dict[str, Any],
    retry_incident: str | None = None,
) -> None:
    row = claim(db, int(time.time()), incident_id=retry_incident)

    if row is None:
        return

    try:
        saved = db.execute(
            """
            SELECT definition FROM observation_definitions
            WHERE revision = ?
            """,
            (row["definition_revision"],),
        ).fetchone()

        if saved is None:
            raise RuntimeError("Incident definition unavailable")

        row["definition"] = json.loads(saved[0])
        row["matches"] = matches_for(db, row["id"])
        finding = investigate(row, config)
    except Exception as error:
        safe_error = (
            str(error) if type(error) is RuntimeError else type(error).__name__
        )
        finish(db, row["id"], error=safe_error)
        LOGGER.warning(
            "Investigation deferred; retry state saved. error_type=%s",
            type(error).__name__,
        )
    else:
        finish(db, row["id"], finding=finding)
        LOGGER.info("Investigation completed and validated.")


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
    parser.add_argument("--detect-only", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--retry-incident")

    args = parser.parse_args()

    if args.retry_incident and not re.fullmatch(
        r"(?:[a-f0-9]{24}|log-[a-f0-9]{32})", args.retry_incident
    ):
        parser.error("retry incident must be a saved incident ID")

    config = json.loads(args.config.read_text())
    definition = load(args.observations, config)
    backend = AwsBackend(config)

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
                backend, AuditLog(args.findings / "collection-audit.jsonl")
            )
            sources = poll(definition, db, int(time.time()), reader)

            if args.retry_incident:
                with db:
                    db.execute(
                        """
                        UPDATE incidents SET next_attempt = 0
                        WHERE id = ? AND status = 'pending'
                        """,
                        (args.retry_incident,),
                    )

            with investigation_lock(
                args.findings / ".investigation.lock"
            ) as acquired:
                if not args.detect_only and acquired:
                    investigate_next(db, config, args.retry_incident)

            export(db, args.findings)

            completed_at = int(time.time())

            for source in sources.values():
                source["lag_seconds"] = max(
                    0, completed_at - source["cursor_ms"] / 1000
                )

            status = {
                "completed_at": completed_at,
                "sources": sources,
                "investigation_budget": budget_status(db, int(time.time())),
            }
            temporary = args.findings / ".last-poll.json.tmp"
            temporary.write_text(json.dumps(status))
            temporary.replace(args.findings / "last-poll.json")

            LOGGER.info("Collector poll completed. %s", json.dumps(status))
        finally:
            db.close()


if __name__ == "__main__":
    main()
