import fcntl
import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

LOCK_PATH = Path("/srv/hermes-lab/findings/.investigation.lock")


@contextmanager
def investigation_lock(path: str | Path = LOCK_PATH) -> Generator[bool]:
    """A busy chat defers automatic claims without spending an attempt."""
    path = Path(path)

    with path.open("a+") as stream:
        if os.geteuid() == 0:
            os.fchown(stream.fileno(), 1000, 1000)

        os.fchmod(stream.fileno(), 0o600)

        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            try:
                yield True
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
