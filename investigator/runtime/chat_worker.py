import argparse
import os
import re
import signal
import subprocess
import sys
import time
from importlib import import_module
from pathlib import Path
from types import FrameType

from tool_boundary.catalog import ToolPackages

WORKERS = Path("/tmp/hermes-lab-chats")


def pid_path(session_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", session_id):
        raise ValueError("Invalid session identifier")

    return WORKERS / (session_id + ".pid")


def stop(session_id: str) -> None:
    path = pid_path(session_id)

    if not path.exists():
        return

    pid = int(path.read_text())

    try:
        environment = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        marker = ("HERMES_LAB_SESSION_ID=" + session_id).encode()

        if marker in environment:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError, FileNotFoundError:
        pass


def read_agent_config(home: Path) -> object:
    """Use the YAML parser bundled with the selected Hermes image."""
    try:
        parser = import_module("hermes_yaml")
        return parser.safe_load((home / "config.yaml").read_text())
    except Exception:
        raise ValueError("Invalid agent tool package configuration") from None


def launch(
    session_id: str,
    seconds: float,
    query: str | None = None,
    stream_json: bool = False,
    incident: bool = False,
) -> int:
    home = Path(os.environ.get("HERMES_HOME", "/opt/data"))

    try:
        selection = ToolPackages.from_agent_config(read_agent_config(home))
    except ValueError, TypeError:
        raise ValueError("Invalid agent tool package configuration") from None

    if not selection.toolsets:
        raise ValueError(
            "Enable at least one cloud tool package in config.yaml"
        )

    command = [
        "/opt/hermes/.venv/bin/hermes",
        "chat",
        "--cli",
        "--toolsets",
        ",".join(selection.toolsets),
        "--in",
        "/opt/hermes-lab/incident" if incident else "/opt/hermes-lab/chat",
    ]

    if incident:
        command += [
            "--oneshot",
            "--query-file",
            "-",
            "--format",
            "stream-json",
        ]
    elif query is not None:
        command += ["--oneshot", "--query", query]

        if stream_json:
            command += ["--format", "stream-json"]

    return run_command(command, session_id, seconds)


def run_command(command: list[str], session_id: str, seconds: float) -> int:
    """Supervise a process group while preserving interactive input."""
    WORKERS.mkdir(exist_ok=True, mode=0o700)

    path = pid_path(session_id)

    path.write_text(str(os.getpid()))
    path.chmod(0o600)

    process = None
    stopping = False
    interactive = sys.stdin.isatty()
    foreground = os.tcgetpgrp(sys.stdin.fileno()) if interactive else None
    previous_ttou = signal.signal(signal.SIGTTOU, signal.SIG_IGN)

    def interrupt(number: int, _: FrameType | None) -> None:
        nonlocal stopping

        if number != signal.SIGINT:
            stopping = True
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, number)
            except ProcessLookupError:
                pass

    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, interrupt)

    try:
        # Interactive children must keep the controlling terminal. A new
        # session would make prompt_toolkit lose its keyboard reader.
        process = subprocess.Popen(
            command,
            start_new_session=not interactive,
            preexec_fn=os.setpgrp if interactive else None,
        )

        if interactive:
            os.tcsetpgrp(sys.stdin.fileno(), process.pid)
            os.killpg(process.pid, signal.SIGCONT)

        end = time.monotonic() + seconds

        while process.poll() is None and not stopping:
            if time.monotonic() >= end:
                stopping = True
                break

            time.sleep(0.2)

        return process.returncode if process.returncode is not None else 124
    finally:
        if process:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()

        path.unlink(missing_ok=True)

        if foreground is not None:
            os.tcsetpgrp(sys.stdin.fileno(), foreground)

        signal.signal(signal.SIGTTOU, previous_ttou)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument("action", choices=["launch", "stop"])
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--seconds", type=int, default=1800)

    source = parser.add_mutually_exclusive_group()

    source.add_argument("--query")
    source.add_argument("--incident", action="store_true")
    parser.add_argument("--stream-json", action="store_true")

    args = parser.parse_args()

    if args.action == "stop":
        stop(args.session_id)
    else:
        if not 1 <= args.seconds <= 1800:
            parser.error("seconds must be between 1 and 1800")

        raise SystemExit(
            launch(
                args.session_id,
                args.seconds,
                args.query,
                args.stream_json,
                args.incident,
            )
        )


if __name__ == "__main__":
    main()
