---
name: blind_spot_review
category: critique
mode: single-model
summary: Find what the current discussion has not covered yet.
---

# Goal

Surface missing angles, neglected constraints, and overlooked questions that are likely to matter later.

# When To Use

- You suspect the discussion has tunnel vision.
- You want gaps, omissions, and underexplored risks.
- You want a second-pass critique that does not repeat obvious points.

# Prompt

You are looking for blind spots in the provided material.

Focus on what is absent, underweighted, or not yet considered:
- missing stakeholders
- missing dependencies
- unasked questions
- ignored constraints
- unexamined assumptions
- likely downstream consequences

Avoid repeating points already made unless you are sharpening them with a new consequence. If the material seems complete, say what is still least certain.

Return only:
1. The most important blind spots.
2. Why each one is easy to miss.
3. What to check or ask next.

# Output Expectations

- 3 to 6 blind spots.
- Prefer novel observations over broad commentary.
- Keep the response compact and focused on omissions.
