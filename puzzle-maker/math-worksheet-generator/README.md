# Math Worksheet Generator

This project is a Docker-first worksheet generation app. It provides a Flask web UI on port `9595`, a Gemini-assisted reward-content flow, worksheet generation APIs, persistent artifact storage, a searchable gallery backed by SQLite, and an optional post-render Gemini worksheet-image styling pipeline.

The current product is no longer just a static preview. The live app lets a user:

- configure worksheet parameters from a single left-side form
- submit once and complete reward review, approval, timing, and generation inside a workflow popup
- call Gemini through the official `google-genai` Python SDK when `GEMINI_API_KEY` is available
- generate worksheet artifacts and persist them in Docker volumes
- browse prior runs in a tile gallery, filter by metadata, and open a modal with downloadable outputs

## Core Capabilities

- Reward-content drafts with `pending`, `edited`, `rejected`, and `approved` review states
- Gemini-assisted clue generation using `gemini-2.5-flash-lite` by default
- Automatic disabling of Gemini UI features when `GEMINI_API_KEY` is not present
- Seeded math problem generation across multiple learner bands and skill profiles
- Letter-bank and color-by-number worksheet rendering
- Solution-sheet generation alongside the main worksheet
- Persistent artifact storage across `docker compose down` and `docker compose up`
- Searchable worksheet history in SQLite with stored parameters and artifact metadata
- Optional post-render worksheet-image styling with plain-render review, confirmation, verification, retry, and retained base artifacts on failure
- Structured container logging with `minimal`, `normal`, and `verbose` modes
- Docker-native test workflow

## Quick Start

Everything should be built and tested through Docker. Do not run the app directly on the host.

Start the app:

```bash
docker compose up -d --build
```

Stop the app:

```bash
docker compose down
```

Open the UI:

```text
http://localhost:9595
```

Run the full automated check suite:

```bash
docker compose run --rm worksheet-test
```

## Environment Variables

The app reads runtime configuration from container environment variables.

- `GEMINI_API_KEY`
Enables Gemini-assisted reward-content generation. If unset, Gemini controls stay disabled in the UI and the backend returns `503` for Gemini-only endpoints.

- `GEMINI_MODEL`
Optional override for the Gemini model. The default is `gemini-2.5-flash-lite`, which is the low-cost Gemini 2.5 choice currently used by the app.

- `GEMINI_IMAGE_MODEL`
Optional override for the Gemini image-styling model. Default: `gemini-3.1-flash-image-preview`

- `APP_DB_PATH`
SQLite database location inside the container. Default: `/var/lib/math-worksheet-generator/db/app.sqlite3`

- `APP_ARTIFACT_ROOT`
Artifact storage root inside the container. Default: `/var/lib/math-worksheet-generator/artifacts`

- `APP_LOG_VERBOSITY`
Logging mode for the container console. Supported values:
  - `minimal`: startup, completion, and failure events
  - `normal`: task events plus prompts, responses, and major workflow payloads
  - `verbose`: normal logging plus lower-level generation/export details
Default: `normal`

- `APP_WORKSHEET_GENERATION_TIMEOUT_SECONDS`
Background worksheet-generation timeout in seconds. Default: `180`

- `APP_WORKSHEET_STYLING_TIMEOUT_SECONDS`
Background worksheet-styling timeout in seconds. Default: `180`

- `APP_STYLING_CONFIRMATION_TIMEOUT_SECONDS`
How long a run may remain in plain-review confirmation before startup reconciliation auto-cancels styling and keeps the plain worksheet. Default: `86400`

- `APP_JOB_WORKER_ENABLED`
 Enables the startup SQLite-backed worksheet job worker and timeout watchdog. Default: `true`

Compose already passes these through where needed:
[compose.yaml](/home/ben/playground/math-worksheet-generator/compose.yaml)

## Persistent Storage

Docker Compose creates two named volumes:

