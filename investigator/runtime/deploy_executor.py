import argparse
import gzip
import hashlib
import io
import logging
import subprocess
import tarfile
from pathlib import Path

from investigator.logging_config import configure_logging

from .deploy import PROJECT_ROOT, ssh_command, terraform_output

LOGGER = logging.getLogger(__name__)


def build_archive(root: Path = PROJECT_ROOT) -> bytes:
    output = io.BytesIO()
    paths = list((root / "tool_boundary").glob("*.py")) + [
        root / "investigator/__init__.py",
        root / "investigator/logging_config.py",
        root / "investigator/cli_output.py",
        root / "investigator/runtime/__init__.py",
        root / "investigator/runtime/verify_executor.py",
        root / "investigator/runtime/install_executor.py",
        root / "investigator/runtime/hermes-lab-executor.service",
    ]

    with tarfile.open(fileobj=output, mode="w") as archive:
        for path in sorted(paths):
            info = archive.gettarinfo(str(path), str(path.relative_to(root)))
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""

            with path.open("rb") as source:
                archive.addfile(info, source)

    return gzip.compress(output.getvalue(), mtime=0)


def main() -> None:
    configure_logging()

    parser = argparse.ArgumentParser()

    parser.add_argument("--ssh-key", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)

    args = parser.parse_args()
    destination = (
        terraform_output("ssh_destination", raw=True).decode().strip()
    )
    ssh = ssh_command(args, destination)
    archive = build_archive()
    release_id = hashlib.sha256(archive).hexdigest()[:20]

    # A remote private staging directory; no credentials/state are uploaded.
    stage = subprocess.check_output(
        ssh + ["mktemp -d /tmp/hermes-executor.XXXXXXXX"], text=True
    ).strip()

    try:
        subprocess.run(
            ssh + [f"tar -xzf - -C {stage}"], input=archive, check=True
        )
        subprocess.run(
            ssh + [f"umask 077 && cat > {stage}/investigator.json"],
            input=terraform_output("investigator"),
            check=True,
        )

        subprocess.run(
            ssh
            + [
                f"cd {stage} && sudo /usr/bin/python3.14 -m "
                "investigator.runtime.install_executor "
                f"--source {stage} --config {stage}/investigator.json "
                f"--release-id {release_id}"
            ],
            check=True,
        )
    finally:
        subprocess.run(ssh + [f"rm -rf -- {stage}"], check=False)

    LOGGER.info(
        "Executor release %s deployed on the existing EC2.", release_id
    )


if __name__ == "__main__":
    main()
