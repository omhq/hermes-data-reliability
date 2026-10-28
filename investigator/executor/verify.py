import argparse
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from investigator.cli_output import write_json

from .wire import call, encode

SOCKETS = Path("/run/hermes-lab-executor")


def check(condition: object, name: str) -> None:
    if not condition:
        raise RuntimeError(name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart-check", action="store_true")
    args = parser.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("Run with sudo on EC2")

    checks = []

    with sqlite3.connect(
        "file:/srv/hermes-lab/findings/incidents.sqlite3?mode=ro", uri=True
    ) as database:
        row = database.execute(
            "SELECT evidence FROM incidents ORDER BY detected_at DESC LIMIT 1"
        ).fetchone()

    check(row is not None, "synthetic_fixture_available")

    incident = json.loads(row[0])
    context = {
        "run_id": incident["run_id"],
        "attempt_ids": [incident["attempt_id"]],
        "incident_time_ms": int(
            datetime.fromisoformat(
                incident["timestamp"].replace("Z", "+00:00")
            ).timestamp()
            * 1000
        ),
    }

    opened = call(
        SOCKETS / "control.sock", {"operation": "open", "context": context}
    )

    check(opened.get("ok"), "context_open")

    token = opened["context_id"]
    request_ids = []

    def passed(name: str) -> None:
        checks.append({"check": name, "passed": True})

    def request(
        tool: str = "read_run_object", **arguments: Any
    ) -> dict[str, Any]:
        response = call(
            SOCKETS / "requests.sock",
            {
                "context_id": token,
                "request": {
                    "tool": tool,
                    "arguments": {"run_id": incident["run_id"], **arguments},
                },
            },
        )
        request_ids.append(response["request_id"])

        return response

    try:
        for kind, arguments in (
            ("input", {}),
            ("log", {"attempt_id": incident["attempt_id"]}),
        ):
            response = request(kind=kind, max_bytes=4096, **arguments)
            check(
                response.get("ok") and response["data"]["text"],
                "s3_read_" + kind,
            )
            passed("s3_read_" + kind)

        response = request(kind="input", max_bytes=64)
        check(
            response.get("ok")
            and response["truncated"]
            and len(encode(response["data"])) <= 64,
            "bounded_object_sample",
        )
        passed("bounded_object_sample")

        for kind in ("input", "output", "runs"):
            response = request(
                "list_run_objects", kind=kind, max_keys=2, max_bytes=4096
            )

            check(response.get("ok"), "s3_list_" + kind)
            passed("s3_list_" + kind)

        response = request("search_run_logs", max_events=2, max_bytes=4096)

        check(
            response.get("ok") and response["data"]["events"],
            "cloudwatch_read",
        )
        passed("cloudwatch_read")

        for tool, arguments, code in (
            ("DeleteObject", {"kind": "input"}, "unknown_tool"),
            (
                "read_run_object",
                {"kind": "input", "bucket": "other"},
                "unexpected_arguments",
            ),
            (
                "read_run_object",
                {"kind": "input", "run_id": "0" * 24},
                "wrong_incident",
            ),
        ):
            response = request(tool, **arguments)

            check(response.get("error") == code, "deny_" + code)
            passed("deny_" + code)

        for _ in range(3):
            check(
                request("list_run_objects", kind="input", max_bytes=256).get(
                    "ok"
                ),
                "consume_call_budget",
            )

        check(
            request(kind="input").get("error") == "call_budget_exhausted",
            "call_budget",
        )
        passed("call_budget")

        records = [
            json.loads(line)
            for line in Path("/var/lib/hermes-lab-executor/audit.jsonl")
            .read_text()
            .splitlines()
        ]

        for request_id in request_ids:
            matching = [
                record
                for record in records
                if record.get("request_id") == request_id
            ]

            check(bool(matching), "audit_receipt")

            if any(record["status"] == "denied" for record in matching):
                check(
                    all(record["status"] == "denied" for record in matching),
                    "denied_without_execution",
                )

        check(token not in json.dumps(records), "tokens_not_audited")
        passed("audit_receipts_without_context_tokens_or_bodies")
    finally:
        call(
            SOCKETS / "control.sock",
            {"operation": "close", "context_id": token},
        )

    check(
        request(kind="input").get("error") == "unknown_context",
        "revoked_context",
    )
    passed("revoked_context")

    if args.restart_check:
        opened = call(
            SOCKETS / "control.sock", {"operation": "open", "context": context}
        )
        token = opened["context_id"]

        subprocess.run(
            ["systemctl", "restart", "hermes-lab-executor.service"], check=True
        )

        for _ in range(50):
            try:
                response = request(kind="input")
            except OSError:
                time.sleep(0.1)
                continue

            check(
                response.get("error") == "unknown_context",
                "restart_revokes_contexts",
            )
            break
        else:
            raise RuntimeError("executor_unavailable_after_restart")

        passed("restart_revokes_contexts")

    write_json({"passed": True, "checks": checks}, indent=2)


if __name__ == "__main__":
    main()