- `worksheet_app_db`
Stores SQLite state.

- `worksheet_app_artifacts`
Stores generated worksheets, manifests, preview images, and solution exports.

Those volumes are mounted here inside the container:

- database: `/var/lib/math-worksheet-generator/db`
- artifacts: `/var/lib/math-worksheet-generator/artifacts`

Each worksheet run gets its own artifact directory like `run-00001/`.

## Product Workflow

The end-to-end flow is:

1. Open the control pane and choose theme, learner band preset, reveal mode, skill profile, difficulty range, language, style, and optional seed.
2. Submit the form once to open the workflow popup.
3. If Gemini is enabled, the popup generates a draft and starts a per-job elapsed timer. If Gemini is disabled, the popup opens directly in manual review mode.
4. Review the prompt and solution inside the popup, regenerate if needed, or edit the fields directly.
5. Proceed with generation from the popup. The popup saves edits, approves the draft, and queues a background worksheet-generation job while keeping the form disabled.
6. The popup tracks the job with structured phase labels and elapsed time in `mm:ss`.
7. If image styling was not requested, the workflow auto-hands off into the run-detail modal when the plain worksheet is ready.
8. If image styling was requested, the popup shows the plain worksheet first and requires an explicit styling confirmation.
9. If styling is confirmed, the popup starts a background styling job and tracks prompt refinement, Gemini styling, semantic foreground compositing, verification, and retry.
10. If styling is cancelled, times out, or fails, the plain worksheet remains available and the run-detail modal remains the canonical inspection surface.
11. On completion, the generated run appears in the gallery and can be reopened from the tile view, by direct URL state, or from the workflow handoff.
12. Existing runs can be regenerated from the run-detail modal without rebuilding the clue by hand.

For more advanced worksheets, the Gemini prompt now asks for multi-word solutions so the generator can create longer reveals and more math problems.

## Run Lifecycle And Recovery

One persisted worksheet run owns the full lifecycle from background generation through optional styling and recovery.

Run lifecycle phases exposed by the API:

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

Operational rules:

- base worksheet generation is non-blocking and happens in a background job
- the run-detail modal is the canonical viewer once a run has artifacts
- styling can only begin after the plain worksheet is rendered and the user confirms it
- styling failures never discard the base worksheet
- retryable styling failures are:
  - `styled_failed_verification`
  - `styled_failed_error`
- a styling retry creates a new `worksheet_style` job without regenerating the worksheet itself
- retry artifacts are written into distinct retry directories so prior failure evidence remains inspectable
- startup reconciliation converts stale in-progress jobs into explicit failed states instead of leaving them pending forever
- timeout watchdogs prevent generation or styling jobs from remaining stuck in `running` forever
- worksheet generation and styling are claimed from a durable SQLite-backed queue by startup background workers rather than per-request threads

## Gemini Behavior

Gemini integration lives in:
[reward_content_generation.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/reward_content_generation.py)

Important behavior:

- Uses the official `google-genai` SDK
- Defaults to `gemini-2.5-flash-lite`
- Requests structured JSON output
- Parses that output into a validated payload
- Applies solution-length guidance based on learner band and difficulty

Solution guidance is intentionally different by difficulty:

- lower difficulty prefers a short single-word answer or very short phrase
- higher difficulty prefers a multi-word answer phrase with 2 to 4 words

If `GEMINI_API_KEY` is missing, the left panel still renders but Gemini features are visibly disabled with a note.

## Web UI

The Flask app lives under:
[webapp](/home/ben/playground/math-worksheet-generator/worksheet_generator/webapp)

Main UI pieces:

- [index.html](/home/ben/playground/math-worksheet-generator/worksheet_generator/webapp/templates/index.html)
Defines the split layout, controls, gallery, and modal shell.

- [app.css](/home/ben/playground/math-worksheet-generator/worksheet_generator/webapp/static/app.css)
Styles the application shell, cards, tiles, modal, and form states.

