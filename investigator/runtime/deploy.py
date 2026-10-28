import io
import json
import gzip
import argparse
import hashlib
import logging
import shlex
import subprocess
import tarfile

from pathlib import Path

import yaml

from hermes_cloud_tools.packages import ToolPackages
from investigator.logging_config import configure_logging

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOGGER = logging.getLogger(__name__)


def terraform_output(name: str, raw: bool = False) -> bytes:
    """Read one nonsecret deployment output from local Terraform state."""
    return subprocess.check_output(
        ["terraform", "output", "-raw" if raw else "-json", name],
        cwd=PROJECT_ROOT / "infra/aws-agent",
    )


def validate_tool_configuration(executor_config: bytes) -> None:
    """Reject stale host capabilities before uploading a new agent config."""
    try:
        snapshot = ToolPackages.from_executor_config(json.loads(executor_config))
        configuration = yaml.safe_load(
            (PROJECT_ROOT / "investigator/runtime/config.yaml").read_text()
        )
        selected = ToolPackages.from_agent_config(configuration)
    except ValueError, TypeError, yaml.YAMLError:
        raise ValueError("Invalid agent tool package configuration") from None

    if snapshot != selected:
        raise ValueError(
            "Agent YAML differs from Terraform tool configuration; "
            "review and apply the agent plan before deploying"
        )


def build_archive(root: Path = PROJECT_ROOT) -> bytes:
    """Package source folders, excluding local state and Python bytecode."""
    archive = io.BytesIO()
    paths = []

    for folder in (
        "hermes_cloud_tools",
        "investigator",
        "pipeline",
        "evals",
    ):
        paths.extend((root / folder).rglob("*"))

    with tarfile.open(fileobj=archive, mode="w") as bundle:
        for path in sorted(paths):
            relative = path.relative_to(root)

            if not path.is_file() or any(
                part.startswith(".") or part in {"__pycache__", "findings"}
                for part in relative.parts
            ):
                continue

            info = bundle.gettarinfo(str(path), arcname=str(relative))
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            info.mode = 0o755 if info.mode & 0o111 else 0o644

            with path.open("rb") as source:
                bundle.addfile(info, source)

    return gzip.compress(archive.getvalue(), mtime=0)


def ssh_command(args: argparse.Namespace, destination: str) -> list[str]:
    command = [
        "ssh",
        "-i",
        str(args.ssh_key.expanduser()),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=10",
    ]

    if args.known_hosts:
        command += [
            "-o",
            f"UserKnownHostsFile={args.known_hosts.expanduser()}",
        ]

    return command + [destination]


def upload_command(release: str) -> str:
    """Extract readable source without changing runtime-created files."""
    destination = shlex.quote(release)

    return (
        f"umask 022 && mkdir -p {destination} "
        f"&& tar --no-same-owner -xzf - -C {destination}"
    )


def main() -> None:
    configure_logging()

    parser = argparse.ArgumentParser()

    parser.add_argument("--ssh-key", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path)
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Activate the uploaded release with its installer",
    )

    args = parser.parse_args()

    destination = terraform_output("ssh_destination", raw=True).decode().strip()
    outputs = {name: terraform_output(name) for name in ("operations", "investigator")}

    validate_tool_configuration(outputs["investigator"])

    ssh = ssh_command(args, destination)
    archive = build_archive()
    release_id = hashlib.sha256(archive).hexdigest()[:20]
    release = "/srv/hermes-lab/releases/" + release_id

    subprocess.run(ssh + [upload_command(release)], input=archive, check=True)

    for name, filename in (
        ("operations", "operations.json"),
        ("investigator", "investigator.json"),
    ):
        subprocess.run(
            ssh + [f"umask 077 && cat > /srv/hermes-lab/{filename}"],
            input=outputs[name],
            check=True,
        )

    LOGGER.info("Uploaded release %s and nonsecret lab identifiers.", release_id)

    if args.activate:
        try:
            subprocess.run(
                ssh
                + [
                    f"cd {release} && sudo /usr/bin/python3.14 "
                    "-m investigator.runtime.install"
                ],
                check=True,
            )
        except subprocess.CalledProcessError:
            raise SystemExit(
                "Release " + release_id + " uploaded, but activation failed. "
                "Resolve the host diagnostic above before retrying activation."
            ) from None
    else:
        LOGGER.info(
            "Activate with: cd %s && sudo /usr/bin/python3.14 "
            "-m investigator.runtime.install",
            release,
        )


if __name__ == "__main__":
    main()
