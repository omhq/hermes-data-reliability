import os
import selectors
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from .wire import ExecutionError, decode


class Deadline:
    def __init__(
        self, seconds: float, expires_at_ms: int, clock: Callable[[], int]
    ) -> None:
        self.end = time.monotonic() + seconds
        self.expires_at_ms = expires_at_ms
        self.clock = clock

    def remaining(self) -> float:
        remaining = min(
            self.end - time.monotonic(),
            (self.expires_at_ms - self.clock()) / 1000,
        )

        if remaining <= 0:
            raise ExecutionError("execution_timeout")

        return remaining


class CliRunner(Protocol):
    """AWS CLI execution callable shared by the backend and test doubles."""

    def __call__(
        self,
        command: list[str],
        environment: dict[str, str],
        deadline: Deadline,
        *,
        body: Path | None = None,
        body_limit: int = 0,
    ) -> dict[str, Any]: ...


def run_json(
    command: list[str],
    environment: dict[str, str],
    deadline: Deadline,
    body: Path | None = None,
    body_limit: int = 0,
) -> dict[str, Any]:
    """Kill the process group on timeout, oversized output, or file growth."""
    deadline.remaining()

    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    caps = {"stdout": 2 * 1024 * 1024, "stderr": 16 * 1024}

    with subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        shell=False,
        start_new_session=True,
    ) as process:
        failed = True

        try:
            stdout, stderr = process.stdout, process.stderr

            if stdout is None or stderr is None:
                raise ExecutionError("aws_failure")

            with selectors.DefaultSelector() as selector:
                selector.register(stdout, selectors.EVENT_READ, "stdout")
                selector.register(stderr, selectors.EVENT_READ, "stderr")

                while selector.get_map() or process.poll() is None:
                    remaining = deadline.remaining()

                    if (
                        body
                        and body.exists()
                        and body.stat().st_size > body_limit
                    ):
                        raise ExecutionError("upstream_response_too_large")

                    for key, _ in selector.select(min(remaining, 0.05)):
                        chunk = os.read(key.fd, 65536)

                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue

                        buffer = buffers[key.data]

                        if len(buffer) + len(chunk) > caps[key.data]:
                            raise ExecutionError("upstream_response_too_large")

                        buffer.extend(chunk)

            deadline.remaining()

            if process.returncode:
                # Interpret only known codes. Never return raw stderr.
                stderr = bytes(buffers["stderr"])
                for marker, code in (
                    (b"(AccessDenied", "access_denied"),
                    (b"(NoSuchKey)", "not_found"),
                    (b"(ExpiredToken", "credentials_expired"),
                    (b"(Throttling", "throttled"),
                ):
                    if marker in stderr:
                        raise ExecutionError(code)

                raise ExecutionError("aws_failure")

            response = decode(buffers["stdout"])
            failed = False

            return response
        finally:
            # A child can keep a pipe open after the CLI parent exits.
            if failed or process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

            process.wait()
