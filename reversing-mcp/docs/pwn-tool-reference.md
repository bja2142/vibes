# PWN-MCP Tool Reference

Complete reference for all 90 tools in the `pwn-mcp` dynamic analysis server. Tools are grouped by function.

## Session Management

### `create_execution_session(name?)`

Create an isolated execution session. All binaries launched within a session share a working directory and are cleaned up together on destroy.

### `list_execution_sessions()`

List all active execution sessions.

### `destroy_execution_session(session_id)`

Destroy an execution session: kill all running processes and clean up the session working directory.

## Process Control

### `launch_binary(session_id, binary_path, args?, stdin_data?, env?, timeout_seconds?)`

Launch a binary inside a session. Architecture is auto-detected from the ELF header; non-native architectures are transparently executed via QEMU user-mode. Stdin/stdout/stderr are piped.

### `send_input(session_id, process_id, data)`

Send text (UTF-8) to a running process's stdin.

### `read_output(session_id, process_id, stream?, max_lines?)`

Read buffered output from a running or exited process. Output is captured by background reader threads and stored in a ring buffer.

- `stream`: `stdout` (default) or `stderr`

### `get_process_state(session_id, process_id)`

Get current state of a launched process (running, exited, exit code, output buffer sizes).

### `terminate_process(session_id, process_id, signal?)`

Send a signal to a running process. Default signal is SIGTERM.

## GDB Debugging

### `start_debug_session(session_id, binary_path, args?, env?)`

Start a GDB/MI debug session. Returns a `debug_id` used by all other GDB tools. Automatically sets QEMU exec-wrapper for non-native architectures.

### `stop_debug_session(session_id, debug_id)`

Terminate a GDB debug session and free its resources.

### `send_gdb_command(session_id, debug_id, command)`

Send an arbitrary GDB command. Prefix with `-` for raw MI commands (e.g. `-exec-continue`); otherwise treated as a CLI command.

### `set_breakpoint(session_id, debug_id, location)`

Set a breakpoint at a function name, source location, or hex address.

### `delete_breakpoint(session_id, debug_id, breakpoint_number)`

Delete a breakpoint by its number.

### `list_breakpoints(session_id, debug_id)`

List all breakpoints in the current debug session.

### `continue_execution(session_id, debug_id)`

Resume execution until the next breakpoint or program exit.

### `step_instruction(session_id, debug_id)`

Execute a single machine instruction, stepping into calls.

### `step_over_instruction(session_id, debug_id)`

Execute a single machine instruction, stepping over calls.

### `step_into(session_id, debug_id)`

Step into the next source line (step into function calls).

### `step_over(session_id, debug_id)`

Step over the next source line (step over function calls).

### `finish_function(session_id, debug_id)`

Run until the current function returns.

### `run_until(session_id, debug_id, location)`

Run until the given location is reached.

### `read_registers(session_id, debug_id, register_names?)`

Read CPU register values. Pass `register_names` to read specific registers; omit for all.

### `write_register(session_id, debug_id, register_name, value)`

Set a CPU register to a value.

### `read_memory(session_id, debug_id, address, length?)`

Read bytes from the inferior's address space. Returns hex and ASCII.

### `write_memory(session_id, debug_id, address, hex_bytes)`

Write bytes to the inferior's address space.

### `search_memory(session_id, debug_id, pattern, start_address?, end_address?)`

Search the inferior's memory for a byte pattern.

### `dump_memory_region(session_id, debug_id, address, length, output_filename?)`

Dump a bounded region of inferior memory into the session output directory. The output path is returned and remains under `PWN_MCP_OUTPUT_ROOT`.

### `get_backtrace(session_id, debug_id, limit?)`

Get the call stack at the current position.

### `get_locals(session_id, debug_id, frame?)`

List local variables and their values in the specified frame.

### `evaluate_expression(session_id, debug_id, expression)`

