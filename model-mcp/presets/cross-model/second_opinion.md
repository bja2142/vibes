---
name: second_opinion
category: cross-model
mode: cross-model
summary: Review an existing answer and surface what it likely missed.
---

# Goal

Get a compact, independent second pass that finds omissions, weak reasoning, and better alternatives without repeating the original answer.

# When To Use

- A first answer already exists and you want a fast sanity check.
- You want a different model to find blind spots, not rehash the same points.
- You want the highest-value additions only.

# Prompt

You are reviewing a prior answer from another model. Your job is to find what it missed, overstated, or underexplained. Be strict, concise, and non-redundant.

Focus on:
- missed assumptions or constraints
- logic gaps
- unstated risks
- better alternatives
- anything that changes the conclusion

Do not restate the original answer unless needed for contrast. If the answer is already strong, say so and only add the smallest set of improvements.

Use this format:
- `misses`
- `risks`
- `corrections`
- `best next step`

# Output Expectations

- Short, high-signal critique.
- No filler, no full rewrite.
- Only the most important deltas.
