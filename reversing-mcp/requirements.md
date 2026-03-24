# Requirements for Static Binary Analysis MCP

This document defines the requirements and capabilities for a static-analysis-focused MCP that assists an expert reverse engineering agent. The current scope is intentionally limited to static analysis tasks. Dynamic debugging, tracing, emulation, or live execution support are out of scope for this phase.

Interpreted and bytecode formats such as Java `.class`/`.jar`, .NET CIL, Python `.pyc`, and Dalvik `.dex` are out of scope for this phase. These require fundamentally different analysis pipelines and may be addressed in a future extension.

The MCP is expected to operate as a stateful analysis system rather than a collection of isolated one-shot tools. One call should be able to create analysis context that later calls can reuse without re-consuming unnecessary tokens. The MCP will run in a containerized environment with a shared volume so the agent and the analysis container can exchange binaries, extracted artifacts, patches, reports, and other intermediate files over disk.

All tool responses should be designed for an LLM caller rather than a human GUI user. Large result sets should be paginated, expensive jobs should expose an asynchronous job model, and every result should make clear what is exact, what is inferred, and which backend or tool produced it.

**Referencing convention:** Requirements are referenced by section number and bold bullet name (e.g., §2 File Typing, §4 Constant Propagation Queries). Bullet names are stable identifiers; do not reorder or rename them without updating all cross-references in §12.

## 0. Result Model and MCP Ergonomics
The MCP should define a consistent result schema across tools so the agent can compose calls safely.

### Cross-Cutting Principles

The following principles apply uniformly across all tools and sections. Individual sections reference them by name rather than restating them.

*   **Confidence and Evidence:** Any result that is inferred, heuristic, or incomplete must include a confidence indicator and the evidence or method that produced it. Confidence must use a standard enum: `exact`, `high`, `medium`, `low`, or `speculative`, plus an optional free-text `method` field describing what produced the result. Confidence should be attachable to individual recovered facts such as variable types, inferred symbols, function boundaries, taxonomy labels, triage scores, or classification tags — not only to whole-tool responses.
*   **Provenance:** Every result must record which tool, analyzer, or backend produced it, what parameters were used, and whether the result is exact or inferred. For carved and extracted outputs, provenance includes source artifact, byte range or container path, extraction method, and any parser warnings.
*   **Pagination and Truncation Controls:** Any list or large text result should support deterministic ordering, `limit`, and continuation or cursor parameters. Default response sizes should target approximately 4,000 tokens to stay within practical LLM context budgets; the agent may request larger pages explicitly. Cursors must be opaque but stable within a session and must not expire during normal analysis workflows. Large fields such as decompilation, disassembly, string tables, and xref lists should support truncation with metadata indicating whether the result was partial.
*   **Error Taxonomy:** All tools must return structured failure states using a defined set of error categories so the agent can distinguish between conditions that warrant different recovery strategies. At minimum, the taxonomy must distinguish: unsupported format or architecture, invalid or expired object ID, analysis timeout or resource limit exceeded, backend failure, missing prerequisite (e.g., analysis not yet run), and partial result. Additional categories may be added per tool.

### Result Schema and Object Model

*   **Stable Object Identity:** Session IDs, artifact IDs, function IDs, string IDs, and other analysis object IDs must remain stable within a session across repeated calls and incremental analysis. IDs are invalidated only by operations that change the underlying object mapping — specifically: re-analysis of the artifact, removal of the artifact from the session, or session destruction. If an ID becomes invalid, the MCP must report that explicitly using the `invalid_id` error category rather than silently returning a different object.
*   **Structured Result Schema:** All tools should return structured JSON with explicit typed fields rather than backend-native free-form text dumps. Sample-controlled text such as strings, comments recovered from binaries, or carved filenames should be returned in dedicated string fields that are never interpolated into tool names, parameter names, or protocol-level structures. All binary-derived string content must be properly JSON-escaped; no binary-derived content may appear in JSON keys or structural positions. This prevents output injection where a binary contains strings resembling JSON structure or MCP protocol messages.

### Agent Discoverability and Workflow Guidance

