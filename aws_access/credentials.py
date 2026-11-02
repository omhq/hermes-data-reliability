from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .configuration import CredentialError


@dataclass(frozen=True, repr=False)
class Credentials:
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    token: str | None = field(default=None, repr=False)
    expiration: datetime | None = None

    @classmethod
    def load(cls, value: object, *, temporary: bool = False) -> Credentials:
        if type(value) is not dict:
            raise CredentialError("invalid_credentials")

        for name in ("AccessKeyId", "SecretAccessKey"):
            if (
                type(value.get(name)) is not str
                or not 1 <= len(value[name]) <= 4096
            ):
                raise CredentialError("invalid_credentials")

        token = value.get("SessionToken")
        expiration = value.get("Expiration")

        if token is not None and (
            type(token) is not str or not 1 <= len(token) <= 16384
        ):
            raise CredentialError("invalid_credentials")
        if temporary and (token is None or expiration is None):
            raise CredentialError("invalid_credentials")
        if expiration is not None:
            if type(expiration) is not str:
                raise CredentialError("invalid_credentials")

            try:
                expiration = datetime.fromisoformat(
                    expiration.replace("Z", "+00:00")
                )
            except ValueError:
                raise CredentialError("invalid_credentials") from None

            if expiration.tzinfo is None:
                raise CredentialError("invalid_credentials")
            if expiration <= datetime.now(UTC):
                raise CredentialError("credentials_expired")

        return cls(
            value["AccessKeyId"], value["SecretAccessKey"], token, expiration
        )

    def document(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "Version": 1,
            "AccessKeyId": self.access_key,
            "SecretAccessKey": self.secret_key,
        }

        if self.token is not None:
            result["SessionToken"] = self.token
        if self.expiration is not None:
            result["Expiration"] = self.expiration.isoformat()

        return result

    def environment(self, base: dict[str, str]) -> dict[str, str]:
        environment = {
            **base,
            "HOME": "/nonexistent",
            "AWS_CONFIG_FILE": "/dev/null",
            "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_ACCESS_KEY_ID": self.access_key,
            "AWS_SECRET_ACCESS_KEY": self.secret_key,
        }
        environment.pop("AWS_PROFILE", None)
        environment.pop("AWS_SESSION_TOKEN", None)

        if self.token is not None:
            environment["AWS_SESSION_TOKEN"] = self.token

        return environment
