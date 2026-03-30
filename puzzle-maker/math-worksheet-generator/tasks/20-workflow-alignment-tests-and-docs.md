# Task 20: Workflow Alignment Tests And Docs

## Goal

Lock in the workflow-alignment changes with automated tests and update the documentation so another agent or engineer can review the run lifecycle against the intended design.

## Scope

- Add coverage for background generation and structured phases
- Add coverage for auto-open/detail behavior where practical
- Document the new lifecycle, retry, and cleanup behavior

## Requirements

- Add automated tests for:
  - non-blocking worksheet generation request behavior
  - background job progression for base worksheet generation
  - structured phase progression for generation and styling
  - plain-review checkpoint behavior
  - detail-view run loading after completion
  - styling retry eligibility and retry success/failure paths
  - startup cleanup of stale jobs
  - timeout-triggered job failure
- Where browser-automation is too heavy, add shell/API-level tests that still prove the lifecycle transitions
- Update reviewer-facing documentation to describe:
  - the end-to-end worksheet run lifecycle
  - the distinction between draft, plain worksheet, and styled worksheet phases
  - retryable vs non-retryable failure states
  - stale-job cleanup behavior
  - timeout behavior
- Update the task index and any relevant design notes so the workflow-alignment work is easy to review in order

## Deliverables

- Automated tests for workflow alignment
- Updated README/design/task documentation

## Dependencies

- Tasks 15 through 19
- Existing Docker-native test workflow

## Acceptance Criteria

- The new workflow behavior is testable in Docker without requiring live external Gemini calls
- Another engineer can understand the intended run lifecycle and recovery behavior from the documentation alone
- Regressions in job sequencing, retry handling, or stale-job cleanup are caught by the test suite