Evaluate a GDB expression in the current context.

### `get_memory_maps(session_id, debug_id)`

Show memory maps (segments, permissions, file mappings) of the running process.

### `get_heap_info(session_id, debug_id)`

Inspect heap structure using GEF or pwndbg heap commands. Shows bins, chunks, and tcache state.

### `analyze_heap(session_id, debug_id)`

Run framework-aware heap inspection commands and return raw command output, allocator inference for ptmalloc2/jemalloc/tcmalloc, and a compact summary for heap, tcache, fastbin, and chunk mentions.

### `find_format_string_vulns(session_id, binary_path, max_findings?)`

Heuristically scan a binary for printf-family format-string attack surface. Findings identify sink calls and format-string literals; confirm attacker control dynamically before treating a hit as exploitable.

### `get_libc_info(session_id, debug_id)`

Show shared libraries loaded by the process, including libc path and base address.

## Frida Dynamic Instrumentation

### `start_frida_session(session_id, binary_path, args?)`

Spawn a binary under Frida for dynamic instrumentation. Returns a `frida_id`.

### `stop_frida_session(session_id, frida_id)`

Stop a Frida session: detach from process, unload scripts, and clean up.

### `inject_script(session_id, frida_id, script_source, script_name?)`

Inject a Frida JavaScript script into the target process.

### `hook_function(session_id, frida_id, function_name?, address?, on_enter?, on_leave?)`

Hook a function by name or address using Frida Interceptor. Provide custom JS bodies for `onEnter` and `onLeave` handlers.

### `trace_calls(session_id, frida_id, module_name?, function_pattern?)`

Trace function calls in a module or the main binary using Frida Interceptor.

### `get_exports(session_id, frida_id, module_name?)`

List exports of a loaded module, or list all loaded modules if `module_name` is omitted.

### `get_memory_ranges(session_id, frida_id, protection?)`

List memory ranges with specific protection (e.g. `rwx`, `r-x`). Default: `r--`.

### `dump_memory(session_id, frida_id, address, length?)`

Dump raw bytes from a process memory address. Max 64KB. Returns hex string.

## Record/Replay (rr)

x86/x86_64 only. Requires `perf_event_paranoid <= 1`.

### `start_rr_record(session_id, binary_path, args?, env?)`

Record a deterministic execution trace with rr. Returns a `recording_id`.

### `start_rr_replay(session_id, recording_id)`

Start an rr replay debug session. Returns a `debug_id` that supports all GDB commands plus reverse execution.

### `list_recordings(session_id)`

List all rr recordings in a session.

### `reverse_continue(session_id, debug_id)`

Reverse-continue execution (run backwards to the previous breakpoint or start).

### `reverse_step(session_id, debug_id)`

Reverse single-step one source line.

### `reverse_next(session_id, debug_id)`

Reverse step over one source line.

### `reverse_finish(session_id, debug_id)`

Reverse-finish: run backwards until the start of the current function.

## Tracing

### `run_with_strace(session_id, binary_path, args?, filter_syscalls?, timeout_seconds?)`

Run a binary under strace to capture system calls.

### `run_with_ltrace(session_id, binary_path, args?, filter_functions?, timeout_seconds?)`

Run a binary under ltrace to capture library calls.

### `run_with_uftrace(session_id, binary_path, args?, timeout_seconds?)`

Run a binary under uftrace for structured function call tracing.

### `run_with_valgrind(session_id, binary_path, args?, tool?, timeout_seconds?)`

Run a binary under Valgrind. Supported tools: `memcheck` (default), `callgrind`, `helgrind`, `massif`, `dhat`, `cachegrind`.

### `get_trace_output(session_id, trace_id)`

Retrieve stored trace output by `trace_id` (returned by the `run_with_*` tools).

## Code Coverage

### `run_with_coverage(session_id, binary_path, args?, timeout_seconds?)`

