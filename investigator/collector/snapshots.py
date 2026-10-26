import re
import json
import subprocess
import tempfile

from pathlib import Path
from typing import Any


def prepare_evidence(row: dict[str, Any], config: dict[str, Any]) -> None:
    """Read AWS on the host and expose bounded snapshots to tools."""
    incident = json.loads(row["evidence"])

    if not re.fullmatch(r"[a-f0-9]{24}", row["id"]):
        raise ValueError("invalid incident ID")

    attempt = incident.get("attempt_id", "")

    if not isinstance(attempt, str) or not re.fullmatch(
        r"[a-zA-Z0-9-]{1,80}", attempt
    ):
        raise ValueError("invalid attempt ID")

    root = Path(config.get("evidence_path", "/srv/hermes-lab/evidence"))

    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o755)

    target = root / row["id"]
    
    target.mkdir(exist_ok=True)
    target.chmod(0o755)

    keys = {
        "input": f"input/{row['id']}.json",
        "log": f"runs/{row['id']}/{attempt}.json",
        "output": f"output/{row['id']}.json",
    }

    for action, key in keys.items():
        exists = True

        if action == "output":
            listed = subprocess.run(
                [
                    "aws",
                    "s3api",
                    "list-objects-v2",
                    "--bucket",
                    config["bucket"],
                    "--prefix",
                    key,
                    "--max-keys",
                    "10",
                    "--region",
                    config["region"],
                    "--output",
                    "json",
                    "--no-cli-pager",
                ],
                capture_output=True,
                text=True,
                timeout=45,
                check=True,
            )
            exists = any(
                item["Key"] == key
                for item in json.loads(listed.stdout).get("Contents", [])
            )

        value = {
            "reference": f"s3://{config['bucket']}/{key}",
            "exists": exists,
        }

        if exists:
            with tempfile.TemporaryDirectory() as directory:
                body = Path(directory) / "body.json"

                subprocess.run(
                    [
                        "aws",
                        "s3api",
                        "get-object",
                        "--bucket",
                        config["bucket"],
                        "--key",
                        key,
                        "--range",
                        "bytes=0-262144",
                        "--region",
                        config["region"],
                        "--output",
                        "json",
                        "--no-cli-pager",
                        str(body),
                    ],
                    capture_output=True,
                    timeout=45,
                    check=True,
                )

                if body.stat().st_size > 262144:
                    raise ValueError("evidence exceeds 256 KiB limit")

                value["data"] = json.loads(body.read_text())

        staged = target / f".{action}.tmp"
        
        staged.write_text(json.dumps(value))
        staged.chmod(0o444)
        staged.replace(target / f"{action}.json")
