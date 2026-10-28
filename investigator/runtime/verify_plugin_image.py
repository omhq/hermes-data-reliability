import json
import os
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

NAMES = {
    "search_run_logs": "aws_cloudwatch",
    "list_run_objects": "hermes_lab_aws",
    "read_run_object": "hermes_lab_aws",
}


def require(condition: object, name: str) -> None:
    if not condition:
        raise RuntimeError(name)


def main() -> None:
    fixture = json.load(sys.stdin)
    home = Path(os.environ["HERMES_HOME"])

    (home / "plugins").mkdir(parents=True)
    (home / "config.yaml").write_text(
        json.dumps(
            {
                "plugins": {
                    "enabled": ["hermes-lab-aws", "hermes-cloudwatch"]
                },
                "security": {"allow_lazy_installs": False},
            }
        )
    )
    (home / "plugins/hermes-lab-aws").symlink_to("/opt/lab-plugin")
    (home / "plugins/hermes-cloudwatch").symlink_to("/opt/cloudwatch-plugin")
    (home / "plugins/hermes-cloudtrail").symlink_to("/opt/cloudtrail-plugin")

    # Hermes provides these modules only in the image. Load them after the
    # isolated profile exists and never use the running profile.
    plugins = import_module("hermes_cli.plugins")
    registry = import_module("tools.registry").registry
    manager = plugins.PluginManager()

    manager.discover_and_load()

    checks = []

    def passed(name: str) -> None:
        checks.append({"check": name, "passed": True})

    for name, toolset in NAMES.items():
        entry = registry.get_entry(name, scope=manager.scope_key)

        require(
            entry is not None and entry.toolset == toolset,
            "native_registration",
        )

    passed("native_plugin_discovery_and_three_registrations")

    require(
        registry.get_entry("lookup_cloudtrail_events", scope=manager.scope_key)
        is None,
        "disabled_cloudtrail_not_registered",
    )
    passed("disabled_cloudtrail_not_registered")

    definitions = registry.get_definitions(set(NAMES), quiet=True)

    require(
        {item["function"]["name"] for item in definitions} == set(NAMES),
        "model_visible_schemas",
    )

    for item in definitions:
        parameters = item["function"]["parameters"]

        require(
            parameters["additionalProperties"] is False
            and "context_id" not in parameters["properties"],
            "private_context_schema",
        )

    passed("bounded_model_schemas_without_context_tokens")

    def dispatch(name: str, **arguments: Any) -> dict[str, Any]:
        result = registry.dispatch(
            name,
            {"run_id": fixture["run_id"], **arguments},
            scope=manager.scope_key,
        )

        if type(result) is not str:
            raise RuntimeError("json_string_result")

        return json.loads(result)

    token = os.environ.pop("HERMES_LAB_CONTEXT_ID")

    require(
        dispatch("read_run_object", kind="input").get("error")
        == "context_required",
        "missing_context",
    )
    passed("missing_context_fails_closed")
    os.environ["HERMES_LAB_CONTEXT_ID"] = token

    for name, arguments, field in (
        ("read_run_object", {"kind": "input"}, "text"),
        ("list_run_objects", {"kind": "input"}, "objects"),
        ("search_run_logs", {"max_events": 2}, "events"),
    ):
        response = dispatch(name, max_bytes=4096, **arguments)

        require(response.get("ok") and response["data"][field], name)
        require(
            response["untrusted_evidence"] is True
            and response["data_bytes"] <= 4096,
            "bounded_evidence_receipt",
        )
        require(token not in json.dumps(response), "private_context_result")
        passed(name + "_live_dispatch")

    response = dispatch(
        "read_run_object",
        kind="log",
        attempt_id=fixture["attempt_id"],
        max_bytes=4096,
    )

    require(
        response.get("ok") and response["data"]["text"], "known_attempt_log"
    )
    passed("known_attempt_log")

    response = dispatch("read_run_object", kind="input", max_bytes=64)

    require(
        response.get("ok")
        and response["truncated"]
        and response["data_bytes"] <= 64,
        "partial_sample",
    )
    passed("bounded_partial_sample")

    for arguments, code in (
        ({"bucket": "other"}, "unexpected_arguments"),
        ({"endpoint": "http://untrusted"}, "unexpected_arguments"),
        ({"context_id": "forged-context"}, "unexpected_arguments"),
        ({"run_id": "0" * 24}, "wrong_incident"),
    ):
        response = dispatch("read_run_object", kind="input", **arguments)

        require(response.get("error") == code, "host_denial")

    passed("host_rejects_resource_endpoint_context_and_run_overrides")
    os.environ["HERMES_LAB_CONTEXT_ID"] = "0" * 64
    require(
        dispatch("read_run_object", kind="input").get("error")
        == "unknown_context",
        "unknown_context",
    )
    passed("unknown_context_is_not_recreated")
    require(
        not any(
            os.environ.get(name)
            for name in (
                "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY",
                "AWS_SESSION_TOKEN",
                "OPENAI_API_KEY",
            )
        ),
        "credential_isolation",
    )
    require(
        not Path("/run/hermes-lab-aws/control.sock").exists(),
        "control_socket_absent",
    )
    passed("no_aws_provider_credentials_or_control_socket")

    # This file runs alone in the image, stdout is its JSON protocol.
    sys.stdout.write(
        json.dumps({"passed": True, "checks": checks}, indent=2) + "\n"
    )
    sys.stdout.flush()


if __name__ == "__main__":
    main()
