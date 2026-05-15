"""
Static reverse-engineering triage wrappers useful during CTF work.
"""
from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..config import CAPA_RULES_DIR, DEFAULT_EXEC_TIMEOUT_SECONDS, TOOL_CAPA, TOOL_FLOSS, TOOL_R2, TOOL_YARA
from ..errors import PwnMcpError
from ..utils import which_tool

if TYPE_CHECKING:
    from ..app import PwnMcpApp


def _require_tool(name: str, label: str) -> str:
    found = which_tool(name)
    if not found:
        raise PwnMcpError("tool_not_found", f"{label}_missing", f"'{name}' is not installed.")
    return found


def _resolve_workspace_file(app: "PwnMcpApp", path: str) -> Path:
    return app.security.resolve_binary(path)


def _resolve_workspace_path(app: "PwnMcpApp", path: str) -> Path:
    candidate = app.security._normalize(path)
    resolved = candidate.resolve()
    app.security._require_within(resolved, app.security.workspace_root, "Path")
    if not resolved.exists():
        raise PwnMcpError(
            "invalid_request",
            "path_missing",
            f"Path '{resolved}' does not exist.",
            details={"path": str(resolved)},
        )
    return resolved


def run_capa(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    output_format: str = "json",
    rules_path: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Run FLARE capa against a binary."""
    _require_tool(TOOL_CAPA, "capa")
    app.sessions.get(session_id)
    binary = app.security.resolve_binary(binary_path)
    if output_format not in {"json", "text"}:
        raise PwnMcpError("invalid_request", "invalid_capa_format", "output_format must be 'json' or 'text'.")
    timeout = timeout_seconds or DEFAULT_EXEC_TIMEOUT_SECONDS
    rule_dir = _resolve_workspace_path(app, rules_path) if rules_path else CAPA_RULES_DIR
    cmd = [TOOL_CAPA, "-q", str(binary)]
    if output_format == "json":
        cmd = [TOOL_CAPA, "-q", "-j", str(binary)]
    if rule_dir.exists():
        cmd[1:1] = ["-r", str(rule_dir)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise PwnMcpError("timeout_or_resource_limit", "capa_timeout", f"capa exceeded {timeout}s timeout.")
    return {
        "ok": True,
        "result": {
            "binary_path": str(binary),
            "format": output_format,
            "rules_path": str(rule_dir) if rule_dir.exists() else None,
            "return_code": proc.returncode,
            "stdout": proc.stdout[:20000],
            "stderr": proc.stderr[:10000],
        },
    }


def run_floss(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    output_format: str = "json",
    analysis_types: list[str] | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Run FLARE FLOSS string extraction against a binary."""
    _require_tool(TOOL_FLOSS, "floss")
    app.sessions.get(session_id)
    binary = app.security.resolve_binary(binary_path)
    if output_format not in {"json", "text"}:
        raise PwnMcpError("invalid_request", "invalid_floss_format", "output_format must be 'json' or 'text'.")
    selected = analysis_types or ["static"]
    allowed_types = {"all", "static", "stack", "tight", "decoded"}
    invalid = [item for item in selected if item not in allowed_types]
    if invalid:
        raise PwnMcpError("invalid_request", "invalid_floss_analysis_type", f"Unsupported FLOSS analysis type(s): {', '.join(invalid)}.")
    if "all" in selected and len(selected) > 1:
        raise PwnMcpError("invalid_request", "invalid_floss_analysis_type", "'all' cannot be combined with specific FLOSS analysis types.")
    timeout = timeout_seconds or DEFAULT_EXEC_TIMEOUT_SECONDS
    cmd = [TOOL_FLOSS, str(binary)]
    if output_format == "json":
        cmd = [TOOL_FLOSS, "--json", str(binary)]
    if "all" not in selected:
        cmd[1:1] = ["--only", *selected]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise PwnMcpError("timeout_or_resource_limit", "floss_timeout", f"FLOSS exceeded {timeout}s timeout.")
    return {
        "ok": True,
        "result": {
            "binary_path": str(binary),
            "format": output_format,
            "analysis_types": selected,
            "return_code": proc.returncode,
            "stdout": proc.stdout[:20000],
            "stderr": proc.stderr[:10000],
        },
    }


def run_yara_scan(
    app: "PwnMcpApp",
    session_id: str,
    target_path: str,
    rule_source: str | None = None,
    rule_path: str | None = None,
    show_strings: bool = True,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Run yara rules against a workspace file."""
    _require_tool(TOOL_YARA, "yara")
    app.sessions.get(session_id)
    target = app.security.resolve_binary(target_path)
    if not rule_source and not rule_path:
        raise PwnMcpError("invalid_request", "yara_rule_required", "Provide rule_source or rule_path.")
    if rule_source and rule_path:
        raise PwnMcpError("invalid_request", "yara_rule_ambiguous", "Provide only one of rule_source or rule_path.")

    out_dir = app.security.output_dir(session_id, "yara")
    if rule_source:
        rule_file = out_dir / f"rule_{uuid.uuid4().hex[:8]}.yar"
        rule_file.write_text(rule_source, encoding="utf-8")
    else:
        rule_file = _resolve_workspace_file(app, rule_path or "")
    timeout = timeout_seconds or DEFAULT_EXEC_TIMEOUT_SECONDS
    cmd = [TOOL_YARA]
    if show_strings:
        cmd.append("-s")
    cmd.extend([str(rule_file), str(target)])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise PwnMcpError("timeout_or_resource_limit", "yara_timeout", f"yara exceeded {timeout}s timeout.")
    matches = [line for line in proc.stdout.splitlines() if line and not line.startswith("0x")]
    return {
        "ok": True,
        "result": {
            "target_path": str(target),
            "rule_path": str(rule_file),
            "return_code": proc.returncode,
            "matches": matches[:200],
            "stdout": proc.stdout[:20000],
            "stderr": proc.stderr[:10000],
        },
    }


def run_radare2_command(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    commands: list[str] | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Run read-only radare2 commands against a binary."""
    _require_tool(TOOL_R2, "radare2")
    app.sessions.get(session_id)
    binary = app.security.resolve_binary(binary_path)
    commands = commands or ["ij"]
    if len(commands) > 20:
        raise PwnMcpError("invalid_request", "too_many_radare2_commands", "At most 20 radare2 commands may be run at once.")
    denied = ("!", "o+", "oo+", "wx", "w ")
    for command in commands:
        stripped = command.strip()
        if any(item in stripped for item in denied):
            raise PwnMcpError("invalid_request", "radare2_command_denied", f"Denied potentially mutating radare2 command: {command}")
    timeout = timeout_seconds or DEFAULT_EXEC_TIMEOUT_SECONDS
    cmd = [TOOL_R2, "-q"]
    for command in commands:
        cmd.extend(["-c", command])
    cmd.extend(["-c", "q", str(binary)])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise PwnMcpError("timeout_or_resource_limit", "radare2_timeout", f"radare2 exceeded {timeout}s timeout.")
    return {
        "ok": True,
        "result": {
            "binary_path": str(binary),
            "commands": commands,
            "return_code": proc.returncode,
            "stdout": proc.stdout[:30000],
            "stderr": proc.stderr[:10000],
        },
    }


def register(app: "PwnMcpApp") -> dict[str, dict]:
    def _h(fn):
        def wrapper(**kwargs):
            return fn(app, **kwargs)
        return wrapper

    _sid = {"type": "string", "description": "Session ID."}
    _bp = {"type": "string", "description": "Workspace-relative binary path."}
    return {
        "run_capa": {
            "handler": _h(run_capa),
            "schema": Tool(
                name="run_capa",
                description="Run FLARE capa against a binary and return capability matches.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": _bp,
                        "output_format": {"type": "string", "enum": ["json", "text"], "default": "json"},
                        "rules_path": {"type": "string", "description": "Optional workspace-relative capa rules file or directory. Defaults to bundled /opt/capa-rules when available."},
                        "timeout_seconds": {"type": "integer", "default": 30},
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
        "run_floss": {
            "handler": _h(run_floss),
            "schema": Tool(
                name="run_floss",
                description="Run FLARE FLOSS to extract static, stack, tight, and decoded strings.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": _bp,
                        "output_format": {"type": "string", "enum": ["json", "text"], "default": "json"},
                        "analysis_types": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["all", "static", "stack", "tight", "decoded"]},
                            "default": ["static"],
                            "description": "FLOSS analysis classes. Defaults to static strings for ELF/shellcode compatibility; use ['all'] for full PE decoding.",
                        },
                        "timeout_seconds": {"type": "integer", "default": 30},
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
        "run_yara_scan": {
            "handler": _h(run_yara_scan),
            "schema": Tool(
                name="run_yara_scan",
                description="Run yara rules against a workspace file. Provide rule_source or rule_path.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "target_path": {"type": "string"},
                        "rule_source": {"type": "string"},
                        "rule_path": {"type": "string"},
                        "show_strings": {"type": "boolean", "default": True},
                        "timeout_seconds": {"type": "integer", "default": 30},
                    },
                    "required": ["session_id", "target_path"],
                    "additionalProperties": False,
                },
            ),
        },
        "run_radare2_command": {
            "handler": _h(run_radare2_command),
            "schema": Tool(
                name="run_radare2_command",
                description="Run read-only radare2 commands against a binary, such as ij, iSj, afl, or pdf @ sym.main.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": _bp,
                        "commands": {"type": "array", "items": {"type": "string"}, "default": ["ij"]},
                        "timeout_seconds": {"type": "integer", "default": 30},
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
    }
