---
name: risk_register_builder
category: communication
mode: single-model
summary: Turn a proposal into a concise risk register with triggers, mitigations, and owners.
---

# Goal

Convert the supplied proposal or design into a compact risk register. Keep the structure stable so the caller can feed in different plans without rebuilding the framing.

# When To Use

- A design needs risks made explicit before execution.
- The caller wants mitigation-oriented output for planning or review.
- The same risk format should be reused across projects.

# Prompt

Build a risk register from the provided material only.

For each meaningful risk, include:
- a short risk statement
- why it matters
- likely trigger or failure signal
- practical mitigation
- owner or area if available

Rules:
- Be specific, not generic.
- Prioritize the risks that would most affect delivery, safety, cost, or reliability.
- Merge duplicates instead of listing near-identical items.
- Label uncertainty when the evidence is weak.
- Keep the register compact enough to act on.

If the input is incomplete, infer conservatively and say where the gap is.

# Output Expectations

- A ranked list or table of risks.
- Each row should be short and actionable.
- Include only the highest-value risks, not an exhaustive dump.
