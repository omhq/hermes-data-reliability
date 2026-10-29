import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path("/srv/hermes-lab")


def validate_image(image: str) -> None:
    """Accept only reviewed official image digests before using Docker."""
    if not re.fullmatch(
        r"nousresearch/hermes-agent@sha256:[a-f0-9]{64}", image
    ):
        raise ValueError("Hermes must use an immutable official image digest")


def verify_image_python(image: str) -> None:
    """Check an already downloaded image without pulling inside the probe."""
    validate_image(image)

    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--memory",
            "128m",
            "--entrypoint",
            "/opt/hermes/.venv/bin/python",
            image,
            "-c",
            "import sys; sys.exit(sys.version_info[:2] != (3, 14))",
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )

    if result.returncode:
        raise ValueError("The selected Hermes image must run Python 3.14")


def common_arguments(command: str) -> list[str]:
    arguments = ["docker", command]

    if command == "run":
        arguments.append("--rm")

    return arguments + [
        "--log-opt",
        "max-size=10m",
        "--log-opt",
        "max-file=3",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "256",
    ]


def tools_arguments(runtime: dict[str, str]) -> list[str]:
    ops = ROOT / "ops"
    release = ROOT / "code"

    return common_arguments("run") + [
        "--init",
        "--name",
        "hermes-lab-tools",
        "--network",
        "hermes-lab-tools-net",
        "--network-alias",
        "hermes-lab-tools",
        "--memory",
        "512m",
        "--cpus",
        "0.5",
        "--read-only",
        "--tmpfs",
        "/run:rw,nosuid,size=16m",
        "--tmpfs",
        "/tmp:rw,nosuid,size=128m",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "SETUID",
        "--cap-add",
        "SETGID",
        "--cap-add",
        "SYS_CHROOT",
        "--cap-add",
        "CHOWN",
        "--cap-add",
        "DAC_OVERRIDE",
        "-v",
        f"{ops}/tool-server:/tool-ssh:ro",
        "-v",
        f"{ROOT}/tool-workspace:/workspace",
        "-v",
        f"{ROOT}/tool-home:/home/hermes",
        "-v",
        f"{ROOT}/evidence:/evidence:ro",
        "-v",
        f"{release}/investigator/runbooks:/lab/runbooks:ro",
        runtime["tools_image"],
    ]


def runtime_arguments(runtime: dict[str, str]) -> list[str]:
    ops = ROOT / "ops"
    image = runtime["hermes_image"]

    validate_image(image)

    return common_arguments("create") + [
        "--name",
        "hermes-lab-runtime",
        "--network",
        "hermes-lab-runtime-net",
        "--memory",
        "3g",
        "--cpus",
        "1.5",
        "-e",
        "TERMINAL_SSH_HOST=hermes-lab-tools",
        "-e",
        "TERMINAL_SSH_PORT=2222",
        "-e",
        "TERMINAL_SSH_USER=hermes",
        "-e",
        "TERMINAL_SSH_KEY=/run/tool-client/id_ed25519",
        "--health-cmd",
        (
            "ssh -i /run/tool-client/id_ed25519 -p 2222 "
            "-o BatchMode=yes -o StrictHostKeyChecking=yes "
            "-o ConnectTimeout=5 hermes@hermes-lab-tools true"
        ),
        "--health-interval",
        "60s",
        "--health-timeout",
        "15s",
        "--health-start-period",
        "30s",
        "--health-start-interval",
        "5s",
        "-v",
        f"{ROOT}/hermes:/opt/data",
        "-v",
        f"{ROOT}/workspace:/workspace",
        "-v",
        f"{ops}/tool-client:/run/tool-client:ro",
        "-v",
        f"{ops}/known_hosts:/etc/ssh/ssh_known_hosts:ro",
        "-v",
        f"{ROOT}/code/investigator/tools/hermes_lab_aws:/opt/hermes/plugins/hermes-lab-aws:ro",
        "-v",
        f"{ROOT}/code/investigator/tools/aws_cloudwatch:/opt/hermes/plugins/hermes-cloudwatch:ro",
        "-v",
        f"{ROOT}/code/investigator/tools/aws_cloudtrail:/opt/hermes/plugins/hermes-cloudtrail:ro",
        "-v",
        f"{ROOT}/code/tool_boundary/__init__.py:/opt/hermes-lab/tool_boundary/__init__.py:ro",
        "-v",
        f"{ROOT}/code/tool_boundary/catalog.py:/opt/hermes-lab/tool_boundary/catalog.py:ro",
        "-v",
        "/run/hermes-lab-executor/requests:/run/hermes-lab-aws:ro",
        "-v",
        f"{ROOT}/code/investigator/runtime/chat_worker.py:/opt/hermes-lab/chat_worker.py:ro",
        "-v",
        f"{ROOT}/code/investigator/runbooks/CHAT.md:/opt/hermes-lab/chat/AGENTS.md:ro",
        "-v",
        f"{ROOT}/code/investigator/runbooks/RUNBOOK.md:/opt/hermes-lab/incident/AGENTS.md:ro",
        image,
        "sleep",
        "infinity",
    ]


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument("role", choices=["tools", "hermes"])

    args = parser.parse_args()
    runtime = json.loads((ROOT / "ops/investigator.runtime.json").read_text())

    if args.role == "tools":
        raise SystemExit(subprocess.call(tools_arguments(runtime)))

    subprocess.run(
        runtime_arguments(runtime), check=True, stdout=subprocess.DEVNULL
    )

    # Attach the private tools network before executing the model runtime.
    subprocess.run(
        [
            "docker",
            "network",
            "connect",
            "hermes-lab-tools-net",
            "hermes-lab-runtime",
        ],
        check=True,
    )

    raise SystemExit(
        subprocess.call(["docker", "start", "-a", "hermes-lab-runtime"])
    )


if __name__ == "__main__":
    main()
