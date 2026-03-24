# Feature 04: Core Disassembly and Analysis

## Goal
Integrate at least one headless analysis backend and expose the primary query surface for symbols, functions, code, bytes, and cross-references.

## Execute After
- `01-foundation-result-model-and-session-core.md`
- `02-security-and-workspace-hardening.md`
- `03-file-intake-and-metadata-triage.md`

## Enables
- `05-semantic-recovery-and-analyst-workflow.md`
- `06-signatures-extraction-and-obfuscation.md`
- `07-patching-multi-artifact-and-interop.md`

## Implementation Tasks
1. Integrate a primary headless analysis backend and register it through the shared capability model.
2. Implement initial artifact analysis as an asynchronous job with progress, cancellation, and partial-result handling.
3. Implement symbol extraction for imports, exports, ordinals, thunks, and unresolved symbols.
4. Implement symbol demangling and allow queries by mangled or demangled names.
5. Implement function enumeration with addresses, signatures, calling conventions, stack sizes, and analyzer confidence.
6. Implement targeted disassembly for functions and ranges, including bytes, addresses, comments, operand resolution, mixed code/data indicators, and active instruction-set mode.
7. Implement instruction-set-mode query and override support where the architecture supports multiple modes.
8. Implement targeted decompilation with warnings, failure states, and output-limiting controls.
9. Implement raw-byte inspection by file offset or virtual address.
10. Implement cross-reference queries for functions, addresses, globals, and strings.
11. Implement basic program search by names, strings, immediates, opcodes, byte patterns, and address ranges.
12. Implement relocation and linkage analysis covering GOT, PLT, IAT, fixups, and related linkage metadata.
13. Implement debug-information parsing for DWARF, PDB-derived metadata, and embedded source references where available.
14. Implement compact analysis synopsis output for an artifact or session.
15. Add tool dependency declaration metadata so the agent can discover which tools require completed analysis.
16. Add machine-actionable suggested next actions for the major tool responses introduced here.

## Deliverables
- Backend adapter with async analysis pipeline.
- Core query tools for code and symbol inspection.
- Analysis synopsis and dependency metadata.

## Acceptance Criteria
- An analyzed artifact can be queried for functions, disassembly, decompilation, xrefs, bytes, and symbols without backend-native text dumps.
- Large results can be bounded with truncation metadata and deterministic pagination where appropriate.
- Decompilation and disassembly failures are explicit and structured.
- The MCP can state which features and architectures are available for the loaded artifact.
- Tool responses can point the agent to valid next steps with tool names and parameter suggestions.

## Requirements Covered
- §0 Capability Introspection
- §0 Analysis Synopsis
- §0 Suggested Next Actions
- §0 Tool Dependency Declaration
- §0 Pagination and Truncation Controls
- §3 Symbol Extraction
- §3 Symbol Demangling
- §3 Function Enumeration
- §3 Targeted Disassembly
- §3 Targeted Decompilation
- §3 Raw Byte Inspection
- §3 Cross-References
- §3 Basic Program Search
- §3 Relocation and Linkage Analysis
- §3 Debug Information Parsing

## Notes for the Implementing Agent
- Prefer one solid backend integration first; add abstraction points only where they help later expansion.
- Make artifact-level capability reporting precise so unsupported features fail predictably.
