"""
Process control tools: launch, interact with, and terminate binary processes.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import uuid
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..config import (
    QEMU_USER_MAP,
    DEFAULT_EXEC_TIMEOUT_SECONDS,
    DEFAULT_OUTPUT_MAX_BYTES,
)
from ..errors import PwnMcpError, process_not_found
from ..store import ProcessHandle
from ..utils import detect_arch, truncate_output

if TYPE_CHECKING:
    from ..app import PwnMcpApp


# ── Internal helpers ──────────────────────────────────────────────────────────

def _stream_reader(buf: bytearray, stream, lock: threading.Lock, max_bytes: int) -> None:
    """Background thread: drain a pipe into a capped ring buffer."""
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            with lock:
                buf.extend(chunk)
                # Keep only the last max_bytes
                if len(buf) > max_bytes:
                    del buf[: len(buf) - max_bytes]
    except Exception:
        pass


def _build_cmd(binary_path: str, arch: str, args: list[str]) -> list[str]:
    qemu = QEMU_USER_MAP.get(arch, "")
    return ([qemu] if qemu else []) + [binary_path] + args


def _get_process(app: "PwnMcpApp", session_id: str, process_id: str) -> ProcessHandle:
    session = app.sessions.get(session_id)
    handle = session.processes.get(process_id)
    if handle is None:
        raise process_not_found(process_id)
    return handle


# ── Tool handlers ─────────────────────────────────────────────────────────────

def create_execution_session(app: "PwnMcpApp", arch: str | None = None) -> dict[str, Any]:
    session = app.sessions.create(arch=arch)
    return {
        "ok": True,
        "result": {
            "session_id": session.session_id,
            "arch": session.arch,
            "created_at": session.created_at,
        },
    }


def list_execution_sessions(app: "PwnMcpApp") -> dict[str, Any]:
    return {"ok": True, "result": {"sessions": app.sessions.list_all()}}


def destroy_execution_session(app: "PwnMcpApp", session_id: str) -> dict[str, Any]:
    app.sessions.destroy(session_id)
    return {"ok": True, "result": {"session_id": session_id, "destroyed": True}}


def launch_binary(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    session = app.sessions.get(session_id)
    binary = app.security.resolve_binary(binary_path)
    arch = detect_arch(binary)

    cmd = _build_cmd(str(binary), arch, args or [])
    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)

    work_dir = app.security.session_dir(session_id)

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=proc_env,
            cwd=str(work_dir),
        )
    except FileNotFoundError as exc:
        raise PwnMcpError(
            "tool_not_found", "qemu_missing",
            f"Cannot launch binary: {exc}. "
            f"Arch '{arch}' requires QEMU binary '{QEMU_USER_MAP.get(arch, '')}'.",
        ) from exc

    process_id = f"proc_{uuid.uuid4().hex[:12]}"
    handle = ProcessHandle(
        process_id=process_id,
        pid=proc.pid,
        arch=arch,
        binary_path=str(binary),
        args=args or [],
        proc=proc,
        start_time=time.time(),
    )

    # Start background output readers
    t_out = threading.Thread(
        target=_stream_reader,
        args=(handle.stdout_buf, proc.stdout, handle._lock, DEFAULT_OUTPUT_MAX_BYTES),
        daemon=True,
    )
    t_err = threading.Thread(
        target=_stream_reader,
        args=(handle.stderr_buf, proc.stderr, handle._lock, DEFAULT_OUTPUT_MAX_BYTES),
        daemon=True,
    )
    t_out.start()
    t_err.start()

    with session._lock:
        session.processes[process_id] = handle

    return {
        "ok": True,
        "result": {
            "process_id": process_id,
            "pid": proc.pid,
            "arch": arch,
            "cmd": cmd,
        },
    }


def send_input(
    app: "PwnMcpApp",
    session_id: str,
    process_id: str,
    data: str,
    newline: bool = True,
) -> dict[str, Any]:
    handle = _get_process(app, session_id, process_id)
    if handle.proc.stdin is None or handle.proc.stdin.closed:
        raise PwnMcpError("process_error", "stdin_closed", "Process stdin is not available.")

    rc = handle.proc.poll()
    if rc is not None:
        raise PwnMcpError(
            "process_error", "process_exited",
            f"Process {process_id} has already exited with code {rc}.",
        )

    payload = data.encode("utf-8")
    if newline and not payload.endswith(b"\n"):
        payload += b"\n"

    try:
        handle.proc.stdin.write(payload)
        handle.proc.stdin.flush()
    except BrokenPipeError as exc:
        raise PwnMcpError("process_error", "broken_pipe", "Write failed: process stdin closed.") from exc

    return {"ok": True, "result": {"bytes_sent": len(payload)}}


def read_output(
    app: "PwnMcpApp",
    session_id: str,
    process_id: str,
    stream: str = "stdout",
    max_bytes: int = 8192,
    wait_ms: int = 0,
    clear: bool = False,
) -> dict[str, Any]:
    """
    Read buffered output from a running process.

    stream: "stdout" | "stderr" | "both"
    wait_ms: if buffer is empty, wait up to this many ms for new data.
    clear: if True, flush the buffer after reading.
    """
    handle = _get_process(app, session_id, process_id)

    if stream not in ("stdout", "stderr", "both"):
        raise PwnMcpError("invalid_request", "invalid_stream", "stream must be stdout, stderr, or both.")

    # Optionally wait a short time for data to appear
    if wait_ms > 0:
        deadline = time.monotonic() + wait_ms / 1000.0
        while time.monotonic() < deadline:
            with handle._lock:
                has_data = bool(
                    (stream in ("stdout", "both") and handle.stdout_buf) or
                    (stream in ("stderr", "both") and handle.stderr_buf)
                )
            if has_data:
                break
            time.sleep(0.02)

    with handle._lock:
        if stream == "stdout":
            raw = bytes(handle.stdout_buf[-max_bytes:])
            if clear:
                handle.stdout_buf.clear()
        elif stream == "stderr":
            raw = bytes(handle.stderr_buf[-max_bytes:])
            if clear:
                handle.stderr_buf.clear()
        else:  # both
            combined = bytes(handle.stdout_buf) + bytes(handle.stderr_buf)
            raw = combined[-max_bytes:]
            if clear:
                handle.stdout_buf.clear()
                handle.stderr_buf.clear()

    text = raw.decode("utf-8", errors="replace")
    return {
        "ok": True,
        "result": {
            "text": text,
            "bytes": len(raw),
            "stream": stream,
        },
    }


def get_process_state(
    app: "PwnMcpApp",
    session_id: str,
    process_id: str,
) -> dict[str, Any]:
    handle = _get_process(app, session_id, process_id)
    rc = handle.proc.poll()
    state = "running" if rc is None else "exited"
    runtime = time.time() - handle.start_time

    with handle._lock:
        stdout_bytes = len(handle.stdout_buf)
        stderr_bytes = len(handle.stderr_buf)

    return {
        "ok": True,
        "result": {
            "process_id": process_id,
            "pid": handle.pid,
            "arch": handle.arch,
            "binary_path": handle.binary_path,
            "state": state,
            "exit_code": rc,
            "runtime_seconds": round(runtime, 3),
            "buffered_stdout_bytes": stdout_bytes,
            "buffered_stderr_bytes": stderr_bytes,
        },
    }


def terminate_process(
    app: "PwnMcpApp",
    session_id: str,
    process_id: str,
    sig: str = "SIGTERM",
) -> dict[str, Any]:
    handle = _get_process(app, session_id, process_id)
    rc = handle.proc.poll()
    if rc is not None:
        return {"ok": True, "result": {"process_id": process_id, "already_exited": True, "exit_code": rc}}

    sig_map = {
        "SIGTERM": signal.SIGTERM,
        "SIGKILL": signal.SIGKILL,
        "SIGINT": signal.SIGINT,
        "SIGHUP": signal.SIGHUP,
    }
    sig_num = sig_map.get(sig.upper())
    if sig_num is None:
        raise PwnMcpError("invalid_request", "invalid_signal", f"Unknown signal '{sig}'. Use SIGTERM, SIGKILL, SIGINT, or SIGHUP.")

    try:
        handle.proc.send_signal(sig_num)
    except ProcessLookupError:
        pass  # already dead

    # Wait briefly for clean exit
    try:
        handle.proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if sig_num != signal.SIGKILL:
            handle.proc.kill()
            handle.proc.wait(timeout=2)

    rc = handle.proc.poll()
    return {"ok": True, "result": {"process_id": process_id, "signal": sig, "exit_code": rc}}


# ── Tool definitions (MCP schemas) ────────────────────────────────────────────

def register(app: "PwnMcpApp") -> dict[str, dict]:
    def _h(fn):
        """Wrap handler to inject app as first arg."""
        def wrapper(**kwargs):
            return fn(app, **kwargs)
        return wrapper

    return {
        "create_execution_session": {
            "handler": _h(create_execution_session),
            "schema": Tool(
                name="create_execution_session",
                description=(
                    "Create an isolated execution session. All binaries launched within a session "
                    "share a working directory and are cleaned up together on destroy."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "arch": {
                            "type": "string",
                            "description": "Expected architecture hint (e.g. 'x86_64', 'aarch64'). Optional; auto-detected on launch.",
                        },
                    },
                    "additionalProperties": False,
                },
            ),
        },
        "list_execution_sessions": {
            "handler": _h(list_execution_sessions),
            "schema": Tool(
                name="list_execution_sessions",
                description="List all active execution sessions.",
                inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
            ),
        },
        "destroy_execution_session": {
            "handler": _h(destroy_execution_session),
            "schema": Tool(
                name="destroy_execution_session",
                description=(
                    "Destroy an execution session: kill all running processes and clean up "
                    "the session working directory."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "Session ID returned by create_execution_session."},
                    },
                    "required": ["session_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "launch_binary": {
            "handler": _h(launch_binary),
            "schema": Tool(
                name="launch_binary",
                description=(
                    "Launch a binary inside a session. Architecture is auto-detected from the ELF header; "
                    "non-native architectures are transparently executed via QEMU user-mode. "
                    "Stdin/stdout/stderr are piped — use send_input/read_output to interact."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "binary_path": {
                            "type": "string",
                            "description": "Path to the binary, relative to workspace root or absolute within it.",
                        },
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Command-line arguments.",
                            "default": [],
                        },
                        "env": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                            "description": "Extra environment variables to merge into the process environment.",
                            "default": {},
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "description": "Maximum wall-clock seconds before the process is killed. Default: no limit.",
                        },
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
        "send_input": {
            "handler": _h(send_input),
            "schema": Tool(
                name="send_input",
                description="Send text (UTF-8) to a running process's stdin.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "process_id": {"type": "string"},
                        "data": {"type": "string", "description": "Data to write to stdin."},
                        "newline": {
                            "type": "boolean",
                            "description": "Append a newline if not already present. Default true.",
                            "default": True,
                        },
                    },
                    "required": ["session_id", "process_id", "data"],
                    "additionalProperties": False,
                },
            ),
        },
        "read_output": {
            "handler": _h(read_output),
            "schema": Tool(
                name="read_output",
                description=(
                    "Read buffered output from a running or exited process. "
                    "Output is captured by background reader threads and stored in a ring buffer."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "process_id": {"type": "string"},
                        "stream": {
                            "type": "string",
                            "enum": ["stdout", "stderr", "both"],
                            "description": "Which stream to read. Default: stdout.",
                            "default": "stdout",
                        },
                        "max_bytes": {
                            "type": "integer",
                            "description": "Maximum bytes to return. Default: 8192.",
                            "default": 8192,
                        },
                        "wait_ms": {
                            "type": "integer",
                            "description": "If buffer is empty, wait up to this many milliseconds. Default: 0.",
                            "default": 0,
                        },
                        "clear": {
                            "type": "boolean",
                            "description": "Clear the buffer after reading. Default: false.",
                            "default": False,
                        },
                    },
                    "required": ["session_id", "process_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "get_process_state": {
            "handler": _h(get_process_state),
            "schema": Tool(
                name="get_process_state",
                description="Get current state of a launched process (running, exited, exit code, output buffer sizes).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "process_id": {"type": "string"},
                    },
                    "required": ["session_id", "process_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "terminate_process": {
            "handler": _h(terminate_process),
            "schema": Tool(
                name="terminate_process",
                description="Send a signal to a running process.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "process_id": {"type": "string"},
                        "sig": {
                            "type": "string",
                            "enum": ["SIGTERM", "SIGKILL", "SIGINT", "SIGHUP"],
                            "description": "Signal to send. Default: SIGTERM.",
                            "default": "SIGTERM",
                        },
                    },
                    "required": ["session_id", "process_id"],
                    "additionalProperties": False,
                },
            ),
        },
    }
