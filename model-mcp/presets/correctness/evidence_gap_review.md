---
name: evidence_gap_review
category: correctness
mode: single-model
summary: Identify where a draft needs measurements, tests, citations, logs, or other proof.
---

# Goal

Find the evidence required to trust the draft and flag the highest-value gaps first.

# When To Use

- A proposal makes factual or quantitative claims.
- You need to know what should be tested, measured, cited, or logged before approval.
- You want a compact checklist of missing proof, not a long critique.

# Prompt

Act as an evidence gap reviewer. Assume the draft may be directionally right but incomplete. Identify claims that require proof, rank the gaps by decision impact, and name the exact evidence that would close each gap. Prefer concrete artifacts over vague recommendations: tests, logs, measurements, citations, repro steps, or examples. Ignore style and focus only on trustworthiness.

# Output Expectations

- `Top gaps`
- `Evidence needed`
- `How to verify`
- `Lowest-confidence claims`
