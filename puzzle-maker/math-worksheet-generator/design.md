# Math Worksheet Generator Design

## Document Purpose

This file is the authoritative product and implementation design for the current math worksheet generator. It is intentionally verbose. The target audience is:

- an engineer implementing additional features
- an engineer reviewing an implementation for correctness
- an agent performing a code review and needing to know what behavior is expected versus incidental

This document is not just a feature brainstorm. It is a review-oriented spec. It should be possible to compare an implementation against this document and identify:

- required behavior
- acceptable flexibility
- explicit invariants
- failure handling expectations
- likely regression risks

The current intended product is a Docker-first Flask application served on port `9595`, backed by SQLite and persistent artifact storage, with Gemini-assisted reward-content generation, a browser UI for configuring, generating, reviewing, searching, and previewing worksheets, and an optional post-render worksheet-image styling stage.

That styling feature must allow Gemini to theme the worksheet visually without changing any worksheet semantics.

## Product Summary

The product generates printable math worksheets where solving math problems reveals the answer to a riddle, pun, or thematic question.

The core user experience is:

1. The user chooses a theme or topic plus worksheet parameters in a single form.
2. The user submits that form once to open a workflow popup.
3. The popup generates reward content with Gemini if available, or falls back to manual review mode if Gemini is unavailable.
4. The popup lets the user review, edit, regenerate, or cancel without leaving the workflow.
5. The popup proceeds through draft approval and worksheet generation while showing live per-job elapsed time and task feedback.
6. The system derives the worksheet length from the approved solution phrase.
7. The system generates a printable worksheet and a solution sheet.
8. The generated artifacts are saved persistently and appear in a gallery.
9. The user can reopen prior worksheet runs, preview images, and download artifacts later.
10. When styling is requested and available, the system first renders the plain worksheet, lets the user review it, and only then proceeds into the optional styled-image stage if the user confirms.
11. If the user cancels at that checkpoint, the base worksheet remains saved and styling is skipped.

The current product is not a one-shot static export tool. It is a persisted generation system with:

- a left-side control pane
- a right-side gallery
- a modal detail view per worksheet run
- a persistent database
- a persistent artifact volume

## Product Goals

Primary goals:

- Make it easy to generate age-appropriate math worksheets that reveal an answer phrase.
- Keep the reward mechanic motivating but secondary to the math.
- Make all generated artifacts durable across container restarts.
- Make generated runs searchable by stored parameters and content metadata.
- Support both direct review/edit flows and Gemini-assisted reward-content generation.
- Make review and debugging easy by persisting run metadata, manifests, and downloadable artifacts.

Secondary goals:

- Keep the rendering printer-friendly and legible.
- Keep the system deterministic when a seed is provided.
- Allow future progress streaming without redesigning persistence.
- Keep Gemini-based worksheet-image styling safe, reviewable, and debuggable.

## Non-Goals

These are not required in the current design:

- multi-user auth
- cloud deployment concerns beyond Dockerized local/runtime behavior
- live collaborative editing
- direct browser-side Gemini calls
- vector editing inside the browser
- arbitrary problem-type plugin installation from the UI
- freeform drawing or WYSIWYG worksheet layout editing

## Deployment And Runtime Model

The expected runtime model is Docker-only.

Requirements:

- The application is built and run through Docker.
- The normal local workflow uses `docker compose`.
- The web app is exposed on port `9595`.
- The app should not require running Python directly on the host.

Expected Docker resources:

- one persistent volume for SQLite data
- one persistent volume for generated worksheet artifacts

Expected Compose behavior:

- `docker compose up -d --build` brings up the latest app
- `docker compose down` tears down the services without losing persistent volumes

The app should tolerate restarts without losing:

- worksheet runs
- reward-content drafts
- generation-job records
- generated artifacts
- styling request metadata
- styled artifacts and verification/debug artifacts

The current implementation also expects:

- worksheet generation and styling jobs are persisted as queued work in SQLite
- startup background workers claim queued jobs from SQLite rather than launching one thread per request
- timeout enforcement is owned by a background watchdog rather than per-request timers

