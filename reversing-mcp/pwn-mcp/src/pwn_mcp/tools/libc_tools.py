"""
Libc identification, version management, and ELF patching tools.

Uses:
- libc-database (/opt/libc-database) for leak-based libc identification
- glibc-all-in-one (/opt/glibc-all-in-one) for downloading specific libc versions
- patchelf for modifying ELF interpreter and rpath
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..config import DEFAULT_EXEC_TIMEOUT_SECONDS
from ..errors import PwnMcpError
from ..utils import which_tool

if TYPE_CHECKING:
    from ..app import PwnMcpApp

LIBC_DB_PATH = Path(os.environ.get("PWN_MCP_LIBC_DB", "/opt/libc-database"))
GLIBC_AIO_PATH = Path(os.environ.get("PWN_MCP_GLIBC_AIO", "/opt/glibc-all-in-one"))


def _require_tool(name: str, label: str) -> str:
    path = which_tool(name)
    if not path:
        raise PwnMcpError("tool_not_found", f"{label}_missing", f"'{name}' not installed.")
    return path


# ── Tool handlers ─────────────────────────────────────────────────────────────

def identify_libc(
    app: "PwnMcpApp",
    session_id: str,
    function_name: str,
    leaked_address: str,
) -> dict[str, Any]:
    """Identify libc version from a leaked function address."""
    session = app.sessions.get(session_id)
    if session is None:
        raise PwnMcpError("not_found", "session_not_found", f"Session '{session_id}' not found.")

    find_script = LIBC_DB_PATH / "find"
    if not find_script.exists():
        raise PwnMcpError("missing_prerequisite", "libc_database_missing", f"libc-database not found at {LIBC_DB_PATH}")

    addr = leaked_address.strip()
    if not addr.startswith("0x"):
        addr = "0x" + addr

    try:
        result = subprocess.run(
            [str(find_script), function_name, addr],
            capture_output=True, text=True,
            timeout=DEFAULT_EXEC_TIMEOUT_SECONDS,
            cwd=str(LIBC_DB_PATH),
        )
    except subprocess.TimeoutExpired:
        raise PwnMcpError("timeout_or_resource_limit", "libc_identify_timeout", "libc identification timed out.")

    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    matches = []
    for line in lines:
        if line.startswith("http") or line.startswith("/") or "(" in line:
            matches.append(line)
        elif line and not line.startswith("No"):
            matches.append(line)

    return {
        "ok": True,
        "result": {
            "query": f"{function_name} {addr}",
            "matches": matches,
            "raw_output": result.stdout[:5000],
            "match_count": len(matches),
        },
    }


def list_available_libcs(
    app: "PwnMcpApp",
    session_id: str,
) -> dict[str, Any]:
    """List libc versions available for download via glibc-all-in-one."""
    session = app.sessions.get(session_id)
    if session is None:
        raise PwnMcpError("not_found", "session_not_found", f"Session '{session_id}' not found.")

    list_file = GLIBC_AIO_PATH / "list"
    if not list_file.exists():
        raise PwnMcpError("missing_prerequisite", "glibc_aio_missing", f"glibc-all-in-one not found at {GLIBC_AIO_PATH}")

    try:
        result = subprocess.run(
            ["cat", str(list_file)],
            capture_output=True, text=True, timeout=5,
        )
    except subprocess.TimeoutExpired:
        raise PwnMcpError("timeout_or_resource_limit", "list_timeout", "Listing timed out.")

    versions = [l.strip() for l in result.stdout.splitlines() if l.strip()]

    # Also check what's already downloaded
    libs_dir = GLIBC_AIO_PATH / "libs"
    downloaded = []
    if libs_dir.exists():
        downloaded = [d.name for d in libs_dir.iterdir() if d.is_dir()]

    return {
        "ok": True,
        "result": {
            "available": versions,
            "available_count": len(versions),
            "downloaded": downloaded,
            "downloaded_count": len(downloaded),
        },
    }


def download_libc(
    app: "PwnMcpApp",
    session_id: str,
    version: str,
) -> dict[str, Any]:
    """Download a specific libc version using glibc-all-in-one."""
    session = app.sessions.get(session_id)
    if session is None:
        raise PwnMcpError("not_found", "session_not_found", f"Session '{session_id}' not found.")

    download_script = GLIBC_AIO_PATH / "download"
    if not download_script.exists():
        raise PwnMcpError("missing_prerequisite", "glibc_aio_missing", f"glibc-all-in-one not found at {GLIBC_AIO_PATH}")

    try:
        result = subprocess.run(
            [str(download_script), version],
            capture_output=True, text=True,
            timeout=120,
            cwd=str(GLIBC_AIO_PATH),
        )
    except subprocess.TimeoutExpired:
        raise PwnMcpError("timeout_or_resource_limit", "download_timeout", "libc download timed out.")

    lib_path = GLIBC_AIO_PATH / "libs" / version
    success = lib_path.exists()

    return {
        "ok": success,
        "result": {
            "version": version,
            "path": str(lib_path) if success else None,
            "stdout": result.stdout[:2000],
            "stderr": result.stderr[:2000] if not success else "",
        },
    }


def patch_binary_libc(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    interpreter: str | None = None,
    rpath: str | None = None,
) -> dict[str, Any]:
    """Use patchelf to set the ELF interpreter and/or rpath on a binary."""
    _require_tool("patchelf", "patchelf")
    session = app.sessions.get(session_id)
    if session is None:
        raise PwnMcpError("not_found", "session_not_found", f"Session '{session_id}' not found.")

    binary = app.security.resolve_binary(binary_path)

    if not interpreter and not rpath:
        raise PwnMcpError("invalid_request", "no_patch_specified", "Provide at least one of interpreter or rpath.")

    results = {}
    if interpreter:
        r = subprocess.run(
            ["patchelf", "--set-interpreter", interpreter, str(binary)],
            capture_output=True, text=True, timeout=10,
        )
        results["interpreter"] = {"set_to": interpreter, "ok": r.returncode == 0, "error": r.stderr.strip() if r.returncode != 0 else None}

    if rpath:
        r = subprocess.run(
            ["patchelf", "--set-rpath", rpath, str(binary)],
            capture_output=True, text=True, timeout=10,
        )
        results["rpath"] = {"set_to": rpath, "ok": r.returncode == 0, "error": r.stderr.strip() if r.returncode != 0 else None}

    return {"ok": True, "result": results}


def get_elf_metadata(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
) -> dict[str, Any]:
    """Get current ELF interpreter, rpath, and needed libraries via patchelf."""
    _require_tool("patchelf", "patchelf")
    session = app.sessions.get(session_id)
    if session is None:
        raise PwnMcpError("not_found", "session_not_found", f"Session '{session_id}' not found.")

    binary = app.security.resolve_binary(binary_path)
    info = {}

    for flag, key in [("--print-interpreter", "interpreter"), ("--print-rpath", "rpath"), ("--print-needed", "needed")]:
        r = subprocess.run(
            ["patchelf", flag, str(binary)],
            capture_output=True, text=True, timeout=5,
        )
        if key == "needed":
            info[key] = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        else:
            info[key] = r.stdout.strip() if r.returncode == 0 else None

    return {"ok": True, "result": info}


# ── Registration ──────────────────────────────────────────────────────────────

def register(app: "PwnMcpApp") -> dict[str, dict]:
    def _h(fn):
        def wrapper(**kwargs):
            return fn(app, **kwargs)
        return wrapper

    _sid = {"type": "string", "description": "Session ID."}
    _bp = {"type": "string", "description": "Path to binary."}

    return {
        "identify_libc": {
            "handler": _h(identify_libc),
            "schema": Tool(
                name="identify_libc",
                description="Identify libc version from a leaked function address (e.g. puts=0x7f...). Uses libc-database.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "function_name": {"type": "string", "description": "Leaked function name (e.g. 'puts', 'system', 'printf')."},
                        "leaked_address": {"type": "string", "description": "Leaked address in hex (e.g. '0x7f1234567890')."},
                    },
                    "required": ["session_id", "function_name", "leaked_address"],
                    "additionalProperties": False,
                },
            ),
        },
        "list_available_libcs": {
            "handler": _h(list_available_libcs),
            "schema": Tool(
                name="list_available_libcs",
                description="List libc versions available for download and already downloaded via glibc-all-in-one.",
                inputSchema={
                    "type": "object",
                    "properties": {"session_id": _sid},
                    "required": ["session_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "download_libc": {
            "handler": _h(download_libc),
            "schema": Tool(
                name="download_libc",
                description="Download a specific libc version using glibc-all-in-one.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "version": {"type": "string", "description": "Libc version string from list_available_libcs."},
                    },
                    "required": ["session_id", "version"],
                    "additionalProperties": False,
                },
            ),
        },
        "patch_binary_libc": {
            "handler": _h(patch_binary_libc),
            "schema": Tool(
                name="patch_binary_libc",
                description="Use patchelf to set the ELF interpreter and/or rpath on a binary for libc version swapping.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": _bp,
                        "interpreter": {"type": "string", "description": "New ELF interpreter path (e.g. /opt/glibc-all-in-one/libs/2.31-0ubuntu9_amd64/ld-linux-x86-64.so.2)."},
                        "rpath": {"type": "string", "description": "New rpath (e.g. /opt/glibc-all-in-one/libs/2.31-0ubuntu9_amd64/)."},
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
        "get_elf_metadata": {
            "handler": _h(get_elf_metadata),
            "schema": Tool(
                name="get_elf_metadata",
                description="Get current ELF interpreter, rpath, and needed libraries for a binary using patchelf.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": _bp,
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
    }
