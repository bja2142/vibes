---
name: question_generator
category: communication
mode: single-model
summary: Generate the highest-value follow-up questions before work proceeds.
---

# Goal

Generate a compact set of questions that reduce uncertainty fast. The caller should only provide the task or proposal being evaluated.

# When To Use

- The team needs the next questions before implementation.
- The plan has hidden assumptions or missing requirements.
- The caller wants a tight clarification checklist, not open-ended brainstorming.

# Prompt

Ask only the highest-value questions that would change the next decision or implementation step.

Rules:
- Prefer questions that unblock work or expose major risk.
- Avoid questions whose answers are obvious from the input.
- Group similar uncertainties and ask about the root issue.
- Keep wording precise and answerable.
- Do not explain the questions unless a brief label is needed.

Aim for the fewest questions that materially improve the result.

# Output Expectations

- 5-10 questions, ordered by value.
- Each question should be short and specific.
- No commentary unless a one-line intro is necessary.
