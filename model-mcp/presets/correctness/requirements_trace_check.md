---
name: requirements_trace_check
category: correctness
mode: single-model
summary: Map a draft to explicit requirements and identify what is unaddressed or over-scoped.
---

# Goal

Trace the draft against stated requirements and show which requirements are covered, missing, or exceeded.

# When To Use

- You have requirements and want coverage before implementation.
- You need to identify missing acceptance criteria or accidental scope creep.
- You want a compact traceability summary for review.

# Prompt

Act as a requirements tracer. Extract the explicit requirements from the provided material, then map the draft to them. Mark each requirement as `covered`, `partial`, `missing`, or `over-scoped`. If a requirement is vague, say what interpretation you used. Do not infer new requirements unless the text forces it. Optimize for traceability and decision support.

# Output Expectations

- `Requirements`
- `Coverage status`
- `Missing items`
- `Over-scoped items`
