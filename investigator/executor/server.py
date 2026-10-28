import argparse
import json
import logging
import os
import socket
import socketserver
import struct
import threading
import uuid
from pathlib import Path
from typing import Any, cast

from investigator.logging_config import configure_logging

from .audit import AuditLog
from .aws import AwsBackend
from .executor import Executor
from .models import PolicyDenied
from .policy import fields, require, wall_time_ms
from .wire import ExecutionError, encode, receive

LOGGER = logging.getLogger(__name__)


class Handler(socketserver.BaseRequestHandler):
    request: socket.socket
    owner_uid: int = 0

    @property
    def executor_server(self) -> Server:
        """Server registers this handler and socketserver supplies it here."""
        return cast(Server, self.server)

    def handle(self) -> None:
        server = self.executor_server
        executor = server.executor

        try:
            if server.control:
                peer = self.request.getsockopt(
                    socket.SOL_SOCKET, socket.SO_PEERCRED, 12
                )
                _, uid, _ = struct.unpack("3i", peer)

                require(
                    uid == 0 or (server.collector and uid == 1000),
                    "control_forbidden",
                )

                self.owner_uid = uid if server.collector else 0

            request = receive(self.request)

            if not server.control:
                response = executor.execute(request)
            else:
                response = {"ok": True, **self.control(request)}
        except Exception as error:
            code = (
                error.code
                if isinstance(error, (PolicyDenied, ExecutionError))
                else "invalid_request"
            )
            response = {
                "ok": False,
                "error": code,
                "request_id": uuid.uuid4().hex,
            }

            try:
                executor.audit.write(
                    {
                        "request_id": response["request_id"],
                        "time_ms": wall_time_ms(),
                        "status": "denied",
                        "code": code,
                    }
                )
            except Exception:
                response["error"] = "audit_unavailable"

        try:
            self.request.sendall(encode(response) + b"\n")
        except OSError:
            pass

    def control(self, request: object) -> dict[str, Any]:
        if type(request) is not dict:
            raise PolicyDenied("invalid_arguments")

        operation = request.get("operation")
        server = self.executor_server
        executor = server.executor
        owner = self.owner_uid if server.collector else None

        if operation == "open":
            fields(request, {"operation", "context"}, set())

            if server.collector:
                context = request["context"]
                required = (
                    {"observation_id", "incident_time_ms"}
                    if type(context) is dict and "observation_id" in context
                    else {"run_id", "attempt_ids", "incident_time_ms"}
                )

                fields(context, required, {"ttl_seconds", "expected_source"})

            return executor.register(request["context"], self.owner_uid)
        if operation == "close":
            fields(request, {"operation", "context_id"}, set())
            return executor.revoke(request["context_id"], owner_uid=owner)
        if operation == "check":
            fields(request, {"operation", "context_id"}, set())
            return executor.status(request["context_id"], owner_uid=owner)
        if operation == "evidence":
            fields(request, {"operation", "context_id", "citations"}, set())
            return executor.evidence(
                request["context_id"], request["citations"], owner_uid=owner
            )
        if operation == "status" and not server.collector:
            fields(request, {"operation"}, set())
            return {
                "active_contexts": sum(
                    not executor._expired(session)
                    for session in executor.sessions.values()
                )
            }

        raise PolicyDenied("unknown_control_operation")


class Server(socketserver.UnixStreamServer):
    request_queue_size = 8

    def __init__(
        self,
        path: Path,
        executor: Executor,
        control: bool = False,
        shared: bool = False,
        collector: bool = False,
    ) -> None:
        self.executor = executor
        self.control = control
        self.collector = collector

        super().__init__(str(path), Handler)
        if shared or collector:
            os.chown(path, -1, 1000 if collector else 10000)

        os.chmod(path, 0o660 if shared or collector else 0o600)


def main() -> None:
    configure_logging()

    parser = argparse.ArgumentParser()

    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--socket-dir", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument(
        "--collector-dir",
        type=Path,
        default=Path("/run/hermes-lab-collector"),
    )

    args = parser.parse_args()

    os.umask(0o077)

    config = json.loads(args.config.read_text())
    backend = AwsBackend(config)
    audit = AuditLog(args.state_dir / "audit.jsonl")
    executor = Executor(config, backend, audit)

    # Both directories are systemd-owned, outside agent-writable storage.
    requests_dir = args.socket_dir / "requests"

    requests_dir.mkdir(exist_ok=True)
    os.chown(requests_dir, -1, 10000)
    requests_dir.chmod(0o750)
    args.collector_dir.mkdir(exist_ok=True)
    os.chown(args.collector_dir, -1, 1000)
    args.collector_dir.chmod(0o750)

    collector_path = args.collector_dir / "control.sock"
    collector_path.unlink(missing_ok=True)

    for name in ("control.sock", "requests.sock", "requests/requests.sock"):
        (args.socket_dir / name).unlink(missing_ok=True)

    # Host verifiers retain their original path. Only requests/ is mounted.
    (args.socket_dir / "requests.sock").symlink_to("requests/requests.sock")

    with Server(args.socket_dir / "control.sock", executor, True) as control:
        with Server(
            collector_path, executor, True, collector=True
        ) as collector:
            with Server(
                requests_dir / "requests.sock", executor, shared=True
            ) as requests:
                for server in (control, collector):
                    threading.Thread(
                        target=server.serve_forever, daemon=True
                    ).start()

                LOGGER.info(
                    "Executor ready; scoped read sockets are listening."
                )
                requests.serve_forever()


if __name__ == "__main__":
    main()
