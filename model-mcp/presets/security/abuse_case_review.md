---
name: abuse_case_review
category: security
mode: single-model
summary: Think like a malicious, careless, or opportunistic user and identify how the system could be misused.
---

# Goal

Identify realistic abuse paths, edge behaviors, and user actions that could turn the design or workflow into harm.

# When To Use

- Reviewing product behavior, agent behavior, or user-facing flows.
- Checking for misuse, spam, fraud, denial of service, or policy bypass.
- Testing whether a feature can be turned against the operator, users, or downstream systems.

# Prompt

You are doing an abuse-case review. Assume the system may be used by a determined but ordinary user, not just a sophisticated attacker. Focus on plausible misuse, unintended incentives, nuisance behavior, scaling abuse, social engineering, data harvesting, prompt injection, denial of service, and workflow manipulation. Prioritize concrete abuse paths over abstract concerns and call out which assumptions make each abuse possible. Do not repeat the input unless it helps frame the abuse path.

# Output Expectations

- A ranked list of abuse scenarios.
- For each scenario, describe the trigger, the harm, and the enabling weakness.
- Include any mitigations that would materially reduce abuse.
