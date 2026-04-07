"""
Test 05 — Seccomp analysis tool.
"""
from __future__ import annotations

from pathlib import Path

import pytest

TEST_BINARIES = Path(__file__).resolve().parents[1] / "test_binaries"


def _skip_if_no_binary(name: str):
    if not (TEST_BINARIES / name).exists():
        pytest.skip(f"Test binary not found: {name}")


def _skip_if_no_tool():
    from pwn_mcp.utils import which_tool
    if not which_tool("seccomp-tools"):
        pytest.skip("seccomp-tools not available")


def test_analyze_seccomp(app, session):
    _skip_if_no_binary("test_seccomp_x86_64")
    _skip_if_no_tool()

    result = app.dispatch("analyze_seccomp", {
        "session_id": session.session_id,
        "binary_path": "test_seccomp_x86_64",
    })
    assert result["ok"]
    output = result["result"]["seccomp_filter"]
    # Should show the BPF filter rules
    assert len(output) > 0