## High-Level System Architecture

The system has five main layers:

1. Web application layer
2. Reward-content generation and review layer
3. Worksheet assembly layer
4. Rendering and export layer
5. Persistence layer

### 1. Web Application Layer

This layer serves:

- the HTML UI
- JSON APIs
- generated artifact files

It is responsible for:

- exposing app configuration
- handling form submissions
- validating high-level request shape
- orchestrating worksheet generation
- returning usable error responses instead of raw stack traces

### 2. Reward-Content Generation And Review Layer

This layer handles:

- Gemini prompt construction
- Gemini API integration
- structured JSON response parsing
- draft creation
- draft editing
- draft approval workflow

### 3. Worksheet Assembly Layer

This layer handles:

- selecting appropriate problem generators
- generating problems
- solving and verifying generated problems
- mapping problems onto reveal tokens
- validating the worksheet

### 4. Rendering And Export Layer

This layer handles:

- SVG page generation
- preview-sheet rendering
- solution-sheet rendering
- export to `svg`, `png`, and `pdf`
- semantic foreground export for styled-image compositing
- styled preview artifact export after compositing

The renderer is also expected to support a semantic-foreground render path so styled worksheet images can be composited safely.

### 5. Persistence Layer

This layer handles:

- SQLite tables for runs, artifacts, jobs, and reward-content drafts
- persisted artifact directory layout
- gallery search
- worksheet-run retrieval
- storage of styling intent, styled-artifact metadata, verification status, and style-check/debug artifact references
- persisted styling job status and progress messages through the shared job table

## Worksheet Image Styling

Worksheet image styling is part of the current product design and backend implementation.

The feature is intended to:

- take the rendered worksheet PNG as input
- apply theme-aware visual styling to the image
- let the user review the plain worksheet render before the styling call begins
- preserve all worksheet text, equations, labels, lookup entries, answer boxes, and layout structure
- save the original and styled variants side by side

Hard rules for this feature:

- Gemini may decorate backgrounds, borders, margins, and empty whitespace
- Gemini may not alter any semantic worksheet content
- the workflow must include an explicit confirm-or-cancel checkpoint after the plain worksheet render is available
- cancelling at that checkpoint must preserve the plain worksheet and skip styling
- the implementation must not rely on prompt wording alone to preserve worksheet integrity
- the final implementation must reapply an immutable semantic foreground layer over the Gemini output
- verification and retry are required
- the base worksheet must remain available even if styling fails

Implemented execution expectations:

1. The user may request styling from the main form.
2. The base worksheet must be rendered first and persisted before any styling call begins.
3. The workflow popup must show the plain worksheet and require explicit confirmation before styling starts.
4. Confirming styling must create a persisted `worksheet_style` job.
5. The backend must refine the styling prompt, apply Gemini image styling, composite the semantic foreground, verify the result, and retry once if verification fails.
6. A failed styling attempt must leave the base worksheet intact and visible.
7. The system must persist both status metadata and debugging artifacts for failed or questionable styling runs.

Current persisted styling states include at least:

- `awaiting_confirmation`
- `confirmed_pending_styling`
- `styling_in_progress`
- `retry_pending_styling`
- `retry_in_progress`
- `styled_verified`
- `styled_failed_verification`
- `styled_failed_error`
- `cancelled_after_plain_review`

The styling implementation is intentionally sequenced into explicit tasks:

- feature overview
- settings and persistence
- prompt builder and style catalog
- Gemini image-styling service
- semantic foreground compositing
- verification and retry
- job-flow and gallery integration
- tests and documentation

Those task files under [tasks](/home/ben/playground/math-worksheet-generator/tasks) are part of the implementation contract and are intended to support code review.

## Verification Strategy

The expected verification strategy is layered.

### 1. Logic And Service Verification

Automated Python tests must cover:

- reward-content generation and review transitions
- worksheet assembly and validation
- rendering and export behavior
- styling prompt construction
- styling verification and retry
- timeout handling
- cancellation behavior
- stale-job reconciliation

