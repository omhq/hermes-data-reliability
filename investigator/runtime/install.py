import os
import time
import json
import logging
import shutil
import sqlite3
import subprocess

from contextlib import closing
from pathlib import Path
from typing import Any

from investigator.collector.ledger import UnsupportedLedger, validate_schema
from investigator.logging_config import configure_logging

from .containers import validate_image, verify_image_python
from .notification_store import (
    DATABASE_NAME,
)
from .notification_store import (
    validate_schema as validate_notifications,
)
from .sessions import SessionUnavailable, investigation_lock

ROOT = Path("/srv/hermes-lab")
RELEASE = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops"
LOGGER = logging.getLogger(__name__)


def run(*args: str | Path, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(list(args), check=True, **kwargs)


def directory(path: Path, uid: int = 0, mode: int = 0o700) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, mode)
    os.chown(path, uid, uid)


def key(path: Path) -> None:
    if not path.exists():
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path))


def prepare_directories() -> None:
    directory(OPS)

    for folder in ("tool-home", "tool-workspace", "hermes", "workspace"):
        directory(ROOT / folder, 10000)

    directory(ROOT / "evidence", 1000, 0o755)
    directory(OPS / "tool-client", 10000)
    directory(OPS / "tool-server", 0, 0o755)

    key(OPS / "tool-client/id_ed25519")

    for path in (OPS / "tool-client").iterdir():
        os.chown(path, 10000, 10000)

    key(OPS / "tool-server/ssh_host_ed25519_key")

    public = (OPS / "tool-client/id_ed25519.pub").read_text()
    (OPS / "tool-server/authorized_keys").write_text(public)
    (OPS / "tool-server/authorized_keys").chmod(0o644)

    shutil.copy2(
        RELEASE / "investigator/runtime/sshd_config",
        OPS / "tool-server/sshd_config",
    )

    host = (OPS / "tool-server/ssh_host_ed25519_key.pub").read_text().split()

    (OPS / "known_hosts").write_text(
        "[hermes-lab-tools]:2222 " + " ".join(host[:2]) + "\n"
    )
    (OPS / "known_hosts").chmod(0o644)


def build_tools_image() -> tuple[str, str]:
    image = (RELEASE / "investigator/runtime/image.txt").read_text().strip()
    tools_tag = "hermes-lab-tools:" + RELEASE.name

    validate_image(image)
    run("docker", "pull", image, timeout=900)
    verify_image_python(image)
    run(
        "docker",
        "build",
        "--build-arg",
        "HERMES_IMAGE=" + image,
        "-f",
        str(RELEASE / "investigator/runtime/Tools.Dockerfile"),
        "-t",
        tools_tag,
        str(RELEASE / "investigator/runtime"),
    )

    tools_id = subprocess.check_output(
        ["docker", "image", "inspect", tools_tag, "--format", "{{.Id}}"],
        text=True,
    ).strip()

    return image, tools_id


