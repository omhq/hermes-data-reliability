import argparse
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .access import CollectionAccess
from .cli import configure_logging
from .definition import (
    MAX_DEFINITION_BYTES,
    DefinitionError,
    load,
    validate,
)
from .matching import Matcher

LOGGER = logging.getLogger(__name__)


class Dumper(yaml.SafeDumper):
    pass


def configure(
    approved: dict[str, Any],
    destination: Path,
    template: Path | None = None,
    source: Path | None = None,
) -> dict[str, Any]:
    """Validate the operator definition before installing it."""
    CollectionAccess.load(approved)

    existing = destination.exists()
    selected = source or (destination if existing else template)

    if selected is None:
        raise DefinitionError(
            "Initial configuration needs a template or source"
        )

    definition = load(selected)

    if not existing and source is None:
        for source_definition in definition["sources"].values():
            for key in ("region", "log_group"):
                source_definition[key] = approved[key]

    validate(definition, approved)

    for source_definition in definition["sources"].values():
        with Matcher(source_definition["rules"]):
            pass

    if not existing or source is not None:
        write_definition(definition, destination)

    return definition


def text(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style)


def write_definition(definition: dict[str, Any], destination: Path) -> None:
    """Keep multiline instructions readable in the installed YAML."""
    Dumper.add_representer(str, text)
    payload = yaml.dump(definition, Dumper=Dumper, sort_keys=False)

    if len(payload.encode("utf-8")) > MAX_DEFINITION_BYTES:
        raise DefinitionError(
            "Installed observation definition exceeds 64 KiB"
        )

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=destination.parent, delete=False
    ) as stream:
        staged = Path(stream.name)

        try:
            stream.write(payload)
            stream.flush()
            staged.chmod(0o644)
            staged.replace(destination)
        finally:
            staged.unlink(missing_ok=True)


def main() -> None:
    configure_logging()

    parser = argparse.ArgumentParser()

    parser.add_argument("--approved", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument(
        "--source",
        type=Path,
        help="Replace installed YAML with this validated definition",
    )
    parser.add_argument(
        "--template",
        type=Path,
        help="Initial observation template supplied by the host application",
    )

    args = parser.parse_args()

    configure(
        json.loads(args.approved.read_text()),
        args.destination,
        args.template,
        source=args.source,
    )

    LOGGER.info("Collector configuration validated and installed.")


if __name__ == "__main__":
    main()
