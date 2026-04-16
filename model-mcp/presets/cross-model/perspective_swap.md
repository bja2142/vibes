---
name: perspective_swap
category: cross-model
mode: cross-model
summary: Review the same material from several stakeholder or discipline viewpoints.
---

# Goal

Force a clean perspective shift so the same proposal is examined as if by different reviewers with different priorities.

# When To Use

- You want security, ops, product, and engineering angles.
- You suspect one viewpoint is dominating the discussion.
- You need a compact multi-lens critique.

# Prompt

Review the material from multiple distinct perspectives and keep the results separated. Use only the viewpoints that materially change the judgment.

Default lenses:
- `security`
- `operations`
- `product`
- `maintainability`

For each lens, state the most important concern and the highest-value recommendation. Avoid overlap unless it is genuinely important from that lens.

Use this format:
- `security`
- `operations`
- `product`
- `maintainability`

# Output Expectations

- Separate lens-based feedback.
- One or two high-value points per lens.
- No blended summary unless requested.
