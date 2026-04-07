"""
rr record/replay tools — deterministic recording and reverse debugging.

rr only supports x86/x86_64 and requires perf_event_paranoid <= 1.
"""
from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..config import TOOL_RR, QEMU_USER_MAP, DEFAULT_EXEC_TIMEOUT_SECONDS
from ..errors import PwnMcpError
from ..utils import detect_arch, which_tool

if TYPE_CHECKING:
    from ..app import PwnMcpApp


def _require_rr() -> None:
    if not which_tool(TOOL_RR):
        raise PwnMcpError("tool_not_found", "rr_missing", f"'{TOOL_RR}' is not installed.")


def _check_perf_paranoid() -> None:
    try:
        val = int(Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip())
        if val > 1:
            raise PwnMcpError(
                "configuration_error", "perf_event_paranoid",
                f"perf_event_paranoid is {val}, must be <= 1. "
                "Run: echo 1 | sudo tee /proc/sys/kernel/perf_event_paranoid",
            )
    except FileNotFoundError:
        pass  # Not on Linux, or proc not mounted — let rr fail naturally


def start_rr_record(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    args: list[str] | None = None,
    stdin_data: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    _require_rr()
    _check_perf_paranoid()

    session = app.sessions.get(session_id)
    binary = app.security.resolve_binary(binary_path)
    arch = detect_arch(binary)

    if arch not in ("x86", "x86_64"):
        raise PwnMcpError(
            "invalid_request", "rr_arch_unsupported",
            f"rr only supports x86/x86_64, got '{arch}'.",
        )

    recording_id = f"rr_{uuid.uuid4().hex[:8]}"
    rec_dir = session.session_dir / "rr_recordings" / recording_id
    rec_dir.mkdir(parents=True, exist_ok=True)

    timeout = timeout_seconds or DEFAULT_EXEC_TIMEOUT_SECONDS
    cmd = [TOOL_RR, "record", "-o", str(rec_dir), str(binary)] + (args or [])

    try:
        proc = subprocess.run(
            cmd,
            input=stdin_data.encode() if stdin_data else None,
            capture_output=True,
            timeout=timeout,
            cwd=str(session.session_dir),
        )
    except subprocess.TimeoutExpired:
        raise PwnMcpError("timeout_or_resource_limit", "rr_timeout", f"rr record exceeded {timeout}s timeout.")

    with session._lock:
        session.recordings[recording_id] = str(rec_dir)

    return {
        "ok": True,
        "result": {
            "recording_id": recording_id,
            "recording_path": str(rec_dir),
            "exit_code": proc.returncode,
            "stderr": proc.stderr.decode("utf-8", errors="replace")[:4096],
        },
    }


def start_rr_replay(
    app: "PwnMcpApp",
    session_id: str,
    recording_id: str,
) -> dict[str, Any]:
    """
    Start an rr replay session. This launches `rr replay` with GDB/MI output,
    creating a debug session that supports reverse stepping.
    """
    _require_rr()
    session = app.sessions.get(session_id)

    rec_path = session.recordings.get(recording_id)
    if rec_path is None:
        raise PwnMcpError("not_found", "recording_not_found", f"Recording '{recording_id}' not found.")

    # Import GDB tools to create a debug session backed by rr replay
    from .gdb import GdbMiClient
    from ..store import DebugSession
    import os

    cmd = [TOOL_RR, "replay", "-o", "--interpreter=mi2", rec_path]
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
        )
    except FileNotFoundError as exc:
        raise PwnMcpError("tool_not_found", "rr_replay_exec", str(exc))

    debug_id = f"rr_dbg_{uuid.uuid4().hex[:8]}"
    ds = DebugSession(
        debug_id=debug_id,
        session_id=session_id,
        binary_path="(rr replay)",
        framework="vanilla",
        rr_proc=proc,
        recording_path=rec_path,
    )
    mi = GdbMiClient(proc)
    ds._mi = mi  # type: ignore[attr-defined]

    with session._lock:
        session.debug_sessions[debug_id] = ds

    return {
        "ok": True,
        "result": {
            "debug_id": debug_id,
            "recording_id": recording_id,
            "hint": "Use reverse_continue, reverse_step, reverse_next, reverse_finish for reverse debugging.",
        },
    }


