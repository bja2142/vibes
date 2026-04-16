---
name: premortem
category: critique
mode: single-model
summary: Assume the effort failed and explain why.
---

# Goal

Work backward from failure to identify the most plausible reasons the plan would not survive contact with reality.

# When To Use

- You want to de-risk a project before committing.
- You need to identify failure paths early.
- You want a candid review without optimism bias.

# Prompt

Assume this project failed after shipping or during execution. Work backward from that failure.

Focus on:
- the most plausible failure causes
- what was underestimated
- where execution would stall
- what would break under real usage
- what would make the project look successful briefly but fail later

Do not hedge. Treat the failure as real and explain the chain of events that caused it.

Return only:
1. The likely failure modes.
2. The trigger or condition that exposes each one.
3. The earliest warning sign to watch for.

# Output Expectations

- 3 to 6 failure modes.
- Rank by plausibility and impact.
- Keep the answer direct and concrete.
