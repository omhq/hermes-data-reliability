import argparse
import json
import logging
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from incidents.database import connect
from incidents.publication import publish
from investigator.logging_config import configure_logging
from tool_boundary.catalog import ToolPackages
from tool_boundary.wire import call

LOGGER = logging.getLogger(__name__)


def enqueue(
    database: sqlite3.Connection,
    events: list[dict[str, Any]],
    account: str,
    now: int,
) -> None:
    with database:
        database.execute("BEGIN IMMEDIATE")

        for event in events:
            if event["account_id"] != account:
                raise ValueError("Pipeline-health account changed")

            publish(
                database,
                identifier=event["incident_id"],
                evidence=event,
                detected_at=now,
                kind="health",
                source=event["result"]["pipeline_id"],
                severity="error",
                definition={"investigation": event["investigation"]},
                account=account,
            )


def tick(socket_path: Path, findings: Path, account: str) -> dict[str, Any]:
    response = call(socket_path, {"operation": "poll_pipeline_health"})

    if not response.get("ok") or type(response.get("events")) is not list:
        raise RuntimeError("Pipeline-health poll unavailable")

    events = response["events"]
    now = int(time.time())

    with closing(connect(findings / "incidents.sqlite3")) as database:
        enqueue(database, events, account, now)

    for event in events:
        acknowledgement = call(
            socket_path,
            {
                "operation": "acknowledge_pipeline_health",
                "incident_id": event["incident_id"],
            },
        )

        if not acknowledgement.get("ok"):
            raise RuntimeError("Pipeline-health acknowledgement unavailable")

    return {
        "completed_at": now,
        "queued": len(events),
        "configured_pipelines": response["configured_pipelines"],
        "lookback_seconds": response["lookback_seconds"],
        "checks_completed": response["checks_completed"],
        "check_errors": response["check_errors"],
    }


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--findings", type=Path, required=True)
    parser.add_argument(
        "--socket",
        type=Path,
        default=Path("/run/hermes-lab-collector/control.sock"),
    )
    args = parser.parse_args()
    os.umask(0o077)
    args.findings.mkdir(exist_ok=True, parents=True)

    try:
        config = json.loads(args.config.read_text())
        if (
            "hermes-pipeline-health"
            in ToolPackages.from_executor_config(config).enabled
        ):
            status = tick(args.socket, args.findings, config["account_id"])
        else:
            status = {
                "completed_at": int(time.time()),
                "queued": 0,
                "status": "disabled",
            }
    except Exception:
        LOGGER.error(
            "Pipeline-health scheduler failed; no healthy state was inferred."
        )
        raise SystemExit(1) from None

    temporary = args.findings / ".last-pipeline-health.tmp"
    temporary.write_text(json.dumps(status))
    temporary.replace(args.findings / "last-pipeline-health.json")
    LOGGER.info("Pipeline-health check completed; queued=%s", status["queued"])


if __name__ == "__main__":
    main()
