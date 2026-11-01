from typing import Any

from tool_boundary.models import ResourceContext

from .observations import Target


def context(
    config: dict[str, Any],
    targets: tuple[Target, ...],
    start: int,
    end: int,
    expires: int,
) -> ResourceContext:
    groups = [
        target.location["location"]
        for target in targets
        if target.adapter == "collected_events"
        and target.location["connector"] == "cloudwatch_logs"
    ]
    return ResourceContext(
        "pipeline_health",
        config["account_id"],
        config["region"],
        config["bucket"],
        groups[0] if groups else config["log_group"],
        tuple(
            target.location["key"]
            for target in targets
            if target.adapter == "s3_object"
        ),
        start,
        end,
        expires,
    )
