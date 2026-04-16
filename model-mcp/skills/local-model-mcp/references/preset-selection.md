# Preset Selection

Use these presets when the user wants reusable review framing instead of a raw one-off model call.

## Critique

- `adversarial_design_review`: Break a design by surfacing hidden assumptions and brittle points.
- `blind_spot_review`: Find important concerns that have not been discussed yet.
- `counterproposal_review`: Produce a simpler or safer alternative.
- `stakeholder_pushback`: Simulate objections from security, infra, product, finance, or maintainers.
- `premortem`: Assume the effort failed and explain the most likely reasons.

## Security

- `security_assessment`: Review auth, boundaries, secrets, injection, privilege, and unsafe defaults.
- `abuse_case_review`: Think like a hostile or opportunistic user.
- `data_exposure_review`: Focus on privacy, logs, retention, backups, and leakage.
- `supply_chain_review`: Review dependencies, provenance, build paths, and update channels.
- `mcp_tool_risk_review`: Review prompt injection, unsafe delegation, and overbroad tool access.

## Correctness

- `grounding_check`: Separate facts from assumptions and speculation.
- `evidence_gap_review`: Identify what still needs citations, tests, logs, or metrics.
- `consistency_check`: Find contradictions across requirements, discussion, and code.
- `requirements_trace_check`: Map the proposal back to explicit requirements.
- `edge_case_review`: Focus on malformed inputs, retries, concurrency, limits, and partial failures.

## Implementation

- `operability_review`: Focus on deployability, observability, rollback, and recovery.
- `test_strategy_review`: Produce the highest-value missing tests.
- `performance_risk_review`: Identify latency, scaling, and memory risks.
- `cost_review`: Focus on infra, inference, storage, egress, and operational labor costs.
- `maintenance_review`: Identify complexity that will age badly.

## Cross-Model

- `second_opinion`: Ask another model to find what the first review likely missed.
- `cross_model_consensus`: Keep only the points that broadly agree.
- `cross_model_dissent`: Surface the strongest disagreements and fault lines.
- `model_red_team`: Attack another model's answer directly.
- `relay_brainstorm`: Expand ideas, then compress toward the strongest directions.
- `perspective_swap`: Re-run the same material through different fixed roles.
- `conversation_handoff`: Move one stored session into another model and continue it.

## Decision

- `tradeoff_matrix`: Compare options across complexity, cost, risk, and reversibility.
- `decision_challenge`: Attack the current favorite and argue for an alternative.
- `go_no_go_review`: Decide whether work is ready to proceed.
- `scope_trim_review`: Cut a plan down to the smallest viable slice.
- `priority_review`: Rank issues or opportunities by leverage.

## Communication

- `executive_summary_review`: Convert a technical thread into a short decision memo.
- `engineer_feedback_review`: Turn critique into actionable engineering feedback.
- `question_generator`: Produce the next highest-value questions to ask.
- `risk_register_builder`: Convert a proposal into structured risks and mitigations.
