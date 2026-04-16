---
name: tradeoff_matrix
category: decision
mode: single-model
summary: Compare candidate options across cost, complexity, risk, reversibility, and time-to-value.
---

# Goal

Produce a structured comparison of the available options and make the tradeoffs explicit instead of letting the discussion stay impressionistic.

# When To Use

- There are two or more viable approaches on the table.
- The discussion is circling without a clear decision framework.
- You need a recommendation that shows why one option wins.

# Prompt

You are evaluating a decision with multiple plausible options. Build a concise tradeoff matrix from the provided material.

Instructions:
- Identify the candidate options explicitly. If the input only names one option, infer the most likely alternatives and label them as inferred.
- Compare the options across these dimensions: implementation complexity, operational risk, security risk, cost, performance impact, reversibility, and time-to-value.
- For each dimension, give a short judgment and explain the reason in one or two sentences.
- Call out where the decision depends on assumptions, missing data, or changing priorities.
- End with a recommendation, the conditions under which it should change, and the next information that would reduce uncertainty fastest.
- Avoid generic pros-and-cons lists that do not discriminate between the options.

# Output Expectations

- A table or clearly structured matrix covering the required dimensions.
- A short recommendation with explicit caveats.
- A short list of the highest-value unknowns.
