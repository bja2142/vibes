# MCP Tool Surface

Assume this skill is used only when the `local-model-mcp` server is already available to the agent.

Prefer `run_helper` for most execution because it is the single routing entrypoint.

## Default Pattern

Use `run_helper` unless you are inspecting presets or sessions.

### Raw provider call

- Use when the user wants a direct one-model answer without preset framing.
- Arguments: `provider`, `prompt`, optional `input_text`, `model`, `session_id`, `save_session`, `save_path`

### Single-model preset

- Use when the user wants a named review framing against one provider.
- Arguments: `provider`, `preset_name`, `prompt`, optional `input_text`, `session_id`

### Cross-model preset

- Use when the user wants multiple models compared or synthesized.
- Arguments: `preset_name`, `prompt`, optional `input_text`, `draft_providers`, optional `judge_provider`

### Conversation handoff

- Use when the user wants one stored session continued by another provider.
- Arguments: `preset_name="conversation_handoff"`, `source_session_id`, `target_provider`, `prompt`

## Non-routing Tools

- `list_presets`: Discover presets by category or mode.
- `validate_presets`: Check for preset parse errors.
- `reload_presets`: Force preset refresh from disk.
- `get_preset`: Read one preset body and metadata.
- `list_sessions`: Find existing local sessions.
- `get_session`: Inspect one stored session and transcript.
- `update_session`: Add a label, notes, or metadata patch.
- `export_session`: Write a transcript to disk.
- `delete_session`: Remove a stored session.

## Working Rules

- Keep the task prompt short. Do not rewrite the preset framing in the request.
- Reuse `session_id` when the conversation should continue.
- Use `save_session=true` when the transcript will be useful later.
- Use `update_session` to label important threads after creation.
- Prefer preset names over ad hoc long prompts when a matching preset exists.