Run a binary under DynamoRIO to collect basic-block code coverage (drcov format).

### `get_coverage_report(session_id, coverage_id)`

Parse a drcov coverage log and return module list, block count, and optionally sampled basic block identities.

### `diff_coverage(session_id, coverage_id_a, coverage_id_b)`

Compare two coverage runs and report new/dropped drcov basic blocks where block data is available.

## Exploit Tools

### `checksec(session_id, binary_path)`

Run checksec on a binary to detect security properties (RELRO, stack canary, NX, PIE, FORTIFY).

### `run_pwntools_script(session_id, script, timeout_seconds?)`

Execute a pwntools Python script. `from pwn import *` is pre-imported. `WORKSPACE` and `OUTPUT_DIR` variables are available. Use for crafting payloads, ROP chains, I/O interaction, and exploit prototyping.

### `generate_cyclic_pattern(session_id, length?)`

Generate a De Bruijn cyclic pattern for finding crash offsets (buffer overflow exploitation).

### `find_cyclic_offset(session_id, value)`

Find the offset of a value in a cyclic pattern. Pass the value found at crash (e.g. EIP/RIP value as hex).

### `find_one_gadgets(session_id, binary_path)`

Find one-shot RCE gadgets (`execve('/bin/sh')`) in a libc binary.

### `get_rop_gadgets(session_id, binary_path, query?, max_results?)`

Search for ROP gadgets in a binary using ropper or ROPgadget.

## Seccomp Analysis

### `analyze_seccomp(session_id, binary_path, args?)`

Dump and disassemble seccomp BPF filters installed by a binary. Returns raw output plus parsed syscall/action summaries and sandbox archetype hints.

## Constraint Solving

### `run_z3_script(session_id, script, timeout_seconds?)`

Run a Z3 constraint solver script. `from z3 import *` is pre-imported along with a `_solve_and_print()` helper. Use for solving buffer overflow offsets, checksum constraints, custom XOR transformations, angr-style path constraints, etc.

## Symbolic Execution

### `run_angr_script(session_id, script, timeout_seconds?)`

Run a Python script with `angr`, `claripy`, and `cle` pre-imported. `WORKSPACE` and `OUTPUT_DIR` point at the shared workspace and per-session output directory.

### `get_angr_project_info(session_id, binary_path)`

Load a workspace binary in angr and report architecture, bitness, entrypoint, loader objects, and symbol/function hints.

### `angr_find_path(session_id, binary_path, find_address, avoid_address?, stdin_size?, timeout_seconds?)`

Use angr to solve symbolic stdin bytes that reach a target address, optionally avoiding an address. Returns parsed stdin as hex and printable ASCII when a path is found.

## Emulation

### `emulate_blob_unicorn(session_id, arch, code_hex, start_address?, registers?, memory_size?, timeout_seconds?)`

Run raw instruction bytes under Unicorn for quick shellcode and gadget behavior checks. Returns final registers, execution metadata, and any emulator error.

### `run_qiling_script(session_id, script, timeout_seconds?)`

Run a Python script with Qiling pre-imported for OS-aware emulation workflows. `WORKSPACE` and `OUTPUT_DIR` are available.

## Assembly and Disassembly

### `assemble_code(session_id, assembly, arch, syntax?, base_address?, backend?)`

Assemble short snippets using Keystone or NASM. Returns bytes as hex and the backend used.

### `disassemble_bytes(session_id, code_hex, arch, base_address?, syntax?, max_instructions?, backend?)`

Disassemble raw bytes with Capstone or rasm2 and return structured instruction records.

### `disassemble_file_region(session_id, binary_path, offset, length, arch, base_address?, max_instructions?)`

Read a bounded region from a workspace file and disassemble it.

## Reverse-Engineering Triage

### `run_capa(session_id, binary_path, output_format?, rules_path?, timeout_seconds?)`

