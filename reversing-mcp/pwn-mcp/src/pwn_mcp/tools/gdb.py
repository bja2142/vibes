"""
GDB integration tools using the GDB/MI protocol.

All tools operate on a DebugSession which wraps a gdb-multiarch subprocess
running in --interpreter=mi2 mode.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
import uuid
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..config import TOOL_GDB, QEMU_USER_MAP, DEFAULT_OUTPUT_MAX_BYTES
from ..errors import PwnMcpError
from ..store import DebugSession
from ..utils import detect_arch, which_tool

if TYPE_CHECKING:
    from ..app import PwnMcpApp


# ── GDB/MI client ─────────────────────────────────────────────────────────────

class GdbMiClient:
    """
    Thin GDB/MI client wrapping a gdb-multiarch subprocess.

    Sends MI commands (with auto-incrementing tokens) and collects responses.
    Non-blocking: commands return a future-like result queue.
    """

    _RESULT_RE = re.compile(r"^(\d+)\^(done|running|error|exit|connected)(,(.*))?$")
    _ASYNC_RE  = re.compile(r"^(\d+)?[*=~@&^](.*)")

    def __init__(self, proc: subprocess.Popen) -> None:
        self._proc = proc
        self._lock = threading.Lock()
        self._token = 1
        self._pending: dict[int, threading.Event] = {}
        self._results: dict[int, dict[str, Any]] = {}
        self._console_buf: list[str] = []
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _next_token(self) -> int:
        with self._lock:
            t = self._token
            self._token += 1
        return t

    def _read_loop(self) -> None:
        """Background thread: parse GDB/MI output and wake pending commands."""
        try:
            for raw_line in self._proc.stdout:  # type: ignore[union-attr]
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if not line or line == "(gdb)":
                    continue
                # Console stream output (~"...")
                if line.startswith('~"'):
                    self._console_buf.append(line[2:].rstrip('"').replace("\\n", "\n"))
                    continue
                # Result record: token^class[,results]
                m = self._RESULT_RE.match(line)
                if m:
                    token = int(m.group(1))
                    cls = m.group(2)
                    payload = m.group(4) or ""
                    result = {"class": cls, "payload": payload, "raw": line}
                    with self._lock:
                        self._results[token] = result
                        ev = self._pending.pop(token, None)
                    if ev:
                        ev.set()
        except Exception:
            pass

    def send(self, cmd: str, timeout: float = 30.0) -> dict[str, Any]:
        """Send a GDB/MI command and block until its result arrives."""
        token = self._next_token()
        full_cmd = f"{token}-{cmd}\n"
        ev = threading.Event()
        with self._lock:
            self._pending[token] = ev

        self._proc.stdin.write(full_cmd.encode())  # type: ignore[union-attr]
        self._proc.stdin.flush()                    # type: ignore[union-attr]

        if not ev.wait(timeout=timeout):
            with self._lock:
                self._pending.pop(token, None)
            raise PwnMcpError("timeout_or_resource_limit", "gdb_timeout", f"GDB command timed out: {cmd!r}")

        with self._lock:
            return self._results.pop(token)

    def exec_cli(self, cli_cmd: str, timeout: float = 30.0) -> str:
        """Execute a CLI command via -interpreter-exec console and return console output."""
        before = len(self._console_buf)
        self.send(f'interpreter-exec console "{cli_cmd}"', timeout=timeout)
        new_lines = self._console_buf[before:]
        return "".join(new_lines)

    def is_alive(self) -> bool:
        return self._proc.poll() is None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _require_gdb() -> None:
    if not which_tool(TOOL_GDB):
        raise PwnMcpError("tool_not_found", "gdb_missing", f"'{TOOL_GDB}' is not installed.")


def _get_debug_session(app: "PwnMcpApp", session_id: str, debug_id: str) -> DebugSession:
    session = app.sessions.get(session_id)
    ds = session.debug_sessions.get(debug_id)
    if ds is None:
        raise PwnMcpError("not_found", "debug_session_not_found", f"Debug session '{debug_id}' not found.")
    return ds


def _get_mi(app: "PwnMcpApp", session_id: str, debug_id: str) -> GdbMiClient:
    ds = _get_debug_session(app, session_id, debug_id)
    mi = getattr(ds, "_mi", None)
    if mi is None or not mi.is_alive():
        raise PwnMcpError("process_error", "gdb_not_running", f"GDB process for debug session '{debug_id}' is not running.")
    return mi


def _parse_int(value: int | str, *, field: str) -> int:
    try:
        if isinstance(value, int):
            return value
        return int(str(value).strip(), 0)
    except (TypeError, ValueError) as exc:
        raise PwnMcpError("invalid_request", "invalid_integer", f"{field} must be an integer or hex integer string.") from exc


def _safe_gdb_path(path: str) -> str:
    if '"' in path or "\n" in path or "\r" in path:
        raise PwnMcpError("invalid_request", "path_not_safe_for_gdb", "GDB dump output path may not contain quotes or newlines.")
    return path


def _detect_allocator(text: str) -> dict[str, str]:
    lowered = text.lower()
    if "jemalloc" in lowered:
        return {"name": "jemalloc", "confidence": "high", "evidence": "jemalloc mapping or symbol found"}
    if "tcmalloc" in lowered or "libtcmalloc" in lowered or "gperftools" in lowered:
        return {"name": "tcmalloc", "confidence": "high", "evidence": "tcmalloc mapping or symbol found"}
    if "ptmalloc" in lowered:
        return {"name": "ptmalloc2", "confidence": "high", "evidence": "ptmalloc symbol or output found"}
    if "libc.so" in lowered or "glibc" in lowered:
        return {"name": "ptmalloc2", "confidence": "medium", "evidence": "glibc libc mapping found"}
    return {"name": "unknown", "confidence": "low", "evidence": "no allocator-specific mapping or symbol found"}


# ── Tool handlers ─────────────────────────────────────────────────────────────

def start_debug_session(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    framework: str = "gef",
    extra_gdb_args: list[str] | None = None,
) -> dict[str, Any]:
    _require_gdb()
    session = app.sessions.get(session_id)
    binary = app.security.resolve_binary(binary_path)
    arch = detect_arch(binary)

    qemu = QEMU_USER_MAP.get(arch, "")
    gdb_args = [TOOL_GDB, "--quiet", "--interpreter=mi2"]
    if extra_gdb_args:
        gdb_args.extend(extra_gdb_args)
    gdb_args.append(str(binary))

    # Set GDBINIT_FRAMEWORK for the wrapper script
    env = {"GDBINIT_FRAMEWORK": framework}
    if framework in ("gef", "pwndbg"):
        gdb_args[0] = "gdb-fw"  # use the framework-selecting wrapper

    proc_env = {**__import__("os").environ, **env}
    try:
        proc = subprocess.Popen(
            gdb_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=proc_env,
        )
    except FileNotFoundError as exc:
        raise PwnMcpError("tool_not_found", "gdb_exec_failed", str(exc)) from exc

    debug_id = f"dbg_{uuid.uuid4().hex[:12]}"
    ds = DebugSession(
        debug_id=debug_id,
        session_id=session_id,
        binary_path=str(binary),
        framework=framework,
        gdb_proc=proc,
    )
    mi = GdbMiClient(proc)
    ds._mi = mi  # type: ignore[attr-defined]

    # Wait for GDB to initialise (up to 10 s)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and mi.is_alive():
        if "(gdb)" in "".join(mi._console_buf[-5:]):
            break
        time.sleep(0.1)

    # Set QEMU if needed
    if qemu:
        mi.send(f'set exec-wrapper {qemu}')

    with session._lock:
        session.debug_sessions[debug_id] = ds

    return {
        "ok": True,
        "result": {
            "debug_id": debug_id,
            "binary_path": str(binary),
            "arch": arch,
            "framework": framework,
            "qemu_wrapper": qemu or None,
        },
    }


def send_gdb_command(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
    command: str,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    # Distinguish MI commands from CLI commands
    if command.startswith("-"):
        # Already an MI command
        result = mi.send(command.lstrip("-"), timeout=float(timeout_seconds))
    else:
        # CLI command — wrap via interpreter-exec
        output = mi.exec_cli(command, timeout=float(timeout_seconds))
        return {"ok": True, "result": {"output": output}}
    return {"ok": True, "result": result}


def set_breakpoint(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
    location: str,
    condition: str | None = None,
    temporary: bool = False,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    cmd = "break-insert"
    if temporary:
        cmd += " -t"
    if condition:
        cmd += f' -c "{condition}"'
    cmd += f' "{location}"'
    result = mi.send(cmd)
    return {"ok": True, "result": result}


def delete_breakpoint(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
    breakpoint_number: int,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    result = mi.send(f"break-delete {breakpoint_number}")
    return {"ok": True, "result": result}


def list_breakpoints(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    result = mi.send("break-list")
    return {"ok": True, "result": result}


def continue_execution(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    result = mi.send("exec-continue", timeout=float(timeout_seconds))
    return {"ok": True, "result": result}


def step_instruction(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    result = mi.send("exec-step-instruction")
    return {"ok": True, "result": result}


def step_over_instruction(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    result = mi.send("exec-next-instruction")
    return {"ok": True, "result": result}


def step_into(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    result = mi.send("exec-step")
    return {"ok": True, "result": result}


def step_over(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    result = mi.send("exec-next")
    return {"ok": True, "result": result}


def finish_function(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    result = mi.send("exec-finish")
    return {"ok": True, "result": result}


def run_until(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
    location: str,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    stripped = location.strip()
    try:
        int(stripped.lstrip("*"), 0)
        target = stripped if stripped.startswith("*") else f"*{stripped}"
    except ValueError:
        target = stripped
    result = mi.send(f"exec-until {target}")
    return {"ok": True, "result": result}


def read_registers(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
    register_names: list[str] | None = None,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    if register_names:
        names_result = mi.send("data-list-register-names")
        names = re.findall(r'"([^"]*)"', names_result.get("payload", ""))
        index_by_name = {name.lower(): index for index, name in enumerate(names) if name}
        missing = [name for name in register_names if name.lower() not in index_by_name]
        if missing:
            raise PwnMcpError(
                "invalid_request",
                "unknown_register",
                f"Unknown register name(s): {', '.join(missing)}.",
                details={"available_registers": [name for name in names if name]},
            )
        indices = " ".join(str(index_by_name[name.lower()]) for name in register_names)
        result = mi.send(f"data-list-register-values x {indices}")
        result["requested_registers"] = register_names
    else:
        result = mi.send("data-list-register-values x")
    return {"ok": True, "result": result}


def write_register(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
    register: str,
    value: str,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    normalized = register[1:] if register.startswith("$") else register
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized):
        raise PwnMcpError("invalid_request", "invalid_register_name", f"Invalid register name: {register!r}.")
    result = mi.send(f"gdb-set ${normalized} = {value}")
    return {"ok": True, "result": result}


def read_memory(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
    address: str,
    length: int,
    word_size: int = 1,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    result = mi.send(f"data-read-memory-bytes {address} {length}")
    return {"ok": True, "result": result}


def write_memory(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
    address: str,
    data_hex: str,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    result = mi.send(f"data-write-memory-bytes {address} {data_hex}")
    return {"ok": True, "result": result}


def search_memory(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
    start_address: str,
    end_address: str,
    pattern_hex: str,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    cleaned = re.sub(r"[^0-9a-fA-F]", "", pattern_hex or "")
    if not cleaned or len(cleaned) % 2:
        raise PwnMcpError("invalid_request", "invalid_hex_pattern", "pattern_hex must contain whole bytes.")
    byte_args = ", ".join(f"0x{cleaned[index:index + 2]}" for index in range(0, len(cleaned), 2))
    output = mi.exec_cli(f"find /b {start_address}, {end_address}, {byte_args}")
    matches = re.findall(r"0x[0-9a-fA-F]+", output)
    return {
        "ok": True,
        "result": {
            "start_address": start_address,
            "end_address": end_address,
            "pattern_hex": cleaned.lower(),
            "matches": matches,
            "output": output,
        },
    }


def dump_memory_region(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
    address: int | str,
    length: int,
    output_filename: str | None = None,
) -> dict[str, Any]:
    """Dump a bounded memory region from the inferior to the session output directory."""
    if length <= 0:
        raise PwnMcpError("invalid_request", "length_invalid", "length must be greater than zero.")
    if length > DEFAULT_OUTPUT_MAX_BYTES:
        raise PwnMcpError(
            "timeout_or_resource_limit",
            "memory_dump_too_large",
            f"Memory dumps are capped at {DEFAULT_OUTPUT_MAX_BYTES} bytes.",
            details={"max_bytes": DEFAULT_OUTPUT_MAX_BYTES, "requested_bytes": length},
        )

    mi = _get_mi(app, session_id, debug_id)
    start = _parse_int(address, field="address")
    end = start + int(length)
    filename = output_filename or f"memory_{start:#x}_{length}.bin".replace("0x", "")
    output_path = app.security.resolve_output_file(session_id, filename)
    safe_path = _safe_gdb_path(str(output_path))

    output = mi.exec_cli(f"dump memory {safe_path} {start:#x} {end:#x}")
    if not output_path.exists():
        raise PwnMcpError(
            "backend_failure",
            "memory_dump_failed",
            "GDB did not create the requested memory dump.",
            details={"gdb_output": output[-2000:] if output else ""},
        )
    return {
        "ok": True,
        "result": {
            "path": str(output_path),
            "address": f"{start:#x}",
            "end_address": f"{end:#x}",
            "bytes_written": output_path.stat().st_size,
            "gdb_output": output,
        },
    }


def get_backtrace(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
    max_frames: int = 32,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    result = mi.send(f"stack-list-frames 0 {max_frames}")
    return {"ok": True, "result": result}


def get_locals(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
    frame: int = 0,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    result = mi.send(f"stack-list-locals --thread 1 --frame {frame} --all-values")
    return {"ok": True, "result": result}


def evaluate_expression(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
    expression: str,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    result = mi.send(f'data-evaluate-expression "{expression}"')
    return {"ok": True, "result": result}


def get_memory_maps(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    output = mi.exec_cli("info proc mappings")
    return {"ok": True, "result": {"maps": output}}


def get_heap_info(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
) -> dict[str, Any]:
    """Use GEF or pwndbg heap command to get heap state."""
    mi = _get_mi(app, session_id, debug_id)
    ds = _get_debug_session(app, session_id, debug_id)
    if ds.framework == "pwndbg":
        output = mi.exec_cli("heap")
    else:
        output = mi.exec_cli("heap chunks")
    return {"ok": True, "result": {"heap_info": output}}


def get_libc_info(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
) -> dict[str, Any]:
    """Find and report loaded libc version."""
    mi = _get_mi(app, session_id, debug_id)
    output = mi.exec_cli("info sharedlibrary")
    return {"ok": True, "result": {"shared_libraries": output}}


def analyze_heap(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
) -> dict[str, Any]:
    """Run the best available heap-inspection commands for the active GDB framework."""
    mi = _get_mi(app, session_id, debug_id)
    ds = _get_debug_session(app, session_id, debug_id)
    if ds.framework == "pwndbg":
        commands = ["heap", "bins", "vis_heap_chunks"]
    elif ds.framework == "gef":
        commands = ["heap chunks", "heap bins", "heap arenas"]
    else:
        commands = ["info proc mappings", "info sharedlibrary"]

    for command in ("info proc mappings", "info sharedlibrary"):
        if command not in commands:
            commands.append(command)

    outputs: dict[str, str] = {}
    for command in commands:
        try:
            outputs[command] = mi.exec_cli(command)
        except Exception as exc:
            outputs[command] = f"ERROR: {exc}"
    combined = "\n".join(outputs.values()).lower()
    return {
        "ok": True,
        "result": {
            "framework": ds.framework,
            "commands": outputs,
            "allocator": _detect_allocator(combined),
            "summary": {
                "mentions_heap": "heap" in combined,
                "mentions_tcache": "tcache" in combined,
                "mentions_fastbin": "fastbin" in combined or "fast bin" in combined,
                "mentions_chunk": "chunk" in combined,
            },
        },
    }


def find_format_string_vulns(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    max_findings: int = 50,
) -> dict[str, Any]:
    """Heuristically identify format-string attack surface in a binary."""
    app.sessions.get(session_id)
    binary = app.security.resolve_binary(binary_path)
    max_findings = max(1, min(int(max_findings or 50), 500))
    dangerous_sinks = {
        "printf",
        "fprintf",
        "sprintf",
        "snprintf",
        "vprintf",
        "vfprintf",
        "vsprintf",
        "vsnprintf",
        "syslog",
        "err",
        "warn",
    }
    findings: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {"objdump_available": False, "strings_available": False}

    if which_tool("objdump"):
        evidence["objdump_available"] = True
        completed = subprocess.run(
            ["objdump", "-d", str(binary)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        call_re = re.compile(
            r"^\s*([0-9a-fA-F]+):.*\b(call\w*|bl|blr|jal|jalr|bal|brasl)\b.*<([^>@]+)(?:@[^>]*)?>",
            re.IGNORECASE,
        )
        for line in completed.stdout.splitlines():
            match = call_re.search(line)
            if not match:
                continue
            sink = match.group(3)
            if sink in dangerous_sinks:
                findings.append({
                    "kind": "format_sink_call",
                    "sink": sink,
                    "address": f"0x{int(match.group(1), 16):x}",
                    "confidence": "medium",
                    "evidence": line.strip(),
                    "reason": "Binary calls a printf-family sink. Verify whether the format argument is attacker-controlled.",
                })
                if len(findings) >= max_findings:
                    break
        evidence["objdump_returncode"] = completed.returncode
        if completed.stderr:
            evidence["objdump_stderr_tail"] = completed.stderr[-1000:]

    if len(findings) < max_findings and which_tool("strings"):
        evidence["strings_available"] = True
        completed = subprocess.run(
            ["strings", "-a", str(binary)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        fmt_re = re.compile(r"%(?:\d+\$)?[#0 +'-]?(?:\*|\d+)?(?:\.(?:\*|\d+))?[hljztL]*[diuoxXfFeEgGaAcspn]")
        for line in completed.stdout.splitlines():
            matches = fmt_re.findall(line)
            if not matches:
                continue
            risky = any(item.endswith("n") or item.endswith("p") or item.endswith("x") or item.endswith("s") for item in matches)
            findings.append({
                "kind": "format_string_literal",
                "string": line[:500],
                "directives": matches[:20],
                "confidence": "low" if risky else "informational",
                "reason": "Binary contains format directives; this is context for sink review, not proof of a vulnerability.",
            })
            if len(findings) >= max_findings:
                break
        evidence["strings_returncode"] = completed.returncode
        if completed.stderr:
            evidence["strings_stderr_tail"] = completed.stderr[-1000:]

    return {
        "ok": True,
        "result": {
            "binary_path": str(binary),
            "finding_count": len(findings),
            "findings": findings,
            "evidence": evidence,
            "limitations": [
                "This is a heuristic sink and literal scanner.",
                "Confirm attacker control of the format argument with GDB, tracing, or source review before treating a finding as exploitable.",
            ],
        },
    }


def stop_debug_session(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
) -> dict[str, Any]:
    session = app.sessions.get(session_id)
    ds = session.debug_sessions.pop(debug_id, None)
    if ds is None:
        raise PwnMcpError("not_found", "debug_session_not_found", f"Debug session '{debug_id}' not found.")
    mi = getattr(ds, "_mi", None)
    if mi:
        try:
            mi.send("gdb-exit", timeout=3)
        except Exception:
            pass
    if ds.gdb_proc:
        try:
            ds.gdb_proc.kill()
            ds.gdb_proc.wait(timeout=2)
        except Exception:
            pass
    return {"ok": True, "result": {"debug_id": debug_id, "stopped": True}}


# ── Tool definitions (MCP schemas) ────────────────────────────────────────────

def register(app: "PwnMcpApp") -> dict[str, dict]:
    def _h(fn):
        def wrapper(**kwargs):
            return fn(app, **kwargs)
        return wrapper

    _sid = {"type": "string", "description": "Session ID from create_execution_session."}
    _did = {"type": "string", "description": "Debug session ID from start_debug_session."}

    return {
        "start_debug_session": {
            "handler": _h(start_debug_session),
            "schema": Tool(
                name="start_debug_session",
                description=(
                    "Start a GDB/MI debug session for a binary. Returns a debug_id used by all "
                    "other GDB tools. Automatically sets QEMU exec-wrapper for non-native architectures."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": {"type": "string"},
                        "framework": {
                            "type": "string",
                            "enum": ["gef", "pwndbg", "vanilla"],
                            "description": "GDB plugin framework. Default: gef.",
                            "default": "gef",
                        },
                        "extra_gdb_args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Additional arguments passed to gdb.",
                            "default": [],
                        },
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
        "stop_debug_session": {
            "handler": _h(stop_debug_session),
            "schema": Tool(
                name="stop_debug_session",
                description="Terminate a GDB debug session and free its resources.",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid, "debug_id": _did},
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "send_gdb_command": {
            "handler": _h(send_gdb_command),
            "schema": Tool(
                name="send_gdb_command",
                description=(
                    "Send an arbitrary GDB command. Prefix with '-' for raw MI commands "
                    "(e.g. '-exec-continue'); otherwise treated as a CLI command."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "debug_id": _did,
                        "command": {"type": "string", "description": "GDB command or MI command."},
                        "timeout_seconds": {"type": "integer", "default": 30, "description": "Max seconds to wait for the command to complete. Default: 30."},
                    },
                    "required": ["session_id", "debug_id", "command"],
                    "additionalProperties": False,
                },
            ),
        },
        "set_breakpoint": {
            "handler": _h(set_breakpoint),
            "schema": Tool(
                name="set_breakpoint",
                description="Set a breakpoint at a function name, source location, or address.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "debug_id": _did,
                        "location": {"type": "string", "description": "Function name, file:line, or *0xADDR."},
                        "condition": {"type": "string", "description": "Conditional expression (C syntax)."},
                        "temporary": {"type": "boolean", "description": "Delete after first hit. Default: false.", "default": False},
                    },
                    "required": ["session_id", "debug_id", "location"],
                    "additionalProperties": False,
                },
            ),
        },
        "delete_breakpoint": {
            "handler": _h(delete_breakpoint),
            "schema": Tool(
                name="delete_breakpoint",
                description="Delete a breakpoint by its number.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid, "debug_id": _did,
                        "breakpoint_number": {"type": "integer"},
                    },
                    "required": ["session_id", "debug_id", "breakpoint_number"],
                    "additionalProperties": False,
                },
            ),
        },
        "list_breakpoints": {
            "handler": _h(list_breakpoints),
            "schema": Tool(
                name="list_breakpoints",
                description="List all breakpoints in the current debug session.",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid, "debug_id": _did},
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "continue_execution": {
            "handler": _h(continue_execution),
            "schema": Tool(
                name="continue_execution",
                description="Resume execution until the next breakpoint or program exit.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid, "debug_id": _did,
                        "timeout_seconds": {"type": "integer", "default": 30, "description": "Max seconds to wait before assuming the program hung. Default: 30."},
                    },
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "step_instruction": {
            "handler": _h(step_instruction),
            "schema": Tool(
                name="step_instruction",
                description="Execute a single machine instruction, stepping into calls.",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid, "debug_id": _did},
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "step_over_instruction": {
            "handler": _h(step_over_instruction),
            "schema": Tool(
                name="step_over_instruction",
                description="Execute a single machine instruction, stepping over calls.",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid, "debug_id": _did},
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "step_into": {
            "handler": _h(step_into),
            "schema": Tool(
                name="step_into",
                description="Step into the next source line (step into function calls).",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid, "debug_id": _did},
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "step_over": {
            "handler": _h(step_over),
            "schema": Tool(
                name="step_over",
                description="Step over the next source line (step over function calls).",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid, "debug_id": _did},
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "finish_function": {
            "handler": _h(finish_function),
            "schema": Tool(
                name="finish_function",
                description="Run until the current function returns.",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid, "debug_id": _did},
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "run_until": {
            "handler": _h(run_until),
            "schema": Tool(
                name="run_until",
                description="Run until the given location is reached.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid, "debug_id": _did,
                        "location": {"type": "string"},
                    },
                    "required": ["session_id", "debug_id", "location"],
                    "additionalProperties": False,
                },
            ),
        },
        "read_registers": {
            "handler": _h(read_registers),
            "schema": Tool(
                name="read_registers",
                description="Read CPU register values. Pass register_names to read specific registers.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid, "debug_id": _did,
                        "register_names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Register names to read. Omit for all registers.",
                        },
                    },
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "write_register": {
            "handler": _h(write_register),
            "schema": Tool(
                name="write_register",
                description="Set a CPU register to a value.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid, "debug_id": _did,
                        "register": {"type": "string"},
                        "value": {"type": "string", "description": "Numeric value (hex 0x... or decimal)."},
                    },
                    "required": ["session_id", "debug_id", "register", "value"],
                    "additionalProperties": False,
                },
            ),
        },
        "read_memory": {
            "handler": _h(read_memory),
            "schema": Tool(
                name="read_memory",
                description="Read bytes from the inferior's address space.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid, "debug_id": _did,
                        "address": {"type": "string", "description": "Hex address (0x...)."},
                        "length": {"type": "integer", "description": "Number of bytes to read."},
                    },
                    "required": ["session_id", "debug_id", "address", "length"],
                    "additionalProperties": False,
                },
            ),
        },
        "write_memory": {
            "handler": _h(write_memory),
            "schema": Tool(
                name="write_memory",
                description="Write bytes to the inferior's address space.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid, "debug_id": _did,
                        "address": {"type": "string"},
                        "data_hex": {"type": "string", "description": "Hex string of bytes to write (no 0x prefix)."},
                    },
                    "required": ["session_id", "debug_id", "address", "data_hex"],
                    "additionalProperties": False,
                },
            ),
        },
        "search_memory": {
            "handler": _h(search_memory),
            "schema": Tool(
                name="search_memory",
                description="Search the inferior's memory for a byte pattern.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid, "debug_id": _did,
                        "start_address": {"type": "string"},
                        "end_address": {"type": "string"},
                        "pattern_hex": {"type": "string", "description": "Hex bytes to search for."},
                    },
                    "required": ["session_id", "debug_id", "start_address", "end_address", "pattern_hex"],
                    "additionalProperties": False,
                },
            ),
        },
        "dump_memory_region": {
            "handler": _h(dump_memory_region),
            "schema": Tool(
                name="dump_memory_region",
                description="Dump a bounded region of inferior memory to the session output directory.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "debug_id": _did,
                        "address": {"type": ["string", "integer"], "description": "Start address as 0x... or integer."},
                        "length": {"type": "integer", "description": "Number of bytes to dump. Capped by PWN_MCP_OUTPUT_MAX."},
                        "output_filename": {"type": "string", "description": "Optional output filename under the session output directory."},
                    },
                    "required": ["session_id", "debug_id", "address", "length"],
                    "additionalProperties": False,
                },
            ),
        },
        "get_backtrace": {
            "handler": _h(get_backtrace),
            "schema": Tool(
                name="get_backtrace",
                description="Get the call stack at the current position.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid, "debug_id": _did,
                        "max_frames": {"type": "integer", "default": 32},
                    },
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "get_locals": {
            "handler": _h(get_locals),
            "schema": Tool(
                name="get_locals",
                description="List local variables and their values in the specified frame.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid, "debug_id": _did,
                        "frame": {"type": "integer", "default": 0},
                    },
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "evaluate_expression": {
            "handler": _h(evaluate_expression),
            "schema": Tool(
                name="evaluate_expression",
                description="Evaluate a GDB expression in the current context.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid, "debug_id": _did,
                        "expression": {"type": "string"},
                    },
                    "required": ["session_id", "debug_id", "expression"],
                    "additionalProperties": False,
                },
            ),
        },
        "get_memory_maps": {
            "handler": _h(get_memory_maps),
            "schema": Tool(
                name="get_memory_maps",
                description="Show memory maps (segments, permissions, file mappings) of the running process.",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid, "debug_id": _did},
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "get_heap_info": {
            "handler": _h(get_heap_info),
            "schema": Tool(
                name="get_heap_info",
                description=(
                    "Inspect heap structure using GEF or pwndbg heap commands. "
                    "Shows bins, chunks, and tcache state."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid, "debug_id": _did},
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "analyze_heap": {
            "handler": _h(analyze_heap),
            "schema": Tool(
                name="analyze_heap",
                description="Run framework-aware heap analysis commands and return raw output plus a compact summary.",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid, "debug_id": _did},
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "find_format_string_vulns": {
            "handler": _h(find_format_string_vulns),
            "schema": Tool(
                name="find_format_string_vulns",
                description="Heuristically scan a binary for printf-family format-string attack surface.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": {"type": "string", "description": "Binary path relative to the pwn workspace or absolute within it."},
                        "max_findings": {"type": "integer", "default": 50, "description": "Maximum findings to return, capped at 500."},
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
        "get_libc_info": {
            "handler": _h(get_libc_info),
            "schema": Tool(
                name="get_libc_info",
                description="Show shared libraries loaded by the process, including libc path and base address.",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid, "debug_id": _did},
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
    }
