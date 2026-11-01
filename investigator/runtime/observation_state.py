import grp
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from observations.configurations.registry import ConfigurationRegistry

from .observation_configurations import CONFIGURATIONS

STORE = Path("/var/lib/hermes-lab-observations/observations.sqlite3")


def snapshot(
    root: Path,
    destination: Path,
    *,
    registry: ConfigurationRegistry = CONFIGURATIONS,
) -> None:
    for filename in registry.yaml_filenames:
        if (root / filename).exists():
            shutil.copy2(root / filename, destination / filename)

    if STORE.exists():
        with (
            closing(
                sqlite3.connect(f"{STORE.as_uri()}?mode=ro", uri=True)
            ) as source,
            closing(
                sqlite3.connect(destination / "observations.sqlite3")
            ) as target,
        ):
            source.backup(target)

        (destination / "observations.sqlite3").chmod(0o600)


def stored_database(source: Path) -> Path | None:
    candidates = (
        source / "observations.sqlite3",
        source.parent.parent / STORE.relative_to("/"),
    )
    return next((path for path in candidates if path.is_file()), None)


def validate(source: Path) -> None:
    database = stored_database(source)

    if database is not None:
        with closing(sqlite3.connect(database)) as connection:
            if (
                connection.execute("PRAGMA integrity_check").fetchone()[0]
                != "ok"
            ):
                raise ValueError(
                    "Restored observation database failed integrity check"
                )


def restore(
    root: Path,
    source: Path,
    *,
    registry: ConfigurationRegistry = CONFIGURATIONS,
) -> None:
    for filename in registry.yaml_filenames:
        if (source / filename).is_file():
            shutil.copy2(source / filename, root / filename)
            os.chown(root / filename, 0, 0)
            (root / filename).chmod(0o644)

    database = stored_database(source)

    if database is not None:
        staged = STORE.parent / ".restore.sqlite3"
        shutil.copy2(database, staged)
        os.chown(staged, 1000, grp.getgrnam("hermes-lab-executor").gr_gid)
        staged.chmod(0o640)
        staged.replace(STORE)
    else:
        STORE.unlink(missing_ok=True)
