# Reversing MCP + PWN MCP

A two-server MCP system for end-to-end binary analysis: static reverse engineering and dynamic exploitation.

| Server | Port | Purpose | Tool Count |
|---|---|---|---|
| `reversing-mcp` | 6767 | Static analysis: disassembly, decompilation, semantic recovery, signatures, patching | 98 |
| `pwn-mcp` | 6768 | Dynamic analysis: GDB, Frida, rr, pwntools, angr, Qiling, RE triage | 90 |

Both servers share a workspace volume so static analysis results feed directly into dynamic workflows.

## Quick Start

```bash
cp .env.example .env
docker compose up -d --build
```

### MCP Client Configuration

Add both servers to your MCP client (e.g. Claude Code `~/.claude.json`):

```json
{
  "mcpServers": {
    "reversing-mcp": {
      "type": "http",
      "url": "http://127.0.0.1:6767/mcp"
    },
    "pwn-mcp": {
      "type": "http",
      "url": "http://127.0.0.1:6768/mcp"
    }
  }
}
```

Both servers are wired for streamable HTTP at `/mcp`. SSE compatibility remains available when a server is launched with `--transport sse` or `--transport both`.

### Place Binaries in the Workspace

```bash
cp /path/to/target.elf runtime/workspace/
```

Both servers can access files under `runtime/workspace/`.

## Architecture

```
                    MCP Client (Claude Code, etc.)
                     /                        \
               SSE/HTTP                    SSE/HTTP
                 /                              \
    +-----------------------+     +-----------------------+
    |    reversing-mcp      |     |       pwn-mcp         |
    |    (static only)      |     |   (dynamic execution) |
    |                       |     |                       |
    | angr, Ghidra, YARA,   |     | GDB, Frida, rr,      |
    | FLOSS, pyelftools,    |     | rr, pwntools, Z3,    |
    | pefile, macholib      |     | boofuzz, Valgrind,   |
    |                       |     | DynamoRIO, strace    |
    +-----------+-----------+     +-----------+-----------+
                |                             |
                +---------- /workspace -------+
                     (shared volume mount)
```

### Static-Dynamic Bridge

reversing-mcp can export a JSON manifest of functions, strings, and imports via `export_dynamic_manifest`. pwn-mcp can import it via `import_static_analysis` and auto-set GDB breakpoints on discovered functions via `auto_set_breakpoints`. This eliminates manual address copying between static and dynamic analysis.

## reversing-mcp

Static binary analysis server with persistent sessions, artifact management, and structured results.

### Capabilities

- **Format support**: ELF, PE, Mach-O, ZIP, TAR, Intel HEX, SREC, raw blobs
- **Analysis backends**: angr CFGFast, Ghidra headless decompiler
- **Semantic recovery**: call graphs, CFGs, variables, stack frames, types, data-flow slicing, system calls
- **Signatures**: YARA scanning, crypto constants, packer detection, compiler fingerprinting, FLOSS string deobfuscation
- **Patching**: byte patches, assembly patches (x86/x64/ARM/AArch64/Thumb), code cave discovery
- **Multi-artifact**: session-wide correlation, structural diffing, dependency analysis
- **Workflow**: annotations with revision history, session snapshots, curated exports, composite brief workflows with token budgeting

### Tool Groups (98 tools)

