import argparse
import grp
import json
import logging
import os
import pwd
import re
import shutil
import subprocess
from pathlib import Path

from investigator.capabilities.pipeline_health.collection import (
    with_collection,
)
from investigator.capabilities.pipeline_health.definition import bind, parse
from investigator.logging_config import configure_logging

ROOT = Path("/opt/hermes-lab-executor")
UNIT = "hermes-lab-executor.service"
LOGGER = logging.getLogger(__name__)


def run(*command: str) -> None:
    subprocess.run(command, check=True)


def main() -> None:
    configure_logging()

    parser = argparse.ArgumentParser()

    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--release-id", required=True)

    args = parser.parse_args()

    if os.geteuid() != 0 or not re.fullmatch(r"[a-f0-9]{20}", args.release_id):
        raise SystemExit("Root and a valid release identifier are required")

    config = json.loads(args.config.read_text())
    health_path = Path("/etc/hermes-lab/pipeline-health.json")
    definition = (
        json.loads(health_path.read_text())
        if health_path.exists()
        else {
            "version": 4,
            "consumer": {
                "id": "pipeline_health",
                "enabled": True,
                "subscriptions": {},
            },
            "sources": {},
            "pipelines": {},
        }
    )
    parsed = parse(definition)
    collection = json.loads(
        Path("/etc/hermes-lab/collection.json").read_text()
    )
    bind(parsed, with_collection(config, parsed, collection))

    try:
        grp.getgrgid(10000)
    except KeyError:
        run("groupadd", "--gid", "10000", "hermes-lab-agent")

    try:
        pwd.getpwnam("hermes-lab-executor")
    except KeyError:
        run(
            "useradd",
            "--system",
            "--user-group",
            "--no-create-home",
            "--shell",
            "/sbin/nologin",
            "hermes-lab-executor",
        )

    observations = Path("/var/lib/hermes-lab-observations")
    observations.mkdir(exist_ok=True, mode=0o750)
    os.chown(
        observations,
        pwd.getpwnam("ec2-user").pw_uid,
        grp.getgrnam("hermes-lab-executor").gr_gid,
    )
    observations.chmod(0o750)

    release = ROOT / "releases" / args.release_id

    release.parent.mkdir(parents=True, exist_ok=True)

    if not release.exists():
        shutil.copytree(args.source / "observations", release / "observations")
        shutil.copytree(
            args.source / "tool_boundary",
            release / "tool_boundary",
        )
        shutil.copytree(
            args.source / "investigator/capabilities",
            release / "investigator/capabilities",
        )
        for relative in (
            "incidents/__init__.py",
            "incidents/specification.py",
        ):
            destination = release / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(args.source / relative, destination)

        for relative in (
            "investigator/__init__.py",
            "investigator/cli_output.py",
            "investigator/runtime/__init__.py",
            "investigator/runtime/verify_executor.py",
        ):
            destination = release / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(args.source / relative, destination)

    for path in (ROOT, ROOT / "releases", release, *release.rglob("*")):
        os.chown(path, 0, 0)
        path.chmod(0o755 if path.is_dir() else 0o644)

    config_dir = Path("/etc/hermes-lab")

    config_dir.mkdir(exist_ok=True, mode=0o755)

    config_path = config_dir / "executor.json"

    config_path.write_text(json.dumps(config))
    config_path.chmod(0o644)

    if not health_path.exists():
        health_path.write_text(json.dumps(definition))
        health_path.chmod(0o644)

    shutil.copy2(
        args.source / "investigator/runtime" / UNIT,
        Path("/etc/systemd/system") / UNIT,
    )

    staged = ROOT / ".code-next"

    staged.unlink(missing_ok=True)
    staged.symlink_to(release, target_is_directory=True)
    staged.replace(ROOT / "code")

    run("systemctl", "daemon-reload")
    run("systemctl", "enable", UNIT)

    if Path("/srv/hermes-lab/ops/enabled").exists():
        run("systemctl", "restart", UNIT)

    LOGGER.info("Executor installed; Hermes runtime and findings preserved.")


if __name__ == "__main__":
    main()
