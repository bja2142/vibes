# Task 12: Style Verification And Retry

## Goal

Verify that the styled worksheet still preserves the original content, retry once with a stricter prompt if necessary, and surface any remaining failure cleanly.

## Scope

- Add post-styling verification
- Retry one time on verification failure
- Save verification/debug artifacts when useful

## Requirements

- Verify the final composited styled worksheet, not just the raw Gemini output
- Verification must focus on text and semantic integrity, including at least:
  - title
  - prompt text
  - question labels
  - representative equations or problem text
  - lookup entries when present
  - final answer labels when present
- Region-based verification is acceptable and preferred over whole-page OCR if the renderer can provide precise bounding boxes
- OCR may be used, but the implementation should take advantage of known layout coordinates rather than treating the page as an unstructured image
- If verification fails:
  - retry the styling stage once with a stricter preservation prompt
  - record the retry event in job history
  - preserve failure details for debugging
- If verification still fails after retry:
  - mark styling as failed
  - keep the base worksheet available
  - surface the styling failure in the UI without discarding the worksheet
- Save a verification/debug artifact when practical, such as an overlay or structured report showing failed regions
- Verification logic must be mockable and testable without requiring live external API calls

## Deliverables

- Verification module
- Retry policy for styling failures
- Debug artifact or report format
- Job-status integration for verification events

## Dependencies

- Task 11 foreground compositing
- Existing job/event tracking

## Acceptance Criteria

- Styling failures do not silently ship corrupted text
- A verification failure triggers at most one retry
- The final user-visible result is either a verified styled worksheet or a clean fallback to the original worksheet with an error state recorded