def list_recordings(
    app: "PwnMcpApp",
    session_id: str,
) -> dict[str, Any]:
    session = app.sessions.get(session_id)
    with session._lock:
        recs = dict(session.recordings)
    return {"ok": True, "result": {"recordings": recs}}


def reverse_continue(app: "PwnMcpApp", session_id: str, debug_id: str) -> dict[str, Any]:
    from .gdb import _get_mi
    mi = _get_mi(app, session_id, debug_id)
    result = mi.send("exec-continue --reverse")
    return {"ok": True, "result": result}


def reverse_step(app: "PwnMcpApp", session_id: str, debug_id: str) -> dict[str, Any]:
    from .gdb import _get_mi
    mi = _get_mi(app, session_id, debug_id)
    result = mi.send("exec-step --reverse")
    return {"ok": True, "result": result}


def reverse_next(app: "PwnMcpApp", session_id: str, debug_id: str) -> dict[str, Any]:
    from .gdb import _get_mi
    mi = _get_mi(app, session_id, debug_id)
    result = mi.send("exec-next --reverse")
    return {"ok": True, "result": result}


def reverse_finish(app: "PwnMcpApp", session_id: str, debug_id: str) -> dict[str, Any]:
    from .gdb import _get_mi
    mi = _get_mi(app, session_id, debug_id)
    result = mi.send("exec-finish --reverse")
    return {"ok": True, "result": result}


def register(app: "PwnMcpApp") -> dict[str, dict]:
    def _h(fn):
        def wrapper(**kwargs):
            return fn(app, **kwargs)
        return wrapper

    _sid = {"type": "string", "description": "Session ID."}
    _did = {"type": "string", "description": "Debug session ID (from start_rr_replay)."}

    return {
        "start_rr_record": {
            "handler": _h(start_rr_record),
            "schema": Tool(
                name="start_rr_record",
                description=(
                    "Record a deterministic execution trace with rr. "
                    "x86/x86_64 only. Requires perf_event_paranoid <= 1."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": {"type": "string"},
                        "args": {"type": "array", "items": {"type": "string"}, "default": []},
                        "stdin_data": {"type": "string"},
                        "timeout_seconds": {"type": "integer", "description": "Max seconds for the recording. Default: 30."},
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
        "start_rr_replay": {
            "handler": _h(start_rr_replay),
            "schema": Tool(
                name="start_rr_replay",
                description=(
                    "Start an rr replay debug session. Returns a debug_id that supports "
                    "all GDB commands plus reverse_continue, reverse_step, reverse_next, reverse_finish."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "recording_id": {"type": "string"},
                    },
                    "required": ["session_id", "recording_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "list_recordings": {
            "handler": _h(list_recordings),
            "schema": Tool(
                name="list_recordings",
                description="List all rr recordings in a session.",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid},
                    "required": ["session_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "reverse_continue": {
            "handler": _h(reverse_continue),
            "schema": Tool(
                name="reverse_continue",
                description="Reverse-continue execution (run backwards to the previous breakpoint or start).",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid, "debug_id": _did},
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "reverse_step": {
            "handler": _h(reverse_step),
            "schema": Tool(
                name="reverse_step",
                description="Reverse single-step one source line.",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid, "debug_id": _did},
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "reverse_next": {
            "handler": _h(reverse_next),
            "schema": Tool(
                name="reverse_next",
                description="Reverse step over one source line.",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid, "debug_id": _did},
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "reverse_finish": {
            "handler": _h(reverse_finish),
            "schema": Tool(
                name="reverse_finish",
                description="Reverse-finish: run backwards until the start of the current function.",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid, "debug_id": _did},
                    "required": ["session_id", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
    }
