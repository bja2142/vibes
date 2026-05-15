"""
CPU emulation helpers using Unicorn and Qiling.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..config import DEFAULT_SCRIPT_TIMEOUT_SECONDS
from ..errors import PwnMcpError

if TYPE_CHECKING:
    from ..app import PwnMcpApp


QILING_PREAMBLE = """\
from pathlib import Path
import os
from qiling import Qiling

"""


def _require_import(name: str, label: str | None = None) -> None:
    if importlib.util.find_spec(name) is None:
        raise PwnMcpError("tool_not_found", f"{label or name}_missing", f"The '{name}' Python package is not installed.")


def _clean_hex(value: str) -> bytes:
    cleaned = re.sub(r"[^0-9a-fA-F]", "", value or "")
    if len(cleaned) % 2:
        raise PwnMcpError("invalid_request", "invalid_hex", "Hex input must contain an even number of hex digits.")
    return bytes.fromhex(cleaned)


def _unicorn_arch(arch: str):
    from unicorn import UC_ARCH_ARM, UC_ARCH_ARM64, UC_ARCH_MIPS, UC_ARCH_X86
    from unicorn import UC_MODE_32, UC_MODE_64, UC_MODE_ARM, UC_MODE_BIG_ENDIAN, UC_MODE_LITTLE_ENDIAN, UC_MODE_MIPS32, UC_MODE_THUMB

    normalized = arch.lower().replace("-", "_")
    if normalized in {"x86_64", "amd64"}:
        return UC_ARCH_X86, UC_MODE_64, "x86_64"
    if normalized in {"x86", "i386", "i686"}:
        return UC_ARCH_X86, UC_MODE_32, "x86"
    if normalized == "arm":
        return UC_ARCH_ARM, UC_MODE_ARM, "arm"
    if normalized in {"arm_thumb", "thumb"}:
        return UC_ARCH_ARM, UC_MODE_THUMB, "arm"
    if normalized in {"aarch64", "arm64"}:
        return UC_ARCH_ARM64, UC_MODE_ARM, "aarch64"
    if normalized == "mips":
        return UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_BIG_ENDIAN, "mips"
    if normalized == "mipsel":
        return UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN, "mipsel"
    raise PwnMcpError("invalid_request", "unsupported_emulation_arch", f"Unsupported Unicorn architecture: {arch}")


def _register_map(canonical_arch: str) -> dict[str, int]:
    if canonical_arch in {"x86_64", "x86"}:
        from unicorn.x86_const import (
            UC_X86_REG_EAX, UC_X86_REG_EBP, UC_X86_REG_EBX, UC_X86_REG_ECX,
            UC_X86_REG_EDI, UC_X86_REG_EDX, UC_X86_REG_EIP, UC_X86_REG_ESI,
            UC_X86_REG_ESP, UC_X86_REG_RAX, UC_X86_REG_RBP, UC_X86_REG_RBX,
            UC_X86_REG_RCX, UC_X86_REG_RDI, UC_X86_REG_RDX, UC_X86_REG_RIP,
            UC_X86_REG_RSI, UC_X86_REG_RSP,
        )
        return {
            "rax": UC_X86_REG_RAX, "rbx": UC_X86_REG_RBX, "rcx": UC_X86_REG_RCX,
            "rdx": UC_X86_REG_RDX, "rsi": UC_X86_REG_RSI, "rdi": UC_X86_REG_RDI,
            "rbp": UC_X86_REG_RBP, "rsp": UC_X86_REG_RSP, "rip": UC_X86_REG_RIP,
            "eax": UC_X86_REG_EAX, "ebx": UC_X86_REG_EBX, "ecx": UC_X86_REG_ECX,
            "edx": UC_X86_REG_EDX, "esi": UC_X86_REG_ESI, "edi": UC_X86_REG_EDI,
            "ebp": UC_X86_REG_EBP, "esp": UC_X86_REG_ESP, "eip": UC_X86_REG_EIP,
        }
    if canonical_arch == "aarch64":
        from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3
        return {"x0": UC_ARM64_REG_X0, "x1": UC_ARM64_REG_X1, "x2": UC_ARM64_REG_X2, "x3": UC_ARM64_REG_X3, "sp": UC_ARM64_REG_SP, "pc": UC_ARM64_REG_PC}
    if canonical_arch == "arm":
        from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP
        return {"r0": UC_ARM_REG_R0, "r1": UC_ARM_REG_R1, "r2": UC_ARM_REG_R2, "r3": UC_ARM_REG_R3, "sp": UC_ARM_REG_SP, "pc": UC_ARM_REG_PC}
    if canonical_arch in {"mips", "mipsel"}:
        from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3, UC_MIPS_REG_PC, UC_MIPS_REG_SP, UC_MIPS_REG_V0
        return {"a0": UC_MIPS_REG_A0, "a1": UC_MIPS_REG_A1, "a2": UC_MIPS_REG_A2, "a3": UC_MIPS_REG_A3, "v0": UC_MIPS_REG_V0, "sp": UC_MIPS_REG_SP, "pc": UC_MIPS_REG_PC}
    return {}


def emulate_blob_unicorn(
    app: "PwnMcpApp",
    session_id: str,
    arch: str,
    code_hex: str,
    start_address: int | str = 0x100000,
    memory_size: int = 0x20000,
    registers: dict[str, int | str] | None = None,
    max_instructions: int = 1000,
) -> dict[str, Any]:
    """Emulate a raw instruction blob with Unicorn and return register state."""
    _require_import("unicorn")
    app.sessions.get(session_id)
    from unicorn import Uc, UC_HOOK_CODE, UcError

    code = _clean_hex(code_hex)
    start = start_address if isinstance(start_address, int) else int(str(start_address), 0)
    memory_size = max(0x1000, min(int(memory_size), 0x1000000))
    page_base = start & ~0xfff
    arch_const, mode_const, canonical = _unicorn_arch(arch)
    reg_map = _register_map(canonical)
    uc = Uc(arch_const, mode_const)
    uc.mem_map(page_base, memory_size)
    uc.mem_write(start, code)

    for name, value in (registers or {}).items():
        reg = reg_map.get(name.lower())
        if reg is None:
            raise PwnMcpError("invalid_request", "unknown_register", f"Unknown register for {canonical}: {name}")
        uc.reg_write(reg, value if isinstance(value, int) else int(str(value), 0))

    executed: list[dict[str, str | int]] = []

    def _hook_code(_uc, address, size, _user_data):
        if len(executed) < 200:
            executed.append({"address": f"0x{address:x}", "size": int(size)})

    uc.hook_add(UC_HOOK_CODE, _hook_code)
    error = None
    try:
        uc.emu_start(start, start + len(code), count=max(1, min(int(max_instructions), 100000)))
    except UcError as exc:
        error = str(exc)

    reg_state = {}
    for name, reg in reg_map.items():
        try:
            reg_state[name] = f"0x{uc.reg_read(reg):x}"
        except Exception:
            pass

    return {
        "ok": True,
        "result": {
            "arch": canonical,
            "start_address": f"0x{start:x}",
            "code_size": len(code),
            "registers": reg_state,
            "executed": executed,
            "execution_error": error,
        },
    }


def run_qiling_script(
    app: "PwnMcpApp",
    session_id: str,
    script: str,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Run a Python script with Qiling pre-imported."""
    _require_import("qiling")
    session = app.sessions.get(session_id)
    timeout = timeout_seconds or DEFAULT_SCRIPT_TIMEOUT_SECONDS
    out_dir = app.security.output_dir(session_id, "qiling")
    script_id = f"qiling_{uuid.uuid4().hex[:8]}"
    script_path = out_dir / f"{script_id}.py"
    wrapped = (
        QILING_PREAMBLE
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
        raise PwnMcpError("timeout_or_resource_limit", "qiling_timeout", f"Qiling script timed out after {timeout}s.")

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


def register(app: "PwnMcpApp") -> dict[str, dict]:
    def _h(fn):
        def wrapper(**kwargs):
            return fn(app, **kwargs)
        return wrapper

    _sid = {"type": "string", "description": "Session ID."}
    return {
        "emulate_blob_unicorn": {
            "handler": _h(emulate_blob_unicorn),
            "schema": Tool(
                name="emulate_blob_unicorn",
                description="Emulate a raw instruction blob with Unicorn and return register state plus executed addresses.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "arch": {"type": "string", "enum": ["x86", "x86_64", "arm", "arm_thumb", "aarch64", "mips", "mipsel"]},
                        "code_hex": {"type": "string"},
                        "start_address": {"oneOf": [{"type": "string"}, {"type": "integer"}], "default": "0x100000"},
                        "memory_size": {"type": "integer", "default": 131072},
                        "registers": {"type": "object", "additionalProperties": {"oneOf": [{"type": "string"}, {"type": "integer"}]}, "default": {}},
                        "max_instructions": {"type": "integer", "default": 1000},
                    },
                    "required": ["session_id", "arch", "code_hex"],
                    "additionalProperties": False,
                },
            ),
        },
        "run_qiling_script": {
            "handler": _h(run_qiling_script),
            "schema": Tool(
                name="run_qiling_script",
                description="Run a Qiling Python script. WORKSPACE, OUTPUT_DIR, and SESSION_DIR are pre-defined.",
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
    }
