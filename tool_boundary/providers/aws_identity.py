import re
from typing import Any

from tool_boundary.models import IncidentContext, ResourceContext
from tool_boundary.validation import require
from tool_boundary.wire import ExecutionError


class AwsIdentity:
    @staticmethod
    def valid_account(value: object) -> bool:
        return (
            type(value) is str
            and re.fullmatch(r"[0-9]{12}", value) is not None
        )

    @staticmethod
    def valid_region(value: object) -> bool:
        return (
            type(value) is str
            and re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", value) is not None
        )

    @classmethod
    def validate_configuration(cls, configuration: dict[str, Any]) -> None:
        account = configuration.get("account_id")

        if not cls.valid_account(account) or not cls.valid_region(
            configuration.get("region")
        ):
            raise ExecutionError("invalid_configuration")

        for name in ("role_arn", "host_role_arn"):
            value = configuration.get(name)
            pattern = rf"arn:aws:iam::{account}:role/[A-Za-z0-9_-]+"

            if type(value) is not str or re.fullmatch(pattern, value) is None:
                raise ExecutionError("invalid_configuration")

        if configuration["role_arn"] == configuration["host_role_arn"]:
            raise ExecutionError("invalid_configuration")

    @classmethod
    def validate_scope(
        cls,
        context: IncidentContext | ResourceContext,
        configuration: dict[str, Any],
    ) -> None:
        require(
            cls.valid_account(context.account_id)
            and cls.valid_region(context.region)
            and context.account_id == configuration.get("account_id")
            and context.region == configuration.get("region"),
            "invalid_context",
        )

    @staticmethod
    def resource_arns(
        value: object, prefix: str, suffix: str
    ) -> tuple[str, ...]:
        if type(value) is not list or len(value) > 50:
            raise ValueError("Invalid service resource allowlist")

        approved: list[str] = []

        for arn in value:
            if (
                type(arn) is not str
                or re.fullmatch(re.escape(prefix) + suffix, arn) is None
                or arn in approved
            ):
                raise ValueError("Invalid service resource allowlist")

            approved.append(arn)

        return tuple(approved)