- [app.js](/home/ben/playground/math-worksheet-generator/worksheet_generator/webapp/static/app.js)
Drives the single-form workflow, popup review flow, elapsed timer, cancellation, worksheet generation, gallery refresh, search, and modal behavior.

The current backend transport is synchronous request-response plus persisted job rows. The popup tracks each in-flight job locally with a live timer, and the persistence model is still compatible with future long-poll or SSE.

## Debug And Maintenance Mode

The customer UI stays focused by default. Developer diagnostics and maintenance controls are only exposed when:

- `APP_DEBUG_UI=true`

In debug mode the UI additionally shows:

- runtime metadata
- maintenance summary
- orphan-artifact pruning controls
- SQLite vacuum/analyze control
- raw run/debug metadata

The maintenance controls are intended for local operations, not customer use.

## Worksheet Image Styling

The worksheet image-styling feature is now part of the implemented workflow.

The styling pipeline is:

1. The user enables `Apply Gemini Image Styling` in the left-side form.
2. The app generates the plain worksheet first.
3. The workflow popup shows the plain worksheet preview and requires an explicit confirm-or-cancel decision.
4. If the user cancels, styling is marked `cancelled_after_plain_review` and the base worksheet remains the final artifact.
5. If the user confirms, the backend:
   - refines the worksheet styling prompt with `gemini-2.5-flash-lite`
   - sends the base preview PNG plus the prompt to the image model, default `gemini-3.1-flash-image-preview`
   - exports a semantic-foreground PNG from the renderer
   - composites the semantic foreground back over the styled background
   - verifies the composited result against the semantic foreground
   - retries once with a stricter prompt if verification fails
6. The final run stores both original and styled artifacts side by side when styling succeeds.

Hard guarantees:

- the base worksheet PNG remains the source of truth
- the semantic foreground is deterministic and renderer-driven
- the system does not trust prompting alone to preserve content

## Verification Strategy

The project uses three verification layers:

1. Unit and service tests
   These cover reward-content logic, worksheet assembly, rendering/export behavior, styling prompt construction, styling verification, timeout handling, cancellation, retry, and stale-job reconciliation.

2. Visual sanity regression tests
   The preset color-by-number pictures have snapshot-backed metric checks so obvious bitmap regressions are caught without relying on brittle full-image goldens. The current snapshot coverage includes:
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

3. Browser-level integration validation
   Before closing major rewrite phases, the live compose-managed app is exercised in a real browser session through the MCP browser tooling. That validates the customer workflow at the UI level even when the automated Python suite is mocking Gemini calls.

What still requires live/manual validation:

- outbound Gemini behavior against the real API
- subjective themed-image quality for worksheet styling
- any customer-facing visual polish review that depends on human judgment rather than structural correctness
- a styling failure does not discard the base worksheet

Styled artifact/debug outputs may include:

- styled preview PNG
- styled preview PDF
- raw styled background PNG
- semantic foreground SVG/PNG
- styling debug JSON
- verification report JSON
- verification overlay PNG

Current styling status values used in persisted runs include:

- `awaiting_confirmation`
- `confirmed_pending_styling`
- `styling_in_progress`
- `retry_pending_styling`
- `retry_in_progress`
- `styled_verified`
- `styled_failed_verification`
- `styled_failed_error`
- `cancelled_after_plain_review`

Verification behavior:

- strategy: `semantic_foreground_pixel_preservation`
- max attempts: `2`
- retry: one stricter prompt retry on verification failure
- explicit retry from a failed run: supported without regenerating the worksheet
- timeout policy:
  - worksheet generation: `180s` default
  - worksheet styling: `180s` default
  - styling confirmation idle window: `86400s` default

How to disable styling cleanly:

- unset `GEMINI_API_KEY`
- the UI disables Gemini styling controls
- the backend will not allow Gemini-only styling actions
- the rest of the worksheet product continues to work in manual/plain mode

