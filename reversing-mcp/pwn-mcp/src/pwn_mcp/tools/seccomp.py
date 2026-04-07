"""
Seccomp BPF filter analysis using seccomp-tools.
"""
from __future__ import annotations

import subprocess
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..config import TOOL_SECCOMP_TOOLS, DEFAULT_EXEC_TIMEOUT_SECONDS
from ..errors import PwnMcpError
from ..utils import which_tool

if TYPE_CHECKING:
    from ..app import PwnMcpApp


def analyze_seccomp(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    args: list[str] | None = None,
) -> dict[str, Any]:
    path = which_tool(TOOL_SECCOMP_TOOLS)
    if not path:
        raise PwnMcpError("tool_not_found", "seccomp_tools_missing", f"'{TOOL_SECCOMP_TOOLS}' is not installed.")

    binary = app.security.resolve_binary(binary_path)
    cmd = [TOOL_SECCOMP_TOOLS, "dump", str(binary)] + (args or [])

    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=DEFAULT_EXEC_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise PwnMcpError("timeout_or_resource_limit", "seccomp_timeout", "seccomp-tools timed out.")
    except FileNotFoundError as exc:
        raise PwnMcpError("tool_not_found", "seccomp_exec", str(exc))

    output = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    if not output and stderr:
        output = stderr

    return {
        "ok": True,
        "result": {
            "binary": str(binary),
            "seccomp_filter": output,
            "exit_code": proc.returncode,
        },
    }


def register(app: "PwnMcpApp") -> dict[str, dict]:
    def _h(fn):
        def wrapper(**kwargs):
            return fn(app, **kwargs)
        return wrapper

    return {
        "analyze_seccomp": {
            "handler": _h(analyze_seccomp),
            "schema": Tool(
                name="analyze_seccomp",
                description=(
                    "Dump and disassemble seccomp BPF filters installed by a binary. "
                    "Shows which syscalls are allowed/blocked."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "binary_path": {"type": "string"},
                        "args": {"type": "array", "items": {"type": "string"}, "default": []},
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
    }
