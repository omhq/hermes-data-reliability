import os
import re
import sys
import time
import json
import shutil
import logging
import sqlite3
import argparse
import subprocess

from contextlib import closing
from pathlib import Path
from typing import Any

from investigator.cli_output import write_json
from investigator.collector.ledger import budget_status
from investigator.logging_config import configure_logging

from .containers import verify_image_python
from .notification_store import (
    DATABASE_NAME,
)
from .notification_store import (
    validate_schema as validate_notifications,
)

ROOT = Path("/srv/hermes-lab")
OPS = ROOT / "ops"


LOGGER = logging.getLogger(__name__)


def run(
    *args: str | Path, check: bool = True, **kwargs: Any
) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(list(args), check=check, **kwargs)


def pause() -> None:
    (OPS / "enabled").unlink(missing_ok=True)
    run("systemctl", "disable", "hermes-lab.target")

    # Wait for each child; stopping only the target may return first.
    run(
        "systemctl",
        "stop",
        "hermes-lab-poller.timer",
        "hermes-lab-poller.service",
        "hermes-lab-notifications.timer",
        "hermes-lab-notifications.service",
        "hermes-lab-runtime.service",
        "hermes-lab-tools.service",
        "hermes-lab.target",
    )


def configure_collector(observations: Path | None = None) -> None:
    """Install a runtime-supplied YAML with the collector's isolated parser."""
    arguments = [
        str(ROOT / "collector-venv-py314/bin/python"),
        "-m",
        "investigator.collector.configure",
        "--approved",
        "/etc/hermes-lab/executor.json",
        "--destination",
        str(ROOT / "observations.yaml"),
    ]

    if observations is not None:
        arguments.extend(["--source", str(observations)])

    run(*arguments, cwd=ROOT / "code")


def start(observations: Path | None = None) -> None:
    configure_collector(observations)
    start_services()


def start_services() -> None:
    (OPS / "enabled").touch(mode=0o600)
    run("systemctl", "enable", "hermes-lab.target")
    run(
        "systemctl",
        "start",
        "hermes-lab.target",
        "hermes-lab-tools.service",
        "hermes-lab-runtime.service",
        "hermes-lab-poller.timer",
        "hermes-lab-notifications.timer",
    )


def status() -> None:
    state = json.loads((OPS / "investigator.runtime.json").read_text())
    budget = None
    database = ROOT / "findings/incidents.sqlite3"

    if database.exists():
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as ledger:
            budget = budget_status(ledger, int(time.time()))

    write_json(
        {
            "mode": "running" if (OPS / "enabled").exists() else "paused",
            "runtime": state,
            "observations": str(ROOT / "observations.yaml"),
            "investigation_budget": budget,
        },
        indent=2,
    )

    run(
        "systemctl",
        "--no-pager",
        "show",
        "hermes-lab.target",
        "hermes-lab-poller.timer",
        "hermes-lab-health.timer",
        "hermes-lab-notifications.timer",
        "hermes-lab-executor.service",
        "-p",
        "Id",
        "-p",
        "ActiveState",
        check=False,
    )

    run(
        "docker",
        "ps",
        "--filter",
        "name=hermes-lab-",
        "--format",
        "{{.Names}} {{.Status}}",
        check=False,
    )

    if (OPS / "health.json").exists():
        write_json(json.loads((OPS / "health.json").read_text()))


def checkpoint() -> Path:
    """Save an offline checkpoint; off-host backups are still required."""
    target = ROOT / "checkpoints" / str(time.time_ns())
    target.mkdir(parents=True, mode=0o700)

    for folder in ("hermes", "findings"):
        shutil.copytree(ROOT / folder, target / folder, symlinks=True)

    shutil.copy2(
        OPS / "investigator.runtime.json", target / "investigator.runtime.json"
    )

    if (ROOT / "observations.yaml").exists():
        shutil.copy2(ROOT / "observations.yaml", target / "observations.yaml")

    (OPS / "checkpoint.json").write_text(json.dumps({"path": str(target)}))

    return target