The reviewer-facing task sequence for this feature is:

- [07-styled-image-feature-overview.md](/home/ben/playground/math-worksheet-generator/tasks/07-styled-image-feature-overview.md)
- [08-styling-settings-and-persistence.md](/home/ben/playground/math-worksheet-generator/tasks/08-styling-settings-and-persistence.md)
- [09-styling-prompt-and-theme-catalog.md](/home/ben/playground/math-worksheet-generator/tasks/09-styling-prompt-and-theme-catalog.md)
- [10-gemini-image-styling-service.md](/home/ben/playground/math-worksheet-generator/tasks/10-gemini-image-styling-service.md)
- [11-foreground-compositing.md](/home/ben/playground/math-worksheet-generator/tasks/11-foreground-compositing.md)
- [12-style-verification-and-retry.md](/home/ben/playground/math-worksheet-generator/tasks/12-style-verification-and-retry.md)
- [13-job-flow-and-gallery-integration.md](/home/ben/playground/math-worksheet-generator/tasks/13-job-flow-and-gallery-integration.md)
- [14-styling-tests-and-documentation.md](/home/ben/playground/math-worksheet-generator/tasks/14-styling-tests-and-documentation.md)
- [15-background-worksheet-generation.md](/home/ben/playground/math-worksheet-generator/tasks/15-background-worksheet-generation.md)
- [16-run-state-machine-and-phase-model.md](/home/ben/playground/math-worksheet-generator/tasks/16-run-state-machine-and-phase-model.md)
- [17-detail-view-and-auto-open-flow.md](/home/ben/playground/math-worksheet-generator/tasks/17-detail-view-and-auto-open-flow.md)
- [18-styling-retry-and-recovery.md](/home/ben/playground/math-worksheet-generator/tasks/18-styling-retry-and-recovery.md)
- [19-stale-job-cleanup-and-timeouts.md](/home/ben/playground/math-worksheet-generator/tasks/19-stale-job-cleanup-and-timeouts.md)
- [20-workflow-alignment-tests-and-docs.md](/home/ben/playground/math-worksheet-generator/tasks/20-workflow-alignment-tests-and-docs.md)

Implementation entry points:

- [image_styling.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/image_styling.py)
- [image_styling_service.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/image_styling_service.py)
- [image_styling_verification.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/image_styling_verification.py)
- [image_compositing.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/image_compositing.py)
- [generation_service.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/webapp/generation_service.py)
- [app.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/webapp/app.py)

## API Surface

Implemented HTTP routes in:
[app.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/webapp/app.py)

Core pages and status:

- `GET /`
- `GET /api/app-config`
- `GET /api/gallery`
- `GET /api/health`
- `GET /artifacts/<path>`

Reward-content draft flow:

- `POST /api/reward-content/generate`
- `POST /api/reward-content/direct`
- `POST /api/reward-content/<id>/regenerate`
- `PATCH /api/reward-content/<id>`
- `POST /api/reward-content/<id>/reject`
- `POST /api/reward-content/<id>/approve`
- `GET /api/reward-content/<id>`

Worksheet generation flow:

- `POST /api/worksheets/generate`
- `GET /api/worksheet-runs/<id>`
- `POST /api/worksheet-runs/<id>/styling-decision`
- `POST /api/worksheet-runs/<id>/styling-retry`
- `GET /api/jobs/<id>?wait_seconds=<n>`

`/api/gallery` supports metadata search and filtering:

- `search`
- `learner_band`
- `reveal_mode`

## Database Model

SQLite schema and initialization live in:
[db.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/webapp/db.py)

Current tables:

- `worksheet_runs`
Stores run metadata, parameters, prompt, solution, base thumbnail location, and styling metadata including status, verification status, prompt text, styled artifact group, and styled thumbnail/debug paths.

- `worksheet_artifacts`
Stores downloadable artifact metadata for each run.

