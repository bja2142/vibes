# pwn-mcp -- Dynamic Analysis MCP Server

Companion to `reversing-mcp`. Provides 79 dynamic analysis tools for reverse engineering, vulnerability research, exploit development, and CTF challenge solving.

## Architecture

Two separate MCP servers sharing a workspace volume:

| Server | Port | Purpose | Execution |
|---|---|---|---|
| `reversing-mcp` | 6767 | Static analysis (disassembly, decompilation, strings, signatures) | None -- sandboxed parser |
| `pwn-mcp` | 6768 | Dynamic analysis (GDB, Frida, AFL++, rr, tracing, exploitation) | Required -- binaries run |

## Transport

Serves MCP over SSE (`GET /sse`) and streamable HTTP (`POST /sse`) at port `6768`.

MCP client configuration:

```json
{
  "pwn-mcp": {
    "type": "sse",
    "url": "http://127.0.0.1:6768/sse"
  }
}
```

## Supported Architectures

| Architecture | Method |
|---|---|
| x86 / x86_64 | Native |
| ARM / AArch64 | QEMU user-mode |
| MIPS / MIPSel / MIPS64 | QEMU user-mode |
| PowerPC / PPC64 | QEMU user-mode |
| RISC-V 32 / 64 | QEMU user-mode |
| SPARC / SPARC64 | QEMU user-mode |
| s390x, m68k, SH4, Xtensa | QEMU user-mode |

Non-native binaries are auto-detected via ELF header and transparently executed via `qemu-user-static` + `binfmt_misc`. GDB sessions auto-configure QEMU exec-wrappers.

## Tool Inventory (79 tools)

### Process Control (5 tools)
- `create_execution_session` / `list_execution_sessions` / `destroy_execution_session` -- session lifecycle
- `launch_binary` -- run a binary with piped I/O, auto-detects architecture
- `send_input` / `read_output` / `get_process_state` / `terminate_process` -- process interaction

### GDB Debugging (24 tools)
- `start_debug_session` / `stop_debug_session` -- GDB/MI session management
- `send_gdb_command` -- arbitrary GDB CLI or MI commands
- `set_breakpoint` / `delete_breakpoint` / `list_breakpoints` -- breakpoint management
- `continue_execution` / `step_into` / `step_over` / `step_instruction` / `step_over_instruction` / `finish_function` / `run_until` -- execution control
- `read_registers` / `write_register` -- register access
- `read_memory` / `write_memory` / `search_memory` -- memory access
- `get_backtrace` / `get_locals` / `evaluate_expression` -- inspection
- `get_memory_maps` / `get_heap_info` / `get_libc_info` -- process state

### Frida Instrumentation (8 tools)
- `start_frida_session` / `stop_frida_session` -- Frida session lifecycle
- `inject_script` -- inject custom Frida JS into target process
- `hook_function` -- hook by name or address with custom onEnter/onLeave
- `trace_calls` -- trace function calls per module
- `get_exports` / `get_memory_ranges` / `dump_memory` -- runtime inspection

### Record/Replay (7 tools)
- `start_rr_record` / `start_rr_replay` / `list_recordings` -- rr lifecycle
- `reverse_continue` / `reverse_step` / `reverse_next` / `reverse_finish` -- reverse execution (x86/x64 only)

### Tracing (5 tools)
- `run_with_strace` -- syscall tracing
- `run_with_ltrace` -- library call tracing
- `run_with_uftrace` -- structured function call graphs
- `run_with_valgrind` -- memcheck, callgrind, helgrind, massif, dhat, cachegrind
- `get_trace_output` -- retrieve stored trace results

### Code Coverage (3 tools)
- `run_with_coverage` -- DynamoRIO drcov collection
- `get_coverage_report` / `diff_coverage` -- analysis and comparison

### Fuzzing (5 tools)
- `start_afl_session` -- AFL++ QEMU-mode fuzzing (no source needed)
- `get_fuzzer_status` / `get_crash_inputs` / `stop_fuzzer` -- campaign management
- `minimize_input` -- afl-tmin crash minimization

### Exploit Tools (6 tools)
- `checksec` -- binary security properties (RELRO, canary, NX, PIE, FORTIFY)
- `run_pwntools_script` -- full pwntools scripting (`from pwn import *` pre-imported)
- `generate_cyclic_pattern` / `find_cyclic_offset` -- buffer overflow offset discovery
- `find_one_gadgets` -- one-shot RCE gadget search in libc
- `get_rop_gadgets` -- ROP gadget search via ropper/ROPgadget

### Seccomp Analysis (1 tool)
- `analyze_seccomp` -- dump and disassemble BPF filters

### Constraint Solving (1 tool)
- `run_z3_script` -- Z3 solver scripts (`from z3 import *` pre-imported)

### Protocol Fuzzing (1 tool)
- `run_boofuzz_script` -- grammar-based protocol fuzzing (`from boofuzz import *` pre-imported)

### libc Tools (5 tools)
- `identify_libc` -- identify libc version from leaked addresses
- `list_available_libcs` / `download_libc` -- libc version management
- `patch_binary_libc` -- patchelf interpreter/rpath for libc swapping
- `get_elf_metadata` -- current ELF interpreter, rpath, needed libs

### Static-Dynamic Bridge (2 tools)
- `import_static_analysis` -- import reversing-mcp manifest
- `auto_set_breakpoints` -- auto-set GDB breakpoints from static analysis

### Job Management (3 tools)
- `get_job` / `cancel_job` / `list_jobs` -- async job tracking

## Quick Start

```bash
# Start both MCPs
docker compose up -d reversing-mcp pwn-mcp

# Run test suite
docker compose --profile test run pwn-mcp-test

# Fast tests only (no fuzzing)
docker compose --profile test run pwn-mcp-test python3 -m pytest tests/ -m "not slow" -v
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PWN_MCP_WORKSPACE_ROOT` | `/workspace/binaries` | Binary input directory (read-only mount) |
| `PWN_MCP_OUTPUT_ROOT` | `/workspace/dynamic-output` | Output for traces, coverage, fuzzing |
| `PWN_MCP_SESSIONS_ROOT` | `/tmp/pwn-mcp-sessions` | Session working directories |
| `PWN_MCP_LOG_LEVEL` | `INFO` | Server log level |
| `PWN_MCP_PORT` | `6768` | Server port |
| `GDBINIT_FRAMEWORK` | `gef` | GDB enhancement (`gef` or `pwndbg`) |

## Host Requirements

- Docker with Compose v2
- `SYS_PTRACE` capability: granted via docker-compose `cap_add`
- For rr: `echo 1 > /proc/sys/kernel/perf_event_paranoid`

## Security Model

- Runs as non-root user (UID 10002) inside the container
- Input binaries mounted read-only (`/workspace/binaries:ro`)
- Seccomp profile applied (`pwn-mcp/seccomp/dynamic-analysis.json`)
- `SYS_PTRACE` is the only added capability
- Script execution tools (`run_pwntools_script`, `run_z3_script`, `run_boofuzz_script`) run in isolated subprocesses with configurable timeouts

## Documentation

- [PWN-MCP Tool Reference](../docs/pwn-tool-reference.md) -- complete tool catalog
- [Cross-Server Workflows](../docs/cross-server-workflows.md) -- static-dynamic bridge patterns
- [TASKS.md](TASKS.md) -- implementation plan and progress
