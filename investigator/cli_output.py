import json
import sys


def write_json(value: object, *, indent: int | None = None) -> None:
    """Keep JSON results on stdout without log prefixes or extra messages."""
    sys.stdout.write(json.dumps(value, indent=indent) + "\n")
    sys.stdout.flush()
