"""
Symbolic and concolic execution helpers built on angr.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..config import DEFAULT_SCRIPT_TIMEOUT_SECONDS
from ..errors import PwnMcpError

if TYPE_CHECKING:
    from ..app import PwnMcpApp


ANGR_PREAMBLE = """\
from pathlib import Path
import json
import os
import angr
import claripy
import cle

"""


def _require_import(name: str, label: str | None = None) -> None:
    if importlib.util.find_spec(name) is None:
        raise PwnMcpError("tool_not_found", f"{label or name}_missing", f"The '{name}' Python package is not installed.")


def _parse_address(value: int | str, field: str) -> int:
    try:
        return value if isinstance(value, int) else int(str(value).strip(), 0)
    except (TypeError, ValueError) as exc:
        raise PwnMcpError("invalid_request", "invalid_address", f"{field} must be an integer or hex string.") from exc


def run_angr_script(
    app: "PwnMcpApp",
    session_id: str,
    script: str,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Run a Python script with angr, claripy, and cle pre-imported."""
    for module in ("angr", "claripy", "cle"):
        _require_import(module)
    session = app.sessions.get(session_id)
    timeout = timeout_seconds or DEFAULT_SCRIPT_TIMEOUT_SECONDS
    out_dir = app.security.output_dir(session_id, "angr")
    script_id = f"angr_{uuid.uuid4().hex[:8]}"
    script_path = out_dir / f"{script_id}.py"
    wrapped = (
        ANGR_PREAMBLE
        + f"WORKSPACE = Path({str(app.security.workspace_root)!r})\n"
        + f"OUTPUT_DIR = Path({str(out_dir)!r})\n"
        + f"SESSION_DIR = Path({str(session.session_dir)!r})\n"
        + script
        + "\n"
    )
    script_path.write_text(wrapped, encoding="utf-8")
    try:
        proc = subprocess.run(
            ["python3", str(script_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(session.session_dir),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    except subprocess.TimeoutExpired:
        raise PwnMcpError("timeout_or_resource_limit", "angr_timeout", f"angr script timed out after {timeout}s.")

    return {
        "ok": True,
        "result": {
            "script_id": script_id,
            "script_path": str(script_path),
            "return_code": proc.returncode,
            "stdout": proc.stdout[:20000],
            "stderr": proc.stderr[:10000],
        },
    }


def get_angr_project_info(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    auto_load_libs: bool = False,
    max_symbols: int = 80,
) -> dict[str, Any]:
    """Load a binary with angr and return loader/project metadata."""
    _require_import("angr")
    app.sessions.get(session_id)
    binary = app.security.resolve_binary(binary_path)

    import angr  # type: ignore[import-not-found]

    project = angr.Project(str(binary), auto_load_libs=auto_load_libs)
    obj = project.loader.main_object
    symbols = []
    for sym in obj.symbols:
        if not sym.name:
            continue
        if sym.is_function or sym.is_export:
            symbols.append({
                "name": sym.name,
                "rebased_addr": f"0x{sym.rebased_addr:x}",
                "relative_addr": f"0x{sym.relative_addr:x}",
                "is_function": bool(sym.is_function),
                "is_export": bool(sym.is_export),
            })
        if len(symbols) >= max(1, min(int(max_symbols), 500)):
            break

    return {
        "ok": True,
        "result": {
            "binary_path": str(binary),
            "arch": project.arch.name,
            "bits": project.arch.bits,
            "entry": f"0x{project.entry:x}",
            "main_object": {
                "binary": obj.binary,
                "pic": bool(getattr(obj, "pic", False)),
                "min_addr": f"0x{obj.min_addr:x}",
                "max_addr": f"0x{obj.max_addr:x}",
                "mapped_base": f"0x{obj.mapped_base:x}",
            },
            "symbols": symbols,
            "auto_load_libs": auto_load_libs,
        },
    }


def angr_find_path(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    find_address: int | str,
    avoid_addresses: list[int | str] | None = None,
    stdin_size: int = 32,
    args: list[str] | None = None,
    auto_load_libs: bool = False,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Use angr to find symbolic stdin that reaches an address while avoiding others."""
    for module in ("angr", "claripy"):
        _require_import(module)
    session = app.sessions.get(session_id)
    binary = app.security.resolve_binary(binary_path)
    timeout = timeout_seconds or DEFAULT_SCRIPT_TIMEOUT_SECONDS
    stdin_size = max(1, min(int(stdin_size), 4096))
    params = {
        "binary": str(binary),
        "find": _parse_address(find_address, "find_address"),
        "avoid": [_parse_address(item, "avoid_addresses") for item in (avoid_addresses or [])],
        "stdin_size": stdin_size,
        "args": args or [],
        "auto_load_libs": bool(auto_load_libs),
    }
    out_dir = app.security.output_dir(session_id, "angr")
    script_id = f"angr_find_{uuid.uuid4().hex[:8]}"
    script_path = out_dir / f"{script_id}.py"
    script_path.write_text(
        ANGR_PREAMBLE
        + f"PARAMS = {params!r}\n"
        + r"""
project = angr.Project(PARAMS["binary"], auto_load_libs=PARAMS["auto_load_libs"])
sym_stdin = claripy.BVS("stdin", PARAMS["stdin_size"] * 8)
stdin = angr.SimFileStream(name="stdin", content=sym_stdin, has_end=True)
state = project.factory.full_init_state(args=[PARAMS["binary"]] + PARAMS["args"], stdin=stdin)
simgr = project.factory.simulation_manager(state)
simgr.explore(find=PARAMS["find"], avoid=PARAMS["avoid"], num_find=1)
if simgr.found:
    found = simgr.found[0]
    concrete = found.solver.eval(sym_stdin, cast_to=bytes)
    stdout = found.posix.dumps(1)
    print(json.dumps({
        "found": True,
        "reached_address": hex(PARAMS["find"]),
        "stdin_hex": concrete.hex(),
        "stdin_ascii": concrete.decode("latin-1", errors="replace"),
        "stdout": stdout.decode("latin-1", errors="replace"),
    }))
else:
    print(json.dumps({
        "found": False,
        "active": len(simgr.active),
        "deadended": len(simgr.deadended),
        "errored": len(simgr.errored),
    }))
""",
        encoding="utf-8",
    )
    try:
        proc = subprocess.run(
            ["python3", str(script_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(session.session_dir),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    except subprocess.TimeoutExpired:
        raise PwnMcpError("timeout_or_resource_limit", "angr_path_timeout", f"angr path search timed out after {timeout}s.")

    parsed: dict[str, Any] | None = None
    if proc.stdout.strip():
        last_line = proc.stdout.strip().splitlines()[-1]
        try:
            parsed = json.loads(last_line)
        except json.JSONDecodeError:
            parsed = None
    return {
        "ok": True,
        "result": {
            "script_id": script_id,
            "script_path": str(script_path),
            "return_code": proc.returncode,
            "parsed": parsed,
            "stdout": proc.stdout[:20000],
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
        "run_angr_script": {
            "handler": _h(run_angr_script),
            "schema": Tool(
                name="run_angr_script",
                description="Run an angr/claripy/cle Python script. WORKSPACE, OUTPUT_DIR, and SESSION_DIR are pre-defined.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "script": {"type": "string"},
                        "timeout_seconds": {"type": "integer", "default": 30},
                    },
                    "required": ["session_id", "script"],
                    "additionalProperties": False,
                },
            ),
        },
        "get_angr_project_info": {
            "handler": _h(get_angr_project_info),
            "schema": Tool(
                name="get_angr_project_info",
                description="Load a binary with angr and report architecture, entry point, loader ranges, and symbols.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": _bp,
                        "auto_load_libs": {"type": "boolean", "default": False},
                        "max_symbols": {"type": "integer", "default": 80},
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
        "angr_find_path": {
            "handler": _h(angr_find_path),
            "schema": Tool(
                name="angr_find_path",
                description="Use angr to solve symbolic stdin that reaches a target address while avoiding optional addresses.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": _bp,
                        "find_address": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
                        "avoid_addresses": {"type": "array", "items": {"oneOf": [{"type": "string"}, {"type": "integer"}]}, "default": []},
                        "stdin_size": {"type": "integer", "default": 32},
                        "args": {"type": "array", "items": {"type": "string"}, "default": []},
                        "auto_load_libs": {"type": "boolean", "default": False},
                        "timeout_seconds": {"type": "integer", "default": 30},
                    },
                    "required": ["session_id", "binary_path", "find_address"],
                    "additionalProperties": False,
                },
            ),
        },
    }
