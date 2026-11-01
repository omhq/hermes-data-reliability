import re
from dataclasses import dataclass
from typing import Any

from collector.execution import CollectionError


@dataclass(frozen=True)
class AwsIdentity:
    account_id: str
    region: str
    host_role_arn: str
    role_arn: str

    @classmethod
    def load(cls, configuration: dict[str, Any]) -> AwsIdentity:
        for name, pattern in (
            ("account_id", r"[0-9]{12}"),
            ("region", r"[a-z]{2}(?:-[a-z]+)+-[0-9]+"),
        ):
            value = configuration.get(name)

            if type(value) is not str or re.fullmatch(pattern, value) is None:
                raise CollectionError("invalid_configuration")

        account = configuration["account_id"]

        for name in ("role_arn", "host_role_arn"):
            value = configuration.get(name)
            pattern = rf"arn:aws:iam::{account}:role/[A-Za-z0-9_-]+"

            if type(value) is not str or re.fullmatch(pattern, value) is None:
                raise CollectionError("invalid_configuration")

        if configuration["role_arn"] == configuration["host_role_arn"]:
            raise CollectionError("invalid_configuration")

        return cls(
            account,
            configuration["region"],
            configuration["host_role_arn"],
            configuration["role_arn"],
        )
