# Reversing MCP Overview

## Purpose

`reversing-mcp` is a reverse engineering MCP server for static analysis workflows. It is designed around a workspace-local session model so an agent or human client can ingest artifacts, analyze them, persist results, annotate findings, extract derived artifacts, and continue working across turns without re-uploading state.

## Core Model

The server revolves around these persisted objects:

- `session`: named workspace-local analysis state
- `artifact`: a file attached to a session
- `function_id`: a stable function handle for the current analysis generation of an artifact
- `string_id`: a stable string handle for the current analysis generation of an artifact
- `annotation`: persisted analyst state with revision history
- `snapshot`: a whole-session checkpoint
- `job`: async analysis or reanalysis handle

Analysis-derived object IDs are generation-scoped. When an artifact is reanalyzed, old `function_id` and `string_id` values expire by design.

## Feature Areas

### Foundation

- Structured result envelopes with provenance, confidence, partial-result signaling, and normalized errors
- Persistent sessions and workspace-local state
- Provisional object IDs
- Annotation history and revert
- Session snapshots
- Async jobs and cancellation

### File Intake And Triage

- Format detection for ELF, PE, Mach-O, ZIP, TAR, Intel HEX, SREC, and raw hinted blobs
- Header, section, segment, mitigation, and signature reporting
- String extraction
- Address translation
- Child-artifact mapping for supported containers

### Core Analysis

- Async program analysis with `angr CFGFast`
- Ghidra headless decompilation for higher-quality pseudo-C output on complex binaries
- Ghidra headless full analysis export (functions, strings, imports, sections)
- Custom Ghidra Python script execution with full API access
- Function and symbol enumeration
- Disassembly and disassembly-range queries
- Best-effort pseudo-C decompilation (angr) and Ghidra decompilation
- Raw byte inspection
- Cross-reference queries
- Search across strings, names, immediates, opcodes, and ranges
- Linkage and debug metadata

### Semantic Recovery

- Call graph and control-flow graph views
- Variable and stack-frame recovery
- Constant propagation
- Type information and heuristic type recovery
- Data-segment inspection
- Indirect-flow recovery
- Exception metadata
- Calling convention and IR access
- Runtime metadata
- Static data-flow slicing
- System-call identification
- Neighborhood navigation
- Prioritization and function classification
- Analyst workflow items and curated exports
- Batch operations across all artifacts in a session

### Signatures, Extraction, And Obfuscation

- YARA-style scanning through real YARA if installed or heuristic fallback
- Crypto-constant detection
- Library and runtime recognition
- Compiler and toolchain fingerprinting
- Packer heuristics
- Entropy reporting
- Bounded string deobfuscation
- Resource extraction
- Embedded-artifact carving
- Recursive handoff of extracted artifacts back into sessions
- Parent-child artifact relationship tracking

### Patching, Overrides, And Interop

- Byte patching by file offset, RVA, or virtual address
- Compact built-in assembly patching for `x86`, `x86_64`, `aarch64`, `arm`, and `thumb`
- Code-cave discovery
- Artifact-local overrides for names, types, globals, variables, and calling conventions
- Type import from headers or structured definitions
- Command-log and analysis-report export
- Dependency summaries, artifact correlation, and structural diffing

### Transport And Operational Model

- Local stdio transport for subprocess MCP clients
- Streamable HTTP transport for shared service deployment
- Optional HTTP bearer auth, disabled by default unless explicitly required
- Per-agent request quotas and per-tenant session or job quotas
- Single-agent session leasing for HTTP clients within a tenant
- Capability reporting for transports, quotas, patching support, and composite workflows

### Composite Brief Workflows

- One-shot intake and triage through `ingest_and_triage_artifact`
- Compact post-analysis summaries through `analyze_and_summarize`
- Ranked hunting shortlists through `hunt_interesting_regions`
- Function-level expansion through `trace_capability`
- Patch planning through `prepare_patch_plan`
- Multi-artifact relationship summaries through `artifact_relationship_brief`
- Shared response-budget controls with `verbosity`, `token_budget_hint`, `include_next_actions`, and `include_raw_sections`

## Safety And Resource Controls

The server is designed for static-only workflows.

- Inputs must remain inside the workspace root
- Parsing happens in an isolated subprocess
- Input size, parser timeout, parser memory, parser CPU, recursion depth, string counts, artifact counts, and carved bytes are all capped
- Derived outputs use sanitized filenames and workspace-safe output paths

### Cross-Server Bridge

- JSON manifest export for dynamic analysis tools via `export_dynamic_manifest`
- Designed for use with the companion `pwn-mcp` dynamic analysis server
- Exports function addresses, strings, imports, and artifact metadata to a shared workspace volume

## Recommended Reading

- [Getting Started](getting-started.md)
- [Workflows](workflows.md)
- [Tool Reference](tool-reference.md)
- [PWN-MCP Tool Reference](pwn-tool-reference.md)
- [Cross-Server Workflows](cross-server-workflows.md)
