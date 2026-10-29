from typing import Any

from host_aws.cli import (
    AwsCallError,
    CliRunner,
    Deadline,
    host_environment,
    run_json,
)

from .access import CollectionAccess


class CloudWatchClient:
    """Host collection access, independent of agent tool authorization."""

    def __init__(
        self, access: CollectionAccess, runner: CliRunner = run_json
    ) -> None:
        self.access = access
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
                self.access.region,
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
            f"arn:aws:sts::{self.access.account_id}:"
            f"assumed-role/{role.rsplit('/', 1)[-1]}/"
        )
        arn = identity.get("Arn")

        if (
            identity.get("Account") != self.access.account_id
            or type(arn) is not str
            or not arn.startswith(expected)
        ):
            raise AwsCallError("unexpected_aws_identity")

    def _assume(self, deadline: Deadline) -> dict[str, str]:
        environment = host_environment(self.access.region)

        self._identity(
            self._call(
                "sts", "get-caller-identity", [], environment, deadline
            ),
            self.access.host_role_arn,
        )
        response = self._call(
            "sts",
            "assume-role",
            [
                "--role-arn",
                self.access.role_arn,
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
            raise AwsCallError("invalid_credentials")

        for name, variable in (
            ("AccessKeyId", "AWS_ACCESS_KEY_ID"),
            ("SecretAccessKey", "AWS_SECRET_ACCESS_KEY"),
            ("SessionToken", "AWS_SESSION_TOKEN"),
        ):
            value = credentials.get(name)

            if type(value) is not str or not value:
                raise AwsCallError("invalid_credentials")

            environment[variable] = value

        environment["AWS_EC2_METADATA_DISABLED"] = "true"

        self._identity(
            self._call(
                "sts", "get-caller-identity", [], environment, deadline
            ),
            self.access.role_arn,
        )

        return environment

    def filter_log_events(
        self,
        start: int,
        end: int,
        limit: int,
        token: str | None,
        deadline: Deadline,
    ) -> dict[str, Any]:
        if self.environment is None:
            self.environment = self._assume(deadline)

        arguments = [
            "--log-group-name",
            self.access.log_group,
            "--start-time",
            str(start),
            "--end-time",
            str(end),
            "--filter-pattern",
            "",
            "--limit",
            str(limit),
            "--no-unmask",
        ]

        if token is not None:
            arguments.extend(["--next-token", token])

        return self._call(
            "logs", "filter-log-events", arguments, self.environment, deadline
        )
