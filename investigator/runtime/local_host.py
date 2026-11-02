import argparse
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path

from investigator.logging_config import configure_logging

from .deploy import build_archive, validate_tool_configuration

ROOT = Path("/srv/hermes-lab")
SOURCE = Path("/opt/hermes-lab-source")
INPUT = Path("/run/hermes-lab-input")
LOGGER = logging.getLogger(__name__)


def install_credentials() -> None:
    directory = Path("/run/hermes-lab-credentials")
    directory.mkdir(exist_ok=True)
    os.chown(directory, 0, 1000)
    directory.chmod(0o750)
    staged = directory / ".aws-next"

    shutil.copyfile(INPUT / "aws-credentials.json", staged)
    os.chown(staged, 0, 1000)
    staged.chmod(0o640)
    staged.replace(directory / "aws.json")


def check_services() -> bool:
    units = ["hermes-local-host.service", "docker.service"]

    if (ROOT / "ops/enabled").exists():
        units.extend(
            [
                "hermes-lab-executor.service",
                "hermes-lab-tools.service",
                "hermes-lab-runtime.service",
                "hermes-lab-poller.timer",
                "hermes-lab-log-rules.timer",
                "hermes-lab-investigation.timer",
                "hermes-lab-pipeline-health.timer",
                "hermes-lab-notifications.timer",
            ]
        )

    result = subprocess.run(
        ["systemctl", "show", "--property=ActiveState", "--value", *units],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    states = result.stdout.split()

    return (
        result.returncode == 0
        and len(states) == len(units)
        and all(state == "active" for state in states)
    )


def install() -> None:
    configuration = (INPUT / "investigator.json").read_bytes()

    validate_tool_configuration(configuration)
    ROOT.mkdir(exist_ok=True)

    for name in ("investigator.json", "operations.json"):
        shutil.copyfile(INPUT / name, ROOT / name)
        os.chown(ROOT / name, 0, 1000)
        (ROOT / name).chmod(0o640)

    install_credentials()
    Path("/etc/hermes-lab").mkdir(exist_ok=True)
    Path("/etc/hermes-lab/local-runtime").touch(mode=0o600)
    first = not (ROOT / "ops/investigator.runtime.json").exists()
    archive = build_archive(SOURCE)
    release = ROOT / "releases" / hashlib.sha256(archive).hexdigest()[:20]

    if not release.exists():
        release.mkdir(parents=True)

        with tarfile.open(fileobj=BytesIO(archive), mode="r:gz") as bundle:
            bundle.extractall(release, filter="data")

    environment = ROOT / "collector-venv-py314"

    if not environment.exists():
        environment.symlink_to("/opt/hermes-lab-collector")

    findings = ROOT / "findings"
    findings.mkdir(exist_ok=True, mode=0o700)
    os.chown(findings, 1000, 1000)

    subprocess.run(
        [sys.executable, "-m", "investigator.runtime.install"],
        cwd=release,
        check=True,
        env={**os.environ, "PIP_NO_INDEX": "1"},
    )

    if first:
        subprocess.run(["labctl", "start"], check=True)

    LOGGER.info("Local host installed with the shared runtime services.")


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--credentials-only", action="store_true")
    actions.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("Local host installation requires root")

    if args.check:
        raise SystemExit(0 if check_services() else 1)
    elif args.credentials_only:
        install_credentials()
    else:
        install()


if __name__ == "__main__":
    main()
