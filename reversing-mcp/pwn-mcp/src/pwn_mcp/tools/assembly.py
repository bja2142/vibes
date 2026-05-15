"""
Assembly and disassembly helpers using Keystone and Capstone.
"""
from __future__ import annotations

import importlib.util
import re
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..errors import PwnMcpError
from ..utils import detect_arch

if TYPE_CHECKING:
    from ..app import PwnMcpApp


def _require_import(name: str, label: str | None = None) -> None:
    if importlib.util.find_spec(name) is None:
        raise PwnMcpError("tool_not_found", f"{label or name}_missing", f"The '{name}' Python package is not installed.")


def _clean_hex(value: str) -> bytes:
    cleaned = re.sub(r"[^0-9a-fA-F]", "", value or "")
    if len(cleaned) % 2:
        raise PwnMcpError("invalid_request", "invalid_hex", "Hex input must contain an even number of hex digits.")
    return bytes.fromhex(cleaned)


def _ks_arch(arch: str):
    from keystone import KS_ARCH_ARM, KS_ARCH_ARM64, KS_ARCH_MIPS, KS_ARCH_X86
    from keystone import KS_MODE_32, KS_MODE_64, KS_MODE_ARM, KS_MODE_BIG_ENDIAN, KS_MODE_LITTLE_ENDIAN, KS_MODE_MIPS32, KS_MODE_THUMB

    normalized = arch.lower().replace("-", "_")
    if normalized in {"x86_64", "amd64"}:
        return KS_ARCH_X86, KS_MODE_64, "x86_64"
    if normalized in {"x86", "i386", "i686"}:
        return KS_ARCH_X86, KS_MODE_32, "x86"
    if normalized == "arm":
        return KS_ARCH_ARM, KS_MODE_ARM, "arm"
    if normalized in {"thumb", "arm_thumb"}:
        return KS_ARCH_ARM, KS_MODE_THUMB, "arm_thumb"
    if normalized in {"aarch64", "arm64"}:
        return KS_ARCH_ARM64, 0, "aarch64"
    if normalized == "mips":
        return KS_ARCH_MIPS, KS_MODE_MIPS32 | KS_MODE_BIG_ENDIAN, "mips"
    if normalized == "mipsel":
        return KS_ARCH_MIPS, KS_MODE_MIPS32 | KS_MODE_LITTLE_ENDIAN, "mipsel"
    raise PwnMcpError("invalid_request", "unsupported_assembly_arch", f"Unsupported Keystone architecture: {arch}")


def _cs_arch(arch: str):
    from capstone import CS_ARCH_ARM, CS_ARCH_ARM64, CS_ARCH_MIPS, CS_ARCH_X86
    from capstone import CS_MODE_32, CS_MODE_64, CS_MODE_ARM, CS_MODE_BIG_ENDIAN, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS32, CS_MODE_THUMB

    normalized = arch.lower().replace("-", "_")
    if normalized in {"x86_64", "amd64"}:
        return CS_ARCH_X86, CS_MODE_64, "x86_64"
    if normalized in {"x86", "i386", "i686"}:
        return CS_ARCH_X86, CS_MODE_32, "x86"
    if normalized == "arm":
        return CS_ARCH_ARM, CS_MODE_ARM, "arm"
    if normalized in {"thumb", "arm_thumb"}:
        return CS_ARCH_ARM, CS_MODE_THUMB, "arm_thumb"
    if normalized in {"aarch64", "arm64"}:
        return CS_ARCH_ARM64, 0, "aarch64"
    if normalized == "mips":
        return CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_BIG_ENDIAN, "mips"
    if normalized == "mipsel":
        return CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN, "mipsel"
    raise PwnMcpError("invalid_request", "unsupported_disassembly_arch", f"Unsupported Capstone architecture: {arch}")


def assemble_code(
    app: "PwnMcpApp",
    session_id: str,
    assembly: str,
    arch: str = "x86_64",
    address: int | str = 0,
    syntax: str = "intel",
) -> dict[str, Any]:
    """Assemble instructions to bytes using Keystone."""
    _require_import("keystone")
    app.sessions.get(session_id)
    from keystone import Ks, KS_OPT_SYNTAX_ATT, KS_OPT_SYNTAX_INTEL

    ks_arch, ks_mode, canonical = _ks_arch(arch)
    ks = Ks(ks_arch, ks_mode)
    if canonical in {"x86", "x86_64"}:
        ks.syntax = KS_OPT_SYNTAX_ATT if syntax == "att" else KS_OPT_SYNTAX_INTEL
    base = address if isinstance(address, int) else int(str(address), 0)
    try:
        encoding, count = ks.asm(assembly, addr=base)
    except Exception as exc:
        raise PwnMcpError("backend_failure", "assembly_failed", str(exc)) from exc
    data = bytes(encoding or [])
    return {
        "ok": True,
        "result": {
            "arch": canonical,
            "address": f"0x{base:x}",
            "instruction_count": count,
            "bytes_hex": data.hex(),
            "bytes": list(data),
        },
    }


