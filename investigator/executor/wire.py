import json
import socket

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
        return json.loads(payload, object_pairs_hook=unique_fields)
    except ValueError, UnicodeError, RecursionError:
        raise ExecutionError("invalid_json") from None


def receive(connection: socket.socket, limit: int = MAX_REQUEST_BYTES) -> Any:
    connection.settimeout(5)
    payload = bytearray()

    while b"\n" not in payload:
        chunk = connection.recv(min(4096, limit + 1 - len(payload)))

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
        connection.settimeout(40)
        connection.connect(str(path))
        connection.sendall(payload)

        response = bytearray()

        while b"\n" not in response:
            chunk = connection.recv(65536)

            if not chunk or len(response) + len(chunk) > 270_336:
                raise ExecutionError("invalid_response")

            response.extend(chunk)

    return decode(response)
