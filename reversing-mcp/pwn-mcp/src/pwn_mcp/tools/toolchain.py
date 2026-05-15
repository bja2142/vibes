"""
Toolchain diagnostics for installed dynamic-analysis backends.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..config import (
    TOOL_GDB,
    TOOL_RR,
    TOOL_STRACE,
    TOOL_LTRACE,
    TOOL_VALGRIND,
    TOOL_UFTRACE,
    TOOL_CHECKSEC,
    TOOL_ONE_GADGET,
    TOOL_ROPPER,
    TOOL_ROPGADGET,
    TOOL_SECCOMP_TOOLS,
    TOOL_DRRUN,
    TOOL_DRCOV,
    TOOL_CAPA,
    TOOL_FLOSS,
    TOOL_YARA,
    TOOL_R2,
    TOOL_RABIN2,
    TOOL_RASM2,
    TOOL_NASM,
    QEMU_USER_MAP,
)
from ..utils import which_tool

if TYPE_CHECKING:
    from ..app import PwnMcpApp


EXTERNAL_TOOLS: dict[str, dict[str, Any]] = {
    "gdb-multiarch": {"path": TOOL_GDB, "mcp_tools": ["start_debug_session", "send_gdb_command"]},
    "rr": {"path": TOOL_RR, "mcp_tools": ["start_rr_record", "start_rr_replay", "reverse_continue"]},
    "strace": {"path": TOOL_STRACE, "mcp_tools": ["run_with_strace"]},
    "ltrace": {"path": TOOL_LTRACE, "mcp_tools": ["run_with_ltrace"]},
    "valgrind": {"path": TOOL_VALGRIND, "mcp_tools": ["run_with_valgrind"]},
    "uftrace": {"path": TOOL_UFTRACE, "mcp_tools": ["run_with_uftrace"]},
    "frida": {"path": "frida", "mcp_tools": ["start_frida_session", "inject_script", "hook_function"]},
    "drrun": {"path": TOOL_DRRUN, "mcp_tools": ["run_with_coverage", "get_coverage_report", "diff_coverage"]},
    "drcov": {"path": TOOL_DRCOV, "mcp_tools": ["run_with_coverage"]},
    "checksec": {"path": TOOL_CHECKSEC, "mcp_tools": ["checksec"]},
    "one_gadget": {"path": TOOL_ONE_GADGET, "mcp_tools": ["find_one_gadgets"]},
    "ropper": {"path": TOOL_ROPPER, "mcp_tools": ["get_rop_gadgets"]},
    "ROPgadget": {"path": TOOL_ROPGADGET, "mcp_tools": ["get_rop_gadgets"]},
    "seccomp-tools": {"path": TOOL_SECCOMP_TOOLS, "mcp_tools": ["analyze_seccomp"]},
    "patchelf": {"path": "patchelf", "mcp_tools": ["get_elf_metadata", "patch_binary_libc"]},
    "capa": {"path": TOOL_CAPA, "mcp_tools": ["run_capa"]},
    "floss": {"path": TOOL_FLOSS, "mcp_tools": ["run_floss"]},
    "yara": {"path": TOOL_YARA, "mcp_tools": ["run_yara_scan"]},
    "r2": {"path": TOOL_R2, "mcp_tools": ["run_radare2_command"]},
    "rabin2": {"path": TOOL_RABIN2, "mcp_tools": ["run_radare2_command"]},
    "rasm2": {"path": TOOL_RASM2, "mcp_tools": ["assemble_code", "disassemble_bytes"]},
    "nasm": {"path": TOOL_NASM, "mcp_tools": ["assemble_code"]},
}

PYTHON_IMPORTS: dict[str, list[str]] = {
    "pwn": ["run_pwntools_script"],
    "frida": ["start_frida_session", "inject_script", "hook_function"],
    "z3": ["run_z3_script"],
    "boofuzz": ["run_boofuzz_script"],
    "angr": ["run_angr_script", "get_angr_project_info", "angr_find_path"],
    "claripy": ["run_angr_script", "angr_find_path"],
    "cle": ["run_angr_script", "get_angr_project_info"],
    "unicorn": ["emulate_blob_unicorn"],
    "qiling": ["run_qiling_script"],
    "capstone": ["disassemble_bytes", "disassemble_file_region"],
    "keystone": ["assemble_code"],
    "yara": ["run_yara_scan"],
}

DATA_PATHS: dict[str, dict[str, Any]] = {
    "glibc-all-in-one": {"path": "/opt/glibc-all-in-one", "mcp_tools": ["list_available_libcs", "download_libc"]},
    "libc-database": {"path": "/opt/libc-database", "mcp_tools": ["identify_libc"]},
}


def _path_available(path_or_tool: str) -> tuple[bool, str | None]:
    path = Path(path_or_tool)
    if path.is_absolute() or "/" in path_or_tool:
        return path.exists(), str(path) if path.exists() else None
    found = which_tool(path_or_tool)
    return found is not None, found


def _probe_command(path_or_tool: str, timeout: int) -> dict[str, Any]:
    commands = {
        "gdb-multiarch": [path_or_tool, "--version"],
        "rr": [path_or_tool, "--version"],
        "strace": [path_or_tool, "--version"],
        "ltrace": [path_or_tool, "--version"],
        "valgrind": [path_or_tool, "--version"],
        "uftrace": [path_or_tool, "--version"],
        "frida": [path_or_tool, "--version"],
        "checksec": [path_or_tool, "--version"],
        "one_gadget": [path_or_tool, "--version"],
        "ropper": [path_or_tool, "--version"],
        "ROPgadget": [path_or_tool, "--version"],
        "seccomp-tools": [path_or_tool, "--version"],
        "patchelf": [path_or_tool, "--version"],
        "capa": [path_or_tool, "--version"],
        "floss": [path_or_tool, "--version"],
        "yara": [path_or_tool, "--version"],
        "r2": [path_or_tool, "-v"],
        "rabin2": [path_or_tool, "-v"],
        "rasm2": [path_or_tool, "-v"],
        "nasm": [path_or_tool, "-v"],
    }
    command = commands.get(Path(path_or_tool).name, [path_or_tool, "--version"])
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": completed.returncode == 0,
        "return_code": completed.returncode,
        "stdout": completed.stdout[:1000],
        "stderr": completed.stderr[:1000],
    }


def validate_toolchain(
    app: "PwnMcpApp",
    run_probes: bool = False,
    timeout_seconds: int = 5,
) -> dict[str, Any]:
    """Report installed backend availability and the MCP tools that expose each backend."""
    external = {}
    for name, meta in EXTERNAL_TOOLS.items():
        available, resolved = _path_available(str(meta["path"]))
        record = {
            "available": available,
            "path": resolved or meta["path"],
            "mcp_tools": meta["mcp_tools"],
        }
        if run_probes and available and name != "drcov":
            record["probe"] = _probe_command(str(meta["path"]), timeout_seconds)
        external[name] = record

    qemu = {}
    for arch, tool_name in QEMU_USER_MAP.items():
        if not tool_name:
            qemu[arch] = {"available": True, "path": "native", "mcp_tools": ["launch_binary", "start_debug_session"]}
            continue
        available, resolved = _path_available(tool_name)
        qemu[arch] = {
            "available": available,
            "path": resolved or tool_name,
            "mcp_tools": ["launch_binary", "start_debug_session", "run_with_strace", "run_with_ltrace"],
        }

    python_imports = {}
    for name, mcp_tools in PYTHON_IMPORTS.items():
        available = importlib.util.find_spec(name) is not None
        python_imports[name] = {"available": available, "mcp_tools": mcp_tools}

    data_paths = {}
    for name, meta in DATA_PATHS.items():
        path = Path(str(meta["path"]))
        data_paths[name] = {"available": path.exists(), "path": str(path), "mcp_tools": meta["mcp_tools"]}

    missing_required = [
        name
        for name, record in {**external, **python_imports}.items()
        if not record["available"] and name not in {"rr"}
    ]

    return {
        "ok": True,
        "result": {
            "external_tools": external,
            "python_imports": python_imports,
            "qemu_user": qemu,
            "data_paths": data_paths,
            "missing_required": sorted(missing_required),
            "notes": [
                "AFL++ is intentionally not part of this CTF harness.",
                "rr is optional because it depends on host perf_event_paranoid and architecture support.",
                "Each listed backend maps to one or more MCP tools in mcp_tools.",
            ],
        },
    }


def register(app: "PwnMcpApp") -> dict[str, dict]:
    def _h(fn):
        def wrapper(**kwargs):
            return fn(app, **kwargs)
        return wrapper

    return {
        "validate_toolchain": {
            "handler": _h(validate_toolchain),
            "schema": Tool(
                name="validate_toolchain",
                description="Report installed backend availability and the MCP tools that expose each backend.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "run_probes": {"type": "boolean", "default": False, "description": "Run lightweight version probes for CLI tools."},
                        "timeout_seconds": {"type": "integer", "default": 5, "description": "Per-probe timeout in seconds."},
                    },
                    "additionalProperties": False,
                },
            ),
        },
    }
