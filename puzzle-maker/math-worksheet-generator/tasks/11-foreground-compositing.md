# Task 11: Foreground Compositing

## Goal

Guarantee worksheet content preservation by reapplying an immutable semantic foreground layer on top of the Gemini-styled image.

## Scope

- Render the worksheet semantic layer separately from decorative/background content
- Composite the semantic foreground over the Gemini-styled result
- Keep the final styled worksheet visually themed while semantically identical to the original worksheet

## Requirements

- Generate a foreground layer that contains every semantically important worksheet element, including:
  - title
  - prompt text
  - note text
  - problem labels
  - equations and numeric content
  - lookup entries
  - answer boxes and answer letters
  - grid labels and cell labels where applicable
  - structural lines that define worksheet meaning
- Exclude purely decorative backgrounds and non-semantic ornamentation from the immutable foreground layer
- Preserve transparency correctly so the Gemini-styled background remains visible behind semantic content
- Composite the foreground over the styled image in a deterministic way
- Ensure the composited result is pixel-aligned with the original layout so text does not blur or drift
- Preserve both original and styled variants so failures can be diagnosed
- Use compositing for both worksheet preview styling and any downloadable styled PNG variant
- If a styled PDF is later supported, build it from the composited styled image rather than from the raw Gemini output

## Deliverables

- Semantic foreground render path
- Image compositing utility
- Styled artifact variant that uses the composited result

## Dependencies

- Task 10 Gemini image styling service
- Existing renderer/exporter with enough structure to isolate semantic content

## Acceptance Criteria

- The final styled worksheet preserves all original text and structural content exactly
- Semantic content remains readable even if Gemini heavily decorates the page
- A failed or low-quality Gemini background still cannot corrupt worksheet meaning
