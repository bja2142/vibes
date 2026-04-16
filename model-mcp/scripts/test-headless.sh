#!/usr/bin/env bash

set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
relay="$root_dir/scripts/model-headless.sh"

providers=(codex claude gemini)

for provider in "${providers[@]}"; do
  printf '[%s] running probe\n' "$provider" >&2
  output="$("$relay" "$provider" "Reply with exactly PONG" | tr -d '\r')"
  if [[ "$output" != "PONG" ]]; then
    printf '[%s] unexpected output: %q\n' "$provider" "$output" >&2
    exit 1
  fi
  printf '[%s] ok\n' "$provider" >&2
done

printf 'all providers returned PONG\n'
