# Task 16: Run State Machine And Phase Model

## Goal

Give the worksheet product one coherent, reviewable lifecycle model similar to the reference word-search flow, while preserving worksheet-specific stages like reward-content review and plain-preview styling confirmation.

## Scope

- Define the visible lifecycle states for a worksheet run
- Define the structured phases used by jobs and popup progress
- Reduce reliance on ad hoc status text as the only source of truth

## Requirements

- Introduce a documented run lifecycle that covers at minimum:
  - draft generation requested
  - draft ready for review
  - draft approved
  - worksheet generation queued
  - worksheet generation running
  - plain worksheet ready
  - awaiting styling confirmation
  - styling queued
  - styling running
  - styling verified
  - styling failed with plain retained
  - styling cancelled after plain review
  - run failed
- Introduce structured phase identifiers for job progress instead of relying only on free-form text
- The popup UI must be able to render progress from these structured phases without parsing English strings
- The gallery/detail view must expose the current run state in a way that is stable across refreshes
- The draft/reward-content workflow may remain implemented with its own table, but the user-facing workflow must read as one worksheet run lifecycle
- The repository/API layer must return enough structured state for the frontend to:
  - show phase checklist progress
  - know whether confirm/cancel styling actions are legal
  - know whether the final output is plain only, styled, cancelled, or failed
- The state model must remain backward compatible for older runs already stored in SQLite

## Deliverables

- Documented worksheet run state machine
- Structured phase fields or payloads in job/run responses
- Popup checklist driven by structured phase data
- Backend translation layer for older runs where needed

## Dependencies

- Task 15
- Existing worksheet run and generation job tables

## Acceptance Criteria

- The frontend no longer depends on brittle string matching to understand major workflow phases
- A developer can inspect one run record and understand exactly where it is in the lifecycle
- Old runs remain viewable even if they do not have every new state field populated