*   **Tool Descriptions and Naming:** Each tool's MCP description must be a concise natural-language sentence that an LLM can use for tool selection. Parameter names should be self-documenting. Each tool must declare its prerequisites (e.g., "requires an active session with a loaded artifact") so the agent can determine valid call sequences without trial and error.
*   **Tool Dependency Declaration:** The MCP should expose prerequisite relationships between tools so the agent knows what must be called first. For example, decompilation requires that initial analysis has completed. This may be expressed via capability introspection, per-tool metadata, or a dedicated dependency query.
*   **Capability Introspection:** The MCP should expose both global capabilities and per-artifact capabilities so the agent can determine which analyses, patching operations, or decompilation features are available for a specific binary and backend.
*   **Analysis Synopsis:** Provide a tool that returns a compact, token-efficient summary of the current analysis state for an artifact or session, including key metrics (function count, string count, matched signatures), applied annotations, extraction history, and outstanding unknowns. This allows the agent to rebuild working context after context window pressure without re-querying individual tools.
*   **Suggested Next Actions:** Tool responses should include optional hints about logical next steps given the current analysis state, or the MCP should expose a dedicated tool that takes the current state and returns ranked next actions. Suggestions must be machine-actionable: each should include the tool name, suggested parameter values, and a brief rationale so the agent can invoke them directly rather than interpreting prose.

### Operational Model

*   **Asynchronous Job Model:** Long-running operations such as initial auto-analysis, recursive carving, large YARA scans, and cross-binary diffing should return job handles that expose progress, partial results where possible, and cancellation.
*   **Deterministic Ordering and Filtering:** Queries that return collections should define default sort order and expose stable filters so the agent can page through large result sets without duplicates or hidden backend-specific ordering changes.

## 1. Operating Model and State Management
The MCP must support iterative expert workflows where analysis results accumulate across multiple calls.

*   **Stateful Sessions:** Create, load, list, and destroy analysis sessions/projects tied to one or more input binaries, with clearly defined isolation for mutable annotations and analysis settings. Session semantics: a session may contain multiple artifacts (see §9). Session state is persisted to the shared volume so that it survives container restarts and is recoverable by any container instance that mounts the same volume. Sessions are single-agent; concurrent access from multiple agents to the same session is not supported. Destroying a session removes analysis state but does not delete files previously written to the shared volume.
*   **Persistent Analysis Context:** Preserve discovered functions, names, comments, labels, types, structs, bookmarks, and analysis settings across calls.
*   **Token-Efficient References:** Allow later calls to refer to prior analysis objects by stable identifiers, addresses, function names, artifact IDs, or session IDs instead of resending large context blobs.
*   **Shared Volume Workflow:** Read input binaries from a mounted workspace and write extracted files, reports, patched binaries, and generated scripts back to the shared volume. All paths should resolve under configured workspace roots after canonicalization.
*   **Reproducible Outputs:** Export machine-readable analysis results such as JSON so the agent can chain tool outputs safely.
*   **Provenance and Auditability:** Per the cross-cutting Provenance principle in §0.
*   **Error and Confidence Reporting:** Per the cross-cutting Confidence and Evidence and Error Taxonomy principles in §0.
*   **Annotation History:** Track a per-annotation revision history for agent-applied annotations such as renames, type changes, and comments so the agent can view prior values and revert individual edits within a session. The history is scoped per annotation (not per function or per session) and does not need to support diffing between revisions or cross-annotation transactions.
*   **Progress Reporting and Cancellation:** The agent must be able to determine whether a long-running operation has completed, is making progress, or should be abandoned, and must be able to cancel it. The mechanism for progress reporting (polling, streaming, or callback) is an implementation choice.
*   **Batch Operations:** Support applying the same query or action, such as string extraction, YARA scan, or function enumeration, across all artifacts in a session in a single call rather than requiring per-artifact iteration.
*   **Architecture and Format Capability Reporting:** Expose which architectures, binary formats, and analysis features are available in the current container so the agent can gracefully degrade or inform the user when a target is unsupported.
*   **Session Snapshots:** Support creating named snapshots of a session's analysis state that the agent can revert to if a destructive operation (e.g., a bad type import or re-analysis) corrupts the working state. Snapshot granularity is whole-session; per-annotation rollback is covered by Annotation History.

## 2. File Identification and Metadata Analysis
Before deep analysis begins, the agent needs to understand what it is looking at.

