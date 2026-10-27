import os
import re
import fcntl

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Self

from investigator.executor.wire import call

CONTROL_SOCKET = Path("/run/hermes-lab-executor/control.sock")
COLLECTOR_SOCKET = Path("/run/hermes-lab-collector/control.sock")
LOCK_PATH = Path("/srv/hermes-lab/findings/.investigation.lock")


class SessionUnavailable(RuntimeError):
    pass


@contextmanager
def investigation_lock(path: str | Path = LOCK_PATH) -> Iterator[bool]:
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


@dataclass
class InvestigationSession:
    """Accept a trusted scope specification, never model-provided context."""

    specification: dict[str, Any]
    request: Callable[[str | Path, dict[str, Any]], dict[str, Any]] = field(
        default=call, repr=False
    )
    control_socket: Path = field(default=CONTROL_SOCKET, repr=False)
    token: str = field(default="", init=False, repr=False)
    session_id: str = field(default="", init=False)
    expires_at_ms: int = field(default=0, init=False)

    def __enter__(self) -> Self:
        try:
            opened = self.request(
                self.control_socket,
                {"operation": "open", "context": self.specification},
            )
        except Exception:
            raise SessionUnavailable("Executor connection unavailable") from None

        if not opened.get("ok"):
            raise SessionUnavailable("The executor could not open this scope")
        if not (
            re.fullmatch(r"[a-f0-9]{64}", opened.get("context_id", ""))
            and re.fullmatch(r"[a-f0-9]{32}", opened.get("session_id", ""))
            and type(opened.get("expires_at_ms")) is int
        ):
            raise SessionUnavailable("Executor returned an invalid session")

        self.token = opened["context_id"]
        self.session_id = opened["session_id"]
        self.expires_at_ms = opened["expires_at_ms"]

        return self

    def check(self) -> None:
        try:
            result = self.request(
                self.control_socket,
                {"operation": "check", "context_id": self.token},
            )
        except Exception:
            raise SessionUnavailable("Executor connection lost") from None

        if not result.get("ok") or result.get("active") is not True:
            raise SessionUnavailable("Investigation access ended")

    def evidence(self, citations: list[dict[str, str]]) -> dict[str, Any]:
        """Read provenance directly from the executor, never model output."""
        try:
            result = self.request(
                self.control_socket,
                {
                    "operation": "evidence",
                    "context_id": self.token,
                    "citations": citations,
                },
            )
        except Exception:
            raise SessionUnavailable("Evidence verification unavailable") from None
        if (
            not result.get("ok")
            or type(result.get("receipts")) is not list
            or type(result.get("limitations")) is not list
        ):
            raise SessionUnavailable("Evidence citations could not be verified")
        return result

    def __exit__(self, *_: object) -> None:
        try:
            self.request(
                self.control_socket,
                {"operation": "close", "context_id": self.token},
            )
        except Exception:
            # Lost executors invalidate tokens; never recreate a context here.
            pass
        finally:
            self.token = ""
