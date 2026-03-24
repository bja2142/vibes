# Feature 09: Composite Brief Workflows and Token Budgeting

## Goal
Reduce model round trips and token usage by adding composite reverse-engineering commands that bundle common multi-step workflows into compact, bounded responses.

## Execute After
- `08-transport-ops-and-polish.md`

## Enables
- Token-efficient MCP calling
- Fewer back-and-forth turns for common reverse-engineering tasks
- Analyst-oriented summaries that preserve stable IDs for follow-up actions

## Implementation Tasks
1. Add shared response-budget controls for composite tools, including `verbosity`, `token_budget_hint`, `include_next_actions`, and `include_raw_sections`.
2. Implement `ingest_and_triage_artifact` to attach an artifact, triage it, and optionally queue analysis in one call.
3. Implement `analyze_and_summarize` to start analysis when needed, optionally wait for completion, and return a compact artifact brief tuned by `focus`.
4. Implement `hunt_interesting_regions` to combine prioritized functions, suspicious strings, imports, crypto hints, and library/runtime hints into a ranked shortlist.
5. Implement `trace_capability` to expand a function target into neighborhood, xrefs, variables, and bounded instruction previews without requiring several separate calls.
6. Implement `prepare_patch_plan` to combine instruction-mode awareness, target resolution, code-cave discovery, and candidate patch points into one patching-focused brief.
7. Implement `artifact_relationship_brief` to summarize parent-child relationships, dependency hints, correlation hits, and likely diff candidates for one artifact.
8. Expose focus presets for common use cases such as `general`, `malware`, `patching`, `diffing`, `firmware`, and `extraction` where they make sense.
9. Update capability reporting so clients can discover composite workflows and response-budget support programmatically.
10. Document the composite tools in the README, workflows guide, tool reference, and requirements matrix.
11. Add direct and MCP protocol tests that verify the composite tools reduce round trips while still returning stable IDs and bounded payloads.

## Deliverables
- Composite workflow tools for intake, analysis, hunting, tracing, patch planning, and relationship briefing.
- Shared response-budget profile logic.
- Updated docs and tests.

## Acceptance Criteria
- A model can ingest, triage, and queue analysis in one call.
- A model can request a compact analysis brief without manually polling several lower-level tools once analysis is ready.
- Composite tools preserve actionable IDs and return only bounded previews by default.
- Budget controls deterministically reduce list sizes and raw-section detail.
- The MCP advertises composite workflow and response-budget capabilities.

## Requirements Covered
- §0 Pagination and Truncation Controls
- §0 Tool Descriptions and Naming
- §0 Tool Dependency Declaration
- §0 Capability Introspection
- §0 Analysis Synopsis
- §0 Suggested Next Actions
- §1 Token-Efficient References
- §5 Search, Navigation, And Workflow Acceleration

## Notes for the Implementing Agent
- Favor composition over reinvention: reuse existing triage, analysis, patching, correlation, and prioritization helpers.
- Return ranked shortlists, not full tables.
- Keep raw sections opt-in and bounded.
