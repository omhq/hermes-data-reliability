import argparse
import json
import time
from contextlib import closing
from pathlib import Path

from incidents.database import connect
from investigator.logging_config import configure_logging
from investigator.runtime.locking import investigation_lock
from observations import store
from observations.files import install_bytes

from .consumer import consume
from .definition import validate


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--definition", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--findings", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    definition = validate(json.loads(args.definition.read_text()))
    plan = json.loads(args.plan.read_text())

    if args.validate_only:
        return

    with investigation_lock(args.findings / ".log-rules.lock") as acquired:
        if not acquired:
            return

        if not args.store.exists():
            status = {"status": "awaiting_collection", "events_processed": 0}
        else:
            with (
                closing(store.connect(args.store)) as observations,
                closing(
                    connect(args.findings / "incidents.sqlite3")
                ) as incidents,
            ):
                status = consume(
                    definition, plan, observations, incidents, int(time.time())
                )

        status["completed_at"] = int(time.time())
        install_bytes(
            args.findings / "last-log-rules.json",
            json.dumps(status).encode(),
            0o600,
        )


if __name__ == "__main__":
    main()
