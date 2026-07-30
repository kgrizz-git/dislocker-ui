#!/usr/bin/env bash
# Launch dislocker-ui from the repo root.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m dislocker_ui "$@"
