"""
Code coverage tools using DynamoRIO drcov.
"""
from __future__ import annotations

import subprocess
import uuid
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..config import TOOL_DRRUN, TOOL_DRCOV, QEMU_USER_MAP, DEFAULT_EXEC_TIMEOUT_SECONDS
from ..errors import PwnMcpError
from ..utils import detect_arch, which_tool

if TYPE_CHECKING:
    from ..app import PwnMcpApp


def run_with_coverage(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    args: list[str] | None = None,
    stdin_data: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    if not which_tool(TOOL_DRRUN):
        raise PwnMcpError("tool_not_found", "dynamorio_missing", f"'{TOOL_DRRUN}' not found.")

    session = app.sessions.get(session_id)
    binary = app.security.resolve_binary(binary_path)
    arch = detect_arch(binary)
    timeout = timeout_seconds or DEFAULT_EXEC_TIMEOUT_SECONDS

    cov_id = f"cov_{uuid.uuid4().hex[:8]}"
    out_dir = app.security.output_dir(session_id, "coverage")
    log_dir = out_dir / cov_id
    log_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        TOOL_DRRUN,
        "-c", TOOL_DRCOV,
        "-logdir", str(log_dir),
        "--", str(binary),
    ] + (args or [])

    try:
        proc = subprocess.run(
            cmd,
            input=stdin_data.encode() if stdin_data else None,
            capture_output=True,
            timeout=timeout,
            cwd=str(session.session_dir),
        )
    except subprocess.TimeoutExpired:
        raise PwnMcpError("timeout_or_resource_limit", "coverage_timeout", f"DynamoRIO exceeded {timeout}s timeout.")
    except FileNotFoundError as exc:
        raise PwnMcpError("tool_not_found", "drrun_exec", str(exc))

    # Find generated .log files
    log_files = list(log_dir.glob("*.log"))

    return {
        "ok": True,
        "result": {
            "coverage_id": cov_id,
            "log_dir": str(log_dir),
            "log_files": [str(f) for f in log_files],
            "exit_code": proc.returncode,
            "stderr": proc.stderr.decode("utf-8", errors="replace")[:4096],
        },
    }


def get_coverage_report(
    app: "PwnMcpApp",
    session_id: str,
    coverage_id: str,
) -> dict[str, Any]:
    """Parse drcov log files and return a coverage summary."""
    out_dir = app.security.output_dir(session_id, "coverage")
    cov_dir = out_dir / coverage_id

    if not cov_dir.exists():
        raise PwnMcpError("not_found", "coverage_not_found", f"Coverage data '{coverage_id}' not found.")

    log_files = list(cov_dir.glob("*.log"))
    if not log_files:
        return {"ok": True, "result": {"coverage_id": coverage_id, "modules": [], "total_blocks": 0}}

    # Parse drcov log format
    modules = []
    total_blocks = 0
    for log in log_files:
        raw = log.read_text(errors="replace")
        in_modules = False
        in_bb = False
        for line in raw.splitlines():
            if line.startswith("Module Table:"):
                in_modules = True
                continue
            if line.startswith("BB Table:"):
                in_modules = False
                in_bb = True
                # BB Table header contains count
                parts = line.split()
                for p in parts:
                    if p.isdigit():
                        total_blocks = int(p)
                        break
                continue
            if in_modules and line.strip() and not line.startswith("Columns"):
                modules.append(line.strip())

    return {
        "ok": True,
        "result": {
            "coverage_id": coverage_id,
            "modules": modules[:50],
            "total_blocks": total_blocks,
            "log_files": [str(f) for f in log_files],
        },
    }


def diff_coverage(
    app: "PwnMcpApp",
    session_id: str,
    coverage_id_a: str,
    coverage_id_b: str,
) -> dict[str, Any]:
    """Compare two coverage runs and report new blocks in B not in A."""
    report_a = get_coverage_report(app, session_id, coverage_id_a)
    report_b = get_coverage_report(app, session_id, coverage_id_b)

    blocks_a = report_a["result"]["total_blocks"]
    blocks_b = report_b["result"]["total_blocks"]

    return {
        "ok": True,
        "result": {
            "coverage_a": coverage_id_a,
            "coverage_b": coverage_id_b,
            "blocks_a": blocks_a,
            "blocks_b": blocks_b,
            "delta": blocks_b - blocks_a,
            "note": "For detailed block-level diff, use a drcov diff tool externally.",
        },
    }


def register(app: "PwnMcpApp") -> dict[str, dict]:
    def _h(fn):
        def wrapper(**kwargs):
            return fn(app, **kwargs)
        return wrapper

    _sid = {"type": "string", "description": "Session ID."}

    return {
        "run_with_coverage": {
            "handler": _h(run_with_coverage),
            "schema": Tool(
                name="run_with_coverage",
                description="Run a binary under DynamoRIO to collect basic-block code coverage (drcov format).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": {"type": "string"},
                        "args": {"type": "array", "items": {"type": "string"}, "default": []},
                        "stdin_data": {"type": "string"},
                        "timeout_seconds": {"type": "integer", "description": "Max seconds before the process is killed. Default: 30."},
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
        "get_coverage_report": {
            "handler": _h(get_coverage_report),
            "schema": Tool(
                name="get_coverage_report",
                description="Parse a drcov coverage log and return module list and block count.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "coverage_id": {"type": "string"},
                    },
                    "required": ["session_id", "coverage_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "diff_coverage": {
            "handler": _h(diff_coverage),
            "schema": Tool(
                name="diff_coverage",
                description="Compare two coverage runs and report block-count delta.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "coverage_id_a": {"type": "string", "description": "Baseline coverage ID."},
                        "coverage_id_b": {"type": "string", "description": "New coverage ID to compare."},
                    },
                    "required": ["session_id", "coverage_id_a", "coverage_id_b"],
                    "additionalProperties": False,
                },
            ),
        },
    }