### 2. Visual Preset Sanity Verification

The color-by-number preset library is intentionally hand-tuned and failure-prone. It must have regression guards that catch obvious bitmap drift for the most fragile presets.

At minimum the regression suite must cover:

- smile
- flower
- apple
- tree
- evergreen
- clown fish
- blue tang
- cat
- butterfly
- rocket

These checks do not need to be pixel-perfect full-image goldens, but they must be stable enough to detect broken silhouettes, missing semantic colors, and major shape regressions.

### 3. Browser Workflow Verification

The customer workflow must be validated in a real browser against the live compose-managed app.

This browser validation must cover:

- form submission
- reward-content review flow
- background worksheet generation handoff
- plain worksheet review
- styling confirm/cancel flow
- run-detail modal restore behavior
- gallery interaction
- debug-mode visibility differences when enabled

### 4. Live Gemini Validation Boundary

Normal automated tests may mock Gemini. That is acceptable and expected.

However, the team should treat these items as still requiring occasional live/manual validation against the real Gemini API:

- reward-content quality
- themed image-styling quality
- prompt robustness against real model drift

## Run Lifecycle And Recovery

The current product is explicitly run-centric.

Operational expectations:

- a completed or failed run may be regenerated from the run-detail modal into a fresh worksheet run
- developer/debug-only maintenance controls may prune orphan artifact directories and run SQLite `VACUUM` / `ANALYZE`
- those maintenance controls must not be visible in normal customer mode

One persisted worksheet run owns:

- the approved prompt and solution
- background worksheet generation
- plain-artifact readiness
- styling confirmation
- optional styling execution
- retry and recovery behavior

Expected lifecycle phases:

- `worksheet_generation_queued`
- `worksheet_generation_running`
- `plain_worksheet_ready`
- `awaiting_styling_confirmation`
- `styling_queued`
- `styling_running`
- `styled_verified`
- `styled_failed_plain_retained`
- `styling_cancelled_plain_retained`
- `run_failed`

Expected recovery rules:

- base worksheet generation must happen in a background job
- the run-detail modal is the canonical inspection surface after artifacts exist
- styling may start only after plain render plus explicit user confirmation
- retryable styling failures are `styled_failed_verification` and `styled_failed_error`
- a retry must create a new persisted `worksheet_style` job without regenerating the worksheet itself
- retry artifacts must be written into distinct retry output paths so the prior failure remains inspectable
- stale in-progress jobs discovered on startup must be reconciled into explicit failed states
- stale `awaiting_styling_confirmation` runs may be auto-cancelled after a configurable timeout
- timed-out jobs must not be allowed to overwrite their own failed state if a late worker result arrives afterward

## User Experience

### Main Page Layout

The main page is a split layout:

- left control pane
- right gallery pane

### Left Control Pane

The left pane is expected to contain the following sections:

- content setup
- worksheet parameters
- workflow submission
- runtime metadata

### Right Gallery Pane

The right pane is expected to contain:

- persistent gallery header
- counts for runs, artifacts, jobs, and drafts
- search box
- learner-band filter
- reveal-mode filter
- tile grid of prior worksheet runs

### Worksheet Run Modal

Clicking a gallery tile opens a modal that shows:

- prompt
- solution
- stored parameters
- download links for all artifacts
- PNG preview cards

Important modal behavior:

- Clicking PNG preview cards should open an in-app full-resolution preview.
- Clicking preview cards should not trigger downloads.
- Only dedicated download links/buttons should trigger downloads.
- When styling is still awaiting confirmation, the run modal may also present confirm/cancel controls.
- When styling has succeeded, failed, or been cancelled, the run modal should make that state explicit and show the available original/styled/debug image variants without forcing download.

### Full-Resolution Image Preview

The image-preview modal should:

- open from a PNG preview card
- display the PNG at full resolution within the modal
- allow close without changing worksheet state

### Workflow Popup

The workflow popup is the primary control surface for in-progress work.