def prepare_networks() -> None:
    if subprocess.run(
        ["docker", "network", "inspect", "hermes-lab-tools-net"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode:
        run("docker", "network", "create", "--internal", "hermes-lab-tools-net")

    if subprocess.run(
        ["docker", "network", "inspect", "hermes-lab-runtime-net"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode:
        run(
            "docker",
            "network",
            "create",
            "--opt",
            "com.docker.network.bridge.name=br-hl-runtime",
            "hermes-lab-runtime-net",
        )


def stop_services() -> None:
    # Complete builds before stopping the currently running deployment.
    units = Path("/etc/systemd/system")

    if (units / "hermes-lab-notifications.timer").exists():
        run(
            "systemctl",
            "stop",
            "hermes-lab-notifications.timer",
            "hermes-lab-notifications.service",
        )

    if (units / "hermes-lab-health.timer").exists():
        # Its entry point changes with the code symlink during layout upgrades.
        # install_units resumes the timer after code and units agree again.
        run(
            "systemctl",
            "stop",
            "hermes-lab-health.timer",
            "hermes-lab-health.service",
        )
    if (units / "hermes-lab-poller.timer").exists():
        run("systemctl", "disable", "--now", "hermes-lab-poller.timer")
    if (units / "hermes-lab-poller.service").exists():
        run("systemctl", "stop", "hermes-lab-poller.service")

    # Stop through Hermes while its container is still alive, so its supported
    # lifecycle command persists stopped intent for the next s6 reconciliation.
    subprocess.run(
        [
            "docker",
            "exec",
            "--user",
            "hermes",
            "hermes-lab-runtime",
            "hermes",
            "gateway",
            "stop",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )

    installed = [
        name
        for name in (
            "hermes-lab-runtime.service",
            "hermes-lab-tools.service",
            "hermes-lab.target",
        )
        if (units / name).exists()
    ]

    if installed:
        run("systemctl", "stop", *installed)

    subprocess.run(
        ["docker", "stop", "-t", "20", "hermes-lab-runtime"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def save_checkpoint(first: bool) -> tuple[dict[str, Any] | None, Path]:
    old_runtime = (
        json.loads((OPS / "investigator.runtime.json").read_text())
        if not first
        else None
    )
    offline = ROOT / "checkpoints" / str(int(time.time()))

    directory(offline)

    for folder in ("hermes", "findings"):
        if (ROOT / folder).exists():
            shutil.copytree(ROOT / folder, offline / folder, symlinks=True)

    if (ROOT / "observations.yaml").exists():
        shutil.copy2(ROOT / "observations.yaml", offline / "observations.yaml")

    return old_runtime, offline


def activate_release(
    old_runtime: dict[str, Any] | None,
    offline: Path,
    image: str,
    tools_id: str,
) -> None:
    current = ROOT / "code"

    if current.exists() and not current.is_symlink():
        current.rename(ROOT / ("code-before-operations-" + str(int(time.time()))))

    previous_release = str(current.resolve()) if current.is_symlink() else None
    staged = ROOT / ".code-next"

    staged.unlink(missing_ok=True)
    staged.symlink_to(RELEASE, target_is_directory=True)
    staged.replace(current)

    if old_runtime:
        (OPS / "rollback.json").write_text(
            json.dumps(
                {
                    "runtime": old_runtime,
                    "release": previous_release,
                    "checkpoint": str(offline),
                }
            )
        )

    (OPS / "investigator.runtime.json").write_text(
        json.dumps(
            {
                "hermes_image": image,
                "tools_image": tools_id,
                "release": str(RELEASE),
            }
        )
    )

    shutil.copy2(
        RELEASE / "investigator/runtime/config.yaml",
        ROOT / "hermes/config.yaml",
    )
    os.chown(ROOT / "hermes/config.yaml", 10000, 10000)
    (ROOT / "hermes/config.yaml").chmod(0o600)


def install_units(was_running: bool) -> None:
    for path in (RELEASE / "investigator/runtime").glob("hermes-lab*"):
        if path.suffix in (".service", ".timer", ".target"):
            shutil.copy2(path, Path("/etc/systemd/system") / path.name)

    Path("/usr/local/bin/labctl").write_text(
        "#!/bin/sh\n"
        "cd /srv/hermes-lab/code || exit 1\n"
        'exec /usr/bin/python3.14 -m investigator.runtime.control "$@"\n'
    )
    Path("/usr/local/bin/labctl").chmod(0o755)

    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "--now", "hermes-lab-health.timer")

    if was_running:
        (OPS / "enabled").touch(mode=0o600)
        run("systemctl", "enable", "--now", "hermes-lab.target")


def prepare_collector() -> None:
    """Prepare the collector's host dependency and operator configuration."""
    environment = ROOT / "collector-venv-py314"

    if not (environment / "bin/python").exists():
        run("/usr/bin/python3.14", "-m", "venv", str(environment))

    run(
        str(environment / "bin/python"),
        "-m",
        "pip",
        "install",
        "--requirement",
        str(RELEASE / "investigator/collector/requirements.txt"),
    )

    run(
        str(environment / "bin/python"),
        "-m",
        "investigator.collector.configure",
        "--approved",
        str(ROOT / "investigator.json"),
        "--destination",
        str(ROOT / "observations.yaml"),
        cwd=RELEASE,
    )


def activate() -> None:
    if os.geteuid() != 0:
        raise SystemExit("Run installer with sudo")

    database = ROOT / "findings/incidents.sqlite3"

    if database.exists():
        with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as ledger:
            validate_schema(ledger)

    notifications = ROOT / "findings" / DATABASE_NAME

    if notifications.exists():
        with closing(
            sqlite3.connect(f"file:{notifications}?mode=ro", uri=True)
        ) as delivery_state:
            validate_notifications(delivery_state)

    first = not (OPS / "investigator.runtime.json").exists()
    was_running = (OPS / "enabled").exists()

    if first and not was_running:
        poller_status = subprocess.run(
            ["systemctl", "is-active", "--quiet", "hermes-lab-poller.timer"]
        ).returncode
        was_running = poller_status == 0

    prepare_directories()
    prepare_collector()

    image, tools_id = build_tools_image()

    prepare_networks()
    stop_services()

    old_runtime, offline = save_checkpoint(first)

    activate_release(old_runtime, offline, image, tools_id)
    run(
        "/usr/bin/python3.14",
        "-m",
        "investigator.runtime.install_executor",
        "--source",
        str(RELEASE),
        "--config",
        str(ROOT / "investigator.json"),
        "--release-id",
        RELEASE.name,
        cwd=RELEASE,
    )
    install_units(was_running)

    LOGGER.info(
        "Release activated; authentication and results preserved. "
        "Use sudo labctl status."
    )


def main() -> None:
    configure_logging()

    if os.geteuid() != 0:
        raise SystemExit("Run installer with sudo")
    if not (ROOT / "findings").exists():
        directory(ROOT / "findings", 1000)

    with investigation_lock() as acquired:
        if not acquired:
            raise SessionUnavailable("Finish the active investigation before deploying")

        try:
            activate()
        except UnsupportedLedger as error:
            raise SystemExit(
                str(error) + ". Activation stopped; running services were "
                "preserved. See docs/RECOVERY.md for recovery options."
            ) from None


if __name__ == "__main__":
    main()
