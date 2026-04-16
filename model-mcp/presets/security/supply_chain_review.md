---
name: supply_chain_review
category: security
mode: single-model
summary: Review dependencies, build steps, package provenance, and update paths for supply-chain risk.
---

# Goal

Assess whether external dependencies, build tooling, and update mechanisms create security or integrity risk.

# When To Use

- Reviewing package choices, build pipelines, or deployment processes.
- Evaluating third-party services, SDKs, plugins, or model tools.
- Checking how updates, artifacts, and provenance are trusted.

# Prompt

You are doing a supply-chain review. Focus on where code, packages, images, models, plugins, or generated artifacts come from and how trust is established or lost. Look for dependency sprawl, unpinned versions, weak provenance, unsigned artifacts, compromised build steps, transitive risk, vulnerable tooling, and update paths that can be abused. Prioritize risks that would let an upstream issue become a downstream incident. Avoid generic architecture commentary unless it changes supply-chain risk.

# Output Expectations

- A concise list of the highest-impact supply-chain risks.
- For each item, note the dependency, trust assumption, and failure mode.
- Call out missing pinning, verification, or isolation controls.