It must:

- show per-job elapsed time in `mm:ss`
- log stage-by-stage progress messages
- own reward-content generation, review, editing, approval, and worksheet generation
- remain open for plain-worksheet review when styling was requested
- allow the user to confirm styling or keep the plain worksheet
- continue tracking the styling job after confirmation without forcing the user into a separate modal

## Reward Content

Reward content is the prompt and solution pair used to drive a worksheet.

It must include:

- prompt text
- solution phrase
- optional theme metadata
- source metadata
- language metadata
- review notes
- approval state

Reward content can come from:

- Gemini-assisted generation
- manual editing during the popup review flow
- direct/manual input through the popup review flow when Gemini is unavailable

### Reward-Content States

The state model is explicit:

- `PENDING`
- `EDITED`
- `REJECTED`
- `APPROVED`

Only `APPROVED` content can generate a worksheet.

### Gemini Behavior

Gemini-assisted generation is optional and environment-driven.

Rules:

- Gemini features are enabled only if `GEMINI_API_KEY` is present in the environment.
- The API key must not be baked into the image.
- If the API key is missing, Gemini UI controls remain visible but disabled.
- If the API key is missing, the UI must show a note explaining that Gemini was not detected.
- Gemini-only endpoints should return a clean failure response, not a generic server error.

The intended Gemini SDK is the official `google-genai` Python library.

The intended default model is:

- `gemini-2.5-flash-lite`

The model may be overridden by environment variable, but the default expectation for review is `gemini-2.5-flash-lite`, not a larger or more expensive model.

### Gemini Prompt Expectations

The Gemini request should be structured and constrained. It should include:

- theme/topic
- target learner band
- preferred style
- language
- tone constraints
- classroom-appropriateness constraints
- output-structure requirements

The response is expected to be structured JSON, not freeform text.

The generated content should be suitable for school use:

- short
- clear
- classroom-appropriate
- easy to review/edit

### Multi-Word Solution Guidance

Difficulty affects prompt guidance.

Expected behavior:

- lower-difficulty configurations may prefer a single-word or very short answer
- higher-difficulty configurations should prefer multi-word solutions

The purpose of multi-word solutions is not stylistic only. It is functional:

- more non-space letters create more worksheet problems
- more advanced students should receive longer reveal phrases and therefore longer problem sets

## Worksheet Configuration

### Core Principle

The worksheet configuration must expose the parameters the user can meaningfully control while avoiding invalid or misleading controls.

The current intended interaction model is:

- the left pane is one configuration form
- submitting that form opens a dedicated workflow popup
- the popup owns review, approval, cancellation, elapsed timing, and worksheet generation status
- the main form remains disabled while a workflow is active

### Learner Band As Preset

Learner band is not intended to be a hard-locked hidden engine mode that the user cannot reason about. It is intended to function as a preset layer.

Expected behavior:

- The learner-band selector should behave as a preset selector.
- Each learner band should provide default values for other parameters.
- The user should still be able to override the resulting parameters after the preset is applied.

Expected preset defaults:

- `early_arithmetic`: arithmetic-oriented defaults
- `upper_elementary`: balanced arithmetic defaults
- `pre_algebra`: defaults to the shared algebra profile with a low difficulty ceiling
- `algebra`: defaults to the same algebra profile with a higher difficulty range
- `geometry`: defaults to a geometry-and-trigonometry profile

### Skill Profiles

The implementation is expected to expose explicit skill profiles rather than one opaque math mode.

Expected profiles include:

- `mixed_operations`
- `subtraction_and_addition`
- `multiplication_focus`
- `division_focus`
- `algebra`
- `geometry`

Support expectations:

- the shared `algebra` profile must be available for `pre_algebra`, `algebra`, and `geometry`
- the `geometry` profile must be available for `geometry`
- arithmetic-only learner bands must not expose unsupported profiles

### Difficulty Strategy

Difficulty must be meaningful and visibly change the generated work.

For arithmetic profiles:

