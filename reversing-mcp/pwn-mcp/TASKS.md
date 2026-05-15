# pwn-mcp Implementation Plan

Dynamic analysis MCP server — task-by-task implementation plan.
Each task includes a validation gate before moving to the next.

---

## Phase 0 — Scaffolding

### Task 1: Scaffold project structure
Create the directory layout:
- `pwn-mcp/src/pwn_mcp/` — Python package (server.py, app.py, jobs.py, store.py, security.py, config.py, errors.py, utils.py, worker.py)
- `pwn-mcp/tests/` — test suite
- `pwn-mcp/test_binaries/` — small C programs for integration tests
- `pwn-mcp/Dockerfile.dynamic`
- `pwn-mcp/pyproject.toml`
- Update root `docker-compose.yml` to add the new service with shared workspace volume (ro for binaries, rw for dynamic-output)

---

## Phase 1 — Dockerfile (layered, independently testable)

### Task 2: Dockerfile — base + QEMU + binfmt_misc
- Base: ubuntu:24.04
- qemu-user-static, qemu-system-x86, qemu-system-arm, qemu-system-misc, binfmt-support
- Register all binfmt_misc handlers for transparent cross-arch execution
- Python 3.12, pip, venv
- Unprivileged user `analyzer` (uid 10002)

**Gate:** `docker build` succeeds; copy an ARM64 ELF into the container and execute it transparently.

### Task 3: Dockerfile — GDB multiarch + GEF + pwndbg
- gdb-multiarch (apt)
- GEF from GitHub release
- pwndbg cloned and installed
- Selectable via `GDBINIT_FRAMEWORK` env var (default: gef)

**Gate:** `gdb -ex "python import gef" --batch` exits 0; break at main of x86_64 binary, verify hit.

### Task 4: Dockerfile — tracing tools
- strace, ltrace (apt)
- valgrind (apt)
- uftrace (build from source)
- perf (linux-tools-generic)

**Gate:** each tool runs against test_hello, produces expected output, exits cleanly.

### Task 5: Dockerfile — Frida + DynamoRIO
- frida-tools via pip
- frida-gadget prebuilt .so for x86_64 and arm64
- DynamoRIO prebuilt release to /opt/dynamorio

**Gate:** `frida --version`; `drrun -version`; drcov run against test_hello produces .log.

### Task 6: Dockerfile — rr record/replay
- rr from prebuilt GitHub release (or build from source)
- x86/x86_64 only — document constraint
- Requires host `perf_event_paranoia <= 1`

**Gate:** record + replay test_hello, verify deterministic output.

### Task 7: Dockerfile — AFL++ with QEMU mode

Out of scope for this CTF harness. AFL++ is intentionally not installed or exposed through MCP. Keep Z3 symbolic solving, Frida, coverage, and boofuzz protocol scripts.

### Task 8: Dockerfile — exploit toolchain
- pwntools (pip)
- ropper (pip)
- one_gadget (gem, needs ruby)
- checksec.sh → /usr/local/bin/checksec
- seccomp-tools (gem)
- ROPgadget (pip, fallback)
- patchelf (apt)
- glibc-all-in-one cloned to /opt/glibc-all-in-one
- libc-database cloned to /opt/libc-database

**Gate:** all tools report version; pwntools imports cleanly in Python.

---

## Phase 2 — Test Binaries

### Task 9: Write test binaries
Small C programs in `pwn-mcp/test_binaries/`, compiled for x86_64 and aarch64:

| Binary | Purpose |
|---|---|
| test_hello | Baseline execution, prints "hello world" |
| test_crash_offset | Overflows buffer at known offset (64 bytes), controlled crash |
| test_heap | Predictable malloc/free sequence for heap analysis |
| test_format | Classic `printf(buf)` format string vuln |
| test_seccomp | Installs seccomp (allow read/write/exit, block execve) |
| test_network | Simple TCP echo server on random port |

Includes Makefile for x86_64 and aarch64 targets.

---

## Phase 3 — Core MCP Infrastructure

### Task 10: Core infrastructure — server, sessions, jobs, security
Fresh codebase (not a copy of static MCP, but same patterns):

- `config.py` — workspace root, env var overrides, tool paths, per-session tmpfs paths
- `errors.py` — StructuredToolError with category/code/message/details
- `security.py` — WorkspaceSecurity: resolve_binary_path (ro), resolve_output_path (rw), per-session sandbox dir, resource limits
- `store.py` — SessionStore: create/destroy sessions, track process/debug session handles
- `jobs.py` — async job system: create_job, poll_job, cancel_job
- `server.py` — MCP server entry point, @expose decorator, HTTP transport
- `app.py` — AppServer stub + get_capabilities()

**Gate:** unit tests for session lifecycle, job state machine, path traversal rejection all pass.

---

## Phase 4 — Tool Implementation (each with tests)

### Task 11: Process control tools
`launch_binary`, `send_input`, `read_output`, `get_process_state`, `terminate_process`

