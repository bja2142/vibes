# Task 10: Gemini Image Styling Service

## Goal

Implement the Gemini image-styling call that takes the rendered worksheet PNG plus a styling prompt and returns a styled image candidate.

## Scope

- Add an image-styling service behind a narrow interface
- Match the reference API pattern where practical
- Support environment-driven model configuration
- Persist enough debugging information to diagnose failures

## Requirements

- Use the official `google-genai` Python SDK
- Follow the general reference calling pattern:
  - create a client with the environment API key
  - call `client.models.generate_content(...)`
  - send the source image and the prompt together
  - request image output through response modalities
- Default the image-styling model to the closest appropriate image-editing model available to the project, with a configuration point for future upgrades
- Keep the model name stored in the run metadata
- Convert the rendered PNG into the format expected by the SDK without lossy preprocessing that could degrade text fidelity
- Handle timeouts, malformed responses, empty image responses, and API errors explicitly
- Save raw Gemini output artifacts when useful for debugging, as long as they remain inside the project’s persistent artifact volume
- The service must be mockable in automated tests
- The service must not directly overwrite the base worksheet artifact

## Deliverables

- Gemini image styling service module
- Styled-image artifact writer
- Error model for styling failures
- Config support for styling-model overrides

## Dependencies

- Task 09 styling prompt builder
- Existing Gemini SDK dependency and environment handling
- Existing artifact storage model

## Acceptance Criteria

- The service can accept a base worksheet PNG and a prompt and produce a styled image artifact
- Failures are reported as styling-stage failures, not as generic worksheet-generation crashes
- Base worksheet generation still succeeds even if the styling call fails
- The image-styling service can be fully mocked in tests
