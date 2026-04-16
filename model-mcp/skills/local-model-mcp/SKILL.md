---
name: local-model-mcp
description: Use the local-model MCP for second opinions, preset-driven reviews, cross-model comparison, conversation handoff, and local session inspection across Codex, Claude, and Gemini. Use when Codex should call the MCP instead of rebuilding long prompts inline for adversarial design review, security assessment, grounding checks, decision critique, brainstorming through multiple models, or resuming and exporting prior local-model conversations.
---

# Local Model MCP

Use this skill when the `local-model-mcp` server is available and the task benefits from:

- a second opinion from one local model
- a reusable preset such as security review or adversarial critique
- cross-model comparison or synthesis
- moving a stored session from one provider to another
- inspecting, labeling, exporting, or deleting stored sessions

Default to `run_helper` for execution. Use the explicit MCP tools only when you need discovery, validation, or session management.

## Workflow

1. Decide whether the task is raw, preset-based, cross-model, or a conversation handoff.
2. If a preset fits, prefer it over writing a long custom framing.
3. Keep the task prompt short and pass task-specific material as `input_text` when needed.
4. Reuse `session_id` only when the conversation should continue.
5. Label or save sessions when the transcript will matter later.

## Execution Rules

- Use `run_helper` with `provider` and `prompt` for a raw one-model call.
- Use `run_helper` with `provider`, `preset_name`, and `prompt` for a single-model preset.
- Use `run_helper` with `preset_name`, `prompt`, and `draft_providers` for cross-model synthesis or disagreement analysis.
- Use `run_helper` with `preset_name="conversation_handoff"`, `source_session_id`, `target_provider`, and `prompt` to continue a thread in another model.
- Use `list_presets` or read [references/preset-selection.md](references/preset-selection.md) when you need help choosing a preset.
- Use `list_sessions`, `get_session`, `update_session`, `export_session`, and `delete_session` for stored-session management.

## Heuristics

- Prefer `security_assessment`, `adversarial_design_review`, `grounding_check`, `tradeoff_matrix`, and `question_generator` as the first preset candidates for broad review tasks.
- Prefer `cross_model_dissent` when the user wants hidden weaknesses or disagreement, not consensus.
- Prefer `cross_model_consensus` when the user wants overlap only.
- Prefer `conversation_handoff` when the user already has a useful session and wants another model to continue from it.
- Use `save_session=true` for important review threads and then `update_session` to add a meaningful label.

## References

- Read [references/tool-surface.md](references/tool-surface.md) when you need the exact MCP routing options.
- Read [references/preset-selection.md](references/preset-selection.md) when you need help mapping a user request to a preset name.