| Group | Tools |
|---|---|
| Discovery | `describe_tools`, `get_capabilities`, `get_runtime_policies`, `run_parser_probe` |
| Sessions | `create_session`, `load_session`, `list_sessions`, `destroy_session`, `update_session_settings` |
| Artifacts | `add_artifact`, `list_artifacts`, `remove_artifact` |
| Triage | `triage_artifact`, `list_artifact_strings`, `translate_artifact_address`, `list_artifact_children`, `lookup_external_enrichment` |
| Analysis | `start_artifact_analysis`, `start_artifact_reanalysis`, `get_job`, `list_jobs`, `cancel_job`, `get_analysis_synopsis` |
| Disassembly | `list_artifact_symbols`, `list_artifact_functions`, `disassemble_function`, `disassemble_range`, `decompile_function`, `read_artifact_bytes`, `get_artifact_instruction_mode`, `set_artifact_instruction_mode` |
| Cross-refs | `list_artifact_xrefs`, `search_artifact`, `get_artifact_linkage`, `get_artifact_debug_info` |
| Semantic | `get_call_graph`, `get_control_flow_graph`, `get_function_variables`, `get_stack_frame`, `get_constant_propagation`, `get_type_information`, `recover_types`, `inspect_data_segments`, `get_indirect_flows`, `get_exception_metadata`, `get_calling_convention`, `get_intermediate_representation`, `get_runtime_metadata`, `slice_data_flow`, `identify_system_calls`, `navigate_neighborhood`, `prioritize_functions`, `classify_functions` |
| Signatures | `scan_with_yara`, `fingerprint_compiler_toolchain`, `detect_packer`, `calculate_entropy`, `deobfuscate_strings`, `detect_crypto_constants`, `recognize_library_code` |
| Extraction | `extract_resources`, `carve_embedded_artifacts`, `get_artifact_relationships` |
| Patching | `patch_artifact_bytes`, `patch_artifact_assembly`, `find_code_caves`, `edit_artifact_metadata`, `import_type_definitions` |
| Multi-artifact | `list_artifact_dependencies`, `correlate_session_artifacts`, `diff_artifacts` |
| Workflow | `save_workflow_item`, `list_workflow_items`, `register_provisional_function`, `register_provisional_string`, `get_object_reference`, `put_annotation`, `list_annotations`, `get_annotation_history`, `revert_annotation` |
| Snapshots | `create_session_snapshot`, `list_session_snapshots`, `restore_session_snapshot` |
| Exports | `export_curated_analysis`, `export_command_log`, `export_analysis_report`, `export_session_state`, `batch_query_artifacts` |
| Composite briefs | `ingest_and_triage_artifact`, `analyze_and_summarize`, `hunt_interesting_regions`, `trace_capability`, `prepare_patch_plan`, `artifact_relationship_brief` |
| Ghidra | `ghidra_decompile`, `ghidra_analyze`, `run_ghidra_script` |
| Bridge | `export_dynamic_manifest` |

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `REVERSING_MCP_WORKSPACE_ROOT` | `/workspace` | Workspace root for sessions and artifacts |
| `REVERSING_MCP_LOG_LEVEL` | `INFO` | Server log level |
| `REVERSING_MCP_MAX_INPUT_SIZE_BYTES` | — | Max accepted input size |
| `REVERSING_MCP_MAX_ARTIFACTS_PER_SESSION` | — | Artifact count limit per session |
| `REVERSING_MCP_PARSER_TIMEOUT_SECONDS` | — | Isolated parser timeout |
| `REVERSING_MCP_PARSER_MEMORY_MB` | — | Isolated parser memory limit |
| `REVERSING_MCP_HTTP_REQUIRE_AUTH` | `false` | Enable bearer token auth |
| `REVERSING_MCP_HTTP_TOKENS` | — | Comma-separated `tenant=token` pairs |

## pwn-mcp

Dynamic analysis server for process control, debugging, instrumentation, solving, emulation, reverse-engineering triage, and exploitation.

### Capabilities

- **Process control**: launch, I/O, signal, state inspection
- **GDB debugging**: full GDB/MI integration with breakpoints, stepping, registers, memory, heap inspection
- **Frida instrumentation**: function hooking, script injection, memory dump, export listing, call tracing
- **Record/replay**: deterministic rr recording with reverse stepping
- **Tracing**: strace (syscalls), ltrace (library calls), uftrace (call graphs), Valgrind (memory/performance)
- **Protocol fuzzing**: boofuzz script execution for targeted network services
- **Symbolic execution**: angr scripts, project summaries, and stdin path solving
- **Emulation**: Unicorn blob emulation and Qiling script execution
- **Assembly/disassembly**: Keystone/NASM assembly and Capstone/rasm2 disassembly
- **RE triage**: capa, FLOSS, YARA, and bounded read-only radare2 commands
- **Exploit tools**: pwntools scripting, cyclic patterns, one_gadget, ROP gadgets, checksec
- **Constraint solving**: Z3 scripts for offset/checksum/transformation solving
- **libc management**: version identification, download, binary patching with patchelf
- **Coverage**: DynamoRIO drcov collection and diffing
- **Seccomp**: BPF filter analysis
- **Cross-arch**: transparent QEMU user-mode for ARM, AArch64, MIPS, RISC-V, PowerPC, SPARC, s390x, m68k, SH4, Xtensa

### Tool Groups (90 tools)

