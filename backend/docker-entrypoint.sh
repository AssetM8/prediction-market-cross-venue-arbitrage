#!/bin/sh
# Optionally run the deterministic fixture demo (paper trades included) before serving.
set -eu
if [ "${RUN_DEMO_ON_START:-false}" = "true" ]; then
  python -m app.cli demo
fi
exec "$@"
