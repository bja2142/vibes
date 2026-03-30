# Task 17: Detail View And Auto-Open Flow

## Goal

Make the final preview handoff behave more like the reference word-search product: generation should land the user in the actual run detail view automatically, with plain and styled outputs treated as phases of the same run rather than separate manual navigation steps.

## Scope

- Auto-open the generated run once the relevant phase is complete
- Make the detail modal the canonical viewer for plain and styled artifacts
- Reduce redundant workflow controls once the preview exists

## Requirements

- When base worksheet generation completes and styling is not requested:
  - the workflow popup should hand off directly to the run detail view
- When base worksheet generation completes and styling is requested:
  - the popup should show the plain worksheet preview and styling decision controls
  - after styling completes, the same run detail view should be easy to open automatically or with a single obvious action
- The detail view should be the main place to inspect:
  - plain preview
  - solution
  - raw Gemini styled background
  - composited styled preview
  - verification overlay
  - debug JSON/report artifacts
- The detail view should clearly show styling state:
  - not requested
  - awaiting confirmation
  - cancelled
  - in progress
  - verified
  - failed with plain retained
- If the workflow popup is closed and reopened later, the run detail should still be the canonical inspection path
- If practical, support URL-addressable detail state for deep-linking or browser refresh continuity

## Deliverables

- Updated auto-open behavior after generation phases
- Improved run detail modal or route
- Clear artifact ordering and state presentation in the detail view

## Dependencies

- Task 15
- Task 16
- Existing modal/gallery artifact rendering

## Acceptance Criteria

- A user does not have to hunt through the gallery to find the run they just generated
- The detail view reads as the single source of truth for the run’s output artifacts
- Plain-only and styled runs are both understandable from the same UI surface
