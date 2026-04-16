#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  model-headless.sh <codex|claude|gemini> <prompt>

Examples:
  model-headless.sh codex "Reply with exactly PONG"
  printf 'hello\n' | model-headless.sh claude "Summarize the input"

Behavior:
  - Reads optional stdin and appends it to the prompt inside <relay_input> tags.
  - Prints only the final model text to stdout.
  - Uses read-only / plan-style defaults so it is safe to embed in a relay or MCP wrapper.
EOF
}

if [[ $# -lt 2 ]]; then
  usage >&2
  exit 2
fi

provider="$1"
shift
prompt="$*"

stdin_payload=""
if [[ ! -t 0 ]]; then
  stdin_payload="$(cat)"
fi

full_prompt="$prompt"
if [[ -n "$stdin_payload" ]]; then
  full_prompt+=$'\n\n<relay_input>\n'
  full_prompt+="$stdin_payload"
  full_prompt+=$'\n</relay_input>'
fi

case "$provider" in
  codex)
    out_file="$(mktemp)"
    trap 'rm -f "$out_file"' EXIT
    codex exec \
      --skip-git-repo-check \
      --sandbox read-only \
      --color never \
      --output-last-message "$out_file" \
      "$full_prompt" >/dev/null
    cat "$out_file"
    ;;
  claude)
    claude -p --output-format text --permission-mode plan "$full_prompt"
    ;;
  gemini)
    gemini -p "$full_prompt" --approval-mode plan --output-format text
    ;;
  *)
    echo "Unsupported provider: $provider" >&2
    usage >&2
    exit 2
    ;;
esac
