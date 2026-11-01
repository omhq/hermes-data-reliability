import argparse
import json
import logging
from pathlib import Path
from typing import Any

from investigator.logging_config import configure_logging
from observations.configuration import InvalidConfiguration
from observations.files import install_bytes
from observations.yaml import load_yaml

from .collection import with_collection
from .definition import InvalidDefinition, bind, parse

LOGGER = logging.getLogger(__name__)


def install(
    source: Path,
    destination: Path,
    compiled: Path,
    approved: dict[str, Any],
    collection_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = source.read_bytes()

    try:
        definition = load_yaml(source)
        parsed = parse(definition)
        if collection_plan is None:
            collection_plan = {
                "consumers": {parsed.consumer.identifier: {}},
                "consumer_revisions": {
                    parsed.consumer.identifier: parsed.consumer.revision
                },
                "feeds": {},
            }

        bound = with_collection(approved, parsed, collection_plan)
        bind(parsed, bound)
    except InvalidConfiguration:
        raise InvalidDefinition("Invalid pipeline-health YAML") from None

    install_bytes(destination, payload)
    install_bytes(compiled, json.dumps(definition).encode())

    return definition


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--collection-plan", type=Path)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--approved", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--compiled", type=Path, required=True)
    args = parser.parse_args()
    source = args.source or (
        args.destination if args.destination.exists() else args.template
    )

    if source is None:
        parser.error("First installation requires --source or --template")

    install(
        source,
        args.destination,
        args.compiled,
        json.loads(args.approved.read_text()),
        json.loads(args.collection_plan.read_text())
        if args.collection_plan
        else None,
    )
    LOGGER.info(
        "Pipeline-health configuration validated; "
        "restart the executor to activate."
    )


if __name__ == "__main__":
    main()