Run FLARE capa. The container bundles `/opt/capa-rules` and the wrapper passes it automatically unless `rules_path` is provided.

### `run_floss(session_id, binary_path, output_format?, analysis_types?, timeout_seconds?)`

Run FLARE FLOSS. Defaults to `analysis_types=["static"]` so ELF CTF binaries work; pass `["all"]` for full PE decoding.

### `run_yara_scan(session_id, target_path, rule_source?, rule_path?, show_strings?, timeout_seconds?)`

Run YARA against a workspace file with inline rule source or a workspace rule file.

### `run_radare2_command(session_id, binary_path, commands?, timeout_seconds?)`

Run bounded read-only radare2 commands such as `ij`, `iSj`, and `aflj` against a workspace binary.

## Protocol Fuzzing

### `run_boofuzz_script(session_id, script, timeout_seconds?)`

Run a boofuzz protocol fuzzing script. `from boofuzz import *` is pre-imported. Use for grammar-based network protocol fuzzing against TCP/UDP services.

## libc Tools

### `identify_libc(session_id, symbols)`

Identify libc version from leaked function addresses (e.g. `{"puts": "0x7f..."`}). Uses libc-database.

### `list_available_libcs(session_id)`

List libc versions available for download and already downloaded via glibc-all-in-one.

### `download_libc(session_id, libc_id)`

Download a specific libc version using glibc-all-in-one.

### `patch_binary_libc(session_id, binary_path, interpreter?, rpath?)`

Use patchelf to set the ELF interpreter and/or rpath on a binary for libc version swapping.

### `get_elf_metadata(session_id, binary_path)`

Get current ELF interpreter, rpath, and needed libraries for a binary using patchelf.

## Static-Dynamic Bridge

### `import_static_analysis(session_id, manifest_path)`

Import a static analysis manifest exported by reversing-mcp (`export_dynamic_manifest`). Returns functions, strings, and imports with addresses.

### `auto_set_breakpoints(session_id, debug_id, manifest_path, filter_names?)`

Import a reversing-mcp manifest and auto-set GDB breakpoints on all (or filtered) functions in an active debug session.

## Job Management

### `get_job(session_id, job_id)`

Get the status and result of an async job.

### `cancel_job(session_id, job_id)`

Cancel a running async job.

### `list_jobs(session_id, status?)`

List all jobs for a given session.

## Diagnostics

### `validate_toolchain(run_probes?, timeout_seconds?)`

Report installed backend availability and the MCP tools that expose each backend. With `run_probes=true`, run lightweight version probes for CLI tools.

## Supported Architectures

| Architecture | Native | QEMU User-Mode |
|---|---|---|
| x86 / x86_64 | Yes | — |
| ARM / AArch64 | — | Yes |
| MIPS / MIPSel / MIPS64 | — | Yes |
| PowerPC / PPC64 | — | Yes |
| RISC-V 32 / 64 | — | Yes |
| SPARC / SPARC64 | — | Yes |
| s390x | — | Yes |
| m68k | — | Yes |
| SH4 | — | Yes |
| Xtensa | — | Yes |

Non-native binaries are automatically detected via ELF header inspection and executed through QEMU user-mode with transparent `binfmt_misc` registration. GDB sessions automatically configure the appropriate QEMU exec-wrapper.

## Notes

- All tools require a `session_id` from `create_execution_session`
- GDB tools require a `debug_id` from `start_debug_session` or `start_rr_replay`
- Frida tools require a `frida_id` from `start_frida_session`
- Script tools (`run_pwntools_script`, `run_z3_script`, `run_angr_script`, `run_qiling_script`, `run_boofuzz_script`) execute in isolated subprocesses with configurable timeouts
- `validate_toolchain` maps installed external tools and Python packages to their MCP wrappers
- capa uses bundled `/opt/capa-rules` by default because the PyPI package does not include rules
- rr recording requires x86/x86_64 and `perf_event_paranoid <= 1`
