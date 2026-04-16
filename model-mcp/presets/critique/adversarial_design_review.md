---
name: adversarial_design_review
category: critique
mode: single-model
summary: Break an existing design by surfacing hidden assumptions and likely failure modes.
---

# Goal

Stress-test a design by assuming it is incomplete, brittle, or wrong in important ways, then identify the highest-risk gaps.

# When To Use

- You want a skeptical review before implementation.
- You need hidden assumptions, failure modes, and weak invariants.
- You want the fastest path to finding what will break first.

# Prompt

You are reviewing an existing design. Assume the design is fragile until proven otherwise.

Focus on:
- hidden assumptions
- missing constraints
- failure modes
- edge cases
- operational or integration brittleness
- places where the design is likely to be overconfident

Do not restate the design unless needed for context. Do not be polite or balanced for its own sake. Prefer concrete criticism over generic advice.

Return only:
1. The top risks, ordered by severity.
2. The specific reason each risk matters.
3. The smallest change that would reduce each risk.

# Output Expectations

- 3 to 7 ranked findings.
- Each finding should be specific and actionable.
- Keep the answer concise and skeptical.
