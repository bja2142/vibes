---
name: cross_model_consensus
category: cross-model
mode: cross-model
summary: Compare multiple model responses and extract only the shared conclusions.
---

# Goal

Produce a conservative synthesis that keeps only conclusions supported across models and flags disagreements clearly.

# When To Use

- You want the safest shared answer from multiple providers.
- You want to reduce overconfidence and model-specific noise.
- You need a compact consensus view for downstream automation.

# Prompt

You are given multiple model responses on the same topic. Extract the consensus only. Prefer statements supported by at least two responses, and separate facts from interpretation.

Be strict about agreement. If a point is only supported by one model, do not promote it as consensus.

Use this format:
- `consensus`
- `shared risks`
- `open disagreements`
- `recommended next action`

Keep the synthesis terse. Do not merge away meaningful uncertainty.

# Output Expectations

- Conservative summary of shared conclusions.
- Explicit list of unresolved disagreements.
- Minimal prose.
