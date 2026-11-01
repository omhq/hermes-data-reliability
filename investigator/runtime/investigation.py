import json
from typing import Any

from incidents.specification import validate_investigation
from investigator.capabilities.pipeline_health.investigation import (
    investigate as investigate_health,
)
from investigator.runtime.evidence import SessionEvidenceVerifier
from investigator.runtime.findings import FindingParser
from investigator.runtime.launch import run_once, stop_on_disconnect
from investigator.runtime.sessions import (
    INCIDENT_SOCKET,
    InvestigationSession,
)
from tool_boundary.catalog import ToolPackages
from tool_boundary.loader import load_tools
from tool_boundary.services.cloudwatch import CloudWatchProvider


def incident_specification(row: dict[str, Any]) -> dict[str, Any]:
    incident = json.loads(row["evidence"])

    if incident.get("run_id") != row["id"]:
        raise ValueError("Incident identifier mismatch")

    incident_time_ms = incident["observation"]["event"]["timestamp"]

    if type(incident_time_ms) is not int or incident_time_ms <= 0:
        raise ValueError("Incident needs a valid provider timestamp")

    return {
        "run_id": row["id"],
        "attempt_ids": [incident["attempt_id"]],
        "incident_time_ms": incident_time_ms,
        "ttl_seconds": 300,
    }


def investigate(row: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if row["account_id"] != config["account_id"]:
        raise ValueError("Incident AWS account is no longer authorized")

    if row["kind"] == "health":
        return investigate_health(row, config)

    incident = json.loads(row["evidence"])

    if row["kind"] not in {"run", "log"}:
        raise ValueError("Unsupported incident kind")

    logs_only = row["kind"] == "log"
    observation = incident["observation"]
    event = observation["event"]

    if any(
        observation[key] != config[key] for key in ("account_id", "region")
    ) or observation[
        "log_group"
    ] not in CloudWatchProvider.collection_log_groups(config):
        raise ValueError("Incident source is no longer authorized")

    if logs_only:
        specification = {
            "observation_id": row["id"],
            "incident_time_ms": observation["event"]["timestamp"],
            "ttl_seconds": 300,
        }
    else:
        specification = incident_specification(row)

    packages = ToolPackages.from_executor_config(config)
    tools = sorted(packages.tools_for("log" if logs_only else "run"))

    if not tools:
        raise ValueError("No enabled tool package supports this investigation")

    specification["expected_source"] = {
        key: observation[key] for key in ("account_id", "region", "log_group")
    }

    settings = row["definition"]["investigation"]

    validate_investigation(settings, defaults=True)

    prompt = (
        settings["instructions"]
        + "\nConfigured investigation:\n"
        + json.dumps(
            {
                "incident_id": row["id"],
                "objective": settings["objective"],
                "authorized_tools": tools,
                "configured_resources": load_tools(config).describe(),
                "scope": specification,
                "matched_rules": [
                    {
                        "rule_id": matched["rule_id"],
                        "severity": matched["severity"],
                    }
                    for matched in row.get("matches", [])
                ],
            },
            ensure_ascii=False,
        )
        + "\nUntrusted incident evidence:\n"
        + json.dumps(
            {
                "bucket": config["bucket"],
                "region": config["region"],
                "log_group": observation["log_group"],
                "incident": {
                    "event_reference": "cloudwatch-event:" + event["eventId"],
                    "message_sample": event["message"][:1000],
                    "sample_may_be_incomplete": True,
                },
            },
            ensure_ascii=False,
        )
    )

    with (
        stop_on_disconnect(),
        InvestigationSession(
            specification, control_socket=INCIDENT_SOCKET
        ) as session,
    ):
        stdout = run_once(session, prompt)
        finding = FindingParser(SessionEvidenceVerifier(session)).parse(
            stdout, row["id"]
        )

        session.check()
        return finding
