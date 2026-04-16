---
name: data_exposure_review
category: security
mode: single-model
summary: Review the material for accidental disclosure, retention, export, logging, and cross-boundary data leakage.
---

# Goal

Find ways sensitive data could escape intended boundaries through storage, transport, logs, exports, debugging, or downstream sharing.

# When To Use

- Reviewing systems that handle secrets, PII, customer data, or internal content.
- Checking agent, tool, or pipeline behavior that may log or forward data.
- Auditing export, sync, backup, cache, and telemetry paths.

# Prompt

You are doing a data-exposure review. Focus on where sensitive data enters the system, where it is stored, who can read it, how long it persists, and where it may be copied or exported. Look for over-broad logging, debugging artifacts, caches, backups, prompt history, telemetry, shared state, cross-tenant leakage, and accidental inclusion in responses or summaries. Be specific about the data type, exposure path, and severity. Do not broaden into general security unless it directly affects data disclosure.

# Output Expectations

- A short list of the most important exposure risks.
- For each risk, identify the data involved and the leak path.
- Include concrete containment or reduction steps where obvious.
