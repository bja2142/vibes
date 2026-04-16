---
name: conversation_handoff
category: cross-model
mode: cross-model
summary: Continue a conversation in another model with a compact, controlled handoff.
---

# Goal

Carry a conversation forward in a different model without replaying unnecessary history or losing the active objective.

# When To Use

- You want to move a live session between providers.
- You want a second model to continue from the current thread.
- You need a short handoff that preserves intent and constraints.

# Prompt

Continue the existing conversation from the provided transcript and state. Preserve the active goal, constraints, and unresolved questions. Do not restart the discussion.

Rules:
- keep continuity with prior turns
- do not re-explain already established context
- preserve names, decisions, and open tasks
- if something is ambiguous, resolve it only if the transcript supports it

Use this format:
- `current goal`
- `important context`
- `open questions`
- `next response`

# Output Expectations

- Continuation, not re-analysis.
- Compact context retention.
- Minimal restatement of prior material.