*   **File Typing:** Identify architecture, endianness, bitness, platform, and format such as ELF, PE, Mach-O, raw firmware blob, archive, fat binary, or multi-image container.
*   **Binary Taxonomy:** Classify the binary at a macro level where possible using an extensible, documented category schema. Initial categories should include at least: CLI tool, GUI application, service or daemon, kernel driver, shared library, shellcode, firmware image, and installer. Implementations may extend the schema with additional categories. Taxonomy results should be treated as ranked hypotheses with evidence per the cross-cutting Confidence and Evidence principle. Note: classification accuracy degrades significantly for stripped or statically-linked binaries; the MCP should return `speculative` confidence in these cases rather than guessing.
*   **Hashing:** Generate cryptographic hashes such as MD5, SHA1, and SHA256 plus similarity or intelligence-oriented hashes such as `ssdeep` and `imphash`.
*   **Header Parsing:** Extract execution environment details, entry points, image base, section or segment layout, permissions, subsystem, linker metadata, relocations, and load configuration where relevant.
*   **Segment and Section Discrepancy Analysis:** Detect and report mismatches between the segment (load) view and the section (link) view, including stripped or missing section tables, overlapping segments, and non-standard alignment, as these are common in packed or malicious binaries.
*   **Address Translation:** Convert between file offsets, virtual addresses, relative virtual addresses, sections, and segments.
*   **String Extraction:** Extract ASCII and Unicode strings with addresses, encoding metadata including detection of UTF-8, UTF-16LE, UTF-16BE, and code-page-specific encodings, and section context to identify URLs, file paths, mutexes, command strings, or registry keys.
*   **Security Mitigations Check:** Analyze compiled-in security features such as NX or DEP, ASLR or PIE, stack canaries, RELRO, CFG, and similar binary hardening features when the format supports them.
*   **Certificate and Signature Analysis:** Parse and report Authenticode signatures for PE, code-signing metadata for Mach-O, and ELF `.note.gnu.build-id` where present. Report certificate chain details, validity, and whether the signature covers the full binary or has been tampered with.
*   **Relocatable Object File Support:** Accept relocatable object files (`.o`, `.obj`, `.ko`) and handle unresolved relocations, missing virtual address maps, and section-relative addressing.
*   **Firmware and Headerless Binary Support:** Accept raw firmware blobs, Intel HEX, S-records, and UEFI images with agent-supplied hints for base address, architecture, endianness, and memory map when headers are absent or non-standard.
*   **Format-Specific Deep Inspection:** Parse format-specific metadata that is commonly abused or analytically important, including TLS callbacks and delay-load descriptors for PE, init and fini arrays and GNU notes for ELF, and LC_LOAD_DYLIB chains and code-signing metadata for Mach-O.
*   **Container and Child Artifact Mapping:** For archives, fat binaries, installers, and firmware containers, expose parent-child relationships, embedded artifact offsets, and per-child metadata so the agent can analyze nested components without losing provenance.
*   **Optional External Enrichment:** When network access is available and explicitly opted in, support hash-based lookups against external services such as VirusTotal, NSRL known-good databases, or symbol servers for PDB retrieval. Enrichment must be opt-in, clearly labeled as external, and the MCP must function fully without it.

## 3. Core Disassembly and Decompilation
This is the heart of the MCP, requiring integration with a headless analysis framework. The implementation should prefer open-source, non-licensed tools.