- difficulty `1` should use small integers less than `10`
- difficulty `2` should move into larger but still elementary integer ranges
- difficulty `3` should produce clearly larger multi-digit work
- difficulty `4` should use four-digit scale arithmetic
- difficulty `5` should use large four- and five-digit arithmetic inputs

For equation profiles:

- difficulty `1` should support one-step solve-for-`x` problems
- difficulty `2` should support simple two-step linear equations such as `2x + 3 = 5`
- difficulty `3` should support more involved linear equations such as variable-on-both-sides forms
- difficulty `4` should support simple systems while still yielding a single numeric worksheet answer
- difficulty `5` should support constrained polynomial prompts with integer roots and explicit answer-selection wording

For geometry profiles:

- difficulty `1` should support perimeter-based side solving
- difficulty `2` should support area-based side solving
- difficulty `3` should support Pythagorean right-triangle side solving
- difficulty `4` should support tangent-based right-triangle side solving
- difficulty `5` should support sine or cosine-based right-triangle side solving

Important invariant:

- every generated worksheet problem must still map to one single numeric answer value, because the reveal engine depends on numeric solution values

### Problem Generation Strategy

Geometry-specific requirements:

- Geometry worksheets should not reuse generic algebra text as a placeholder for a geometry mode.
- Geometry problems should carry structured render metadata so the worksheet can draw a matching rectangle or right-triangle sketch in the question card.
- The validator must verify those geometry problems against their stored dimensions or trig-ratio metadata before the worksheet is accepted.

The generation strategy is expected to use family-specific generators with explicit verification rules.

Arithmetic families:

- addition
- subtraction
- multiplication
- division

Equation families:

- a basic equation family for pre-algebra style one-step and gentle solve-for-`x` prompts
- a more advanced algebraic-equation family for two-step equations, variable-on-both-sides equations, simple systems, and constrained polynomial prompts

Validation expectations:

- arithmetic problems must validate by recomputing the operation
- equation problems must validate against the generated template metadata, not by trusting the printed prompt string alone
- systems and polynomial prompts must still resolve to a single explicit numeric worksheet answer

### Workflow Popup

The workflow popup is now a required part of the design.

It should contain:

- a task-status area
- a per-job elapsed timer that counts upward from `00:00`
- a configuration snapshot
- editable review fields for prompt and solution
- cancel
- regenerate when Gemini is available
- proceed-with-generation
- final completion state with access to the generated worksheet

Status/feedback expectations:

- job-specific feedback should live in the popup, not the main control pane
- the popup should show stage transitions such as draft generation, review, approval, and worksheet generation
- the user should be able to cancel the active workflow

### Container Logging

Console logging in the Docker container is an explicit product requirement.

Expected logging modes:

- `minimal`
- `normal`
- `verbose`

Expected behavior:

- `minimal` logs startup, completion, and failure boundaries
- `normal` logs prompts, responses, and task execution events
- `verbose` logs normal output plus lower-level generation/export details

Important requirement:

- each major task event should be visible in logs as it executes so the compose logs can be used as an operational trace

This means learner band remains a meaningful classification, but it should not imply that all other controls are frozen.

### Preset Metadata

Each learner band should provide at least:

- label
- description
- default reveal mode
- default skill profile
- default difficulty minimum
- default difficulty maximum

The UI should display a short note explaining the preset and explicitly indicate that the user can override the downstream fields.

### Configurable Worksheet Parameters

The worksheet parameter section should expose all meaningful editable parameters except problem count.

Current intended editable parameters:

- learner band preset
- reveal mode
- skill profile
- difficulty minimum
- difficulty maximum
- seed

Reward-content-related parameters in the reward-content section:

- theme/topic
- reward style
- language

### Non-Editable Derived Parameter

`problem_count` should not be a user-editable input.

This is a hard design rule.

Problem count is always derived from the approved solution phrase:

- count all non-space characters in the approved solution text
- that count determines the number of worksheet problems

The UI may display this count, but only as a read-only derived value.

This rule exists to prevent:

