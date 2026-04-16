---
name: executive_summary_review
category: communication
mode: single-model
summary: Turn dense material into a concise executive summary with decisions, risks, and next steps.
---

# Goal

Produce a tight executive summary from the supplied material. Keep the framing stable so the caller only needs to provide the source content and any audience constraints.

# When To Use

- A technical or strategic discussion needs a leader-friendly summary.
- The caller wants decisions, risks, and actions without long background.
- The same source needs to be condensed repeatedly for different audiences.

# Prompt

You are writing for an executive audience. Use only the provided material unless explicitly told to infer.

Prioritize:
- the decision or current state
- why it matters now
- top risks or blockers
- concrete next steps and owners if present

Rules:
- Be concise and specific.
- Do not repeat the source material.
- Call out uncertainty instead of smoothing it over.
- If something is missing, say so briefly.
- Prefer bullets over prose unless a short paragraph is clearly better.

If the input is messy, synthesize it into a clean summary without adding new facts.

# Output Expectations

- 1 short opening paragraph.
- 3-6 bullets for decisions, risks, and actions.
- No filler, no restating the full source.
