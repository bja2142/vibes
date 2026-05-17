---
name: binary-analysis
description: >
  Binary analysis, reverse engineering, and exploit development using the reversing-mcp
  (static) and pwn-mcp (dynamic) MCP servers. Covers disassembly, decompilation, Ghidra
  analysis, debugging with GDB, Frida instrumentation, exploit
  scripting with pwntools, ROP gadget discovery, constraint solving with Z3, memory
  inspection, heap analysis, string extraction, YARA scanning, code coverage, record/replay
  debugging, seccomp analysis, libc identification, protocol fuzzing, and cross-architecture
  binary execution. Triggers on any request involving: ELF/PE/Mach-O files, executables,
  shared libraries, firmware images, CTF challenges, pwn challenges, buffer overflows,
  format strings, ROP chains, shellcode, binary patching, reverse engineering, malware
  analysis, vulnerability research, binary diffing, symbol extraction, calling conventions,
  control flow graphs, data flow analysis, or any low-level binary inspection task.
tools:
  - mcp__reversing-mcp__*
  - mcp__pwn-mcp__*
---

# Binary Analysis Skill

You have access to two specialized MCP servers for comprehensive binary analysis:

- **reversing-mcp** (port 6767): Static analysis -- disassembly, decompilation, semantic recovery, signatures, patching
- **pwn-mcp** (port 6768): Dynamic analysis -- GDB debugging, Frida instrumentation, exploit tools, symbolic solving, tracing

These servers share a workspace volume. Static analysis results can be exported as JSON manifests and imported into dynamic analysis sessions.

---

## IMPORTANT RULES

1. **Always create a session first.** reversing-mcp uses `create_session`, pwn-mcp uses `create_execution_session`. Every other tool requires a session_id.
2. **Binaries must be in the workspace.** In this ben-mcp port, both servers use `$BEN_MCP_REPO_ROOT/agent-sandbox-work` as their workspace root. Host MCP pulls are visible under `mcp-artifacts/` inside that shared workspace.
3. **Analysis is async.** After `start_artifact_analysis`, poll with `get_job` until `status: "completed"`. Or use `analyze_and_summarize` which waits automatically.
4. **IDs are generation-scoped.** `function_id` and `string_id` values expire when an artifact is reanalyzed. Always re-query after reanalysis.
5. **Use composite briefs to save tokens.** Prefer `ingest_and_triage_artifact`, `analyze_and_summarize`, `hunt_interesting_regions` over manual multi-step sequences when doing initial investigation.
6. **Clean up sessions.** Call `destroy_execution_session` (pwn-mcp) or `destroy_session` (reversing-mcp) when done.
7. **Check `ok` field.** Every response has `"ok": true/false`. Always check before proceeding. Errors include `category`, `code`, `message`, and `retryable` fields.
8. **pwn-mcp tools return flat JSON.** The response structure is `{"ok": true, "result": {...}}` or `{"ok": false, "error": {...}}`.
9. **reversing-mcp tools return an envelope.** Every response includes `ok`, `result`, `error`, `partial`, `confidence`, `provenance`, and `suggested_next_actions`.

---

## REVERSING-MCP: STATIC ANALYSIS (98 tools)

### Result Envelope

Every reversing-mcp response follows this structure:
```json
{
  "schema_version": "1.0",
  "server": {"name": "reversing-mcp", "version": "0.1.0"},
  "timestamp": "...",
  "ok": true,
  "partial": false,
  "confidence": {"level": "exact", "method": null},
  "provenance": {"backend": "...", "tool": "...", "parameters": {...}},
  "result": { ... },
  "error": null,
  "suggested_next_actions": [...]
}
```

On failure:
```json
{
  "ok": false,
  "error": {
    "category": "format_error|missing_prerequisite|not_found|...",
    "code": "specific_error_code",
    "message": "Human-readable description",
    "details": {},
    "retryable": false
  }
}
```

### Session & Artifact Lifecycle

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `create_session` | `name` | Create a new analysis session. Returns `session_id`. |
| `load_session` | `session_id` or `name` | Load existing session by ID or unique name. |
| `list_sessions` | — | Enumerate all sessions. Supports `cursor`, `limit`. |
| `destroy_session` | `session_id` or `name` | Delete session and all persisted state. |
| `update_session_settings` | `session_id`, `settings_patch` | Merge settings into session config. |
| `add_artifact` | `session_id`, `path` | Attach a binary file to the session. Returns `artifact_id`. |
| `list_artifacts` | `session_id` | List artifacts in session. Supports `cursor`, `limit`. |
| `remove_artifact` | `session_id`, `artifact_id` or `display_name` | Detach artifact from session. |

