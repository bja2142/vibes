# Task 07: Styled Image Feature Overview

## Goal

Define the end-to-end feature for theme-based worksheet restyling after the base worksheet PNG is rendered.

## Scope

- Add an optional styling stage after base worksheet rendering
- Treat the base worksheet render as a review checkpoint before any styling call is made
- Use Gemini image generation/editing to decorate the worksheet according to the user theme
- Preserve all worksheet text, equations, labels, answer boxes, lookup chips, and other semantic content
- Keep original and styled artifacts side by side in the product
- Match the general style catalog, prompt structure, and API pattern used by the reference `puzzle-maker` project

## Requirements

- The styling feature must operate on the rendered worksheet PNG, not on raw worksheet data
- The styling feature must remain optional and must not block base worksheet generation if Gemini styling fails
- The user must be able to inspect the plain worksheet render before deciding whether to continue into Gemini image styling
- After the plain render is shown, the workflow must offer an explicit continue-or-cancel decision for the styling stage
- Cancelling at that checkpoint must keep the base worksheet run and artifacts without starting styling
- The feature must use environment-provided Gemini credentials only
- If `GEMINI_API_KEY` is absent, styling controls must be hidden or disabled with an explicit explanatory note
- The implementation must follow the reference flow conceptually:
  - style catalog
  - prompt builder
  - optional prompt refinement
  - image styling call
  - verification
  - retry
- The implementation must improve on the reference by preserving worksheet semantics through deterministic compositing, not by trusting prompt instructions alone

## Deliverables

- A written feature contract that other styling subtasks can implement against
- A continued task sequence for settings, prompting, Gemini integration, compositing, verification, UI/job flow, and tests

## Dependencies

- Existing worksheet rendering/export pipeline
- Existing Gemini reward-content integration patterns
- Existing job tracker, gallery, artifact storage, and Docker workflow

## Acceptance Criteria

- The project has a clear documented styling feature plan before implementation begins
- The feature contract clearly states that Gemini may decorate the worksheet image but may not alter worksheet content
- The feature contract clearly states that the plain worksheet must be reviewable before the styling stage begins
- The downstream tasks are sequenced so another agent can implement the feature without re-planning it
