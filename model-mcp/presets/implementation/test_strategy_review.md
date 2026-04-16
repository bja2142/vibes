---
name: test_strategy_review
category: implementation
mode: single-model
summary: Identify the highest-value tests and missing coverage needed to trust the implementation.
---

# Goal

Produce a practical test strategy for the provided design or code. Focus on the tests most likely to catch real regressions, not on exhaustive coverage.

# When To Use

- Before implementing or merging a nontrivial change.
- When you need a focused list of regression tests and edge cases.
- When you want to compare the current test plan against likely failure modes.

# Prompt

Review the material as a testing strategist. Identify the few tests that would give the most confidence, including unit, integration, contract, negative, and edge-case coverage where relevant. Focus on behavior that is likely to regress, be mis-implemented, or fail silently. Prefer test cases that are cheap, specific, and high-value. Do not propose bloated suites or generic advice. If the design already has good coverage, say what is still missing and why it matters.

# Output Expectations

- List the highest-value tests first.
- For each test, explain what it proves and what failure it would catch.
- Include any missing coverage gaps or risky assumptions at the end.
