# Feature 02: Security and Workspace Hardening

## Goal
Harden file handling and container behavior before deeper binary analysis is exposed to untrusted samples.

## Execute After
- `01-foundation-result-model-and-session-core.md`

## Enables
- `03-file-intake-and-metadata-triage.md`
- `06-signatures-extraction-and-obfuscation.md`
- `07-patching-multi-artifact-and-interop.md`

## Implementation Tasks
1. Implement canonical workspace-root resolution for all input and output paths.
2. Enforce shared-volume-only file access for binaries, carved artifacts, reports, scripts, and patched outputs.
3. Add symlink resolution and rejection for any path that escapes the configured workspace root.
4. Add filename sanitization for all binary-derived output names, preserving unsafe originals only in provenance metadata.
5. Enforce configurable maximum input size checks before analysis begins.
6. Add sample-containment protections so binaries, extracted scripts, and sample-controlled helpers are never executed as part of static workflows.
7. Prevent sample-controlled data from flowing into shell commands, identifiers, filenames, or protocol structure without sanitization.
8. Wrap all parser entry points with crash containment so malformed files return structured errors instead of killing the server.
9. Run parsers with minimal privileges inside the container.
10. Add deterministic tool-version pinning or equivalent version reporting to keep outputs reproducible.
11. Implement global timeout and resource-control plumbing for CPU, memory, runtime, recursion depth, artifact count, decompilation size, string count, and carved-byte budget.
12. Ensure all failures introduced here use the shared error taxonomy from Feature 01.

## Deliverables
- Secure path and filename utilities.
- Input-size gate and resource-limit configuration.
- Parser isolation wrapper and structured error propagation.
- Container/runtime hardening configuration.

## Acceptance Criteria
- Inputs and outputs outside the configured workspace root are rejected after canonicalization.
- Symlink-based escape attempts are rejected consistently for reads and writes.
- Unsafe filenames from carved artifacts are rewritten safely while provenance preserves the original name.
- Oversized samples fail fast with a clear structured error.
- Parser crashes are surfaced as structured failures and do not terminate the MCP server.
- Resource limits can be configured centrally and are available to later expensive operations.

## Requirements Covered
- §1 Shared Volume Workflow
- §11 Shared Workspace Mount
- §11 Offline-Friendly Operation
- §11 Sample Containment
- §11 Parser Crash Resilience
- §11 Filename Sanitization
- §11 Symlink Protection
- §11 Input Size Limits
- §11 Deterministic Tooling
- §11 Timeout and Resource Controls

## Notes for the Implementing Agent
- This feature should be finished before any recursive extraction or patch-writing workflow is implemented.
- Prefer reusable guards around all file-system and parser entry points instead of ad hoc checks in individual tools.
