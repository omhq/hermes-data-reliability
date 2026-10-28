from dataclasses import dataclass
from typing import Self

PACKAGE_TOOLSETS = {
    "hermes-lab-aws": "hermes_lab_aws",
    "hermes-cloudwatch": "aws_cloudwatch",
    "hermes-cloudtrail": "aws_cloudtrail",
}
PACKAGE_TOOLS = {
    "hermes-lab-aws": frozenset(
        {
            "list_run_objects",
            "read_run_object",
            "list_aws_resources",
            "list_s3_objects",
            "read_s3_object",
        }
    ),
    "hermes-cloudwatch": frozenset(
        {"describe_cloudwatch_scope", "search_run_logs", "search_log_events"}
    ),
    "hermes-cloudtrail": frozenset(
        {"describe_cloudtrail_scope", "lookup_cloudtrail_events"}
    ),
}
ALL_TOOLS: frozenset[str] = frozenset().union(*PACKAGE_TOOLS.values())


def names(value: object, allowed: set[str] | None = None) -> tuple[str, ...]:
    if type(value) is not list or len(value) > 50:
        raise ValueError("Invalid tool package configuration")

    result = []

    for item in value:
        if (
            type(item) is not str
            or not 1 <= len(item) <= 2048
            or any(ord(character) < 32 for character in item)
            or item in result
            or allowed is not None
            and item not in allowed
        ):
            raise ValueError("Invalid tool package configuration")

        result.append(item)

    return tuple(result)


@dataclass(frozen=True)
class ToolPackages:
    enabled: tuple[str, ...]
    cloudtrail_resources: tuple[str, ...]

    @classmethod
    def parse(cls, enabled: object, disabled: object, resources: object) -> Self:
        approved = set(PACKAGE_TOOLSETS)
        active = names(enabled, approved)
        denied = names(disabled, approved)
        selected = tuple(name for name in active if name not in denied)
        resources = names(resources)

        if "hermes-cloudtrail" in selected and not resources:
            raise ValueError("CloudTrail requires explicit resource_names")

        return cls(selected, resources)

    @classmethod
    def from_agent_config(cls, config: object) -> Self:
        if type(config) is not dict or type(config.get("plugins")) is not dict:
            raise ValueError("Agent configuration requires plugins")

        plugins = config["plugins"]
        entries = plugins.get("entries", {})

        if type(entries) is not dict:
            raise ValueError("Invalid plugin entries")

        cloudtrail = entries.get("hermes-cloudtrail", {})

        if type(cloudtrail) is not dict:
            raise ValueError("Invalid CloudTrail configuration")

        return cls.parse(
            plugins.get("enabled", []),
            plugins.get("disabled", []),
            cloudtrail.get("resource_names", []),
        )

    @classmethod
    def from_executor_config(cls, config: object) -> Self:
        if type(config) is not dict:
            raise ValueError("Invalid executor tool package configuration")

        return cls.parse(
            config.get("tool_packages"),
            [],
            config.get("cloudtrail_resource_names", []),
        )

    @property
    def tools(self) -> frozenset[str]:
        return frozenset().union(*(PACKAGE_TOOLS[name] for name in self.enabled))

    @property
    def toolsets(self) -> tuple[str, ...]:
        return tuple(PACKAGE_TOOLSETS[name] for name in self.enabled)