def disassemble_bytes(
    app: "PwnMcpApp",
    session_id: str,
    code_hex: str,
    arch: str = "x86_64",
    address: int | str = 0,
    syntax: str = "intel",
    max_instructions: int = 200,
) -> dict[str, Any]:
    """Disassemble hex bytes using Capstone."""
    _require_import("capstone")
    app.sessions.get(session_id)
    from capstone import Cs, CS_OPT_SYNTAX_ATT, CS_OPT_SYNTAX_INTEL

    code = _clean_hex(code_hex)
    cs_arch, cs_mode, canonical = _cs_arch(arch)
    md = Cs(cs_arch, cs_mode)
    md.detail = True
    if canonical in {"x86", "x86_64"}:
        md.syntax = CS_OPT_SYNTAX_ATT if syntax == "att" else CS_OPT_SYNTAX_INTEL
    base = address if isinstance(address, int) else int(str(address), 0)
    limit = max(1, min(int(max_instructions), 5000))
    instructions = []
    for insn in md.disasm(code, base):
        instructions.append({
            "address": f"0x{insn.address:x}",
            "size": insn.size,
            "bytes_hex": insn.bytes.hex(),
            "mnemonic": insn.mnemonic,
            "op_str": insn.op_str,
        })
        if len(instructions) >= limit:
            break
    return {"ok": True, "result": {"arch": canonical, "instruction_count": len(instructions), "instructions": instructions}}


def disassemble_file_region(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    offset: int = 0,
    length: int = 256,
    arch: str | None = None,
    address: int | str | None = None,
    syntax: str = "intel",
    max_instructions: int = 200,
) -> dict[str, Any]:
    """Disassemble a byte range from a workspace file."""
    binary = app.security.resolve_binary(binary_path)
    file_size = binary.stat().st_size
    offset = max(0, min(int(offset), file_size))
    length = max(1, min(int(length), 1024 * 1024))
    with binary.open("rb") as handle:
        handle.seek(offset)
        data = handle.read(length)
    resolved_arch = arch or detect_arch(binary)
    base = address if address is not None else offset
    result = disassemble_bytes(app, session_id, data.hex(), resolved_arch, base, syntax, max_instructions)
    result["result"]["binary_path"] = str(binary)
    result["result"]["offset"] = offset
    result["result"]["length"] = len(data)
    return result


def register(app: "PwnMcpApp") -> dict[str, dict]:
    def _h(fn):
        def wrapper(**kwargs):
            return fn(app, **kwargs)
        return wrapper

    _sid = {"type": "string", "description": "Session ID."}
    arch_enum = ["x86", "x86_64", "arm", "arm_thumb", "aarch64", "mips", "mipsel"]
    return {
        "assemble_code": {
            "handler": _h(assemble_code),
            "schema": Tool(
                name="assemble_code",
                description="Assemble instructions to bytes using Keystone.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "assembly": {"type": "string"},
                        "arch": {"type": "string", "enum": arch_enum, "default": "x86_64"},
                        "address": {"oneOf": [{"type": "string"}, {"type": "integer"}], "default": 0},
                        "syntax": {"type": "string", "enum": ["intel", "att"], "default": "intel"},
                    },
                    "required": ["session_id", "assembly"],
                    "additionalProperties": False,
                },
            ),
        },
        "disassemble_bytes": {
            "handler": _h(disassemble_bytes),
            "schema": Tool(
                name="disassemble_bytes",
                description="Disassemble hex bytes using Capstone.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "code_hex": {"type": "string"},
                        "arch": {"type": "string", "enum": arch_enum, "default": "x86_64"},
                        "address": {"oneOf": [{"type": "string"}, {"type": "integer"}], "default": 0},
                        "syntax": {"type": "string", "enum": ["intel", "att"], "default": "intel"},
                        "max_instructions": {"type": "integer", "default": 200},
                    },
                    "required": ["session_id", "code_hex"],
                    "additionalProperties": False,
                },
            ),
        },
        "disassemble_file_region": {
            "handler": _h(disassemble_file_region),
            "schema": Tool(
                name="disassemble_file_region",
                description="Disassemble a byte range from a workspace file using Capstone.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": {"type": "string"},
                        "offset": {"type": "integer", "default": 0},
                        "length": {"type": "integer", "default": 256},
                        "arch": {"type": "string", "enum": arch_enum},
                        "address": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
                        "syntax": {"type": "string", "enum": ["intel", "att"], "default": "intel"},
                        "max_instructions": {"type": "integer", "default": 200},
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
    }
