#!/usr/bin/env bash

set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
server_log="$root_dir/.mcp-server.log"
port="${MODEL_MCP_PORT:-7777}"

"$root_dir/scripts/run_server.sh" >"$server_log" 2>&1 &
server_pid="$!"

cleanup() {
  if kill -0 "$server_pid" 2>/dev/null; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
}

trap cleanup EXIT

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

curl -fsS "http://127.0.0.1:${port}/healthz" >/dev/null

source "$root_dir/.venv/bin/activate"
export PYTHONPATH="$root_dir/src${PYTHONPATH:+:$PYTHONPATH}"
python "$root_dir/scripts/test_mcp_client.py"