*   **Symbol Extraction:** List imported libraries and functions, exported functions, ordinal imports, thunk functions, and unresolved symbols.
*   **Symbol Demangling:** Present both mangled and demangled forms for C++, Rust, Swift, D, Go, and other name-mangling schemes, and allow the agent to query by either form.
*   **Function Enumeration:** Retrieve all identified functions with start and end addresses, signatures, calling conventions, stack sizes, and analyzer confidence where available.
*   **Targeted Disassembly:** Retrieve assembly instructions for a specific function or memory range with bytes, addresses, comments, symbolic operand resolution, instruction set mode (e.g., ARM vs Thumb, x86 16/32/64-bit), and explicit indication when the analysis framework has flagged a region as mixed code/data, overlapping with a data reference, or containing an alternate instruction stream (e.g., Thumb interworking). The agent must be able to query and override the active instruction set mode when the architecture supports multiple.
*   **Targeted Decompilation:** Retrieve pseudo-C output for a specific function and expose decompiler warnings or failure states. Decompilation requests should support output controls such as line limits or block-range selection so the agent can stay within context limits.
*   **Raw Byte Inspection:** Read and display arbitrary byte ranges from an artifact by file offset or virtual address, with configurable length, hex and ASCII representation, and optional interpretation hints such as pointer width and endianness.
*   **Cross-References:** Find all locations that call a specific function, reference a specific address, access a global, or reference a specific string.
*   **Basic Program Search:** Search by function name, symbol, string contents, immediate value, opcode pattern, byte pattern, or address range.
*   **Relocation and Linkage Analysis:** Enumerate and interpret relocations, fixups, and dynamic linking structures including GOT, PLT, IAT thunks, delay-load descriptors, and binding metadata to clarify how the binary resolves external references.
*   **Debug Information Parsing:** When present, parse DWARF debug info, PDB symbols, or embedded source references to seed function names, variable names, types, and source-line mappings into the analysis context.

## 4. Control, Data Flow, and Semantic Recovery
To understand the logic without executing the binary.

*   **Call Graphs:** Generate a graph or adjacency view of how functions interact, including incoming and outgoing edges.
*   **Control Flow Graphs:** Provide basic block structure, branch targets, loop edges, and fallthrough behavior within a specific function.
*   **Variable Recovery:** Expose stack variables, arguments, register-based parameters, globals, and their inferred types.
*   **Stack Frame Layout:** Provide a structured view of a function's stack frame including each local variable's offset, size, inferred type, and cross-references. This is a first-class query distinct from general variable recovery, as it supports reasoning about buffer sizes, adjacency, and overflow potential.
*   **Constant Propagation Queries:** For a given instruction or call site, report statically-determinable argument values, resolved immediates, and propagated constants where the analysis framework supports it. This is critical for understanding API call semantics such as flags passed to system calls. Results are best-effort and depend on the upstream decompiler's analysis quality; the MCP should indicate when propagation is incomplete or ambiguous rather than omitting the result silently.
*   **Type Information Query:** Query existing data types, structures, unions, enums, classes, and typed memory at specific addresses as recovered by the analysis framework.
*   **Automated Type Recovery:** Attempt recovery of RTTI, vtables, and class hierarchies where the analysis framework supports it. This is highly framework-dependent and may be incomplete for stripped C++ binaries; results should carry per-item confidence and are best-effort.
*   **Data Segment Inspection:** Query initialized data regions (`.data`, `.rodata`, `.bss`, and equivalents) as structured data — interpret arrays, pointer tables, vtable entries, or configuration blobs at a given address using agent-supplied type hints or inferred types. This complements code-focused queries for binaries where significant logic is data-driven.
*   **Indirect Flow Recovery:** Identify jump tables, switch statements, function pointers, virtual dispatch sites, and unresolved indirect calls. Unresolved indirects must be reported explicitly with the analysis framework's confidence, as jump table recovery is inherently heuristic and incomplete.
*   **Exception and Unwind Metadata:** Extract exception handlers, unwind info, and related control metadata when the format supports it.
*   **Calling Convention Query:** Allow the agent to query the detected calling convention for any function.
*   **Intermediate Representation Access:** Expose the analysis framework's intermediate language for a given function or instruction range so the agent can reason about instruction semantics at a finer grain than decompiled C. The specific IR depends on the backend in use.
*   **Language Runtime Metadata Recovery:** Recover and query language-specific metadata where present, including C++ RTTI and vtables, Go symbol and runtime metadata, Objective-C class and selector metadata, Swift reflection artifacts, and Rust-specific naming and type artifacts.
*   **Static Data-Flow Slicing:** Support backward and forward data-flow slicing from a given instruction or variable, bounded by configurable depth and scope, using the analysis framework's static analysis capabilities. This enables the agent to trace where a value came from or where a result flows without execution.
*   **System Call Identification:** Map raw system call instructions (`syscall`, `int 0x80`, `svc`, etc.) to resolved syscall numbers and names given the target OS and architecture. This is critical for understanding stripped binaries that bypass libc.

## 5. Search, Navigation, and Workflow Acceleration
An expert reverse engineering agent needs fast navigation primitives, not just isolated lookups.