- `generation_jobs`
Stores job status and progress messages so the frontend can poll for completion.

- `reward_content_drafts`
Stores Gemini-generated or user-edited clue drafts plus review metadata.

Query and persistence logic lives in:
[repository.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/webapp/repository.py)

## Worksheet Generation Pipeline

The generation pipeline spans the existing worksheet engine plus the new app service layer.

- [generation_service.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/webapp/generation_service.py)
Builds a worksheet run from an approved draft, writes persistent artifacts, and applies optional styled-image processing after user confirmation.
The generated worksheet always uses one problem per letter in the approved solution phrase for `letter_bank` mode.

- [worksheet_assembly.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/worksheet_assembly.py)
Combines problem generation, mapping, validation, and retry logic.

- [problem_generators](/home/ben/playground/math-worksheet-generator/worksheet_generator/problem_generators)
Contains family-specific math generators and the seeded assembly service.
Families now include arithmetic plus equation-oriented generators:
  - `addition`
  - `subtraction`
  - `multiplication`
  - `division`
  - `algebraic_equation`
  - `geometry_problem`

- [mapping_engine.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/mapping_engine.py)
Maps solved answers onto reveal tokens and supports repeated-letter behavior.

- [validator.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/validator.py)
Rejects invalid worksheets before rendering.

- [rendering.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/rendering.py)
Produces SVG layout for worksheets and answer sheets.

- [exporter.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/exporter.py)
Exports SVG, PNG, and PDF variants.

Current output set per run:

- worksheet preview: `svg`, `png`, `pdf`
- worksheet solution: `svg`, `png`, `pdf`
- worksheet manifest: `json`
- worksheet run metadata: `json`

When styling succeeds, additional outputs may include:

- styled preview: `png`, `pdf`
- styled raw background: `png`
- semantic foreground: `svg`, `png`
- styling debug: `json`
- verification report: `json`
- verification overlay: `png`

The color-by-number flow now supports a full 32-color label pool, matching the UI contract.

## Skill Profiles And Difficulty Strategy

The current skill profiles are:

- `mixed_operations`
- `subtraction_and_addition`
- `multiplication_focus`
- `division_focus`
- `algebra`
- `geometry`

Support by learner band is intentional:

- `early_arithmetic` is limited to gentler arithmetic profiles
- `upper_elementary` supports arithmetic profiles
- `pre_algebra` and `algebra` both default to the shared `algebra` profile
- `geometry` defaults to the `geometry` profile but can also use `algebra`

Difficulty is now meaningful rather than cosmetic.

Arithmetic expectations:

- difficulty `1`: single-digit operands under `10`
- difficulty `2`: two-digit arithmetic
- difficulty `3`: three-digit arithmetic
- difficulty `4`: four-digit arithmetic
- difficulty `5`: large four- and five-digit arithmetic

Equation expectations:

- difficulty `1`: one-step solve-for-`x` equations such as `x + 3 = 9`
- difficulty `2`: two-step equations such as `2x + 3 = 11`
- difficulty `3`: variable-on-both-sides equations
- difficulty `4`: simple systems where the prompt still asks for numeric `x`
- difficulty `5`: constrained polynomial prompts with integer roots and an explicit root-selection instruction

Geometry expectations:

- difficulty `1`: rectangle perimeter with a missing side
- difficulty `2`: rectangle area with a missing side
- difficulty `3`: right-triangle side solving with the Pythagorean theorem
- difficulty `4`: tangent-based right-triangle side solving
- difficulty `5`: sine-based right-triangle side solving

All generated equation prompts still resolve to a single numeric answer so they remain compatible with the worksheet reveal/mapping system.
All generated geometry prompts also resolve to a single numeric answer, and geometry cards can render a matching rectangle or right-triangle diagram.

## Logging

Container logging is structured and environment-driven.

Implementation:

