# Feature 08: Transport, Operational Polish, and Final Coverage

## Goal
Finish the product surface with transport support, quota enforcement, and any cross-cutting completeness work needed to close remaining requirements.

## Execute After
- `07-patching-multi-artifact-and-interop.md`

## Enables
- Release readiness

## Implementation Tasks
1. Implement stdio transport for local subprocess use if not already present in the skeleton.
2. Implement streamable HTTP transport for remote service deployment.
3. Add authentication, tenant isolation, and per-agent or per-session quota enforcement for HTTP mode.
4. Expose concurrency-safe session isolation for multiple agents in HTTP deployments while preserving the single-agent rule for a single session.
5. Audit every tool for compliance with the shared result schema, confidence, provenance, pagination, deterministic ordering, and structured error rules.
6. Audit every tool for explicit prerequisite and capability metadata.
7. Add or finish any remaining pagination and truncation controls on large text or collection results introduced in later features.
8. Add final analysis-synopsis coverage so extraction history, annotations, matched signatures, and unresolved unknowns are represented compactly.
9. Add final next-action generation coverage for the later-stage workflows, including extraction, diffing, patching, and export.
10. Verify offline-friendly operation paths and ensure optional networked enrichment remains explicitly opt-in.
11. Produce an implementation matrix that maps all requirement references in `requirements.md` to concrete tools or modules for ongoing maintenance.

## Deliverables
- HTTP transport with auth and quotas.
- Cross-cutting compliance audit and cleanup.
- Requirements traceability matrix.

## Acceptance Criteria
- The MCP works over stdio and authenticated HTTP.
- In HTTP mode, one agent cannot access another agent's sessions or exceed configured quotas without authorization.
- All large responses exposed by previous features support deterministic pagination or explicit truncation metadata.
- A traceability matrix shows every requirement in `requirements.md` is implemented or intentionally deferred with rationale.

## Requirements Covered
- §0 Pagination and Truncation Controls
- §0 Tool Descriptions and Naming
- §0 Tool Dependency Declaration
- §0 Capability Introspection
- §0 Analysis Synopsis
- §0 Suggested Next Actions
- §0 Deterministic Ordering and Filtering
- §11 MCP Transport

## Notes for the Implementing Agent
- Treat this feature as a closure pass, not a place to invent major new analysis behaviors.
- The implementation matrix should make future requirement drift obvious.
