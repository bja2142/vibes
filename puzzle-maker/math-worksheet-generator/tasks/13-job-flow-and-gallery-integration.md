# Task 13: Job Flow And Gallery Integration

## Goal

Integrate the styling workflow into the existing generation popup, live job status, artifact gallery, and worksheet modal.

## Scope

- Extend the current workflow popup to include styling stages
- Show styling progress and timing in real time
- Present original and styled artifacts in the gallery and modal viewer
- Insert a plain-render review checkpoint before the styling stage begins

## Requirements

- Add styling stages to the job tracker, such as:
  - render base worksheet
  - review plain worksheet
  - confirm or cancel styling
  - refine styling prompt
  - apply Gemini styling
  - composite semantic foreground
  - verify styled worksheet
  - retry styling if needed
  - complete
- Reuse the existing blocking/polling-friendly job model so the popup can track this work in real time
- Keep cancellation behavior coherent with the existing popup flow
- After the base worksheet render completes, the popup must show the plain worksheet preview and let the user either:
  - continue into styling
  - cancel styling and keep the base worksheet only
- If the user cancels at that checkpoint, the workflow should finish cleanly with the base worksheet preserved and styling marked as cancelled or skipped
- Show whether a worksheet run has:
  - original only
  - original plus styled
  - original approved but styling cancelled before execution
  - styled failed and original retained
- In the gallery tile and modal:
  - show original preview
  - show styled preview when present
  - allow full-resolution preview without forcing download
  - keep download buttons separate from image preview clicks
- Make styled artifacts searchable through stored run metadata
- Preserve backward compatibility for existing gallery runs that have no styling data
- If styling is disabled or unavailable, the UI must not present broken or misleading controls

## Deliverables

- Updated workflow popup
- Updated gallery tile metadata
- Updated worksheet modal with original/styled variants
- Search metadata support for styling-related fields

## Dependencies

- Tasks 08 through 12
- Existing modal, gallery, and job-tracking UI

## Acceptance Criteria

- A user can watch the base-render stage complete, inspect the plain worksheet, and explicitly choose whether to start styling
- A user can watch the styling stage progress in the same popup used for generation
- Styled artifacts appear in the gallery alongside the base worksheet without replacing it invisibly
- Existing runs remain viewable and searchable
- The UI makes it clear whether styling succeeded, failed, or was not requested
