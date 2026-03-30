# Task 02: Reward Content Flow

## Goal

Implement direct-input and assisted reward-content workflows with explicit approval gating.

## Scope

- Support direct prompt and solution input
- Add structured candidate generation inputs for theme, learner band, style, and language
- Implement the review state machine: `PENDING`, `REJECTED`, `EDITED`, `APPROVED`
- Stub Gemini integration behind an interface so it can be mocked in tests
- Add reading-level and appropriateness checks

## Deliverables

- Reward-content service layer
- Review and approval logic
- Mockable assisted-generation adapter

## Acceptance Criteria

- Worksheet generation is blocked unless content is `APPROVED`
- Generated content can be accepted, rejected, or edited before approval
- Direct-input mode bypasses external generation while still enforcing approval

