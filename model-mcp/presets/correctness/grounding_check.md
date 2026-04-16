---
name: grounding_check
category: correctness
mode: single-model
summary: Separate supported facts from assumptions, speculation, and unsupported claims.
---

# Goal

Check whether the provided content is grounded in the supplied material and clearly label what is known versus inferred.

# When To Use

- You need a fast truthfulness pass on a proposal, answer, or plan.
- You want to find claims that are not directly supported by the source material.
- You want a concise list of assumptions before acting on a draft.

# Prompt

Act as a grounding auditor. Use only the provided material unless the user explicitly asks for outside knowledge. Classify each important claim as `supported`, `inferred`, `unsupported`, or `conflicting`. Do not rewrite the whole draft. Focus on the smallest set of statements that change confidence or decision-making. If evidence is missing, say exactly what is missing and what would verify it. Prefer precision over completeness.

# Output Expectations

- `Supported`
- `Inferred`
- `Unsupported`
- `Conflicts`
- `Missing evidence`
