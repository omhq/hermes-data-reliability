import json
from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol

from .configuration import AccessPolicy, CredentialError
from .credentials import Credentials


class AwsCall(Protocol):
    def __call__(
        self, command: list[str], environment: dict[str, str]
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Principal:
    account_id: str
    arn: str
    identifier: str

    @classmethod
    def load(cls, value: dict[str, Any], account: str) -> Principal:
        arn = value.get("Arn")
        identifier = value.get("UserId")

        if (
            value.get("Account") != account
            or type(arn) is not str
            or type(identifier) is not str
            or not identifier
        ):
            raise CredentialError("unexpected_aws_identity")

        if arn.startswith(f"arn:aws:sts::{account}:assumed-role/"):
            if len(arn.split("/")) != 3:
                raise CredentialError("unexpected_aws_identity")

            identifier = identifier.split(":", 1)[0]
        elif not (
            arn.startswith(f"arn:aws:iam::{account}:user/")
            or arn == f"arn:aws:iam::{account}:root"
            or arn.startswith(f"arn:aws:sts::{account}:federated-user/")
        ):
            raise CredentialError("unexpected_aws_identity")

        return cls(account, arn, identifier)

    def matches_arn(self, expected: str) -> bool:
        if ":role/" not in expected:
            return self.arn == expected

        return self.arn.startswith(
            f"arn:aws:sts::{self.account_id}:assumed-role/"
            f"{expected.rsplit('/', 1)[-1]}/"
        )


@dataclass(frozen=True, repr=False)
class Authenticated:
    credentials: Credentials
    source_principal: Principal
    principal: Principal
    environment: dict[str, str]


class CredentialProvider:
    def __init__(
        self, policy: AccessPolicy, executable: str = "/usr/bin/aws"
    ) -> None:
        self.policy = policy
        self.executable = executable
        self.principal_id = policy.principal_id
        self.identity_lock = Lock()

    def command(
        self, service: str, operation: str, arguments: list[str]
    ) -> list[str]:
        return [
            self.executable,
            service,
            operation,
            "--region",
            self.policy.region,
            "--output",
            "json",
            "--no-cli-pager",
            "--cli-connect-timeout",
            "5",
            "--cli-read-timeout",
            "20",
            *arguments,
        ]

    def authenticate(self, call: AwsCall) -> Authenticated:
        base = self.policy.environment()

        if self.policy.source == "file":
            assert self.policy.path is not None

            try:
                with self.policy.path.open("rb") as source:
                    payload = source.read(65537)

                if len(payload) > 65536:
                    raise CredentialError("invalid_credentials")

                document = json.loads(payload)
            except OSError, ValueError:
                raise CredentialError("credentials_unavailable") from None
        else:
            document = call(
                [
                    self.executable,
                    "configure",
                    "export-credentials",
                    "--format",
                    "process",
                ],
                base,
            )

        if (
            type(document) is not dict
            or type(document.get("Version")) is not int
            or document["Version"] != 1
        ):
            raise CredentialError("invalid_credentials")

        credentials = Credentials.load(document)
        environment = credentials.environment(base)
        source_principal = Principal.load(
            call(self.command("sts", "get-caller-identity", []), environment),
            self.policy.account_id,
        )

        with self.identity_lock:
            if (
                self.principal_id is not None
                and source_principal.identifier != self.principal_id
            ) or (
                self.policy.principal_arn is not None
                and not source_principal.matches_arn(self.policy.principal_arn)
            ):
                raise CredentialError("unexpected_aws_identity")

            self.principal_id = source_principal.identifier
        principal = source_principal

        if self.policy.assume_role_arn is not None:
            response = call(
                self.command(
                    "sts",
                    "assume-role",
                    [
                        "--role-arn",
                        self.policy.assume_role_arn,
                        "--role-session-name",
                        "hermes-lab",
                        "--duration-seconds",
                        "900",
                    ],
                ),
                environment,
            )
            credentials = Credentials.load(
                response.get("Credentials"), temporary=True
            )
            environment = credentials.environment(base)
            principal = Principal.load(
                call(
                    self.command("sts", "get-caller-identity", []), environment
                ),
                self.policy.account_id,
            )

            if not principal.matches_arn(self.policy.assume_role_arn):
                raise CredentialError("unexpected_aws_identity")

        return Authenticated(
            credentials, source_principal, principal, environment
        )
