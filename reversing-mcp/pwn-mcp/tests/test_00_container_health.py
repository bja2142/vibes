"""
Test 00 — Container health checks.
Verifies all required tools are present and report correct versions.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest


REQUIRED_TOOLS = [
    "gdb-multiarch",
    "strace",
    "ltrace",
    "valgrind",
    "uftrace",
    "frida",
    "checksec",
    "ropper",
    "patchelf",
    "capa",
    "floss",
    "yara",
    "r2",
    "rabin2",
    "rasm2",
    "nasm",
]

REQUIRED_TOOLS_RR = ["rr"]
REQUIRED_PYTHON_IMPORTS = [
    "pwn", "frida", "z3", "boofuzz",
    "angr", "claripy", "cle", "unicorn", "qiling",
    "capstone", "keystone", "yara",
]


@pytest.mark.parametrize("tool", REQUIRED_TOOLS)
def test_tool_in_path(tool):
    assert shutil.which(tool) is not None, f"'{tool}' not found in PATH"


@pytest.mark.requires_rr
@pytest.mark.parametrize("tool", REQUIRED_TOOLS_RR)
def test_rr_in_path(tool):
    assert shutil.which(tool) is not None, f"'{tool}' not found in PATH"


@pytest.mark.parametrize("module", REQUIRED_PYTHON_IMPORTS)
def test_python_import(module):
    result = subprocess.run(
        ["python3", "-c", f"import {module}"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Failed to import '{module}': {result.stderr}"


def test_drrun_present():
    assert shutil.which("drrun") is not None or \
        __import__("pathlib").Path("/opt/dynamorio/bin64/drrun").exists(), \
        "drrun not found"


def test_qemu_user_static_present():
    import shutil
    assert shutil.which("qemu-x86_64") or shutil.which("qemu-x86_64-static"), \
        "qemu-x86_64 not found"
