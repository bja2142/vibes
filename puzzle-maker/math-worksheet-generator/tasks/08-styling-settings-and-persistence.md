# Task 08: Styling Settings And Persistence

## Goal

Add all user-configurable settings and storage needed to request worksheet image styling and preserve its results.

## Scope

- Extend the UI configuration model
- Extend persisted worksheet-run metadata
- Extend artifact metadata for original and styled images
- Keep the styling feature compatible with the current job popup, gallery, and modal viewer

## Requirements

- Add a user-facing option to enable or disable Gemini styling for a generated worksheet
- Add a style selector that mirrors the reference `puzzle-maker` style catalog as closely as practical
- Add a color-treatment selector aligned with the reference prompt structure
- Add an ink-saver or low-ink styling option aligned with the reference prompt structure
- Reuse the existing worksheet theme field as the theme input for styling
- Persist enough state to distinguish:
  - styling requested in the form
  - plain worksheet generated and awaiting styling confirmation
  - styling cancelled by the user after reviewing the plain render
  - styling started
  - styling completed or failed
- Persist the following with each run or equivalent database model:
  - whether styling was requested
  - requested style name
  - requested color mode
  - ink-saver flag
  - effective theme text
  - effective Gemini styling model
  - styling prompt text after refinement
  - styling status
  - verification status
  - whether the user confirmed the styling stage after previewing the plain worksheet
  - original artifact paths
  - styled artifact paths
  - verification/debug artifact paths if generated
- Persist the styling settings in the same way the existing control-panel settings are preserved across refreshes
- If `GEMINI_API_KEY` is missing, styling settings must not be actionable and the UI must explain why
- The persisted schema must remain backward-compatible with existing runs that have no styling metadata

## Deliverables

- Updated UI settings schema
- Database migration or startup schema update
- Repository/service persistence support for styling fields
- Artifact metadata support for original and styled variants

## Dependencies

- Task 07 feature overview
- Existing `localStorage` panel persistence
- Existing SQLite persistence and gallery artifact model

## Acceptance Criteria

- A user can configure styling intent before generation
- Styling settings are saved with the worksheet run and survive page refresh
- Existing non-styled runs continue to load correctly
- The UI degrades cleanly when Gemini credentials are unavailable
