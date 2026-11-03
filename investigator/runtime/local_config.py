import argparse
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from aws_access.configuration import AccessPolicy, CredentialError
from aws_access.provider import Authenticated, CredentialProvider
from collector.execution import CollectionError
from investigator.logging_config import configure_logging
from observations.yaml import dump_yaml
from tool_boundary.wire import ExecutionError

from .deploy import PROJECT_ROOT
from .notification_aws import NotificationConfig
from .resource_configuration import RESOURCE_FIELDS, load_resources

DESTINATION = PROJECT_ROOT / ".local/docker"
RESOURCE_CONFIG = PROJECT_ROOT / ".local/runtime.yaml"
CREDENTIAL_PATH = "/run/hermes-lab-credentials/aws.json"
LOGGER = logging.getLogger(__name__)


class LocalConfigurationError(ValueError):
    """An actionable diagnostic containing only trusted text."""


def write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)

    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)

        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def aws_failure(command: list[str], diagnostics: bytes) -> str:
    operation = "AWS credential resolution"

    if command[1:3] == ["sts", "get-caller-identity"]:
        operation = "AWS identity check"
    elif command[1:3] == ["sts", "assume-role"]:
        operation = "Optional AWS role assumption"

    lowered = diagnostics.lower()

    if b"(accessdenied" in lowered:
        return (
            f"{operation} failed (access_denied). Check the selected "
            "profile's permissions. If you configured assume_role_arn, "
            "also check that role's trust policy."
        )
    if any(
        marker in lowered
        for marker in (
            b"sso",
            b"(expiredtoken",
            b"(invalidclienttokenid)",
            b"token has expired",
            b"unable to locate credentials",
        )
    ):
        return (
            f"{operation} failed (credentials_unavailable). Refresh your "
            "selected profile's login and retry. For SSO, use "
            "aws sso login --profile YOUR_PROFILE."
        )
    if any(
        marker in lowered
        for marker in (
            b"could not connect",
            b"ssl validation failed",
            b"connect timeout",
            b"read timeout",
            b"proxy",
        )
    ):
        return f"{operation} failed (connection_failed). Check network access."

    return (
        f"{operation} failed (aws_request_failed). "
        "Check the selected profile. Provider output was withheld."
    )


