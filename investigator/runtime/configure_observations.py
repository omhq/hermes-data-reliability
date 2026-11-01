import argparse
import json
import logging
from pathlib import Path
from typing import Any

from collector.definition import plan
from investigator.logging_config import configure_logging
from observations.configuration import InvalidConfiguration
from observations.configurations.base import Configuration, ConfigurationGraph
from observations.configurations.registry import (
    ConfigurationRegistry,
    ConfigurationSpec,
)
from observations.files import install_bytes
from observations.models import Source
from observations.subscriptions import manifest
from observations.yaml import dump_yaml, load_yaml

from .observation_configurations import CONFIGURATIONS

LOGGER = logging.getLogger(__name__)


def configure(
    root: Path,
    templates: Path,
    approved: dict[str, Any],
    destination: Path,
    catalog_source: Path | None = None,
    additional_consumers: tuple[Path, ...] = (),
    *,
    registry: ConfigurationRegistry = CONFIGURATIONS,
) -> dict[str, Any]:
    loaded: list[tuple[ConfigurationSpec, Configuration, bool]] = []
    sources: dict[str, Source] = {}

    for entry in registry.entries:
        installed = root / entry.yaml_filename
        override = catalog_source if entry == registry.catalog else None
        existing = installed.exists()
        source = override or (
            installed if existing else entry.template_path(templates)
        )
        configuration = entry.load(
            source,
            approved,
            resolve_template=not existing and override is None,
        )
        loaded.append(
            (entry, configuration, not existing or override is not None)
        )
        contributed = configuration.sources(approved)

        if sources.keys() & contributed.keys():
            raise InvalidConfiguration("Source aliases must be unique")

        sources.update(contributed)

    consumers = tuple(
        consumer
        for _, configuration, _ in loaded
        for consumer in configuration.consumers()
    ) + tuple(manifest(load_yaml(path)) for path in additional_consumers)
    compiled = plan(sources, consumers)
    graph = ConfigurationGraph(approved, sources, compiled)

    for _, configuration, _ in loaded:
        configuration.validate_graph(graph)

    outputs: list[tuple[Path, bytes]] = []

    for entry, configuration, install_source in loaded:
        if install_source:
            outputs.append(
                (
                    root / entry.yaml_filename,
                    dump_yaml(configuration.definition),
                )
            )

        outputs.append(
            (
                destination / entry.compiled_filename,
                json.dumps(configuration.compile(graph)).encode(),
            )
        )

    for path, payload in outputs:
        install_bytes(path, payload)

    return compiled


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--templates", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--consumer", type=Path, action="append", default=[])
    args = parser.parse_args()
    configure(
        args.root,
        args.templates,
        json.loads(args.approved.read_text()),
        args.destination,
        args.catalog,
        tuple(args.consumer),
    )
    LOGGER.info(
        "Source catalog, consumers, and shared collection plan validated."
    )


if __name__ == "__main__":
    main()
