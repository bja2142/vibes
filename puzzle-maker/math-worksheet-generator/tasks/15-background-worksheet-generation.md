# Task 15: Background Worksheet Generation

## Goal

Move base worksheet generation onto the persisted job system so the request/response path returns quickly and the popup tracks real generation progress instead of waiting for a blocking HTTP call to finish.

## Scope

- Convert base worksheet generation into a background job
- Reuse the existing `generation_jobs` table instead of inventing a second mechanism
- Keep plain-render review before styling
- Preserve current artifact persistence and gallery behavior

## Requirements

- `POST /api/worksheets/generate` must:
  - validate input
  - create the worksheet run row
  - create a `worksheet_generate` job row
  - queue the actual worksheet build in the background
  - return immediately with the run id and job id
- The base generation work that is currently executed inline must move behind a worker function or background thread/process
- The popup must poll the generation job while the base worksheet is being built
- The job progress messages must cover the real worksheet stages, such as:
  - prepare approved draft
  - assemble worksheet
  - export preview and solution
  - write manifest and metadata
  - persist artifacts
- When base generation completes successfully:
  - the run must be marked complete for the plain worksheet phase
  - the preview and solution artifacts must already be attached
  - the UI must transition into plain review if styling was requested
  - the UI must open the run detail directly if styling was not requested
- When base generation fails:
  - the run must be marked failed
  - the job must be marked failed
  - the popup must show the error without leaving the run in an ambiguous pending state
- The implementation must preserve the existing persistent artifact directory structure
- The implementation must not break Docker-only development and test workflows

## Deliverables

- Background worksheet generation worker or thread entrypoint
- Updated `POST /api/worksheets/generate` behavior
- Popup polling for base worksheet generation jobs
- Persisted phase-aware job messages for base generation

## Dependencies

- Existing worksheet run persistence
- Existing generation job persistence
- Existing popup polling flow

## Acceptance Criteria

- Clicking generate returns quickly instead of blocking until the worksheet finishes rendering
- The popup shows real worksheet-generation progress while the run is being built
- A successful run automatically transitions into plain review or detail view without requiring a second manual fetch path
- Failed runs leave behind a coherent run/job state that can be inspected later
