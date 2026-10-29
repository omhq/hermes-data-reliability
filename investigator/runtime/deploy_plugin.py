import argparse
import gzip
import hashlib
import io
import re
import subprocess
import tarfile
from pathlib import Path

from .deploy import PROJECT_ROOT, ssh_command, terraform_output


def build_archive(root: Path = PROJECT_ROOT) -> bytes:
    paths = [
        root / "investigator/__init__.py",
        root / "investigator/cli_output.py",
        root / "investigator/tools/__init__.py",
        root / "investigator/runtime/__init__.py",
        root / "investigator/runtime/image.txt",
        root / "investigator/runtime/verify_hermes_plugin.py",
        root / "investigator/runtime/verify_plugin_image.py",
        root / "tool_boundary/__init__.py",
        root / "tool_boundary/wire.py",
    ]

    for package in ("hermes_lab_aws", "aws_cloudwatch", "aws_cloudtrail"):
        directory = root / "investigator/tools" / package
        paths.extend(directory.glob("*.py"))
        paths.append(directory / "plugin.yaml")

    output = io.BytesIO()

    with tarfile.open(fileobj=output, mode="w") as archive:
        for path in sorted(paths):
            info = archive.gettarinfo(str(path), str(path.relative_to(root)))
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""

            with path.open("rb") as source:
                archive.addfile(info, source)

    return gzip.compress(output.getvalue(), mtime=0)


def main() -> None:
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
    stage = subprocess.check_output(
        ssh + ["mktemp -d /tmp/hermes-plugin.XXXXXXXX"], text=True
    ).strip()

    if not re.fullmatch(r"/tmp/hermes-plugin\.[A-Za-z0-9]{8}", stage):
        raise SystemExit("Unexpected staging path")

    try:
        subprocess.run(
            ssh + [f"tar -xzf - -C {stage}"], input=archive, check=True
        )
        subprocess.run(
            ssh
            + [
                f"cd {stage} && sudo /usr/bin/python3.14 -m "
                "investigator.runtime.verify_hermes_plugin "
                f"--install --release-id {release_id}"
            ],
            check=True,
        )
    finally:
        subprocess.run(ssh + [f"sudo rm -rf -- {stage}"], check=False)


if __name__ == "__main__":
    main()
