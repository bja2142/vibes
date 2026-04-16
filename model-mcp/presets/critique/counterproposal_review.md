---
name: counterproposal_review
category: critique
mode: single-model
summary: Propose a simpler or safer alternative to the current design.
---

# Goal

Offer a competing design that reduces complexity, cost, or risk while preserving the core objective.

# When To Use

- The current design feels overbuilt or brittle.
- You want a simpler path to the same outcome.
- You need an alternative to compare against the original.

# Prompt

You are reviewing a design and must propose a credible alternative.

Optimize for:
- simplicity
- lower operational risk
- lower implementation effort
- fewer moving parts
- clearer failure behavior

Do not merely rewrite the original design. Produce a distinct counterproposal that changes the approach if that is the better tradeoff.

Return only:
1. The core idea of the alternative.
2. The main tradeoffs versus the original.
3. The specific reasons it is better or worse.

# Output Expectations

- One primary alternative, not a list of many.
- Include tradeoffs explicitly.
- Keep the answer short and decision-oriented.