*   **Recursive Navigation:** Find callers, callees, and transitive neighborhoods around a target function up to a configurable depth, with explicit bounds on returned nodes and edges plus continuation support when the result is too large for a single response.
*   **Neighborhood Queries:** Retrieve nearby functions, surrounding strings, adjacent basic blocks, and references within a bounded radius from an address.
*   **Filtering and Prioritization:** Filter out known library code, compiler helpers, thunks, or autogenerated stubs so the agent can focus on custom logic. Each excluded item must include the mechanism that triggered exclusion (e.g., signature match, symbol-name heuristic, or section attribution) and a confidence level. Filtering should be non-destructive — excluded items must remain queryable.
*   **Triage Scoring:** Compute per-function complexity metrics such as cyclomatic complexity, basic block count, and instruction count, and flag functions that reference suspicious API patterns, high-entropy data, or anti-analysis idioms so the agent can prioritize where to look first. Heuristic scores should include evidence and tunable thresholds. Note: static detection of anti-analysis idioms (timing checks, debugger detection, VM detection) has inherently high false-positive rates; scores are advisory.
*   **Function Classification Tags:** Automatically tag functions by behavioral category where detectable, such as cryptographic, networking, file I/O, string manipulation, anti-analysis, or memory allocation, to support filtered queries and triage. These tags should be best-effort classifications rather than hard labels.
*   **Bookmarks and Named Regions:** Save interesting addresses, functions, or ranges for later retrieval within the same analysis session.
*   **Analysis Notes:** Allow the agent to attach comments, tags, hypotheses, and short analyst notes to functions, addresses, or artifacts.
*   **Curated Artifact Export:** Allow the agent to export a compact saved view containing selected functions, strings, xrefs, notes, and carved artifacts for reuse in later prompts or downstream tooling.

## 6. Pattern Matching and Signature Analysis
Identifying known malware families, cryptographic algorithms, or standard library code.

*   **YARA Scanning:** Run custom or community YARA rules against binaries and extracted artifacts.
*   **Crypto Constant Detection:** Identify common cryptographic constants such as AES s-boxes, CRC tables, RSA constants, or custom lookup tables. Results should include evidence and confidence because constant matching is inherently heuristic.
*   **Library Code Recognition:** Use mechanisms like Ghidra Function ID or FLIRT-style signatures to identify standard library and compiler-generated code.
*   **Compiler and Toolchain Fingerprinting:** Detect likely compiler families, packer stubs, runtime libraries, and build traits when possible.

## 7. Obfuscation, Packing, and Artifact Extraction
Handling binaries that actively try to thwart static analysis.

*   **Packer Detection:** Identify common packers such as UPX, ASPack, or Themida using signature-based or heuristic methods.
*   **Entropy Calculation:** Calculate Shannon entropy for the full file and for individual sections or extracted regions.
*   **Static String Deobfuscation:** Recover encoded or stack-constructed strings where possible without execution using heuristic engines. Coverage is limited to patterns the engine supports (primarily stack strings and simple XOR encoding); results should indicate the deobfuscation method used and are not expected to be comprehensive.
*   **Resource Extraction:** Extract embedded files, icons, manifests, and secondary payloads from PE resources and equivalent format-specific resource containers.
*   **General Embedded Artifact Carving:** Extract overlays, appended blobs, archives, embedded firmware components, and suspicious byte ranges into standalone files on disk. Extraction must detect and abort on decompression bombs (zip bombs, recursive archives, or inputs with extreme expansion ratios) in addition to respecting the carved-byte budget defined in §11.
*   **Recursive Analysis Handoff:** Feed extracted artifacts back into new or existing analysis sessions without manual re-ingestion overhead. Recursive extraction must respect the resource controls defined in §11 (extraction depth, artifact count, and carved-byte budget). When a limit is reached, the MCP must stop extraction, return all artifacts extracted so far as partial results, and report which containers were not fully extracted so the agent can selectively continue.
*   **Artifact Relationship Tracking:** Preserve provenance for carved and extracted outputs, including source artifact, byte range or container path, extraction method, and any parser warnings.

## 8. Binary Modification and Output Generation
Static reversing often requires validating hypotheses by rewriting bytes or exporting machine-consumable artifacts, even without executing the result.

