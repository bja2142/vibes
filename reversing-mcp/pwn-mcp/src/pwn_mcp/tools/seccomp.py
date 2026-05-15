"""
Seccomp BPF filter analysis using seccomp-tools.
"""
from __future__ import annotations

import re
import subprocess
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..config import TOOL_SECCOMP_TOOLS, DEFAULT_EXEC_TIMEOUT_SECONDS
from ..errors import PwnMcpError
from ..utils import which_tool

if TYPE_CHECKING:
    from ..app import PwnMcpApp


_ACTION_WORDS = ("ALLOW", "KILL", "KILL_PROCESS", "KILL_THREAD", "ERRNO", "TRAP", "TRACE", "LOG")


def _parse_seccomp_output(text: str) -> dict[str, Any]:
    allowed: set[str] = set()
    blocked: set[str] = set()
    mentioned: set[str] = set()
    action_counts: dict[str, int] = {action.lower(): 0 for action in _ACTION_WORDS}
    default_action = "unknown"

    for line in text.splitlines():
        upper = line.upper()
        for action in _ACTION_WORDS:
            if re.search(rf"\b{action}\b", upper):
                action_counts[action.lower()] += 1

        syscall_match = re.search(r"\b(?:syscall|A)\s*(?:==|!=)\s*([A-Za-z_][A-Za-z0-9_]*)", line)
        if not syscall_match:
            syscall_match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\b\s*=>\s*(ALLOW|KILL|KILL_PROCESS|KILL_THREAD|ERRNO|TRAP|TRACE|LOG)", line, re.I)
        if syscall_match:
            syscall = syscall_match.group(1)
            if syscall not in {"if", "else", "return", "action", "syscall"}:
                mentioned.add(syscall)
                window = upper
                if "ALLOW" in window:
                    allowed.add(syscall)
                if any(action in window for action in ("KILL", "ERRNO", "TRAP")):
                    blocked.add(syscall)

        if "DEFAULT" in upper:
            for action in _ACTION_WORDS:
                if action in upper:
                    default_action = action.lower()

    if default_action == "unknown":
        last_actions = [action for action in _ACTION_WORDS if re.search(rf"\bRETURN\s+{action}\b", text.upper())]
        if last_actions:
            default_action = last_actions[-1].lower()

    archetypes = []
    if "execve" in blocked or ("execve" in mentioned and default_action.startswith("kill")):
        archetypes.append("no_exec")
    if {"read", "write"}.issubset(allowed) and any(item in allowed for item in ("exit", "exit_group")) and default_action.startswith("kill"):
        archetypes.append("strict_io")
    network_syscalls = {"socket", "connect", "accept", "accept4", "bind", "listen", "sendto", "recvfrom"}
    if network_syscalls & blocked:
        archetypes.append("network_restricted")
    if default_action == "allow" and not blocked:
        archetypes.append("allow_default")

    return {
        "allowed_syscalls": sorted(allowed),
        "blocked_syscalls": sorted(blocked),
        "mentioned_syscalls": sorted(mentioned),
        "default_action": default_action,
        "action_counts": {key: value for key, value in action_counts.items() if value},
        "archetypes": archetypes or ["unknown"],
    }


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
    cmd = [TOOL_SECCOMP_TOOLS, "dump", "-f", "pfc", str(binary)] + (args or [])

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
    if proc.returncode != 0 and ("invalid option" in stderr.lower() or "unknown" in stderr.lower()):
        fallback_cmd = [TOOL_SECCOMP_TOOLS, "dump", str(binary)] + (args or [])
        proc = subprocess.run(
            fallback_cmd,
            capture_output=True,
            timeout=DEFAULT_EXEC_TIMEOUT_SECONDS,
        )
        output = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
    if not output and stderr:
        output = stderr

    return {
        "ok": True,
        "result": {
            "binary": str(binary),
            "seccomp_filter": output,
            "parsed": _parse_seccomp_output(output),
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
