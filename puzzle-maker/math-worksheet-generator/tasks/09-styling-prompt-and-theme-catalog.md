# Task 09: Styling Prompt And Theme Catalog

## Goal

Create a worksheet-specific styling prompt builder that follows the reference `puzzle-maker` structure while enforcing worksheet-preservation rules.

## Scope

- Mirror the reference style catalog
- Build a worksheet styling prompt from theme, style, color mode, and ink-saver settings
- Add optional prompt refinement with Gemini text generation
- Explicitly constrain the model to decorate without changing worksheet content

## Requirements

- Define an internal style catalog that aligns with the reference project wherever practical, including styles such as:
  - cartoon
  - watercolor
  - sketch
  - flat
  - isometric
  - cyberpunk
  - origami
  - steampunk
  - pixel-art
  - oil-painting
  - crayon
  - blueprint
  - stained-glass
  - pop-art
  - chalkboard
- Preserve the reference prompt shape conceptually:
  - theme
  - style
  - color treatment
  - ink use
  - explicit preservation constraints
  - decorative guidance
- Adapt the preservation section for worksheets with hard rules:
  - do not change or redraw any text
  - do not change or redraw any numbers
  - do not change equations or symbols
  - do not move labels, boxes, or lines
  - do not alter lookup entries, answer slots, question content, or titles
  - only decorate whitespace, borders, margins, and background areas
- Add a prompt-refinement step modeled on the reference flow using a cheap Gemini text model
- Default the refinement model to `gemini-2.5-flash-lite`
- Store both the pre-refinement and post-refinement prompts for debugging
- Include theme-aware examples or phrasing so styling remains tied to the user’s requested theme rather than becoming generic decoration

## Deliverables

- Style catalog module
- Worksheet styling prompt builder
- Optional prompt refinement helper
- Stored prompt artifacts or metadata for auditability

## Dependencies

- Task 08 styling settings and persistence
- Existing Gemini text-generation support in the codebase

## Acceptance Criteria

- The prompt builder produces deterministic, inspectable prompts from stored settings
- The style catalog exposed in the UI matches the backend prompt builder
- The prompt clearly forbids content mutation and restricts styling to non-semantic regions
- Prompt refinement can be disabled, mocked, or overridden in tests
