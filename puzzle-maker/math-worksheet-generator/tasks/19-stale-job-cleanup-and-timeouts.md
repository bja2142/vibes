# Task 19: Stale Job Cleanup And Timeouts

## Goal

Make the background job system more robust by cleaning up stale in-progress state on startup and by enforcing timeout handling for long-running generation or styling work.

## Scope

- Startup reconciliation for stale jobs and runs
- Timeout handling for background generation and styling jobs
- Safe failure-state transitions for interrupted work

## Requirements

- On application startup, inspect persisted jobs and runs for stale in-progress work
- Define how to reconcile cases such as:
  - generation job marked running but thread/process is gone
  - styling job marked running but no worker is active
  - run marked awaiting confirmation for a long time
- Mark irrecoverably stale work as failed or cancelled with a clear message instead of leaving it pending forever
- Add configurable timeout thresholds for:
  - worksheet generation
  - worksheet styling
- When a timeout is reached:
  - mark the job failed
  - update the worksheet run state coherently
  - preserve already-written artifacts when safe
  - never leave the gallery entry in a permanently running state
- Emit clear logs for stale-job reconciliation and timeout-triggered failure
- Keep the implementation compatible with Docker restarts and persisted SQLite state

## Deliverables

- Startup stale-job reconciliation logic
- Timeout checks for background jobs
- Clear failure-state updates and logs

## Dependencies

- Task 15
- Task 16
- Existing persisted job/run storage

## Acceptance Criteria

- Restarting the app does not leave obviously dead jobs stuck in a running state forever
- Timed-out runs are visible as failed with useful status messaging
- Persisted artifacts remain inspectable when appropriate
