# Task 03: Problem Generation

## Goal

Build modular math-problem generators that are learner-band-aware and deterministic under a seed.

## Scope

- Start with addition, subtraction, multiplication, and exact division
- Encode parameter ranges and difficulty controls per family
- Support difficulty gradients within one worksheet
- Verify every generated problem before it enters the worksheet

## Deliverables

- One generator module per problem family
- Shared problem-validation helpers
- Seeded generation support

## Acceptance Criteria

- Supported learner bands map to the right problem families
- Generated answers are canonical and mathematically correct
- Re-running generation with the same seed produces the same problems

