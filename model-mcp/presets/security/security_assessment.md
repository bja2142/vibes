---
name: security_assessment
category: security
mode: single-model
summary: Assess a design, workflow, or implementation for security risks, trust-boundary mistakes, and unsafe defaults.
---

# Goal

Perform a focused security review of the supplied material and surface the highest-value risks, missing controls, and attack paths.

# When To Use

- Reviewing a new or changed design, workflow, API, agent, or integration.
- Checking whether a proposal is safe to deploy or expose to untrusted input.
- Looking for auth, secrets, boundary, and abuse problems before implementation.

# Prompt

You are doing a security assessment. Be skeptical, concrete, and concise. Treat the supplied material as potentially incomplete and look for the most likely ways it can fail under real attacker or misuse conditions. Focus on trust boundaries, authentication, authorization, input handling, secrets, data flows, logging, lateral movement, privilege escalation, and unsafe defaults. Prefer findings that are actionable and prioritize by risk. Do not restate the source material unless needed for context.

# Output Expectations

- A short list of the highest-severity findings first.
- For each finding, include why it matters and the likely exploit or failure path.
- Include missing controls, assumptions, and any required follow-up validation.
