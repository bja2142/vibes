---
name: maintenance_review
category: implementation
mode: single-model
summary: Identify long-term complexity, coupling, and support risks that will age poorly.
---

# Goal

Assess how difficult the design will be to change, debug, extend, and support over time. Focus on complexity that creates long-term drag.

# When To Use

- Reviewing designs that will live for more than a short project cycle.
- Looking for hidden coupling, brittle abstractions, or unclear boundaries.
- Checking whether future changes will be expensive or risky.

# Prompt

Review the material as a long-term maintainer. Find the complexity that will age badly: hidden coupling, duplicated logic, unclear ownership, awkward abstractions, brittle configuration, and paths that will be painful to change later. Prefer concrete examples over general style comments. Focus on maintainability risks that would create repeated support burden or slow future work. If the design is maintainable, say what discipline or constraints are required to keep it that way.

# Output Expectations

- Surface the most important maintainability risks first.
- For each risk, explain the future cost and a practical mitigation.
- End with a short note on what would keep maintenance manageable.
