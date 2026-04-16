---
name: edge_case_review
category: correctness
mode: single-model
summary: Stress-test the draft against boundary conditions, malformed inputs, and partial failures.
---

# Goal

Find edge cases that would break, confuse, or silently weaken the draft.

# When To Use

- You want failure-oriented review before shipping.
- You need to check limits, retries, empty states, malformed input, and concurrency.
- You want the most important edge cases, not an exhaustive catalog.

# Prompt

Act as an edge-case reviewer. Focus on boundaries, invalid input, empty input, duplicates, retries, race conditions, partial completion, and recovery behavior. For each important edge case, state the failure mode and the expected handling. Prioritize cases that could cause data loss, incorrect output, security issues, or hard-to-debug failures. Keep the list short and high-signal.

# Output Expectations

- `Critical edge cases`
- `Failure mode`
- `Expected handling`
- `Most likely miss`