def restore_files(source: str | Path) -> None:
    if (OPS / "enabled").exists():
        raise SystemExit("Pause the agent before restoring files")

    for service in (
        "hermes-lab-runtime.service",
        "hermes-lab-poller.service",
        "hermes-lab-notifications.service",
    ):
        result = subprocess.run(["systemctl", "is-active", "--quiet", service])

        if result.returncode == 0:
            raise SystemExit("Runtime services must be stopped before restoring files")

    source = Path(source).resolve()

    if source == ROOT or (
        ROOT in source.parents and ROOT / "checkpoints" not in source.parents
    ):
        raise SystemExit(
            "Restore source must be an independently mounted recovery volume"
        )

    for folder in ("hermes", "findings"):
        if not (source / folder).is_dir():
            raise SystemExit("Restore source must contain hermes/ and findings/")

    stage = ROOT / (".restore-" + str(time.time_ns()))
    stage.mkdir(mode=0o700)

    try:
        for folder in ("hermes", "findings"):
            shutil.copytree(source / folder, stage / folder, symlinks=True)

        for relative in ("hermes/state.db", "findings/incidents.sqlite3"):
            dbpath = stage / relative

            if not dbpath.is_file():
                raise SystemExit("Restore is missing a core SQLite database")

            with sqlite3.connect(dbpath) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise SystemExit("Restored SQLite integrity check failed")

        notifications = stage / "findings" / DATABASE_NAME

        if notifications.exists():
            with closing(sqlite3.connect(notifications)) as delivery_state:
                validate_notifications(delivery_state)

                if (
                    delivery_state.execute("PRAGMA integrity_check").fetchone()[0]
                    != "ok"
                ):
                    raise SystemExit("Restored notification integrity check failed")

        saved = checkpoint()

        if (source / "observations.yaml").is_file():
            shutil.copy2(source / "observations.yaml", ROOT / "observations.yaml")
            os.chown(ROOT / "observations.yaml", 0, 0)
            (ROOT / "observations.yaml").chmod(0o644)

        for folder, uid in (("hermes", 10000), ("findings", 1000)):
            for path in [stage / folder, *(stage / folder).rglob("*")]:
                os.chown(path, uid, uid, follow_symlinks=False)

            (ROOT / folder).rename(saved / ("live-" + folder))
            (stage / folder).rename(ROOT / folder)

        # Current reviewed runtime configuration stays authoritative.
        shutil.copy2(
            ROOT / "code/investigator/runtime/config.yaml",
            ROOT / "hermes/config.yaml",
        )
        os.chown(ROOT / "hermes/config.yaml", 10000, 10000)

        LOGGER.info(
            "Files restored and checked. Start the agent; reauthenticate "
            "if the backed-up OAuth token has expired or rotated."
        )
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def replace_runtime(state: dict[str, str]) -> None:
    state = {**state, "release": str((ROOT / "code").resolve())}
    (OPS / "investigator.runtime.json").write_text(json.dumps(state))


def change_image(action: str, image: str | None = None) -> None:
    """Checkpoint offline and preserve running intent during image changes."""
    was_running = (OPS / "enabled").exists()
    previous = json.loads((OPS / "investigator.runtime.json").read_text())
    selected_runtime: dict[str, str] = previous

    if action == "upgrade":
        if not image or not re.fullmatch(
            r"nousresearch/hermes-agent@sha256:[a-f0-9]{64}", image
        ):
            raise SystemExit("Supply --image with a reviewed official digest")

        run("docker", "pull", image, timeout=900)
        verify_image_python(image)

        selected_runtime = {**previous, "hermes_image": image}
    elif action == "rollback" and not (OPS / "rollback.json").exists():
        raise SystemExit("No previous deployment has been recorded for rollback")

    if action == "rollback":
        rollback = json.loads((OPS / "rollback.json").read_text())
        selected_runtime = rollback["runtime"]
        verify_image_python(selected_runtime["hermes_image"])

    pause()

    saved = checkpoint()

    if action == "upgrade":
        (OPS / "rollback.json").write_text(
            json.dumps({"runtime": previous, "checkpoint": str(saved)})
        )
    if action in ("upgrade", "rollback"):
        # Apply the validated image selection without replaying OAuth state.
        replace_runtime(selected_runtime)

    if was_running:
        try:
            start()
        except subprocess.CalledProcessError:
            pause()
            replace_runtime(previous)
            start()
            raise SystemExit(
                "Requested runtime failed readiness; "
                "the previous image selection was restored"
            )

    LOGGER.info("Offline checkpoint saved; state files were preserved.")


