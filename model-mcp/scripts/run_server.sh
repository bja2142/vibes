#!/usr/bin/env bash

set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -x "$root_dir/.venv/bin/python" ]]; then
  echo "Missing venv at $root_dir/.venv. Run scripts/setup_venv.sh first." >&2
  exit 1
fi

# Shell entrypoint for local runs without containerization.
source "$root_dir/.venv/bin/activate"
export PYTHONPATH="$root_dir/src${PYTHONPATH:+:$PYTHONPATH}"

exec python -m model_mcp.server "$@"