- mismatches between answer-slot count and problem count
- extra distractor problems in the web product
- confusing UI where the user can request an impossible or contradictory worksheet length

### Skill-Profile Availability

Skill-profile availability depends on learner band.

Expected support matrix:

- `early_arithmetic`
  - supports addition/subtraction-oriented profiles
  - does not support multiplication-only or division-only profiles
- `upper_elementary` and above
  - support broader arithmetic profiles including multiplication and division

The UI should filter available skill profiles when the learner band changes.

The backend must still validate requests and reject unsupported combinations with a clean `400`.

This is important because the UI is not trusted as the only enforcement layer.

## Problem Generation

### General Rules

The system generates one problem per non-space letter in the approved solution phrase.

Each generated problem must have:

- a problem ID
- a prompt
- a canonical answer
- a normalized answer
- a learner-band classification
- a difficulty value
- a problem family

### Current Problem Families

The current intended family set includes:

- addition
- subtraction
- multiplication
- division with exact integer answers

### Learner-Band Behavior

Different learner bands may limit which families are available.

Reviewers should verify:

- unsupported learner-band/profile combinations do not produce `500`s
- the API rejects invalid combinations cleanly
- the UI does not present obviously invalid combinations

### Difficulty Gradient

The worksheet should support a difficulty range rather than one fixed difficulty.

Expected behavior:

- problems can ramp from easier to harder across the worksheet
- the generated set should stay inside the configured minimum and maximum

### Determinism

If a seed is present:

- problem generation should be deterministic for the same inputs

If no seed is provided:

- the system may choose a default deterministic fallback strategy per run

### Problem IDs

Problem IDs are worksheet-local, not globally incremental across all worksheet runs.

This is a hard requirement.

Expected behavior:

- each new worksheet run starts numbering from the same local prefix
- current intended format is `R01`, `R02`, `R03`, and so on

Incorrect behavior would include:

- embedding the worksheet run ID in problem IDs
- continuing numbering across worksheet runs

## Mapping Rules

### Core Reveal Mechanic

In letter-bank mode:

1. Solve a problem.
2. Find the numeric answer in the lookup.
3. Read the corresponding letter.
4. Place the letter into the answer slot.

In color-by-number mode:

1. Solve a problem.
2. Find the numeric answer in the color key.
3. Use the corresponding color label/color.
4. Fill the reveal grid or regions.

### Distinct-Letter Constraint

Distinct letters must not map to the same answer value.

This is a first-class constraint because otherwise the lookup becomes ambiguous.

### Repeated Letters

Repeated letters are allowed to behave in either of two ways:

- shared-answer mapping, where repeated letters reuse the same answer value
- split-answer mapping, where repeated letters receive different answer values

Both behaviors are valid. The system may vary between them.

The key invariant is:

- repeated letters may vary
- different letters must remain unambiguous

### Web Product Rule About Extra Problems

The current web product should not generate extra distractor problems.

The historical domain model supports distractors, but the intended web behavior is:

- problem count equals non-space letter count
- therefore no extra distractor rows appear on generated web worksheets
- no placeholder token such as `DISTRACTOR` should appear in rendered student-facing output

If a code path still renders distractors into the main product workflow, that is a bug.

## Rendering And Visual Rules

### General Rendering Goals

The worksheet should be:

- printer-friendly
- high contrast
- readable
- visually simple
- laid out with enough spacing to avoid overlap

### Letter Lookup Rules

In the letter lookup:

- letters must be displayed in uppercase

This is required even if internal data or source text used lowercase or mixed case.

### Solution Sheet Case Rules

On the solution sheet:

- filled answer letters must always be rendered in uppercase

This is a display rule, not necessarily a storage rule.

### Letter-Bank Solution Sheet Rules

The solution sheet for letter-bank mode should:

- show solved answer values on the problem cards
- visually highlight the corresponding lookup entry
- fill the final answer slots
- use consistent color coding across the problem answer chip, lookup highlight, and answer slot for the same solution path

If the same reveal letter is used in multiple places, the visual mapping should remain coherent for the student/reviewer.

