import json
import sqlite3

from pathlib import Path
from typing import Any

from .observation_store import matches_for


def evidence_lines(finding: dict[str, Any]) -> str:
    lines = []

    for reference in finding["evidence"]:
        partial = "; partial" if reference["truncated"] else ""
        lines.append(
            f"- {reference['reference']} — {reference['observation']}{partial}"
            f"\n  Receipt: `{reference['evidence_id']}`; "
            f"SHA-256: `{reference['sha256']}`"
            f"\n  Observed at (Unix ms): {reference['observed_at_ms']}; "
            f"source: `{json.dumps(reference['source'], sort_keys=True)}`"
        )

    if finding.get("evidence_warnings"):
        lines.append("\nEvidence limitations:")
        lines.extend(
            "- " + warning["code"] for warning in finding["evidence_warnings"]
        )

    return "\n".join(lines)


def export(db: sqlite3.Connection, root: Path) -> None:
    rows = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM incidents ORDER BY detected_at,id"
        )
    ]

    for row in rows:
        row["evidence"] = json.loads(row["evidence"])
        row["finding"] = json.loads(row["finding"]) if row["finding"] else None
        row["matches"] = matches_for(db, row["id"])

    temporary = root / "incidents.jsonl.tmp"

    temporary.write_text("".join(json.dumps(row) + "\n" for row in rows))
    temporary.replace(root / "incidents.jsonl")

    reports = root / "reports"

    reports.mkdir(exist_ok=True)

    for row in rows:
        if row["finding"] is None:
            continue

        finding = row["finding"]
        report = f"# {finding['summary']}\n\nIncident: `{row['id']}`\n\n"
        report += f"Severity: **{row['severity']}**\n\n"

        if row["matches"]:
            report += (
                "Matched rules: "
                + ", ".join(
                    "`" + matched["rule_id"] + "`"
                    for matched in row["matches"]
                )
                + "\n\n"
            )

        for title, key in (
            ("Failed stage", "failed_stage"),
            ("Explanation", "explanation"),
            ("Uncertainty", "uncertainty"),
            ("Proposed action", "proposed_action"),
        ):
            report += f"## {title}\n\n{finding[key]}\n\n"

        report += "## Evidence\n\n" + evidence_lines(finding) + "\n"
        target = reports / f"{row['id']}.md"
        
        target.with_suffix(".tmp").write_text(report)
        target.with_suffix(".tmp").replace(target)
