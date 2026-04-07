from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .analysis import analysis_backend_status
from .ghidra import ghidra_available, ghidra_decompile_function, ghidra_export_analysis, ghidra_run_custom_script
from .config import SERVER_NAME, SERVER_VERSION
from .errors import StructuredToolError
from .feature07 import (
    apply_patch_bytes,
    assemble_patch,
    build_analysis_report,
    build_patch_report,
    correlate_artifacts,
    decode_patch_bytes,
    diff_artifacts,
    discover_code_caves,
    import_type_definitions as import_type_definitions_report,
    list_dependencies,
    parse_artifact_context as parse_feature07_context,
    render_command_log,
)
from .feature09 import (
    compact_code_cave,
    compact_function,
    compact_instruction,
    compact_page,
    compact_string,
    compact_symbol,
    compact_xref,
    normalize_brief_options,
    profile_summary,
    truncate_text,
)
from .jobs import JobManager
from .result import failure, success
from .semantic import filter_and_prioritize_functions, navigate_function_neighborhood, slice_function_data_flow
from .signatures import (
    calculate_entropy as calculate_entropy_report,
    deobfuscate_strings as deobfuscate_strings_report,
    detect_crypto_constants as detect_crypto_constants_report,
    detect_overlay,
    detect_packer as detect_packer_report,
    extract_archive_members,
    extract_pe_resources,
    fingerprint_compiler_toolchain,
    materialize_output_file,
    parse_artifact_context,
    recognize_library_code as recognize_library_code_report,
    run_yara_scan,
)
from .security import ParserSandbox, WorkspaceSecurity
from .store import SessionStore
from .transport import load_http_transport_config
from .utils import json_clone

TOOL_CATALOG = [
    {
        "name": "describe_tools",
        "description": "List available foundation tools, parameter summaries, and explicit prerequisites.",
        "prerequisites": [],
        "parameters": [],
    },
    {
        "name": "get_capabilities",
        "description": "Report server, transport, ID-model, and backend capabilities for this container.",
        "prerequisites": [],
        "parameters": [],
    },
    {
        "name": "get_runtime_policies",
        "description": "Report workspace hardening settings, parser isolation policy, resource limits, and version information.",
        "prerequisites": [],
        "parameters": [],
    },
    {
        "name": "run_parser_probe",
        "description": "Run a file probe in the isolated parser subprocess to validate crash containment and path policy.",
        "prerequisites": [],
        "parameters": ["path", "simulate?"],
    },
    {
        "name": "create_session",
        "description": "Create a persisted single-agent analysis session rooted in the shared workspace volume.",
        "prerequisites": [],
        "parameters": ["name", "description?", "settings?"],
    },
    {
        "name": "load_session",
        "description": "Load one persisted analysis session by session_id or unique session name.",
        "prerequisites": [],
        "parameters": ["session_id?", "name?"],
    },
    {
        "name": "list_sessions",
        "description": "List persisted sessions with deterministic ordering and cursor pagination.",
        "prerequisites": [],
        "parameters": ["cursor?", "limit?"],
    },
    {
        "name": "destroy_session",
        "description": "Delete persisted analysis state for one session without deleting workspace files.",
        "prerequisites": [],
        "parameters": ["session_id?", "name?"],
    },
    {
        "name": "update_session_settings",
        "description": "Persist analysis-setting changes for an existing session.",
        "prerequisites": ["create_session"],
        "parameters": ["session_id", "settings_patch"],
    },
    {
        "name": "add_artifact",
        "description": "Attach one workspace file to a session and assign a stable artifact_id.",
        "prerequisites": ["create_session"],
        "parameters": ["session_id", "path", "display_name?"],
    },
    {
        "name": "list_artifacts",
        "description": "List artifacts currently attached to a session.",
        "prerequisites": ["create_session"],
        "parameters": ["session_id", "cursor?", "limit?"],
    },
    {
        "name": "remove_artifact",
        "description": "Remove one artifact from the session and invalidate its object mappings.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id?", "display_name?"],
    },
    {
        "name": "triage_artifact",
        "description": "Identify a loaded artifact, summarize its headers and layout, preview strings, and report cheap static metadata.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "hints?", "string_preview_limit?"],
    },
    {
        "name": "list_artifact_strings",
        "description": "List extracted strings for an artifact with pagination and optional filters.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "cursor?", "limit?", "min_length?", "encoding?", "query?", "hints?"],
    },
    {
        "name": "translate_artifact_address",
        "description": "Translate one file offset, virtual address, or RVA within a loaded artifact.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "input_kind", "value", "hints?"],
    },
    {
        "name": "list_artifact_children",
        "description": "List child artifacts for container formats such as archives or fat binaries.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "cursor?", "limit?", "hints?"],
    },
    {
        "name": "lookup_external_enrichment",
        "description": "Query the opt-in external enrichment hook state for an artifact without requiring it for normal triage.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "providers?", "opt_in?"],
    },
    {
        "name": "scan_with_yara",
        "description": "Scan one artifact and optionally derived children with YARA or the built-in heuristic fallback.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "rules_text?", "include_related?"],
    },
    {
        "name": "fingerprint_compiler_toolchain",
        "description": "Return structured compiler and toolchain fingerprints for one artifact.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id"],
    },
    {
        "name": "detect_packer",
        "description": "Return signature-based and heuristic packer detections, including suspicious overlays and high-entropy sections.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id"],
    },
    {
        "name": "calculate_entropy",
        "description": "Calculate entropy for the whole file and each parsed section of an artifact.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id"],
    },
    {
        "name": "deobfuscate_strings",
        "description": "Return bounded static string deobfuscation candidates with explicit decode methods.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "limit?"],
    },
    {
        "name": "extract_resources",
        "description": "Extract PE resources or archive-like container members into the workspace with provenance-preserving metadata.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "output_subdir?", "attach_to_session?", "target_session_id?", "analyze_extracted?"],
    },
    {
        "name": "carve_embedded_artifacts",
        "description": "Carve appended overlays and embedded child artifacts into the workspace with optional recursive session handoff.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "output_subdir?", "attach_to_session?", "target_session_id?", "analyze_extracted?", "recurse?"],
    },
    {
        "name": "get_artifact_relationships",
        "description": "Return parent and child artifact relationships created through extraction and carving workflows.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "direction?"],
    },
    {
        "name": "start_artifact_analysis",
        "description": "Start asynchronous headless program analysis for one loaded artifact and persist the recovered function and symbol cache.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "hints?"],
    },
    {
        "name": "get_analysis_synopsis",
        "description": "Return a compact persisted synopsis of the current analysis state for one artifact.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id"],
    },
    {
        "name": "list_artifact_symbols",
        "description": "List recovered imports, exports, thunks, and unresolved symbols with demangled-name filtering and pagination.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "cursor?", "limit?", "query?", "kind?"],
    },
    {
        "name": "list_artifact_functions",
        "description": "List recovered functions with addresses, signatures, calling conventions, stack sizes, and analyzer confidence.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "cursor?", "limit?", "query?"],
    },
    {
        "name": "get_artifact_instruction_mode",
        "description": "Report the supported and active instruction-set mode for the analyzed artifact.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id"],
    },
    {
        "name": "set_artifact_instruction_mode",
        "description": "Override the active instruction-set mode when the analyzed architecture supports multiple modes.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "mode"],
    },
    {
        "name": "disassemble_function",
        "description": "Retrieve structured disassembly for one recovered function with bytes, addresses, symbolic operand hints, and pagination.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "function_id?", "name?", "address?", "cursor?", "limit?", "instruction_mode_override?"],
    },
    {
        "name": "disassemble_range",
        "description": "Retrieve structured disassembly for an address or file-backed byte range within an analyzed artifact.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "input_kind", "start_value", "size", "cursor?", "limit?", "instruction_mode_override?"],
    },
    {
        "name": "decompile_function",
        "description": "Retrieve best-effort pseudo-C for one recovered function with explicit failure and truncation metadata.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "function_id?", "name?", "address?", "char_limit?", "line_limit?"],
    },
    {
        "name": "read_artifact_bytes",
        "description": "Inspect raw bytes by file offset or virtual address with hex and ASCII views.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "input_kind", "value", "length", "hints?"],
    },
    {
        "name": "list_artifact_xrefs",
        "description": "List callers and other cross-references targeting a recovered function, string, or address.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "function_id?", "string_id?", "address?", "cursor?", "limit?"],
    },
    {
        "name": "search_artifact",
        "description": "Search an analyzed artifact by names, strings, immediates, opcodes, byte patterns, or address ranges.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "kind", "query?", "start_address?", "end_address?", "cursor?", "limit?", "case_sensitive?"],
    },
    {
        "name": "get_artifact_linkage",
        "description": "Return relocations and linkage metadata such as PLT, GOT, IAT, thunks, and unresolved bindings.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id"],
    },
    {
        "name": "get_artifact_debug_info",
        "description": "Return parsed DWARF, PDB-derived, or embedded source-reference metadata when available.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id"],
    },
    {
        "name": "detect_crypto_constants",
        "description": "Return recovered crypto and checksum constants with per-hit evidence and confidence.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id"],
    },
    {
        "name": "recognize_library_code",
        "description": "Return recognized runtime and library code using imports, symbols, and function metadata.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id"],
    },
    {
        "name": "get_call_graph",
        "description": "Return bounded incoming and outgoing call-graph edges for one recovered function.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "function_id?", "name?", "address?", "direction?", "depth?", "limit_nodes?", "limit_edges?"],
    },
    {
        "name": "get_control_flow_graph",
        "description": "Return the recovered control-flow graph for one function with blocks, branch targets, loops, and fallthrough edges.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "function_id?", "name?", "address?"],
    },
    {
        "name": "get_function_variables",
        "description": "Return recovered arguments, locals, globals, and register parameters for one function.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "function_id?", "name?", "address?"],
    },
    {
        "name": "get_stack_frame",
        "description": "Return the recovered stack-frame layout for one function.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "function_id?", "name?", "address?"],
    },
    {
        "name": "get_constant_propagation",
        "description": "Return recovered immediates and bounded call-site argument propagation for one function.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "function_id?", "name?", "address?"],
    },
    {
        "name": "get_type_information",
        "description": "Return recovered type summaries, function signatures, and typed-memory views for an artifact.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id"],
    },
    {
        "name": "recover_types",
        "description": "Return heuristic RTTI, vtable, class-hierarchy, and typed-memory recoveries with confidence and evidence.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id"],
    },
    {
        "name": "inspect_data_segments",
        "description": "Inspect non-executable data regions for strings, pointer tables, arrays, and typed views.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id"],
    },
    {
        "name": "get_indirect_flows",
        "description": "Return recovered indirect calls, branches, jump-table hints, and unresolved control-flow transfers for one function.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "function_id?", "name?", "address?"],
    },
    {
        "name": "get_exception_metadata",
        "description": "Return recovered exception, unwind, and personality metadata for an artifact.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id"],
    },
    {
        "name": "get_calling_convention",
        "description": "Return the detected or inferred calling convention for one function.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "function_id?", "name?", "address?"],
    },
    {
        "name": "get_intermediate_representation",
        "description": "Return a bounded backend IR view for one function.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "function_id?", "name?", "address?", "limit_blocks?", "limit_statements?"],
    },
    {
        "name": "get_runtime_metadata",
        "description": "Return recovered C++, Go, Objective-C, Swift, Rust, or generic runtime metadata for an artifact.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id"],
    },
    {
        "name": "slice_data_flow",
        "description": "Return a bounded heuristic data-flow slice around an instruction or register use inside one function.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "function_id?", "name?", "address?", "anchor_address?", "register?", "radius?"],
    },
    {
        "name": "identify_system_calls",
        "description": "Return recovered raw system-call instructions and bounded syscall-number inferences for one function.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "function_id?", "name?", "address?"],
    },
    {
        "name": "navigate_neighborhood",
        "description": "Return callers, callees, nearby functions, and nearby strings around one target function.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "function_id?", "name?", "address?", "depth?", "radius?"],
    },
    {
        "name": "prioritize_functions",
        "description": "Return triaged and optionally filtered functions with explicit scores, thresholds, and evidence.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "include_tags?", "exclude_tags?", "min_score?", "limit?"],
    },
    {
        "name": "classify_functions",
        "description": "Return heuristic function-classification tags with filtering support.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "include_tags?", "exclude_tags?", "limit?"],
    },
    {
        "name": "save_workflow_item",
        "description": "Save a bookmark, named region, or analysis note using the persisted annotation history model.",
        "prerequisites": ["create_session"],
        "parameters": ["session_id", "kind", "target", "value", "annotation_id?"],
    },
    {
        "name": "list_workflow_items",
        "description": "List saved bookmarks, named regions, and notes for a session.",
        "prerequisites": ["save_workflow_item"],
        "parameters": ["session_id", "kind?", "artifact_id?", "cursor?", "limit?"],
    },
    {
        "name": "export_curated_analysis",
        "description": "Export a curated subset of functions, strings, xrefs, and workflow items for one artifact.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "function_ids?", "string_ids?", "annotation_ids?", "output_path?"],
    },
    {
        "name": "batch_query_artifacts",
        "description": "Run one eligible semantic query across every artifact in a session in a single call.",
        "prerequisites": ["create_session"],
        "parameters": ["session_id", "operation", "include_tags?", "exclude_tags?", "min_score?", "limit?"],
    },
    {
        "name": "register_provisional_function",
        "description": "Create a provisional function handle so later calls can reference it by stable ID before a real backend exists.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "name", "address?"],
    },
    {
        "name": "register_provisional_string",
        "description": "Create a provisional string handle so later calls can reference it by stable ID before a real backend exists.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "value", "address?"],
    },
    {
        "name": "get_object_reference",
        "description": "Resolve a provisional function or string object ID and fail with invalid_id if it expired.",
        "prerequisites": ["register_provisional_function or register_provisional_string"],
        "parameters": ["session_id", "object_id"],
    },
    {
        "name": "put_annotation",
        "description": "Create or update one annotation with full per-annotation revision history.",
        "prerequisites": ["create_session"],
        "parameters": ["session_id", "target", "annotation_type", "value", "annotation_id?"],
    },
    {
        "name": "list_annotations",
        "description": "List annotations with deterministic ordering and filters.",
        "prerequisites": ["put_annotation"],
        "parameters": ["session_id", "artifact_id?", "target_kind?", "annotation_type?", "cursor?", "limit?"],
    },
    {
        "name": "get_annotation_history",
        "description": "Return the full revision history for one annotation.",
        "prerequisites": ["put_annotation"],
        "parameters": ["session_id", "annotation_id"],
    },
    {
        "name": "revert_annotation",
        "description": "Revert one annotation to a prior revision without affecting other annotations.",
        "prerequisites": ["put_annotation"],
        "parameters": ["session_id", "annotation_id", "revision_id?"],
    },
    {
        "name": "create_session_snapshot",
        "description": "Capture a named whole-session snapshot that can later be restored.",
        "prerequisites": ["create_session"],
        "parameters": ["session_id", "name", "description?"],
    },
    {
        "name": "list_session_snapshots",
        "description": "List named whole-session snapshots for one session.",
        "prerequisites": ["create_session_snapshot"],
        "parameters": ["session_id"],
    },
    {
        "name": "restore_session_snapshot",
        "description": "Restore a named or ID-addressed whole-session snapshot in place.",
        "prerequisites": ["create_session_snapshot"],
        "parameters": ["session_id", "snapshot_id?", "name?"],
    },
    {
        "name": "start_artifact_reanalysis",
        "description": "Start an asynchronous artifact re-analysis job that invalidates provisional object IDs on completion.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id"],
    },
    {
        "name": "get_job",
        "description": "Read one job handle, including progress, partial results, and terminal status.",
        "prerequisites": ["start_artifact_reanalysis"],
        "parameters": ["job_id"],
    },
    {
        "name": "list_jobs",
        "description": "List job handles with deterministic ordering and optional filters.",
        "prerequisites": [],
        "parameters": ["session_id?", "status?", "cursor?", "limit?"],
    },
    {
        "name": "cancel_job",
        "description": "Request cancellation for a running asynchronous job.",
        "prerequisites": ["start_artifact_reanalysis"],
        "parameters": ["job_id"],
    },
    {
        "name": "export_session_state",
        "description": "Export machine-readable session state inline or to a workspace file.",
        "prerequisites": ["create_session"],
        "parameters": ["session_id", "output_path?"],
    },
    {
        "name": "patch_artifact_bytes",
        "description": "Apply a byte patch by file offset, virtual address, or RVA and materialize a patched artifact in the workspace.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "input_kind", "value", "bytes_hex", "output_path?", "attach_to_session?", "display_name?"],
    },
    {
        "name": "patch_artifact_assembly",
        "description": "Assemble and apply a patch for a supported ISA using a compact built-in backend for x86, x86_64, aarch64, arm, and thumb.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "input_kind", "value", "assembly", "isa", "output_path?", "attach_to_session?", "display_name?"],
    },
    {
        "name": "find_code_caves",
        "description": "Discover likely code caves and other contiguous zero-filled slack regions inside mapped sections.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "min_size?"],
    },
    {
        "name": "edit_artifact_metadata",
        "description": "Persist naming, type, variable, global, struct, enum, typedef, or calling-convention overrides for an artifact.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "edit_kind", "target", "value"],
    },
    {
        "name": "import_type_definitions",
        "description": "Import C-header or structured type definitions into artifact-local analysis overrides with explicit loss reporting.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "source_format", "source_text"],
    },
    {
        "name": "export_command_log",
        "description": "Export the persisted command log for supported Feature 07 actions in JSON or text form.",
        "prerequisites": ["create_session"],
        "parameters": ["session_id", "format?", "output_path?"],
    },
    {
        "name": "export_analysis_report",
        "description": "Export a structured report for one artifact in compact JSON or human-readable text form.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "format?", "output_path?"],
    },
    {
        "name": "list_artifact_dependencies",
        "description": "Report dependency hints across imports, linkage metadata, and parent or child artifact relationships.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id"],
    },
    {
        "name": "correlate_session_artifacts",
        "description": "Correlate multiple artifacts in one session by shared imports, strings, and recovered function names with pagination.",
        "prerequisites": ["create_session"],
        "parameters": ["session_id", "artifact_ids?", "cursor?", "limit?"],
    },
    {
        "name": "diff_artifacts",
        "description": "Compare two artifacts structurally and, when analysis exists, by recovered functions and strings.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "left_artifact_id", "right_artifact_id"],
    },
    {
        "name": "ingest_and_triage_artifact",
        "description": "Attach an artifact, triage it, and optionally queue analysis in one bounded call.",
        "prerequisites": ["create_session"],
        "parameters": [
            "session_id",
            "path",
            "display_name?",
            "hints?",
            "analyze?",
            "verbosity?",
            "token_budget_hint?",
            "include_next_actions?",
            "include_raw_sections?",
        ],
    },
    {
        "name": "analyze_and_summarize",
        "description": "Start analysis when needed, optionally wait for completion, and return a compact artifact brief tuned by focus.",
        "prerequisites": ["add_artifact"],
        "parameters": [
            "session_id",
            "artifact_id",
            "focus?",
            "wait_timeout_seconds?",
            "verbosity?",
            "token_budget_hint?",
            "include_next_actions?",
            "include_raw_sections?",
        ],
    },
    {
        "name": "hunt_interesting_regions",
        "description": "Combine prioritized functions, suspicious strings, imports, and static heuristics into a ranked shortlist.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": [
            "session_id",
            "artifact_id",
            "objective?",
            "limit?",
            "verbosity?",
            "token_budget_hint?",
            "include_next_actions?",
            "include_raw_sections?",
        ],
    },
    {
        "name": "trace_capability",
        "description": "Expand one function target into neighborhood, xrefs, variables, and bounded instruction context.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": [
            "session_id",
            "artifact_id",
            "target",
            "depth?",
            "verbosity?",
            "token_budget_hint?",
            "include_next_actions?",
            "include_raw_sections?",
        ],
    },
    {
        "name": "prepare_patch_plan",
        "description": "Bundle patchability context, code caves, instruction-mode state, and candidate patch points in one brief.",
        "prerequisites": ["add_artifact"],
        "parameters": [
            "session_id",
            "artifact_id",
            "objective",
            "target?",
            "min_code_cave_size?",
            "verbosity?",
            "token_budget_hint?",
            "include_next_actions?",
            "include_raw_sections?",
        ],
    },
    {
        "name": "artifact_relationship_brief",
        "description": "Summarize artifact relationships, dependency hints, correlation hits, and likely comparison partners.",
        "prerequisites": ["add_artifact"],
        "parameters": [
            "session_id",
            "artifact_id",
            "focus?",
            "verbosity?",
            "token_budget_hint?",
            "include_next_actions?",
            "include_raw_sections?",
        ],
    },
    {
        "name": "ghidra_decompile",
        "description": "Decompile a function using the Ghidra headless decompiler (higher quality than angr for complex binaries).",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "address", "timeout_seconds?"],
    },
    {
        "name": "ghidra_analyze",
        "description": "Run full Ghidra headless analysis on an artifact and export functions, strings, imports, and sections.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "timeout_seconds?"],
    },
    {
        "name": "run_ghidra_script",
        "description": "Run a custom Ghidra Python script against an artifact binary. Script runs in Ghidra's Jython environment with full API access.",
        "prerequisites": ["add_artifact"],
        "parameters": ["session_id", "artifact_id", "script", "timeout_seconds?"],
    },
    {
        "name": "export_dynamic_manifest",
        "description": "Export a JSON manifest of functions, strings, imports, and addresses for use by a dynamic analysis tool (pwn-mcp). Writes to the shared workspace volume.",
        "prerequisites": ["start_artifact_analysis"],
        "parameters": ["session_id", "artifact_id", "output_path?"],
    },
]


