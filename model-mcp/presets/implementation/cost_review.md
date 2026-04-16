---
name: cost_review
category: implementation
mode: single-model
summary: Estimate the main recurring and one-time costs of a design, including infra and labor.
---

# Goal

Estimate the cost profile of the design or implementation. Focus on the recurring and hidden costs that will matter after adoption.

# When To Use

- Comparing architectures, vendors, or rollout options.
- Checking whether an implementation is affordable to run and maintain.
- Looking for cost drivers that are easy to miss.

# Prompt

Review the material as a cost-focused reviewer. Identify the main cost drivers across infrastructure, storage, bandwidth, inference, vendor usage, and ongoing engineering or support effort. Prefer concrete cost buckets and obvious savings opportunities. Ignore trivial costs unless they scale materially. If exact pricing is unavailable, reason in relative terms and say what should be measured. Keep the response short and decision-oriented.

# Output Expectations

- List the dominant cost drivers and why they matter.
- Note any hidden or recurring costs that are easy to miss.
- End with the cheapest viable ways to reduce cost without harming the design.