### Color-By-Number Rules

Color-by-number mode should support up to 32 colors.

This is a contract-level expectation, not only a UI hint.

Requirements:

- the UI advertises support for 32 colors
- the underlying color-label pool must actually contain 32 entries
- generation should fail cleanly if the configured worksheet would exceed the supported color pool

### Uppercase And Normalization Strategy

Rendering should normalize visual display where necessary without corrupting the underlying saved data.

This means:

- stored solution text may remain as entered
- rendered lookup and rendered solution letters should normalize to uppercase where required

### PNG Preview Behavior

The modal image tiles are preview affordances, not download links.

Requirements:

- clicking a PNG card opens a full-resolution viewer
- download actions belong only to download links/buttons

## Export Artifacts

Each worksheet run should produce a standard artifact set:

- worksheet preview `svg`
- worksheet preview `png`
- worksheet preview `pdf`
- worksheet solution `svg`
- worksheet solution `png`
- worksheet solution `pdf`
- worksheet manifest `json`
- worksheet run metadata `json`

These artifacts should be stored in a per-run directory such as:

- `run-00001/`

## Persistence Model

### Artifact Persistence

Artifacts must be stored in a persistent Docker volume.

Expected result:

- generated files remain available across `docker compose down` and later `docker compose up`

### Database Persistence

SQLite must be stored in a separate persistent volume.

The database should store enough metadata to support later search and inspection.

### Required Persistent Records

The database should persist at least:

- reward-content drafts
- worksheet runs
- worksheet artifacts
- generation jobs

### Searchability Requirements

Each generated worksheet should be searchable later by stored parameters and content metadata.

At minimum, searchable fields should include:

- title
- theme
- prompt text
- solution phrase
- learner band
- reveal mode

## API Design

### Core APIs

The system is expected to expose:

- app configuration API
- gallery API
- reward-content draft APIs
- worksheet-generation API
- worksheet-run detail API
- generation-job API
- artifact-serving endpoint

### App Configuration API

The app configuration response should include:

- Gemini enabled state
- Gemini model
- storage paths
- learner-band preset metadata
- reveal-mode options
- skill-profile options and support matrix
- reward-style options
- language options
- seed metadata
- max color options
- job-tracking capability note

This route is effectively the UI bootstrap contract.

### Worksheet Generation API

The worksheet-generation endpoint should:

- require an approved draft
- derive `problem_count` from the approved solution phrase
- persist a worksheet run row before generation
- persist a generation job row before generation
- generate artifacts
- persist artifact rows
- mark the job completed or failed
- return a clean `400` for expected business-rule failures

Expected business-rule failure examples:

- draft not approved
- unsupported learner-band/profile combination
- impossible mapping constraints
- color-pool overflow

These should not become generic `500`s.

### Job Tracking API

The current transport is persisted polling.

Expected behavior:

- jobs have stored status and progress messages
- the API may block briefly for polling-style wait behavior
- the design should remain compatible with future SSE or long-poll streaming
- startup reconciliation for stale persisted jobs is required
- timeout thresholds for worksheet generation and styling are required
- timeout or restart recovery must leave the run in a coherent visible state rather than a permanent `running` state

## Data Model Expectations

The core domain should include objects equivalent to:

- `WorksheetSpec`
- `RewardContent`
- `RewardContentCandidate`
- `Problem`
- `SolvedProblem`
- `LetterAssignment`
- `Worksheet`
- `RenderedWorksheet`

Important persisted or serialized concepts:

- learner band
- skill profile
- reveal mode
- difficulty range
- seed
- prompt text
- solution phrase
- approval state
- review notes
- problem definitions
- solved answers
- letter assignments
- rendered outputs

## Validation Rules

Validation should occur before rendering and export are considered successful.

### Worksheet-Level Validation

The system should confirm:

- one learner band per worksheet
- prompt exists
- solution exists
- reward content is approved
- each problem has one canonical answer
- each answer slot is fillable
- the final answer reconstructs correctly
- distinct letters do not share numeric answers
- layout fits page bounds
- the generated worksheet obeys the no-extra-problems rule in the web workflow

