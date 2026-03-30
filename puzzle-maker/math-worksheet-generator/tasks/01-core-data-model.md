# Task 01: Core Data Model

## Goal

Define the worksheet domain model and manifest format so generation, validation, and rendering share one source of truth.

## Scope

- Introduce `WorksheetSpec`, `RewardContent`, `Problem`, `SolvedProblem`, `LetterAssignment`, `Worksheet`, and `RenderedWorksheet`
- Define learner-band, reveal-mode, and approval-state enums
- Define a worksheet manifest JSON schema or typed serialization format
- Keep module boundaries aligned with the design dependency direction

## Deliverables

- Typed core models
- Manifest serializer and loader
- Sample manifest fixtures for tests and rendering

## Acceptance Criteria

- A generated worksheet can be serialized and re-rendered without regeneration
- Approval state and reveal mode are explicit in the model
- The model supports distractors, repeated letters, and difficulty ranges