### Triage & File Intake

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `triage_artifact` | `session_id`, `artifact_id` | Identify format, architecture, mitigations, signatures, strings preview. Optional: `hints`, `string_preview_limit`. |
| `list_artifact_strings` | `session_id`, `artifact_id` | Extract strings with filtering. Optional: `cursor`, `limit`, `min_length`, `encoding`, `query`. |
| `translate_artifact_address` | `session_id`, `artifact_id`, `input_kind`, `value` | Convert between file offset, VA, RVA. `input_kind`: `"file_offset"`, `"virtual_address"`, `"rva"`. |
| `list_artifact_children` | `session_id`, `artifact_id` | List embedded objects (archive members, Mach-O headers). |

### Analysis Engine

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `start_artifact_analysis` | `session_id`, `artifact_id` | Queue async angr CFGFast analysis. Returns `job_id`. |
| `start_artifact_reanalysis` | `session_id`, `artifact_id` | Re-run analysis (invalidates old function/string IDs). |
| `get_job` | `job_id` | Poll job status. Returns `status`: `"queued"`, `"running"`, `"completed"`, `"failed"`, `"cancelled"`. |
| `list_jobs` | — | List jobs. Optional: `session_id`, `status`, `cursor`, `limit`. |
| `cancel_job` | `job_id` | Cancel a running job. |
| `get_analysis_synopsis` | `session_id`, `artifact_id` | Compact summary: function count, annotation counts, extraction history, unknowns. |

### Disassembly & Decompilation

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `list_artifact_symbols` | `session_id`, `artifact_id` | Imports, exports, thunks. Optional: `query`, `kind`, `cursor`, `limit`. |
| `list_artifact_functions` | `session_id`, `artifact_id` | Recovered functions. Optional: `query` (exact > prefix > substring), `cursor`, `limit`. |
| `disassemble_function` | `session_id`, `artifact_id` + one of: `function_id`, `name`, `address` | Structured disassembly. Optional: `cursor`, `limit`, `instruction_mode_override`. |
| `disassemble_range` | `session_id`, `artifact_id`, `input_kind`, `start_value`, `size` | Disassemble arbitrary range. |
| `decompile_function` | `session_id`, `artifact_id` + one of: `function_id`, `name`, `address` | Best-effort pseudo-C (angr). Optional: `char_limit`, `line_limit`. |
| `read_artifact_bytes` | `session_id`, `artifact_id`, `input_kind`, `value`, `length` | Raw hex + ASCII dump. |
| `get_artifact_instruction_mode` | `session_id`, `artifact_id` | Report supported instruction modes. |
| `set_artifact_instruction_mode` | `session_id`, `artifact_id`, `mode` | Override instruction mode (ARM/Thumb). |

### Cross-References & Search

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `list_artifact_xrefs` | `session_id`, `artifact_id` + one of: `function_id`, `string_id`, `address` | Cross-references to/from target. |
| `search_artifact` | `session_id`, `artifact_id`, `kind`, `query` | Search by: `"name"`, `"string"`, `"immediate"`, `"opcode"`, `"pattern"`, `"range"`. |
| `get_artifact_linkage` | `session_id`, `artifact_id` | Imports, exports, PLT/GOT/IAT metadata. |
| `get_artifact_debug_info` | `session_id`, `artifact_id` | DWARF/PDB source metadata. |

### Semantic Recovery

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `get_call_graph` | `session_id`, `artifact_id` + function target | Bounded call graph. Optional: `direction`, `depth`, `limit_nodes`, `limit_edges`. |
| `get_control_flow_graph` | `session_id`, `artifact_id` + function target | Basic blocks, edges, loops, branches. |
| `get_function_variables` | `session_id`, `artifact_id` + function target | Arguments, locals, globals, register params. |
| `get_stack_frame` | `session_id`, `artifact_id` + function target | Stack slots, saved registers. |
| `get_constant_propagation` | `session_id`, `artifact_id` + function target | Recovered immediates, call-site propagation. |
| `get_type_information` | `session_id`, `artifact_id` | Function signatures, named types, typed memory. |
| `recover_types` | `session_id`, `artifact_id` | Heuristic RTTI, vtables, type recovery. |
| `inspect_data_segments` | `session_id`, `artifact_id` | Non-executable sections: string pools, pointer tables. |
| `get_indirect_flows` | `session_id`, `artifact_id` + function target | Unresolved indirect calls/branches. |
| `get_exception_metadata` | `session_id`, `artifact_id` | Unwind and exception info. |
| `get_calling_convention` | `session_id`, `artifact_id` + function target | Detected calling convention. |
| `get_intermediate_representation` | `session_id`, `artifact_id` + function target | VEX IR. Optional: `limit_blocks`, `limit_statements`. |
| `get_runtime_metadata` | `session_id`, `artifact_id` | Language/runtime hints (C++, Go, Rust, etc.). |
| `slice_data_flow` | `session_id`, `artifact_id` + function target | Static data-flow slice. Optional: `anchor_address`, `register`, `radius`. |
| `identify_system_calls` | `session_id`, `artifact_id` + function target | Syscall instructions and number guesses. |
| `navigate_neighborhood` | `session_id`, `artifact_id` + function target | Callers, callees, nearby functions/strings. Optional: `depth`, `radius`. |
| `prioritize_functions` | `session_id`, `artifact_id` | Triage-scored function ranking. Optional: `include_tags`, `exclude_tags`, `min_score`, `limit`. |
| `classify_functions` | `session_id`, `artifact_id` | Group functions by heuristic tags. |

