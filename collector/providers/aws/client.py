from typing import Any

from collector.execution import CollectionError, Deadline

from .identity import AwsIdentity
from .process import CliRunner, run_json


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


class AwsClient:
    """Host collection access, independent of agent tool authorization."""

    def __init__(
        self, identity: AwsIdentity, runner: CliRunner = run_json
    ) -> None:
        self.identity = identity
        self.runner = runner
        self.environment: dict[str, str] | None = None

    def _call(
        self,
        service: str,
        operation: str,
        arguments: list[str],
        environment: dict[str, str],
        deadline: Deadline,
    ) -> dict[str, Any]:
        return self.runner(
            [
                "/usr/bin/aws",
                service,
                operation,
                "--region",
                self.identity.region,
                "--output",
                "json",
                "--no-cli-pager",
                "--no-paginate",
                "--cli-connect-timeout",
                "5",
                "--cli-read-timeout",
                "20",
                *arguments,
            ],
            environment,
            deadline,
        )

    def _identity(self, identity: dict[str, Any], role: str) -> None:
        expected = (
            f"arn:aws:sts::{self.identity.account_id}:"
            f"assumed-role/{role.rsplit('/', 1)[-1]}/"
        )
        arn = identity.get("Arn")

        if (
            identity.get("Account") != self.identity.account_id
            or type(arn) is not str
            or not arn.startswith(expected)
        ):
            raise CollectionError("unexpected_aws_identity")

    def _assume(self, deadline: Deadline) -> dict[str, str]:
        environment = host_environment(self.identity.region)

        self._identity(
            self._call(
                "sts", "get-caller-identity", [], environment, deadline
            ),
            self.identity.host_role_arn,
        )
        response = self._call(
            "sts",
            "assume-role",
            [
                "--role-arn",
                self.identity.role_arn,
                "--role-session-name",
                "hermes-lab-collector",
                "--duration-seconds",
                "900",
            ],
            environment,
            deadline,
        )
        credentials = response.get("Credentials")

        if type(credentials) is not dict:
            raise CollectionError("invalid_credentials")

        for name, variable in (
            ("AccessKeyId", "AWS_ACCESS_KEY_ID"),
            ("SecretAccessKey", "AWS_SECRET_ACCESS_KEY"),
            ("SessionToken", "AWS_SESSION_TOKEN"),
        ):
            value = credentials.get(name)

            if type(value) is not str or not value:
                raise CollectionError("invalid_credentials")

            environment[variable] = value

        environment["AWS_EC2_METADATA_DISABLED"] = "true"

        self._identity(
            self._call(
                "sts", "get-caller-identity", [], environment, deadline
            ),
            self.identity.role_arn,
        )

        return environment

    def call(
        self,
        service: str,
        operation: str,
        arguments: list[str],
        deadline: Deadline,
    ) -> dict[str, Any]:
        if self.environment is None:
            self.environment = self._assume(deadline)

        return self._call(
            service, operation, arguments, self.environment, deadline
        )
