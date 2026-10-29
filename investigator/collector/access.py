import re
from dataclasses import dataclass
from typing import Any

from investigator.aws_cli import AwsCallError


@dataclass(frozen=True)
class CollectionAccess:
    account_id: str
    region: str
    log_group: str
    host_role_arn: str
    role_arn: str

    @classmethod
    def load(cls, configuration: dict[str, Any]) -> CollectionAccess:
        """Validate only the host identity and collection source settings."""
        for name, pattern in (
            ("account_id", r"[0-9]{12}"),
            ("region", r"[a-z]{2}(?:-[a-z]+)+-[0-9]+"),
            ("log_group", r"[./A-Za-z0-9_#-]{1,512}"),
        ):
            value = configuration.get(name)

            if type(value) is not str or re.fullmatch(pattern, value) is None:
                raise AwsCallError("invalid_configuration")

        account = configuration["account_id"]

        for name in ("role_arn", "host_role_arn"):
            value = configuration.get(name)
            pattern = rf"arn:aws:iam::{account}:role/[A-Za-z0-9_-]+"

            if type(value) is not str or re.fullmatch(pattern, value) is None:
                raise AwsCallError("invalid_configuration")

        if configuration["role_arn"] == configuration["host_role_arn"]:
            raise AwsCallError("invalid_configuration")

        return cls(
            account,
            configuration["region"],
            configuration["log_group"],
            configuration["host_role_arn"],
            configuration["role_arn"],
        )
