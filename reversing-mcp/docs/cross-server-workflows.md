# Cross-Server Workflows

This guide covers workflows that span both `reversing-mcp` (static analysis) and `pwn-mcp` (dynamic analysis). The two servers share a workspace volume and can exchange structured data through the static-dynamic bridge.

## The Static-Dynamic Bridge

reversing-mcp can export a JSON manifest containing all discovered functions, strings, and imports for an artifact. pwn-mcp can import this manifest to set up debug sessions informed by static analysis.

### Export from reversing-mcp

```json
{"tool": "export_dynamic_manifest", "arguments": {
  "session_id": "sess_...",
  "artifact_id": "art_..."
}}
```

This writes a JSON file to the shared workspace containing:
- Function names and addresses
- Extracted strings and their addresses
- Import symbols and addresses
- Artifact metadata (architecture, format, path)

### Import into pwn-mcp

```json
{"tool": "import_static_analysis", "arguments": {
  "session_id": "exec_...",
  "manifest_path": "/workspace/dynamic-output/manifest_art_xxx.json"
}}
```

Returns the parsed function list, strings, and imports for reference during dynamic analysis.

### Auto-Set Breakpoints

```json
{"tool": "auto_set_breakpoints", "arguments": {
  "session_id": "exec_...",
  "debug_id": "dbg_...",
  "manifest_path": "/workspace/dynamic-output/manifest_art_xxx.json",
  "filter_pattern": "main|check_|verify_"
}}
```

Automatically sets GDB breakpoints on all (or filtered) functions discovered by static analysis.

## End-to-End Workflows

### 1. Comprehensive Binary Analysis

Static triage, then targeted dynamic investigation:

```
# Static phase (reversing-mcp)
create_session -> add_artifact -> ingest_and_triage_artifact
-> analyze_and_summarize -> hunt_interesting_regions
-> export_dynamic_manifest

# Dynamic phase (pwn-mcp)
create_execution_session -> import_static_analysis
-> checksec -> start_debug_session -> auto_set_breakpoints
-> continue_execution -> get_backtrace -> read_registers
```

### 2. Vulnerability Research Pipeline

Find interesting code statically, validate dynamically:

```
# Static: identify targets
analyze_and_summarize(focus="general")
-> hunt_interesting_regions(objective="vulnerability")
-> trace_capability (expand suspicious functions)
-> scan_with_yara (check for known patterns)
-> decompile_function (read pseudo-C)
-> export_dynamic_manifest

# Dynamic: validate and exploit
checksec -> run_with_strace (observe syscalls)
-> start_debug_session -> auto_set_breakpoints(filter_pattern="vuln_func")
-> run_with_coverage (measure code reached)
-> run_z3_script (solve path constraints/checksums)
-> start_debug_session (debug candidate input)
-> get_backtrace -> read_memory -> get_heap_info
```

### 3. CTF Pwn Challenge

Fast triage to working exploit:

```
# Static: understand the binary
ingest_and_triage_artifact(analyze=true)
-> analyze_and_summarize -> decompile_function
-> list_artifact_strings -> list_artifact_xrefs
-> export_dynamic_manifest

# Dynamic: build exploit
checksec -> start_debug_session -> auto_set_breakpoints
-> generate_cyclic_pattern -> send_input (crash it)
-> read_registers -> find_cyclic_offset
-> get_rop_gadgets -> find_one_gadgets
-> identify_libc(symbols={"puts": "0x..."})
-> download_libc -> patch_binary_libc
-> run_z3_script (solve constraints)
-> run_pwntools_script (craft final exploit)
```

### 4. Malware Analysis

Safe static analysis with controlled dynamic observation:

```
# Static: characterize the sample
ingest_and_triage_artifact -> analyze_and_summarize(focus="malware")
-> detect_packer -> calculate_entropy
-> deobfuscate_strings -> detect_crypto_constants
-> scan_with_yara -> fingerprint_compiler_toolchain
-> carve_embedded_artifacts(recurse=true)
-> export_dynamic_manifest

# Dynamic: observe behavior
run_with_strace -> run_with_ltrace
-> start_frida_session -> hook_function(function_name="connect")
-> hook_function(function_name="send")
-> trace_calls -> get_memory_ranges
-> dump_memory (extract decrypted payloads)
```

### 5. Firmware Analysis

Static extraction with dynamic component testing:

```
# Static: extract and catalog
ingest_and_triage_artifact -> analyze_and_summarize(focus="firmware")
-> extract_resources -> carve_embedded_artifacts(recurse=true, attach_to_session=true)
-> correlate_session_artifacts
-> batch_query_artifacts(operation="classify_functions")

# For each extracted binary:
-> triage_artifact -> start_artifact_analysis
-> identify_system_calls -> get_runtime_metadata
-> export_dynamic_manifest

# Dynamic: test extracted components
launch_binary (via QEMU for embedded arch)
-> run_with_strace -> start_debug_session
```

### 6. Patch Development

Plan patches statically, validate dynamically:

```
# Static: plan the patch
analyze_and_summarize(focus="patching")
-> prepare_patch_plan(objective="bypass_check", target={"function_id": "fn_..."})
-> find_code_caves -> get_artifact_instruction_mode
-> patch_artifact_assembly (apply the patch)
-> diff_artifacts (compare original vs patched)
-> export_dynamic_manifest (for patched binary)

# Dynamic: validate the patch
start_debug_session (on patched binary)
-> auto_set_breakpoints -> continue_execution
-> run_with_coverage (verify patched path is taken)
-> diff_coverage (compare original vs patched coverage)
```

## Shared Workspace Layout

```
runtime/workspace/              # Shared volume
  samples/                      # Input binaries
    target.elf
  .reversing-mcp/               # reversing-mcp session state
    sessions/
  dynamic-output/               # pwn-mcp output
    manifest_art_xxx.json       # Bridge manifests
    traces/                     # strace/ltrace output
    coverage/                   # drcov files
    protocol-fuzzing/           # boofuzz scripts and logs, if used
```

## Tips

- Always `checksec` before debugging to understand protections (PIE affects addresses, canaries affect overflow strategy)
- Use `analyze_and_summarize` with appropriate `focus` preset to get relevant static context efficiently
- Export manifests before starting debug sessions to enable `auto_set_breakpoints`
- Use composite brief tools (`ingest_and_triage_artifact`, `hunt_interesting_regions`) to reduce token usage during static analysis
- For CTF challenges, the typical flow is: static triage -> checksec -> debug -> find offset -> find gadgets -> write exploit
- `run_z3_script` is useful for solving custom encoding/encryption checks found during static analysis
- `identify_libc` + `patch_binary_libc` lets you match the exact remote libc for reliable exploit development
