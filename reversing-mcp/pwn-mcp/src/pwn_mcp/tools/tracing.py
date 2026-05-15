"""
Tracing tools: strace, ltrace, valgrind, uftrace.

Each tool runs the binary as a subprocess, captures output, and stores it
in the session output directory for later retrieval.
"""
from __future__ import annotations

import re
import subprocess
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..config import (
    TOOL_STRACE, TOOL_LTRACE, TOOL_VALGRIND, TOOL_UFTRACE,
    QEMU_USER_MAP, DEFAULT_TRACE_TIMEOUT_SECONDS, DEFAULT_OUTPUT_MAX_BYTES,
)
from ..errors import PwnMcpError
from ..utils import detect_arch, which_tool, truncate_output

if TYPE_CHECKING:
    from ..app import PwnMcpApp


# ── Internal helpers ──────────────────────────────────────────────────────────

def _require_tool(name: str, label: str) -> str:
    path = which_tool(name)
    if path is None:
        raise PwnMcpError("tool_not_found", f"{label}_missing", f"'{name}' is not installed.")
    return path


def _summarize_strace(text: str) -> dict[str, Any]:
    syscall_re = re.compile(r"^\s*(?:\[[^\]]+\]\s*)?(?:\d+\s+)?([A-Za-z_][A-Za-z0-9_]*)\(")
    counts: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    for line in text.splitlines():
        match = syscall_re.match(line)
        if not match:
            continue
        syscall = match.group(1)
        counts[syscall] += 1
        err_match = re.search(r"=\s*-1\s+([A-Z][A-Z0-9_]+)", line)
        if err_match:
            errors[err_match.group(1)] += 1
    return {
        "syscall_count": sum(counts.values()),
        "top_syscalls": [{"name": name, "count": count} for name, count in counts.most_common(20)],
        "errors": [{"errno": name, "count": count} for name, count in errors.most_common(20)],
    }


def _summarize_valgrind(text: str) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    error_match = re.search(r"ERROR SUMMARY:\s+(\d+)\s+errors?", text)
    if error_match:
        summary["error_count"] = int(error_match.group(1))
    leak_match = re.search(r"definitely lost:\s+([0-9,]+)\s+bytes\s+in\s+([0-9,]+)\s+blocks", text)
    if leak_match:
        summary["definitely_lost_bytes"] = int(leak_match.group(1).replace(",", ""))
        summary["definitely_lost_blocks"] = int(leak_match.group(2).replace(",", ""))
    invalid_reads = len(re.findall(r"Invalid read", text))
    invalid_writes = len(re.findall(r"Invalid write", text))
    if invalid_reads or invalid_writes:
        summary["invalid_reads"] = invalid_reads
        summary["invalid_writes"] = invalid_writes
    return summary


def _trace_summary(label: str, text: str) -> dict[str, Any]:
    if label == "strace":
        return _summarize_strace(text)
    if label.startswith("valgrind"):
        return _summarize_valgrind(text)
    return {}


def _run_tracer(
    app: "PwnMcpApp",
    session_id: str,
    cmd: list[str],
    label: str,
    timeout: int,
    stdin_data: str | None = None,
) -> dict[str, Any]:
    """Run a tracer command and capture its output."""
    session = app.sessions.get(session_id)
    out_dir = app.security.output_dir(session_id, label)
    trace_id = f"{label}_{uuid.uuid4().hex[:8]}"
    out_file = out_dir / f"{trace_id}.txt"

    try:
        proc = subprocess.run(
            cmd,
            input=stdin_data.encode() if stdin_data else None,
            capture_output=True,
            timeout=timeout,
            cwd=str(session.session_dir),
        )
    except subprocess.TimeoutExpired:
        raise PwnMcpError(
            "timeout_or_resource_limit", "trace_timeout",
            f"{label} exceeded {timeout}s timeout.",
        )
    except FileNotFoundError as exc:
        raise PwnMcpError("tool_not_found", f"{label}_exec_failed", str(exc))

    # strace/valgrind write to stderr; ltrace/uftrace may use either
    raw = proc.stderr if proc.stderr else proc.stdout
    out_file.write_bytes(raw)

    text, truncated = truncate_output(raw, DEFAULT_OUTPUT_MAX_BYTES)
    decoded = text.decode("utf-8", errors="replace")
    return {
        "ok": True,
        "result": {
            "trace_id": trace_id,
            "output_file": str(out_file),
            "exit_code": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", errors="replace")[:4096],
            "trace_output": decoded,
            "summary": _trace_summary(label, decoded),
            "truncated": truncated,
        },
    }


