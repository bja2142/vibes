"""
AFL++ fuzzing tools — start/stop/monitor coverage-guided fuzzing with QEMU mode.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..config import (
    TOOL_AFL_FUZZ, TOOL_AFL_TMIN,
    QEMU_USER_MAP, DEFAULT_FUZZ_MAX_SECONDS,
)
from ..errors import PwnMcpError
from ..utils import detect_arch, which_tool

if TYPE_CHECKING:
    from ..app import PwnMcpApp


def start_afl_session(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    input_dir: str | None = None,
    args: list[str] | None = None,
    timeout_seconds: int | None = None,
    qemu_mode: bool = True,
    extra_flags: list[str] | None = None,
    stdin_data: str | None = None,
) -> dict[str, Any]:
    if not which_tool(TOOL_AFL_FUZZ):
        raise PwnMcpError("tool_not_found", "afl_missing", f"'{TOOL_AFL_FUZZ}' not installed.")

    session = app.sessions.get(session_id)
    binary = app.security.resolve_binary(binary_path)
    arch = detect_arch(binary)
    timeout = timeout_seconds or DEFAULT_FUZZ_MAX_SECONDS

    fuzz_id = f"fuzz_{uuid.uuid4().hex[:8]}"
    out_dir = app.security.output_dir(session_id, f"fuzzing/{fuzz_id}")

    # Create input dir with a seed if none provided
    if input_dir:
        in_dir = Path(input_dir)
        if not in_dir.is_absolute():
            in_dir = app.security.workspace_root / in_dir
    else:
        in_dir = out_dir / "input"
        in_dir.mkdir(parents=True, exist_ok=True)
        seed = (in_dir / "seed0")
        if not seed.exists():
            seed.write_bytes(stdin_data.encode() if stdin_data else b"AAAA")

    cmd = [TOOL_AFL_FUZZ, "-i", str(in_dir), "-o", str(out_dir / "output")]
    if qemu_mode:
        cmd.append("-Q")
    if extra_flags:
        cmd.extend(extra_flags)
    cmd.extend(["--", str(binary)] + (args or []))

    env = os.environ.copy()
    env["AFL_NO_UI"] = "1"  # headless
    env["AFL_SKIP_CPUFREQ"] = "1"

    # Launch as background job
    job = app.jobs.create("afl_fuzz", session_id)

    def _run_fuzz(j):
        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=timeout,
                cwd=str(session.session_dir), env=env,
            )
            return {
                "fuzz_id": fuzz_id,
                "exit_code": proc.returncode,
                "output_dir": str(out_dir / "output"),
            }
        except subprocess.TimeoutExpired:
            return {"fuzz_id": fuzz_id, "status": "timeout_reached", "output_dir": str(out_dir / "output")}

    app.jobs.run_async(job, _run_fuzz)

    return {
        "ok": True,
        "result": {
            "fuzz_id": fuzz_id,
            "job_id": job.job_id,
            "output_dir": str(out_dir / "output"),
            "cmd": cmd,
            "hint": f"Use get_fuzzer_status to monitor. Use get_crash_inputs to retrieve crashes. Job ID: {job.job_id}",
        },
    }


def get_fuzzer_status(
    app: "PwnMcpApp",
    session_id: str,
    fuzz_id: str,
) -> dict[str, Any]:
    session = app.sessions.get(session_id)
    out_dir = app.security.output_dir(session_id, f"fuzzing/{fuzz_id}/output")

    stats_file = None
    for f in out_dir.rglob("fuzzer_stats"):
        stats_file = f
        break

    if stats_file is None:
        return {"ok": True, "result": {"fuzz_id": fuzz_id, "status": "not_started_or_no_stats"}}

    stats = {}
    for line in stats_file.read_text().splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            stats[key.strip()] = val.strip()

    return {"ok": True, "result": {"fuzz_id": fuzz_id, "stats": stats}}


def get_crash_inputs(
    app: "PwnMcpApp",
    session_id: str,
    fuzz_id: str,
    max_inputs: int = 20,
) -> dict[str, Any]:
    session = app.sessions.get(session_id)
    out_dir = app.security.output_dir(session_id, f"fuzzing/{fuzz_id}/output")

    crashes = []
    crash_dir = None
    for d in out_dir.rglob("crashes"):
        if d.is_dir():
            crash_dir = d
            break

    if crash_dir:
        for f in sorted(crash_dir.iterdir())[:max_inputs]:
            if f.is_file() and f.name != "README.txt":
                raw = f.read_bytes()
                crashes.append({
                    "filename": f.name,
                    "size": len(raw),
                    "hex_preview": raw[:64].hex(),
                    "path": str(f),
                })

    return {
        "ok": True,
        "result": {
            "fuzz_id": fuzz_id,
            "crash_count": len(crashes),
            "crashes": crashes,
        },
    }


def stop_fuzzer(
    app: "PwnMcpApp",
    session_id: str,
    job_id: str,
) -> dict[str, Any]:
    job = app.jobs.get(job_id)
    if job is None:
        raise PwnMcpError("not_found", "job_not_found", f"Job '{job_id}' not found.")
    job.cancel()
    return {"ok": True, "result": {"job_id": job_id, "cancelled": True}}


def minimize_input(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    input_file: str,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    if not which_tool(TOOL_AFL_TMIN):
        raise PwnMcpError("tool_not_found", "afl_tmin_missing", f"'{TOOL_AFL_TMIN}' not installed.")

    binary = app.security.resolve_binary(binary_path)
    session = app.sessions.get(session_id)
    out_dir = app.security.output_dir(session_id, "minimize")
    out_file = out_dir / f"min_{uuid.uuid4().hex[:8]}"

    cmd = [TOOL_AFL_TMIN, "-Q", "-i", input_file, "-o", str(out_file), "--", str(binary)]
    env = os.environ.copy()
    env["AFL_NO_UI"] = "1"

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout_seconds, env=env)
    except subprocess.TimeoutExpired:
        raise PwnMcpError("timeout_or_resource_limit", "minimize_timeout", f"afl-tmin exceeded {timeout_seconds}s.")

    minimized = out_file.read_bytes() if out_file.exists() else b""
    return {
        "ok": True,
        "result": {
            "output_file": str(out_file),
            "original_size": os.path.getsize(input_file) if os.path.exists(input_file) else None,
            "minimized_size": len(minimized),
            "hex_preview": minimized[:64].hex(),
        },
    }


def register(app: "PwnMcpApp") -> dict[str, dict]:
    def _h(fn):
        def wrapper(**kwargs):
            return fn(app, **kwargs)
        return wrapper

    _sid = {"type": "string", "description": "Session ID."}

    return {
        "start_afl_session": {
            "handler": _h(start_afl_session),
            "schema": Tool(
                name="start_afl_session",
                description=(
                    "Start AFL++ coverage-guided fuzzing in QEMU mode (no source needed). "
                    "Runs as a background job. Use get_fuzzer_status to monitor and get_crash_inputs to retrieve crashes."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": {"type": "string"},
                        "input_dir": {"type": "string", "description": "Seed corpus directory. Auto-created with a default seed if omitted."},
                        "args": {"type": "array", "items": {"type": "string"}, "default": []},
                        "timeout_seconds": {"type": "integer", "description": "Max fuzzing duration. Default: 3600s (1 hour)."},
                        "qemu_mode": {"type": "boolean", "default": True},
                        "extra_flags": {"type": "array", "items": {"type": "string"}},
                        "stdin_data": {"type": "string", "description": "Seed input to use if no input_dir is provided."},
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
        "get_fuzzer_status": {
            "handler": _h(get_fuzzer_status),
            "schema": Tool(
                name="get_fuzzer_status",
                description="Get AFL++ fuzzer stats (executions/sec, paths found, crashes, hangs).",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid, "fuzz_id": {"type": "string"}},
                    "required": ["session_id", "fuzz_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "get_crash_inputs": {
            "handler": _h(get_crash_inputs),
            "schema": Tool(
                name="get_crash_inputs",
                description="Retrieve crash-triggering inputs from an AFL++ session.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "fuzz_id": {"type": "string"},
                        "max_inputs": {"type": "integer", "default": 20},
                    },
                    "required": ["session_id", "fuzz_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "stop_fuzzer": {
            "handler": _h(stop_fuzzer),
            "schema": Tool(
                name="stop_fuzzer",
                description="Cancel a running AFL++ fuzzing job.",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid, "job_id": {"type": "string"}},
                    "required": ["session_id", "job_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "minimize_input": {
            "handler": _h(minimize_input),
            "schema": Tool(
                name="minimize_input",
                description="Minimize a crash-triggering input using afl-tmin.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": {"type": "string"},
                        "input_file": {"type": "string", "description": "Path to the crash input file."},
                        "timeout_seconds": {"type": "integer", "default": 60, "description": "Max seconds for minimization. Default: 60."},
                    },
                    "required": ["session_id", "binary_path", "input_file"],
                    "additionalProperties": False,
                },
            ),
        },
    }
