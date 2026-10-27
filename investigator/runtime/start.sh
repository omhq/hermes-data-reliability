#!/usr/bin/env bash
set -euo pipefail
exec sudo /usr/local/bin/labctl start "$@"
