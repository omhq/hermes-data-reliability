import copy
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tool_boundary.loader import load_tools
from tool_boundary.models import PolicyDenied
from tool_boundary.process import run_json
from tool_boundary.services.base import CliRequest
from tool_boundary.wire import ExecutionError

from .aws_identity import AwsIdentity

if TYPE_CHECKING:
    from tool_boundary.models import ApprovedAction
    from tool_boundary.process import CliRunner, Deadline


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


class AwsBackend:
    def __init__(
        self, config: dict[str, Any], runner: CliRunner = run_json
    ) -> None:
        AwsIdentity.validate_configuration(config)
        self.config = copy.deepcopy(config)
        self.providers = load_tools(self.config)
        self.runner = runner

    def _call(
        self,
        request: CliRequest,
        environment: dict[str, str],
        deadline: Deadline,
        *,
        body: Path | None = None,
        body_limit: int = 0,
    ) -> dict[str, Any]:
        command = [
            "/usr/bin/aws",
            request.service,
            request.operation,
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
            *request.arguments,
        ]

        if body is not None:
            command.append(str(body))

        return self.runner(
            command, environment, deadline, body=body, body_limit=body_limit
        )

    def _identity(self, identity: dict[str, Any], role: str) -> None:
        expected = (
            f"arn:aws:sts::{self.config['account_id']}:"
            f"assumed-role/{role.rsplit('/', 1)[-1]}/"
        )

        arn = identity.get("Arn")

        if (
            identity.get("Account") != self.config["account_id"]
            or type(arn) is not str
            or not arn.startswith(expected)
        ):
            raise ExecutionError("unexpected_aws_identity")

    def assume(self, deadline: Deadline) -> dict[str, str]:
        environment = host_environment(self.config["region"])
        identity = self._call(
            CliRequest("sts", "get-caller-identity", ()), environment, deadline
        )

        self._identity(identity, self.config["host_role_arn"])

        response = self._call(
            CliRequest(
                "sts",
                "assume-role",
                (
                    "--role-arn",
                    self.config["role_arn"],
                    "--role-session-name",
                    "hermes-lab-executor",
                    "--duration-seconds",
                    "900",
                ),
            ),
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
        identity = self._call(
            CliRequest("sts", "get-caller-identity", ()), environment, deadline
        )

        self._identity(identity, self.config["role_arn"])
        return environment

    def execute(
        self,
        action: ApprovedAction,
        environment: dict[str, str],
        deadline: Deadline,
    ) -> tuple[dict[str, Any], bytes | None]:
        if (
            action.account_id != self.config["account_id"]
            or action.region != self.config["region"]
            or action.max_pages != 1
        ):
            raise ExecutionError("invalid_execution_plan")

        try:
            request = self.providers.service(action.service).request(action)
        except PolicyDenied:
            raise ExecutionError("invalid_execution_plan") from None

        if not request.body:
            return self._call(request, environment, deadline), None

        with tempfile.TemporaryDirectory(
            prefix="hermes-lab-read-"
        ) as directory:
            body = Path(directory) / "body"
            metadata = self._call(
                request,
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
