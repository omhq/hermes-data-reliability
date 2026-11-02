import copy
import json
import re
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import yaml

from evals.pipeline_fixtures import PipelineFixture
from incidents.database import connect
from incidents.queue import budget_status
from investigator.capabilities.pipeline_health import monitor, scheduler
from investigator.capabilities.pipeline_health.configure import install
from investigator.capabilities.pipeline_health.definition import (
    InvalidDefinition,
    parse,
)
from investigator.capabilities.pipeline_health.evaluator import Capability
from investigator.capabilities.pipeline_health.extension import Extension, load
from investigator.capabilities.pipeline_health.history import History
from investigator.capabilities.pipeline_health.tools import register
from investigator.capabilities.pipeline_health.tools.client import (
    validate_response,
)
from investigator.runtime.investigation_worker import investigate_next
from investigator.runtime.sessions import InvestigationSession
from tool_boundary.executor import Executor
from tool_boundary.models import ApprovedAction, PolicyDenied
from tool_boundary.process import Deadline
from tool_boundary.wire import MAX_RESPONSE_BYTES, ExecutionError, encode

ROOT = Path(__file__).resolve().parents[1]
CONFIG: dict[str, Any] = {
    "tool_packages": [
        "hermes-lab-aws",
        "hermes-cloudwatch",
        "hermes-pipeline-health",
    ],
    "health_resources": {
        "pipeline_output": {
            "adapter": "s3_object",
            "bucket": "example-lab-bucket",
            "region": "us-east-1",
            "prefix": "output/",
        },
        "pipeline_input": {
            "adapter": "s3_object",
            "bucket": "example-lab-bucket",
            "region": "us-east-1",
            "prefix": "input/",
        },
    },
    "cloudtrail_resource_names": [],
    "account_id": "111111111111",
    "region": "us-east-1",
    "bucket": "example-lab-bucket",
    "log_group": "/aws/lambda/example-loader",
}


class PipelineHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.now = int(datetime(2026, 10, 30, 9, 20, tzinfo=UTC).timestamp())
        guide = ROOT.parents[2] / "docs/OBSERVATION_CONFIG.md"
        section = guide.read_text().split(
            "## Pipeline delivery expectations", 1
        )[1]
        self.examples = [
            yaml.safe_load(block)
            for block in re.findall(r"```yaml\n(.*?)\n```", section, re.S)
        ]
        self.definition = self.examples[0]
        self.source = self.directory / "definition.yaml"
        self.source.write_text(yaml.safe_dump(self.definition))
        self.pipelines = parse(self.definition)
        self.history = History(self.directory / "health.sqlite3")
        self.capability = Capability(self.pipelines, self.history, CONFIG)
        self.backend = Mock()
        self.backend.authenticate.return_value = {"credential": "host-only"}
        self.backend.execute.return_value = (
            self.listing(),
            None,
        )
        self.audit = Mock()
        self.executor = Executor(
            CONFIG,
            self.backend,
            self.audit,
            clock=lambda: self.now * 1000,
            extension=Extension(CONFIG, self.capability),
        )
        self.specification = {
            "pipeline_id": "daily_orders",
            "expectation_id": "output_arrived",
            "occurrence": "2026-10-30T09:00:00Z",
            "revision": self.pipelines.pipelines[0].revision,
            "source_scope": self.capability.scopes[0],
        }

    def listing(
        self, keys: tuple[str, ...] = (), *, complete: bool = True
    ) -> dict[str, Any]:
        return {
            "Name": CONFIG["bucket"],
            "Prefix": "output/orders/2026-10-30.json",
            "Contents": [{"Key": key, "Size": 10} for key in keys],
            "KeyCount": len(keys),
            "IsTruncated": not complete,
        }

    def opened(self) -> str:
        return self.executor.register(self.specification, 1000)["context_id"]

    def check(
        self,
        token: str,
        tool: str = "check_pipeline_expectations",
        **arguments: object,
    ) -> dict[str, Any]:
        return self.executor.execute(
            {
                "context_id": token,
                "request": {
                    "tool": tool,
                    "arguments": {
                        "pipeline_id": self.specification["pipeline_id"],
                        "expectation_id": self.specification["expectation_id"],
                        "occurrence": self.specification["occurrence"],
                        **arguments,
                    },
                },
            }
        )

    def control(
        self, path: str | Path, request: dict[str, Any]
    ) -> dict[str, Any]:
        operation = request["operation"]

        if operation == "poll_pipeline_health":
            response = monitor.poll(self.executor, self.capability)
        elif operation == "acknowledge_pipeline_health":
            response = monitor.acknowledge(self.capability, request)
        elif operation == "open":
            response = self.executor.register(request["context"], 1000)
        elif operation == "close":
            response = self.executor.revoke(request["context_id"], 1000)
        elif operation == "check":
            response = self.executor.status(request["context_id"], 1000)
        elif operation == "evidence":
            response = self.executor.evidence(
                request["context_id"], request["citations"], 1000
            )
        else:
            raise AssertionError(operation)

        return {"ok": True, **response}

    def test_pending_does_not_read_aws_or_queue_incident(self) -> None:
        self.now -= 15 * 60
        result = self.check(self.opened())

        self.assertEqual(result["data"]["status"], "pending")
        self.backend.execute.assert_not_called()
        self.assertEqual(self.capability.pending(), [])

    def test_complete_exact_listing_and_neighbor_keys(self) -> None:
        self.backend.execute.return_value = (
            self.listing(("output/orders/2026-10-30.json.backup",)),
            None,
        )
        result = self.check(self.opened())

        self.assertEqual(result["data"]["status"], "fail")
        self.assertTrue(result["references"])
        validate_response(result, {})
        action = self.backend.execute.call_args.args[0]
        self.assertEqual(action.operation, "ListObjectsV2")
        self.assertEqual(
            action.parameters["Prefix"], result["data"]["target"]["key"]
        )

    def test_presence_is_sufficient_even_on_partial_listing(self) -> None:
        self.backend.execute.return_value = (
            self.listing(("output/orders/2026-10-30.json",), complete=False),
            None,
        )
        result = self.check(self.opened())

        self.assertEqual(result["data"]["status"], "pass")
        self.assertIn("content validity", result["data"]["limitation"])
        self.assertEqual(self.capability.pending(), [])

    def test_partial_failed_or_malformed_listing_is_unknown(self) -> None:
        responses: list[object] = [
            (self.listing(complete=False), None),
            ({"Contents": []}, None),
            ({**self.listing(), "Name": "other-bucket"}, None),
            ExecutionError("aws_read_failed"),
        ]

        for response in responses:
            with self.subTest(response_type=type(response).__name__):
                if isinstance(response, Exception):
                    self.backend.execute.side_effect = response
                else:
                    self.backend.execute.side_effect = None
                    self.backend.execute.return_value = response

                result = self.check(self.opened())
                self.assertTrue(result["ok"])
                self.assertEqual(result["data"]["status"], "unknown")

    def test_scope_budgets_and_receipt_ownership(self) -> None:
        token = self.opened()
        result = self.check(token)
        citation = {
            "evidence_id": result["evidence_id"],
            "reference": result["references"][0]["reference"],
        }
        self.assertEqual(
            self.executor.evidence(token, [citation])["receipts"][0]["run_id"],
            result["data"]["incident_id"],
        )

        with self.assertRaises(PolicyDenied):
            self.executor.evidence(self.opened(), [citation])

        for arguments in (
            {"occurrence": "2026-10-29"},
            {"pipeline_id": "another_pipeline"},
            {"bucket": "another-bucket"},
        ):
            self.assertFalse(self.check(token, **arguments)["ok"])

        for _ in range(9):
            self.assertTrue(
                self.check(token, "get_pipeline_check_history")["ok"]
            )

        self.assertEqual(self.check(token)["error"], "call_budget_exhausted")

    def test_disabled_capability_and_changed_definition_deny_access(
        self,
    ) -> None:
        config = {**CONFIG, "tool_packages": ["hermes-cloudwatch"]}
        executor = Executor(
            config,
            self.backend,
            self.audit,
            extension=Extension(config, self.capability),
        )

        with self.assertRaises(PolicyDenied):
            executor.register(self.specification, 1000)
        with self.assertRaises(PolicyDenied):
            self.executor.register(
                {**self.specification, "revision": "other"}, 1000
            )

    def test_investigation_listing_withholds_neighboring_objects(self) -> None:
        self.backend.execute.return_value = (
            self.listing(("output/orders/2026-10-30.json.backup",)),
            None,
        )
        response = self.executor.execute(
            {
                "context_id": self.opened(),
                "request": {
                    "tool": "list_s3_objects",
                    "arguments": {
                        "bucket": CONFIG["bucket"],
                        "prefix": "output/orders/2026-10-30.json",
                    },
                },
            }
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["data"]["objects"], [])
        self.assertTrue(response["truncated"])
        self.assertNotIn(".backup", json.dumps(response))

    def test_outbox_survives_restart_and_late_presence_is_recorded(
        self,
    ) -> None:
        self.check(self.opened())
        restarted = History(self.history.path)
        self.assertEqual(len(self.capability.pending()), 1)
        identifier = self.capability.pending()[0]["incident_id"]
        restarted.acknowledge(identifier)
        self.now += 120
        self.backend.execute.return_value = (
            self.listing(("output/orders/2026-10-30.json",)),
            None,
        )
        self.check(self.opened())
        history = restarted.read(identifier)

        self.assertEqual(history["observations"][0]["status"], "pass")
        self.assertIsNotNone(history["first_failure_at"])
        self.assertEqual(history["recovered_at"], self.now)
        self.assertEqual(self.capability.pending(), [])

    def test_scheduler_commits_before_ack_and_deduplicates(self) -> None:
        with patch.object(scheduler, "call", side_effect=self.control):
            scheduler.tick(
                Path("unused"), self.directory, CONFIG["account_id"]
            )
            scheduler.tick(
                Path("unused"), self.directory, CONFIG["account_id"]
            )

        with closing(
            connect(self.directory / "incidents.sqlite3")
        ) as database:
            rows = database.execute("SELECT * FROM incidents").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "health")
            self.assertEqual(budget_status(database, self.now)["used"], 0)

        self.assertEqual(self.capability.pending(), [])

    def test_scheduler_failed_commit_keeps_outbox(self) -> None:
        with (
            patch.object(scheduler, "call", side_effect=self.control),
            patch.object(scheduler, "enqueue", side_effect=RuntimeError),
            self.assertRaises(RuntimeError),
        ):
            scheduler.tick(
                Path("unused"), self.directory, CONFIG["account_id"]
            )

        self.assertEqual(len(self.capability.pending()), 1)

    def test_unsuccessful_recheck_reoffers_same_incident_after_reset(
        self,
    ) -> None:
        first = self.check(self.opened())["data"]["incident_id"]
        self.history.acknowledge(first)
        self.now += 60
        second = self.check(self.opened())["data"]["incident_id"]

        self.assertEqual(first, second)
        self.assertEqual(
            self.capability.pending()[0]["incident_id"],
            first,
        )

    def test_history_and_outbox_are_isolated_when_authorized_source_changes(
        self,
    ) -> None:
        self.check(self.opened())

        for changed in (
            {"account_id": "222222222222"},
            {"region": "us-west-2"},
            {"bucket": "another-example-bucket"},
        ):
            with self.subTest(changed_field=next(iter(changed))):
                config = {**CONFIG, **changed}
                config["health_resources"] = {
                    name: {
                        **profile,
                        "bucket": config["bucket"],
                        "region": config["region"],
                    }
                    for name, profile in CONFIG["health_resources"].items()
                }
                capability = Capability(
                    self.pipelines,
                    self.history,
                    config,
                )
                executor = Executor(
                    config,
                    self.backend,
                    self.audit,
                    clock=lambda: self.now * 1000,
                    extension=Extension(config, capability),
                )
                with self.assertRaises(PolicyDenied):
                    executor.register(self.specification, 1000)

                specification = {
                    **self.specification,
                    "source_scope": capability.scopes[0],
                }
                token = executor.register(specification, 1000)["context_id"]
                response = executor.execute(
                    {
                        "context_id": token,
                        "request": {
                            "tool": "get_pipeline_check_history",
                            "arguments": {
                                "pipeline_id": "daily_orders",
                                "expectation_id": "output_arrived",
                                "occurrence": "2026-10-30T09:00:00Z",
                            },
                        },
                    }
                )
                self.assertTrue(response["ok"])
                self.assertEqual(response["data"]["observations"], [])
                self.assertEqual(response["data"]["coverage"], "unknown")
                self.assertEqual(capability.pending(), [])

    def test_polling_is_bounded_and_new_deliveries_are_not_starved(
        self,
    ) -> None:
        pipeline = self.definition["pipelines"]["daily_orders"]
        definition = {
            **self.definition,
            "pipelines": {f"pipeline_{index}": pipeline for index in range(8)},
        }
        self.capability.pipelines = {
            item.name: item for item in parse(definition).pipelines
        }

        for _ in range(8):
            before = self.backend.execute.call_count
            monitor.poll(self.executor, self.capability)
            self.assertLessEqual(self.backend.execute.call_count - before, 8)
            self.now += 15

        with closing(self.history.connect()) as database:
            self.assertEqual(
                database.execute(
                    "SELECT COUNT(DISTINCT pipeline) FROM health_deliveries"
                ).fetchone()[0],
                8,
            )

    def test_yaml_is_strict_and_compiles_for_standard_library_executor(
        self,
    ) -> None:
        destination = self.directory / "pipeline-health.yaml"
        compiled = self.directory / "pipeline-health.json"
        self.source.write_text(
            "# "
            + "example comment " * 5000
            + "\n"
            + yaml.safe_dump(self.definition)
        )
        installed = install(self.source, destination, compiled, CONFIG)
        self.assertEqual(
            parse(installed), parse(json.loads(compiled.read_text()))
        )

        for mutate in (
            "identity",
            "expectations",
            "schedule",
            "investigation",
        ):
            invalid = copy.deepcopy(self.definition)
            invalid["pipelines"]["daily_orders"][mutate] = "unsupported"

            with self.assertRaises(InvalidDefinition):
                parse(invalid)

        source = self.directory / "bad.yaml"
        source.write_text("version: 1\nversion: 1\npipelines: {}\n")

        with self.assertRaises(ValueError):
            install(source, destination, compiled, CONFIG)

    def test_native_plugin_registers_only_the_three_capability_tools(
        self,
    ) -> None:
        context = Mock()
        register(context)
        names = {
            call.kwargs["name"]
            for call in context.register_tool.call_args_list
        }
        self.assertEqual(
            names,
            {
                "list_pipeline_expectations",
                "check_pipeline_expectations",
                "get_pipeline_check_history",
            },
        )

    def assert_central_worker(self) -> None:
        with patch.object(scheduler, "call", side_effect=self.control):
            scheduler.tick(
                Path("unused"), self.directory, CONFIG["account_id"]
            )

        def session(
            specification: dict[str, Any], *, control_socket: Path
        ) -> InvestigationSession:
            return InvestigationSession(specification, request=self.control)

        def model(active: InvestigationSession, prompt: str) -> str:
            self.assertIn("unmet", prompt)
            settings = self.capability.pipelines[
                self.specification["pipeline_id"]
            ].investigation
            self.assertIn(settings["instructions"], prompt)
            self.assertIn(json.dumps(settings["objective"]), prompt)
            result = self.check(active.token)
            finding = {
                "run_id": result["data"]["incident_id"],
                "summary": "Expected output is absent",
                "failed_stage": "delivery_expectation",
                "explanation": (
                    "The exact output key is absent after the deadline."
                ),
                "uncertainty": "The underlying cause is unverified.",
                "proposed_action": "Inspect the upstream delivery status.",
                "evidence": [
                    {
                        "evidence_id": result["evidence_id"],
                        "reference": result["references"][0]["reference"],
                    }
                ],
            }
            return json.dumps(
                {
                    "type": "result",
                    "exit_code": 0,
                    "text": json.dumps(finding),
                }
            )

        with (
            closing(connect(self.directory / "incidents.sqlite3")) as database,
            patch(
                "investigator.capabilities.pipeline_health.investigation.InvestigationSession",
                side_effect=session,
            ),
            patch(
                "investigator.capabilities.pipeline_health.investigation.run_once",
                side_effect=model,
            ),
        ):
            investigate_next(database, CONFIG)
            row = database.execute("SELECT * FROM incidents").fetchone()
            self.assertEqual(row["status"], "complete", row["last_error"])
            self.assertEqual(row["attempts"], 1)
            finding = json.loads(row["finding"])
            self.assertTrue(finding["evidence"][0]["session_id"])
            self.assertEqual(finding["evidence"][0]["run_id"], row["id"])

    def test_each_incident_saves_its_configured_investigation(self) -> None:
        definition = copy.deepcopy(self.definition)
        second = copy.deepcopy(definition["pipelines"]["daily_orders"])
        second["investigation"] = {
            "objective": "Compare upstream delivery and downstream processing",
            "instructions": "Report competing causes and a safe next check.",
        }
        definition["pipelines"]["other_delivery"] = second
        expected = {
            name: copy.deepcopy(pipeline["investigation"])
            for name, pipeline in definition["pipelines"].items()
        }
        self.capability = Capability(parse(definition), self.history, CONFIG)
        self.executor = Executor(
            CONFIG,
            self.backend,
            self.audit,
            clock=lambda: self.now * 1000,
            extension=Extension(CONFIG, self.capability),
        )
        monitor.poll(self.executor, self.capability)
        second["investigation"]["instructions"] = "Edited after detection."

        with (
            patch.object(scheduler, "call", side_effect=self.control),
            closing(connect(self.directory / "incidents.sqlite3")) as database,
        ):
            scheduler.tick(
                Path("unused"), self.directory, CONFIG["account_id"]
            )
            rows = database.execute(
                """
                SELECT incidents.source_id, observation_definitions.definition
                FROM incidents JOIN observation_definitions
                ON incidents.definition_revision =
                   observation_definitions.revision
                """
            ).fetchall()

        self.assertEqual(len(rows), 2)

        for row in rows:
            self.assertEqual(
                json.loads(row[1])["investigation"], expected[row[0]]
            )

        changed = parse(definition)
        original = self.capability.pipelines["other_delivery"]
        self.assertNotEqual(changed.pipelines[1].revision, original.revision)

        response = self.executor.execute(
            {
                "context_id": self.executor.register({"profile": "lab"})[
                    "context_id"
                ],
                "request": {
                    "tool": "list_pipeline_expectations",
                    "arguments": {"pipeline_id": "other_delivery"},
                },
            }
        )
        self.assertTrue(response["ok"], response)
        self.assertEqual(
            response["data"]["pipelines"][0]["investigation"],
            expected["other_delivery"],
        )

    def use_lab(self, milliseconds: bool = False) -> PipelineFixture:
        fixture = PipelineFixture()
        suffix = ".000Z" if milliseconds else "Z"
        fixture.produce(occurrence="2026-10-30T09:00:43" + suffix)
        fixture.produce(occurrence="2026-10-30T09:01:43" + suffix)
        definition = copy.deepcopy(self.examples[1])
        definition["pipelines"]["synthetic_loader"]["identity"][
            "timestamp_format"
        ] = "utc_milliseconds" if milliseconds else "utc_seconds"
        self.capability = Capability(parse(definition), self.history, CONFIG)
        self.executor = Executor(
            CONFIG,
            self.backend,
            self.audit,
            clock=lambda: self.now * 1000,
            extension=Extension(CONFIG, self.capability),
        )
        pipeline = self.capability.pipelines["synthetic_loader"]
        expectation = pipeline.expectations[0]
        occurrence = pipeline.occurrence("2026-10-30T09:00:43Z")
        self.now = pipeline.deadline(occurrence)
        self.specification = {
            "pipeline_id": pipeline.name,
            "expectation_id": expectation.name,
            "occurrence": occurrence.identifier,
            "revision": pipeline.revision,
            "source_scope": self.capability.target(
                pipeline, expectation, occurrence
            ).scope,
        }

        def execute(
            action: ApprovedAction,
            environment: dict[str, str],
            deadline: Deadline,
        ) -> tuple[dict[str, Any], bytes | None]:
            deadline.remaining()

            if action.operation == "GetObject":
                payload = json.dumps(
                    fixture.objects[action.parameters["Key"]]
                ).encode()
                return {
                    "ContentRange": (
                        f"bytes 0-{len(payload) - 1}/{len(payload)}"
                    )
                }, payload

            self.assertEqual(action.operation, "ListObjectsV2")
            prefix = action.parameters["Prefix"]
            keys = [key for key in fixture.objects if key.startswith(prefix)]
            return {
                "Name": CONFIG["bucket"],
                "Prefix": prefix,
                "Contents": [{"Key": key, "Size": 10} for key in keys],
                "KeyCount": len(keys),
                "IsTruncated": False,
            }, None

        self.backend.execute.side_effect = execute
        return fixture

    def test_large_configured_prompts_are_delivered_in_bounded_batches(
        self,
    ) -> None:
        definition = copy.deepcopy(self.definition)
        pipeline = definition["pipelines"]["daily_orders"]
        pipeline["investigation"] = {
            "objective": "é" * 3999,
            "instructions": "é" * 7999,
        }
        definition["pipelines"] = {
            f"delivery_{index}": copy.deepcopy(pipeline) for index in range(8)
        }
        self.source.write_text(yaml.safe_dump(definition))
        compiled = self.directory / "compiled.json"
        install(
            self.source,
            self.directory / "installed.yaml",
            compiled,
            CONFIG,
        )
        self.assertGreater(compiled.stat().st_size, 65536)
        collection_plan = self.directory / "collection.json"
        collection_plan.write_text(
            json.dumps(
                {
                    "consumers": {"pipeline_health": {}},
                    "feeds": {},
                    "consumer_revisions": {
                        "pipeline_health": parse(definition).consumer.revision
                    },
                }
            )
        )
        extension = load(
            {
                **CONFIG,
                "collection_plan": str(collection_plan),
                "observation_store": str(
                    self.directory / "observations.sqlite3"
                ),
            },
            self.directory,
            compiled,
        )
        self.capability = extension.capability
        self.executor = Executor(
            CONFIG,
            self.backend,
            self.audit,
            clock=lambda: self.now * 1000,
            extension=extension,
        )
        response = monitor.poll(self.executor, self.capability)
        self.assertGreater(len(response["events"]), 0)
        self.assertLess(len(response["events"]), 8)
        self.assertLessEqual(
            len(encode({"ok": True, **response})) + 1, MAX_RESPONSE_BYTES
        )
        self.assertEqual(len(self.capability.pending()), 8)
        ticks = 0

        with patch.object(scheduler, "call", side_effect=self.control):
            while self.capability.pending():
                self.assertLess(ticks, 8)
                scheduler.tick(
                    Path("unused"), self.directory, CONFIG["account_id"]
                )
                ticks += 1

        with closing(
            connect(self.directory / "incidents.sqlite3")
        ) as database:
            self.assertEqual(
                database.execute("SELECT COUNT(*) FROM incidents").fetchone()[
                    0
                ],
                8,
            )

    def test_daily_incident_uses_central_worker(self) -> None:
        self.assert_central_worker()

    def test_minute_lab_incident_uses_same_central_worker(self) -> None:
        self.use_lab()
        self.assert_central_worker()

    def test_real_pipeline_missing_then_late_output_for_both_timestamp_formats(
        self,
    ) -> None:
        for milliseconds in (False, True):
            with self.subTest(milliseconds=milliseconds):
                fixture = self.use_lab(milliseconds)
                self.now -= 1
                pending = self.check(self.opened())
                self.assertEqual(pending["data"]["status"], "pending")
                before = self.backend.execute.call_count
                self.now += 1
                missing = self.check(self.opened())
                self.assertTrue(missing["ok"], missing)
                self.assertEqual(missing["data"]["status"], "fail")
                self.assertEqual(self.backend.execute.call_count - before, 1)
                self.assertFalse(
                    any(
                        item.get("level") == "ERROR"
                        for item in fixture.entries
                    )
                )
                delivery_id = missing["data"]["delivery_id"]
                self.assertIn(f"input/{delivery_id}.json", fixture.objects)
                self.assertEqual(
                    missing["data"]["target"]["key"],
                    f"output/{delivery_id}.json",
                )
                fixture.process([fixture.message(f"input/{delivery_id}.json")])
                self.now += 60
                present = self.check(self.opened())
                self.assertEqual(present["data"]["status"], "pass")
                history = self.check(
                    self.opened(), "get_pipeline_check_history"
                )["data"]
                self.assertEqual(
                    [item["status"] for item in history["observations"][:3]],
                    ["pass", "fail", "pending"],
                )
                self.assertEqual(history["recovered_at"], self.now)

    def test_supporting_input_reads_use_exact_delivery_and_receipts(
        self,
    ) -> None:
        fixture = self.use_lab()
        record = fixture.entries[0]
        key = f"input/{record['run_id']}.json"
        token = self.opened()

        def read(selected_key: str) -> dict[str, Any]:
            return self.executor.execute(
                {
                    "context_id": token,
                    "request": {
                        "tool": "read_s3_object",
                        "arguments": {
                            "bucket": CONFIG["bucket"],
                            "key": selected_key,
                        },
                    },
                }
            )

        response = read(key)
        self.assertTrue(response["ok"], response)
        self.assertEqual(
            response["references"][0]["reference"],
            f"s3://{CONFIG['bucket']}/{key}",
        )
        self.assertFalse(read(key + ".backup")["ok"])
        self.assertFalse(read("input/another-delivery.json")["ok"])

    def test_off_schedule_occurrences_and_changed_scopes_are_denied(
        self,
    ) -> None:
        self.use_lab()

        for changes in (
            {"occurrence": "2026-10-30T09:00:00Z"},
            {"expectation_id": "not_configured"},
            {"source_scope": "0" * 64},
        ):
            with self.assertRaises(PolicyDenied):
                self.executor.register({**self.specification, **changes}, 1000)

        token = self.opened()
        self.now += 301
        self.assertEqual(self.check(token)["error"], "context_expired")

    def test_different_expectations_have_independent_history(self) -> None:
        definition = copy.deepcopy(self.definition)
        definition["pipelines"]["daily_orders"]["expectations"].append(
            {
                "id": "other_delivery",
                "check": "object_exists",
                "source": "orders_output",
            }
        )
        capability = Capability(parse(definition), self.history, CONFIG)
        due = capability.due(self.now)
        self.assertEqual(len(due), 2)
        self.assertNotEqual(
            capability.identifier(*due[0]), capability.identifier(*due[1])
        )
        self.assertEqual(
            capability.due(self.now)[0][2].identifier, "2026-10-30T09:00:00Z"
        )

    def test_configuration_rejects_ambiguous_identities_and_unapproved_sources(
        self,
    ) -> None:
        self.use_lab()
        pipeline = self.capability.pipelines["synthetic_loader"]
        definition = {
            "version": 4,
            "consumer": {
                "id": "pipeline_health",
                "enabled": True,
                "subscriptions": {},
            },
            "sources": self.definition["sources"],
            "pipelines": {
                "minute_orders": {
                    "schedule": {
                        "kind": "interval",
                        "every": "1m",
                        "anchor": "2026-10-30T09:00:43Z",
                    },
                    "identity": {"kind": "date"},
                    "grace_period": "5m",
                    "investigation": self.definition["pipelines"][
                        "daily_orders"
                    ]["investigation"],
                    "expectations": [
                        {
                            "id": "check_output",
                            "check": "object_exists",
                            "source": "orders_output",
                        }
                    ],
                }
            },
        }
        self.assertEqual(pipeline.schedule.period_seconds, 60)

        with self.assertRaises(InvalidDefinition):
            parse(definition)

        invalid = copy.deepcopy(self.definition)
        invalid["sources"]["orders_output"]["resource"] = "unapproved"

        with self.assertRaises(InvalidDefinition):
            Capability(parse(invalid), self.history, CONFIG)

    def test_poll_shares_time_budget_and_closes_every_context(self) -> None:
        self.use_lab()

        with patch.object(
            monitor.time, "monotonic", side_effect=[100.0, 126.0]
        ):
            response = monitor.poll(self.executor, self.capability)

        self.assertEqual(response["checks_completed"], 0)
        self.assertEqual(self.executor.sessions, {})
        self.backend.execute.assert_not_called()

    def test_minute_poll_bounds_catchup_and_retries(self) -> None:
        self.use_lab()
        self.now += 20 * 60
        first = monitor.poll(self.executor, self.capability)
        second = monitor.poll(self.executor, self.capability)
        self.assertEqual(first["checks_completed"], 8)
        self.assertEqual(second["checks_completed"], 8)
        self.assertEqual(self.backend.execute.call_count, 16)
        self.assertEqual(self.executor.sessions, {})

        with closing(self.history.connect()) as database:
            distinct = database.execute(
                "SELECT COUNT(*) FROM health_deliveries"
            ).fetchone()[0]

        self.assertEqual(distinct, 16)
        self.now += 60
        third = monitor.poll(self.executor, self.capability)
        self.assertEqual(third["checks_completed"], 8)
        self.assertLessEqual(self.backend.execute.call_count, 24)

    def test_removed_or_changed_definitions_cannot_offer_old_incidents(
        self,
    ) -> None:
        self.check(self.opened())
        self.assertEqual(len(self.capability.pending()), 1)
        changed = copy.deepcopy(self.definition)
        changed["sources"]["orders_output"]["key_template"] = (
            "replacement/{delivery_id}.json"
        )
        capability = Capability(parse(changed), self.history, CONFIG)
        self.assertEqual(capability.pending(), [])
        capability = Capability(
            parse(
                {
                    "version": 4,
                    "consumer": {
                        "id": "pipeline_health",
                        "enabled": True,
                        "subscriptions": {},
                    },
                    "sources": {},
                    "pipelines": {},
                }
            ),
            self.history,
            CONFIG,
        )
        self.assertEqual(capability.pending(), [])

    def test_discovery_resolves_exact_supporting_sources(self) -> None:
        self.use_lab()
        response = self.executor.execute(
            {
                "context_id": self.opened(),
                "request": {
                    "tool": "list_pipeline_expectations",
                    "arguments": {},
                },
            }
        )
        self.assertTrue(response["ok"], response)
        self.assertEqual(
            set(response["data"]["sources"]), {"lab_input", "lab_output"}
        )
        self.assertEqual(len(response["data"]["targets"]), 2)
        self.assertEqual(response["references"], [])
        self.backend.execute.assert_not_called()

    def test_documented_lab_configuration_compiles(self) -> None:
        definition = copy.deepcopy(self.examples[1])
        source = self.directory / "source.yaml"
        source.write_text(yaml.safe_dump(definition))
        installed = install(
            source,
            self.directory / "installed.yaml",
            self.directory / "compiled.json",
            CONFIG,
        )
        self.assertEqual(installed, definition)
        invalid = copy.deepcopy(CONFIG)
        invalid["health_resources"].pop("pipeline_output")

        with self.assertRaises(InvalidDefinition):
            install(
                source,
                self.directory / "installed.yaml",
                self.directory / "compiled.json",
                invalid,
            )

        self.assertEqual(
            json.loads((self.directory / "compiled.json").read_text()),
            definition,
        )

    def test_timestamp_identity_supports_direct_keys(self) -> None:
        fixture = self.use_lab()
        definition = copy.deepcopy(self.examples[1])
        definition["pipelines"]["synthetic_loader"]["identity"] = {
            "kind": "timestamp",
            "timestamp_format": "utc_seconds",
        }
        self.capability = Capability(parse(definition), self.history, CONFIG)
        self.executor = Executor(
            CONFIG,
            self.backend,
            self.audit,
            clock=lambda: self.now * 1000,
            extension=Extension(CONFIG, self.capability),
        )
        self.specification["revision"] = self.capability.pipelines[
            "synthetic_loader"
        ].revision
        key = "output/2026-10-30T09:00:43Z.json"
        fixture.objects[key] = {"loaded": True}
        token = self.opened()
        result = self.check(token)
        self.assertEqual(result["data"]["status"], "pass")
        self.assertEqual(result["data"]["target"]["key"], key)
        result = self.executor.execute(
            {
                "context_id": token,
                "request": {
                    "tool": "read_s3_object",
                    "arguments": {"bucket": CONFIG["bucket"], "key": key},
                },
            }
        )
        self.assertTrue(result["ok"], result)

    def test_observation_times_are_explicit_utc_and_saved_times_stay_intact(
        self,
    ) -> None:
        result = self.check(self.opened())["data"]
        self.assertEqual(
            result["timing"],
            {
                "scheduled_at_utc": "2026-10-30T09:00:00Z",
                "deadline_utc": "2026-10-30T09:10:00Z",
                "observed_at_utc": "2026-10-30T09:20:00Z",
            },
        )
        self.assertEqual(result["observed_at"], self.now)
        history = self.check(self.opened(), "get_pipeline_check_history")[
            "data"
        ]
        self.assertEqual(
            history["first_failure_at_utc"], "2026-10-30T09:20:00Z"
        )

        with closing(self.history.connect()) as database, database:
            stored = json.loads(
                database.execute(
                    "SELECT result FROM health_observations"
                ).fetchone()[0]
            )
            stored.pop("timing")
            original = json.dumps(stored)
            database.execute(
                "UPDATE health_observations SET result = ?", (original,)
            )

        history = self.check(self.opened(), "get_pipeline_check_history")[
            "data"
        ]
        self.assertEqual(
            history["observations"][0]["timing"]["observed_at_utc"],
            "2026-10-30T09:20:00Z",
        )

        with closing(self.history.connect()) as database:
            self.assertEqual(
                database.execute(
                    "SELECT result FROM health_observations"
                ).fetchone()[0],
                original,
            )


if __name__ == "__main__":
    unittest.main()
