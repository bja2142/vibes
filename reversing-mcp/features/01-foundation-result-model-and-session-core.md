# Feature 01: Foundation, Result Model, and Session Core

## Goal
Establish the MCP skeleton, shared schemas, session lifecycle, and state model that every later feature depends on.

## Execute After
None. This is the first feature.

## Enables
- `02-security-and-workspace-hardening.md`
- `03-file-intake-and-metadata-triage.md`
- All later feature documents

## Implementation Tasks
1. Define the canonical JSON result envelope used by every tool response.
2. Implement the shared confidence model with `exact`, `high`, `medium`, `low`, and `speculative`, plus optional `method`.
3. Implement the shared provenance model, including backend name, parameters, exact-vs-inferred markers, and extracted-artifact provenance fields.
4. Define the structured error taxonomy with explicit categories for unsupported format or architecture, invalid or expired object ID, timeout or resource limit exceeded, backend failure, missing prerequisite, and partial result.
5. Create stable session, artifact, function, and string ID formats and ID invalidation rules tied to re-analysis, artifact removal, and session destruction.
6. Build session lifecycle tools for create, load, list, destroy, and persist sessions on the shared volume.
7. Persist mutable analysis context for annotations and settings so later tool calls can reuse state without resending large payloads.
8. Implement token-efficient reference handling so tools can accept session IDs, artifact IDs, addresses, and names instead of large context blobs.
9. Add reproducible machine-readable export primitives for internal state snapshots and tool outputs.
10. Implement per-annotation revision history with revert support for individual annotation edits.
11. Implement long-running job handles with progress, partial-result support, and cancellation.
12. Add architecture and format capability reporting for the current container.
13. Add named whole-session snapshots with revert support.
14. Publish concise MCP tool descriptions, self-documenting parameters, and explicit prerequisites for the tools introduced in this feature.

## Deliverables
- Shared response schema module used across the MCP.
- Session persistence layer rooted in the workspace volume.
- Job manager with progress and cancellation.
- Capability-reporting surface.
- Session and annotation history model.

## Acceptance Criteria
- A new session survives container restart when the same volume is mounted.
- IDs remain stable across repeated calls and fail with `invalid_id` after re-analysis or deletion.
- Every tool created so far returns structured JSON with confidence, provenance, and typed error objects where applicable.
- Long-running operations expose a job handle, progress state, cancellation, and partial results.
- Annotation edits can be listed and reverted at the annotation level.
- Session snapshots can be created and reverted without destroying the session.

## Requirements Covered
- §0 Confidence and Evidence
- §0 Provenance
- §0 Error Taxonomy
- §0 Stable Object Identity
- §0 Structured Result Schema
- §0 Tool Descriptions and Naming
- §0 Asynchronous Job Model
- §1 Stateful Sessions
- §1 Persistent Analysis Context
- §1 Token-Efficient References
- §1 Reproducible Outputs
- §1 Provenance and Auditability
- §1 Error and Confidence Reporting
- §1 Annotation History
- §1 Progress Reporting and Cancellation
- §1 Architecture and Format Capability Reporting
- §1 Session Snapshots

## Notes for the Implementing Agent
- Do not add backend-specific analysis logic yet beyond what is required to persist IDs and sessions.
- Keep the schema centralized so later tools cannot drift.
- Treat this feature as the contract layer for the rest of the system.
