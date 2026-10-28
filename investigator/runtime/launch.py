import os
import selectors
import signal
import subprocess
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from types import FrameType
from typing import TYPE_CHECKING, Any

from .sessions import SessionUnavailable

if TYPE_CHECKING:
    from investigator.runtime.sessions import InvestigationSession


WORKER = "/opt/hermes-lab/chat_worker.py"
PYTHON = "/opt/hermes/.venv/bin/python"
MAX_OUTPUT_BYTES = 4 * 1024 * 1024


@contextmanager
def stop_on_disconnect() -> Generator[None]:
    """Unwind the worker and authorization when SSH or the host stops us."""
    signals = (signal.SIGHUP, signal.SIGTERM)
    previous = {number: signal.getsignal(number) for number in signals}

    def interrupt(_number: int, _frame: FrameType | None) -> None:
        # Repeated disconnect signals must not interrupt the cleanup itself.
        for number in signals:
            signal.signal(number, signal.SIG_IGN)

        raise SessionUnavailable("Chat connection closed")

    for number in signals:
        signal.signal(number, interrupt)

    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def worker_environment(session: InvestigationSession) -> dict[str, str]:
    return {
        **os.environ,
        "HERMES_LAB_CONTEXT_ID": session.token,
        "HERMES_LAB_SESSION_ID": session.session_id,
    }


def worker_command(
    session: InvestigationSession,
    seconds: float,
    query: str | None = None,
    stream_json: bool = False,
    incident: bool = False,
) -> list[str]:
    command = ["docker", "exec", "-i"]

    if query is None and not incident:
        command += ["-t"]

    command += [
        "--user",
        "hermes",
        "--env",
        "HERMES_LAB_CONTEXT_ID",
        "--env",
        "HERMES_LAB_SESSION_ID",
        "hermes-lab-runtime",
        PYTHON,
        WORKER,
        "launch",
        "--session-id",
        session.session_id,
        "--seconds",
        str(seconds),
    ]

    if incident:
        command += ["--incident"]
    elif query is not None:
        command += ["--query", query]
    if stream_json:
        command += ["--stream-json"]

    return command


def finish_worker(
    process: subprocess.Popen[Any], session: InvestigationSession
) -> None:
    """A disconnected docker exec client does not terminate its worker."""
    try:
        subprocess.run(
            [
                "docker",
                "exec",
                "--user",
                "hermes",
                "hermes-lab-runtime",
                PYTHON,
                WORKER,
                "stop",
                "--session-id",
                session.session_id,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    finally:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def run_once(
    session: InvestigationSession, prompt: str, seconds: float = 240
) -> str:
    """Keep the same session until the model and citation checks finish."""
    payload = prompt.encode("utf-8")

    if len(payload) > 32768:
        raise RuntimeError("Investigation prompt is too large")

    # Anonymous, private input avoids putting incident data in argv or logs.
    with tempfile.TemporaryFile() as source:
        source.write(payload)
        source.seek(0)

        process = subprocess.Popen(
            worker_command(session, seconds, incident=True),
            env=worker_environment(session),
            stdin=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        try:
            return collect_output(process, session, seconds)
        finally:
            try:
                finish_worker(process, session)
            finally:
                if process.stdout is not None:
                    process.stdout.close()


def collect_output(
    process: subprocess.Popen[bytes],
    session: InvestigationSession,
    seconds: float,
) -> str:
    stream = process.stdout

    if stream is None:
        raise RuntimeError("Investigation output pipe is unavailable")

    output = bytearray()
    deadline = time.monotonic() + seconds
    ended = False

    with selectors.DefaultSelector() as selector:
        selector.register(stream, selectors.EVENT_READ)

        while process.poll() is None or not ended:
            session.check()

            if time.monotonic() >= deadline:
                raise SessionUnavailable("Investigation time limit reached")

            for key, _ in selector.select(timeout=0.5):
                chunk = os.read(key.fd, 65536)

                if not chunk:
                    selector.unregister(stream)
                    ended = True
                else:
                    if len(output) + len(chunk) > MAX_OUTPUT_BYTES:
                        raise RuntimeError(
                            "Investigation output limit exceeded"
                        )

                    output.extend(chunk)

    session.check()

    if process.returncode:
        raise RuntimeError(f"Hermes exited with status {process.returncode}")

    return output.decode("utf-8")
