# Feature 07: Patching, Multi-Artifact Analysis, and Interoperability

## Goal
Support mutation workflows, cross-binary reasoning, and import/export interoperability once the analysis core is mature.

## Execute After
- `04-core-disassembly-and-analysis.md`
- `05-semantic-recovery-and-analyst-workflow.md`
- `06-signatures-extraction-and-obfuscation.md`

## Enables
- `08-transport-ops-and-polish.md`

## Implementation Tasks
1. Implement byte patching by file offset and virtual address, with warnings for overlapping instructions, relocations, and structurally sensitive regions.
2. Implement assembly-assisted patching for at least one architecture and expose supported ISAs through capability introspection.
3. Implement code-cave discovery with file offsets, virtual addresses, sizes, and section context.
4. Implement naming and type edits for functions, variables, globals, structs, enums, and typedefs.
5. Implement calling-convention override support so decompilation can be corrected manually.
6. Implement type and header import for C headers, protobuf definitions, or structured type descriptions.
7. Implement script-generation or command-log export for supported analysis actions.
8. Implement structured report export in compact JSON and optional human-readable form.
9. Extend the session model to support multi-binary sessions.
10. Implement dependency awareness across binaries, libraries, extracted children, and linked modules.
11. Implement cross-binary correlation for shared symbols, strings, function matches, and loader-payload relationships.
12. Implement binary diff support across structural, function-level, and semantic levels, using async jobs for expensive semantic diffing.
13. Implement analysis-database import for supported foreign formats with explicit loss reporting.
14. Implement analysis-database export in portable formats.
15. Implement signature-pack management for listing, loading, and applying packs to the current session.

## Deliverables
- Patch and output-generation toolset.
- Multi-artifact session extensions and correlation utilities.
- Analysis-state import/export and signature-pack workflows.

## Acceptance Criteria
- Patches can be applied and saved to the shared workspace with clear warnings but without unnecessary refusal.
- Supported assembly backends fail clearly on unsupported ISAs.
- Multiple artifacts can coexist in a session and be queried for dependencies and relationships.
- Diff operations report which levels are available and use async jobs for expensive semantic comparison.
- Imported and exported analysis databases disclose unsupported or lossy metadata explicitly.

## Requirements Covered
- §8 Byte Patching
- §8 Assembly-Assisted Patching
- §8 Code Cave Discovery
- §8 Naming and Type Edits
- §8 Calling Convention Override
- §8 Type and Header Import
- §8 Script Generation
- §8 Structured Report Export
- §9 Multi-Binary Sessions
- §9 Dependency Awareness
- §9 Cross-Binary Correlation
- §9 Binary Diff Support
- §10 Analysis Database Import
- §10 Analysis Database Export
- §10 Signature Pack Management

## Notes for the Implementing Agent
- Reuse annotation and snapshot mechanisms so patching and type-import workflows can be rolled back safely.
- Keep portability explicit: imported analysis should never pretend to preserve unsupported metadata.