def _available_analysis_backends() -> list[dict[str, Any]]:
    backends = [
        {
            "name": "session-core",
            "available": True,
            "exact": True,
            "notes": "Foundation-only session and object lifecycle backend.",
        },
        {
            "name": "triage-worker",
            "available": True,
            "exact": False,
            "notes": "Cheap static metadata extraction backend for format identification, headers, strings, and container mapping.",
        },
        analysis_backend_status(),
    ]
    ghidra_headless = Path("/opt/ghidra/support/analyzeHeadless")
    if ghidra_headless.exists():
        backends.append(
            {
                "name": "ghidra-headless",
                "available": True,
                "exact": False,
                "notes": "Installed headless Ghidra backend available in the container for future analysis and scripting workflows.",
                "path": str(ghidra_headless),
            }
        )
    return backends


class ReversingMCPApp:
    def __init__(self, workspace_root: Path | None = None) -> None:
        self.security = WorkspaceSecurity(workspace_root=workspace_root)
        self.store = SessionStore(workspace_root=self.security.workspace_root, security=self.security)
        self.parser_sandbox = ParserSandbox(self.security)
        self.jobs = JobManager(self.store, self.parser_sandbox)

    def describe_tools(self) -> dict[str, Any]:
        return self._respond(
            "describe_tools",
            {},
            lambda: {
                "tools": json_clone(TOOL_CATALOG),
            },
        )

    def get_capabilities(self) -> dict[str, Any]:
        return self._respond(
            "get_capabilities",
            {},
            self._capability_payload,
        )

    def get_runtime_policies(self) -> dict[str, Any]:
        return self._respond("get_runtime_policies", {}, lambda: self.security.runtime_policy_report())

    def _capability_payload(self) -> dict[str, Any]:
        backend_available = analysis_backend_status()["available"]
        transport_config = load_http_transport_config()
        return {
            "server": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "workspace_root": str(self.store.workspace_root),
            "state_root": str(self.store.state_root),
            "transports": {
                "stdio": {"enabled": True},
                "sse": {"enabled": True, "paths": ["/sse", "/messages"]},
                "streamable_http": {
                    "enabled": True,
                    "path": "/mcp",
                    "authentication": {
                        "supported": True,
                        "enabled": transport_config.auth_enabled,
                        "required": transport_config.require_auth,
                        "agent_header": transport_config.agent_header,
                    },
                    "quotas": {
                        "requests_per_minute_per_agent": transport_config.requests_per_minute_per_agent,
                        "max_sessions_per_tenant": transport_config.max_sessions_per_tenant,
                        "max_active_jobs_per_tenant": transport_config.max_active_jobs_per_tenant,
                    },
                    "session_isolation": {
                        "tenant_isolation": True,
                        "single_agent_per_session": True,
                    },
                },
            },
            "features": {
                "session_persistence": True,
                "annotation_history": True,
                "session_snapshots": True,
                "async_jobs": True,
                "provisional_object_ids": True,
                "workspace_hardening": True,
                "parser_isolation": True,
                "file_metadata_triage": True,
                "program_analysis": backend_available,
                "function_enumeration": backend_available,
                "symbol_extraction": backend_available,
                "disassembly": backend_available,
                "decompilation": backend_available,
                "cross_references": backend_available,
                "raw_byte_inspection": True,
                "program_search": backend_available,
                "call_graphs": backend_available,
                "control_flow_graphs": backend_available,
                "variable_recovery": backend_available,
                "stack_frames": backend_available,
                "constant_propagation": backend_available,
                "type_information": backend_available,
                "runtime_metadata": backend_available,
                "triage_scoring": backend_available,
                "workflow_items": True,
                "curated_exports": True,
                "batch_queries": True,
                "yara_scanning": True,
                "crypto_constant_detection": backend_available,
                "library_recognition": backend_available,
                "compiler_fingerprinting": True,
                "packer_detection": True,
                "entropy_analysis": True,
                "string_deobfuscation": True,
                "resource_extraction": True,
                "artifact_carving": True,
                "recursive_analysis_handoff": True,
                "artifact_relationship_tracking": True,
                "byte_patching": True,
                "assembly_patching": True,
                "code_cave_discovery": True,
                "analysis_overrides": True,
                "type_import": True,
                "command_log_export": True,
                "analysis_report_export": True,
                "dependency_tracking": True,
                "cross_artifact_correlation": True,
                "artifact_diffing": True,
                "composite_brief_workflows": True,
                "response_budget_controls": True,
                "external_enrichment": False,
                "http_authentication": True,
                "tenant_isolation": True,
                "request_rate_limiting": True,
            },
            "id_formats": {
                "session_id": "sess_<hex>",
                "artifact_id": "art_<session-token>_<counter>",
                "function_id": "fn_<artifact_id>_g<analysis-generation>_<counter>",
                "string_id": "str_<artifact_id>_g<analysis-generation>_<counter>",
            },
            "tool_dependencies": {item["name"]: item["prerequisites"] for item in TOOL_CATALOG},
            "analysis_backends": _available_analysis_backends(),
            "patching": {
                "supported_isas": ["x86", "x86_64", "aarch64", "arm", "thumb"],
                "assembly_backend": {
                    "name": "builtin-mini-assembler",
                    "supported_instructions": [
                        "nop",
                        "ret",
                        "int3",
                        "brk <imm16>",
                        "bkpt <imm16|imm8>",
                        "jmp <address>",
                        "call <address>",
                        "b <address>",
                        "bl <address>",
                    ],
                    "per_isa_examples": {
                        "x86": ["nop", "ret", "int3", "jmp 0x401000", "call 0x401050"],
                        "x86_64": ["nop", "ret", "int3", "jmp 0x401000", "call 0x401050"],
                        "aarch64": ["nop", "ret", "brk 0", "b 0x401000", "bl 0x401050"],
                        "arm": ["nop", "ret", "bkpt 0", "b 0x401000", "bl 0x401050"],
                        "thumb": ["nop", "ret", "bkpt 0", "b 0x401000"],
                    },
                },
            },
            "composite_workflows": {
                "supported_tools": [
                    "ingest_and_triage_artifact",
                    "analyze_and_summarize",
                    "hunt_interesting_regions",
                    "trace_capability",
                    "prepare_patch_plan",
                    "artifact_relationship_brief",
                ],
                "response_budget": {
                    "verbosity": ["brief", "normal", "deep"],
                    "supports_token_budget_hint": True,
                    "supports_raw_section_opt_in": True,
                },
                "focus_presets": ["general", "malware", "patching", "diffing", "firmware", "extraction"],
            },
            "runtime_policy": self.security.runtime_policy_report(),
        }

    def run_parser_probe(self, path: str, simulate: str | None = None) -> dict[str, Any]:
        parameters = {"path": path, "simulate": simulate}
        return self._respond("run_parser_probe", parameters, lambda: self.parser_sandbox.run_probe(path, simulate=simulate))

    def create_session(self, name: str, description: str | None = None, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        parameters = {"name": name, "description": description, "settings": settings}
        return self._respond(
            "create_session",
            parameters,
            lambda: self.store.create_session(name=name, description=description, settings=settings),
            suggested_next_actions=lambda result: [
                {
                    "tool": "add_artifact",
                    "parameters": {"session_id": result["session"]["session_id"]},
                    "rationale": "Attach a binary so later analysis and annotations have stable artifact IDs.",
                }
            ],
        )

    def load_session(self, session_id: str | None = None, name: str | None = None) -> dict[str, Any]:
        parameters = {"session_id": session_id, "name": name}
        return self._respond("load_session", parameters, lambda: self.store.load_session(session_id=session_id, name=name))

    def list_sessions(self, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
        parameters = {"cursor": cursor, "limit": limit}
        return self._respond("list_sessions", parameters, lambda: self.store.list_sessions(cursor=cursor, limit=limit))

    def destroy_session(self, session_id: str | None = None, name: str | None = None) -> dict[str, Any]:
        parameters = {"session_id": session_id, "name": name}
        return self._respond("destroy_session", parameters, lambda: self.store.destroy_session(session_id=session_id, name=name))

    def update_session_settings(self, session_id: str, settings_patch: dict[str, Any]) -> dict[str, Any]:
        parameters = {"session_id": session_id, "settings_patch": settings_patch}
        return self._respond("update_session_settings", parameters, lambda: self.store.update_settings(session_id, settings_patch))

    def add_artifact(self, session_id: str, path: str, display_name: str | None = None) -> dict[str, Any]:
        parameters = {"session_id": session_id, "path": path, "display_name": display_name}
        return self._respond(
            "add_artifact",
            parameters,
            lambda: self.store.add_artifact(session_id=session_id, path=path, display_name=display_name),
            suggested_next_actions=lambda result: [
                {
                    "tool": "triage_artifact",
                    "parameters": {"session_id": session_id, "artifact_id": result["artifact_id"]},
                    "rationale": "Cheaply identify the file and capture layout, strings, and mitigation metadata before deeper analysis.",
                },
                {
                    "tool": "start_artifact_reanalysis",
                    "parameters": {"session_id": session_id, "artifact_id": result["artifact_id"]},
                    "rationale": "Exercise the async job model and reset provisional object mappings before deeper analysis arrives.",
                }
            ],
        )

    def triage_artifact(
        self,
        session_id: str,
        artifact_id: str,
        hints: dict[str, Any] | None = None,
        string_preview_limit: int = 20,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "hints": hints,
            "string_preview_limit": string_preview_limit,
        }

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            triage = self.parser_sandbox.triage_artifact(
                artifact["canonical_path"],
                hints=hints,
                string_preview_limit=string_preview_limit,
            )
            return {
                "artifact": self._artifact_reference(artifact),
                **triage["result"],
            }

        return self._respond(
            "triage_artifact",
            parameters,
            operation,
            suggested_next_actions=lambda result: [
                {
                    "tool": "list_artifact_strings",
                    "parameters": {"session_id": session_id, "artifact_id": artifact_id},
                    "rationale": "Page through the full string table when the preview is insufficient.",
                },
                {
                    "tool": "translate_artifact_address",
                    "parameters": {"session_id": session_id, "artifact_id": artifact_id, "input_kind": "file_offset", "value": 0},
                    "rationale": "Translate offsets or virtual addresses once the layout is known.",
                },
            ],
        )

    def list_artifact_strings(
        self,
        session_id: str,
        artifact_id: str,
        cursor: int = 0,
        limit: int = 50,
        min_length: int = 4,
        encoding: str | None = None,
        query: str | None = None,
        hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "cursor": cursor,
            "limit": limit,
            "min_length": min_length,
            "encoding": encoding,
            "query": query,
            "hints": hints,
        }

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            strings = self.parser_sandbox.list_strings(
                artifact["canonical_path"],
                hints=hints,
                cursor=cursor,
                limit=limit,
                min_length=min_length,
                encoding=encoding,
                query=query,
            )
            return {
                "artifact": self._artifact_reference(artifact),
                **strings["result"],
            }

        return self._respond("list_artifact_strings", parameters, operation)

    def translate_artifact_address(
        self,
        session_id: str,
        artifact_id: str,
        input_kind: str,
        value: int | str,
        hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "input_kind": input_kind,
            "value": value,
            "hints": hints,
        }

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            translated = self.parser_sandbox.translate_address(
                artifact["canonical_path"],
                input_kind=input_kind,
                value=self._normalize_numeric_value(value),
                hints=hints,
            )
            return {
                "artifact": self._artifact_reference(artifact),
                **translated["result"],
            }

        return self._respond("translate_artifact_address", parameters, operation)

    def list_artifact_children(
        self,
        session_id: str,
        artifact_id: str,
        cursor: int = 0,
        limit: int = 50,
        hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "cursor": cursor,
            "limit": limit,
            "hints": hints,
        }

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            children = self.parser_sandbox.list_child_artifacts(
                artifact["canonical_path"],
                hints=hints,
                cursor=cursor,
                limit=limit,
            )
            return {
                "artifact": self._artifact_reference(artifact),
                **children["result"],
            }

        return self._respond("list_artifact_children", parameters, operation)

    def lookup_external_enrichment(
        self,
        session_id: str,
        artifact_id: str,
        providers: list[str] | None = None,
        opt_in: bool = False,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "providers": providers,
            "opt_in": opt_in,
        }

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            enrichment = self.parser_sandbox.lookup_external_enrichment(
                artifact["canonical_path"],
                providers=providers,
                opt_in=opt_in,
            )
            return {
                "artifact": self._artifact_reference(artifact),
                **enrichment["result"],
            }

        return self._respond("lookup_external_enrichment", parameters, operation)

    def scan_with_yara(
        self,
        session_id: str,
        artifact_id: str,
        rules_text: str | None = None,
        include_related: bool = False,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "rules_text": rules_text,
            "include_related": include_related,
        }

        def operation() -> dict[str, Any]:
            scan_targets = [self.store.get_artifact_record(session_id, artifact_id=artifact_id)]
            if include_related:
                relationships = self._artifact_relationships(session_id, artifact_id)
                child_ids = [item["artifact"]["artifact_id"] for item in relationships["children"]]
                for child_id in child_ids:
                    scan_targets.append(self.store.get_artifact_record(session_id, artifact_id=child_id))
            items = []
            total_matches = 0
            for target in scan_targets:
                context = self._artifact_parse_context(target)
                analysis = self._maybe_load_analysis(session_id, target["artifact_id"])
                scan = run_yara_scan(
                    path=context["path"],
                    data=context["data"],
                    parsed=context["parsed"],
                    strings=context["strings"],
                    analysis=analysis,
                    rules_text=rules_text,
                )
                total_matches += len(scan["matches"])
                items.append(
                    {
                        "artifact": self._artifact_reference(target),
                        "backend": scan["backend"],
                        "matches": scan["matches"],
                    }
                )
            command = self.store.append_operation_log(
                session_id,
                tool_name="scan_with_yara",
                artifact_id=artifact_id,
                action="scan",
                details={"include_related": include_related, "total_matches": total_matches},
            )
            return {"items": items, "total_matches": total_matches, "include_related": include_related, "command_log_entry": command}

        return self._respond("scan_with_yara", parameters, operation)

    def fingerprint_compiler_toolchain(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id}

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            context = self._artifact_parse_context(artifact)
            fingerprints = fingerprint_compiler_toolchain(
                path=context["path"],
                parsed=context["parsed"],
                strings=context["strings"],
            )
            command = self.store.append_operation_log(
                session_id,
                tool_name="fingerprint_compiler_toolchain",
                artifact_id=artifact_id,
                action="fingerprint",
                details={"match_count": len(fingerprints.get("items", []))},
            )
            return {
                "artifact": self._artifact_reference(artifact),
                "fingerprints": fingerprints,
                "command_log_entry": command,
            }

        return self._respond("fingerprint_compiler_toolchain", parameters, operation)

    def detect_packer(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id}

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            context = self._artifact_parse_context(artifact)
            packer_detection = detect_packer_report(
                path=context["path"],
                data=context["data"],
                parsed=context["parsed"],
                strings=context["strings"],
            )
            command = self.store.append_operation_log(
                session_id,
                tool_name="detect_packer",
                artifact_id=artifact_id,
                action="detect",
                details={"detection_count": len(packer_detection.get("detections", []))},
            )
            return {
                "artifact": self._artifact_reference(artifact),
                "packer_detection": packer_detection,
                "command_log_entry": command,
            }

        return self._respond("detect_packer", parameters, operation)

    def calculate_entropy(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id}

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            context = self._artifact_parse_context(artifact)
            return {
                "artifact": self._artifact_reference(artifact),
                "entropy": calculate_entropy_report(
                    path=context["path"],
                    data=context["data"],
                    parsed=context["parsed"],
                ),
            }

        return self._respond("calculate_entropy", parameters, operation)

    def deobfuscate_strings(self, session_id: str, artifact_id: str, limit: int = 50) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "limit": limit}

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            context = self._artifact_parse_context(artifact)
            return {
                "artifact": self._artifact_reference(artifact),
                "deobfuscated_strings": deobfuscate_strings_report(
                    path=context["path"],
                    parsed=context["parsed"],
                    strings=context["strings"],
                    limit=limit,
                ),
            }

        return self._respond(
            "deobfuscate_strings",
            parameters,
            operation,
            partial_result=lambda result: result["deobfuscated_strings"]["truncated"],
        )

    def extract_resources(
        self,
        session_id: str,
        artifact_id: str,
        output_subdir: str | None = None,
        attach_to_session: bool = False,
        target_session_id: str | None = None,
        analyze_extracted: bool = False,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "output_subdir": output_subdir,
            "attach_to_session": attach_to_session,
            "target_session_id": target_session_id,
            "analyze_extracted": analyze_extracted,
        }

        def operation() -> dict[str, Any]:
            if target_session_id and not attach_to_session:
                raise StructuredToolError(
                    "invalid_request",
                    "target_session_requires_attach",
                    "target_session_id may only be provided when attach_to_session is true.",
                )
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            context = self._artifact_parse_context(artifact)
            items = extract_pe_resources(context["path"], context["data"])
            skipped: list[dict[str, Any]] = []
            if not items:
                archive = extract_archive_members(
                    context["path"],
                    max_items=self.security.resource_limits.max_artifacts_per_session,
                    max_bytes=self.security.resource_limits.carved_byte_budget,
                )
                items = archive["items"]
                skipped = archive["skipped"]
            materialized = self._materialize_derived_artifacts(
                source_session_id=session_id,
                source_artifact=artifact,
                items=items,
                output_subdir=output_subdir or f"extracted/{artifact['artifact_id']}/resources",
                attach_to_session=attach_to_session,
                target_session_id=target_session_id,
                analyze_extracted=analyze_extracted,
            )
            return {
                "artifact": self._artifact_reference(artifact),
                "items": materialized["items"],
                "skipped": skipped + materialized["skipped"],
                "attached_artifacts": materialized["attached_artifacts"],
                "truncated": bool(skipped or materialized["skipped"]),
                "command_log_entry": self.store.append_operation_log(
                    session_id,
                    tool_name="extract_resources",
                    artifact_id=artifact_id,
                    action="extract",
                    details={
                        "attach_to_session": attach_to_session,
                        "target_session_id": target_session_id,
                        "item_count": len(materialized["items"]),
                        "skipped_count": len(skipped + materialized["skipped"]),
                    },
                ),
            }

        return self._respond(
            "extract_resources",
            parameters,
            operation,
            partial_result=lambda result: result["truncated"],
            suggested_next_actions=lambda result: self._post_extraction_actions(session_id, result),
        )

    def carve_embedded_artifacts(
        self,
        session_id: str,
        artifact_id: str,
        output_subdir: str | None = None,
        attach_to_session: bool = False,
        target_session_id: str | None = None,
        analyze_extracted: bool = False,
        recurse: bool = False,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "output_subdir": output_subdir,
            "attach_to_session": attach_to_session,
            "target_session_id": target_session_id,
            "analyze_extracted": analyze_extracted,
            "recurse": recurse,
        }

        def operation() -> dict[str, Any]:
            if target_session_id and not attach_to_session:
                raise StructuredToolError(
                    "invalid_request",
                    "target_session_requires_attach",
                    "target_session_id may only be provided when attach_to_session is true.",
                )
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            context = self._artifact_parse_context(artifact)
            overlay = detect_overlay(path=context["path"], data=context["data"], parsed=context["parsed"])
            items = []
            skipped = []
            if overlay["present"] and overlay["size"] > 0:
                payload = context["data"][int(overlay["offset"]) :]
                extension = overlay.get("suggested_extension") or ".bin"
                items.append(
                    {
                        "name": f"{artifact['safe_display_name']}_overlay{extension}",
                        "bytes": payload,
                        "provenance": {
                            "parent_artifact_id": artifact["artifact_id"],
                            "offset": int(overlay["offset"]),
                            "size": len(payload),
                            "detected_format": overlay.get("detected_format"),
                            "extraction_method": "overlay-carve",
                        },
                    }
                )
            archive = extract_archive_members(
                context["path"],
                max_items=self.security.resource_limits.max_artifacts_per_session,
                max_bytes=self.security.resource_limits.carved_byte_budget,
            )
            items.extend(archive["items"])
            skipped.extend(archive["skipped"])
            materialized = self._materialize_derived_artifacts(
                source_session_id=session_id,
                source_artifact=artifact,
                items=items,
                output_subdir=output_subdir or f"extracted/{artifact['artifact_id']}/carved",
                attach_to_session=attach_to_session,
                target_session_id=target_session_id,
                analyze_extracted=analyze_extracted,
                recurse=recurse,
            )
            return {
                "artifact": self._artifact_reference(artifact),
                "overlay": overlay,
                "items": materialized["items"],
                "skipped": skipped + materialized["skipped"],
                "attached_artifacts": materialized["attached_artifacts"],
                "truncated": bool(skipped or materialized["skipped"]),
                "command_log_entry": self.store.append_operation_log(
                    session_id,
                    tool_name="carve_embedded_artifacts",
                    artifact_id=artifact_id,
                    action="carve",
                    details={
                        "attach_to_session": attach_to_session,
                        "target_session_id": target_session_id,
                        "recurse": recurse,
                        "item_count": len(materialized["items"]),
                        "skipped_count": len(skipped + materialized["skipped"]),
                    },
                ),
            }

        return self._respond(
            "carve_embedded_artifacts",
            parameters,
            operation,
            partial_result=lambda result: result["truncated"],
            suggested_next_actions=lambda result: self._post_extraction_actions(session_id, result),
        )

    def get_artifact_relationships(self, session_id: str, artifact_id: str, direction: str = "both") -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "direction": direction}

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            normalized = direction.strip().lower()
            if normalized not in {"parents", "children", "both"}:
                raise StructuredToolError(
                    "invalid_request",
                    "relationship_direction_invalid",
                    "direction must be one of: parents, children, both.",
                )
            relationships = self._artifact_relationships(session_id, artifact_id)
            payload = {"artifact": self._artifact_reference(artifact), "direction": normalized}
            if normalized in {"parents", "both"}:
                payload["parents"] = relationships["parents"]
            if normalized in {"children", "both"}:
                payload["children"] = relationships["children"]
            return payload

        return self._respond("get_artifact_relationships", parameters, operation)

    def start_artifact_analysis(
        self,
        session_id: str,
        artifact_id: str,
        hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "hints": hints}
        return self._respond(
            "start_artifact_analysis",
            parameters,
            lambda: self.jobs.start_artifact_analysis(session_id, artifact_id, hints),
            suggested_next_actions=lambda result: [
                {
                    "tool": "get_job",
                    "parameters": {"job_id": result["job_id"]},
                    "rationale": "Poll the job handle until the analysis cache is available.",
                }
            ],
        )

    def get_analysis_synopsis(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            synopsis = self.parser_sandbox.build_analysis_synopsis(analysis, artifact_summary=self._artifact_reference(artifact))
            return self._enrich_analysis_synopsis(session_id, artifact_id, artifact, synopsis["result"])

        return self._respond(
            "get_analysis_synopsis",
            parameters,
            operation,
            suggested_next_actions=lambda result: self._analysis_synopsis_next_actions(session_id, artifact_id, result),
        )

    def list_artifact_symbols(
        self,
        session_id: str,
        artifact_id: str,
        cursor: int = 0,
        limit: int = 50,
        query: str | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "cursor": cursor,
            "limit": limit,
            "query": query,
            "kind": kind,
        }

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            items = list(analysis["symbols"])
            if kind:
                items = [item for item in items if item["kind"] == kind]
            if query:
                needle = query.lower()
                items = [
                    item
                    for item in items
                    if needle in item["name"].lower() or needle in (item.get("demangled_name") or "").lower()
                ]
            paged = self._page_items(items, cursor=cursor, limit=limit)
            return {"artifact": self._artifact_reference(artifact), **paged}

        return self._respond("list_artifact_symbols", parameters, operation, partial_result=lambda result: result["page"]["truncated"])

    def list_artifact_functions(
        self,
        session_id: str,
        artifact_id: str,
        cursor: int = 0,
        limit: int = 50,
        query: str | None = None,
        include_plt: bool = False,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "cursor": cursor,
            "limit": limit,
            "query": query,
            "include_plt": include_plt,
        }

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            items = list(analysis["functions"])
            if not include_plt:
                items = [item for item in items if not item.get("is_plt")]
            if query:
                needle = query.lower()
                def _query_rank(item: dict[str, Any]) -> tuple[int, int, int, str]:
                    names = [item["name"].lower(), (item.get("demangled_name") or "").lower()]
                    exact = any(name == needle for name in names)
                    prefix = any(name.startswith(needle) for name in names if name)
                    return (
                        0 if exact else 1,
                        0 if prefix else 1,
                        0 if any(needle in name for name in names) else 1,
                        item.get("demangled_name") or item["name"],
                    )

                items = [
                    item
                    for item in items
                    if needle in item["name"].lower() or needle in (item.get("demangled_name") or "").lower()
                ]
                items.sort(key=_query_rank)
            paged = self._page_items(items, cursor=cursor, limit=limit)
            return {"artifact": self._artifact_reference(artifact), **paged}

        return self._respond(
            "list_artifact_functions",
            parameters,
            operation,
            partial_result=lambda result: result["page"]["truncated"],
            suggested_next_actions=lambda result: [
                {
                    "tool": "disassemble_function",
                    "parameters": {
                        "session_id": session_id,
                        "artifact_id": artifact_id,
                        "function_id": result["items"][0]["function_id"],
                    }
                    if result["items"]
                    else {"session_id": session_id, "artifact_id": artifact_id},
                    "rationale": "Disassembly is the next step after selecting a recovered function.",
                }
            ],
        )

    def get_artifact_instruction_mode(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            modes = json_clone(analysis["capabilities"]["instruction_set_modes"])
            modes["current"] = modes.get("override") or modes["default"]
            return {"artifact": self._artifact_reference(artifact), "instruction_set_mode": modes}

        return self._respond("get_artifact_instruction_mode", parameters, operation)

    def set_artifact_instruction_mode(self, session_id: str, artifact_id: str, mode: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "mode": mode}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            supported = analysis["capabilities"]["instruction_set_modes"]["supported"]
            if mode not in supported:
                raise StructuredToolError(
                    "unsupported_format",
                    "instruction_mode_unsupported",
                    f"Instruction-set mode '{mode}' is not supported for this artifact.",
                    details={"supported": supported},
                )
            if len(supported) < 2:
                raise StructuredToolError(
                    "unsupported_format",
                    "instruction_mode_fixed",
                    "The current architecture exposes only one active instruction-set mode.",
                    details={"supported": supported},
                )
            self.store.set_artifact_instruction_mode_override(session_id, artifact_id, mode)
            modes = json_clone(analysis["capabilities"]["instruction_set_modes"])
            modes["override"] = mode
            modes["current"] = mode
            return {"artifact": self._artifact_reference(artifact), "instruction_set_mode": modes}

        return self._respond("set_artifact_instruction_mode", parameters, operation)

    def disassemble_function(
        self,
        session_id: str,
        artifact_id: str,
        function_id: str | None = None,
        name: str | None = None,
        address: int | str | None = None,
        cursor: int = 0,
        limit: int = 200,
        instruction_mode_override: str | None = None,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "function_id": function_id,
            "name": name,
            "address": address,
            "cursor": cursor,
            "limit": limit,
            "instruction_mode_override": instruction_mode_override,
        }

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            function = self._resolve_function_from_analysis(
                analysis,
                function_id=function_id,
                name=name,
                address=self._normalize_numeric_value(address) if address is not None else None,
            )
            result = self.parser_sandbox.disassemble_function(
                artifact["canonical_path"],
                analysis=analysis,
                function_address=function["address"],
                cursor=cursor,
                limit=limit,
                instruction_mode_override=instruction_mode_override,
            )
            return {"artifact": self._artifact_reference(artifact), **result["result"]}

        return self._respond(
            "disassemble_function",
            parameters,
            operation,
            partial_result=lambda result: result["page"]["truncated"],
            suggested_next_actions=lambda result: [
                {
                    "tool": "decompile_function",
                    "parameters": {
                        "session_id": session_id,
                        "artifact_id": artifact_id,
                        "function_id": result["function"]["function_id"],
                    },
                    "rationale": "Decompilation is the natural next step after targeted disassembly succeeds.",
                }
            ],
        )

    def disassemble_range(
        self,
        session_id: str,
        artifact_id: str,
        input_kind: str,
        start_value: int | str,
        size: int,
        cursor: int = 0,
        limit: int = 200,
        instruction_mode_override: str | None = None,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "input_kind": input_kind,
            "start_value": start_value,
            "size": size,
            "cursor": cursor,
            "limit": limit,
            "instruction_mode_override": instruction_mode_override,
        }

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            result = self.parser_sandbox.disassemble_range(
                artifact["canonical_path"],
                analysis=analysis,
                input_kind=input_kind,
                start_value=self._normalize_numeric_value(start_value),
                size=size,
                cursor=cursor,
                limit=limit,
                instruction_mode_override=instruction_mode_override,
            )
            return {"artifact": self._artifact_reference(artifact), **result["result"]}

        return self._respond("disassemble_range", parameters, operation, partial_result=lambda result: result["page"]["truncated"])

    def decompile_function(
        self,
        session_id: str,
        artifact_id: str,
        function_id: str | None = None,
        name: str | None = None,
        address: int | str | None = None,
        char_limit: int | None = None,
        line_limit: int = 200,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "function_id": function_id,
            "name": name,
            "address": address,
            "char_limit": char_limit,
            "line_limit": line_limit,
        }

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            function = self._resolve_function_from_analysis(
                analysis,
                function_id=function_id,
                name=name,
                address=self._normalize_numeric_value(address) if address is not None else None,
            )
            result = self.parser_sandbox.decompile_function(
                artifact["canonical_path"],
                function_address=function["address"],
                char_limit=char_limit or self.security.resource_limits.decompilation_char_limit,
                line_limit=line_limit,
            )
            return {"artifact": self._artifact_reference(artifact), "function": function, **result["result"]}

        return self._respond(
            "decompile_function",
            parameters,
            operation,
            partial_result=lambda result: bool(result.get("truncated")),
            suggested_next_actions=lambda result: [
                {
                    "tool": "disassemble_function",
                    "parameters": {
                        "session_id": session_id,
                        "artifact_id": artifact_id,
                        "function_id": result["function"]["function_id"],
                    },
                    "rationale": "Fallback to assembly when the decompilation is ambiguous or incomplete.",
                }
            ],
        )

    def read_artifact_bytes(
        self,
        session_id: str,
        artifact_id: str,
        input_kind: str,
        value: int | str,
        length: int,
        hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "input_kind": input_kind,
            "value": value,
            "length": length,
            "hints": hints,
        }

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            result = self.parser_sandbox.read_bytes(
                artifact["canonical_path"],
                input_kind=input_kind,
                value=self._normalize_numeric_value(value),
                length=length,
                hints=hints,
            )
            return {"artifact": self._artifact_reference(artifact), **result["result"]}

        return self._respond("read_artifact_bytes", parameters, operation)

    def list_artifact_xrefs(
        self,
        session_id: str,
        artifact_id: str,
        function_id: str | None = None,
        string_id: str | None = None,
        address: int | str | None = None,
        cursor: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "function_id": function_id,
            "string_id": string_id,
            "address": address,
            "cursor": cursor,
            "limit": limit,
        }

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            if function_id:
                items = [item for item in analysis["xrefs"] if item.get("target_function_id") == function_id]
            elif string_id:
                items = [item for item in analysis["xrefs"] if item.get("target_string_id") == string_id]
            elif address is not None:
                numeric_address = self._normalize_numeric_value(address)
                items = [item for item in analysis["xrefs"] if item.get("target_address") == numeric_address]
            else:
                raise StructuredToolError(
                    "invalid_request",
                    "xref_target_required",
                    "Provide function_id, string_id, or address to query cross-references.",
                )
            paged = self._page_items(items, cursor=cursor, limit=limit)
            return {"artifact": self._artifact_reference(artifact), **paged}

        return self._respond("list_artifact_xrefs", parameters, operation, partial_result=lambda result: result["page"]["truncated"])

    def search_artifact(
        self,
        session_id: str,
        artifact_id: str,
        kind: str,
        query: str | None = None,
        start_address: int | str | None = None,
        end_address: int | str | None = None,
        cursor: int = 0,
        limit: int = 50,
        case_sensitive: bool = False,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "kind": kind,
            "query": query,
            "start_address": start_address,
            "end_address": end_address,
            "cursor": cursor,
            "limit": limit,
            "case_sensitive": case_sensitive,
        }

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            result = self.parser_sandbox.search_program(
                artifact["canonical_path"],
                analysis=analysis,
                kind=kind,
                query=query,
                start_address=self._normalize_numeric_value(start_address) if start_address is not None else None,
                end_address=self._normalize_numeric_value(end_address) if end_address is not None else None,
                cursor=cursor,
                limit=limit,
                case_sensitive=case_sensitive,
            )
            return {"artifact": self._artifact_reference(artifact), **result["result"]}

        return self._respond("search_artifact", parameters, operation, partial_result=lambda result: result["page"]["truncated"])

    def get_artifact_linkage(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            return {"artifact": self._artifact_reference(artifact), "linkage": analysis["linkage"]}

        return self._respond("get_artifact_linkage", parameters, operation)

    def get_artifact_debug_info(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            return {"artifact": self._artifact_reference(artifact), "debug_info": analysis["debug_info"]}

        return self._respond("get_artifact_debug_info", parameters, operation)

    def detect_crypto_constants(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            context = self._artifact_parse_context(artifact)
            crypto_constants = detect_crypto_constants_report(analysis, context["data"])
            command = self.store.append_operation_log(
                session_id,
                tool_name="detect_crypto_constants",
                artifact_id=artifact_id,
                action="detect",
                details={"hit_count": len(crypto_constants.get("items", []))},
            )
            return {
                "artifact": self._artifact_reference(artifact),
                "crypto_constants": crypto_constants,
                "command_log_entry": command,
            }

        return self._respond("detect_crypto_constants", parameters, operation)

    def recognize_library_code(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            context = self._artifact_parse_context(artifact)
            library_recognition = recognize_library_code_report(context["parsed"], analysis)
            command = self.store.append_operation_log(
                session_id,
                tool_name="recognize_library_code",
                artifact_id=artifact_id,
                action="recognize",
                details={"recognized_count": len(library_recognition.get("recognized", []))},
            )
            return {
                "artifact": self._artifact_reference(artifact),
                "library_recognition": library_recognition,
                "command_log_entry": command,
            }

        return self._respond("recognize_library_code", parameters, operation)

    def get_call_graph(
        self,
        session_id: str,
        artifact_id: str,
        function_id: str | None = None,
        name: str | None = None,
        address: int | str | None = None,
        direction: str = "both",
        depth: int = 1,
        limit_nodes: int = 100,
        limit_edges: int = 200,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "function_id": function_id,
            "name": name,
            "address": address,
            "direction": direction,
            "depth": depth,
            "limit_nodes": limit_nodes,
            "limit_edges": limit_edges,
        }

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            function = self._resolve_function_from_analysis(
                analysis,
                function_id=function_id,
                name=name,
                address=self._normalize_numeric_value(address) if address is not None else None,
            )
            normalized_direction = direction.strip().lower()
            if normalized_direction not in {"incoming", "outgoing", "both"}:
                raise StructuredToolError("invalid_request", "call_graph_direction_invalid", "direction must be one of: incoming, outgoing, both.")
            function_by_address = {int(item["address"]): item for item in analysis.get("functions", [])}
            current_ids = {function.get("function_id")} if function.get("function_id") else set()
            current_addresses = {int(function["address"])}
            visited_ids = set(current_ids)
            visited_addresses = set(current_addresses)
            edge_items: list[dict[str, Any]] = []
            seen_edges: set[tuple[int | None, int | None, str | None, str | None, str | None]] = set()
            for _ in range(max(1, int(depth))):
                next_ids: set[str] = set()
                next_addresses: set[int] = set()
                for edge in analysis.get("call_graph", {}).get("edges", []):
                    source_address = edge.get("source_function_address") or edge.get("source_address")
                    target_address = edge.get("target_address")
                    include_outgoing = normalized_direction in {"outgoing", "both"} and (
                        edge.get("source_function_id") in current_ids or source_address in current_addresses
                    )
                    include_incoming = normalized_direction in {"incoming", "both"} and (
                        edge.get("target_function_id") in current_ids or target_address in current_addresses
                    )
                    if not include_outgoing and not include_incoming:
                        continue
                    edge_key = (
                        int(source_address) if source_address is not None else None,
                        int(target_address) if target_address is not None else None,
                        edge.get("source_function_id"),
                        edge.get("target_function_id"),
                        edge.get("target_name"),
                    )
                    if edge_key in seen_edges:
                        continue
                    seen_edges.add(edge_key)
                    edge_items.append(edge)
                    for key in ("source_function_id", "target_function_id"):
                        if edge.get(key) and edge[key] not in visited_ids:
                            next_ids.add(edge[key])
                    for candidate in (source_address, target_address):
                        if candidate is None:
                            continue
                        numeric_candidate = int(candidate)
                        if numeric_candidate not in visited_addresses:
                            next_addresses.add(numeric_candidate)
                if len(edge_items) >= max(1, int(limit_edges)):
                    break
                if not next_ids and not next_addresses:
                    break
                visited_ids.update(next_ids)
                visited_addresses.update(next_addresses)
                current_ids = next_ids
                current_addresses = next_addresses
            node_items: list[dict[str, Any]] = []
            seen_node_keys: set[tuple[str | None, int | None, str]] = set()
            for node in analysis.get("call_graph", {}).get("nodes", []):
                node_address = node.get("address")
                include_node = node.get("function_id") in visited_ids or (
                    node_address is not None and int(node_address) in visited_addresses
                ) or (
                    node.get("node_kind") == "external"
                    and any(edge.get("target_name") == node.get("name") for edge in edge_items)
                )
                if include_node:
                    node_key = (node.get("function_id"), int(node_address) if node_address is not None else None, node.get("node_kind", "unknown"))
                    if node_key in seen_node_keys:
                        continue
                    seen_node_keys.add(node_key)
                    node_items.append(node)
            for address_value in sorted(visited_addresses):
                function_item = function_by_address.get(int(address_value))
                if function_item is None:
                    continue
                node_key = (function_item.get("function_id"), int(function_item["address"]), "function")
                if node_key in seen_node_keys:
                    continue
                seen_node_keys.add(node_key)
                node_items.append(
                    {
                        "node_kind": "function",
                        "function_id": function_item.get("function_id"),
                        "address": int(function_item["address"]),
                        "name": function_item.get("demangled_name") or function_item["name"],
                    }
                )
            return {
                "artifact": self._artifact_reference(artifact),
                "target_function": function,
                "nodes": node_items[: max(1, int(limit_nodes))],
                "edges": edge_items[: max(1, int(limit_edges))],
                "direction": normalized_direction,
                "depth": max(1, int(depth)),
                "truncated": len(node_items) > max(1, int(limit_nodes)) or len(edge_items) > max(1, int(limit_edges)),
            }

        return self._respond("get_call_graph", parameters, operation, partial_result=lambda result: result["truncated"])

    def get_control_flow_graph(
        self,
        session_id: str,
        artifact_id: str,
        function_id: str | None = None,
        name: str | None = None,
        address: int | str | None = None,
    ) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "function_id": function_id, "name": name, "address": address}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            function = self._resolve_function_from_analysis(
                analysis,
                function_id=function_id,
                name=name,
                address=self._normalize_numeric_value(address) if address is not None else None,
            )
            detail = self._function_detail(analysis, function)
            return {"artifact": self._artifact_reference(artifact), "function": function, "control_flow_graph": detail["control_flow_graph"]}

        return self._respond("get_control_flow_graph", parameters, operation)

    def get_function_variables(
        self,
        session_id: str,
        artifact_id: str,
        function_id: str | None = None,
        name: str | None = None,
        address: int | str | None = None,
    ) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "function_id": function_id, "name": name, "address": address}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            function = self._resolve_function_from_analysis(
                analysis,
                function_id=function_id,
                name=name,
                address=self._normalize_numeric_value(address) if address is not None else None,
            )
            detail = self._function_detail(analysis, function)
            return {"artifact": self._artifact_reference(artifact), "function": function, "variables": detail["variables"]}

        return self._respond("get_function_variables", parameters, operation)

    def get_stack_frame(
        self,
        session_id: str,
        artifact_id: str,
        function_id: str | None = None,
        name: str | None = None,
        address: int | str | None = None,
    ) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "function_id": function_id, "name": name, "address": address}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            function = self._resolve_function_from_analysis(
                analysis,
                function_id=function_id,
                name=name,
                address=self._normalize_numeric_value(address) if address is not None else None,
            )
            detail = self._function_detail(analysis, function)
            return {"artifact": self._artifact_reference(artifact), "function": function, "stack_frame": detail["stack_frame"]}

        return self._respond("get_stack_frame", parameters, operation)

    def get_constant_propagation(
        self,
        session_id: str,
        artifact_id: str,
        function_id: str | None = None,
        name: str | None = None,
        address: int | str | None = None,
    ) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "function_id": function_id, "name": name, "address": address}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            function = self._resolve_function_from_analysis(
                analysis,
                function_id=function_id,
                name=name,
                address=self._normalize_numeric_value(address) if address is not None else None,
            )
            detail = self._function_detail(analysis, function)
            return {"artifact": self._artifact_reference(artifact), "function": function, "constant_propagation": detail["constant_propagation"]}

        return self._respond("get_constant_propagation", parameters, operation)

    def get_type_information(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            return {"artifact": self._artifact_reference(artifact), "type_information": analysis.get("type_information", {})}

        return self._respond("get_type_information", parameters, operation)

    def recover_types(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            return {"artifact": self._artifact_reference(artifact), "recovered_types": analysis.get("recovered_types", {})}

        return self._respond("recover_types", parameters, operation)

    def inspect_data_segments(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            return {"artifact": self._artifact_reference(artifact), "data_segments": analysis.get("data_segments", {})}

        return self._respond("inspect_data_segments", parameters, operation)

    def get_indirect_flows(
        self,
        session_id: str,
        artifact_id: str,
        function_id: str | None = None,
        name: str | None = None,
        address: int | str | None = None,
    ) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "function_id": function_id, "name": name, "address": address}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            function = self._resolve_function_from_analysis(
                analysis,
                function_id=function_id,
                name=name,
                address=self._normalize_numeric_value(address) if address is not None else None,
            )
            detail = self._function_detail(analysis, function)
            return {"artifact": self._artifact_reference(artifact), "function": function, "indirect_flows": detail["indirect_flows"]}

        return self._respond("get_indirect_flows", parameters, operation)

    def get_exception_metadata(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            return {"artifact": self._artifact_reference(artifact), "exception_metadata": analysis.get("exception_metadata", {})}

        return self._respond("get_exception_metadata", parameters, operation)

    def get_calling_convention(
        self,
        session_id: str,
        artifact_id: str,
        function_id: str | None = None,
        name: str | None = None,
        address: int | str | None = None,
    ) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "function_id": function_id, "name": name, "address": address}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            function = self._resolve_function_from_analysis(
                analysis,
                function_id=function_id,
                name=name,
                address=self._normalize_numeric_value(address) if address is not None else None,
            )
            detail = self._function_detail(analysis, function)
            return {"artifact": self._artifact_reference(artifact), "function": function, "calling_convention": detail["calling_convention"]}

        return self._respond("get_calling_convention", parameters, operation)

    def get_intermediate_representation(
        self,
        session_id: str,
        artifact_id: str,
        function_id: str | None = None,
        name: str | None = None,
        address: int | str | None = None,
        limit_blocks: int = 8,
        limit_statements: int = 25,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "function_id": function_id,
            "name": name,
            "address": address,
            "limit_blocks": limit_blocks,
            "limit_statements": limit_statements,
        }

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            function = self._resolve_function_from_analysis(
                analysis,
                function_id=function_id,
                name=name,
                address=self._normalize_numeric_value(address) if address is not None else None,
            )
            detail = self._function_detail(analysis, function)
            ir = json_clone(detail["intermediate_representation"])
            ir["blocks"] = [
                {**block, "statements": block["statements"][: max(1, int(limit_statements))]}
                for block in ir["blocks"][: max(1, int(limit_blocks))]
            ]
            return {"artifact": self._artifact_reference(artifact), "function": function, "intermediate_representation": ir}

        return self._respond("get_intermediate_representation", parameters, operation, partial_result=lambda result: bool(result["intermediate_representation"].get("truncated")))

    def get_runtime_metadata(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            return {"artifact": self._artifact_reference(artifact), "runtime_metadata": analysis.get("runtime_metadata", {})}

        return self._respond("get_runtime_metadata", parameters, operation)

    def slice_data_flow(
        self,
        session_id: str,
        artifact_id: str,
        function_id: str | None = None,
        name: str | None = None,
        address: int | str | None = None,
        anchor_address: int | str | None = None,
        register: str | None = None,
        radius: int = 6,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "function_id": function_id,
            "name": name,
            "address": address,
            "anchor_address": anchor_address,
            "register": register,
            "radius": radius,
        }

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            function = self._resolve_function_from_analysis(
                analysis,
                function_id=function_id,
                name=name,
                address=self._normalize_numeric_value(address) if address is not None else None,
            )
            detail = self._function_detail(analysis, function)
            slice_payload = slice_function_data_flow(
                detail,
                anchor_address=self._normalize_numeric_value(anchor_address) if anchor_address is not None else None,
                register=register.lower() if register else None,
                radius=radius,
            )
            return {"artifact": self._artifact_reference(artifact), "function": function, "slice": slice_payload}

        return self._respond("slice_data_flow", parameters, operation)

    def identify_system_calls(
        self,
        session_id: str,
        artifact_id: str,
        function_id: str | None = None,
        name: str | None = None,
        address: int | str | None = None,
    ) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "function_id": function_id, "name": name, "address": address}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            function = self._resolve_function_from_analysis(
                analysis,
                function_id=function_id,
                name=name,
                address=self._normalize_numeric_value(address) if address is not None else None,
            )
            detail = self._function_detail(analysis, function)
            return {"artifact": self._artifact_reference(artifact), "function": function, "system_calls": detail["system_calls"]}

        return self._respond("identify_system_calls", parameters, operation)

    def navigate_neighborhood(
        self,
        session_id: str,
        artifact_id: str,
        function_id: str | None = None,
        name: str | None = None,
        address: int | str | None = None,
        depth: int = 1,
        radius: int = 1,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "function_id": function_id,
            "name": name,
            "address": address,
            "depth": depth,
            "radius": radius,
        }

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            function = self._resolve_function_from_analysis(
                analysis,
                function_id=function_id,
                name=name,
                address=self._normalize_numeric_value(address) if address is not None else None,
            )
            neighborhood = navigate_function_neighborhood(analysis, function, depth=depth, radius=radius)
            return {"artifact": self._artifact_reference(artifact), "neighborhood": neighborhood}

        return self._respond("navigate_neighborhood", parameters, operation)

    def prioritize_functions(
        self,
        session_id: str,
        artifact_id: str,
        include_tags: list[str] | None = None,
        exclude_tags: list[str] | None = None,
        min_score: int | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "include_tags": include_tags,
            "exclude_tags": exclude_tags,
            "min_score": min_score,
            "limit": limit,
        }

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            items = filter_and_prioritize_functions(
                analysis,
                include_tags=include_tags,
                exclude_tags=exclude_tags,
                min_score=min_score,
                max_items=limit,
            )
            return {
                "artifact": self._artifact_reference(artifact),
                "items": items,
                "filters": {"include_tags": include_tags or [], "exclude_tags": exclude_tags or [], "min_score": min_score},
            }

        return self._respond("prioritize_functions", parameters, operation)

    def classify_functions(
        self,
        session_id: str,
        artifact_id: str,
        include_tags: list[str] | None = None,
        exclude_tags: list[str] | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "include_tags": include_tags,
            "exclude_tags": exclude_tags,
            "limit": limit,
        }

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            items = filter_and_prioritize_functions(
                analysis,
                include_tags=include_tags,
                exclude_tags=exclude_tags,
                max_items=limit,
            )
            return {
                "artifact": self._artifact_reference(artifact),
                "items": [
                    {
                        "function_id": item["function_id"],
                        "name": item.get("demangled_name") or item["name"],
                        "address": item["address"],
                        "classification_tags": item.get("classification_tags", []),
                    }
                    for item in items
                ],
                "filters": {"include_tags": include_tags or [], "exclude_tags": exclude_tags or []},
            }

        return self._respond("classify_functions", parameters, operation)

    def save_workflow_item(
        self,
        session_id: str,
        kind: str,
        target: dict[str, Any],
        value: dict[str, Any],
        annotation_id: str | None = None,
    ) -> dict[str, Any]:
        parameters = {"session_id": session_id, "kind": kind, "target": target, "value": value, "annotation_id": annotation_id}
        annotation_type = self._workflow_annotation_type(kind)
        return self._respond(
            "save_workflow_item",
            parameters,
            lambda: self.store.put_annotation(session_id, target, annotation_type, {"kind": kind, **json_clone(value)}, annotation_id),
        )

    def list_workflow_items(
        self,
        session_id: str,
        kind: str | None = None,
        artifact_id: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        parameters = {"session_id": session_id, "kind": kind, "artifact_id": artifact_id, "cursor": cursor, "limit": limit}

        def operation() -> dict[str, Any]:
            annotation_type = self._workflow_annotation_type(kind) if kind else None
            payload = self.store.list_annotations(session_id, artifact_id, None, annotation_type, cursor, limit)
            return payload

        return self._respond("list_workflow_items", parameters, operation)

    def export_curated_analysis(
        self,
        session_id: str,
        artifact_id: str,
        function_ids: list[str] | None = None,
        string_ids: list[str] | None = None,
        annotation_ids: list[str] | None = None,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "function_ids": function_ids,
            "string_ids": string_ids,
            "annotation_ids": annotation_ids,
            "output_path": output_path,
        }

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            selected_functions = [
                item for item in analysis["functions"] if not function_ids or item["function_id"] in set(function_ids)
            ]
            selected_strings = [
                item for item in analysis["strings"] if not string_ids or item["string_id"] in set(string_ids)
            ]
            workflow_items = self.store.list_annotations(session_id, artifact_id=artifact_id, cursor=None, limit=None)["items"]
            if annotation_ids:
                workflow_items = [item for item in workflow_items if item["annotation_id"] in set(annotation_ids)]
            payload = {
                "artifact": self._artifact_reference(artifact),
                "functions": selected_functions,
                "strings": selected_strings,
                "xrefs": [
                    item
                    for item in analysis["xrefs"]
                    if item.get("source_function_id") in {entry["function_id"] for entry in selected_functions}
                    or item.get("target_function_id") in {entry["function_id"] for entry in selected_functions}
                ],
                "workflow_items": workflow_items,
            }
            if output_path:
                export_file = self.security.resolve_output_file(output_path, purpose="Curated analysis export")
                export_file.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
                return {"path": str(export_file), "relative_path": self.security._relative_to_workspace(export_file), "artifact": self._artifact_reference(artifact)}
            return payload

        return self._respond("export_curated_analysis", parameters, operation)

    def batch_query_artifacts(
        self,
        session_id: str,
        operation: str,
        include_tags: list[str] | None = None,
        exclude_tags: list[str] | None = None,
        min_score: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "operation": operation,
            "include_tags": include_tags,
            "exclude_tags": exclude_tags,
            "min_score": min_score,
            "limit": limit,
        }

        def execute_for_artifact(artifact: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
            normalized = operation.strip().lower()
            if normalized == "analysis_synopsis":
                synopsis = self.parser_sandbox.build_analysis_synopsis(analysis, artifact_summary=self._artifact_reference(artifact))
                return synopsis["result"]
            if normalized == "classify_functions":
                return {
                    "items": [
                        {
                            "function_id": item["function_id"],
                            "name": item.get("demangled_name") or item["name"],
                            "classification_tags": item.get("classification_tags", []),
                        }
                        for item in filter_and_prioritize_functions(
                            analysis,
                            include_tags=include_tags,
                            exclude_tags=exclude_tags,
                            max_items=limit or 50,
                        )
                    ]
                }
            if normalized == "prioritize_functions":
                return {
                    "items": filter_and_prioritize_functions(
                        analysis,
                        include_tags=include_tags,
                        exclude_tags=exclude_tags,
                        min_score=min_score,
                        max_items=limit or 50,
                    )
                }
            if normalized == "inspect_data_segments":
                return {"data_segments": analysis.get("data_segments", {})}
            raise StructuredToolError(
                "invalid_request",
                "batch_operation_invalid",
                "operation must be one of: analysis_synopsis, classify_functions, prioritize_functions, inspect_data_segments.",
            )

        def op() -> dict[str, Any]:
            artifacts = self.store.list_artifacts(session_id, cursor=None, limit=None)["items"]
            items = []
            for artifact_summary in artifacts:
                if artifact_summary["analysis_status"] != "completed":
                    items.append({"artifact": artifact_summary, "status": "skipped", "reason": "analysis_not_completed"})
                    continue
                loaded = self.store.load_artifact_analysis(session_id, artifact_summary["artifact_id"])
                items.append(
                    {
                        "artifact": self._artifact_reference(loaded["artifact"]),
                        "status": "completed",
                        "result": execute_for_artifact(loaded["artifact"], loaded["analysis"]),
                    }
                )
            return {"items": items, "operation": operation}

        return self._respond("batch_query_artifacts", parameters, op)

    def list_artifacts(self, session_id: str, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
        parameters = {"session_id": session_id, "cursor": cursor, "limit": limit}
        return self._respond("list_artifacts", parameters, lambda: self.store.list_artifacts(session_id, cursor, limit))

    def remove_artifact(self, session_id: str, artifact_id: str | None = None, display_name: str | None = None) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "display_name": display_name}
        return self._respond("remove_artifact", parameters, lambda: self.store.remove_artifact(session_id, artifact_id, display_name))

    def register_provisional_function(self, session_id: str, artifact_id: str, name: str, address: int | str | None = None) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "name": name, "address": address}
        return self._respond("register_provisional_function", parameters, lambda: self.store.register_provisional_function(session_id, artifact_id, name, address))

    def register_provisional_string(self, session_id: str, artifact_id: str, value: str, address: int | str | None = None) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "value": value, "address": address}
        return self._respond("register_provisional_string", parameters, lambda: self.store.register_provisional_string(session_id, artifact_id, value, address))

    def get_object_reference(self, session_id: str, object_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "object_id": object_id}
        return self._respond("get_object_reference", parameters, lambda: self.store.get_object_reference(session_id, object_id))

    def put_annotation(
        self,
        session_id: str,
        target: dict[str, Any],
        annotation_type: str,
        value: Any,
        annotation_id: str | None = None,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "target": target,
            "annotation_type": annotation_type,
            "value": value,
            "annotation_id": annotation_id,
        }
        return self._respond("put_annotation", parameters, lambda: self.store.put_annotation(session_id, target, annotation_type, value, annotation_id))

    def list_annotations(
        self,
        session_id: str,
        artifact_id: str | None = None,
        target_kind: str | None = None,
        annotation_type: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "target_kind": target_kind,
            "annotation_type": annotation_type,
            "cursor": cursor,
            "limit": limit,
        }
        return self._respond(
            "list_annotations",
            parameters,
            lambda: self.store.list_annotations(session_id, artifact_id, target_kind, annotation_type, cursor, limit),
        )

    def get_annotation_history(self, session_id: str, annotation_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "annotation_id": annotation_id}
        return self._respond("get_annotation_history", parameters, lambda: self.store.get_annotation_history(session_id, annotation_id))

    def revert_annotation(self, session_id: str, annotation_id: str, revision_id: str | None = None) -> dict[str, Any]:
        parameters = {"session_id": session_id, "annotation_id": annotation_id, "revision_id": revision_id}
        return self._respond("revert_annotation", parameters, lambda: self.store.revert_annotation(session_id, annotation_id, revision_id))

    def create_session_snapshot(self, session_id: str, name: str, description: str | None = None) -> dict[str, Any]:
        parameters = {"session_id": session_id, "name": name, "description": description}
        return self._respond("create_session_snapshot", parameters, lambda: self.store.create_snapshot(session_id, name, description))

    def list_session_snapshots(self, session_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id}
        return self._respond("list_session_snapshots", parameters, lambda: self.store.list_snapshots(session_id))

    def restore_session_snapshot(self, session_id: str, snapshot_id: str | None = None, name: str | None = None) -> dict[str, Any]:
        parameters = {"session_id": session_id, "snapshot_id": snapshot_id, "name": name}
        return self._respond("restore_session_snapshot", parameters, lambda: self.store.restore_snapshot(session_id, snapshot_id, name))

    def start_artifact_reanalysis(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id}
        return self._respond(
            "start_artifact_reanalysis",
            parameters,
            lambda: self.jobs.start_artifact_reanalysis(session_id, artifact_id),
            suggested_next_actions=lambda result: [
                {
                    "tool": "get_job",
                    "parameters": {"job_id": result["job_id"]},
                    "rationale": "Poll the asynchronous job handle until the artifact re-analysis completes or is cancelled.",
                }
            ],
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        parameters = {"job_id": job_id}
        return self._respond(
            "get_job",
            parameters,
            lambda: self.jobs.get_job(job_id),
            partial_result=lambda result: result["status"] in {"queued", "running", "cancelling"},
        )

    def list_jobs(self, session_id: str | None = None, status: str | None = None, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
        parameters = {"session_id": session_id, "status": status, "cursor": cursor, "limit": limit}
        return self._respond("list_jobs", parameters, lambda: self.jobs.list_jobs(session_id, status, cursor, limit))

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        parameters = {"job_id": job_id}
        return self._respond("cancel_job", parameters, lambda: self.jobs.cancel_job(job_id))

    def export_session_state(self, session_id: str, output_path: str | None = None) -> dict[str, Any]:
        parameters = {"session_id": session_id, "output_path": output_path}
        return self._respond("export_session_state", parameters, lambda: self.store.export_session_state(session_id, output_path))

    def patch_artifact_bytes(
        self,
        session_id: str,
        artifact_id: str,
        input_kind: str,
        value: int | str,
        bytes_hex: str,
        output_path: str | None = None,
        attach_to_session: bool = True,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "input_kind": input_kind,
            "value": value,
            "bytes_hex": bytes_hex,
            "output_path": output_path,
            "attach_to_session": attach_to_session,
            "display_name": display_name,
        }

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            analysis = self._maybe_load_analysis(session_id, artifact_id)
            patch_bytes = decode_patch_bytes(bytes_hex)
            normalized_value = self._normalize_numeric_value(value)
            report = build_patch_report(
                artifact["canonical_path"],
                input_kind=input_kind,
                value=normalized_value,
                patch_bytes=patch_bytes,
                analysis=analysis,
            )
            output_file = self._resolve_patched_output_path(artifact, output_path, display_name)
            output_info = apply_patch_bytes(
                artifact["canonical_path"],
                file_offset=report["resolved"]["file_offset"],
                patch_bytes=patch_bytes,
                output_path=output_file,
            )
            attached_artifact = None
            if attach_to_session:
                relationship = {
                    "parent_artifact_id": artifact["artifact_id"],
                    "parent_session_id": session_id,
                    "patch": {
                        "input_kind": input_kind,
                        "value": normalized_value,
                        "bytes_hex": patch_bytes.hex(),
                    },
                }
                attached_artifact = self.store.add_artifact(session_id, output_info["path"], display_name or Path(output_info["path"]).name, relationship=relationship)
                self.store.update_artifact_feature07(
                    session_id,
                    attached_artifact["artifact_id"],
                    {
                        "patch_history": [
                            {
                                "kind": "byte_patch",
                                "source_artifact_id": artifact["artifact_id"],
                                "input_kind": input_kind,
                                "value": normalized_value,
                                "bytes_hex": patch_bytes.hex(),
                                "resolved": report["resolved"],
                                "warnings": report["warnings"],
                            }
                        ]
                    },
                )
            command = self.store.append_operation_log(
                session_id,
                tool_name="patch_artifact_bytes",
                artifact_id=artifact_id,
                action="apply_byte_patch",
                details={
                    "input_kind": input_kind,
                    "value": normalized_value,
                    "bytes_hex": patch_bytes.hex(),
                    "output_path": str(output_info["path"]),
                    "attached_artifact_id": attached_artifact["artifact_id"] if attached_artifact else None,
                },
            )
            return {
                "artifact": self._artifact_reference(artifact),
                "patch": report,
                "output": {
                    **output_info,
                    "relative_path": self.security._relative_to_workspace(Path(output_info["path"])),
                },
                "attached_artifact": attached_artifact,
                "command_log_entry": command,
            }

        return self._respond(
            "patch_artifact_bytes",
            parameters,
            operation,
            suggested_next_actions=lambda result: self._patch_follow_up_actions(session_id, artifact_id, result),
        )

    def patch_artifact_assembly(
        self,
        session_id: str,
        artifact_id: str,
        input_kind: str,
        value: int | str,
        assembly: str,
        isa: str,
        output_path: str | None = None,
        attach_to_session: bool = True,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "input_kind": input_kind,
            "value": value,
            "assembly": assembly,
            "isa": isa,
            "output_path": output_path,
            "attach_to_session": attach_to_session,
            "display_name": display_name,
        }

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            analysis = self._maybe_load_analysis(session_id, artifact_id)
            normalized_value = self._normalize_numeric_value(value)
            preliminary = parse_feature07_context(artifact["canonical_path"])
            resolved = build_patch_report(
                artifact["canonical_path"],
                input_kind=input_kind,
                value=normalized_value,
                patch_bytes=b"\x90",
                analysis=analysis,
            )["resolved"]
            assembled = assemble_patch(isa, assembly, origin_virtual_address=resolved.get("virtual_address"))
            report = build_patch_report(
                artifact["canonical_path"],
                input_kind=input_kind,
                value=normalized_value,
                patch_bytes=assembled["bytes"],
                analysis=analysis,
            )
            output_file = self._resolve_patched_output_path(artifact, output_path, display_name)
            output_info = apply_patch_bytes(
                artifact["canonical_path"],
                file_offset=report["resolved"]["file_offset"],
                patch_bytes=assembled["bytes"],
                output_path=output_file,
            )
            attached_artifact = None
            if attach_to_session:
                relationship = {
                    "parent_artifact_id": artifact["artifact_id"],
                    "parent_session_id": session_id,
                    "patch": {
                        "input_kind": input_kind,
                        "value": normalized_value,
                        "assembly": assembly,
                        "isa": assembled["isa"],
                    },
                }
                attached_artifact = self.store.add_artifact(session_id, output_info["path"], display_name or Path(output_info["path"]).name, relationship=relationship)
                self.store.update_artifact_feature07(
                    session_id,
                    attached_artifact["artifact_id"],
                    {
                        "patch_history": [
                            {
                                "kind": "assembly_patch",
                                "source_artifact_id": artifact["artifact_id"],
                                "input_kind": input_kind,
                                "value": normalized_value,
                                "assembly": assembly,
                                "isa": assembled["isa"],
                                "bytes_hex": assembled["bytes"].hex(),
                                "resolved": report["resolved"],
                                "warnings": report["warnings"],
                            }
                        ]
                    },
                )
            command = self.store.append_operation_log(
                session_id,
                tool_name="patch_artifact_assembly",
                artifact_id=artifact_id,
                action="apply_assembly_patch",
                details={
                    "input_kind": input_kind,
                    "value": normalized_value,
                    "assembly": assembly,
                    "isa": assembled["isa"],
                    "bytes_hex": assembled["bytes"].hex(),
                    "output_path": str(output_info["path"]),
                    "parsed_format": preliminary["parsed"].get("file_type", {}).get("format"),
                    "attached_artifact_id": attached_artifact["artifact_id"] if attached_artifact else None,
                },
            )
            return {
                "artifact": self._artifact_reference(artifact),
                "assembly_backend": {
                    "isa": assembled["isa"],
                    "items": assembled["items"],
                    "supported_examples": assembled["supported_examples"],
                },
                "patch": report,
                "output": {
                    **output_info,
                    "relative_path": self.security._relative_to_workspace(Path(output_info["path"])),
                },
                "attached_artifact": attached_artifact,
                "command_log_entry": command,
            }

        return self._respond(
            "patch_artifact_assembly",
            parameters,
            operation,
            suggested_next_actions=lambda result: self._patch_follow_up_actions(session_id, artifact_id, result),
        )

    def find_code_caves(self, session_id: str, artifact_id: str, min_size: int = 32) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "min_size": min_size}

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            result = discover_code_caves(artifact["canonical_path"], min_size=min_size)
            return {"artifact": self._artifact_reference(artifact), "code_caves": result}

        return self._respond(
            "find_code_caves",
            parameters,
            operation,
            suggested_next_actions=lambda result: self._code_cave_actions(session_id, artifact_id, result),
        )

    def edit_artifact_metadata(self, session_id: str, artifact_id: str, edit_kind: str, target: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "edit_kind": edit_kind, "target": target, "value": value}

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            analysis = self._maybe_load_analysis(session_id, artifact_id)
            edit_patch = self._feature07_edit_patch(artifact, analysis, edit_kind, target, value)
            self.store.update_artifact_feature07(session_id, artifact_id, edit_patch)
            command = self.store.append_operation_log(
                session_id,
                tool_name="edit_artifact_metadata",
                artifact_id=artifact_id,
                action=edit_kind,
                details={"target": target, "value": value},
            )
            updated_artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            return {
                "artifact": self._artifact_reference(updated_artifact),
                "edit_kind": edit_kind,
                "target": target,
                "value": value,
                "command_log_entry": command,
            }

        return self._respond(
            "edit_artifact_metadata",
            parameters,
            operation,
            suggested_next_actions=lambda result: self._metadata_follow_up_actions(session_id, artifact_id, result),
        )

    def import_type_definitions(self, session_id: str, artifact_id: str, source_format: str, source_text: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "source_format": source_format, "source_text": source_text}

        def operation() -> dict[str, Any]:
            imported = import_type_definitions_report(source_text, source_format=source_format)
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            feature07 = json_clone(artifact.get("feature07", {}))
            edits = feature07.setdefault("edits", {})
            existing_imports = edits.setdefault("type_imports", [])
            existing_imports.append(imported)
            named_types = edits.setdefault("named_types", {"structs": {}, "enums": {}, "typedefs": {}})
            for kind in ("structs", "enums", "typedefs"):
                named_types.setdefault(kind, {}).update(json_clone(imported.get("named_types", {}).get(kind, {})))
            if imported.get("function_signatures"):
                function_types = edits.setdefault("function_types", {})
                for item in imported["function_signatures"]:
                    function_types[str(item["name"])] = {"signature": item["signature"], "source": "import"}
            self.store.update_artifact_feature07(session_id, artifact_id, {"edits": edits})
            command = self.store.append_operation_log(
                session_id,
                tool_name="import_type_definitions",
                artifact_id=artifact_id,
                action="import_types",
                details={"source_format": source_format, "summary": {"function_signatures": len(imported.get("function_signatures", []))}},
            )
            return {
                "artifact": self._artifact_reference(artifact),
                "imported": imported,
                "command_log_entry": command,
            }

        return self._respond(
            "import_type_definitions",
            parameters,
            operation,
            suggested_next_actions=lambda result: self._metadata_follow_up_actions(session_id, artifact_id, result),
        )

    def export_command_log(self, session_id: str, format: str = "json", output_path: str | None = None) -> dict[str, Any]:
        parameters = {"session_id": session_id, "format": format, "output_path": output_path}

        def operation() -> dict[str, Any]:
            entries = self.store.list_operation_log(session_id)
            rendered = render_command_log(entries, format_name=format)
            output = self._write_rendered_output(rendered, output_path=output_path, purpose="Command log export")
            return {"session": self.store.load_session(session_id=session_id)["session"], "command_log": rendered, "output": output}

        return self._respond(
            "export_command_log",
            parameters,
            operation,
            suggested_next_actions=lambda result: self._export_command_log_actions(session_id),
        )

    def export_analysis_report(self, session_id: str, artifact_id: str, format: str = "json", output_path: str | None = None) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "format": format, "output_path": output_path}

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            parsed = parse_feature07_context(artifact["canonical_path"])["parsed"]
            analysis = self._maybe_load_analysis(session_id, artifact_id)
            report = build_analysis_report(
                artifact,
                parsed=parsed,
                analysis=analysis,
                feature07=artifact.get("feature07", {}),
                format_name=format,
            )
            output = self._write_rendered_output(report, output_path=output_path, purpose="Analysis report export")
            return {"artifact": self._artifact_reference(artifact), "report": report, "output": output}

        return self._respond(
            "export_analysis_report",
            parameters,
            operation,
            suggested_next_actions=lambda result: [
                {
                    "tool": "export_command_log",
                    "parameters": {"session_id": session_id, "format": "text"},
                    "rationale": "Capture the command trail alongside the exported analysis report.",
                }
            ],
        )

    def list_artifact_dependencies(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id}

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            parsed = parse_feature07_context(artifact["canonical_path"])["parsed"]
            analysis = self._maybe_load_analysis(session_id, artifact_id)
            return {
                "dependencies": list_dependencies(
                    artifact,
                    parsed=parsed,
                    analysis=analysis,
                    relationships=self._artifact_relationships(session_id, artifact_id),
                )
            }

        return self._respond(
            "list_artifact_dependencies",
            parameters,
            operation,
            suggested_next_actions=lambda result: [
                {
                    "tool": "correlate_session_artifacts",
                    "parameters": {"session_id": session_id, "artifact_ids": [artifact_id]},
                    "rationale": "Expand dependency hints into a cross-artifact comparison once related binaries are attached.",
                }
            ],
        )

    def correlate_session_artifacts(
        self,
        session_id: str,
        artifact_ids: list[str] | None = None,
        cursor: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_ids": artifact_ids, "cursor": cursor, "limit": limit}

        def operation() -> dict[str, Any]:
            artifacts = self.store.list_artifacts(session_id, cursor=None, limit=None)["items"]
            if artifact_ids:
                selected = [item for item in artifacts if item["artifact_id"] in set(artifact_ids)]
            else:
                selected = artifacts
            records = []
            for summary in selected:
                artifact = self.store.get_artifact_record(session_id, artifact_id=summary["artifact_id"])
                parsed = parse_feature07_context(artifact["canonical_path"])["parsed"]
                analysis = self._maybe_load_analysis(session_id, artifact["artifact_id"])
                records.append({"artifact": artifact, "parsed": parsed, "analysis": analysis})
            correlations = correlate_artifacts(records)
            paged = self._page_items(correlations["items"], cursor=cursor, limit=limit)
            return {
                "artifacts": [self._artifact_reference(item["artifact"]) for item in records],
                "correlations": {
                    "items": paged["items"],
                    "page": paged["page"],
                    "total": len(correlations["items"]),
                },
            }

        return self._respond(
            "correlate_session_artifacts",
            parameters,
            operation,
            partial_result=lambda result: result["correlations"]["page"]["truncated"],
            suggested_next_actions=lambda result: self._correlation_follow_up_actions(session_id, result),
        )

    def diff_artifacts(self, session_id: str, left_artifact_id: str, right_artifact_id: str) -> dict[str, Any]:
        parameters = {"session_id": session_id, "left_artifact_id": left_artifact_id, "right_artifact_id": right_artifact_id}

        def operation() -> dict[str, Any]:
            left_artifact = self.store.get_artifact_record(session_id, artifact_id=left_artifact_id)
            right_artifact = self.store.get_artifact_record(session_id, artifact_id=right_artifact_id)
            left = {
                "artifact": left_artifact,
                "parsed": parse_feature07_context(left_artifact["canonical_path"])["parsed"],
                "analysis": self._maybe_load_analysis(session_id, left_artifact_id),
            }
            right = {
                "artifact": right_artifact,
                "parsed": parse_feature07_context(right_artifact["canonical_path"])["parsed"],
                "analysis": self._maybe_load_analysis(session_id, right_artifact_id),
            }
            return {"diff": diff_artifacts(left, right)}

        return self._respond(
            "diff_artifacts",
            parameters,
            operation,
            suggested_next_actions=lambda result: [
                {
                    "tool": "export_analysis_report",
                    "parameters": {"session_id": session_id, "artifact_id": left_artifact_id, "format": "json"},
                    "rationale": "Preserve the left-side artifact state as a reusable comparison baseline.",
                },
                {
                    "tool": "export_analysis_report",
                    "parameters": {"session_id": session_id, "artifact_id": right_artifact_id, "format": "json"},
                    "rationale": "Preserve the right-side artifact state as a reusable comparison target.",
                },
            ],
        )

    def ingest_and_triage_artifact(
        self,
        session_id: str,
        path: str,
        display_name: str | None = None,
        hints: dict[str, Any] | None = None,
        analyze: bool = False,
        verbosity: str = "brief",
        token_budget_hint: int | None = None,
        include_next_actions: bool = True,
        include_raw_sections: bool = False,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "path": path,
            "display_name": display_name,
            "hints": hints,
            "analyze": analyze,
            "verbosity": verbosity,
            "token_budget_hint": token_budget_hint,
            "include_next_actions": include_next_actions,
            "include_raw_sections": include_raw_sections,
        }
        profile = self._brief_profile(
            verbosity=verbosity,
            token_budget_hint=token_budget_hint,
            include_next_actions=include_next_actions,
            include_raw_sections=include_raw_sections,
        )

        def operation() -> dict[str, Any]:
            attached = self.store.add_artifact(session_id, path, display_name)
            artifact = self.store.get_artifact_record(session_id, artifact_id=attached["artifact_id"])
            triage = self.parser_sandbox.triage_artifact(
                artifact["canonical_path"],
                hints=hints,
                string_preview_limit=int(profile["string_limit"]),
            )["result"]
            analysis_request = {"requested": bool(analyze), "status": artifact.get("analysis", {}).get("status", "not_started")}
            if analyze:
                job = self.jobs.start_artifact_analysis(session_id, artifact["artifact_id"], hints or {})
                analysis_request = {
                    "requested": True,
                    "status": job["status"],
                    "job": {
                        "job_id": job["job_id"],
                        "status": job["status"],
                    },
                }
            payload = {
                "artifact": self._artifact_reference(artifact),
                "triage_brief": self._compact_triage_brief(artifact, triage, profile, focus="general"),
                "analysis": analysis_request,
                "response_profile": profile_summary(profile),
            }
            if profile["include_raw_sections"]:
                payload["raw_sections"] = {
                    "triage": self._raw_triage_section(triage, profile),
                }
            return payload

        return self._respond(
            "ingest_and_triage_artifact",
            parameters,
            operation,
            suggested_next_actions=(lambda result: self._ingest_brief_next_actions(session_id, result)) if profile["include_next_actions"] else None,
        )

    def analyze_and_summarize(
        self,
        session_id: str,
        artifact_id: str,
        focus: str = "general",
        wait_timeout_seconds: float = 15.0,
        verbosity: str = "brief",
        token_budget_hint: int | None = None,
        include_next_actions: bool = True,
        include_raw_sections: bool = False,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "focus": focus,
            "wait_timeout_seconds": wait_timeout_seconds,
            "verbosity": verbosity,
            "token_budget_hint": token_budget_hint,
            "include_next_actions": include_next_actions,
            "include_raw_sections": include_raw_sections,
        }
        profile = self._brief_profile(
            verbosity=verbosity,
            token_budget_hint=token_budget_hint,
            include_next_actions=include_next_actions,
            include_raw_sections=include_raw_sections,
        )

        def operation() -> dict[str, Any]:
            focus_name = self._normalize_focus(focus)
            ensured = self._ensure_analysis_ready(session_id, artifact_id, wait_timeout_seconds=max(0.0, float(wait_timeout_seconds)))
            if ensured["analysis"] is None:
                triage = self.parser_sandbox.triage_artifact(
                    ensured["artifact"]["canonical_path"],
                    string_preview_limit=int(profile["string_limit"]),
                )["result"]
                payload = {
                    "artifact": self._artifact_reference(ensured["artifact"]),
                    "focus": focus_name,
                    "analysis_status": ensured["analysis_status"],
                    "analysis_job": ensured.get("job"),
                    "summary": self._compact_triage_brief(ensured["artifact"], triage, profile, focus=focus_name),
                    "response_profile": profile_summary(profile),
                }
                if profile["include_raw_sections"]:
                    payload["raw_sections"] = {"triage": self._raw_triage_section(triage, profile)}
                return payload
            artifact = ensured["artifact"]
            analysis = ensured["analysis"]
            parsed = parse_feature07_context(artifact["canonical_path"])["parsed"]
            synopsis = self.parser_sandbox.build_analysis_synopsis(analysis, artifact_summary=self._artifact_reference(artifact))
            enriched = self._enrich_analysis_synopsis(session_id, artifact_id, artifact, synopsis["result"])
            payload = {
                "artifact": self._artifact_reference(artifact),
                "focus": focus_name,
                "analysis_status": "completed",
                "analysis_job": ensured.get("job"),
                "summary": self._compact_analysis_brief(session_id, artifact_id, artifact, parsed, analysis, enriched, profile, focus=focus_name),
                "response_profile": profile_summary(profile),
            }
            if profile["include_raw_sections"]:
                payload["raw_sections"] = self._analysis_raw_sections(analysis, enriched, profile)
            return payload

        return self._respond(
            "analyze_and_summarize",
            parameters,
            operation,
            partial_result=lambda result: result["analysis_status"] != "completed",
            suggested_next_actions=(lambda result: self._analysis_and_summarize_next_actions(session_id, artifact_id, result)) if profile["include_next_actions"] else None,
        )

    def hunt_interesting_regions(
        self,
        session_id: str,
        artifact_id: str,
        objective: str = "general",
        limit: int = 8,
        verbosity: str = "brief",
        token_budget_hint: int | None = None,
        include_next_actions: bool = True,
        include_raw_sections: bool = False,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "objective": objective,
            "limit": limit,
            "verbosity": verbosity,
            "token_budget_hint": token_budget_hint,
            "include_next_actions": include_next_actions,
            "include_raw_sections": include_raw_sections,
        }
        profile = self._brief_profile(
            verbosity=verbosity,
            token_budget_hint=token_budget_hint,
            include_next_actions=include_next_actions,
            include_raw_sections=include_raw_sections,
        )

        def operation() -> dict[str, Any]:
            objective_name = self._normalize_focus(objective)
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            parsed = parse_feature07_context(artifact["canonical_path"])["parsed"]
            shortlist = self._build_interesting_region_shortlist(
                artifact,
                parsed,
                analysis,
                profile,
                objective=objective_name,
                limit=max(1, int(limit)),
            )
            payload = {
                "artifact": self._artifact_reference(artifact),
                "objective": objective_name,
                "interesting_regions": shortlist,
                "response_profile": profile_summary(profile),
            }
            if profile["include_raw_sections"]:
                payload["raw_sections"] = {
                    "top_functions": shortlist["top_functions"]["items"],
                    "suspicious_strings": shortlist["suspicious_strings"]["items"],
                }
            return payload

        return self._respond(
            "hunt_interesting_regions",
            parameters,
            operation,
            suggested_next_actions=(lambda result: self._hunt_next_actions(session_id, artifact_id, result)) if profile["include_next_actions"] else None,
        )

    def trace_capability(
        self,
        session_id: str,
        artifact_id: str,
        target: dict[str, Any],
        depth: int = 1,
        verbosity: str = "brief",
        token_budget_hint: int | None = None,
        include_next_actions: bool = True,
        include_raw_sections: bool = False,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "target": target,
            "depth": depth,
            "verbosity": verbosity,
            "token_budget_hint": token_budget_hint,
            "include_next_actions": include_next_actions,
            "include_raw_sections": include_raw_sections,
        }
        profile = self._brief_profile(
            verbosity=verbosity,
            token_budget_hint=token_budget_hint,
            include_next_actions=include_next_actions,
            include_raw_sections=include_raw_sections,
        )

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            function = self._resolve_function_target(analysis, target)
            detail = self._function_detail(analysis, function)
            neighborhood = navigate_function_neighborhood(analysis, function, depth=max(1, int(depth)), radius=max(1, int(depth)))
            xrefs = [item for item in analysis.get("xrefs", []) if item.get("target_function_id") == function.get("function_id")]
            payload = {
                "artifact": self._artifact_reference(artifact),
                "function": compact_function(function, profile=profile),
                "trace": {
                    "callers": compact_page(
                        [compact_xref(item, profile=profile) for item in xrefs],
                        int(profile["xref_limit"]),
                    ),
                    "neighborhood": {
                        "callers": compact_page(
                            [compact_function(item, profile=profile) for item in neighborhood.get("callers", [])],
                            int(profile["function_limit"]),
                        ),
                        "callees": compact_page(
                            [compact_function(item, profile=profile) for item in neighborhood.get("callees", [])],
                            int(profile["function_limit"]),
                        ),
                        "nearby_functions": compact_page(
                            [compact_function(item, profile=profile) for item in neighborhood.get("nearby_functions", [])],
                            int(profile["function_limit"]),
                        ),
                        "nearby_strings": compact_page(
                            [compact_string(item, profile=profile) for item in neighborhood.get("nearby_strings", [])],
                            int(profile["string_limit"]),
                        ),
                    },
                    "variables": {
                        "arguments": detail.get("variables", {}).get("arguments", [])[: int(profile["function_limit"])],
                        "locals": detail.get("variables", {}).get("locals", [])[: int(profile["function_limit"])],
                        "globals": detail.get("variables", {}).get("globals", [])[: int(profile["function_limit"])],
                    },
                    "constant_hints": detail.get("constant_propagation", {}).get("immediates", [])[: int(profile["match_limit"])],
                },
                "response_profile": profile_summary(profile),
            }
            if profile["include_raw_sections"]:
                payload["raw_sections"] = {
                    "instruction_preview": [
                        compact_instruction(item, profile=profile)
                        for item in detail.get("instructions", [])[: int(profile["instruction_limit"])]
                    ]
                }
            return payload

        return self._respond(
            "trace_capability",
            parameters,
            operation,
            suggested_next_actions=(lambda result: self._trace_next_actions(session_id, artifact_id, result)) if profile["include_next_actions"] else None,
        )

    def prepare_patch_plan(
        self,
        session_id: str,
        artifact_id: str,
        objective: str,
        target: dict[str, Any] | None = None,
        min_code_cave_size: int = 32,
        verbosity: str = "brief",
        token_budget_hint: int | None = None,
        include_next_actions: bool = True,
        include_raw_sections: bool = False,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "objective": objective,
            "target": target,
            "min_code_cave_size": min_code_cave_size,
            "verbosity": verbosity,
            "token_budget_hint": token_budget_hint,
            "include_next_actions": include_next_actions,
            "include_raw_sections": include_raw_sections,
        }
        profile = self._brief_profile(
            verbosity=verbosity,
            token_budget_hint=token_budget_hint,
            include_next_actions=include_next_actions,
            include_raw_sections=include_raw_sections,
        )

        def operation() -> dict[str, Any]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            analysis = self._maybe_load_analysis(session_id, artifact_id)
            parsed = parse_feature07_context(artifact["canonical_path"])["parsed"]
            function = None
            if target and analysis is not None:
                function = self._resolve_function_target(analysis, target)
            instruction_mode = None
            if analysis is not None:
                instruction_mode = json_clone(analysis.get("capabilities", {}).get("instruction_set_modes", {}))
                if instruction_mode:
                    instruction_mode["current"] = instruction_mode.get("override") or instruction_mode.get("default")
            caves = discover_code_caves(artifact["canonical_path"], min_size=max(1, int(min_code_cave_size)))
            patch_points = self._candidate_patch_points(parsed, analysis, function, caves, profile)
            payload = {
                "artifact": self._artifact_reference(artifact),
                "objective": objective.strip() or "general",
                "target_function": compact_function(function, profile=profile) if function else None,
                "instruction_mode": instruction_mode,
                "patch_plan": {
                    "candidate_patch_points": patch_points,
                    "code_caves": compact_page(
                        [compact_code_cave(item, profile=profile) for item in caves.get("items", [])],
                        int(profile["code_cave_limit"]),
                    ),
                    "warnings": self._patch_plan_warnings(analysis, function, caves),
                },
                "response_profile": profile_summary(profile),
            }
            if profile["include_raw_sections"] and function is not None and analysis is not None:
                detail = self._function_detail(analysis, function)
                payload["raw_sections"] = {
                    "target_instruction_preview": [
                        compact_instruction(item, profile=profile)
                        for item in detail.get("instructions", [])[: int(profile["instruction_limit"])]
                    ]
                }
            return payload

        return self._respond(
            "prepare_patch_plan",
            parameters,
            operation,
            suggested_next_actions=(lambda result: self._patch_plan_next_actions(session_id, artifact_id, result)) if profile["include_next_actions"] else None,
        )

    def artifact_relationship_brief(
        self,
        session_id: str,
        artifact_id: str,
        focus: str = "general",
        verbosity: str = "brief",
        token_budget_hint: int | None = None,
        include_next_actions: bool = True,
        include_raw_sections: bool = False,
    ) -> dict[str, Any]:
        parameters = {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "focus": focus,
            "verbosity": verbosity,
            "token_budget_hint": token_budget_hint,
            "include_next_actions": include_next_actions,
            "include_raw_sections": include_raw_sections,
        }
        profile = self._brief_profile(
            verbosity=verbosity,
            token_budget_hint=token_budget_hint,
            include_next_actions=include_next_actions,
            include_raw_sections=include_raw_sections,
        )

        def operation() -> dict[str, Any]:
            focus_name = self._normalize_focus(focus)
            artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
            parsed = parse_feature07_context(artifact["canonical_path"])["parsed"]
            analysis = self._maybe_load_analysis(session_id, artifact_id)
            dependencies = list_dependencies(
                artifact,
                parsed=parsed,
                analysis=analysis,
                relationships=self._artifact_relationships(session_id, artifact_id),
            )
            records = self._session_artifact_records(session_id)
            correlations = correlate_artifacts(records)
            brief = self._relationship_brief(
                session_id,
                artifact_id,
                artifact,
                dependencies,
                correlations["items"],
                profile,
                focus=focus_name,
            )
            payload = {
                "artifact": self._artifact_reference(artifact),
                "focus": focus_name,
                "relationship_brief": brief,
                "response_profile": profile_summary(profile),
            }
            if profile["include_raw_sections"]:
                payload["raw_sections"] = {
                    "dependency_summary": dependencies,
                }
            return payload

        return self._respond(
            "artifact_relationship_brief",
            parameters,
            operation,
            suggested_next_actions=(lambda result: self._relationship_next_actions(session_id, artifact_id, result)) if profile["include_next_actions"] else None,
        )

    def _brief_profile(
        self,
        *,
        verbosity: str,
        token_budget_hint: int | None,
        include_next_actions: bool,
        include_raw_sections: bool,
    ) -> dict[str, Any]:
        try:
            return normalize_brief_options(
                verbosity=verbosity,
                token_budget_hint=token_budget_hint,
                include_next_actions=include_next_actions,
                include_raw_sections=include_raw_sections,
            )
        except (TypeError, ValueError) as exc:
            raise StructuredToolError(
                "invalid_request",
                "composite_brief_options_invalid",
                str(exc),
            ) from exc

    def _normalize_focus(self, focus: str | None) -> str:
        normalized = str(focus or "general").strip().lower() or "general"
        if normalized not in {"general", "malware", "patching", "diffing", "firmware", "extraction"}:
            raise StructuredToolError(
                "invalid_request",
                "composite_focus_invalid",
                "focus must be one of: general, malware, patching, diffing, firmware, extraction.",
            )
        return normalized

    def _raw_triage_section(self, triage: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        return {
            "file_type": json_clone(triage.get("file_type", {})),
            "taxonomy": json_clone(triage.get("taxonomy", {})),
            "security_mitigations": json_clone(triage.get("security_mitigations", {})),
            "strings_preview": triage.get("strings_preview", {}).get("items", [])[: int(profile["string_limit"])],
        }

    def _compact_triage_brief(self, artifact: dict[str, Any], triage: dict[str, Any], profile: dict[str, Any], *, focus: str) -> dict[str, Any]:
        strings = [
            compact_string(item, profile=profile)
            for item in triage.get("strings_preview", {}).get("items", [])[: int(profile["string_limit"])]
        ]
        file_type = triage.get("file_type", {})
        brief = {
            "artifact": self._artifact_reference(artifact),
            "file_type": {
                "format": file_type.get("format"),
                "architecture": file_type.get("architecture"),
                "bitness": file_type.get("bitness"),
                "endianness": file_type.get("endianness"),
            },
            "taxonomy": json_clone(triage.get("taxonomy", {})),
            "hashes": {"sha256": triage.get("hashes", {}).get("sha256")},
            "layout": {
                "section_count": len(triage.get("layout", {}).get("sections", [])),
                "segment_count": len(triage.get("layout", {}).get("segments", [])),
            },
            "children_preview": {
                "count": triage.get("children_preview", {}).get("total", 0),
            },
            "strings_preview": compact_page(strings, int(profile["string_limit"])),
        }
        if focus in {"malware", "extraction"}:
            brief["signatures"] = triage.get("signatures", [])[: int(profile["match_limit"])]
        if focus == "firmware":
            brief["deep_inspection"] = {
                "available_keys": sorted(triage.get("deep_inspection", {}).keys())[: int(profile["match_limit"])],
                "discrepancy_count": len(triage.get("discrepancies", [])),
            }
        return brief

    def _ensure_analysis_ready(self, session_id: str, artifact_id: str, *, wait_timeout_seconds: float) -> dict[str, Any]:
        artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
        if artifact.get("analysis", {}).get("status") == "completed":
            loaded_artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            return {
                "artifact": loaded_artifact,
                "analysis": analysis,
                "job": None,
                "analysis_status": "completed",
            }
        last_job_id = artifact.get("analysis", {}).get("last_job_id")
        job = None
        if last_job_id:
            try:
                job = self.jobs.get_job(last_job_id)
            except StructuredToolError:
                job = None
        if job is None or job["status"] in {"failed", "cancelled"}:
            job = self.jobs.start_artifact_analysis(session_id, artifact_id, {})
        deadline = time.time() + max(0.0, wait_timeout_seconds)
        while job["status"] not in {"completed", "failed", "cancelled"} and time.time() < deadline:
            time.sleep(0.05)
            job = self.jobs.get_job(job["job_id"])
        artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
        if job["status"] == "completed":
            loaded_artifact, analysis = self._load_analysis_context(session_id, artifact_id)
            return {
                "artifact": loaded_artifact,
                "analysis": analysis,
                "job": {"job_id": job["job_id"], "status": job["status"]},
                "analysis_status": "completed",
            }
        return {
            "artifact": artifact,
            "analysis": None,
            "job": {"job_id": job["job_id"], "status": job["status"]},
            "analysis_status": job["status"],
        }

    def _compact_analysis_brief(
        self,
        session_id: str,
        artifact_id: str,
        artifact: dict[str, Any],
        parsed: dict[str, Any],
        analysis: dict[str, Any],
        synopsis: dict[str, Any],
        profile: dict[str, Any],
        *,
        focus: str,
    ) -> dict[str, Any]:
        top_functions = compact_page(
            [compact_function(item, profile=profile) for item in analysis.get("functions", [])],
            int(profile["function_limit"]),
        )
        top_strings = compact_page(
            [compact_string(item, profile=profile) for item in analysis.get("strings", [])],
            int(profile["string_limit"]),
        )
        imports = compact_page(
            [
                compact_symbol(item, profile=profile)
                for item in analysis.get("symbols", [])
                if item.get("kind") == "import"
            ],
            int(profile["import_limit"]),
        )
        brief = {
            "artifact": self._artifact_reference(artifact),
            "backend": analysis.get("backend"),
            "summary": json_clone(analysis.get("summary", {})),
            "analysis_state": json_clone(synopsis.get("analysis_state", {})),
            "top_functions": top_functions,
            "interesting_strings": top_strings,
            "imports": imports,
            "outstanding_unknowns": synopsis.get("outstanding_unknowns", [])[: int(profile["match_limit"])],
        }
        if focus == "malware":
            brief["focus_notes"] = {
                "suspicious_strings": self._suspicious_string_page(analysis.get("strings", []), profile),
                "crypto_hints": compact_page(self._compact_crypto_hints(analysis, profile), int(profile["match_limit"])),
                "library_hints": recognize_library_code_report(parsed, analysis).get("libraries", [])[: int(profile["match_limit"])],
            }
        elif focus == "patching":
            caves = discover_code_caves(artifact["canonical_path"], min_size=32)
            brief["focus_notes"] = {
                "instruction_mode": json_clone(analysis.get("capabilities", {}).get("instruction_set_modes", {})),
                "code_caves": compact_page(
                    [compact_code_cave(item, profile=profile) for item in caves.get("items", [])],
                    int(profile["code_cave_limit"]),
                ),
            }
        elif focus == "diffing":
            brief["focus_notes"] = self._relationship_brief(
                session_id,
                artifact_id,
                artifact,
                list_dependencies(
                    artifact,
                    parsed=parsed,
                    analysis=analysis,
                    relationships=self._artifact_relationships(session_id, artifact_id),
                ),
                correlate_artifacts(self._session_artifact_records(session_id)).get("items", []),
                profile,
                focus=focus,
            )
        elif focus == "firmware":
            brief["focus_notes"] = {
                "format": parsed.get("file_type", {}).get("format"),
                "section_names": [item.get("name") for item in parsed.get("sections", [])[: int(profile["match_limit"])]],
                "discrepancy_count": len(parsed.get("discrepancies", [])),
            }
        elif focus == "extraction":
            relationships = self._artifact_relationships(session_id, artifact_id)
            brief["focus_notes"] = {
                "parent_count": len(relationships["parents"]),
                "child_count": len(relationships["children"]),
                "children": [
                    item["artifact"]
                    for item in relationships["children"][: int(profile["match_limit"])]
                ],
            }
        return brief

    def _analysis_raw_sections(self, analysis: dict[str, Any], synopsis: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        return {
            "highlights": {
                "top_functions": [
                    compact_function(item, profile=profile)
                    for item in analysis.get("functions", [])[: int(profile["function_limit"])]
                ],
                "interesting_strings": [
                    compact_string(item, profile=profile)
                    for item in analysis.get("strings", [])[: int(profile["string_limit"])]
                ],
            },
            "analysis_state": json_clone(synopsis.get("analysis_state", {})),
        }

    def _build_interesting_region_shortlist(
        self,
        artifact: dict[str, Any],
        parsed: dict[str, Any],
        analysis: dict[str, Any],
        profile: dict[str, Any],
        *,
        objective: str,
        limit: int,
    ) -> dict[str, Any]:
        prioritized = filter_and_prioritize_functions(analysis, max_items=max(limit, int(profile["function_limit"])))
        if objective == "patching":
            prioritized = [item for item in prioritized if "runtime_init" not in set(item.get("classification_tags", []))]
        top_functions = compact_page(
            [compact_function(item, profile=profile) for item in prioritized],
            min(limit, int(profile["function_limit"])),
        )
        suspicious_strings = self._suspicious_string_page(analysis.get("strings", []), profile, limit=min(limit, int(profile["string_limit"])))
        imports = compact_page(
            [
                compact_symbol(item, profile=profile)
                for item in analysis.get("symbols", [])
                if item.get("kind") == "import"
            ],
            min(limit, int(profile["import_limit"])),
        )
        return {
            "top_functions": top_functions,
            "suspicious_strings": suspicious_strings,
            "imports": imports,
            "crypto_hints": compact_page(self._compact_crypto_hints(analysis, profile), min(limit, int(profile["match_limit"]))),
            "library_hints": compact_page(
                recognize_library_code_report(parsed, analysis).get("libraries", []),
                min(limit, int(profile["match_limit"])),
            ),
            "artifact_summary": {
                "artifact_id": artifact["artifact_id"],
                "display_name": artifact["display_name"],
                "analysis_status": artifact.get("analysis", {}).get("status"),
            },
        }

    def _suspicious_string_page(self, strings: list[dict[str, Any]], profile: dict[str, Any], *, limit: int | None = None) -> dict[str, Any]:
        markers = (
            "http",
            "https",
            "cmd",
            "shell",
            "exec",
            "powershell",
            "/bin/",
            "secret",
            "token",
            "key",
            "xor",
            "encrypt",
            "decrypt",
            "loadlibrary",
            "getprocaddress",
        )
        ranked = []
        for item in strings:
            value = str(item.get("value") or "")
            lowered = value.lower()
            score = sum(1 for marker in markers if marker in lowered)
            if score <= 0:
                continue
            ranked.append((score, len(value), item))
        ranked.sort(key=lambda entry: (-entry[0], -entry[1], str(entry[2].get("value") or "")))
        compacted = [compact_string(item, profile=profile) for _, _, item in ranked]
        return compact_page(compacted, limit or int(profile["string_limit"]))

    def _compact_crypto_hints(self, analysis: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, Any]]:
        hints = detect_crypto_constants_report(analysis).get("items", [])
        items = []
        for hint in hints[: int(profile["match_limit"])]:
            items.append(
                {
                    "name": hint.get("name"),
                    "family": hint.get("family"),
                    "function_id": hint.get("function_id"),
                    "function_name": truncate_text(hint.get("function_name"), int(profile["char_limit"])),
                    "instruction_address": hint.get("instruction_address"),
                }
            )
        return items

    def _resolve_function_target(self, analysis: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(target, dict):
            raise StructuredToolError("invalid_request", "trace_target_invalid", "target must be a JSON object.")
        return self._resolve_function_from_analysis(
            analysis,
            function_id=target.get("function_id"),
            name=target.get("name"),
            address=self._normalize_numeric_value(target["address"]) if target.get("address") is not None else None,
        )

    def _candidate_patch_points(
        self,
        parsed: dict[str, Any],
        analysis: dict[str, Any] | None,
        function: dict[str, Any] | None,
        caves: dict[str, Any],
        profile: dict[str, Any],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if function is not None:
            items.append(
                {
                    "kind": "function_entry",
                    "input_kind": "virtual_address",
                    "value": function["address"],
                    "name": function.get("demangled_name") or function.get("name"),
                    "rationale": "Function entry is the lowest-friction control-flow redirection point.",
                }
            )
        if analysis is not None and function is not None:
            detail = self._function_detail(analysis, function)
            branchy = [item for item in detail.get("instructions", []) if item.get("mnemonic") in {"jmp", "je", "jne", "b", "bl", "ret"}]
            for instruction in branchy[:1]:
                items.append(
                    {
                        "kind": "branch_site",
                        "input_kind": "virtual_address",
                        "value": instruction["address"],
                        "text": truncate_text(f"{instruction.get('mnemonic')} {instruction.get('operand_text') or ''}".strip(), int(profile["char_limit"])),
                        "rationale": "Existing control-transfer sites are natural patch anchors for bypasses and detours.",
                    }
                )
        for cave in caves.get("items", [])[: int(profile["code_cave_limit"])]:
            items.append(
                {
                    "kind": "code_cave",
                    "input_kind": "file_offset",
                    "value": cave.get("file_offset"),
                    "virtual_address": cave.get("virtual_address"),
                    "size": cave.get("size"),
                    "section": cave.get("section_name"),
                    "rationale": "Slack space is suitable for stubs or data without overwriting existing instructions.",
                }
            )
        return items

    def _patch_plan_warnings(self, analysis: dict[str, Any] | None, function: dict[str, Any] | None, caves: dict[str, Any]) -> list[str]:
        warnings = []
        if analysis is None:
            warnings.append("Analysis is not completed, so the patch plan is based on file structure rather than recovered semantics.")
        if function is None:
            warnings.append("No target function was provided, so patch points focus on generic file-level candidates.")
        if not caves.get("items"):
            warnings.append("No code caves matched the requested minimum size.")
        return warnings

    def _session_artifact_records(self, session_id: str) -> list[dict[str, Any]]:
        records = []
        for summary in self.store.list_artifacts(session_id, cursor=None, limit=None)["items"]:
            artifact = self.store.get_artifact_record(session_id, artifact_id=summary["artifact_id"])
            records.append(
                {
                    "artifact": artifact,
                    "parsed": parse_feature07_context(artifact["canonical_path"])["parsed"],
                    "analysis": self._maybe_load_analysis(session_id, artifact["artifact_id"]),
                }
            )
        return records

    def _relationship_brief(
        self,
        session_id: str,
        artifact_id: str,
        artifact: dict[str, Any],
        dependencies: dict[str, Any],
        correlations: list[dict[str, Any]],
        profile: dict[str, Any],
        *,
        focus: str,
    ) -> dict[str, Any]:
        relationships = self._artifact_relationships(session_id, artifact_id)
        correlation_hits = []
        diff_candidates = []
        seen_diff_candidates: set[str] = set()
        for item in relationships["parents"] + relationships["children"]:
            candidate = item["artifact"]
            candidate_id = candidate["artifact_id"]
            if candidate_id in seen_diff_candidates:
                continue
            seen_diff_candidates.add(candidate_id)
            diff_candidates.append(candidate)
        for item in correlations:
            involved = [entry["artifact_id"] for entry in item.get("artifacts", [])]
            if artifact_id not in involved:
                continue
            peers = [entry for entry in item.get("artifacts", []) if entry["artifact_id"] != artifact_id]
            if peers:
                peer_id = peers[0]["artifact_id"]
                if peer_id not in seen_diff_candidates:
                    seen_diff_candidates.add(peer_id)
                    diff_candidates.append(peers[0])
            correlation_hits.append(
                {
                    "kind": item.get("kind"),
                    "value": truncate_text(item.get("value"), int(profile["char_limit"])),
                    "count": item.get("count"),
                    "peers": peers[: int(profile["correlation_limit"])],
                }
            )
        brief = {
            "relationships": {
                "parents": [item["artifact"] for item in relationships["parents"][: int(profile["match_limit"])]],
                "children": [item["artifact"] for item in relationships["children"][: int(profile["match_limit"])]],
            },
            "dependencies": {
                "imports": compact_page(
                    [
                        {
                            "name": truncate_text(item.get("name"), int(profile["char_limit"])),
                            "kind": item.get("kind"),
                        }
                        for item in dependencies.get("imports", [])
                    ],
                    int(profile["import_limit"]),
                ),
                "related_artifacts": compact_page(
                    [json_clone(item["artifact"]) for item in relationships["children"] + relationships["parents"]],
                    int(profile["match_limit"]),
                ),
            },
            "correlations": compact_page(correlation_hits, int(profile["correlation_limit"])),
            "diff_candidates": compact_page(diff_candidates, int(profile["match_limit"])),
        }
        if focus == "extraction":
            brief["extraction"] = {
                "child_count": len(relationships["children"]),
                "parent_count": len(relationships["parents"]),
            }
        return brief

    def _ingest_brief_next_actions(self, session_id: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        artifact_id = result["artifact"]["artifact_id"]
        analysis = result.get("analysis", {})
        if analysis.get("requested") and analysis.get("job"):
            return [
                {
                    "tool": "get_job",
                    "parameters": {"job_id": analysis["job"]["job_id"]},
                    "rationale": "Poll the queued analysis job without reissuing lower-level setup calls.",
                }
            ]
        return [
            {
                "tool": "analyze_and_summarize",
                "parameters": {"session_id": session_id, "artifact_id": artifact_id, "focus": "general"},
                "rationale": "Move directly from intake into a compact post-analysis brief.",
            }
        ]

    def _analysis_and_summarize_next_actions(self, session_id: str, artifact_id: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        if result["analysis_status"] != "completed":
            job = result.get("analysis_job")
            if job:
                return [
                    {
                        "tool": "get_job",
                        "parameters": {"job_id": job["job_id"]},
                        "rationale": "The analysis job is still in flight.",
                    }
                ]
            return []
        focus = result.get("focus")
        if focus == "patching":
            return [
                {
                    "tool": "prepare_patch_plan",
                    "parameters": {"session_id": session_id, "artifact_id": artifact_id, "objective": "patching"},
                    "rationale": "Turn the analysis brief into concrete patch points.",
                }
            ]
        return [
            {
                "tool": "hunt_interesting_regions",
                "parameters": {"session_id": session_id, "artifact_id": artifact_id, "objective": focus or "general"},
                "rationale": "Promote the compact artifact summary into a ranked shortlist of next investigation targets.",
            }
        ]

    def _hunt_next_actions(self, session_id: str, artifact_id: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        functions = result.get("interesting_regions", {}).get("top_functions", {}).get("items", [])
        if not functions:
            return []
        return [
            {
                "tool": "trace_capability",
                "parameters": {
                    "session_id": session_id,
                    "artifact_id": artifact_id,
                    "target": {"function_id": functions[0]["function_id"]},
                },
                "rationale": "Expand the highest-ranked function into neighborhood and variable context.",
            }
        ]

    def _trace_next_actions(self, session_id: str, artifact_id: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        function_id = result.get("function", {}).get("function_id")
        if not function_id:
            return []
        return [
            {
                "tool": "disassemble_function",
                "parameters": {"session_id": session_id, "artifact_id": artifact_id, "function_id": function_id},
                "rationale": "Promote the compact trace into a full instruction view if needed.",
            }
        ]

    def _patch_plan_next_actions(self, session_id: str, artifact_id: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        patch_points = result.get("patch_plan", {}).get("candidate_patch_points", [])
        if not patch_points:
            return []
        first = patch_points[0]
        return [
            {
                "tool": "patch_artifact_bytes",
                "parameters": {
                    "session_id": session_id,
                    "artifact_id": artifact_id,
                    "input_kind": first["input_kind"],
                    "value": first["value"],
                    "bytes_hex": "90" if first["input_kind"] == "file_offset" else "00",
                },
                "rationale": "Use the first candidate patch point as a concrete follow-up mutation target.",
            }
        ]

    def _relationship_next_actions(self, session_id: str, artifact_id: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        diff_candidates = result.get("relationship_brief", {}).get("diff_candidates", {}).get("items", [])
        if diff_candidates:
            return [
                {
                    "tool": "diff_artifacts",
                    "parameters": {
                        "session_id": session_id,
                        "left_artifact_id": artifact_id,
                        "right_artifact_id": diff_candidates[0]["artifact_id"],
                    },
                    "rationale": "Promote the top relationship candidate into a structural diff.",
                }
            ]
        return [
            {
                "tool": "list_artifact_dependencies",
                "parameters": {"session_id": session_id, "artifact_id": artifact_id},
                "rationale": "Inspect the full dependency view when the brief summary is insufficient.",
            }
        ]

    def _enrich_analysis_synopsis(
        self,
        session_id: str,
        artifact_id: str,
        artifact: dict[str, Any],
        synopsis: dict[str, Any],
    ) -> dict[str, Any]:
        enriched = json_clone(synopsis)
        annotations = self.store.list_annotations(session_id, artifact_id=artifact_id, cursor=None, limit=None)["items"]
        relationships = self._artifact_relationships(session_id, artifact_id)
        operations = self.store.list_operation_log(session_id)
        artifact_operations = [item for item in operations if item.get("artifact_id") == artifact_id]
        extraction_ops = [item for item in artifact_operations if item["tool_name"] in {"extract_resources", "carve_embedded_artifacts"}]
        signature_ops = [
            item
            for item in artifact_operations
            if item["tool_name"] in {"scan_with_yara", "fingerprint_compiler_toolchain", "detect_packer", "detect_crypto_constants", "recognize_library_code"}
        ]
        patch_history = artifact.get("feature07", {}).get("patch_history", [])
        enriched["analysis_state"] = {
            "annotation_counts": {
                "total": len(annotations),
                "by_type": self._counts_by_key(annotations, "annotation_type"),
            },
            "relationships": {
                "parent_count": len(relationships["parents"]),
                "child_count": len(relationships["children"]),
            },
            "extraction_history": {
                "operations": [self._operation_summary(item) for item in extraction_ops[-5:]],
                "derived_artifact_count": len(relationships["children"]),
            },
            "matched_signatures": {
                "operations": [self._operation_summary(item) for item in signature_ops[-5:]],
                "count": len(signature_ops),
            },
            "patching": {
                "patch_count": len(patch_history),
                "recent_patches": json_clone(patch_history[-3:]),
            },
            "recent_operations": [self._operation_summary(item) for item in artifact_operations[-5:]],
        }
        unknowns = list(enriched.get("outstanding_unknowns", []))
        if not signature_ops:
            unknowns.append("No signature or recognition workflow has been recorded for this artifact yet.")
        if not extraction_ops and not relationships["children"]:
            unknowns.append("No extraction workflow has produced derived artifacts for this artifact yet.")
        if not annotations:
            unknowns.append("No analyst annotations have been recorded for this artifact yet.")
        enriched["outstanding_unknowns"] = unknowns
        return enriched

    def _analysis_synopsis_next_actions(self, session_id: str, artifact_id: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        actions = [
            {
                "tool": "list_artifact_functions",
                "parameters": {"session_id": session_id, "artifact_id": artifact_id},
                "rationale": "Functions are the primary next navigation surface after analysis completes.",
            }
        ]
        state = result.get("analysis_state", {})
        if not state.get("matched_signatures", {}).get("count"):
            actions.append(
                {
                    "tool": "scan_with_yara",
                    "parameters": {"session_id": session_id, "artifact_id": artifact_id},
                    "rationale": "Signature scanning is still missing from the current artifact synopsis.",
                }
            )
        if not state.get("extraction_history", {}).get("derived_artifact_count"):
            actions.append(
                {
                    "tool": "carve_embedded_artifacts",
                    "parameters": {"session_id": session_id, "artifact_id": artifact_id, "attach_to_session": True},
                    "rationale": "No derived artifacts are tracked yet; carving is the next step for overlays or containers.",
                }
            )
        if not state.get("patching", {}).get("patch_count"):
            actions.append(
                {
                    "tool": "find_code_caves",
                    "parameters": {"session_id": session_id, "artifact_id": artifact_id, "min_size": 32},
                    "rationale": "No patch workflow has been attempted yet; code-cave discovery is a safe next patching primitive.",
                }
            )
        return actions[:4]

    def _post_extraction_actions(self, session_id: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        attached = result.get("attached_artifacts") or []
        actions: list[dict[str, Any]] = []
        if attached:
            first = attached[0]["artifact_id"]
            actions.append(
                {
                    "tool": "list_artifact_dependencies",
                    "parameters": {"session_id": session_id, "artifact_id": first},
                    "rationale": "Review dependencies on the first derived artifact before widening analysis further.",
                }
            )
            actions.append(
                {
                    "tool": "correlate_session_artifacts",
                    "parameters": {"session_id": session_id, "artifact_ids": [result["artifact"]["artifact_id"], first]},
                    "rationale": "Correlate the parent artifact with the newly attached child artifact.",
                }
            )
        return actions

    def _patch_follow_up_actions(self, session_id: str, artifact_id: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        attached = result.get("attached_artifact")
        actions = []
        if attached:
            actions.append(
                {
                    "tool": "diff_artifacts",
                    "parameters": {"session_id": session_id, "left_artifact_id": artifact_id, "right_artifact_id": attached["artifact_id"]},
                    "rationale": "Compare the patched artifact against the source artifact to verify the intended mutation.",
                }
            )
            actions.append(
                {
                    "tool": "list_artifact_dependencies",
                    "parameters": {"session_id": session_id, "artifact_id": attached["artifact_id"]},
                    "rationale": "Review how the patched artifact relates to the rest of the session before deeper export or reporting.",
                }
            )
        return actions

    def _code_cave_actions(self, session_id: str, artifact_id: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        caves = result.get("code_caves", {}).get("items", [])
        if not caves:
            return []
        first = caves[0]
        return [
            {
                "tool": "patch_artifact_bytes",
                "parameters": {"session_id": session_id, "artifact_id": artifact_id, "input_kind": "file_offset", "value": first["file_offset"], "bytes_hex": "00"},
                "rationale": "Use the first discovered code cave as a candidate patch location without assuming the target ISA.",
            }
        ]

    def _metadata_follow_up_actions(self, session_id: str, artifact_id: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "tool": "get_analysis_synopsis",
                "parameters": {"session_id": session_id, "artifact_id": artifact_id},
                "rationale": "Refresh the compact artifact synopsis after changing metadata or importing types.",
            },
            {
                "tool": "export_analysis_report",
                "parameters": {"session_id": session_id, "artifact_id": artifact_id, "format": "json"},
                "rationale": "Export a machine-readable report containing the current naming and type overrides.",
            },
        ]

    def _export_command_log_actions(self, session_id: str) -> list[dict[str, Any]]:
        artifact_id = self._first_session_artifact_id(session_id)
        if artifact_id is None:
            return []
        return [
            {
                "tool": "export_analysis_report",
                "parameters": {"session_id": session_id, "artifact_id": artifact_id, "format": "json"},
                "rationale": "Pair the exported command trail with an artifact report for downstream review.",
            }
        ]

    def _correlation_follow_up_actions(self, session_id: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        artifacts = result.get("artifacts", [])
        if len(artifacts) < 2:
            return []
        return [
            {
                "tool": "diff_artifacts",
                "parameters": {
                    "session_id": session_id,
                    "left_artifact_id": artifacts[0]["artifact_id"],
                    "right_artifact_id": artifacts[1]["artifact_id"],
                },
                "rationale": "Follow a shared correlation by diffing two of the participating artifacts directly.",
            }
        ]

    def _first_session_artifact_id(self, session_id: str) -> str | None:
        artifacts = self.store.list_artifacts(session_id, cursor=None, limit=None)["items"]
        return artifacts[0]["artifact_id"] if artifacts else None

    def _counts_by_key(self, items: list[dict[str, Any]], key: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            value = str(item.get(key) or "unknown")
            counts[value] = counts.get(value, 0) + 1
        return counts

    def _operation_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation_id": item["operation_id"],
            "tool_name": item["tool_name"],
            "action": item["action"],
            "created_at": item["created_at"],
        }

    def _respond(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        operation: Callable[[], dict[str, Any]],
        *,
        suggested_next_actions: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
        partial_result: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        try:
            payload = operation()
            return success(
                tool_name,
                parameters,
                payload,
                partial=partial_result(payload) if partial_result else False,
                suggested_next_actions=suggested_next_actions(payload) if suggested_next_actions else None,
            )
        except StructuredToolError as exc:
            return failure(tool_name, parameters, exc)
        except Exception as exc:  # pragma: no cover - defensive normalization
            return failure(
                tool_name,
                parameters,
                StructuredToolError(
                    "backend_failure",
                    "unexpected_internal_error",
                    str(exc),
                ),
            )

    def _artifact_reference(self, artifact: dict[str, Any]) -> dict[str, Any]:
        return {
            "artifact_id": artifact["artifact_id"],
            "display_name": artifact["display_name"],
            "safe_display_name": artifact.get("safe_display_name"),
            "relationship": json_clone(artifact.get("relationship")),
            "canonical_path": artifact["canonical_path"],
            "relative_path": artifact["relative_path"],
            "size_bytes": artifact["size_bytes"],
            "analysis_generation": artifact["analysis_generation"],
            "analysis_status": artifact.get("analysis", {}).get("status"),
            "patch_count": len(artifact.get("feature07", {}).get("patch_history", [])),
        }

    def _resolve_patched_output_path(self, artifact: dict[str, Any], output_path: str | None, display_name: str | None) -> Path:
        if output_path:
            return self.security.resolve_output_file(output_path, purpose="Patched artifact output")
        safe_name = self.security.sanitize_filename(display_name or artifact["display_name"], default_stem="patched_artifact")["safe_name"]
        stem = Path(safe_name).stem or "patched_artifact"
        suffix = Path(safe_name).suffix
        derived = self.security.derive_output_file(
            subdir="feature07-patches",
            unsafe_name=f"{stem}.patched{suffix}" if suffix else f"{stem}.patched",
            default_stem="patched_artifact",
        )
        return Path(derived["path"])

    def _write_rendered_output(self, rendered: dict[str, Any], *, output_path: str | None, purpose: str) -> dict[str, Any] | None:
        if not output_path:
            return None
        output_file = self.security.resolve_output_file(output_path, purpose=purpose)
        if rendered["format"] == "text":
            output_file.write_text(rendered["text"], encoding="utf-8")
        else:
            output_file.write_text(json.dumps(rendered["json"], indent=2, sort_keys=True), encoding="utf-8")
        return {"path": str(output_file), "relative_path": self.security._relative_to_workspace(output_file)}

    def _feature07_edit_patch(
        self,
        artifact: dict[str, Any],
        analysis: dict[str, Any] | None,
        edit_kind: str,
        target: dict[str, Any],
        value: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(target, dict) or not isinstance(value, dict):
            raise StructuredToolError("invalid_request", "feature07_edit_invalid", "target and value must be JSON objects.")
        normalized = edit_kind.strip().lower()
        edits = {"edits": {}}
        if normalized == "function_name":
            function = self._resolve_function_from_analysis(
                analysis or {"functions": []},
                function_id=target.get("function_id"),
                name=target.get("name"),
                address=self._normalize_numeric_value(target["address"]) if target.get("address") is not None else None,
            )
            new_name = str(value.get("name", "")).strip()
            if not new_name:
                raise StructuredToolError("invalid_request", "function_name_required", "value.name is required for function_name edits.")
            edits["edits"]["function_names"] = {function["function_id"]: {"name": new_name}}
            return edits
        if normalized == "function_type":
            function = self._resolve_function_from_analysis(
                analysis or {"functions": []},
                function_id=target.get("function_id"),
                name=target.get("name"),
                address=self._normalize_numeric_value(target["address"]) if target.get("address") is not None else None,
            )
            signature = str(value.get("signature", "")).strip()
            if not signature:
                raise StructuredToolError("invalid_request", "function_signature_required", "value.signature is required for function_type edits.")
            edits["edits"]["function_types"] = {function["function_id"]: {"signature": signature, "source": "manual"}}
            return edits
        if normalized == "calling_convention":
            function = self._resolve_function_from_analysis(
                analysis or {"functions": []},
                function_id=target.get("function_id"),
                name=target.get("name"),
                address=self._normalize_numeric_value(target["address"]) if target.get("address") is not None else None,
            )
            convention = str(value.get("name", "")).strip()
            if not convention:
                raise StructuredToolError("invalid_request", "calling_convention_required", "value.name is required for calling_convention edits.")
            edits["edits"]["calling_conventions"] = {function["function_id"]: {"name": convention, "source": "manual_override"}}
            return edits
        if normalized in {"variable_name", "variable_type"}:
            function_key = str(target.get("function_id") or target.get("name") or target.get("address") or "").strip()
            variable_name = str(target.get("variable_name") or "").strip()
            if not function_key or not variable_name:
                raise StructuredToolError("invalid_request", "variable_target_invalid", "variable edits require function_id/name/address and variable_name.")
            storage = "variable_names" if normalized == "variable_name" else "variable_types"
            field_name = "name" if normalized == "variable_name" else "type"
            replacement = str(value.get(field_name, "")).strip()
            if not replacement:
                raise StructuredToolError("invalid_request", "variable_edit_value_required", f"value.{field_name} is required for {normalized} edits.")
            edits["edits"][storage] = {f"{function_key}:{variable_name}": {field_name: replacement}}
            return edits
        if normalized in {"global_name", "global_type"}:
            address = target.get("address")
            if address is None:
                raise StructuredToolError("invalid_request", "global_target_invalid", "global edits require target.address.")
            storage = "global_names" if normalized == "global_name" else "global_types"
            field_name = "name" if normalized == "global_name" else "type"
            replacement = str(value.get(field_name, "")).strip()
            if not replacement:
                raise StructuredToolError("invalid_request", "global_edit_value_required", f"value.{field_name} is required for {normalized} edits.")
            edits["edits"][storage] = {str(self._normalize_numeric_value(address)): {field_name: replacement}}
            return edits
        if normalized in {"struct", "enum", "typedef"}:
            type_name = str(target.get("name") or value.get("name") or "").strip()
            if not type_name:
                raise StructuredToolError("invalid_request", "named_type_name_required", f"{normalized} edits require a type name.")
            bucket = f"{normalized}s" if normalized != "typedef" else "typedefs"
            edits["edits"]["named_types"] = {bucket: {type_name: json_clone(value)}}
            return edits
        raise StructuredToolError(
            "invalid_request",
            "feature07_edit_kind_invalid",
            "edit_kind must be one of: function_name, function_type, calling_convention, variable_name, variable_type, global_name, global_type, struct, enum, typedef.",
        )

    def _apply_feature07_overrides(self, artifact: dict[str, Any], analysis: dict[str, Any]) -> None:
        feature07 = artifact.get("feature07", {})
        edits = feature07.get("edits", {})
        function_name_overrides = edits.get("function_names", {})
        function_type_overrides = edits.get("function_types", {})
        calling_convention_overrides = edits.get("calling_conventions", {})
        for function in analysis.get("functions", []):
            function_id = function.get("function_id")
            if function_id in function_name_overrides:
                override = function_name_overrides[function_id]
                function["original_name"] = function.get("original_name") or function.get("name")
                function["name"] = override["name"]
            signature_override = function_type_overrides.get(function_id) or function_type_overrides.get(str(function.get("name")))
            if signature_override:
                function["signature"] = signature_override.get("signature")
            detail = analysis.get("function_details", {}).get(str(int(function["address"])))
            if detail is not None and function_id in calling_convention_overrides:
                detail["calling_convention"] = {
                    **json_clone(detail.get("calling_convention", {})),
                    "name": calling_convention_overrides[function_id]["name"],
                    "source": calling_convention_overrides[function_id].get("source", "manual_override"),
                }
            if detail is not None:
                self._apply_feature07_variable_overrides(detail, function, edits)
        named_types = analysis.setdefault("type_information", {}).setdefault("named_types", [])
        named_type_index = {item.get("name"): item for item in named_types if item.get("name")}
        for kind, entries in edits.get("named_types", {}).items():
            for name, payload in entries.items():
                record = {"name": name, "kind": kind.rstrip("s"), **json_clone(payload)}
                named_type_index[name] = record
        analysis["type_information"]["named_types"] = sorted(named_type_index.values(), key=lambda item: (item.get("kind", ""), item.get("name", "")))
        for function in analysis.get("functions", []):
            signature_override = function_type_overrides.get(function.get("function_id")) or function_type_overrides.get(str(function.get("name")))
            if signature_override:
                existing = next((item for item in analysis["type_information"].get("function_signatures", []) if item.get("function_id") == function.get("function_id")), None)
                if existing is not None:
                    existing["signature"] = signature_override["signature"]
                    existing["name"] = function.get("demangled_name") or function["name"]
        if edits.get("type_imports"):
            analysis["type_information"]["imports"] = json_clone(edits["type_imports"])

    def _apply_feature07_variable_overrides(self, detail: dict[str, Any], function: dict[str, Any], edits: dict[str, Any]) -> None:
        function_tokens = {str(function.get("function_id")), str(function.get("name")), str(function.get("address"))}
        for local in detail.get("variables", {}).get("locals", []):
            original_name = str(local.get("name"))
            for token in function_tokens:
                if f"{token}:{original_name}" in edits.get("variable_names", {}):
                    local["name"] = edits["variable_names"][f"{token}:{original_name}"]["name"]
                    break
            for token in function_tokens:
                if f"{token}:{original_name}" in edits.get("variable_types", {}):
                    local["type"] = edits["variable_types"][f"{token}:{original_name}"]["type"]
                    break
        for global_item in detail.get("variables", {}).get("globals", []):
            key = str(global_item.get("address"))
            if key in edits.get("global_names", {}):
                global_item["name"] = edits["global_names"][key]["name"]
            if key in edits.get("global_types", {}):
                global_item["type"] = edits["global_types"][key]["type"]

    def _normalize_numeric_value(self, value: int | str) -> int:
        if isinstance(value, int):
            return value
        raw = value.strip().lower()
        try:
            return int(raw, 16 if raw.startswith("0x") else 10)
        except ValueError as exc:
            raise StructuredToolError(
                "invalid_request",
                "address_value_invalid",
                f"Value '{value}' is not a valid integer address.",
            ) from exc

    def _load_analysis_context(self, session_id: str, artifact_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        loaded = self.store.load_artifact_analysis(session_id, artifact_id)
        artifact = loaded["artifact"]
        analysis = loaded["analysis"]
        self._apply_feature07_overrides(artifact, analysis)
        override = artifact.get("analysis", {}).get("instruction_set_mode_override")
        if analysis.get("capabilities", {}).get("instruction_set_modes"):
            analysis["capabilities"]["instruction_set_modes"]["override"] = override
        return artifact, analysis

    def _artifact_parse_context(self, artifact: dict[str, Any], hints: dict[str, Any] | None = None) -> dict[str, Any]:
        return parse_artifact_context(
            artifact["canonical_path"],
            resource_limits=self.security.resource_limits.to_dict(),
            hints=hints,
        )

    def _maybe_load_analysis(self, session_id: str, artifact_id: str) -> dict[str, Any] | None:
        try:
            _, analysis = self._load_analysis_context(session_id, artifact_id)
        except StructuredToolError:
            return None
        return analysis

    def _artifact_relationships(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        artifacts = self.store.list_artifacts(session_id, cursor=None, limit=None)["items"]
        target = next((item for item in artifacts if item["artifact_id"] == artifact_id), None)
        if target is None:
            raise StructuredToolError("not_found", "artifact_not_found", f"Unknown artifact_id '{artifact_id}'.", details={"artifact_id": artifact_id})
        relationship = target.get("relationship") or {}
        parents = []
        parent_id = relationship.get("parent_artifact_id")
        if parent_id:
            parent = next((item for item in artifacts if item["artifact_id"] == parent_id), None)
            if parent is not None:
                parents.append({"artifact": self._artifact_reference(parent), "relationship": relationship})
        children = []
        for candidate in artifacts:
            candidate_relationship = candidate.get("relationship") or {}
            if candidate_relationship.get("parent_artifact_id") == artifact_id:
                children.append({"artifact": self._artifact_reference(candidate), "relationship": candidate_relationship})
        return {"parents": parents, "children": children}

    def _materialize_derived_artifacts(
        self,
        *,
        source_session_id: str,
        source_artifact: dict[str, Any],
        items: list[dict[str, Any]],
        output_subdir: str,
        attach_to_session: bool,
        target_session_id: str | None,
        analyze_extracted: bool,
        recurse: bool = False,
        depth: int = 1,
    ) -> dict[str, Any]:
        target_session = target_session_id or source_session_id
        if attach_to_session:
            self.store.load_session(session_id=target_session)
        materialized_items = []
        attached_artifacts = []
        skipped = []
        total_bytes = 0
        for index, item in enumerate(items):
            payload = item.get("bytes", b"")
            if not isinstance(payload, (bytes, bytearray)):
                skipped.append({"name": item.get("name"), "reason": "payload_missing"})
                continue
            payload_bytes = bytes(payload)
            if total_bytes + len(payload_bytes) > self.security.resource_limits.carved_byte_budget:
                skipped.append({"name": item.get("name"), "reason": "carved_byte_budget_exceeded"})
                continue
            total_bytes += len(payload_bytes)
            derived = self.security.derive_output_file(
                subdir=output_subdir,
                unsafe_name=item.get("name"),
                default_stem=f"derived_{index}",
            )
            output_path = materialize_output_file(Path(derived["path"]), payload_bytes)
            relationship = {
                "parent_artifact_id": source_artifact["artifact_id"],
                "parent_session_id": source_session_id,
                "path": self.security._relative_to_workspace(output_path),
                **json_clone(item.get("provenance", {})),
                "output_name_provenance": json_clone(derived["provenance"]),
            }
            record = {
                "name": output_path.name,
                "path": str(output_path),
                "relative_path": self.security._relative_to_workspace(output_path),
                "size": len(payload_bytes),
                "relationship": relationship,
            }
            attached_artifact = None
            if attach_to_session:
                attached_artifact = self.store.add_artifact(
                    target_session,
                    str(output_path),
                    output_path.name,
                    relationship=relationship,
                )
                record["artifact_id"] = attached_artifact["artifact_id"]
                attached_artifacts.append(attached_artifact)
                if analyze_extracted:
                    self._attempt_analysis(target_session, attached_artifact["artifact_id"])
            materialized_items.append(record)
            if recurse:
                if depth >= self.security.resource_limits.recursion_depth_limit:
                    skipped.append({"name": output_path.name, "reason": "recursion_depth_limit"})
                    continue
                nested = extract_archive_members(
                    output_path,
                    max_items=self.security.resource_limits.max_artifacts_per_session,
                    max_bytes=max(1, self.security.resource_limits.carved_byte_budget - total_bytes),
                )
                if nested["items"]:
                    nested_source = attached_artifact or {
                        **source_artifact,
                        "artifact_id": record.get("artifact_id", source_artifact["artifact_id"]),
                        "safe_display_name": output_path.name,
                    }
                    nested_result = self._materialize_derived_artifacts(
                        source_session_id=target_session if attached_artifact else source_session_id,
                        source_artifact=nested_source,
                        items=nested["items"],
                        output_subdir=f"{output_subdir}/nested_{index}",
                        attach_to_session=attach_to_session,
                        target_session_id=target_session if attach_to_session else None,
                        analyze_extracted=analyze_extracted,
                        recurse=True,
                        depth=depth + 1,
                    )
                    materialized_items.extend(nested_result["items"])
                    attached_artifacts.extend(nested_result["attached_artifacts"])
                    skipped.extend(nested["skipped"] + nested_result["skipped"])
                else:
                    skipped.extend(nested["skipped"])
        return {
            "items": materialized_items,
            "attached_artifacts": attached_artifacts,
            "skipped": skipped,
        }

    def _attempt_analysis(self, session_id: str, artifact_id: str) -> None:
        artifact = self.store.get_artifact_record(session_id, artifact_id=artifact_id)
        context = self._artifact_parse_context(artifact)
        if context["parsed"].get("file_type", {}).get("format") not in {"ELF", "PE", "Mach-O"}:
            return
        analysis = self.parser_sandbox.analyze_program(artifact["canonical_path"])
        self.store.persist_artifact_analysis(session_id, artifact_id, analysis["result"])

    def _resolve_function_from_analysis(
        self,
        analysis: dict[str, Any],
        *,
        function_id: str | None = None,
        name: str | None = None,
        address: int | None = None,
    ) -> dict[str, Any]:
        if function_id:
            function = next((item for item in analysis["functions"] if item.get("function_id") == function_id), None)
            if function is None:
                raise StructuredToolError("invalid_id", "function_id_not_found", f"Unknown function_id '{function_id}'.", details={"function_id": function_id})
            return function
        if address is not None:
            function = next((item for item in analysis["functions"] if int(item["address"]) == int(address)), None)
            if function is None:
                raise StructuredToolError(
                    "not_found",
                    "function_address_not_found",
                    f"No recovered function starts at address 0x{address:x}.",
                    details={"address": address},
                )
            return function
        if name:
            lowered = name.lower()
            matches = [
                item
                for item in analysis["functions"]
                if item["name"].lower() == lowered or (item.get("demangled_name") or "").lower() == lowered
            ]
            if not matches:
                matches = [
                    item
                    for item in analysis["functions"]
                    if lowered in item["name"].lower() or lowered in (item.get("demangled_name") or "").lower()
                ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise StructuredToolError(
                    "conflict",
                    "function_name_ambiguous",
                    f"Function name query '{name}' matched multiple recovered functions.",
                    details={"name": name, "match_count": len(matches)},
                )
            raise StructuredToolError("not_found", "function_name_not_found", f"Unknown function name '{name}'.", details={"name": name})
        raise StructuredToolError(
            "invalid_request",
            "function_reference_required",
            "Provide function_id, name, or address to identify a function.",
        )

    def _function_detail(self, analysis: dict[str, Any], function: dict[str, Any]) -> dict[str, Any]:
        detail = analysis.get("function_details", {}).get(str(int(function["address"])))
        if detail is None:
            raise StructuredToolError(
                "missing_prerequisite",
                "function_detail_not_available",
                f"Semantic details are not available for function 0x{int(function['address']):x}.",
            )
        return detail

    def _workflow_annotation_type(self, kind: str) -> str:
        normalized = kind.strip().lower()
        mapping = {
            "bookmark": "workflow.bookmark",
            "named_region": "workflow.named_region",
            "note": "workflow.note",
        }
        if normalized not in mapping:
            raise StructuredToolError(
                "invalid_request",
                "workflow_kind_invalid",
                "kind must be one of: bookmark, named_region, note.",
                details={"kind": kind},
            )
        return mapping[normalized]

    def _page_items(self, items: list[dict[str, Any]], *, cursor: int, limit: int) -> dict[str, Any]:
        start = max(0, int(cursor))
        size = max(1, int(limit))
        page_items = items[start : start + size]
        next_cursor = start + size if start + size < len(items) else None
        return {
            "items": page_items,
            "page": {
                "cursor": start,
                "limit": size,
                "returned": len(page_items),
                "total": len(items),
                "next_cursor": next_cursor,
                "truncated": next_cursor is not None,
            },
        }

    # ── Ghidra headless tools ────────────────────────────────────────────────

    def ghidra_decompile(
        self,
        session_id: str,
        artifact_id: str,
        address: int | str,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "address": address, "timeout_seconds": timeout_seconds}

        def operation() -> dict[str, Any]:
            if not ghidra_available():
                raise StructuredToolError("missing_prerequisite", "ghidra_not_installed", "Ghidra headless is not available in this container.")
            artifact = self.store.get_artifact_record(session_id=session_id, artifact_id=artifact_id)
            addr = self._normalize_numeric_value(address)
            result = ghidra_decompile_function(artifact["canonical_path"], function_address=addr, timeout_seconds=int(timeout_seconds))
            return {"artifact": self._artifact_reference(artifact), **result.get("result", result)}

        return self._respond("ghidra_decompile", parameters, operation)

    def ghidra_analyze(
        self,
        session_id: str,
        artifact_id: str,
        timeout_seconds: int = 600,
    ) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "timeout_seconds": timeout_seconds}

        def operation() -> dict[str, Any]:
            if not ghidra_available():
                raise StructuredToolError("missing_prerequisite", "ghidra_not_installed", "Ghidra headless is not available in this container.")
            artifact = self.store.get_artifact_record(session_id=session_id, artifact_id=artifact_id)
            result = ghidra_export_analysis(artifact["canonical_path"], timeout_seconds=int(timeout_seconds))
            return {"artifact": self._artifact_reference(artifact), **result.get("result", result)}

        return self._respond("ghidra_analyze", parameters, operation)

    def run_ghidra_script(
        self,
        session_id: str,
        artifact_id: str,
        script: str,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "script": script[:200], "timeout_seconds": timeout_seconds}

        def operation() -> dict[str, Any]:
            if not ghidra_available():
                raise StructuredToolError("missing_prerequisite", "ghidra_not_installed", "Ghidra headless is not available in this container.")
            artifact = self.store.get_artifact_record(session_id=session_id, artifact_id=artifact_id)
            result = ghidra_run_custom_script(artifact["canonical_path"], script_content=script, timeout_seconds=int(timeout_seconds))
            return {"artifact": self._artifact_reference(artifact), **result.get("result", result)}

        return self._respond("run_ghidra_script", parameters, operation)

    # ── Cross-server bridge ──────────────────────────────────────────────────

    def export_dynamic_manifest(
        self,
        session_id: str,
        artifact_id: str,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        parameters = {"session_id": session_id, "artifact_id": artifact_id, "output_path": output_path}

        def operation() -> dict[str, Any]:
            artifact, analysis = self._load_analysis_context(session_id, artifact_id)

            functions = [
                {"name": f["name"], "address": hex(int(f["address"])), "address_int": int(f["address"]), "size": f.get("size", 0)}
                for f in analysis.get("functions", [])
            ]
            strings = [
                {"value": s.get("value", ""), "address": hex(int(s["address"])), "address_int": int(s["address"])}
                for s in analysis.get("strings", [])
                if s.get("address") is not None
            ]
            imports = [
                {"name": s["name"], "address": hex(int(s["address"])), "address_int": int(s["address"]), "library": s.get("library")}
                for s in analysis.get("symbols", [])
                if s.get("kind") == "import"
            ]

            caps = analysis.get("capabilities", {})
            summary = analysis.get("summary", {})

            manifest = {
                "schema_version": 1,
                "source": "reversing-mcp",
                "binary": artifact["display_name"],
                "canonical_path": artifact["canonical_path"],
                "architecture": caps.get("architecture", "unknown"),
                "bitness": caps.get("bitness"),
                "endianness": caps.get("endianness"),
                "entry_point": hex(int(summary.get("entry_point", 0))),
                "image_base": hex(int(summary.get("image_base", 0))),
                "function_count": len(functions),
                "string_count": len(strings),
                "import_count": len(imports),
                "functions": functions,
                "strings": strings[:2000],
                "imports": imports,
            }

            if output_path:
                target = self.security.resolve_output_file(output_path, purpose="Dynamic manifest export")
            else:
                safe_name = artifact.get("safe_display_name", "artifact")
                target = Path(self.store.workspace_root) / ".analysis" / f"{safe_name}.manifest.json"
                target.parent.mkdir(parents=True, exist_ok=True)

            target.write_text(json.dumps(manifest, indent=2))

            return {
                "artifact": self._artifact_reference(artifact),
                "manifest_path": str(target),
                "function_count": len(functions),
                "string_count": len(strings),
                "import_count": len(imports),
            }

        return self._respond("export_dynamic_manifest", parameters, operation)
