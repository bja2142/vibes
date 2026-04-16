---
name: scope_trim_review
category: decision
mode: single-model
summary: Reduce a plan to the smallest version that still delivers most of the value.
---

# Goal

Cut excess scope while preserving the core outcome so the plan becomes easier to execute, validate, and revise.

# When To Use

- The current proposal feels too broad or too expensive.
- Delivery risk is rising because too much is bundled together.
- You want a narrower milestone or MVP with clear value.

# Prompt

You are reviewing a plan with the goal of removing scope aggressively without destroying the core value.

Instructions:
- Identify the essential user or business outcome the work is supposed to deliver.
- Separate the plan into must-have, should-have, and defer items.
- Recommend the smallest credible slice that still proves value or unblocks the next decision.
- Flag parts of the proposal that look attractive but are likely scope creep, polish, speculative optimization, or premature generalization.
- Explain the tradeoff created by each cut, especially where the cut introduces risk or limits future options.
- End with a trimmed version of the plan and a short backlog of deferred items.
- Bias toward reversible, low-coupling, testable increments.

# Output Expectations

- A clear statement of the core outcome.
- A trimmed scope recommendation with explicit cuts.
- A short deferred backlog and the main tradeoffs.
