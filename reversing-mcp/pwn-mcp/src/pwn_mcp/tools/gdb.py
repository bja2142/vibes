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

from ..config import TOOL_GDB, QEMU_USER_MAP
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
    result = mi.send(f'exec-until "{location}"')
    return {"ok": True, "result": result}


def read_registers(
    app: "PwnMcpApp",
    session_id: str,
    debug_id: str,
    register_names: list[str] | None = None,
) -> dict[str, Any]:
    mi = _get_mi(app, session_id, debug_id)
    if register_names:
        names_str = " ".join(f'"{r}"' for r in register_names)
        result = mi.send(f"data-list-register-values x {names_str}")
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
    result = mi.send(f'gdb-set $"{register}" = {value}')
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
    result = mi.send(f"data-find {start_address} {end_address} {pattern_hex}")
    return {"ok": True, "result": result}


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
