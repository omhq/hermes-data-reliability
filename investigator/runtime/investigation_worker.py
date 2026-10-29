import argparse
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from investigator.incidents.database import connect
from investigator.incidents.queue import (
    LostClaim,
    budget_status,
    claim,
    finish,
)
from investigator.incidents.records import matches_for
from investigator.incidents.reports import export
from investigator.logging_config import configure_logging

from .investigation import investigate
from .locking import investigation_lock

LOGGER = logging.getLogger(__name__)


def investigate_next(
    db: sqlite3.Connection,
    config: dict[str, Any],
    incident_id: str | None = None,
    *,
    skip_backoff: bool = False,
) -> None:
    row = claim(
        db,
        int(time.time()),
        incident_id=incident_id,
        skip_backoff=skip_backoff,
    )

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
        finish(
            db,
            row["id"],
            claim_token=row["claim_token"],
            error=type(error).__name__,
        )
        LOGGER.warning(
            "Investigation deferred; retry state saved. error_type=%s",
            type(error).__name__,
        )
    else:
        finish(db, row["id"], claim_token=row["claim_token"], finding=finding)
        LOGGER.info("Investigation completed and validated.")


def main() -> None:
    configure_logging()

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--findings", type=Path, default=Path("/srv/hermes-lab/findings")
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--incident", help="Process only this saved incident"
    )
    selection.add_argument(
        "--retry-incident", help="Skip this incident's backoff"
    )
    args = parser.parse_args()
    incident_id = args.incident or args.retry_incident

    if incident_id and not re.fullmatch(
        r"(?:[a-f0-9]{24}|log-[a-f0-9]{32})", incident_id
    ):
        parser.error("incident must be a saved incident ID")

    config = json.loads(args.config.read_text())
    os.umask(0o077)
    args.findings.mkdir(parents=True, exist_ok=True)

    # Shared with chat and maintenance, never with collection. flock releases
    # on process death; the database lease still protects an orphaned worker.
    with investigation_lock(args.findings / ".investigation.lock") as acquired:
        status: dict[str, Any] = {"status": "busy"}

        if acquired:
            with closing(connect(args.findings / "incidents.sqlite3")) as db:
                try:
                    investigate_next(
                        db,
                        config,
                        incident_id,
                        skip_backoff=args.retry_incident is not None,
                    )
                except LostClaim:
                    LOGGER.warning("Expired investigation result discarded.")

                # The database is authoritative. Rebuild exports even when no
                # attempt is due, including after a crash following commit.
                export(db, args.findings)
                status = {
                    "status": "ready",
                    "investigation_budget": budget_status(
                        db, int(time.time())
                    ),
                }

        status["completed_at"] = int(time.time())
        temporary = args.findings / f".last-investigation-{os.getpid()}.tmp"
        temporary.write_text(json.dumps(status))
        temporary.replace(args.findings / "last-investigation.json")


if __name__ == "__main__":
    main()
