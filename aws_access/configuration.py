import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


class CredentialError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AccessPolicy:
    account_id: str
    region: str
    source: Literal["profile", "instance", "file"]
    profile: str | None = None
    path: Path | None = None
    principal_id: str | None = None
    principal_arn: str | None = None
    assume_role_arn: str | None = None

    @classmethod
    def load(cls, configuration: dict[str, Any]) -> AccessPolicy:
        account = configuration.get("account_id")
        region = configuration.get("region")
        authentication = configuration.get("authentication")

        if (
            type(account) is not str
            or re.fullmatch(r"[0-9]{12}", account) is None
            or type(region) is not str
            or re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", region) is None
            or type(authentication) is not dict
            or not authentication.keys()
            <= {
                "source",
                "profile",
                "path",
                "principal_id",
                "principal_arn",
                "assume_role_arn",
            }
        ):
            raise CredentialError("invalid_configuration")

        source = authentication.get("source")
        profile = authentication.get("profile")
        path = authentication.get("path")

        if source not in ("profile", "instance", "file"):
            raise CredentialError("invalid_configuration")
        if source == "profile":
            if (
                type(profile) is not str
                or not profile.strip()
                or profile == "default"
                or path is not None
            ):
                raise CredentialError("invalid_configuration")
        elif source == "file":
            if (
                type(path) is not str
                or not Path(path).is_absolute()
                or profile is not None
            ):
                raise CredentialError("invalid_configuration")
        elif profile is not None or path is not None:
            raise CredentialError("invalid_configuration")

        for field in ("principal_arn", "assume_role_arn"):
            value = authentication.get(field)

            if value is not None and (
                type(value) is not str
                or re.fullmatch(
                    rf"arn:aws:iam::{account}:(?:role|user)/[A-Za-z0-9_+=,.@/-]+",
                    value,
                )
                is None
                or (field == "assume_role_arn" and ":role/" not in value)
            ):
                raise CredentialError("invalid_configuration")

        principal_id = authentication.get("principal_id")

        if principal_id is not None and (
            type(principal_id) is not str
            or re.fullmatch(r"[A-Za-z0-9_+=,.@:/-]{1,256}", principal_id)
            is None
        ):
            raise CredentialError("invalid_configuration")
        if source == "file" and principal_id is None:
            raise CredentialError("invalid_configuration")

        return cls(
            account,
            region,
            source,
            profile,
            Path(path) if path is not None else None,
            principal_id,
            authentication.get("principal_arn"),
            authentication.get("assume_role_arn"),
        )

    def environment(self) -> dict[str, str]:
        environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "HOME": "/nonexistent",
            "AWS_CONFIG_FILE": "/dev/null",
            "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_METADATA_SERVICE_TIMEOUT": "2",
            "AWS_METADATA_SERVICE_NUM_ATTEMPTS": "1",
            "AWS_DEFAULT_REGION": self.region,
            "AWS_MAX_ATTEMPTS": "1",
            "AWS_PAGER": "",
        }

        if self.source == "instance":
            environment["AWS_EC2_METADATA_DISABLED"] = "false"
        elif self.source == "profile":
            assert self.profile is not None
            home = Path.home()
            environment.update(
                {
                    "PATH": os.environ.get("PATH", os.defpath),
                    "HOME": str(home),
                    "AWS_CONFIG_FILE": os.environ.get(
                        "AWS_CONFIG_FILE", str(home / ".aws/config")
                    ),
                    "AWS_SHARED_CREDENTIALS_FILE": os.environ.get(
                        "AWS_SHARED_CREDENTIALS_FILE",
                        str(home / ".aws/credentials"),
                    ),
                    "AWS_PROFILE": self.profile,
                }
            )

        return environment
