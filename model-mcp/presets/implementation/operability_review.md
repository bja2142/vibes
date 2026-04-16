---
name: operability_review
category: implementation
mode: single-model
summary: Review whether the system can be deployed, observed, recovered, and safely operated.
---

# Goal

Assess the operational readiness of a design or implementation. Focus on what breaks in real deployment, what is hard to observe, and what makes recovery or ownership risky.

# When To Use

- Reviewing a proposed service, workflow, or agent system before rollout.
- Checking whether the design has enough logging, metrics, rollback, and incident handling.
- Looking for gaps that only show up after deployment.

# Prompt

Review the material as an operations and reliability reviewer. Be skeptical and concrete. Find the smallest set of operational issues that matter most: deployment friction, missing observability, brittle recovery, unclear ownership, unsafe rollout paths, and failure handling gaps. Prefer high-signal findings over broad commentary. Do not repeat the proposal unless needed for context. Call out assumptions that would block safe operation. If the design is acceptable, say what remains as residual operational risk.

# Output Expectations

- Start with the highest-severity operability findings.
- For each finding, include the operational impact and a concise fix or mitigation.
- End with a short residual-risk summary.
