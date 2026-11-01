import argparse
import json
import logging
from pathlib import Path
from typing import Any

from observations.files import install_bytes
from observations.yaml import dump_yaml

from .cli import configure_logging
from .definition import load

LOGGER = logging.getLogger(__name__)


def configure(
    approved: dict[str, Any],
    destination: Path,
    template: Path | None = None,
    source: Path | None = None,
) -> dict[str, Any]:
    existing = destination.exists()
    selected = source or (destination if existing else template)

    if selected is None:
        raise ValueError("Initial configuration needs a template or source")

    definition = load(
        selected, approved, resolve_template=not existing and source is None
    )

    if not existing or source is not None:
        install_bytes(destination, dump_yaml(definition))

    return definition


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--template", type=Path)
    args = parser.parse_args()
    configure(
        json.loads(args.approved.read_text()),
        args.destination,
        args.template,
        args.source,
    )
    LOGGER.info("Source catalog validated and installed.")


if __name__ == "__main__":
    main()
