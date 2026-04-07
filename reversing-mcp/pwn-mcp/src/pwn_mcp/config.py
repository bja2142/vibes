from __future__ import annotations

import os
from pathlib import Path


SERVER_NAME = "pwn-mcp"
SERVER_VERSION = "0.1.0"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return max(1, int(raw))


def get_workspace_root() -> Path:
    return Path(_env("PWN_MCP_WORKSPACE_ROOT", "/workspace"))


def get_output_root() -> Path:
    return Path(_env("PWN_MCP_OUTPUT_ROOT", "/workspace/dynamic-output"))


def get_sessions_root() -> Path:
    return Path(_env("PWN_MCP_SESSIONS_ROOT", "/tmp/pwn-mcp-sessions"))


# Tool paths — override via env for non-standard installs
TOOL_GDB           = _env("PWN_MCP_GDB",           "gdb-multiarch")
TOOL_RR            = _env("PWN_MCP_RR",             "rr")
TOOL_STRACE        = _env("PWN_MCP_STRACE",         "strace")
TOOL_LTRACE        = _env("PWN_MCP_LTRACE",         "ltrace")
TOOL_VALGRIND      = _env("PWN_MCP_VALGRIND",       "valgrind")
TOOL_UFTRACE       = _env("PWN_MCP_UFTRACE",        "uftrace")
TOOL_AFL_FUZZ      = _env("PWN_MCP_AFL_FUZZ",       "afl-fuzz")
TOOL_AFL_TMIN      = _env("PWN_MCP_AFL_TMIN",       "afl-tmin")
TOOL_AFL_CMIN      = _env("PWN_MCP_AFL_CMIN",       "afl-cmin")
TOOL_CHECKSEC      = _env("PWN_MCP_CHECKSEC",       "checksec")
TOOL_ONE_GADGET    = _env("PWN_MCP_ONE_GADGET",     "one_gadget")
TOOL_ROPPER        = _env("PWN_MCP_ROPPER",         "ropper")
TOOL_ROPGADGET     = _env("PWN_MCP_ROPGADGET",      "ROPgadget")
TOOL_SECCOMP_TOOLS = _env("PWN_MCP_SECCOMP_TOOLS",  "seccomp-tools")
TOOL_DRRUN         = _env("PWN_MCP_DRRUN",          "/opt/dynamorio/bin64/drrun")
TOOL_DRCOV         = _env("PWN_MCP_DRCOV",          "/opt/dynamorio/clients/bin64/libdrcov.so")

# Resource limits
DEFAULT_EXEC_TIMEOUT_SECONDS   = _env_int("PWN_MCP_EXEC_TIMEOUT",   30)
DEFAULT_TRACE_TIMEOUT_SECONDS  = _env_int("PWN_MCP_TRACE_TIMEOUT",  120)
DEFAULT_FUZZ_MAX_SECONDS       = _env_int("PWN_MCP_FUZZ_MAX",       3600)
DEFAULT_OUTPUT_MAX_BYTES       = _env_int("PWN_MCP_OUTPUT_MAX",     1024 * 1024)  # 1 MB
DEFAULT_MAX_SESSIONS           = _env_int("PWN_MCP_MAX_SESSIONS",   16)
DEFAULT_SCRIPT_TIMEOUT_SECONDS = _env_int("PWN_MCP_SCRIPT_TIMEOUT", 30)

# QEMU user-mode binary prefix map: ELF machine type → qemu-user binary
QEMU_USER_MAP: dict[str, str] = {
    "x86":      "qemu-i386",
    "x86_64":   "",              # native — no prefix needed
    "arm":      "qemu-arm",
    "aarch64":  "qemu-aarch64",
    "mips":     "qemu-mips",
    "mipsel":   "qemu-mipsel",
    "mips64":   "qemu-mips64",
    "mips64el": "qemu-mips64el",
    "ppc":      "qemu-ppc",
    "ppc64":    "qemu-ppc64",
    "ppc64le":  "qemu-ppc64le",
    "riscv32":  "qemu-riscv32",
    "riscv64":  "qemu-riscv64",
    "sparc":    "qemu-sparc",
    "sparc64":  "qemu-sparc64",
    "s390x":    "qemu-s390x",
    "m68k":     "qemu-m68k",
    "sh4":      "qemu-sh4",
    "xtensa":   "qemu-xtensa",
    "alpha":    "qemu-alpha",
}
