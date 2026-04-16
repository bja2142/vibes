---
name: priority_review
category: decision
mode: single-model
summary: Rank issues or opportunities by severity, urgency, uncertainty reduction, and effort.
---

# Goal

Turn an undifferentiated list of problems or opportunities into an execution order that reflects actual leverage.

# When To Use

- There are too many possible next steps.
- Bugs, risks, or ideas all sound important and need ranking.
- You need a practical order for engineering attention.

# Prompt

You are reviewing a set of candidate issues, risks, or opportunities and must prioritize them for action.

Instructions:
- Rank the items using these factors: severity, urgency, impact on users or the business, uncertainty reduction, implementation effort, and dependency order.
- Explain why each top-ranked item deserves its place.
- Identify low-priority items that seem noisy, speculative, or safe to defer.
- If two items are close, state the tie-breaker explicitly.
- Prefer priorities that reduce downside risk early or unlock multiple downstream decisions.
- End with a recommended execution order and a short note on what would change the ranking.
- Do not produce a flat list without reasoning.

# Output Expectations

- A ranked list with short reasoning for each top item.
- A separate defer list.
- The main ranking assumptions or tie-breakers.
