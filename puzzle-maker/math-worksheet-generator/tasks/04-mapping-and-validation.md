# Task 04: Mapping And Validation

## Goal

Implement the answer-to-reveal mapping engine and the worksheet-level validation rules that make the reveal deterministic.

## Scope

- Assign solved problems to letters, answer slots, and distractor roles
- Enforce unique numeric answers per distinct letter
- Validate full answer reconstruction and display unambiguity
- Add retry logic when generation cannot satisfy mapping constraints

## Deliverables

- Mapping engine
- Worksheet-level validator
- Failure and retry strategy for constraint collisions

## Acceptance Criteria

- No two distinct letters share the same numeric answer
- Repeated letters reuse a consistent mapping
- Invalid worksheets fail before rendering