*   **Byte Patching:** Patch bytes at file offsets or virtual addresses and save the modified artifact to the shared volume. The MCP should warn when a patch overlaps known instruction boundaries, relocation targets, or other structurally significant regions, but must not refuse the operation — the agent may be intentionally patching across boundaries.
*   **Assembly-Assisted Patching:** Assemble short instruction sequences and apply them at requested locations when the selected backend supports assembly for the target ISA. The MCP must support assembly for at least one architecture and report supported ISAs via capability introspection. When assembly is unavailable for the target ISA, the MCP must fail clearly rather than silently rewriting bytes incorrectly.
*   **Code Cave Discovery:** Identify candidate code caves — contiguous unused, padding, or NOP regions of configurable minimum size — suitable for patch injection, with their file offset, virtual address, and containing section.
*   **Naming and Type Edits:** Rename functions, variables, globals, and symbols; define or update structs, enums, and typedefs.
*   **Calling Convention Override:** Override the detected calling convention for a function when auto-detection is wrong, as this directly affects decompilation accuracy for non-standard or hand-written assembly.
*   **Type and Header Import:** Accept C header files, protobuf definitions, or structured type descriptions from the agent and apply them to the analysis context to seed known struct layouts, function signatures, and enums.
*   **Script Generation:** Export repeatable scripts or command logs for selected analysis actions when the underlying framework supports it.
*   **Structured Report Export:** Generate compact JSON and optional human-readable summaries of findings, extracted indicators, symbols, and relationships.

## 9. Multi-Artifact and Cross-Binary Analysis
Real reverse engineering often involves more than one file.

*   **Multi-Binary Sessions:** Associate multiple binaries, libraries, extracted modules, or firmware parts with the same session.
*   **Dependency Awareness:** Track imports, linked modules, shared libraries, and extracted child artifacts across the session.
*   **Cross-Binary Correlation:** Resolve shared symbols, compare strings or functions across related artifacts, and help identify loader-payload relationships.
*   **Binary Diff Support:** Compare two binaries or two versions of the same binary at multiple levels: structural diffing (sections, imports, exports, strings — relatively cheap), function-level diffing (matched/added/removed functions based on name, address, or hash — moderate cost), and semantic diffing (basic-block and instruction-level similarity scoring — expensive, requires full analysis of both binaries). The MCP should report which diff levels are available via capability introspection and prefer open-source diffing backends. Semantic diffing should use the asynchronous job model as it may require extended analysis time for large binaries.

## 10. Analysis Import and Export Interoperability
Expert workflows frequently involve moving analysis state between tools and teams.

*   **Analysis Database Import:** Import existing analysis state from other tools where feasible, including Ghidra `.gzf` archives and BinExport protobuf files. Report any lossy conversions or unsupported metadata explicitly. IDA users should export to BinExport as the interchange path.
*   **Analysis Database Export:** Export the current session's analysis state in portable formats such as BinExport, SARIF, or framework-native project archives so work can continue in other tools.
*   **Signature Pack Management:** Allow the agent to list, load, and apply FLIRT, Function ID, or custom signature packs to the current analysis session.

## 11. Container, Security, and Operational Requirements
The Docker environment is part of the product surface and must support safe, repeatable static analysis.

