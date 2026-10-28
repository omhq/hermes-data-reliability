import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from investigator.cli_output import write_json
from investigator.executor.wire import call

ROOT = Path("/opt/hermes-lab-plugins")
SOCKETS = Path("/run/hermes-lab-executor")


def install(source: Path, release_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{20}", release_id):
        raise RuntimeError("Invalid release ID")

    release = ROOT / "releases" / release_id
    release.parent.mkdir(parents=True, exist_ok=True)

    if not release.exists():
        shutil.copytree(
            source, release, ignore=shutil.ignore_patterns("__pycache__")
        )

    for path in (ROOT, ROOT / "releases", release, *release.rglob("*")):
        os.chown(path, 0, 0)
        path.chmod(0o755 if path.is_dir() else 0o644)

    return release


def verify(release: Path) -> dict[str, Any]:
    database_path = "file:/srv/hermes-lab/findings/incidents.sqlite3?mode=ro"

    with closing(sqlite3.connect(database_path, uri=True)) as database:
        row = database.execute(
            "SELECT evidence FROM incidents ORDER BY detected_at DESC LIMIT 1"
        ).fetchone()

    if row is None:
        raise RuntimeError("No existing synthetic incident")

    incident = json.loads(row[0])
    context = {
        "run_id": incident["run_id"],
        "attempt_ids": [incident["attempt_id"]],
        "incident_time_ms": int(
            datetime.fromisoformat(
                incident["timestamp"].replace("Z", "+00:00")
            ).timestamp()
            * 1000
        ),
    }

    image = (release / "investigator/runtime/image.txt").read_text().strip()

    if not re.fullmatch(
        r"nousresearch/hermes-agent@sha256:[a-f0-9]{64}", image
    ):
        raise RuntimeError("Invalid pinned image")

    identity = (SOCKETS / "requests.sock").stat()
    container_name = "hermes-lab-plugin-check-" + uuid.uuid4().hex[:12]
    command = [
        "docker",
        "run",
        "--name",
        container_name,
        "--rm",
        "--pull=never",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--memory",
        "512m",
        "--cpus",
        "0.5",
        "--pids-limit",
        "64",
        "--user",
        f"{identity.st_uid}:{identity.st_gid}",
        "--tmpfs",
        "/tmp:rw,nosuid,size=128m",
        "--env",
        "HERMES_HOME=/tmp/hermes-plugin-check",
        "--env",
        "HOME=/tmp",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--env",
        "PYTHONPATH=/opt/hermes",
        "--env",
        "HERMES_BUNDLED_PLUGINS=/nonexistent",
        "--env",
        "HERMES_LAB_CONTEXT_ID",
        "--mount",
        f"type=bind,src={release}/investigator/tools/hermes_lab_aws,dst=/opt/lab-plugin,readonly",
        "--mount",
        f"type=bind,src={release}/investigator/tools/aws_cloudwatch,dst=/opt/cloudwatch-plugin,readonly",
        "--mount",
        f"type=bind,src={release}/investigator/tools/aws_cloudtrail,dst=/opt/cloudtrail-plugin,readonly",
        "--mount",
        f"type=bind,src={release}/investigator/runtime/verify_plugin_image.py,dst=/verify.py,readonly",
        "--mount",
        f"type=bind,src={SOCKETS}/requests.sock,dst=/run/hermes-lab-aws/requests.sock,readonly",
        "--entrypoint",
        "/opt/hermes/.venv/bin/python",
        "--interactive",
        image,
        "/verify.py",
    ]

    opened = call(
        SOCKETS / "control.sock", {"operation": "open", "context": context}
    )

    if not opened.get("ok"):
        raise RuntimeError("Could not open verification context")

    environment = {**os.environ, "HERMES_LAB_CONTEXT_ID": opened["context_id"]}

    try:
        result = subprocess.run(
            command,
            input=json.dumps(
                {
                    "run_id": incident["run_id"],
                    "attempt_id": incident["attempt_id"],
                }
            ),
            text=True,
            capture_output=True,
            env=environment,
            timeout=180,
        )

        if result.returncode:
            # Keep isolated profile loader errors private as well.
            private = Path("/var/lib/hermes-lab-executor/plugin-check.log")
            private.write_text(result.stdout + result.stderr)
            private.chmod(0o600)
            raise RuntimeError(
                "Plugin check failed; private host log retained"
            )

        report = json.loads(result.stdout)

        if report.get("passed") is not True:
            raise RuntimeError("Plugin check did not pass")

        return report
    finally:
        try:
            subprocess.run(
                ["docker", "rm", "--force", container_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
        finally:
            call(
                SOCKETS / "control.sock",
                {"operation": "close", "context_id": opened["context_id"]},
            )


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument("--install", action="store_true")
    parser.add_argument("--release-id")
    parser.add_argument("--release", type=Path)

    args = parser.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("Run verification as root on EC2")

    if args.install:
        release = install(
            Path(__file__).resolve().parents[2], args.release_id or ""
        )
    elif args.release:
        release = args.release.resolve()
    else:
        parser.error("Specify --install --release-id or --release")

    report = verify(release)

    report["plugin_release"] = release.name
    report["automatic_collector_integration"] = False

    write_json(report, indent=2)


if __name__ == "__main__":
    main()