def run_aws(command: list[str], environment: dict[str, str]) -> dict[str, Any]:
    try:
        response = subprocess.run(
            command,
            capture_output=True,
            timeout=60,
            env=environment,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise LocalConfigurationError(
            "The AWS CLI request timed out."
        ) from None
    except OSError:
        raise LocalConfigurationError(
            "The AWS CLI could not start. Check that aws is installed."
        ) from None

    if response.returncode:
        raise LocalConfigurationError(aws_failure(command, response.stderr))
    if len(response.stdout) > 65536:
        raise LocalConfigurationError("AWS credential response is too large")

    try:
        value = json.loads(response.stdout)
    except ValueError:
        raise LocalConfigurationError("Invalid AWS JSON response") from None

    if type(value) is not dict:
        raise LocalConfigurationError("Invalid AWS response")

    return value


def resolve_credentials(
    configuration: dict[str, Any], exported: Path
) -> Authenticated:
    executable = shutil.which("aws")

    if executable is None:
        raise LocalConfigurationError("Install AWS CLI v2 before configuring.")

    try:
        with exported.open("rb") as source:
            payload = source.read(65537)

        if len(payload) > 65536:
            raise LocalConfigurationError("Exported credentials are too large")

        document = json.loads(payload)
    except OSError, ValueError:
        raise LocalConfigurationError(
            "Exported profile credentials are unavailable or invalid."
        ) from None

    provider = CredentialProvider(AccessPolicy.load(configuration), executable)

    return provider.authenticate_exported(document, run_aws)


def import_terraform(source: Path, destination: Path) -> None:
    value = json.loads(source.read_bytes())

    if type(value) is not dict:
        raise LocalConfigurationError("Invalid Terraform resource output")

    definition = {
        "version": 1,
        **{key: value[key] for key in RESOURCE_FIELDS if key in value},
        "notifications": {"enabled": False},
    }
    write_private(destination, dump_yaml(definition))


def configure(
    profile: str, source: Path, destination: Path, exported: Path
) -> None:
    configuration, notifications = load_resources(source, profile)
    acquired = resolve_credentials(configuration, exported)
    original = configuration["authentication"]
    authentication = {
        "source": "file",
        "path": CREDENTIAL_PATH,
        "principal_id": acquired.principal.identifier,
    }
    configuration["authentication"] = authentication
    operations = {
        "account_id": configuration["account_id"],
        "region": configuration["region"],
        "authentication": authentication,
        "finding_notifications_enabled": notifications["enabled"],
    }

    if notifications["enabled"]:
        operations["alert_topic_arn"] = notifications.get("topic_arn")
        NotificationConfig.load(operations)

    metadata = {
        "source_principal_id": acquired.source_principal.identifier,
        "assume_role_arn": original.get("assume_role_arn"),
    }

    for name, value in (
        ("aws-credentials.json", acquired.credentials.document()),
        ("local-access.json", metadata),
        ("investigator.json", configuration),
        ("operations.json", operations),
    ):
        write_private(destination / name, json.dumps(value).encode())

    LOGGER.info(
        "Configured the selected AWS profile; role assumption is optional."
    )


def refresh(profile: str, destination: Path, exported: Path) -> None:
    configuration = json.loads(
        (destination / "investigator.json").read_bytes()
    )
    metadata = json.loads((destination / "local-access.json").read_bytes())
    expected_principal = configuration["authentication"]["principal_id"]
    configuration["authentication"] = {
        "source": "profile",
        "profile": profile,
        "principal_id": metadata["source_principal_id"],
        "assume_role_arn": metadata["assume_role_arn"],
    }
    acquired = resolve_credentials(configuration, exported)

    if acquired.principal.identifier != expected_principal:
        raise CredentialError("unexpected_aws_identity")

    write_private(
        destination / "aws-credentials.json",
        json.dumps(acquired.credentials.document()).encode(),
    )
    LOGGER.info(
        "AWS credentials refreshed; account and principal revalidated."
    )


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["configure", "credentials"])
    parser.add_argument("--profile", required=True)
    parser.add_argument("--exported-credentials", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=RESOURCE_CONFIG)
    parser.add_argument("--terraform-output", type=Path)
    parser.add_argument("--destination", type=Path, default=DESTINATION)
    args = parser.parse_args()

    if not args.profile.strip() or args.profile == "default":
        parser.error("Select an explicit nondefault AWS profile")
    if args.terraform_output is not None and args.action != "configure":
        parser.error("--terraform-output requires configure")

    try:
        if args.action == "configure":
            if args.terraform_output is not None:
                import_terraform(args.terraform_output, args.config)
            if not args.config.is_file():
                raise LocalConfigurationError(
                    "Create .local/runtime.yaml from runtime.example.yaml "
                    "with your approved account and resources, or pass "
                    "--config. Terraform is not required."
                )

            configure(
                args.profile,
                args.config,
                args.destination,
                args.exported_credentials,
            )
        else:
            refresh(args.profile, args.destination, args.exported_credentials)
    except LocalConfigurationError as error:
        raise SystemExit(str(error)) from None
    except CredentialError as error:
        raise SystemExit(
            f"AWS authentication failed ({error.code}). Check the account "
            "allowlist, selected principal, and credential expiry."
        ) from None
    except (
        CollectionError,
        ExecutionError,
        ValueError,
        OSError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ):
        raise SystemExit(
            "Invalid or unreadable local runtime configuration. "
            "Check the resource file and its permissions. "
            "External error details were withheld."
        ) from None


if __name__ == "__main__":
    main()
