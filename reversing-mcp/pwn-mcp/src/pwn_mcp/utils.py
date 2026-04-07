from __future__ import annotations

import os
import shutil
import struct
from pathlib import Path
from typing import Any


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def which_tool(name: str) -> str | None:
    """Return full path to tool or None if not found."""
    return shutil.which(name)


def detect_arch(path: Path) -> str:
    """
    Detect ELF architecture from the binary header.
    Returns a string matching QEMU_USER_MAP keys, or 'unknown'.
    """
    try:
        with open(path, "rb") as f:
            magic = f.read(5)
            if magic[:4] != b"\x7fELF":
                return "unknown"
            bits = magic[4]  # 1 = 32-bit, 2 = 64-bit
            f.seek(18)
            machine_bytes = f.read(2)
            # ELF is little-endian for most arches; check EI_DATA at offset 5
            f.seek(5)
            ei_data = f.read(1)[0]  # 1 = LE, 2 = BE
            endian = "<" if ei_data == 1 else ">"
            machine = struct.unpack(f"{endian}H", machine_bytes)[0]
    except (OSError, struct.error):
        return "unknown"

    # ELF e_machine values
    _MAP: dict[int, dict[int, str]] = {
        0x03: {32: "x86",    64: "x86"},
        0x3E: {32: "x86_64", 64: "x86_64"},
        0x28: {32: "arm",    64: "arm"},
        0xB7: {32: "aarch64",64: "aarch64"},
        0x08: {32: "mips",   64: "mips"},       # big-endian MIPS
        0x0A: {32: "mips",   64: "mips"},       # little-endian MIPS (MIPS RS3000 LE)
        0x15: {32: "ppc",    64: "ppc"},
        0x15 + 1: {64: "ppc64"},                # ppc64 (0x15 is PPC, 0x15 used for both)
        0xF3: {32: "riscv32",64: "riscv64"},
        0x02: {32: "sparc",  64: "sparc"},
        0x2B: {64: "sparc64"},
        0x16: {32: "s390x",  64: "s390x"},
        0x04: {32: "m68k",   64: "m68k"},
        0x2A: {32: "sh4",    64: "sh4"},
        0x5E: {32: "xtensa", 64: "xtensa"},
        0x9026: {64: "alpha"},
    }
    bit_width = 64 if bits == 2 else 32
    entry = _MAP.get(machine, {})
    arch = entry.get(bit_width) or entry.get(32) or entry.get(64)
    if arch is None:
        return "unknown"

    # Refine mips endianness
    if arch == "mips":
        if endian == "<":
            arch = "mipsel" if bit_width == 32 else "mips64el"
        else:
            arch = "mips" if bit_width == 32 else "mips64"
    return arch


def is_native_arch(arch: str) -> bool:
    """Return True if arch runs natively on this host (x86_64 assumed)."""
    return arch in ("x86_64", "x86")


def truncate_output(data: bytes, max_bytes: int) -> tuple[bytes, bool]:
    if len(data) <= max_bytes:
        return data, False
    return data[:max_bytes], True


def json_safe(obj: Any) -> Any:
    """Recursively make an object JSON-serializable."""
    if isinstance(obj, bytes):
        return obj.hex()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return obj