- startup logging is configured in [serve_site.py](/home/ben/playground/math-worksheet-generator/scripts/serve_site.py)
- shared logging helpers live in [logging_utils.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/logging_utils.py)
- task-level app events are emitted from [app.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/webapp/app.py)
- Gemini prompt/response logging is emitted from [reward_content_generation.py](/home/ben/playground/math-worksheet-generator/worksheet_generator/reward_content_generation.py)

Default behavior with `APP_LOG_VERBOSITY=normal`:

- logs reward-content requests
- logs draft completion and approval events
- logs worksheet-generation request parameters
- logs worksheet assembly/export completion
- logs Gemini prompts and Gemini responses when Gemini is used
- logs styling confirmation, styling stage progress, verification outcome, and retry scheduling

Example:

```bash
docker logs -f math-worksheet-generator-worksheet-poc-1
```

## Repeated-Letter Mapping

Repeated letters are allowed to behave in either of two ways:

- shared mapping, where repeated letters reuse the same solved answer
- split mapping, where repeated letters receive different solved answers

That behavior can vary per worksheet. Distinct letters are still prevented from colliding onto the same answer mapping.

## Codebase Layout

Top-level structure:

- [tasks](/home/ben/playground/math-worksheet-generator/tasks)
Design breakdown and implementation order.

- [scripts](/home/ben/playground/math-worksheet-generator/scripts)
Operational scripts for serving, checks, and site generation.

- [worksheet_generator](/home/ben/playground/math-worksheet-generator/worksheet_generator)
Core worksheet domain model, generation logic, rendering, and Flask app.

- [tests](/home/ben/playground/math-worksheet-generator/tests)
Pytest coverage for the service layer, rendering, generation, mapping, and web app.

- [fixtures](/home/ben/playground/math-worksheet-generator/fixtures)
Manifest-backed sample fixture outputs.

Operational entrypoints:

- [serve_site.py](/home/ben/playground/math-worksheet-generator/scripts/serve_site.py)
Starts the Flask application on the requested port.

- [run_full_checks.py](/home/ben/playground/math-worksheet-generator/scripts/run_full_checks.py)
Runs compile checks and pytest.

## Testing and Verification

Primary automated workflow:

```bash
docker compose run --rm worksheet-test
```

That runs:

1. `python -m compileall worksheet_generator scripts tests`
2. `python -m pytest -q`

Useful Docker-only manual checks:

```bash
docker compose exec worksheet-poc python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9595/api/health').status)"
docker compose exec worksheet-poc python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9595/api/gallery').read().decode())"
docker compose exec worksheet-poc ls /var/lib/math-worksheet-generator/artifacts
```

Styling coverage is designed to run without live Gemini access. The test suite uses mocked Gemini responses for:

- styling prompt refinement
- image styling response parsing
- verification pass/fail behavior
- single-retry behavior
- styling job progression and artifact persistence
- gallery visibility for original and styled artifacts

## Notes for Further Development

- The backend already persists generation jobs, so blocking poll is supported and SSE can be added later without reworking storage.
- The Gemini flow is isolated behind a service boundary, which keeps mocking straightforward for tests.
- color-by-number preview pages
- color-by-number solution pages

It also returns metrics such as content bottom and fit-to-page status.

### 7. Export Produces Final Artifacts

Each rendered page is exported to:

- `SVG`
- `PNG`
- `PDF`

## Testing

Run all checks through Docker:

```bash
docker compose run --rm worksheet-poc python -m compileall worksheet_generator scripts
docker compose run --rm worksheet-poc python scripts/check_reward_content_flow.py
docker compose run --rm worksheet-poc python scripts/check_problem_generation.py
docker compose run --rm worksheet-poc python scripts/check_mapping_validation.py
docker compose run --rm worksheet-poc python scripts/check_rendering_export.py
docker compose up -d --build
```

Useful verification examples:

```bash
docker compose ps
docker compose run --rm worksheet-test
docker compose run --rm worksheet-poc python -m pytest -q
```