def main() -> None:
    configure_logging()

    if sys.argv[1:2] == ["chat"]:
        if os.geteuid() != 0:
            raise SystemExit("Use sudo labctl chat")

        raise SystemExit(
            subprocess.call(
                [
                    "/usr/bin/python3.14",
                    "-m",
                    "investigator.runtime.chat",
                    *sys.argv[2:],
                ],
                cwd=ROOT / "code",
            )
        )

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "action",
        choices=[
            "start",
            "pause",
            "stop",
            "status",
            "restart",
            "reset-collector",
            "upgrade",
            "rollback",
            "checkpoint",
            "restore-files",
            "login",
            "chat",
        ],
    )
    parser.add_argument("--image", help="Reviewed official Hermes digest for upgrade")
    parser.add_argument("--source", help="Mounted backup's srv/hermes-lab directory")
    parser.add_argument(
        "--observations",
        type=Path,
        help="YAML for start, restart, or reset-collector",
    )
    parser.add_argument(
        "--since",
        help="Reset collection to now or a lookback such as 15m (up to 24h)",
    )
    args, extra = parser.parse_known_args()

    if args.action != "chat" and extra:
        parser.error("Unrecognized arguments")
    if args.observations is not None and args.action not in (
        "start",
        "restart",
        "reset-collector",
    ):
        parser.error("--observations requires start, restart, or reset-collector")
    if args.since is not None and args.action != "reset-collector":
        parser.error("--since is supported only for reset-collector")

    if os.geteuid() != 0:
        raise SystemExit("Run with sudo: sudo labctl " + args.action)

    OPS.mkdir(exist_ok=True, mode=0o700)

    if args.action == "chat":
        raise SystemExit(
            subprocess.call(
                [
                    "/usr/bin/python3.14",
                    "-m",
                    "investigator.runtime.chat",
                    *extra,
                ],
                cwd=ROOT / "code",
            )
        )
    elif args.action in ("pause", "stop"):
        pause()
    elif args.action == "start":
        start(args.observations)
    elif args.action == "status":
        status()
    elif args.action == "restart":
        # Reject an invalid replacement before interrupting the live runtime.
        configure_collector(args.observations)
        pause()
        start_services()
    elif args.action == "reset-collector":
        arguments = [
            str(ROOT / "collector-venv-py314/bin/python"),
            "-m",
            "investigator.runtime.reset_collector",
            "--since",
            args.since if args.since is not None else "now",
        ]

        if args.observations is not None:
            arguments.extend(["--observations", str(args.observations)])

        result = run(*arguments, cwd=ROOT / "code", check=False)

        if result.returncode:
            raise SystemExit("Collector reset failed; see diagnostic above.")
    elif args.action == "restore-files":
        if not args.source:
            parser.error("restore-files requires --source")

        restore_files(args.source)
    elif args.action == "login":
        run(
            "docker",
            "exec",
            "-it",
            "--user",
            "hermes",
            "hermes-lab-runtime",
            "hermes",
            "auth",
            "add",
            "openai-codex",
            "--no-browser",
        )
    elif args.action in ("checkpoint", "upgrade", "rollback"):
        change_image(args.action, args.image)


if __name__ == "__main__":
    main()
