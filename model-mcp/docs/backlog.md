# MCP Preset Backlog

## Preset Library

- `adversarial_design_review`: Break an existing design by surfacing hidden assumptions, weak constraints, and likely failure modes.
- `blind_spot_review`: Identify important considerations that have not been discussed yet.
- `counterproposal_review`: Produce a simpler or safer alternative approach with explicit tradeoffs.
- `stakeholder_pushback`: Simulate objections from security, infra, finance, product, compliance, and maintainers.
- `premortem`: Assume the effort failed later and explain the most plausible reasons why.

- `security_assessment`: Review architecture or implementation for auth, injection, privilege, secrets, and boundary issues.
- `abuse_case_review`: Enumerate how an attacker or hostile user could misuse the system.
- `data_exposure_review`: Focus on privacy, logging, retention, backups, and accidental data disclosure.
- `supply_chain_review`: Review dependencies, build paths, update channels, provenance, and deployment trust.
- `mcp_tool_risk_review`: Focus on prompt injection, confused deputy problems, overbroad tool access, and unsafe delegation.

- `grounding_check`: Separate supported facts from assumptions, guesses, and unverified claims.
- `evidence_gap_review`: Identify claims that still need sources, tests, metrics, or logs.
- `consistency_check`: Find contradictions between requirements, prior discussion, code, and proposed behavior.
- `requirements_trace_check`: Map the proposal back to explicit requirements and flag missing coverage.
- `edge_case_review`: Focus on malformed input, null paths, retries, concurrency, limits, and partial failure states.

- `operability_review`: Review deployability, observability, rollback, recovery, and ownership gaps.
- `test_strategy_review`: Produce the highest-value missing tests and likely regression areas.
- `performance_risk_review`: Identify latency, throughput, fan-out, memory, and scaling risks.
- `cost_review`: Review likely infra, inference, storage, egress, and operational costs.
- `maintenance_review`: Identify complexity that will become hard to change or support over time.

- `second_opinion`: Ask a second model to review an existing answer and focus on what was missed.
- `cross_model_consensus`: Compare outputs across models and keep only the points they broadly agree on.
- `cross_model_dissent`: Use multiple models to surface disagreements and unresolved judgment calls.
- `model_red_team`: Give one model another model’s answer and ask it to attack weaknesses in reasoning or coverage.
- `relay_brainstorm`: Chain models so one expands ideas, another critiques them, and another prioritizes.
- `perspective_swap`: Re-run the same material through different fixed perspectives such as security, ops, or maintainer.
- `conversation_handoff`: Share one session with another model and continue the thread under a new objective.

- `tradeoff_matrix`: Compare options across cost, complexity, risk, reversibility, and time-to-value.
- `decision_challenge`: Attack the current favorite option and argue for an alternative.
- `go_no_go_review`: Decide whether work is ready to proceed, blocked, or needs redesign.
- `scope_trim_review`: Cut the plan down to the smallest version that still delivers value.
- `priority_review`: Rank issues or options by severity, urgency, uncertainty reduction, and effort.

- `executive_summary_review`: Convert a technical thread into a compact decision memo.
- `engineer_feedback_review`: Turn critique into concrete, actionable engineering feedback.
- `question_generator`: Produce the next highest-value questions to ask before proceeding.
- `risk_register_builder`: Turn a proposal into a structured list of risks, triggers, mitigations, and owners.

## MCP API Follow-Up

- `list_presets`: Return the available preset names, categories, summaries, and execution modes.
- `get_preset`: Return the metadata and prompt body for a named preset.
- `run_preset_review`: Execute a single-model preset against one provider with optional session continuation.
- `run_cross_model_preset`: Execute a cross-model preset across two or three providers and return normalized outputs.
- `share_conversation_with_model`: Take one stored session and continue it with another provider plus a handoff prompt.

## Session Management Follow-Up

- `get_session`: Fetch stored session metadata and the current transcript for a specific session id.
- `list_sessions`: List known sessions with provider, created time, updated time, and current step count.
- `delete_session`: Remove a stored local session and any default transcript export if we decide cleanup should be supported.
- `export_session`: Export an existing session without requiring another model turn.

## Supporting Work

- Add a preset loader that reads prompt files from disk and validates required metadata.
- Add test coverage for preset loading and cross-model orchestration.
- Add docs for how agents should use presets instead of rebuilding long prompts inline.
- Add output normalization rules so cross-model presets produce comparable results.
