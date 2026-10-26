import argparse

from pathlib import Path

from investigator.cli_output import write_json

from .definition import load
from .matching import MAX_BLOCK_BYTES, Matcher


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--text", type=Path, required=True)

    args = parser.parse_args()
    definition = load(args.observations)

    if args.source not in definition["sources"]:
        parser.error("unknown configured source")

    with args.text.open("rb") as stream:
        body = stream.read(MAX_BLOCK_BYTES + 1)

    if len(body) > MAX_BLOCK_BYTES:
        parser.error("text block exceeds 256 KiB")

    text = body.decode("utf-8")
    rules = definition["sources"][args.source]["rules"]

    with Matcher(rules) as matcher:
        matches = matcher.match([text])[0]

    write_json(
        {
            name: {"severity": rules[name]["severity"], "spans": spans}
            for name, spans in matches.items()
        }
    )


if __name__ == "__main__":
    main()
