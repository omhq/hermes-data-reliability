import json
import math
import socket
import time
from pathlib import Path
from typing import Any

MAX_REQUEST_BYTES = 8192


class ExecutionError(RuntimeError):
    """Expose a fixed error code, never raw CLI output or request text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def encode(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}

    for key, value in pairs:
        if key in result:
            raise ExecutionError("invalid_json")

        result[key] = value

    return result


def decode(payload: str | bytes | bytearray) -> Any:
    try:
        return json.loads(
            payload,
            object_pairs_hook=unique_fields,
            parse_constant=reject_constant,
            parse_float=finite_float,
        )
    except ValueError, UnicodeError, RecursionError:
        raise ExecutionError("invalid_json") from None


def reject_constant(value: str) -> float:
    raise ExecutionError("invalid_json")


def finite_float(value: str) -> float:
    result = float(value)

    if not math.isfinite(result):
        raise ExecutionError("invalid_json")

    return result


def set_timeout(connection: socket.socket, end: float, code: str) -> None:
    remaining = end - time.monotonic()

    if remaining <= 0:
        raise ExecutionError(code)

    connection.settimeout(remaining)


def receive(
    connection: socket.socket,
    limit: int = MAX_REQUEST_BYTES,
    timeout_seconds: float = 5,
) -> Any:
    end = time.monotonic() + timeout_seconds
    payload = bytearray()

    while b"\n" not in payload:
        set_timeout(connection, end, "request_timeout")

        try:
            chunk = connection.recv(min(4096, limit + 1 - len(payload)))
        except TimeoutError:
            raise ExecutionError("request_timeout") from None

        if not chunk:
            raise ExecutionError("incomplete_request")

        payload.extend(chunk)

        if len(payload) > limit:
            raise ExecutionError("request_too_large")

    line, remainder = payload.split(b"\n", 1)

    if remainder.strip():
        raise ExecutionError("invalid_frame")

    return decode(line)


def call(path: str | Path, request: dict[str, Any]) -> dict[str, Any]:
    """Operator/collector client; do not log context tokens or payloads."""
    payload = encode(request) + b"\n"

    if len(payload) > MAX_REQUEST_BYTES:
        raise ExecutionError("request_too_large")

    with socket.socket(socket.AF_UNIX) as connection:
        end = time.monotonic() + 40
        set_timeout(connection, end, "response_timeout")
        connection.connect(str(path))
        set_timeout(connection, end, "response_timeout")
        connection.sendall(payload)

        response = bytearray()

        while b"\n" not in response:
            set_timeout(connection, end, "response_timeout")
            chunk = connection.recv(65536)

            if not chunk or len(response) + len(chunk) > 270_336:
                raise ExecutionError("invalid_response")

            response.extend(chunk)

    line, remainder = response.split(b"\n", 1)

    if remainder.strip():
        raise ExecutionError("invalid_response")

    result = decode(line)

    if type(result) is not dict:
        raise ExecutionError("invalid_response")

    return result
