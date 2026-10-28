import re
import json
import tempfile

from pathlib import Path
from typing import TYPE_CHECKING, Any

from hermes_cloud_tools.packages import ToolPackages

from .process import run_json
from .wire import ExecutionError

if TYPE_CHECKING:
    from investigator.executor.models import ApprovedAction
    from investigator.executor.process import CliRunner, Deadline


CLI_PARAMETERS = {
    "get-caller-identity": {},
    "assume-role": {
        "RoleArn": "--role-arn",
        "RoleSessionName": "--role-session-name",
        "DurationSeconds": "--duration-seconds",
    },
    "get-object": {
        "Bucket": "--bucket",
        "Key": "--key",
        "Range": "--range",
    },
    "list-objects-v2": {
        "Bucket": "--bucket",
        "Prefix": "--prefix",
        "MaxKeys": "--max-keys",
    },
    "filter-log-events": {
        "logGroupName": "--log-group-name",
        "startTime": "--start-time",
        "endTime": "--end-time",
        "filterPattern": "--filter-pattern",
        "limit": "--limit",
    },
    "lookup-events": {
        "LookupAttributes": "--lookup-attributes",
        "StartTime": "--start-time",
        "EndTime": "--end-time",
        "MaxResults": "--max-results",
    },
}


def host_environment(region: str) -> dict[str, str]:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": "/nonexistent",
        "AWS_CONFIG_FILE": "/dev/null",
        "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
        "AWS_EC2_METADATA_DISABLED": "false",
        "AWS_METADATA_SERVICE_TIMEOUT": "2",
        "AWS_METADATA_SERVICE_NUM_ATTEMPTS": "1",
        "AWS_DEFAULT_REGION": region,
        "AWS_MAX_ATTEMPTS": "1",
        "AWS_PAGER": "",
    }


def validate_config(config: dict[str, Any]) -> None:
    try:
        ToolPackages.from_executor_config(config)
    except ValueError:
        raise ExecutionError("invalid_configuration") from None

    for name, pattern in (
        ("account_id", r"[0-9]{12}"),
        ("region", r"[a-z]{2}(?:-[a-z]+)+-[0-9]+"),
        ("bucket", r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]"),
        ("log_group", r"[./A-Za-z0-9_#-]{1,512}"),
    ):
        value = config.get(name)

        if type(value) is not str or not re.fullmatch(pattern, value):
            raise ExecutionError("invalid_configuration")

    for name in ("role_arn", "host_role_arn"):
        pattern = rf"arn:aws:iam::{config['account_id']}:role/[A-Za-z0-9_-]+"

        if not re.fullmatch(pattern, config.get(name, "")):
            raise ExecutionError("invalid_configuration")

    if config["role_arn"] == config["host_role_arn"]:
        raise ExecutionError("invalid_configuration")


class AwsBackend:
    def __init__(self, config: dict[str, Any], runner: CliRunner = run_json) -> None:
        validate_config(config)
        self.config = dict(config)
        self.runner = runner

    def _call(
        self,
        service: str,
        operation: str,
        parameters: dict[str, Any],
        env: dict[str, str],
        deadline: Deadline,
        body: Path | None = None,
        body_limit: int = 0,
    ) -> dict[str, Any]:
        command = [
            "/usr/bin/aws",
            service,
            operation,
            "--region",
            self.config["region"],
            "--output",
            "json",
            "--no-cli-pager",
            "--no-paginate",
            "--cli-connect-timeout",
            "5",
            "--cli-read-timeout",
            "20",
        ]

        names = dict(CLI_PARAMETERS[operation])

        if operation == "filter-log-events" and "nextToken" in parameters:
            names["nextToken"] = "--next-token"

        expected = set(names) | (
            {"unmask"} if operation == "filter-log-events" else set()
        )

        if set(parameters) != expected:
            raise ExecutionError("invalid_execution_plan")

        for name, flag in names.items():
            value = parameters[name]

            if name == "LookupAttributes":
                value = json.dumps(value, separators=(",", ":"))

            command.extend([flag, str(value)])

        if operation == "filter-log-events":
            if parameters["unmask"] is not False:
                raise ExecutionError("invalid_execution_plan")
            command.append("--no-unmask")
        if service == "s3api":
            command.extend(["--expected-bucket-owner", self.config["account_id"]])
        if body:
            command.append(str(body))

        return self.runner(command, env, deadline, body=body, body_limit=body_limit)

    def _identity(self, identity: dict[str, Any], role: str) -> None:
        expected = (
            f"arn:aws:sts::{self.config['account_id']}:"
            f"assumed-role/{role.rsplit('/', 1)[-1]}/"
        )

        if identity.get("Account") != self.config["account_id"] or not identity.get(
            "Arn", ""
        ).startswith(expected):
            raise ExecutionError("unexpected_aws_identity")

    def assume(self, deadline: Deadline) -> dict[str, str]:
        environment = host_environment(self.config["region"])
        identity = self._call("sts", "get-caller-identity", {}, environment, deadline)

        self._identity(identity, self.config["host_role_arn"])

        response = self._call(
            "sts",
            "assume-role",
            {
                "RoleArn": self.config["role_arn"],
                "RoleSessionName": "hermes-lab-executor",
                "DurationSeconds": 900,
            },
            environment,
            deadline,
        )

        credentials = response["Credentials"]

        for field, variable in (
            ("AccessKeyId", "AWS_ACCESS_KEY_ID"),
            ("SecretAccessKey", "AWS_SECRET_ACCESS_KEY"),
            ("SessionToken", "AWS_SESSION_TOKEN"),
        ):
            value = credentials[field]
            if type(value) is not str or not value:
                raise ExecutionError("invalid_credentials")
            environment[variable] = value

        environment["AWS_EC2_METADATA_DISABLED"] = "true"
        identity = self._call("sts", "get-caller-identity", {}, environment, deadline)

        self._identity(identity, self.config["role_arn"])
        return environment

    def execute(
        self,
        action: ApprovedAction,
        environment: dict[str, str],
        deadline: Deadline,
    ) -> tuple[dict[str, Any], bytes | None]:
        operations = {
            ("s3", "GetObject"): ("s3api", "get-object"),
            ("s3", "ListObjectsV2"): ("s3api", "list-objects-v2"),
            ("logs", "FilterLogEvents"): ("logs", "filter-log-events"),
            ("cloudtrail", "LookupEvents"): ("cloudtrail", "lookup-events"),
        }
        service, operation = operations[(action.service, action.operation)]

        if action.operation != "GetObject":
            return (
                self._call(
                    service,
                    operation,
                    dict(action.parameters),
                    environment,
                    deadline,
                ),
                None,
            )

        with tempfile.TemporaryDirectory(prefix="hermes-lab-read-") as name:
            body = Path(name) / "body"
            metadata = self._call(
                service,
                operation,
                dict(action.parameters),
                environment,
                deadline,
                body=body,
                body_limit=action.max_bytes,
            )

            with body.open("rb") as source:
                sample = source.read(action.max_bytes + 1)

            if len(sample) > action.max_bytes:
                raise ExecutionError("upstream_response_too_large")

            return metadata, sample