**Function target**: most semantic tools accept one of `function_id`, `name`, or `address` to identify the function.

### Signatures & Extraction

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `scan_with_yara` | `session_id`, `artifact_id` | YARA scan (real or heuristic fallback). Optional: `rules_text`. |
| `fingerprint_compiler_toolchain` | `session_id`, `artifact_id` | Compiler/toolchain identification. |
| `detect_packer` | `session_id`, `artifact_id` | Packer heuristics (overlays, entropy, markers). |
| `calculate_entropy` | `session_id`, `artifact_id` | Whole-file and per-section entropy. |
| `deobfuscate_strings` | `session_id`, `artifact_id` | FLOSS decoded/stack/tight strings + base64/hex. Optional: `limit`. |
| `detect_crypto_constants` | `session_id`, `artifact_id` | Known crypto/checksum constants. |
| `recognize_library_code` | `session_id`, `artifact_id` | Library/runtime code identification. |
| `extract_resources` | `session_id`, `artifact_id` | PE resources, container members. Optional: `attach_to_session`, `analyze_extracted`. |
| `carve_embedded_artifacts` | `session_id`, `artifact_id` | Carve overlays and nested artifacts. Optional: `recurse`, `attach_to_session`. |
| `get_artifact_relationships` | `session_id`, `artifact_id` | Parent/child links. Optional: `direction`. |

### Patching

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `patch_artifact_bytes` | `session_id`, `artifact_id`, `input_kind`, `value`, `bytes_hex` | Apply byte patch at offset/VA/RVA. |
| `patch_artifact_assembly` | `session_id`, `artifact_id`, `input_kind`, `value`, `assembly`, `isa` | Assemble and patch. ISA: `x86`, `x86_64`, `aarch64`, `arm`, `thumb`. |
| `find_code_caves` | `session_id`, `artifact_id` | Zero/filler-byte runs for stubs. Optional: `min_size`. |
| `edit_artifact_metadata` | `session_id`, `artifact_id`, `edit_kind`, `target`, `value` | Override names, types, variables, calling conventions. |
| `import_type_definitions` | `session_id`, `artifact_id`, `source_format`, `source_text` | Import C headers or JSON types. |

### Multi-Artifact

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `list_artifact_dependencies` | `session_id`, `artifact_id` | Dependency hints from imports/linkage. |
| `correlate_session_artifacts` | `session_id` | Cross-artifact correlation by shared imports/strings. Optional: `artifact_ids`. |
| `diff_artifacts` | `session_id`, `left_artifact_id`, `right_artifact_id` | Structural diff of two artifacts. |

### Workflow & Annotations

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `save_workflow_item` | `session_id`, `kind`, `target`, `value` | Save bookmarks, notes, named regions. |
| `list_workflow_items` | `session_id` | List workflow items. Optional: `kind`, `artifact_id`. |
| `put_annotation` | `session_id`, `target`, `annotation_type`, `value` | Create/update annotation with revision history. |
| `list_annotations` | `session_id` | Enumerate annotations. Optional: `artifact_id`, `target_kind`, `annotation_type`. |
| `get_annotation_history` | `session_id`, `annotation_id` | Full revision history. |
| `revert_annotation` | `session_id`, `annotation_id` | Revert to prior revision. Optional: `revision_id`. |
| `register_provisional_function` | `session_id`, `artifact_id`, `name` | Create temp function handle pre-analysis. |
| `register_provisional_string` | `session_id`, `artifact_id`, `value` | Create temp string handle pre-analysis. |
| `get_object_reference` | `session_id`, `object_id` | Resolve provisional IDs. |

### Snapshots & Exports

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `create_session_snapshot` | `session_id`, `name` | Whole-session checkpoint. |
| `list_session_snapshots` | `session_id` | List snapshots. |
| `restore_session_snapshot` | `session_id` + `snapshot_id` or `name` | Restore snapshot. |
| `export_curated_analysis` | `session_id`, `artifact_id` | Export subset to disk. Optional: `function_ids`, `string_ids`. |
| `export_command_log` | `session_id` | Export command log. Optional: `format`, `output_path`. |
| `export_analysis_report` | `session_id`, `artifact_id` | Export artifact report. Optional: `format`, `output_path`. |
| `export_session_state` | `session_id` | Export full session state as JSON. |
| `batch_query_artifacts` | `session_id`, `operation` | Run query across all artifacts. Operations: `analysis_synopsis`, `classify_functions`, `prioritize_functions`, `inspect_data_segments`. |

