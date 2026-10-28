import json
import logging
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from investigator.logging_config import configure_logging

ROOT = Path("/srv/hermes-lab")
LOGGER = logging.getLogger(__name__)


def backup_fresh(operations: dict[str, Any]) -> bool:
    cache = ROOT / "ops/backup-health.json"
    value = json.loads(cache.read_text()) if cache.exists() else {}

    if time.time() - value.get("checked_at", 0) > 3600:
        response = subprocess.run(
            [
                "aws",
                "backup",
                "list-recovery-points-by-backup-vault",
                "--region",
                operations["region"],
                "--backup-vault-name",
                operations["backup_vault"],
                "--output",
                "json",
                "--no-cli-pager",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

        points = json.loads(response.stdout)["RecoveryPoints"]
        dates = [
            datetime.fromisoformat(
                p["CreationDate"].replace("Z", "+00:00")
            ).timestamp()
            for p in points
            if p.get("Status") == "COMPLETED"
        ]
        value = {
            "checked_at": int(time.time()),
            "last_backup_at": max(dates, default=0),
        }

        cache.write_text(json.dumps(value))

    return time.time() - value["last_backup_at"] < 36 * 3600


def healthy() -> dict[str, Any]:
    issues = []

    if shutil.disk_usage(ROOT).free < 1024**3:
        issues.append("low_disk")

    enabled = (ROOT / "ops/enabled").exists()

    if enabled:
        if subprocess.run(
            [
                "systemctl",
                "is-active",
                "--quiet",
                "hermes-lab-executor.service",
            ]
        ).returncode:
            issues.append("executor_stopped")

        for name in ("hermes-lab-tools", "hermes-lab-runtime"):
            response = subprocess.run(
                ["docker", "inspect", "--format", "{{json .State}}", name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            state = (
                json.loads(response.stdout) if response.returncode == 0 else {}
            )

            if (
                not state.get("Running")
                or state.get("Health", {}).get("Status") == "unhealthy"
            ):
                issues.append(name + "_stopped")

        if subprocess.run(
            ["systemctl", "is-active", "--quiet", "hermes-lab-poller.timer"]
        ).returncode:
            issues.append("poller_timer_stopped")

        try:
            heartbeat = json.loads(
                (ROOT / "findings/last-poll.json").read_text()
            )
            last = heartbeat["completed_at"]

            if time.time() - last > 600:
                issues.append("poller_stale")
            if any(
                source.get("status") != "ready"
                for source in heartbeat.get("sources", {}).values()
            ):
                issues.append("collector_source_degraded")
        except OSError, ValueError, KeyError:
            issues.append("poller_heartbeat_missing")

        dbfile = ROOT / "findings/incidents.sqlite3"

        if dbfile.exists():
            with sqlite3.connect(f"file:{dbfile}?mode=ro", uri=True) as db:
                exhausted = db.execute(
                    """
                    SELECT COUNT(*) FROM incidents
                    WHERE status = 'exhausted' AND detected_at > ?
                    """,
                    (int(time.time()) - 86400,),
                ).fetchone()[0]

                if exhausted:
                    issues.append("investigations_exhausted")

    return {
        "healthy": not issues,
        "mode": "running" if enabled else "paused",
        "issues": issues,
        "checked_at": int(time.time()),
    }


def main() -> None:
    configure_logging()

    operations = json.loads((ROOT / "operations.json").read_text())

    try:
        result = healthy()

        if not backup_fresh(operations):
            result["healthy"] = False
            result["issues"].append("backup_older_than_36_hours")
    except Exception:
        result = {
            "healthy": False,
            "issues": ["health_check_failed"],
            "checked_at": int(time.time()),
        }

    (ROOT / "ops/health.json").write_text(json.dumps(result))

    data = [
        {
            "MetricName": "Healthy",
            "Dimensions": [
                {"Name": "InstanceId", "Value": operations["instance_id"]}
            ],
            "Value": int(result["healthy"]),
            "Unit": "Count",
        }
    ]

    sent = subprocess.run(
        [
            "aws",
            "cloudwatch",
            "put-metric-data",
            "--region",
            operations["region"],
            "--namespace",
            "HermesLab/PipelineLab",
            "--metric-data",
            json.dumps(data),
        ],
        capture_output=True,
        timeout=30,
    )

    LOGGER.log(
        logging.INFO if result["healthy"] else logging.WARNING,
        "Health check completed. %s",
        json.dumps(result),
    )

    if sent.returncode:
        raise SystemExit("Health metric delivery failed")


if __name__ == "__main__":
    main()
