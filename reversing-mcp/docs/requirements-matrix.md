# Requirements Matrix

This matrix maps `requirements.md` references to the current implementation. Status values:

- `Implemented`: available now
- `Partial`: implemented with bounded or intentionally reduced scope
- `Deferred`: not implemented in this phase

## §0 Result Model And MCP Ergonomics

| Requirement | Status | Primary implementation |
| --- | --- | --- |
| Confidence and Evidence | Implemented | `src/reversing_mcp/result.py`, per-item analysis/signature payloads |
| Provenance | Implemented | `src/reversing_mcp/result.py`, extraction relationship metadata, session operation log |
| Pagination and Truncation Controls | Partial | paged collection tools in `app.py` and `analysis.py`; truncation metadata on decompilation, extraction, diff strings, exports, correlations |
| Error Taxonomy | Implemented | `src/reversing_mcp/errors.py`, normalized tool failures in `_respond()` |
| Stable Object Identity | Implemented | `src/reversing_mcp/store.py` provisional IDs and generation invalidation |
| Structured Result Schema | Implemented | shared success/failure envelope in `src/reversing_mcp/result.py` |
| Tool Descriptions and Naming | Implemented | `TOOL_CATALOG` in `src/reversing_mcp/app.py`, MCP wrapper docs in `src/reversing_mcp/server.py` |
| Tool Dependency Declaration | Implemented | `describe_tools`, `get_capabilities.tool_dependencies` |
| Capability Introspection | Implemented | `get_capabilities`, per-artifact mode/capability tools |
| Analysis Synopsis | Implemented | `get_analysis_synopsis`, enrichment in `src/reversing_mcp/app.py` |
| Suggested Next Actions | Implemented | `suggested_next_actions` across creation, analysis, extraction, patching, diff, export, synopsis |
| Asynchronous Job Model | Implemented | `src/reversing_mcp/jobs.py`, `get_job`, `list_jobs`, `cancel_job` |
| Deterministic Ordering and Filtering | Implemented | sorted collections and paged helpers in `store.py`, `analysis.py`, and `app.py` |

## §1 Operating Model And State Management

| Requirement | Status | Primary implementation |
| --- | --- | --- |
| Stateful Sessions | Implemented | session lifecycle in `src/reversing_mcp/store.py` |
| Persistent Analysis Context | Implemented | persisted session/artifact JSON plus cached analysis JSON |
| Token-Efficient References | Implemented | session/artifact/function/string IDs plus composite brief tools in `src/reversing_mcp/app.py` |
| Shared Volume Workflow | Implemented | `WorkspaceSecurity`, compose workspace mount, output helpers |
| Reproducible Outputs | Implemented | `export_session_state`, `export_curated_analysis`, command/report export |
| Provenance and Auditability | Implemented | relationships, operation log, result provenance |
| Error and Confidence Reporting | Implemented | result envelope and structured errors |
| Annotation History | Implemented | `put_annotation`, `get_annotation_history`, `revert_annotation` |
| Progress Reporting and Cancellation | Implemented | job progress payloads and cancel support |
| Batch Operations | Implemented | `batch_query_artifacts` |
| Architecture and Format Capability Reporting | Implemented | `get_capabilities`, `triage_artifact`, runtime policy report |
| Session Snapshots | Implemented | `create_session_snapshot`, `list_session_snapshots`, `restore_session_snapshot` |

## §2 File Identification And Metadata Analysis

| Requirement | Status | Primary implementation |
| --- | --- | --- |
| File Typing | Implemented | triage parser in `src/reversing_mcp/signatures.py` |
| Binary Taxonomy | Partial | heuristic taxonomy in triage output |
| Hashing | Implemented | triage hashes |
| Header Parsing | Implemented | triage header parsing |
| Segment and Section Discrepancy Analysis | Implemented | triage/header discrepancy reporting |
| Address Translation | Implemented | `translate_artifact_address` |
| String Extraction | Implemented | triage and `list_artifact_strings` |
| Security Mitigations Check | Implemented | triage mitigation summary |
| Certificate and Signature Analysis | Partial | build-id and signature-oriented metadata where available |
| Relocatable Object File Support | Partial | parser accepts limited object-file layouts |
| Firmware and Headerless Binary Support | Partial | hinted/raw triage support |
| Format-Specific Deep Inspection | Partial | ELF/PE/Mach-O focused metadata fields |
| Container and Child Artifact Mapping | Implemented | `list_artifact_children`, extraction relationships |
| Optional External Enrichment | Partial | opt-in hook is exposed but disabled by default |

## §3 Core Disassembly And Decompilation

| Requirement | Status | Primary implementation |
| --- | --- | --- |
| Symbol Extraction | Implemented | `list_artifact_symbols` |
| Symbol Demangling | Implemented | analysis backend demangling in `analysis.py` |
| Function Enumeration | Implemented | `list_artifact_functions` |
| Targeted Disassembly | Implemented | `disassemble_function`, `disassemble_range` |
| Targeted Decompilation | Implemented | `decompile_function` |
| Raw Byte Inspection | Implemented | `read_artifact_bytes` |
| Cross-References | Implemented | `list_artifact_xrefs` |
| Basic Program Search | Implemented | `search_artifact` |
| Relocation and Linkage Analysis | Implemented | `get_artifact_linkage` |
| Debug Information Parsing | Implemented | `get_artifact_debug_info` |

## §4 Control, Data Flow, And Semantic Recovery