### Composite Brief Workflows

These tools reduce token usage and round trips. They share these optional controls:
- `verbosity`: `"brief"`, `"normal"`, `"deep"`
- `token_budget_hint`: integer hint to clamp response size
- `include_next_actions`: boolean
- `include_raw_sections`: boolean

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `ingest_and_triage_artifact` | `session_id`, `path` | Add + triage in one call. Optional: `analyze` (bool). |
| `analyze_and_summarize` | `session_id`, `artifact_id` | Start analysis + wait + return brief. `focus`: `general`, `malware`, `patching`, `diffing`, `firmware`, `extraction`. |
| `hunt_interesting_regions` | `session_id`, `artifact_id` | Ranked shortlist of interesting targets. Optional: `objective`, `limit`. |
| `trace_capability` | `session_id`, `artifact_id`, `target` | Expand one function: neighborhood, xrefs, variables. `target`: `{"function_id": "..."}` or `{"name": "..."}` or `{"address": "..."}`. |
| `prepare_patch_plan` | `session_id`, `artifact_id`, `objective` | Code caves + patch points + instruction mode. Optional: `target`. |
| `artifact_relationship_brief` | `session_id`, `artifact_id` | Parents, children, deps, correlation, diff candidates. `focus`: `general`, `diffing`, `extraction`. |

### Ghidra Headless Analysis

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `ghidra_decompile` | `session_id`, `artifact_id`, `address` | Decompile function via Ghidra when the image has a native Ghidra decompiler executable for the container architecture. |
| `ghidra_analyze` | `session_id`, `artifact_id` | Full Ghidra analysis: functions, strings, imports, sections. |
| `run_ghidra_script` | `session_id`, `artifact_id`, `script` | Custom PyGhidra Python script with `api`, `program`, and `currentProgram` available. |

### Cross-Server Bridge

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `export_dynamic_manifest` | `session_id`, `artifact_id` | Export JSON manifest for pwn-mcp. Contains functions, strings, imports with addresses. |

### Discovery & Runtime

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `describe_tools` | — | List all tools with descriptions and prerequisites. |
| `get_capabilities` | — | Server capabilities, tool dependencies, transport info. |
| `get_runtime_policies` | — | Workspace root, parser isolation, resource limits. |
| `run_parser_probe` | `path` | Validate parser against a file. |

---

## PWN-MCP: DYNAMIC ANALYSIS (90 tools)

### Response Format

```json
{"ok": true, "result": { ... }}
{"ok": false, "error": {"category": "...", "code": "...", "message": "...", "details": {}, "retryable": false}}
```

### Session Management

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `create_execution_session` | — | Create isolated execution session. Optional: `arch`. Returns `session_id`. |
| `list_execution_sessions` | — | List active sessions. |
| `destroy_execution_session` | `session_id` | Kill all processes, clean up working directory. |

### Process Control

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `launch_binary` | `session_id`, `binary_path` | Launch binary with piped I/O. Auto-detects arch, uses QEMU for non-native. Optional: `args`, `env`, `timeout_seconds`. Returns `process_id`. |
| `send_input` | `session_id`, `process_id`, `data` | Write to stdin. Optional: `newline` (default true). |
| `read_output` | `session_id`, `process_id` | Read buffered output. Optional: `stream` (`stdout`/`stderr`/`both`), `max_bytes`, `wait_ms`, `clear`. |
| `get_process_state` | `session_id`, `process_id` | Running/exited state, exit code, buffer sizes. |
| `terminate_process` | `session_id`, `process_id` | Send signal. Optional: `sig` (`SIGTERM`/`SIGKILL`/`SIGINT`/`SIGHUP`). |

