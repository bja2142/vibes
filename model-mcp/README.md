# Local Headless Model MCP

This repo now contains a Python 3 MCP server that exposes local `codex`, `claude`, and `gemini` CLI installs through MCP.

The server exposes:

- Streamable HTTP on `/mcp`
- SSE on `/sse`
- SSE message POST endpoint on `/messages/`
- Health check on `/healthz`

Default bind address: `127.0.0.1:7777`

## Setup

Bootstrap the project-local venv and install dependencies:

```bash
scripts/setup_venv.sh
```

## Run

Start the MCP server with the project venv activated by the launcher:

```bash
scripts/run_server.sh
```

Override host or port if needed:

```bash
MODEL_MCP_HOST=0.0.0.0 MODEL_MCP_PORT=7777 scripts/run_server.sh
```

Verbose logging:

```bash
scripts/run_server.sh --log-level DEBUG --verbose-tool-logging
```

Write logs to disk instead of stdout:

```bash
scripts/run_server.sh --log-level DEBUG --verbose-tool-logging --log-file tmp/model-mcp.log
```

Write logs to disk in the project folder with environment variables:

```bash
mkdir -p tmp
MODEL_MCP_LOG_LEVEL=DEBUG MODEL_MCP_VERBOSE_TOOL_LOGGING=1 MODEL_MCP_LOG_FILE=tmp/model-mcp.log scripts/run_server.sh
```

Run in the background and write logs to the local `tmp/` folder:

```bash
mkdir -p tmp
nohup env MODEL_MCP_LOG_LEVEL=DEBUG MODEL_MCP_VERBOSE_TOOL_LOGGING=1 MODEL_MCP_LOG_FILE=tmp/model-mcp.log ./scripts/run_server.sh > tmp/model-mcp.stdout.log 2>&1 & echo $!
```

Check what is listening on port `7777`:

```bash
ss -antpu | rg ':7777'
```

Kill the process listening on port `7777` using `ss`:

```bash
kill "$(ss -antpu | grep ':7777' | grep -o 'pid=[0-9]*' | head -n1 | cut -d= -f2)"
```

Equivalent environment variables:

```bash
MODEL_MCP_LOG_LEVEL=DEBUG
MODEL_MCP_VERBOSE_TOOL_LOGGING=1
MODEL_MCP_LOG_FILE=tmp/model-mcp.log
```

## MCP Tools

The MCP now exposes three layers of tools:

- Raw provider access: `ask_codex`, `ask_claude`, `ask_gemini`
- Preset access: `list_presets`, `validate_presets`, `reload_presets`, `get_preset`, `run_preset_review`, `run_cross_model_preset`
- Session helpers: `share_conversation_with_model`, `list_sessions`, `get_session`, `update_session`, `export_session`, `delete_session`
- Background job helpers: `start_async_job`, `get_job`, `list_jobs`, `cancel_job`
- Unified routing: `run_helper`

The raw `ask_*` tools accept:

- `prompt: str`
- `input_text: str | None`
- `model: str | None`
- `timeout_seconds: int | None = None`
- `session_id: str | None`
- `save_session: bool = False`
- `save_path: str | None`

If `timeout_seconds` is omitted, the server computes a provider-specific timeout from the size of the final prompt it sends to the model.

The raw `ask_*` tools return a structured object with:

- `provider`
- `model`
- `output`
- `duration_ms`
- `session_id`
- `session_name`
- `step`
- `steps_remaining`
- `session_state_path`
- `saved_to`
- `timeout_seconds_used`

## Presets

Presets are stored on disk under:

```bash
presets/
```

Use the MCP to inspect them:

- `list_presets`
- `validate_presets`
- `reload_presets`
- `get_preset`

Preset loading behavior:

- The server automatically reloads presets when files under `presets/` change.
- `validate_presets` returns both valid presets and any parse or schema errors.
- `reload_presets` forces a refresh from disk and returns the same validation summary.

Run a single-model preset:

```json
{
  "tool": "run_preset_review",
  "arguments": {
    "provider": "claude",
    "preset_name": "security_assessment",
    "prompt": "Review this auth design",
    "input_text": "..."
  }
}
```

Run a cross-model preset:

```json
{
  "tool": "run_cross_model_preset",
  "arguments": {
    "preset_name": "cross_model_dissent",
    "prompt": "Evaluate this proposal",
    "draft_providers": ["codex", "gemini"],
    "judge_provider": "claude"
  }
}
```