*   **Shared Workspace Mount:** Mount a host directory into the container for binaries, extracted artifacts, reports, scripts, and patched outputs.
*   **Offline-Friendly Operation:** Prefer tools and workflows that do not require network access during analysis.
*   **Sample Containment:** Treat all input files as untrusted and avoid implicit execution paths. The MCP should not invoke target binaries, extracted scripts, or sample-controlled helpers as part of static analysis workflows. Sample-controlled data must not be used in file paths, command arguments, or identifiers without sanitization.
*   **Parser Crash Resilience:** All binary parsing operations (ELF, PE, Mach-O, DWARF, etc.) must catch parser crashes and return structured error responses rather than crashing the MCP server. Parsers should run with minimal privileges within the container. A malformed input must never take down the server or corrupt other sessions.
*   **Filename Sanitization:** All write operations — including artifact carving, resource extraction, and report generation — must sanitize output filenames derived from binary content. Filenames must not contain path separators, null bytes, or other characters that could cause traversal or injection. Carved artifacts that inherit names from the binary must be rewritten to safe names with provenance metadata preserved separately.
*   **Symlink Protection:** All file operations must resolve symlinks and reject any that resolve outside the configured workspace root. This applies to both input files and extracted artifacts (e.g., a crafted archive containing symlinks pointing outside the workspace).
*   **Input Size Limits:** The MCP must enforce a configurable maximum input artifact size and reject files exceeding it with a clear error. The default limit should be in the range of 100–500 MB to accommodate firmware images while preventing resource exhaustion from maliciously large inputs.
*   **Deterministic Tooling:** Pin tool versions where practical so analysis output is stable and reproducible.
*   **Timeout and Resource Controls:** Bound CPU, memory, and analysis runtime for expensive operations and report truncation or timeout conditions clearly. Resource controls should also cover recursive extraction depth, artifact count, decompilation size, string count, and total carved-byte budget.
*   **MCP Transport:** Support at least stdio and streamable HTTP transports so the MCP can be used both as a local subprocess and as a networked service, and handle concurrent sessions from multiple agents when running in HTTP mode. When running in HTTP mode, the MCP must enforce authentication and session isolation so that one agent cannot access another agent's sessions or exhaust shared resources without authorization. Per-session or per-agent resource quotas (concurrent jobs, total CPU time, storage) should be configurable to prevent a single agent from monopolizing shared infrastructure.

## 12. Suggested Build Phasing
This project should be built in coherent layers so the earliest implementation is already useful to an expert agent. Each phase lists must-have items first, then should-have items after the divider. Requirements are referenced by section and bullet name.

*   **Phase 0: Skeleton and Triage.** The minimum to let an agent triage binaries.
    *   Must-have: Session lifecycle (§1 Stateful Sessions), structured JSON result schema (§0 Structured Result Schema), error taxonomy (§0 Error Taxonomy), shared volume I/O (§1 Shared Volume Workflow), file typing (§2 File Typing), hashing (§2 Hashing), string extraction (§2 String Extraction), security mitigations check (§2 Security Mitigations Check), parser crash resilience (§11 Parser Crash Resilience).
    *   Should-have: Input size limits (§11 Input Size Limits), sample containment (§11 Sample Containment), filename sanitization (§11 Filename Sanitization), symlink protection (§11 Symlink Protection), binary taxonomy (§2 Binary Taxonomy), header parsing (§2 Header Parsing), address translation (§2 Address Translation), tool descriptions and naming (§0 Tool Descriptions and Naming).

*   **Phase 0.5: Security Hardening.** Harden the container before exposing to untrusted inputs at scale.
    *   Must-have: Input size limits (§11 Input Size Limits), sample containment (§11 Sample Containment), filename sanitization (§11 Filename Sanitization), symlink protection (§11 Symlink Protection).

*   **Phase 1: Core Analysis.** Load binaries into the analysis framework and expose the primary query interface.
    *   Must-have: Stable object IDs (§0 Stable Object Identity), capability introspection (§0 Capability Introspection), async job model (§0 Asynchronous Job Model), symbol extraction (§3 Symbol Extraction), symbol demangling (§3 Symbol Demangling), function enumeration (§3 Function Enumeration), targeted disassembly (§3 Targeted Disassembly), targeted decompilation (§3 Targeted Decompilation), raw byte inspection (§3 Raw Byte Inspection), cross-references (§3 Cross-References), basic program search (§3 Basic Program Search), analysis synopsis (§0 Analysis Synopsis), timeout and resource controls (§11 Timeout and Resource Controls), naming and type edits (§8 Naming and Type Edits).
    *   Should-have: Pagination and truncation (§0 Pagination and Truncation Controls), deterministic ordering (§0 Deterministic Ordering and Filtering), debug information parsing (§3 Debug Information Parsing), tool dependency declaration (§0 Tool Dependency Declaration), suggested next actions (§0 Suggested Next Actions).