### GDB Debugging

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `start_debug_session` | `session_id`, `binary_path` | Start GDB/MI session. Optional: `framework` (`gef`/`pwndbg`/`vanilla`), `extra_gdb_args`. Returns `debug_id`. |
| `stop_debug_session` | `session_id`, `debug_id` | Terminate GDB session. |
| `send_gdb_command` | `session_id`, `debug_id`, `command` | Raw GDB/MI command. Prefix with `-` for MI. Optional: `timeout_seconds`. |
| `set_breakpoint` | `session_id`, `debug_id`, `location` | Set breakpoint. `location`: function name, `file:line`, or `*0xADDR`. Optional: `condition`, `temporary`. |
| `delete_breakpoint` | `session_id`, `debug_id`, `breakpoint_number` | Delete breakpoint. |
| `list_breakpoints` | `session_id`, `debug_id` | List all breakpoints. |
| `continue_execution` | `session_id`, `debug_id` | Resume until breakpoint/exit. Optional: `timeout_seconds`. |
| `step_instruction` | `session_id`, `debug_id` | Single machine instruction, step into. |
| `step_over_instruction` | `session_id`, `debug_id` | Single machine instruction, step over. |
| `step_into` | `session_id`, `debug_id` | Step into next source line. |
| `step_over` | `session_id`, `debug_id` | Step over next source line. |
| `finish_function` | `session_id`, `debug_id` | Run until current function returns. |
| `run_until` | `session_id`, `debug_id`, `location` | Run until location reached. |
| `read_registers` | `session_id`, `debug_id` | Read CPU registers. Optional: `register_names` (array). |
| `write_register` | `session_id`, `debug_id`, `register`, `value` | Set register value (hex or decimal). |
| `read_memory` | `session_id`, `debug_id`, `address`, `length` | Read bytes. Returns hex + ASCII. |
| `write_memory` | `session_id`, `debug_id`, `address`, `data_hex` | Write bytes. |
| `search_memory` | `session_id`, `debug_id`, `start_address`, `end_address`, `pattern_hex` | Search memory for pattern. |
| `get_backtrace` | `session_id`, `debug_id` | Call stack. Optional: `max_frames`. |
| `get_locals` | `session_id`, `debug_id` | Local variables. Optional: `frame` (default 0). |
| `evaluate_expression` | `session_id`, `debug_id`, `expression` | Evaluate GDB expression. |
| `get_memory_maps` | `session_id`, `debug_id` | Memory segments, permissions, file mappings. |
| `get_heap_info` | `session_id`, `debug_id` | Heap bins, chunks, tcache via GEF/pwndbg. |
| `get_libc_info` | `session_id`, `debug_id` | Loaded shared libraries + libc base address. |

### Frida Dynamic Instrumentation

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `start_frida_session` | `session_id`, `binary_path` | Spawn binary under Frida. Optional: `args`. Returns `frida_id`. |
| `stop_frida_session` | `session_id`, `frida_id` | Detach, unload scripts, clean up. |
| `inject_script` | `session_id`, `frida_id`, `script_source` | Inject Frida JS. Optional: `script_name`. |
| `hook_function` | `session_id`, `frida_id` + `function_name` or `address` | Hook with Interceptor. Optional: `on_enter`, `on_leave` (JS bodies). |
| `trace_calls` | `session_id`, `frida_id` | Trace function calls. Optional: `module_name`, `function_pattern`. |
| `get_exports` | `session_id`, `frida_id` | List module exports or all modules. Optional: `module_name`. |
| `get_memory_ranges` | `session_id`, `frida_id` | Memory ranges by protection. Optional: `protection` (default `r--`). |
| `dump_memory` | `session_id`, `frida_id`, `address` | Dump bytes (max 64KB). Optional: `length` (default 256). Returns hex string. |

### Record/Replay (rr) -- x86/x64 only

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `start_rr_record` | `session_id`, `binary_path` | Record execution. Optional: `args`, `stdin_data`, `timeout_seconds`. Returns `recording_id`. |
| `start_rr_replay` | `session_id`, `recording_id` | Replay with debug. Returns `debug_id` (supports all GDB + reverse commands). |
| `list_recordings` | `session_id` | List recordings. |
| `reverse_continue` | `session_id`, `debug_id` | Run backwards to previous breakpoint. |
| `reverse_step` | `session_id`, `debug_id` | Reverse single-step. |
| `reverse_next` | `session_id`, `debug_id` | Reverse step-over. |
| `reverse_finish` | `session_id`, `debug_id` | Run backwards to function start. |

### Tracing

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `run_with_strace` | `session_id`, `binary_path` | Syscall trace. Optional: `args`, `syscall_filter`, `follow_forks`, `timeout_seconds`, `stdin_data`. Returns `trace_id`. |
| `run_with_ltrace` | `session_id`, `binary_path` | Library call trace. Optional: `library_filter`, `timeout_seconds`. Returns `trace_id`. |
| `run_with_uftrace` | `session_id`, `binary_path` | Function call graph. Optional: `depth`, `timeout_seconds`. Returns `trace_id`. |
| `run_with_valgrind` | `session_id`, `binary_path` | Memory/perf analysis. `tool`: `memcheck`, `callgrind`, `helgrind`, `massif`, `dhat`, `cachegrind`. Returns `trace_id`. |
| `get_trace_output` | `session_id`, `trace_id` | Retrieve trace data. Optional: `max_bytes`. |

### Code Coverage

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `run_with_coverage` | `session_id`, `binary_path` | DynamoRIO drcov. Returns `coverage_id`. |
| `get_coverage_report` | `session_id`, `coverage_id` | Module list, block count, optional block sample. |
| `diff_coverage` | `session_id`, `coverage_id_a`, `coverage_id_b` | Compare two runs with new/dropped block samples. |