def _build_binary_cmd(app: "PwnMcpApp", binary_path: str, args: list[str] | None) -> tuple[str, str, list[str]]:
    """Resolve binary, detect arch, return (resolved_path, arch, full_args)."""
    binary = app.security.resolve_binary(binary_path)
    arch = detect_arch(binary)
    qemu = QEMU_USER_MAP.get(arch, "")
    bin_cmd = ([qemu] if qemu else []) + [str(binary)] + (args or [])
    return str(binary), arch, bin_cmd


# ── Tool handlers ─────────────────────────────────────────────────────────────

def run_with_strace(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    args: list[str] | None = None,
    syscall_filter: str | None = None,
    follow_forks: bool = False,
    timeout_seconds: int | None = None,
    stdin_data: str | None = None,
) -> dict[str, Any]:
    _require_tool(TOOL_STRACE, "strace")
    _, arch, bin_cmd = _build_binary_cmd(app, binary_path, args)
    timeout = timeout_seconds or DEFAULT_TRACE_TIMEOUT_SECONDS

    cmd = [TOOL_STRACE, "-f" if follow_forks else "-e", "trace=all"]
    if syscall_filter:
        cmd = [TOOL_STRACE]
        if follow_forks:
            cmd.append("-f")
        cmd.extend(["-e", f"trace={syscall_filter}"])
    cmd.extend(["--"] + bin_cmd)

    return _run_tracer(app, session_id, cmd, "strace", timeout, stdin_data)


def run_with_ltrace(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    args: list[str] | None = None,
    library_filter: str | None = None,
    timeout_seconds: int | None = None,
    stdin_data: str | None = None,
) -> dict[str, Any]:
    _require_tool(TOOL_LTRACE, "ltrace")
    _, arch, bin_cmd = _build_binary_cmd(app, binary_path, args)
    timeout = timeout_seconds or DEFAULT_TRACE_TIMEOUT_SECONDS

    cmd = [TOOL_LTRACE]
    if library_filter:
        cmd.extend(["-e", library_filter])
    cmd.extend(["--"] + bin_cmd)

    return _run_tracer(app, session_id, cmd, "ltrace", timeout, stdin_data)


def run_with_valgrind(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    args: list[str] | None = None,
    tool: str = "memcheck",
    extra_flags: list[str] | None = None,
    timeout_seconds: int | None = None,
    stdin_data: str | None = None,
) -> dict[str, Any]:
    _require_tool(TOOL_VALGRIND, "valgrind")
    _, arch, bin_cmd = _build_binary_cmd(app, binary_path, args)
    timeout = timeout_seconds or DEFAULT_TRACE_TIMEOUT_SECONDS

    allowed_tools = ("memcheck", "callgrind", "helgrind", "massif", "dhat", "cachegrind")
    if tool not in allowed_tools:
        raise PwnMcpError(
            "invalid_request", "invalid_valgrind_tool",
            f"Valgrind tool must be one of: {', '.join(allowed_tools)}",
        )

    cmd = [TOOL_VALGRIND, f"--tool={tool}"]
    if extra_flags:
        cmd.extend(extra_flags)
    cmd.extend(bin_cmd)

    return _run_tracer(app, session_id, cmd, f"valgrind_{tool}", timeout, stdin_data)


def run_with_uftrace(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    args: list[str] | None = None,
    depth: int = 5,
    timeout_seconds: int | None = None,
    stdin_data: str | None = None,
) -> dict[str, Any]:
    _require_tool(TOOL_UFTRACE, "uftrace")
    _, arch, bin_cmd = _build_binary_cmd(app, binary_path, args)
    timeout = timeout_seconds or DEFAULT_TRACE_TIMEOUT_SECONDS

    cmd = [TOOL_UFTRACE, "record", f"--depth={depth}"] + bin_cmd

    # uftrace records to uftrace.data, then we replay to text
    session = app.sessions.get(session_id)
    try:
        subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            cwd=str(session.session_dir),
            input=stdin_data.encode() if stdin_data else None,
        )
    except subprocess.TimeoutExpired:
        raise PwnMcpError("timeout_or_resource_limit", "uftrace_timeout", f"uftrace exceeded {timeout}s timeout.")

    # Replay the recorded trace
    try:
        replay = subprocess.run(
            [TOOL_UFTRACE, "replay"],
            capture_output=True, timeout=30,
            cwd=str(session.session_dir),
        )
        output = replay.stdout
    except Exception:
        output = b"(replay failed)"

    trace_id = f"uftrace_{uuid.uuid4().hex[:8]}"
    out_dir = app.security.output_dir(session_id, "uftrace")
    out_file = out_dir / f"{trace_id}.txt"
    out_file.write_bytes(output)

    text, truncated = truncate_output(output, DEFAULT_OUTPUT_MAX_BYTES)
    return {
        "ok": True,
        "result": {
            "trace_id": trace_id,
            "output_file": str(out_file),
            "trace_output": text.decode("utf-8", errors="replace"),
            "truncated": truncated,
        },
    }


