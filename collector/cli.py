import json
import logging
import sys
import time


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the process once, respecting existing logging handlers."""
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    logging.basicConfig(level=level, handlers=[handler])


def write_json(value: object, *, indent: int | None = None) -> None:
    """Keep JSON results on stdout without log prefixes or extra messages."""
    sys.stdout.write(json.dumps(value, indent=indent) + "\n")
    sys.stdout.flush()