### Exploit Tools

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `checksec` | `session_id`, `binary_path` | RELRO, canary, NX, PIE, FORTIFY detection. |
| `run_pwntools_script` | `session_id`, `script` | Python script with `from pwn import *` pre-imported. `WORKSPACE` and `OUTPUT_DIR` available. Optional: `timeout_seconds`. |
| `generate_cyclic_pattern` | — | De Bruijn pattern. Optional: `length` (default 200). |
| `find_cyclic_offset` | `value` | Find offset in pattern. `value`: hex addr, decimal, or 4-byte ASCII. |
| `find_one_gadgets` | `session_id`, `libc_path` | One-shot `execve('/bin/sh')` gadgets in libc. |
| `get_rop_gadgets` | `session_id`, `binary_path` | ROP gadgets via ropper/ROPgadget. Optional: `search`, `max_results`, `tool`. |

### Seccomp Analysis

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `analyze_seccomp` | `session_id`, `binary_path` | Dump BPF filters, show allowed/blocked syscalls. |

### Constraint Solving

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `run_z3_script` | `session_id`, `script` | Z3 solver with `from z3 import *` + `_solve_and_print(solver, variables)` pre-imported. Optional: `timeout_seconds`. |

### Symbolic Execution

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `run_angr_script` | `session_id`, `script` | angr/claripy/cle script with shared `WORKSPACE` and `OUTPUT_DIR`. Optional: `timeout_seconds`. |
| `get_angr_project_info` | `session_id`, `binary_path` | angr loader, architecture, entrypoint, object, and symbol summary. |
| `angr_find_path` | `session_id`, `binary_path`, `find_address` | Solve symbolic stdin to reach an address. Optional: `avoid_address`, `stdin_size`, `timeout_seconds`. |

### Emulation

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `emulate_blob_unicorn` | `session_id`, `arch`, `code_hex` | Run raw shellcode/gadget bytes and return final registers. Optional: `start_address`, `registers`, `memory_size`. |
| `run_qiling_script` | `session_id`, `script` | Qiling script runner with shared workspace/output env vars. Optional: `timeout_seconds`. |

### Assembly and Disassembly

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `assemble_code` | `session_id`, `assembly`, `arch` | Assemble snippets with Keystone or NASM. Optional: `syntax`, `base_address`, `backend`. |
| `disassemble_bytes` | `session_id`, `code_hex`, `arch` | Disassemble bytes with Capstone or rasm2. Optional: `base_address`, `syntax`, `max_instructions`, `backend`. |
| `disassemble_file_region` | `session_id`, `binary_path`, `offset`, `length`, `arch` | Disassemble a bounded file region. Optional: `base_address`, `max_instructions`. |

### RE Triage

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `run_capa` | `session_id`, `binary_path` | FLARE capa capability detection; bundled `/opt/capa-rules` is used by default. Optional: `output_format`, `rules_path`. |
| `run_floss` | `session_id`, `binary_path` | FLARE FLOSS strings. Defaults to static strings for ELF; use `analysis_types=["all"]` for full PE decoding. |
| `run_yara_scan` | `session_id`, `target_path` | YARA scan with `rule_source` or `rule_path`. Optional: `show_strings`, `timeout_seconds`. |
| `run_radare2_command` | `session_id`, `binary_path` | Bounded read-only radare2 commands. Optional: `commands`, `timeout_seconds`. |

### Protocol Fuzzing

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `run_boofuzz_script` | `session_id`, `script` | boofuzz with `from boofuzz import *` pre-imported. Optional: `timeout_seconds`. |

### Diagnostics

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `validate_toolchain` | — | Map installed backends to MCP wrappers. Optional: `run_probes`, `timeout_seconds`. |

### libc Tools

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `identify_libc` | `session_id`, `function_name`, `leaked_address` | ID libc from leaked addr (e.g. `function_name="puts"`, `leaked_address="0x7f..."`). |
| `list_available_libcs` | `session_id` | Available + downloaded libc versions. |
| `download_libc` | `session_id`, `version` | Download via glibc-all-in-one. |
| `patch_binary_libc` | `session_id`, `binary_path` | patchelf interpreter/rpath. Optional: `interpreter`, `rpath`. |
| `get_elf_metadata` | `session_id`, `binary_path` | Current interpreter, rpath, NEEDED. |

### Static-Dynamic Bridge

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `import_static_analysis` | `session_id`, `manifest_path` | Import reversing-mcp manifest. Returns functions, strings, imports. |
| `auto_set_breakpoints` | `session_id`, `debug_id`, `manifest_path` | Auto-set GDB breakpoints from manifest. Optional: `filter_names`. |

### Job Management

| Tool | Required Params | Purpose |
|------|----------------|---------|
| `get_job` | `job_id` | Poll async job status. |
| `cancel_job` | `job_id` | Cancel running job. |
| `list_jobs` | `session_id` | List session jobs. |

---

## RECOMMENDED WORKFLOWS

### Quick Static Triage

For fast initial assessment of any binary:

```
1. create_session(name="triage")
2. ingest_and_triage_artifact(session_id, path="/workspace/target.elf", analyze=true)
   → Returns format, arch, mitigations, strings preview + starts analysis
3. analyze_and_summarize(session_id, artifact_id, focus="general", wait_timeout_seconds=60)
   → Waits for analysis, returns compact brief with function count, key findings
4. hunt_interesting_regions(session_id, artifact_id)
   → Ranked shortlist of suspicious functions, strings, imports
```

