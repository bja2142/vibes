---
name: consistency_check
category: correctness
mode: single-model
summary: Detect contradictions, terminology drift, and mismatched assumptions across a draft.
---

# Goal

Check the material for internal consistency and surface contradictions that could break implementation or decision-making.

# When To Use

- You suspect the draft disagrees with itself or with earlier requirements.
- You need a pass for terminology drift, duplicate definitions, or conflicting constraints.
- You want a short list of contradictions and the likely resolution.

# Prompt

Act as a consistency checker. Compare claims, terms, constraints, numbers, and defaults against each other. Flag contradictions, ambiguous reuse of terms, and places where two statements cannot both be true. If a conflict is only partial, explain the exact condition under which each statement holds. Do not restate the whole source. Focus on contradictions that would change behavior, scope, or acceptance.

# Output Expectations

- `Direct conflicts`
- `Partial conflicts`
- `Ambiguous terms`
- `Likely resolution`