Key: architecture auto-detection, QEMU prepend for cross-arch, wall-clock timeout, output buffering.

**Gate:** launch x86_64 + ARM64; stdin/stdout round-trip; crash detection via SIGSEGV; timeout kills process.

### Task 12: GDB integration tools
Full GDB/MI client — the most complex component.

GdbMiClient class + all debug tools: breakpoints, watchpoints, execution control, registers, memory, backtrace, locals, heap info, libc info.

**Gate:** break at main; read RIP; set watchpoint; crash test_crash_offset + backtrace; heap analysis on test_heap.

### Task 13: rr record/replay tools
`start_rr_record`, `start_rr_replay`, `reverse_continue`, `reverse_step`, `reverse_next`, `reverse_finish`, `list_recordings`

Thin layer over GdbMiClient; handles rr's GDB stub port.

**Gate:** record crash, replay, reverse_continue back to the write that caused it.

### Task 14: Tracing tools
`run_with_strace`, `run_with_ltrace`, `run_with_valgrind`, `run_with_uftrace`, `get_trace_output`

All async job-based. Structured parsing where possible (strace syscalls, valgrind error summary).

**Gate:** each tool produces expected output on test binaries; valgrind detects leak in leaky binary, clean in clean binary.

### Task 15: Frida tools
`start_frida_session`, `inject_script`, `hook_function`, `trace_calls`, `get_exports`, `get_memory_ranges`, `dump_memory`

Use `frida` Python library directly (not CLI).

**Gate:** inject alive-check script; hook puts in test_hello; enumerate libc exports.

### Task 16: Coverage tools
`run_with_coverage`, `get_coverage_report`, `diff_coverage`

Backends: DynamoRIO drcov, QEMU TCG plugin, Frida Stalker.

**Gate:** two inputs produce different coverage; diff_coverage shows correct new/dropped blocks.

### Task 17: Fuzzing tools (AFL++)

Out of scope for this CTF harness. Do not register AFL++ MCP tools.

### Task 18: Exploit assistance tools
`checksec`, `generate_cyclic_pattern`, `find_cyclic_offset`, `find_one_gadgets`, `run_pwntools_script`, `get_rop_gadgets`

`run_pwntools_script` executes in subprocess with dropped privileges + timeout.

**Gate:** checksec correct for test_crash_offset; cyclic finds correct offset; one_gadget returns results; pwntools script connects to test_network.

### Task 19: Memory and heap analysis tools
`get_memory_maps`, `dump_memory_region`, `analyze_heap`, `find_format_string_vulns`

Built on GDB integration. Allocator detection: ptmalloc2, jemalloc, tcmalloc.

**Gate:** heap analysis matches test_heap source state; format string detection fires on test_format, no false positive on test_hello.

### Task 20: Seccomp analysis tool
`analyze_seccomp(session_id, path)`

Uses `seccomp-tools dump`. Parses BPF filter into structured allowed/blocked syscall list. Detects common sandbox archetypes.

**Gate:** test_seccomp: execve blocked, read/write allowed. Clean binary: no-filter result.

---

## Phase 5 — Test Suite and Integration

### Task 21: End-to-end integration test suite
Full pytest suite inside container:

```
tests/test_00_container_health.py
tests/test_01_process_control.py
tests/test_02_gdb.py
tests/test_03_rr.py              # skipped if perf_event_paranoia > 1
tests/test_04_tracing.py
tests/test_05_frida.py
tests/test_06_coverage.py
tests/test_07_protocol_fuzzing.py # optional boofuzz script probes
tests/test_08_exploit.py
tests/test_09_memory.py
tests/test_10_seccomp.py
```

Fast path: `pytest -m "not slow"` < 60 seconds
Full path: `pytest` < 5 minutes

---

## Dependency Order

```
Task 1 (scaffold)
  └── Task 2 (Dockerfile base)
        └── Task 3 (GDB)
              └── Task 4 (tracing)
                    └── Task 5 (Frida/DynamoRIO)
                          └── Task 6 (rr)
                                └── Task 7 (AFL++, out of scope)
                                      └── Task 8 (exploit toolchain)

Task 9 (test binaries) — can be done any time after Task 1

Task 10 (core infra) — can start after Task 1
  └── Task 11 (process control)
        └── Task 12 (GDB tools)       ← needs Task 3 + Task 6
              └── Task 13 (rr tools)  ← needs Task 6
              └── Task 14 (tracing)   ← needs Task 4
              └── Task 15 (Frida)     ← needs Task 5
              └── Task 16 (coverage)  ← needs Task 5 + Task 7
        └── Task 17 (AFL++, out of scope)
        └── Task 18 (exploit)         ← needs Task 8
        └── Task 19 (heap/memory)     ← needs Task 12
        └── Task 20 (seccomp)         ← needs Task 8

Task 21 (test suite) — after all tool tasks
```