### Deep Function Analysis

When you've identified a function of interest:

```
1. disassemble_function(session_id, artifact_id, function_id="fn_...")
2. decompile_function(session_id, artifact_id, function_id="fn_...")
3. get_call_graph(session_id, artifact_id, function_id="fn_...", depth=2)
4. get_function_variables(session_id, artifact_id, function_id="fn_...")
5. list_artifact_xrefs(session_id, artifact_id, function_id="fn_...")
6. slice_data_flow(session_id, artifact_id, function_id="fn_...", register="rdi")
```

Or use the composite shortcut:

```
trace_capability(session_id, artifact_id, target={"function_id": "fn_..."}, verbosity="deep")
```

### Ghidra Decompilation

When angr decompilation is insufficient:

```
1. ghidra_decompile(session_id, artifact_id, address="0x401000")
   → Higher-quality pseudo-C from Ghidra
2. ghidra_analyze(session_id, artifact_id)
   → Full Ghidra analysis export (compare with angr results)
```

### CTF Pwn Challenge

Complete flow from binary to exploit:

```
# Static phase (reversing-mcp)
1. create_session(name="ctf")
2. ingest_and_triage_artifact(session_id, path="/workspace/challenge", analyze=true)
3. analyze_and_summarize(session_id, artifact_id)
4. decompile_function(session_id, artifact_id, name="main")
5. export_dynamic_manifest(session_id, artifact_id)

# Dynamic phase (pwn-mcp)
6. create_execution_session()
7. checksec(session_id, binary_path="challenge")
   → Check RELRO, canary, NX, PIE
8. start_debug_session(session_id, binary_path="challenge")
9. auto_set_breakpoints(session_id, debug_id, manifest_path="...")
10. generate_cyclic_pattern(length=500)
11. # Send pattern as input, observe crash
12. read_registers(session_id, debug_id)
13. find_cyclic_offset(value="0x41414141")  # value from crash
14. get_rop_gadgets(session_id, binary_path="challenge", search="pop rdi")
15. find_one_gadgets(session_id, libc_path="/path/to/libc.so.6")
16. run_pwntools_script(session_id, script="""
from pwn import *
elf = ELF(WORKSPACE + '/challenge')
# ... build exploit ...
""")
```

### Vulnerability Research with Coverage And Solving

```
1. create_execution_session()
2. checksec(session_id, binary_path="target")
3. run_with_coverage(session_id, binary_path="target")
4. get_coverage_report(session_id, coverage_id)
5. run_z3_script(session_id, script="...")

# Debug candidate path/input
6. start_debug_session(session_id, binary_path="target")
7. # ... set breakpoints, run with candidate input, inspect state
```

### Malware Behavioral Analysis

```
# Static characterization (reversing-mcp)
1. ingest_and_triage_artifact(session_id, path, analyze=true)
2. analyze_and_summarize(session_id, artifact_id, focus="malware")
3. detect_packer(session_id, artifact_id)
4. deobfuscate_strings(session_id, artifact_id)
5. scan_with_yara(session_id, artifact_id)
6. detect_crypto_constants(session_id, artifact_id)

# Dynamic observation (pwn-mcp)
7. run_with_strace(session_id, binary_path, syscall_filter="network,file")
8. start_frida_session(session_id, binary_path)
9. hook_function(session_id, frida_id, function_name="connect")
10. hook_function(session_id, frida_id, function_name="send")
11. dump_memory(session_id, frida_id, address="0x...", length=4096)
```

### Binary Patching

```
1. analyze_and_summarize(session_id, artifact_id, focus="patching")
2. prepare_patch_plan(session_id, artifact_id, objective="bypass_check",
     target={"function_id": "fn_..."})
3. find_code_caves(session_id, artifact_id, min_size=32)
4. patch_artifact_assembly(session_id, artifact_id,
     input_kind="virtual_address", value="0x401234",
     assembly="nop; nop", isa="x86_64")
5. diff_artifacts(session_id, left_artifact_id=original, right_artifact_id=patched)
```

### Record/Replay Debugging

For non-deterministic bugs or time-travel debugging:

```
1. start_rr_record(session_id, binary_path="target", args=["input"])
   → Returns recording_id
2. start_rr_replay(session_id, recording_id)
   → Returns debug_id (supports all GDB commands + reverse)
3. set_breakpoint(session_id, debug_id, location="*0x401234")
4. continue_execution(session_id, debug_id)
   → Hits breakpoint
5. reverse_continue(session_id, debug_id)
   → Go back to previous breakpoint
6. reverse_step(session_id, debug_id)
   → Step backwards one line
```

### Constraint Solving

For solving custom checks, checksums, or key validation:

