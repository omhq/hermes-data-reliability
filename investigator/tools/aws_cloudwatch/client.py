import hashlib
import json
import os
import re
import socket
import time
from typing import Any

REQUEST_SOCKET = "/run/hermes-lab-aws/requests.sock"
CONTEXT_VARIABLE = "HERMES_LAB_CONTEXT_ID"
MAX_REQUEST_BYTES = 8192
MAX_RESPONSE_BYTES = 270336


class ClientError(RuntimeError):
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
            raise ClientError("invalid_executor_response")

        result[key] = value

    return result


def validate_response(
    response: object, arguments: dict[str, Any]
) -> dict[str, Any]:
    if type(response) is not dict or type(response.get("ok")) is not bool:
        raise ClientError("invalid_executor_response")

    request_id = response.get("request_id")

    if type(request_id) is not str or not re.fullmatch(
        r"[a-f0-9]{32}", request_id
    ):
        raise ClientError("invalid_executor_response")

    if not response["ok"]:
        if (
            set(response) != {"ok", "request_id", "error"}
            or type(response["error"]) is not str
            or not re.fullmatch(r"[a-z_]{1,64}", response["error"])
        ):
            raise ClientError("invalid_executor_response")

        return response

    expected = {
        "ok",
        "request_id",
        "evidence_id",
        "observed_at_ms",
        "source",
        "data",
        "data_bytes",
        "sha256",
        "truncated",
        "untrusted_evidence",
        "references",
    }

    if set(response) != expected:
        raise ClientError("invalid_executor_response")

    references = response["references"]

    if type(references) is not list or len(references) > 20:
        raise ClientError("invalid_executor_response")

    for reference in references:
        if (
            type(reference) is not dict
            or set(reference) != {"reference", "observation"}
            or type(reference["reference"]) is not str
            or not 1 <= len(reference["reference"]) <= 2048
            or type(reference["observation"]) is not str
            or reference["observation"]
            not in {
                "object_sample",
                "object_listed",
                "object_absent",
                "log_event",
                "cloudtrail_event",
            }
        ):
            raise ClientError("invalid_executor_response")

    data = encode(response["data"])
    maximum = arguments.get("max_bytes", 65536)

    if (
        response["evidence_id"] != request_id
        or type(response["observed_at_ms"]) is not int
        or response["observed_at_ms"] <= 0
        or type(response["source"]) is not dict
        or type(response["data"]) is not dict
        or type(response["data_bytes"]) is not int
        or response["data_bytes"] != len(data)
        or type(maximum) is not int
        or not 1 <= maximum <= 262144
        or len(data) > maximum
        or response["sha256"] != hashlib.sha256(data).hexdigest()
        or type(response["truncated"]) is not bool
        or response["untrusted_evidence"] is not True
    ):
        raise ClientError("invalid_executor_response")

    return response


def request(tool: str, arguments: object) -> dict[str, Any]:
    """Forward raw arguments; the host makes every permission decision."""
    token = os.environ.get(CONTEXT_VARIABLE, "")

    if not token:
        raise ClientError("context_required")
    if not re.fullmatch(r"[a-f0-9]{64}", token):
        raise ClientError("invalid_context")
    if type(arguments) is not dict:
        raise ClientError("invalid_arguments")

    try:
        payload = (
            encode(
                {
                    "context_id": token,
                    "request": {"tool": tool, "arguments": arguments},
                }
            )
            + b"\n"
        )
    except ValueError, TypeError, RecursionError:
        raise ClientError("invalid_arguments") from None

    if len(payload) > MAX_REQUEST_BYTES:
        raise ClientError("request_too_large")

    deadline = time.monotonic() + 40

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(REQUEST_SOCKET)
            connection.sendall(payload)

            response = bytearray()

            while b"\n" not in response:
                remaining = deadline - time.monotonic()

                if remaining <= 0:
                    raise ClientError("executor_timeout")

                connection.settimeout(remaining)
                chunk = connection.recv(
                    min(65536, MAX_RESPONSE_BYTES + 1 - len(response))
                )

                if not chunk:
                    raise ClientError("invalid_executor_response")

                response.extend(chunk)

                if len(response) > MAX_RESPONSE_BYTES:
                    raise ClientError("invalid_executor_response")
    except socket.timeout:
        raise ClientError("executor_timeout") from None
    except OSError:
        raise ClientError("executor_unavailable") from None

    line, remainder = response.split(b"\n", 1)

    if remainder.strip():
        raise ClientError("invalid_executor_response")

    try:
        decoded = json.loads(line, object_pairs_hook=unique_fields)
        return validate_response(decoded, arguments)
    except ValueError, TypeError, RecursionError:
        raise ClientError("invalid_executor_response") from None


def invoke(name: str, arguments: object) -> str:
    try:
        return encode(request(name, arguments)).decode("ascii")
    except ClientError as error:
        return encode({"ok": False, "error": error.code}).decode("ascii")
    except Exception:
        return '{"ok":false,"error":"tool_failure"}'