| Group | Tools |
|---|---|
| Sessions | `create_execution_session`, `list_execution_sessions`, `destroy_execution_session` |
| Process | `launch_binary`, `send_input`, `read_output`, `get_process_state`, `terminate_process` |
| GDB | `start_debug_session`, `stop_debug_session`, `send_gdb_command`, `set_breakpoint`, `delete_breakpoint`, `list_breakpoints`, `continue_execution`, `step_instruction`, `step_over_instruction`, `step_into`, `step_over`, `finish_function`, `run_until`, `read_registers`, `write_register`, `read_memory`, `write_memory`, `search_memory`, `dump_memory_region`, `get_backtrace`, `get_locals`, `evaluate_expression`, `get_memory_maps`, `get_heap_info`, `analyze_heap`, `find_format_string_vulns`, `get_libc_info` |
| Frida | `start_frida_session`, `stop_frida_session`, `inject_script`, `hook_function`, `trace_calls`, `get_exports`, `get_memory_ranges`, `dump_memory` |
| Record/replay | `start_rr_record`, `start_rr_replay`, `list_recordings`, `reverse_continue`, `reverse_step`, `reverse_next`, `reverse_finish` |
| Tracing | `run_with_strace`, `run_with_ltrace`, `run_with_uftrace`, `run_with_valgrind`, `get_trace_output` |
| Coverage | `run_with_coverage`, `get_coverage_report`, `diff_coverage` |
| Exploit | `checksec`, `run_pwntools_script`, `generate_cyclic_pattern`, `find_cyclic_offset`, `find_one_gadgets`, `get_rop_gadgets` |
| Seccomp | `analyze_seccomp` |
| Solver | `run_z3_script` |
| Symbolic execution | `run_angr_script`, `get_angr_project_info`, `angr_find_path` |
| Emulation | `emulate_blob_unicorn`, `run_qiling_script` |
| Assembly/disassembly | `assemble_code`, `disassemble_bytes`, `disassemble_file_region` |
| RE triage | `run_capa`, `run_floss`, `run_yara_scan`, `run_radare2_command` |
| Protocol fuzzing | `run_boofuzz_script` |
| libc tools | `identify_libc`, `list_available_libcs`, `download_libc`, `patch_binary_libc`, `get_elf_metadata` |
| Bridge | `import_static_analysis`, `auto_set_breakpoints` |
| Jobs | `get_job`, `cancel_job`, `list_jobs` |
| Diagnostics | `validate_toolchain` |

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PWN_MCP_WORKSPACE_ROOT` | `/workspace/binaries` | Binary input directory |
| `PWN_MCP_OUTPUT_ROOT` | `/workspace/dynamic-output` | Output directory for traces, coverage, etc. |
| `PWN_MCP_SESSIONS_ROOT` | `/tmp/pwn-mcp-sessions` | Session working directories. The compose file places these under the writable dynamic output volume. |
| `PWN_MCP_LOG_LEVEL` | `INFO` | Server log level |
| `PWN_MCP_PORT` | `6768` | Server port |
| `GDBINIT_FRAMEWORK` | `gef` | GDB enhancement framework (`gef` or `pwndbg`) |

## Docker Compose Services

| Service | Image | Port | Purpose |
|---|---|---|---|
| `reversing-mcp` | `reversing-mcp:compose` | 6767 | Static analysis server |
| `pwn-mcp` | `pwn-mcp:compose` | 6768 | Dynamic analysis server (SYS_PTRACE enabled) |
| `pwn-mcp-test` | `pwn-mcp:compose` | — | Test runner (profile: `test`) |

```bash
# Start both servers
docker compose up -d --build

# Run pwn-mcp tests
docker compose --profile test run pwn-mcp-test

# Run reversing-mcp tests
docker exec -w /app reversing-mcp-compose pytest -q

# View logs
docker compose logs -f
```

## Host Requirements

- Docker with Compose v2
- For rr record/replay: `echo 1 > /proc/sys/kernel/perf_event_paranoid`
- `SYS_PTRACE` capability is granted to pwn-mcp via docker-compose

## Common Workflows

### 1. Full Binary Analysis Pipeline

```
create_session -> add_artifact -> triage_artifact -> start_artifact_analysis
-> list_artifact_functions -> decompile_function -> export_dynamic_manifest
-> (pwn-mcp) create_execution_session -> import_static_analysis
-> start_debug_session -> auto_set_breakpoints -> continue_execution
```

### 2. Vulnerability Research

```
(reversing-mcp) analyze_and_summarize -> hunt_interesting_regions
-> trace_capability (on suspicious functions)
-> (pwn-mcp) launch_binary -> run_with_strace
-> start_debug_session -> set_breakpoint -> read_memory
-> run_pwntools_script (craft exploit)
```

### 3. Coverage And Constraint Triage

```
(pwn-mcp) create_execution_session -> checksec
-> run_with_coverage -> get_coverage_report
-> run_z3_script (solve branch/checksum constraints)
-> start_debug_session -> get_backtrace -> read_registers
```

### 4. CTF Pwn Challenge

```
(reversing-mcp) ingest_and_triage_artifact -> analyze_and_summarize
-> (pwn-mcp) checksec -> start_debug_session
-> generate_cyclic_pattern -> find_cyclic_offset
-> get_rop_gadgets -> find_one_gadgets
-> run_pwntools_script (build exploit)
-> run_z3_script (solve constraints)
```

## Security Model

- **reversing-mcp**: static-only, no binary execution. Samples must live inside workspace root. Parser runs in isolated subprocess.
- **pwn-mcp**: executes binaries in a sandboxed container with seccomp profile. Non-root user (UID 10002). Workspace binaries mounted read-only.

## Documentation

- [Overview](docs/overview.md) — architecture and feature summary
- [Getting Started](docs/getting-started.md) — first session walkthrough
- [Workflows](docs/workflows.md) — common analysis patterns
- [Tool Reference](docs/tool-reference.md) — complete reversing-mcp tool catalog
- [PWN-MCP Tool Reference](docs/pwn-tool-reference.md) — complete pwn-mcp tool catalog
- [Cross-Server Workflows](docs/cross-server-workflows.md) — static-dynamic bridge patterns
- [Requirements Matrix](docs/requirements-matrix.md) — requirements traceability
