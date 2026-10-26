"""Publish explicit evaluation fixtures into approved lab resources."""

import argparse
import json
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from investigator.runtime.deploy import terraform_output

from .live_cases import CASES, make_case

if TYPE_CHECKING:
    from investigator.runtime.iam_policy_checks import AwsRequest


def aws_request(
    profile: str, region: str, service: str, operation: str, *arguments: str
) -> dict[str, Any]:
    result = subprocess.run(
        [
            "aws",
            service,
            operation,
            *arguments,
            "--profile",
            profile,
            "--region",
            region,
            "--output",
            "json",
            "--no-cli-pager",
            "--cli-connect-timeout",
            "5",
            "--cli-read-timeout",
            "20",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode:
        raise RuntimeError(f"Evaluation AWS operation failed: {service}.{operation}")

    return json.loads(result.stdout) if result.stdout.strip() else {}


def publish(
    config: dict[str, Any], request: AwsRequest, expected_account: str
) -> dict[str, Any]:
    """Check identity before writing to the lab's configured resources."""
    if request("sts", "get-caller-identity")["Account"] != expected_account:
        raise RuntimeError("Unexpected AWS account; no fixtures published")

    mappings = request(
        "lambda",
        "list-event-source-mappings",
        "--function-name",
        config["function"],
    )["EventSourceMappings"]

    if any(mapping["State"] != "Disabled" for mapping in mappings):
        raise RuntimeError("Disable SQS consumption before publishing direct fixtures")

    suite_id = uuid.uuid4().hex[:12]
    cases = []

    for name in CASES:
        fixture = make_case(name, f"hermes-lab-evaluation-{suite_id}-{name}")
        stream = f"evaluation/{suite_id}/{name}"

        request(
            "logs",
            "create-log-stream",
            "--log-group-name",
            config["log_group"],
            "--log-stream-name",
            stream,
        )

        with tempfile.TemporaryDirectory() as directory:
            body = Path(directory) / "body.json"

            for key, payload in fixture["objects"].items():
                body.write_text(json.dumps(payload, sort_keys=True))
                body.chmod(0o600)
                request(
                    "s3api",
                    "put-object",
                    "--bucket",
                    config["bucket"],
                    "--key",
                    key,
                    "--body",
                    str(body),
                    "--content-type",
                    "application/json",
                )

        request(
            "logs",
            "put-log-events",
            "--log-group-name",
            config["log_group"],
            "--log-stream-name",
            stream,
            "--log-events",
            json.dumps(
                [
                    {
                        "timestamp": int(time.time() * 1000),
                        "message": json.dumps(fixture["incident"], sort_keys=True),
                    }
                ]
            ),
        )

        cases.append(
            {
                "case": name,
                "run_id": fixture["incident"]["run_id"],
                "attempt_id": fixture["incident"]["attempt_id"],
            }
        )

    return {"suite_id": suite_id, "cases": cases}


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument("--profile", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument(
        "--output", type=Path, default=Path(".local/evaluation-cases.json")
    )

    args = parser.parse_args()

    if not re.fullmatch(r"[0-9]{12}", args.account):
        parser.error("Use a twelve-digit account ID")

    config = json.loads(terraform_output("pipeline"))

    def request(service: str, operation: str, *arguments: str) -> dict[str, Any]:
        return aws_request(
            args.profile, config["region"], service, operation, *arguments
        )

    manifest = publish(config, request, args.account)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n")
    args.output.chmod(0o600)

    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
