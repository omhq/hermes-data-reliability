import sys

if sys.version_info[:2] != (3, 14):
    raise RuntimeError("hermes-data-reliability requires Python 3.14")
