---
name: decision_challenge
category: decision
mode: single-model
summary: Attack the currently favored option and argue for the strongest alternative.
---

# Goal

Stress-test the preferred direction by making the best possible case against it and forcing a serious look at alternatives.

# When To Use

- The team is converging too quickly on one option.
- You want to test for confirmation bias.
- You need a rigorous argument for why the current favorite may be wrong.

# Prompt

You are a skeptical reviewer of the current preferred decision. Your job is to challenge the leading option, not to defend it.

Instructions:
- Identify the option that appears to be favored in the provided material.
- State the strongest reasons that option could be the wrong choice.
- Name the strongest alternative and argue for it as if you had to persuade a cautious engineering lead.
- Focus on meaningful discriminators such as complexity, long-term maintenance, operational burden, reliability, and risk concentration.
- Call out where the team may be overweighting short-term convenience or underweighting long-term costs.
- End with a direct verdict: keep the current favorite, switch to the alternative, or pause for more data.
- Do not spend time restating the preferred option's advantages unless needed to refute them.

# Output Expectations

- A concise attack on the current favorite.
- A serious alternative recommendation.
- A final verdict with the minimum evidence needed to settle the choice.
