import os
import re
import time
import json
import argparse
import logging
import sqlite3

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from investigator.collector.findings import TEXT_FIELDS
from investigator.collector.ledger import validate_schema
from investigator.executor.aws import host_environment
from investigator.executor.process import CliRunner, Deadline, run_json
from investigator.executor.wire import ExecutionError
from investigator.logging_config import configure_logging

from . import notification_store as store
from .sessions import investigation_lock

LOGGER = logging.getLogger(__name__)
ROOT = Path("/srv/hermes-lab")


@dataclass(frozen=True)
class NotificationConfig:
    account_id: str
    region: str
    topic_arn: str
    role_arn: str

    @classmethod
    def load(cls, operations: dict[str, Any]) -> NotificationConfig:
        account = operations.get("account_id")
        region = operations.get("region")
        topic = operations.get("alert_topic_arn")
        role = operations.get("notification_role_arn")

        if (
            type(account) is not str
            or not re.fullmatch(r"[0-9]{12}", account)
            or type(region) is not str
            or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", region)
            or type(topic) is not str
            or not re.fullmatch(
                rf"arn:aws:sns:{region}:{account}:[A-Za-z0-9_-]+", topic
            )
            or type(role) is not str
            or not re.fullmatch(rf"arn:aws:iam::{account}:role/[A-Za-z0-9_-]+", role)
        ):
            raise ValueError("Invalid trusted notification configuration")

        return cls(account, region, topic, role)


def report_message(incident: sqlite3.Row, config: NotificationConfig) -> store.Message:
    """Use saved agent prose and host metadata, excluding evidence bodies."""
    finding = json.loads(incident["finding"])

    if any(
        type(finding.get(key)) is not str or not 1 <= len(finding[key]) <= 8000
        for key in TEXT_FIELDS
    ):
        raise ValueError("Invalid saved finding text")

    summary = " ".join(finding["summary"].split())
    subject = "".join(
        character
        for character in f"Hermes [{incident['severity']}]: {summary}"
        if character.isprintable()
    )[:99]
    detected = datetime.fromtimestamp(incident["detected_at"], UTC).isoformat()
    evidence = finding.get("evidence", [])
    partial = sum(reference.get("truncated") is True for reference in evidence)
    limitations = finding.get("evidence_warnings", [])
    message = (
        f"{finding['summary']}\n\n"
        f"{finding['explanation']}\n\n"
        f"Proposed action\n{finding['proposed_action']}\n\n"
        f"Uncertainty\n{finding['uncertainty']}\n\n"
        "---\n"
        f"Incident: {incident['id']}\n"
        f"Severity: {incident['severity']}\n"
        f"Failed stage: {finding['failed_stage']}\n"
        f"Detected (UTC): {detected}\n"
        f"Source: {incident['source_id']}\n"
        f"Region: {config.region}\n"
        f"Evidence: {len(evidence)} references; {partial} partial; "
        f"{len(limitations)} limitations\n"
        "Report by Hermes; delivery by the trusted host."
    )

    if len(message.encode("utf-8")) > 256 * 1024:
        raise ValueError("Agent report exceeds notification size limit")

    return {
        "incident_id": incident["id"],
        "subject": subject,
        "message": message,
    }


def snapshot(database: Path, config: NotificationConfig) -> list[store.Message]:
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row

        validate_schema(db)

        rows = db.execute(
            """
            SELECT * FROM incidents
            WHERE status = 'complete' AND finding IS NOT NULL
                AND account_id = ?
            ORDER BY detected_at, id
            """,
            (config.account_id,),
        )

        return [report_message(row, config) for row in rows]


class Publisher:
    def __init__(
        self, config: NotificationConfig, runner: CliRunner = run_json
    ) -> None:
        self.config = config
        self.runner = runner

    def call(
        self,
        service: str,
        operation: str,
        arguments: list[str],
        deadline: Deadline,
    ) -> dict[str, Any]:
        return self.runner(
            [
                "/usr/bin/aws",
                service,
                operation,
                "--region",
                self.config.region,
                "--output",
                "json",
                "--no-cli-pager",
                "--cli-connect-timeout",
                "5",
                "--cli-read-timeout",
                "15",
                *arguments,
            ],
            host_environment(self.config.region),
            deadline,
        )

    def publish(self, subject: str, message: str) -> str:
        deadline = Deadline(40, int(time.time() * 1000) + 40000, clock_ms)
        identity = self.call("sts", "get-caller-identity", [], deadline)
        expected = (
            f"arn:aws:sts::{self.config.account_id}:assumed-role/"
            f"{self.config.role_arn.rsplit('/', 1)[-1]}/"
        )

        if identity.get("Account") != self.config.account_id or not identity.get(
            "Arn", ""
        ).startswith(expected):
            raise ExecutionError("unexpected_aws_identity")

        result = self.call(
            "sns",
            "publish",
            [
                "--topic-arn",
                self.config.topic_arn,
                "--subject",
                subject,
                "--message",
                message,
            ],
            deadline,
        )
        message_id = result.get("MessageId")

        if type(message_id) is not str or not message_id:
            raise ExecutionError("invalid_publish_response")

        return message_id


def clock_ms() -> int:
    return int(time.time() * 1000)


def deliver_one(
    db: sqlite3.Connection,
    config: NotificationConfig,
    publisher: Publisher,
    now: int,
) -> None:
    item = store.claim(db, config.topic_arn, now)

    if item is None:
        return

    try:
        message_id = publisher.publish(item["subject"], item["message"])
    except Exception as error:
        # Provider errors and agent text must not enter diagnostics.
        safe_error = (
            error.code if isinstance(error, ExecutionError) else type(error).__name__
        )

        store.finish(db, config.topic_arn, item["incident_id"], error=safe_error)
        LOGGER.warning("Agent report delivery failed. code=%s", safe_error)
    else:
        store.finish(db, config.topic_arn, item["incident_id"], message_id=message_id)
        LOGGER.info("Agent report accepted by SNS. incident_id=%s", item["incident_id"])


def main() -> None:
    configure_logging()

    parser = argparse.ArgumentParser()

    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--initialize-only", action="store_true")
    parser.add_argument("--send-incident", help="Queue one historical report once")

    args = parser.parse_args()

    os.umask(0o077)

    operations = json.loads((args.root / "operations.json").read_text())

    if operations.get("finding_notifications_enabled") is not True:
        return

    config = NotificationConfig.load(operations)
    findings = args.root / "findings"
    database = findings / "incidents.sqlite3"

    if not database.exists():
        return

    with investigation_lock(findings / store.LOCK_NAME) as acquired:
        if not acquired:
            return

        with closing(store.connect(findings / store.DATABASE_NAME)) as db:
            with investigation_lock(findings / ".poll.lock") as available:
                if available:
                    store.enqueue(
                        db,
                        config.topic_arn,
                        snapshot(database, config),
                        int(time.time()),
                    )

            if args.send_incident:
                with db:
                    db.execute(
                        """
                        UPDATE deliveries SET status = 'pending'
                        WHERE topic_arn = ? AND incident_id = ?
                            AND status = 'historical'
                        """,
                        (config.topic_arn, args.send_incident),
                    )

            if not args.initialize_only:
                deliver_one(db, config, Publisher(config), int(time.time()))


if __name__ == "__main__":
    main()