def get_trace_output(
    app: "PwnMcpApp",
    session_id: str,
    trace_id: str,
    max_bytes: int = 32768,
) -> dict[str, Any]:
    """Retrieve stored trace output by trace_id."""
    session = app.sessions.get(session_id)
    out_root = app.security.output_dir(session_id)

    # Search for the trace file across subdirectories
    for f in out_root.rglob(f"{trace_id}*"):
        if f.is_file():
            raw = f.read_bytes()
            text, truncated = truncate_output(raw, max_bytes)
            return {
                "ok": True,
                "result": {
                    "trace_id": trace_id,
                    "file": str(f),
                    "text": text.decode("utf-8", errors="replace"),
                    "truncated": truncated,
                },
            }

    raise PwnMcpError("not_found", "trace_not_found", f"Trace output '{trace_id}' not found.")


# ── Tool definitions ──────────────────────────────────────────────────────────

def register(app: "PwnMcpApp") -> dict[str, dict]:
    def _h(fn):
        def wrapper(**kwargs):
            return fn(app, **kwargs)
        return wrapper

    _sid = {"type": "string", "description": "Session ID."}
    _bp = {"type": "string", "description": "Path to binary (relative to workspace or absolute within it)."}
    _args = {"type": "array", "items": {"type": "string"}, "default": []}

    return {
        "run_with_strace": {
            "handler": _h(run_with_strace),
            "schema": Tool(
                name="run_with_strace",
                description="Run a binary under strace to capture system calls.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": _bp,
                        "args": _args,
                        "syscall_filter": {
                            "type": "string",
                            "description": "Comma-separated syscall names or categories (e.g. 'open,read,write' or 'network').",
                        },
                        "follow_forks": {"type": "boolean", "default": False},
                        "timeout_seconds": {"type": "integer", "description": "Max seconds before the traced process is killed. Default: 120."},
                        "stdin_data": {"type": "string", "description": "Data to send to the process's stdin."},
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
        "run_with_ltrace": {
            "handler": _h(run_with_ltrace),
            "schema": Tool(
                name="run_with_ltrace",
                description="Run a binary under ltrace to capture library calls.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": _bp,
                        "args": _args,
                        "library_filter": {
                            "type": "string",
                            "description": "Library call filter expression (e.g. 'malloc+free+printf').",
                        },
                        "timeout_seconds": {"type": "integer", "description": "Max seconds before the traced process is killed. Default: 120."},
                        "stdin_data": {"type": "string"},
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
        "run_with_valgrind": {
            "handler": _h(run_with_valgrind),
            "schema": Tool(
                name="run_with_valgrind",
                description=(
                    "Run a binary under Valgrind. Supports memcheck (default), callgrind, "
                    "helgrind, massif, dhat, cachegrind."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": _bp,
                        "args": _args,
                        "tool": {
                            "type": "string",
                            "enum": ["memcheck", "callgrind", "helgrind", "massif", "dhat", "cachegrind"],
                            "default": "memcheck",
                        },
                        "extra_flags": {"type": "array", "items": {"type": "string"}},
                        "timeout_seconds": {"type": "integer", "description": "Max seconds before the traced process is killed. Default: 120."},
                        "stdin_data": {"type": "string"},
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
        "run_with_uftrace": {
            "handler": _h(run_with_uftrace),
            "schema": Tool(
                name="run_with_uftrace",
                description="Run a binary under uftrace for structured function call tracing.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": _bp,
                        "args": _args,
                        "depth": {"type": "integer", "description": "Maximum call depth to trace. Default: 5.", "default": 5},
                        "timeout_seconds": {"type": "integer", "description": "Max seconds before the traced process is killed. Default: 120."},
                        "stdin_data": {"type": "string"},
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
        "get_trace_output": {
            "handler": _h(get_trace_output),
            "schema": Tool(
                name="get_trace_output",
                description="Retrieve stored trace output by trace_id (returned by the run_with_* tools).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "trace_id": {"type": "string"},
                        "max_bytes": {"type": "integer", "default": 32768},
                    },
                    "required": ["session_id", "trace_id"],
                    "additionalProperties": False,
                },
            ),
        },
    }