*   **Phase 2: Semantic Recovery and Agent Workflow.** Deeper analysis and agent productivity tools.
    *   Must-have: CFG and call graph queries (§4 Call Graphs, §4 Control Flow Graphs), variable recovery (§4 Variable Recovery), stack frame layout (§4 Stack Frame Layout), constant propagation queries (§4 Constant Propagation Queries), calling convention query (§4 Calling Convention Query), calling convention override (§8 Calling Convention Override), notes and bookmarks (§5 Analysis Notes, §5 Bookmarks and Named Regions), filtering and prioritization (§5 Filtering and Prioritization), structured report export (§8 Structured Report Export).
    *   Should-have: Type information query (§4 Type Information Query), automated type recovery (§4 Automated Type Recovery), data segment inspection (§4 Data Segment Inspection), indirect flow recovery (§4 Indirect Flow Recovery), triage scoring (§5 Triage Scoring), function classification tags (§5 Function Classification Tags), annotation history (§1 Annotation History), artifact relationship tracking (§7 Artifact Relationship Tracking), curated artifact export (§5 Curated Artifact Export), type and header import (§8 Type and Header Import), system call identification (§4 System Call Identification), static data-flow slicing (§4 Static Data-Flow Slicing), session snapshots (§1 Session Snapshots).

*   **Phase 3: Advanced Recognition and Extraction.** Pattern matching, obfuscation handling, and artifact carving.
    *   Must-have: YARA scanning (§6 YARA Scanning), library code recognition (§6 Library Code Recognition), packer detection (§7 Packer Detection), entropy calculation (§7 Entropy Calculation), resource extraction (§7 Resource Extraction), general embedded artifact carving (§7 General Embedded Artifact Carving), recursive analysis handoff (§7 Recursive Analysis Handoff), segment and section discrepancy analysis (§2 Segment and Section Discrepancy Analysis).
    *   Should-have: Crypto constant detection (§6 Crypto Constant Detection), compiler and toolchain fingerprinting (§6 Compiler and Toolchain Fingerprinting), static string deobfuscation (§7 Static String Deobfuscation), certificate and signature analysis (§2 Certificate and Signature Analysis), format-specific deep inspection (§2 Format-Specific Deep Inspection), container and child artifact mapping (§2 Container and Child Artifact Mapping), language runtime metadata recovery (§4 Language Runtime Metadata Recovery), IR access (§4 Intermediate Representation Access).

*   **Phase 4: Multi-Artifact, Mutation, and Interoperability.** Cross-binary workflows, patching, and tool interop.
    *   Must-have: Multi-binary sessions (§9 Multi-Binary Sessions), dependency awareness (§9 Dependency Awareness), cross-binary correlation (§9 Cross-Binary Correlation), byte patching (§8 Byte Patching), analysis database export (§10 Analysis Database Export).
    *   Should-have: Assembly-assisted patching (§8 Assembly-Assisted Patching), code cave discovery (§8 Code Cave Discovery), binary diff support (§9 Binary Diff Support), signature pack management (§10 Signature Pack Management), script generation (§8 Script Generation), batch operations (§1 Batch Operations), optional external enrichment (§2 Optional External Enrichment), HTTP transport auth and multi-tenancy (§11 MCP Transport), analysis database import (§10 Analysis Database Import), relocatable object file support (§2 Relocatable Object File Support), firmware and headerless binary support (§2 Firmware and Headerless Binary Support).

## Recommended Tooling for the Dockerfile
This section is non-normative implementation guidance — illustrative, not prescriptive. Alternatives that satisfy the same requirements are acceptable. To support these requirements, the Docker image will likely need:

*   **Frameworks:** Headless Ghidra via `analyzeHeadless` or a Python bridge, `radare2` or `rizin` with pipe bindings, and optionally `angr` for advanced static analysis.
*   **CLI Utilities:** `file`, `strings`, `xxd`, `binutils` such as `objdump`, `readelf`, and format-specific helpers.
*   **Python Libraries:** `lief`, `pefile`, `yara-python`, `ssdeep`, `capstone`, `keystone`, and related libraries for parsing and patch generation.
*   **Specialty Tools:** Detect It Easy console, FLOSS, signature packs, BinDiff or Diaphora for binary diffing, and format-specific extraction utilities.
*   **Debug Info Parsers:** `pyelftools` for DWARF, `pdbparse` or Microsoft symbol utilities for PDB, and related libraries for extracting debug metadata when available.
*   **State Storage:** A persistent project directory inside the shared volume or another mounted location for analysis databases, exported metadata, and cached artifacts.
