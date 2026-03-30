# Task 18: Styling Retry And Recovery

## Goal

Add an explicit user-facing retry path for styling failures so a worksheet can be restyled later without regenerating the entire worksheet from scratch.

## Scope

- Retry failed styling from an existing plain worksheet run
- Preserve the base worksheet artifacts
- Reuse the persisted styling prompt and run metadata where appropriate

## Requirements

- Add an API endpoint that retries styling for an existing worksheet run when the run is in a retryable styling state
- Retryable states should include at least:
  - styled failed verification
  - styled failed error
- Retrying styling must:
  - keep the base worksheet artifacts untouched
  - create a new styling job
  - update styling status to a queued/running retry state
  - write new styled/debug/verification artifacts on completion
- The detail view must expose a retry action only when it is valid
- The popup or modal should show progress for the retry job the same way it does for the initial styling job
- The system must preserve enough debug information from the prior failure to make the retry inspectable
- The implementation must avoid silently deleting runs or artifacts after repeated styling failures

## Deliverables

- Retry styling API
- Retry button in detail view for eligible runs
- Styling retry job integration
- Updated styling-state presentation

## Dependencies

- Tasks 10 through 17

## Acceptance Criteria

- A failed styling run can be retried without regenerating the worksheet itself
- A successful retry updates the run with new styled artifacts
- A failed retry leaves the plain worksheet intact and preserves debug evidence
