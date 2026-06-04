#!/usr/bin/env bash
# Run the intent-handlers-core unit tests in isolation.
# Usage: ./run_tests.sh   (uses the hermes venv python if present)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYBIN="${HERMES_PY:-/opt/hermes/home/.hermes/hermes-agent/venv/bin/python}"
[ -x "$PYBIN" ] || PYBIN="python3"
cd "$HERE/tests"
exec "$PYBIN" -m pytest . -q --rootdir=. -p no:cacheprovider
