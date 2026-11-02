from dataclasses import dataclass
from typing import Any

from aws_access.configuration import AccessPolicy, CredentialError
from collector.execution import CollectionError


@dataclass(frozen=True)
class AwsIdentity:
    account_id: str
    region: str
    authentication: AccessPolicy

    @classmethod
    def load(cls, configuration: dict[str, Any]) -> AwsIdentity:
        try:
            authentication = AccessPolicy.load(configuration)
        except CredentialError as error:
            raise CollectionError(error.code) from None

        return cls(
            authentication.account_id, authentication.region, authentication
        )
