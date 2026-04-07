"""
Test 02 — Tracing tools: strace, ltrace, valgrind, uftrace.
"""
from __future__ import annotations

from pathlib import Path

import pytest

TEST_BINARIES = Path(__file__).resolve().parents[1] / "test_binaries"


def _binary(name: str) -> str:
    return name


def _skip_if_no_binary(name: str):
    p = TEST_BINARIES / name
    if not p.exists():
        pytest.skip(f"Test binary not found: {p}")


def _skip_if_no_tool(tool_name):
    from pwn_mcp.utils import which_tool
    if not which_tool(tool_name):
        pytest.skip(f"{tool_name} not available")


def test_strace_hello(app, session):
    _skip_if_no_binary("test_hello_x86_64")
    _skip_if_no_tool("strace")
    result = app.dispatch("run_with_strace", {
        "session_id": session.session_id,
        "binary_path": _binary("test_hello_x86_64"),
        "timeout_seconds": 10,
    })
    assert result["ok"]
    assert "trace_id" in result["result"]


def test_valgrind_memcheck(app, session):
    _skip_if_no_binary("test_hello_x86_64")
    _skip_if_no_tool("valgrind")
    result = app.dispatch("run_with_valgrind", {
        "session_id": session.session_id,
        "binary_path": _binary("test_hello_x86_64"),
        "tool": "memcheck",
        "timeout_seconds": 30,
    })
    assert result["ok"]
    assert "trace_output" in result["result"]


def test_get_trace_output(app, session):
    _skip_if_no_binary("test_hello_x86_64")
    _skip_if_no_tool("strace")
    result = app.dispatch("run_with_strace", {
        "session_id": session.session_id,
        "binary_path": _binary("test_hello_x86_64"),
        "timeout_seconds": 10,
    })
    trace_id = result["result"]["trace_id"]

    fetched = app.dispatch("get_trace_output", {
        "session_id": session.session_id,
        "trace_id": trace_id,
    })
    assert fetched["ok"]
    assert len(fetched["result"]["text"]) > 0
