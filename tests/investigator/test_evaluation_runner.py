"""Verify the live runner's collector boundary and single-attempt behavior."""

import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

from evals import run_live_cases as runner
from investigator.collector.configure import write_definition
from investigator.collector.ledger import claim, connect, finish
from investigator.runtime.deploy import PROJECT_ROOT

from .test_collector import RUN, event, finding, ingest
from .test_executor import CONFIG
from .test_observation_rules import definition


class EvaluationRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.findings = self.root / "findings"
        self.findings.mkdir()
        self.config = self.root / "executor.json"
        self.config.write_text(json.dumps(CONFIG))
        self.observations = self.root / "operator-rules.yaml"
        write_definition(definition(), self.observations)
        self.case = {"case": "malformed", "run_id": RUN}
        self.database = self.findings / "incidents.sqlite3"

        with closing(connect(self.database)) as ledger:
            ingest(ledger, [event()], 10000)

        self.report = finding()
        self.report["evidence"][0].update(sha256="a" * 64, session_id="test-session")
        self.report["evidence_warnings"] = []

    def run_case(self) -> dict[str, Any]:
        return runner.run_case(
            self.case,
            config=self.config,
            observations=self.observations,
            findings=self.findings,
            code=PROJECT_ROOT,
            collector_python=Path(sys.executable),
        )

    def start_attempt(self) -> None:
        with closing(connect(self.database)) as ledger:
            self.assertIsNotNone(claim(ledger, 10000, incident_id=RUN))

    def finish_attempt(self, error: str | None = None) -> None:
        with closing(connect(self.database)) as ledger:
            finish(
                ledger,
                RUN,
                finding=self.report if error is None else None,
                error=error,
                now=10000,
            )

    def test_launch_validates_against_the_actual_collector_cli(self) -> None:
        execute = subprocess.run

        def collect(
            command: list[str],
            *,
            cwd: Path,
            capture_output: bool,
            text: bool,
            timeout: int,
        ) -> subprocess.CompletedProcess[str]:
            result = execute(
                [*command, "--validate-only"],
                cwd=cwd,
                capture_output=capture_output,
                text=text,
                timeout=min(timeout, 20),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("patterns are valid", result.stdout)
            self.start_attempt()
            self.finish_attempt()
            return result

        with patch.object(runner.subprocess, "run", side_effect=collect):
            result = self.run_case()

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["attempts"], 1)
        self.assertTrue(all(result["checks"].values()))

    def test_wait_covers_default_source_interval_without_extra_attempts(
        self,
    ) -> None:
        elapsed = 0.0

        def wait(seconds: float) -> None:
            nonlocal elapsed
            elapsed += seconds

        def collect(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            if elapsed >= 120:
                self.start_attempt()
                self.finish_attempt()

            return subprocess.CompletedProcess([], 0)

        with (
            patch.object(runner.subprocess, "run", side_effect=collect),
            patch.object(runner.time, "sleep", side_effect=wait),
        ):
            result = self.run_case()

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["attempts"], 1)
        self.assertGreaterEqual(elapsed, 120)

    def test_in_progress_attempt_finishes_before_scoring(self) -> None:
        def collect(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            self.start_attempt()
            return subprocess.CompletedProcess([], 0)

        def wait(_seconds: float) -> None:
            self.finish_attempt()

        with (
            patch.object(runner.subprocess, "run", side_effect=collect) as run,
            patch.object(
                runner.time,
                "sleep",
                side_effect=wait,
            ) as sleep,
        ):
            result = self.run_case()

        self.assertEqual(result["status"], "complete")
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(sleep.call_count, 1)

    def test_failed_attempt_is_returned_without_retrying(self) -> None:
        def collect(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            self.start_attempt()
            self.finish_attempt(error="ValueError")
            return subprocess.CompletedProcess([], 0)

        with patch.object(runner.subprocess, "run", side_effect=collect) as run:
            result = self.run_case()

        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["checks"], {})
        run.assert_called_once()

    def test_existing_attempt_is_rejected_before_collector_launch(
        self,
    ) -> None:
        self.start_attempt()

        with patch.object(runner.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "already consumed"):
                self.run_case()

        run.assert_not_called()

    def test_unavailable_attempt_stops_with_budget_accounting_intact(
        self,
    ) -> None:
        with (
            patch.object(
                runner.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as run,
            patch.object(runner.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "No completed attempt"):
                self.run_case()

        self.assertEqual(run.call_count, runner.MAX_POLLS)

        with closing(sqlite3.connect(self.database)) as ledger:
            self.assertEqual(
                ledger.execute("SELECT COUNT(*) FROM attempts").fetchone()[0],
                0,
            )

    def test_missing_ledger_is_not_created_by_read(self) -> None:
        missing = self.root / "missing"
        self.assertIsNone(runner.read_incident(missing, RUN))
        self.assertFalse(missing.exists())

    def test_cli_uses_current_host_paths_and_private_result_file(self) -> None:
        manifest = self.root / "cases.json"
        manifest.write_text(json.dumps({"cases": [self.case]}))
        completed = {"status": "complete", "checks": {"pass": True}}

        with (
            patch("sys.argv", ["run_live_cases", "--manifest", str(manifest)]),
            patch.object(runner, "run_case", return_value=completed) as run,
            redirect_stdout(io.StringIO()),
        ):
            runner.main()

        run.assert_called_once_with(
            self.case,
            config=Path("/etc/hermes-lab/executor.json"),
            observations=Path("/srv/hermes-lab/observations.yaml"),
            findings=Path("/srv/hermes-lab/findings"),
            code=Path("/srv/hermes-lab/code"),
            collector_python=runner.COLLECTOR_PYTHON,
        )
        output = self.root / "results.json"
        self.assertEqual(json.loads(output.read_text()), {"cases": [completed]})
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
