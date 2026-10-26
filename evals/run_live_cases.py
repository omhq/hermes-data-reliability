"""Run explicit evaluation cases through the deployed collector."""

import argparse
import json
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from investigator.cli_output import write_json

from .live_cases import assess_finding

COLLECTOR_PYTHON = Path("/srv/hermes-lab/collector-venv-py314/bin/python")
MAX_POLLS = 15
POLL_WAIT_SECONDS = 15


def read_incident(findings: Path, run_id: str) -> dict[str, Any] | None:
    database_path = findings / "incidents.sqlite3"

    if not database_path.exists():
        return None

    with closing(sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM incidents WHERE id=?", (run_id,)).fetchone()

    return dict(row) if row else None


def run_case(
    case: dict[str, Any],
    *,
    config: Path,
    observations: Path,
    findings: Path,
    code: Path,
    collector_python: Path = COLLECTOR_PYTHON,
) -> dict[str, Any]:
    """Wait for one attempt to finish; never retry an attempted case."""
    run_id = case["run_id"]
    row = read_incident(findings, run_id)

    if row and row["attempts"]:
        raise RuntimeError("This case already consumed an attempt; use fresh fixtures")

    for _ in range(MAX_POLLS):
        if not row or not row["attempts"]:
            result = subprocess.run(
                [
                    str(collector_python),
                    "-m",
                    "investigator.collector.poll",
                    "--config",
                    str(config),
                    "--observations",
                    str(observations),
                    "--findings",
                    str(findings),
                    "--retry-incident",
                    run_id,
                ],
                cwd=code,
                capture_output=True,
                text=True,
                timeout=390,
            )

            if result.returncode:
                raise RuntimeError("Collector command failed; inspect its host status")

            row = read_incident(findings, run_id)

        if row and row["attempts"] and row["status"] != "running":
            break

        # Allow the default two-minute interval and busy collector locks.
        time.sleep(POLL_WAIT_SECONDS)
        row = read_incident(findings, run_id)
    else:
        raise RuntimeError(
            "No completed attempt: check source progress, hourly budget, "
            "or active chat"
        )

    report = json.loads(row["finding"]) if row["finding"] else None

    return {
        "case": case["case"],
        "run_id": run_id,
        "status": row["status"],
        "attempts": row["attempts"],
        "last_error": row["last_error"],
        "checks": assess_finding(case["case"], report) if report else {},
        "evidence_count": len(report["evidence"]) if report else 0,
        "evidence_warnings": report.get("evidence_warnings", []) if report else [],
    }


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--config", type=Path, default=Path("/etc/hermes-lab/executor.json")
    )
    parser.add_argument(
        "--observations",
        type=Path,
        default=Path("/srv/hermes-lab/observations.yaml"),
    )
    parser.add_argument("--collector-python", type=Path, default=COLLECTOR_PYTHON)
    parser.add_argument(
        "--findings", type=Path, default=Path("/srv/hermes-lab/findings")
    )
    parser.add_argument("--code", type=Path, default=Path("/srv/hermes-lab/code"))

    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text())
    results = []
    output = args.manifest.with_name("results.json")

    for case in manifest["cases"]:
        result = run_case(
            case,
            config=args.config,
            observations=args.observations,
            findings=args.findings,
            code=args.code,
            collector_python=args.collector_python,
        )
        results.append(result)
        output.write_text(json.dumps({"cases": results}, indent=2) + "\n")
        output.chmod(0o600)
        write_json(result)

    if any(
        row["status"] != "complete" or not all(row["checks"].values())
        for row in results
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