That helper gathers draft answers from the `draft_providers`, then asks the `judge_provider` to apply the named cross-model preset to the collected outputs.
Draft providers now run in parallel. If one or more drafts fail but at least one succeeds, the judge still runs and the response includes both `draft_results` and `draft_errors`.

Use `run_helper` if you want one entrypoint instead of choosing among multiple MCP tools:

- Raw provider call: pass `provider` and `prompt`
- Single-model preset: pass `provider`, `preset_name`, and `prompt`
- Cross-model preset: pass `preset_name`, `prompt`, plus `draft_providers` and optionally `judge_provider`
- Conversation handoff: pass `preset_name="conversation_handoff"`, `source_session_id`, `target_provider`, and `prompt`

For long-running work that may exceed the caller's MCP timeout, start it as a background job:

```json
{
  "tool": "start_async_job",
  "arguments": {
    "preset_name": "cross_model_dissent",
    "prompt": "Evaluate this proposal",
    "draft_providers": ["codex", "gemini"],
    "judge_provider": "claude",
    "timeout_seconds": 300,
    "save_session": true
  }
}
```

Poll the result later:

```json
{
  "tool": "get_job",
  "arguments": {
    "job_id": "job-abc123..."
  }
}
```

List recent jobs:

```json
{
  "tool": "list_jobs",
  "arguments": {}
}
```

Cancellation is best-effort and only works before a queued job starts running:

```json
{
  "tool": "cancel_job",
  "arguments": {
    "job_id": "job-abc123..."
  }
}
```

## Session Behavior

- If `session_id` is omitted, `ask_*` creates a new local session automatically.
- The response includes `session_id`, which you pass back on the next `ask_*` call to continue that conversation.
- The MCP layer stores session state locally and rebuilds the full transcript into the next model prompt automatically.
- Sessions are capped at `10` steps.
- Sessions are provider-specific. A Claude session id cannot be reused with `ask_gemini` or `ask_codex`.

Internal session state is stored as JSON under:

```bash
.model_mcp/sessions/
```

Set `MODEL_MCP_STATE_DIR` before startup if you want that state somewhere else.

## Saving Sessions

- If `save_session=true`, the current session is exported automatically.
- If `save_path` is omitted, the export goes to `.model_mcp/exports/<session_id>.md`.
- If `save_path` is provided, that exact path is used. Relative paths are resolved from the repo root.
- If `save_path` ends with `.json`, the exported file is JSON. Otherwise it is a readable Markdown transcript.

Inspect or export existing sessions without adding a new model turn:

- `list_sessions`
- `get_session`
- `update_session`
- `export_session`
- `delete_session`

You can also hand a stored session to another provider:

```json
{
  "tool": "share_conversation_with_model",
  "arguments": {
    "source_session_id": "claude-abc123...",
    "target_provider": "gemini",
    "prompt": "Continue this thread and focus on missing operational risks"
  }
}
```

You can update session metadata without adding a turn:

```json
{
  "tool": "update_session",
  "arguments": {
    "session_id": "claude-abc123...",
    "label": "Auth Review",
    "notes": "Focus on bearer token lifecycle",
    "metadata_patch": {"owner": "security"}
  }
}
```

Example flow:

```json
{"tool":"ask_claude","arguments":{"prompt":"Analyze this stack trace"}}
```

Response excerpt:

```json
{"session_id":"claude-abc123...","step":1,"steps_remaining":9}
```

Continue the same conversation:

```json
{"tool":"ask_claude","arguments":{"session_id":"claude-abc123...","prompt":"Now propose a fix","save_session":true}}
```

## Default Models

- Codex: `gpt-5.4`
- Claude Code: `claude-opus-4-6`
- Gemini: `gemini-3.1-pro-preview`

## Current Safety Defaults

- Codex runs with `--sandbox read-only`
- Claude runs with `--permission-mode plan`
- Gemini runs with `--approval-mode plan`

This keeps the initial relay read-only while you build out orchestration and conversation support.

## Verification

Headless CLI probes:

```bash
scripts/test-headless.sh
```

End-to-end MCP smoke test over `/mcp`:

```bash
scripts/test_mcp.sh
```

Local fake-provider session test:

```bash
.venv/bin/python scripts/test_session_logic.py
```

Local fake-provider preset and orchestration test:

```bash
.venv/bin/python scripts/test_preset_logic.py
```

The MCP smoke test starts the server, verifies `/healthz`, checks the expanded tool list, verifies preset discovery, calls all three `ask_*` tools with a `PONG` prompt, and validates real `get_session` and `list_sessions` behavior against the running server.
The MCP smoke test also validates `validate_presets`, `run_helper`, `update_session`, and `delete_session` against the running server.
