import json
import math
import os
import selectors
import signal
import subprocess
from typing import Any, NoReturn, Protocol

from collector.execution import CollectionError, Deadline


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}

    for key, value in pairs:
        if key in result:
            raise ValueError("Invalid JSON")

        result[key] = value

    return result


def _reject_constant(value: str) -> NoReturn:
    raise ValueError("Invalid JSON")


def _finite_float(value: str) -> float:
    result = float(value)

    if not math.isfinite(result):
        raise ValueError("Invalid JSON")

    return result


def decode_json(payload: str | bytes | bytearray) -> Any:
    """Parse external JSON without ambiguous fields or raw error payloads."""
    try:
        return json.loads(
            payload,
            object_pairs_hook=_unique_fields,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except ValueError, UnicodeError, RecursionError:
        raise ValueError("Invalid JSON") from None


class CliRunner(Protocol):
    """AWS CLI execution callable for the client and test doubles."""

    def __call__(
        self,
        command: list[str],
        environment: dict[str, str],
        deadline: Deadline,
    ) -> dict[str, Any]: ...


def run_json(
    command: list[str],
    environment: dict[str, str],
    deadline: Deadline,
) -> dict[str, Any]:
    """Run an AWS call with bounded time, output, and safe errors."""
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
                raise CollectionError("aws_failure")

            with selectors.DefaultSelector() as selector:
                selector.register(stdout, selectors.EVENT_READ, "stdout")
                selector.register(stderr, selectors.EVENT_READ, "stderr")

                while selector.get_map() or process.poll() is None:
                    remaining = deadline.remaining()

                    for key, _ in selector.select(min(remaining, 0.05)):
                        chunk = os.read(key.fd, 65536)

                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue

                        buffer = buffers[key.data]

                        if len(buffer) + len(chunk) > caps[key.data]:
                            raise CollectionError(
                                "upstream_response_too_large"
                            )

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
                        raise CollectionError(code, resume=code == "throttled")

                raise CollectionError("aws_failure")

            try:
                response = decode_json(buffers["stdout"])
            except ValueError:
                raise CollectionError("invalid_aws_response") from None

            if type(response) is not dict:
                raise CollectionError("invalid_aws_response")

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
