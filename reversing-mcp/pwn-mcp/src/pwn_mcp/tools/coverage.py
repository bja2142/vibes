"""
Code coverage tools using DynamoRIO drcov.
"""
from __future__ import annotations

import re
import struct
import subprocess
import uuid
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..config import TOOL_DRRUN, TOOL_DRCOV, QEMU_USER_MAP, DEFAULT_EXEC_TIMEOUT_SECONDS
from ..errors import PwnMcpError
from ..utils import detect_arch, which_tool

if TYPE_CHECKING:
    from ..app import PwnMcpApp


def _parse_drcov_log(path) -> dict[str, Any]:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    modules: list[str] = []
    total_blocks = 0

    in_modules = False
    for line in text.splitlines():
        if line.startswith("Module Table:"):
            in_modules = True
            continue
        if line.startswith("BB Table:"):
            in_modules = False
            match = re.search(r"BB Table:\s+(\d+)\s+bbs", line)
            if match:
                total_blocks = int(match.group(1))
            break
        if in_modules and line.strip() and not line.startswith("Columns"):
            modules.append(line.strip())

    blocks: list[dict[str, int]] = []
    marker = re.search(rb"BB Table:\s+(\d+)\s+bbs[^\n]*\n", raw)
    if marker:
        total_blocks = int(marker.group(1))
        offset = marker.end()
        available = max(0, len(raw) - offset)
        count = min(total_blocks, available // 8)
        for index in range(count):
            start, size, module_id = struct.unpack_from("<IHH", raw, offset + index * 8)
            blocks.append({"module_id": module_id, "start": start, "size": size})

    return {"modules": modules, "total_blocks": total_blocks, "blocks": blocks}


def _block_key(block: dict[str, int]) -> tuple[int, int, int]:
    return int(block["module_id"]), int(block["start"]), int(block["size"])


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
    include_blocks: bool = False,
    max_blocks: int = 1000,
) -> dict[str, Any]:
    """Parse drcov log files and return a coverage summary."""
    out_dir = app.security.output_dir(session_id, "coverage")
    cov_dir = out_dir / coverage_id

    if not cov_dir.exists():
        raise PwnMcpError("not_found", "coverage_not_found", f"Coverage data '{coverage_id}' not found.")

    log_files = list(cov_dir.glob("*.log"))
    if not log_files:
        result: dict[str, Any] = {
            "coverage_id": coverage_id,
            "modules": [],
            "total_blocks": 0,
            "unique_blocks": 0,
            "log_files": [],
        }
        if include_blocks:
            result["blocks"] = []
            result["blocks_truncated"] = False
        return {"ok": True, "result": result}

    modules: list[str] = []
    all_blocks: list[dict[str, int]] = []
    total_blocks = 0
    for log in log_files:
        parsed = _parse_drcov_log(log)
        modules.extend(parsed["modules"])
        all_blocks.extend(parsed["blocks"])
        total_blocks += parsed["total_blocks"]

    unique_blocks = {_block_key(block) for block in all_blocks}
    sampled_blocks = sorted(unique_blocks)[:max(0, min(int(max_blocks), 10000))]
    result = {
        "coverage_id": coverage_id,
        "modules": modules[:50],
        "total_blocks": total_blocks,
        "unique_blocks": len(unique_blocks),
        "log_files": [str(f) for f in log_files],
    }
    if include_blocks:
        result["blocks"] = [
            {"module_id": module_id, "start": start, "size": size}
            for module_id, start, size in sampled_blocks
        ]
        result["blocks_truncated"] = len(unique_blocks) > len(sampled_blocks)

    return {"ok": True, "result": result}


def diff_coverage(
    app: "PwnMcpApp",
    session_id: str,
    coverage_id_a: str,
    coverage_id_b: str,
) -> dict[str, Any]:
    """Compare two coverage runs and report new blocks in B not in A."""
    report_a = get_coverage_report(app, session_id, coverage_id_a, include_blocks=True, max_blocks=10000)
    report_b = get_coverage_report(app, session_id, coverage_id_b, include_blocks=True, max_blocks=10000)
    blocks_a = {_block_key(block) for block in report_a["result"].get("blocks", [])}
    blocks_b = {_block_key(block) for block in report_b["result"].get("blocks", [])}
    new_blocks = sorted(blocks_b - blocks_a)
    dropped_blocks = sorted(blocks_a - blocks_b)

    return {
        "ok": True,
        "result": {
            "coverage_a": coverage_id_a,
            "coverage_b": coverage_id_b,
            "blocks_a": report_a["result"]["unique_blocks"],
            "blocks_b": report_b["result"]["unique_blocks"],
            "new_block_count": len(new_blocks),
            "dropped_block_count": len(dropped_blocks),
            "new_blocks_sample": [
                {"module_id": module_id, "start": start, "size": size}
                for module_id, start, size in new_blocks[:100]
            ],
            "dropped_blocks_sample": [
                {"module_id": module_id, "start": start, "size": size}
                for module_id, start, size in dropped_blocks[:100]
            ],
            "truncated": report_a["result"].get("blocks_truncated", False) or report_b["result"].get("blocks_truncated", False),
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
                        "include_blocks": {"type": "boolean", "default": False},
                        "max_blocks": {"type": "integer", "default": 1000},
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