### Problem-Level Validation

The system should confirm:

- math is correct
- prompt is well formed
- answer format is valid
- difficulty is within configured bounds

### Review-Oriented Invariants

These are especially important during code review:

- `problem_count == count(non-space chars in approved solution phrase)` for web-generated worksheets
- no generated web worksheet renders a `DISTRACTOR` label
- problem IDs reset per worksheet run
- unsupported learner-band/profile combinations return `400`, not `500`
- lookup letters render uppercase
- solution-sheet letters render uppercase
- PNG preview clicks do not download
- only explicit download controls download files
- color support contract is truly 32, not just documented as 32

## Error Handling Expectations

The app should prefer clean, user-readable failures over raw exceptions.

Expected failure handling:

- Gemini unavailable: clean `503`
- draft missing: `404`
- draft not approved: `400`
- invalid worksheet-generation combination: `400`
- mapping/generation failure: `400`

The system should still log useful stack traces for unexpected bugs, but business-rule failures must not present as unhandled server errors.

## Testing Expectations

The project should remain Docker-testable.

Expected test coverage areas:

- reward-content generation and approval flow
- disabled-Gemini behavior when API key is missing
- worksheet-generation success path
- learner-band/profile validation failures
- deterministic problem generation
- mapping correctness
- rendering/export success
- uppercase rendering normalization
- full artifact generation
- snapshot or metric-oriented render checks

## Review Checklist

An implementation review should inspect at least the following:

### UI Review

- Does the control pane expose the intended configurable parameters?
- Is learner band treated as a preset with overridable downstream fields?
- Is derived problem count visible but read-only?
- Are invalid skill profiles filtered when learner band changes?
- Do PNG previews open in-app instead of downloading?

### API Review

- Does `/api/app-config` expose enough metadata for the UI to bootstrap correctly?
- Does worksheet generation derive `problem_count` server-side?
- Does worksheet generation reject unsupported combinations with `400`?
- Are generation jobs persisted and queryable?

### Persistence Review

- Are artifacts written to the persistent artifact root?
- Is SQLite stored in the persistent DB path?
- Are worksheet runs, jobs, artifacts, and drafts all persisted?
- Is gallery search backed by persisted metadata rather than transient state?

### Generation Review

- Does the solution phrase length drive the number of problems?
- Do problem IDs reset per worksheet?
- Are repeated letters handled without creating ambiguity for distinct letters?
- Does the web workflow avoid distractor rows?

### Rendering Review

- Are lookup letters uppercase?
- Are solution letters uppercase?
- Is the solution sheet color coded consistently in letter-bank mode?
- Does color-by-number truly support 32 colors?
- Is text spacing sufficient to avoid overlap?

### Gemini Review

- Is `google-genai` used?
- Is the default model `gemini-2.5-flash-lite`?
- Is the API key read only from environment?
- Are Gemini controls disabled when the key is absent?
- Do higher-difficulty requests bias toward multi-word solutions?

## Acceptance Criteria

The design should be considered implemented correctly only if all of the following are true:

- The app runs via Docker Compose on port `9595`.
- Generated worksheets and the SQLite DB persist across restarts.
- The UI supports reward-content generation, review, approval, worksheet generation, gallery browsing, and modal inspection.
- Problem count is derived from the approved solution phrase and is not user-editable.
- Learner band behaves as a preset with editable downstream fields.
- Invalid learner-band/profile combinations fail cleanly.
- Letter-bank and color-by-number modes both render correctly.
- Solution sheets are produced and usable.
- Letter lookup and filled solution letters render uppercase.
- PNG previews open in-app at full resolution.
- Download links remain explicit.
- Gallery search works from persisted metadata.
- The expected artifact set is produced for each worksheet run.

## Maintenance Rule

If the product behavior changes materially, this document should be updated before or alongside the code change. For this project, `design.md` is expected to describe the intended current product, not only the original concept.
