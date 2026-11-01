import json
from pathlib import Path
from typing import Any

from incidents.specification import validate_investigation
from investigator.runtime.evidence import SessionEvidenceVerifier
from investigator.runtime.findings import FindingParser
from investigator.runtime.launch import run_once, stop_on_disconnect
from investigator.runtime.sessions import INCIDENT_SOCKET, InvestigationSession
from tool_boundary.catalog import ToolPackages

from .presentation import describe_observation


def investigate(row: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    event = json.loads(row["evidence"])
    result = describe_observation(event["result"])

    if (
        event["account_id"] != config["account_id"]
        or row["id"] != event["incident_id"]
    ):
        raise ValueError("Pipeline-health source is no longer authorized")

    packages = ToolPackages.from_executor_config(config)
    settings = validate_investigation(
        row["definition"]["investigation"], defaults=True
    )
    specification = {
        "pipeline_id": result["pipeline_id"],
        "expectation_id": result["expectation_id"],
        "occurrence": result["occurrence"],
        "source_scope": result["source"],
        "revision": result["revision"],
        "expected_source": {
            key: config[key]
            for key in ("account_id", "region", "bucket", "log_group")
        },
    }
    prompt = (
        (
            Path(__file__).parent
            / "skills/investigate_pipeline_health/SKILL.md"
        ).read_text()
        + "\nPipeline investigation instructions:\n"
        + settings["instructions"]
        + "\nConfigured investigation:\n"
        + json.dumps(
            {
                "incident_id": row["id"],
                "objective": settings["objective"],
                "scope": specification,
                "authorized_tools": sorted(
                    packages.tools
                    & {
                        "list_pipeline_expectations",
                        "check_pipeline_expectations",
                        "get_pipeline_check_history",
                        "search_log_events",
                        "read_s3_object",
                        "list_s3_objects",
                    }
                ),
            }
        )
        + "\nOriginal check (retrieve history for citeable evidence):\n"
        + json.dumps(result)
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
