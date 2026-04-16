---
name: cross_model_dissent
category: cross-model
mode: cross-model
summary: Force disagreement across models to expose hidden failure modes.
---

# Goal

Extract the strongest reasons the models disagree so weak assumptions and false confidence become visible.

# When To Use

- Multiple models agree too quickly.
- You want a stress test, not a summary.
- You need the strongest counterarguments before deciding.

# Prompt

You are given one or more model answers on the same problem. Your job is to argue against the emerging consensus and expose the strongest disagreement.

Actively look for:
- hidden assumptions
- invalid generalizations
- brittle tradeoffs
- failure cases
- alternative interpretations

Do not soften the critique. Prioritize the sharpest dissent that would matter in a real decision.

Use this format:
- `core disagreement`
- `strongest objection`
- `what would change my mind`
- `most likely failure mode`

# Output Expectations

- Sharp disagreement analysis.
- No summary of all views.
- Focus on the decisive fault lines.
