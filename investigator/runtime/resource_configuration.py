import json
from pathlib import Path
from typing import Any

from aws_access.configuration import AccessPolicy
from collector.connectors.cloudwatch import CloudWatchConnector
from observations.yaml import load_yaml
from tool_boundary.catalog import ToolPackages

from .deploy import PROJECT_ROOT, validate_tool_configuration

RESOURCE_FIELDS = {
    "account_id",
    "region",
    "bucket",
    "log_group",
    "collection_log_groups",
    "tool_packages",
    "cloudtrail_resource_names",
    "sqs_queue_arns",
    "lambda_function_arns",
    "lambda_event_source_mappings",
    "health_resources",
}


def configure_resources(
    definition: dict[str, Any], profile: str
) -> dict[str, Any]:
    if (
        definition.get("version") != 1
        or type(definition.get("version")) is not int
        or not definition.keys()
        <= RESOURCE_FIELDS | {"version", "authentication", "notifications"}
    ):
        raise ValueError("Runtime resource configuration requires version 1")

    authentication = definition.get("authentication", {})

    if type(authentication) is not dict or not authentication.keys() <= {
        "assume_role_arn",
        "principal_id",
        "principal_arn",
    }:
        raise ValueError("Invalid local authentication configuration")

    selected = ToolPackages.from_agent_config(
        load_yaml(PROJECT_ROOT / "investigator/runtime/config.yaml")
    )
    configuration = {
        key: value
        for key, value in definition.items()
        if key in RESOURCE_FIELDS
    }
    configuration.setdefault("tool_packages", list(selected.enabled))
    configuration.setdefault(
        "cloudtrail_resource_names",
        selected.options.get("hermes-cloudtrail", {}).get(
            "resource_names", []
        ),
    )

    for key, value in (
        ("collection_log_groups", []),
        ("sqs_queue_arns", []),
        ("lambda_function_arns", []),
        ("lambda_event_source_mappings", {}),
        ("health_resources", {}),
    ):
        configuration.setdefault(key, value)

    configuration["authentication"] = {
        **authentication,
        "source": "profile",
        "profile": profile,
    }
    AccessPolicy.load(configuration)
    CloudWatchConnector(configuration)
    validate_tool_configuration(json.dumps(configuration).encode())

    return configuration


def load_resources(
    path: Path, profile: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    definition = load_yaml(path)
    configuration = configure_resources(definition, profile)
    notifications = definition.get("notifications", {"enabled": False})

    if (
        type(notifications) is not dict
        or not notifications.keys() <= {"enabled", "topic_arn"}
        or type(notifications.get("enabled")) is not bool
    ):
        raise ValueError("Invalid notification configuration")

    return configuration, notifications
