"""
Cross-server bridge: import static analysis manifests from reversing-mcp.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..errors import PwnMcpError

if TYPE_CHECKING:
    from ..app import PwnMcpApp


# ── Tool handlers ─────────────────────────────────────────────────────────────

def _resolve_manifest_path(app: "PwnMcpApp", manifest_path: str) -> Path:
    target = Path(manifest_path)
    if not target.is_absolute():
        target = app.security.workspace_root / manifest_path
    resolved = target.resolve()
    app.security._require_within(resolved, app.security.workspace_root, "Manifest")
    if not resolved.exists():
        raise PwnMcpError("not_found", "manifest_not_found", f"Manifest file not found: {resolved}")
    if not resolved.is_file():
        raise PwnMcpError(
            "invalid_request",
            "manifest_path_not_regular_file",
            f"Manifest path '{resolved}' is not a regular file.",
            details={"path": str(resolved)},
        )
    return resolved

def import_static_analysis(
    app: "PwnMcpApp",
    session_id: str,
    manifest_path: str,
) -> dict[str, Any]:
    """Import a static analysis manifest exported by reversing-mcp."""
    session = app.sessions.get(session_id)
    if session is None:
        raise PwnMcpError("not_found", "session_not_found", f"Session '{session_id}' not found.")

    target = _resolve_manifest_path(app, manifest_path)

    try:
        manifest = json.loads(target.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise PwnMcpError("invalid_request", "manifest_parse_error", f"Failed to parse manifest: {exc}")

    if manifest.get("schema_version") != 1 or manifest.get("source") != "reversing-mcp":
        raise PwnMcpError("invalid_request", "manifest_invalid", "Not a valid reversing-mcp manifest (check schema_version and source).")

    return {
        "ok": True,
        "result": {
            "binary": manifest.get("binary"),
            "architecture": manifest.get("architecture"),
            "bitness": manifest.get("bitness"),
            "endianness": manifest.get("endianness"),
            "entry_point": manifest.get("entry_point"),
            "image_base": manifest.get("image_base"),
            "function_count": manifest.get("function_count", 0),
            "string_count": manifest.get("string_count", 0),
            "import_count": manifest.get("import_count", 0),
            "functions": manifest.get("functions", []),
            "strings": manifest.get("strings", [])[:500],
            "imports": manifest.get("imports", []),
        },
    }


def auto_set_breakpoints(
    app: "PwnMcpApp",
    session_id: str,
    manifest_path: str,
    debug_id: str,
    filter_names: list[str] | None = None,
) -> dict[str, Any]:
    """Import a manifest and auto-set GDB breakpoints on all (or filtered) functions."""
    session = app.sessions.get(session_id)
    if session is None:
        raise PwnMcpError("not_found", "session_not_found", f"Session '{session_id}' not found.")

    debug_session = session.debug_sessions.get(debug_id)
    if debug_session is None:
        raise PwnMcpError("not_found", "debug_session_not_found", f"Debug session '{debug_id}' not found.")

    target = _resolve_manifest_path(app, manifest_path)

    manifest = json.loads(target.read_text())
    functions = manifest.get("functions", [])

    if filter_names:
        lower_names = {n.lower() for n in filter_names}
        functions = [f for f in functions if f["name"].lower() in lower_names]

    # Use the GDB MI client to set breakpoints
    mi = getattr(debug_session, "_mi", None)
    if mi is None:
        raise PwnMcpError("invalid_request", "no_gdb_session", "Debug session has no active GDB connection.")

    results = []
    for func in functions:
        addr = func.get("address", func.get("address_int"))
        try:
            resp = mi.send(f"-break-insert *{addr}")
            results.append({"name": func["name"], "address": str(addr), "ok": True})
        except Exception as exc:
            results.append({"name": func["name"], "address": str(addr), "ok": False, "error": str(exc)})

    return {
        "ok": True,
        "result": {
            "breakpoints_set": sum(1 for r in results if r["ok"]),
            "breakpoints_failed": sum(1 for r in results if not r["ok"]),
            "details": results,
        },
    }


# ── Registration ──────────────────────────────────────────────────────────────

def register(app: "PwnMcpApp") -> dict[str, dict]:
    def _h(fn):
        def wrapper(**kwargs):
            return fn(app, **kwargs)
        return wrapper

    _sid = {"type": "string", "description": "Session ID."}

    return {
        "import_static_analysis": {
            "handler": _h(import_static_analysis),
            "schema": Tool(
                name="import_static_analysis",
                description="Import a static analysis manifest exported by reversing-mcp. Returns functions, strings, imports with addresses.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "manifest_path": {"type": "string", "description": "Path to the .manifest.json file (absolute or relative to workspace)."},
                    },
                    "required": ["session_id", "manifest_path"],
                    "additionalProperties": False,
                },
            ),
        },
        "auto_set_breakpoints": {
            "handler": _h(auto_set_breakpoints),
            "schema": Tool(
                name="auto_set_breakpoints",
                description="Import a reversing-mcp manifest and auto-set GDB breakpoints on all (or filtered) functions in an active debug session.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "manifest_path": {"type": "string", "description": "Path to the .manifest.json file."},
                        "debug_id": {"type": "string", "description": "Active debug session ID."},
                        "filter_names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of function names to filter. If omitted, sets breakpoints on all functions.",
                        },
                    },
                    "required": ["session_id", "manifest_path", "debug_id"],
                    "additionalProperties": False,
                },
            ),
        },
    }
