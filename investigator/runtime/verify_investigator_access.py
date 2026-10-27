import os
import re
import json
import argparse
import subprocess
import tempfile
import time

from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from investigator.cli_output import write_json

from .iam_policy_checks import require, verify_policy

if TYPE_CHECKING:
    from investigator.runtime.iam_policy_checks import AwsRequest


class AwsFailure(RuntimeError):
    """Expose only an AWS error code, never raw CLI output or credentials."""

    def __init__(self, service: str, action: str, stderr: str) -> None:
        match = re.search(r"An error occurred \(([A-Za-z0-9]+)\)", stderr)
        self.code = match.group(1) if match else "CliFailure"
        super().__init__(f"{service}.{action} failed ({self.code})")


def aws_request(
    config: dict[str, Any],
    service: str,
    action: str,
    *flags: str,
    profile: str | None = None,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    command = [
        "aws",
        service,
        action,
        "--region",
        config["region"],
        "--output",
        "json",
        "--no-cli-pager",
        "--cli-connect-timeout",
        "10",
        "--cli-read-timeout",
        "30",
        *flags,
    ]

    if profile:
        command += ["--profile", profile]

    response = subprocess.run(
        command, env=environment, capture_output=True, text=True, timeout=45
    )

    if response.returncode:
        raise AwsFailure(service, action, response.stderr)

    return json.loads(response.stdout)


def host_environment() -> dict[str, str]:
    """Use EC2 instance credentials rather than a saved operator profile."""
    environment = os.environ.copy()

    for key in (
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_ROLE_ARN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_ROLE_SESSION_NAME",
    ):
        environment.pop(key, None)

    environment["AWS_CONFIG_FILE"] = "/dev/null"
    environment["AWS_SHARED_CREDENTIALS_FILE"] = "/dev/null"
    environment["AWS_EC2_METADATA_DISABLED"] = "false"

    return environment


def verify_identity(
    identity: dict[str, Any], config: dict[str, Any], role_arn: str
) -> None:
    role_name = role_arn.rsplit("/", 1)[-1]
    partition = role_arn.split(":")[1]
    expected = (
        f"arn:{partition}:sts::{config['account_id']}:" f"assumed-role/{role_name}/"
    )

    require(identity["Account"] == config["account_id"], "Wrong AWS account")
    require(identity["Arn"].startswith(expected), "Wrong assumed role")


def denied_read(
    request: AwsRequest, service: str, action: str, *flags: str
) -> dict[str, Any]:
    try:
        request(service, action, *flags)
    except AwsFailure as error:
        require(
            error.code in {"AccessDenied", "AccessDeniedException"},
            f"Expected AccessDenied from {service}.{action}",
        )

        return {
            "check": "live_read_denial",
            "action": f"{service}.{action}",
            "error_code": error.code,
            "passed": True,
        }

    raise ValueError(f"Unexpectedly allowed {service}.{action}")


def verify_reads(request: AwsRequest, config: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    bucket = config["bucket"]

    with tempfile.TemporaryDirectory(prefix="hermes-lab-read-check-") as name:
        for prefix in ("input/", "output/", "runs/"):
            response = request(
                "s3api",
                "list-objects-v2",
                "--bucket",
                bucket,
                "--prefix",
                prefix,
                "--max-keys",
                "1",
                "--no-paginate",
            )

            objects = response.get("Contents", [])

            require(objects, f"No synthetic fixture available under {prefix}")

            key = objects[0]["Key"]
            body = Path(name) / "sample"

            request(
                "s3api",
                "get-object",
                "--bucket",
                bucket,
                "--key",
                key,
                "--range",
                "bytes=0-1023",
                str(body),
            )

            require(0 < body.stat().st_size <= 1024, "Unexpected sample size")

            results.append(
                {
                    "check": "live_s3_list_and_read",
                    "prefix": prefix,
                    "sample_bytes": body.stat().st_size,
                    "passed": True,
                }
            )

    logs = request(
        "logs",
        "filter-log-events",
        "--log-group-name",
        config["log_group"],
        "--start-time",
        str(int(time.time() - 86400) * 1000),
        "--limit",
        "5",
        "--no-paginate",
    )

    results.append(
        {
            "check": "live_cloudwatch_read",
            "events": len(logs["events"]),
            "passed": True,
        }
    )

    results.append(
        denied_read(
            request,
            "s3api",
            "list-objects-v2",
            "--bucket",
            bucket,
            "--max-keys",
            "1",
            "--no-paginate",
        )
    )
    results.append(
        denied_read(
            request,
            "s3api",
            "list-objects-v2",
            "--bucket",
            bucket,
            "--prefix",
            "private/",
            "--max-keys",
            "1",
            "--no-paginate",
        )
    )
    results.append(
        denied_read(
            request,
            "s3api",
            "get-object",
            "--bucket",
            bucket,
            "--key",
            "private/iam-verification-probe",
            "/dev/null",
        )
    )
    results.append(denied_read(request, "s3api", "list-buckets"))

    return results


def verify_live(config: dict[str, Any]) -> list[dict[str, Any]]:
    environment = host_environment()
    host = partial(aws_request, config, environment=environment)

    verify_identity(host("sts", "get-caller-identity"), config, config["host_role_arn"])

    response = host(
        "sts",
        "assume-role",
        "--role-arn",
        config["role_arn"],
        "--role-session-name",
        "hermes-lab-permission-check",
        "--duration-seconds",
        "900",
    )
    credentials = response["Credentials"]
    environment.update(
        {
            "AWS_ACCESS_KEY_ID": credentials["AccessKeyId"],
            "AWS_SECRET_ACCESS_KEY": credentials["SecretAccessKey"],
            "AWS_SESSION_TOKEN": credentials["SessionToken"],
            "AWS_EC2_METADATA_DISABLED": "true",
        }
    )
    request = partial(aws_request, config, environment=environment)

    verify_identity(request("sts", "get-caller-identity"), config, config["role_arn"])
    return [
        {"check": "host_assume_investigator", "passed": True},
        *verify_reads(request, config),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument("mode", choices=["policy", "live"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--profile")

    args = parser.parse_args()

    if args.mode == "policy" and not args.profile:
        parser.error("policy verification requires --profile")
    if args.mode == "live" and args.profile:
        parser.error("live verification uses the EC2 role, not a profile")

    config = json.loads(args.config.read_text())

    try:
        if args.mode == "policy":
            request = partial(aws_request, config, profile=args.profile)
            identity = request("sts", "get-caller-identity")

            require(identity["Account"] == config["account_id"], "Wrong account")

            results = verify_policy(request, config)
        else:
            results = verify_live(config)
    except (AwsFailure, ValueError) as error:
        write_json({"passed": False, "error": str(error)})
        raise SystemExit(1) from None

    write_json({"mode": args.mode, "passed": True, "checks": results}, indent=2)


if __name__ == "__main__":
    main()