```
run_z3_script(session_id, script="""
# Solve: input XOR 0x42 == target
from z3 import *
s = Solver()
x = BitVec('x', 32)
s.add(x ^ 0x42 == 0xdeadbeef)
_solve_and_print(s, [x])
""")
```

### Cross-Architecture Analysis

pwn-mcp automatically handles non-native architectures:

```
# ARM binary on x86 host — QEMU is transparent
1. create_execution_session()
2. launch_binary(session_id, binary_path="arm_binary")  # auto-detects ARM, uses qemu-arm
3. start_debug_session(session_id, binary_path="arm_binary")  # auto-sets qemu exec-wrapper
4. read_registers(session_id, debug_id)  # returns ARM registers (r0-r15, cpsr)
```

Supported: ARM, AArch64, MIPS/MIPSel, RISC-V 32/64, PowerPC, SPARC, s390x, m68k, SH4, Xtensa.

---

## INTERPRETING OUTPUT

### Triage Results

Key fields to surface to the user:
- `format`: ELF/PE/Mach-O/etc.
- `architecture`: x86_64, arm, mips, etc.
- `mitigations`: NX, RELRO, stack canary, PIE status
- `strings_preview`: first N interesting strings
- `signatures`: any known signatures detected

### Analysis Synopsis

Key fields:
- `function_count`: total recovered functions
- `named_functions`: functions with symbols (vs `sub_XXXX`)
- `import_count`, `export_count`: linkage surface
- `annotation_counts`: existing analyst work

### Checksec Output

Map to exploit strategy:
- **No PIE**: addresses are fixed, ROP/ret2libc straightforward
- **No canary**: stack buffer overflow directly exploitable
- **No NX**: shellcode injection possible
- **Partial RELRO**: GOT overwrite possible
- **Full RELRO**: GOT is read-only, need other write primitives

### Fuzzer Status

Key metrics:
- `execs_per_sec`: throughput (low = possible hang)
- `unique_crashes`: prioritize these
- `unique_hangs`: potential DoS or infinite loops
- `corpus_count`: path coverage growth

---

## GOTCHAS AND LIMITATIONS

1. **Analysis takes time.** `start_artifact_analysis` returns immediately. You MUST poll `get_job` or use `analyze_and_summarize` with `wait_timeout_seconds`. Calling function-level tools before analysis completes returns empty results.

2. **Ghidra requires installation.** `ghidra_decompile`, `ghidra_analyze`, `run_ghidra_script` fail with `ghidra_not_installed` if Ghidra is not in the container. `ghidra_analyze` and `run_ghidra_script` use PyGhidra. `ghidra_decompile` also requires Ghidra's native decompiler executable for the container architecture; the ARM64 image reports `ghidra_decompiler_unavailable` because upstream Ghidra does not ship `linux_arm_64/decompile`.

3. **rr is x86/x64 only.** `start_rr_record` fails on ARM/MIPS/etc. Also requires `perf_event_paranoid <= 1` on the host.

4. **Reanalysis invalidates IDs.** After `start_artifact_reanalysis`, all `function_id` and `string_id` values from the previous generation are invalid. Re-query functions/strings.

5. **Script tools are sandboxed.** `run_pwntools_script`, `run_z3_script`, `run_angr_script`, `run_qiling_script`, `run_boofuzz_script` run in subprocess with timeout. They cannot access MCP state directly. Use `WORKSPACE` and `OUTPUT_DIR` env vars for file I/O.

6. **Memory dump size limit.** `dump_memory` (Frida) maxes at 64KB. For larger dumps, make multiple calls.

7. **AFL++ is intentionally out of scope.** This CTF harness keeps Z3 symbolic solving, Frida, coverage, and boofuzz protocol scripts, but does not install or expose AFL++.

8. **Partial results.** Extraction and carving may return `"partial": true` when hitting byte/artifact/recursion limits. Always check this field.

9. **Bridge manifest freshness.** `export_dynamic_manifest` captures the current analysis state. If you reanalyze, re-export.

10. **GDB session lifecycle.** A debug session holds resources (GDB process, inferior). Always `stop_debug_session` when done, or `destroy_execution_session` to clean everything.

11. **Composite briefs are bounded.** `analyze_and_summarize` and friends return previews, not full data. Use lower-level tools for complete function lists, full disassembly, etc.

12. **YARA fallback.** If `yara-python` is not installed, `scan_with_yara` uses a built-in heuristic engine (less capable but functional).

13. **Cross-arch GDB.** `start_debug_session` for non-native binaries uses `gdb-multiarch` with a QEMU gdbstub. Some GDB features may behave differently.

14. **Frida requires native execution.** Frida cannot instrument QEMU-emulated processes. Use GDB for cross-arch dynamic analysis.

15. **`function_id` vs `name` vs `address`.** Many tools accept all three. Prefer `function_id` when you have it (stable within a generation). Fall back to `name` or `address` for ad-hoc queries.