| Requirement | Status | Primary implementation |
| --- | --- | --- |
| Call Graphs | Implemented | `get_call_graph` |
| Control Flow Graphs | Implemented | `get_control_flow_graph` |
| Variable Recovery | Implemented | `get_function_variables` |
| Stack Frame Layout | Implemented | `get_stack_frame` |
| Constant Propagation Queries | Implemented | `get_constant_propagation` |
| Type Information Query | Implemented | `get_type_information` |
| Automated Type Recovery | Implemented | `recover_types` |
| Data Segment Inspection | Implemented | `inspect_data_segments` |
| Indirect Flow Recovery | Implemented | `get_indirect_flows` |
| Exception and Unwind Metadata | Implemented | `get_exception_metadata` |
| Calling Convention Query | Implemented | `get_calling_convention` |
| Intermediate Representation Access | Implemented | `get_intermediate_representation` |
| Language Runtime Metadata Recovery | Implemented | `get_runtime_metadata` |
| Static Data-Flow Slicing | Implemented | `slice_data_flow` |
| System Call Identification | Implemented | `identify_system_calls` |

## §5 Search, Navigation, And Workflow Acceleration

| Requirement | Status | Primary implementation |
| --- | --- | --- |
| Recursive Navigation | Implemented | `navigate_neighborhood`, `trace_capability` |
| Neighborhood Queries | Implemented | `navigate_neighborhood`, `trace_capability` |
| Filtering and Prioritization | Implemented | `prioritize_functions`, `classify_functions`, `hunt_interesting_regions` |
| Triage Scoring | Implemented | function scoring in analysis backend and `prioritize_functions` |
| Function Classification Tags | Implemented | `classify_functions` |
| Bookmarks and Named Regions | Implemented | `save_workflow_item`, `list_workflow_items` |
| Analysis Notes | Implemented | workflow notes plus annotations |
| Curated Artifact Export | Implemented | `export_curated_analysis` |

## §6 Pattern Matching And Signature Analysis

| Requirement | Status | Primary implementation |
| --- | --- | --- |
| YARA Scanning | Implemented | `scan_with_yara` |
| Crypto Constant Detection | Implemented | `detect_crypto_constants` |
| Library Code Recognition | Implemented | `recognize_library_code` |
| Compiler and Toolchain Fingerprinting | Implemented | `fingerprint_compiler_toolchain` |

## §7 Obfuscation, Packing, And Artifact Extraction

| Requirement | Status | Primary implementation |
| --- | --- | --- |
| Packer Detection | Implemented | `detect_packer` |
| Entropy Calculation | Implemented | `calculate_entropy` |
| Static String Deobfuscation | Implemented | `deobfuscate_strings` |
| Resource Extraction | Implemented | `extract_resources` |
| General Embedded Artifact Carving | Implemented | `carve_embedded_artifacts` |
| Recursive Analysis Handoff | Implemented | recursive extraction plus optional attach/analyze |
| Artifact Relationship Tracking | Implemented | parent/child relationships in store and extraction outputs |

## §8 Binary Modification And Output Generation

| Requirement | Status | Primary implementation |
| --- | --- | --- |
| Byte Patching | Implemented | `patch_artifact_bytes` |
| Assembly-Assisted Patching | Implemented | `patch_artifact_assembly`, mini assembler in `feature07.py` |
| Code Cave Discovery | Implemented | `find_code_caves` |
| Naming and Type Edits | Implemented | `edit_artifact_metadata` |
| Calling Convention Override | Implemented | `edit_artifact_metadata(edit_kind=calling_convention)` |
| Type and Header Import | Implemented | `import_type_definitions` |
| Script Generation | Partial | command-log export rather than full backend scripting |
| Structured Report Export | Implemented | `export_analysis_report` |

## §9 Multi-Artifact And Cross-Binary Analysis

| Requirement | Status | Primary implementation |
| --- | --- | --- |
| Multi-Binary Sessions | Implemented | session artifact model in `store.py` |
| Dependency Awareness | Implemented | `list_artifact_dependencies` |
| Cross-Binary Correlation | Implemented | `correlate_session_artifacts` |
| Binary Diff Support | Partial | structural plus recovered-object diff in `diff_artifacts`; semantic diff deferred |

## §10 Analysis Import And Export Interoperability

| Requirement | Status | Primary implementation |
| --- | --- | --- |
| Analysis Database Import | Deferred | not implemented in this phase |
| Analysis Database Export | Partial | JSON/session/curated/report exports exist; BinExport/SARIF deferred |
| Signature Pack Management | Deferred | not implemented in this phase |

## §11 Container, Security, And Operational Requirements

| Requirement | Status | Primary implementation |
| --- | --- | --- |
| Shared Workspace Mount | Implemented | `docker-compose.yml`, `WorkspaceSecurity` |
| Offline-Friendly Operation | Implemented | no required network dependency in core workflows; enrichment remains opt-in and disabled |
| Sample Containment | Implemented | no target execution, sanitized identifiers and output paths |
| Parser Crash Resilience | Implemented | parser sandbox subprocess and normalized failures |
| Filename Sanitization | Implemented | `WorkspaceSecurity.sanitize_filename()` |
| Symlink Protection | Implemented | workspace path resolution and output validation |
| Input Size Limits | Implemented | resource limits in `WorkspaceSecurity` |
| Deterministic Tooling | Partial | pinned image/dependency flow for the shipped compose image |
| Timeout and Resource Controls | Implemented | parser and extraction budgets plus truncation controls |
| MCP Transport | Implemented | stdio and streamable HTTP in `server.py`, auth/quota/isolation in `transport.py`, `store.py`, and `jobs.py` |

## Explicit Deferrals

The following requirements are intentionally deferred beyond the current feature set:

- §9 Binary Diff Support semantic diffing
- §10 Analysis Database Import
- §10 Signature Pack Management
- Portable BinExport/SARIF/native project export beyond the current JSON-focused exports
