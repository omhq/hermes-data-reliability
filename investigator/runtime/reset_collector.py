import argparse
import fcntl
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Generator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from collector.configure import configure
from collector.definition import duration
from collector.matching import MatchError
from collector.observation_store import source_key
from incidents.database import connect, validate_schema
from investigator.logging_config import configure_logging
from investigator.runtime.reports import export

from . import control
from .locking import investigation_lock
from .notification_store import LOCK_NAME, PRESERVED_FILES
from .sessions import SessionUnavailable

ROOT = Path("/srv/hermes-lab")
APPROVED = Path("/etc/hermes-lab/executor.json")
LOCK_FILES = {".poll.lock", ".investigation.lock", LOCK_NAME}
LOGGER = logging.getLogger(__name__)


def start_time(since: str, now_ms: int) -> int:
    """Resolve a bounded lookback once for every configured source."""
    seconds = 0 if since == "now" else duration(since, 1, 86400)
    return max(0, now_ms - seconds * 1000)


def attempt_history(database: Path) -> list[tuple[str, int]]:
    """Read attempt history and reject inconsistent accounting."""
    if not database.exists():
        return []

    with closing(
        sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    ) as ledger:
        validate_schema(ledger)

        if ledger.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Collector ledger integrity check failed")

        uncovered = ledger.execute("""
            SELECT COUNT(*) FROM incidents
            WHERE attempts > (
                SELECT COUNT(*) FROM attempts
                WHERE incident_id = incidents.id
            )
            """).fetchone()[0]

        if uncovered:
            raise ValueError("Incident counters exceed saved attempt history")

        return ledger.execute(
            "SELECT incident_id, started_at FROM attempts ORDER BY rowid"
        ).fetchall()


def prepare_findings(
    destination: Path,
    attempts: list[tuple[str, int]],
    definition: dict[str, Any],
    account_id: str,
    since_ms: int,
) -> None:
    """Seed fresh source positions while retaining all spent attempts."""
    destination.mkdir(mode=0o700)

    with closing(connect(destination / "incidents.sqlite3")) as ledger:
        with ledger:
            ledger.executemany("INSERT INTO attempts VALUES (?, ?)", attempts)

            for source_id, source in definition["sources"].items():
                key = source_key(source_id, source, account_id)

                ledger.execute(
                    """
                    INSERT INTO source_progress
                        (source_key, source_id, cursor_ms)
                    VALUES (?, ?, ?)
                    """,
                    (key, source_id, since_ms),
                )
                ledger.execute(
                    "INSERT INTO collection_starts VALUES (?, ?)",
                    (key, since_ms),
                )

        export(ledger, destination)

    for path in (destination, *destination.rglob("*")):
        os.chown(path, 1000, 1000)
        path.chmod(0o700 if path.is_dir() else 0o600)


@contextmanager
def poll_lock(findings: Path) -> Generator[None]:
    """Keep the existing lock inode so another reader cannot bypass it."""
    with (findings / ".poll.lock").open("a+") as stream:
        os.fchown(stream.fileno(), 1000, 1000)
        os.fchmod(stream.fileno(), 0o600)

        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SessionUnavailable(
                "A collector poll is still active"
            ) from None

        yield


def replace_findings(
    root: Path, stage: Path, since_ms: int, was_running: bool
) -> Path:
    """Archive findings and roll back failed replacements."""
    findings = root / "findings"
    archive = root / "checkpoints" / ("collector-reset-" + str(time.time_ns()))

    archive.mkdir(parents=True, mode=0o700)

    saved = archive / "findings"

    saved.mkdir(mode=0o700)

    for source in (
        root / "observations.yaml",
        root / "ops/investigator.runtime.json",
    ):
        if source.exists():
            shutil.copy2(source, archive / source.name)

    (archive / "reset.json").write_text(
        json.dumps(
            {
                "start_ms": since_ms,
                "previous_mode": "running" if was_running else "paused",
            }
        )
    )

    moved = []
    installed = []

    try:
        for path in findings.iterdir():
            if path.name not in LOCK_FILES | PRESERVED_FILES:
                path.rename(saved / path.name)
                moved.append(path.name)

        for path in (stage / "findings").iterdir():
            path.rename(findings / path.name)
            installed.append(path.name)

        (stage / "observations.yaml").replace(root / "observations.yaml")
    except BaseException:
        for name in installed:
            (findings / name).rename(stage / "findings" / name)

        for name in moved:
            (saved / name).rename(findings / name)

        raise

    return archive


def reset_collector(
    since: str = "now", observations: Path | None = None
) -> Path:
    """Validate before pausing and retain authentication and running intent."""
    since_ms = start_time(since, int(time.time() * 1000))
    approved = json.loads(APPROVED.read_text())

    with tempfile.TemporaryDirectory(
        prefix=".collector-reset-", dir=ROOT
    ) as temporary:
        stage = Path(temporary)
        definition = configure(
            approved,
            stage / "observations.yaml",
            ROOT / "observations.yaml",
            source=observations or ROOT / "observations.yaml",
        )
        findings = ROOT / "findings"

        with investigation_lock(findings / ".investigation.lock") as acquired:
            if not acquired:
                raise SessionUnavailable(
                    "An investigation is active; finish it or pause the lab "
                    "before resetting the collector"
                )

            attempts = attempt_history(findings / "incidents.sqlite3")

            prepare_findings(
                stage / "findings",
                attempts,
                definition,
                approved["account_id"],
                since_ms,
            )

            was_running = (ROOT / "ops/enabled").exists()

            try:
                control.pause()

                with investigation_lock(
                    findings / LOCK_NAME
                ) as notification_lock:
                    if not notification_lock:
                        raise SessionUnavailable(
                            "A report delivery is still active"
                        )

                    with poll_lock(findings):
                        archive = replace_findings(
                            ROOT, stage, since_ms, was_running
                        )
            finally:
                if was_running:
                    control.start_services()

    LOGGER.info(
        "Collector reset; previous findings and queue archived at %s. "
        "Attempt budgets retained; %s",
        archive,
        "runtime resumed." if was_running else "runtime remains paused.",
    )

    return archive


def main() -> None:
    configure_logging()

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--since", default="now", help="now or a lookback up to 24h"
    )
    parser.add_argument("--observations", type=Path)

    args = parser.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("Use sudo labctl reset-collector")

    try:
        reset_collector(args.since, args.observations)
    except (
        ValueError,
        MatchError,
        SessionUnavailable,
        OSError,
        sqlite3.Error,
        subprocess.CalledProcessError,
    ) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
