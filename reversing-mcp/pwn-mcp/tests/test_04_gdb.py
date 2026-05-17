"""
Test 04 — GDB integration: debug sessions, breakpoints, stepping, memory.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

TEST_BINARIES = Path(__file__).resolve().parents[1] / "test_binaries"


def _binary(name: str) -> str:
    return name


def _skip_if_no_binary(name: str):
    if not (TEST_BINARIES / name).exists():
        pytest.skip(f"Test binary not found: {name}")


def _skip_if_no_gdb():
    import shutil
    if not shutil.which("gdb-multiarch"):
        pytest.skip("gdb-multiarch not available")


# ── Debug session lifecycle ───────────────────────────────────────────────────

def test_start_and_stop_debug_session(app, session):
    _skip_if_no_binary("test_hello_x86_64")
    _skip_if_no_gdb()

    result = app.dispatch("start_debug_session", {
        "session_id": session.session_id,
        "binary_path": _binary("test_hello_x86_64"),
        "framework": "vanilla",
    })
    assert result["ok"]
    debug_id = result["result"]["debug_id"]
    assert debug_id.startswith("dbg_")

    stop = app.dispatch("stop_debug_session", {
        "session_id": session.session_id,
        "debug_id": debug_id,
    })
    assert stop["ok"]
    assert stop["result"]["stopped"]


# ── Breakpoints ───────────────────────────────────────────────────────────────

def test_set_and_list_breakpoints(app, session):
    _skip_if_no_binary("test_hello_x86_64")
    _skip_if_no_gdb()

    start = app.dispatch("start_debug_session", {
        "session_id": session.session_id,
        "binary_path": _binary("test_hello_x86_64"),
        "framework": "vanilla",
    })
    debug_id = start["result"]["debug_id"]

    try:
        bp = app.dispatch("set_breakpoint", {
            "session_id": session.session_id,
            "debug_id": debug_id,
            "location": "main",
        })
        assert bp["ok"]

        bps = app.dispatch("list_breakpoints", {
            "session_id": session.session_id,
            "debug_id": debug_id,
        })
        assert bps["ok"]
    finally:
        app.dispatch("stop_debug_session", {
            "session_id": session.session_id,
            "debug_id": debug_id,
        })


# ── Run to breakpoint and inspect ────────────────────────────────────────────

def test_run_to_main_and_read_registers(app, session):
    _skip_if_no_binary("test_hello_x86_64")
    _skip_if_no_gdb()

    start = app.dispatch("start_debug_session", {
        "session_id": session.session_id,
        "binary_path": _binary("test_hello_x86_64"),
        "framework": "vanilla",
    })
    debug_id = start["result"]["debug_id"]

    try:
        # Set breakpoint at main
        app.dispatch("set_breakpoint", {
            "session_id": session.session_id,
            "debug_id": debug_id,
            "location": "main",
        })

        # Run the program (exec-run, not exec-continue, since it hasn't started yet)
        app.dispatch("send_gdb_command", {
            "session_id": session.session_id,
            "debug_id": debug_id,
            "command": "run",
            "timeout_seconds": 10,
        })

        time.sleep(0.5)

        # Read registers
        regs = app.dispatch("read_registers", {
            "session_id": session.session_id,
            "debug_id": debug_id,
        })
        assert regs["ok"]

        named_regs = app.dispatch("read_registers", {
            "session_id": session.session_id,
            "debug_id": debug_id,
            "register_names": ["pc"],
        })
        assert named_regs["ok"]
        assert named_regs["result"]["class"] == "done"

        sp = app.dispatch("evaluate_expression", {
            "session_id": session.session_id,
            "debug_id": debug_id,
            "expression": "$sp",
        })
        match = re.search(r'value="(0x[0-9a-fA-F]+)', sp["result"]["payload"])
        assert match
        stack_addr = match.group(1)

        write_reg = app.dispatch("write_register", {
            "session_id": session.session_id,
            "debug_id": debug_id,
            "register": "x2",
            "value": "0x1234",
        })
        assert write_reg["ok"]
        assert write_reg["result"]["class"] == "done"

        write_mem = app.dispatch("write_memory", {
            "session_id": session.session_id,
            "debug_id": debug_id,
            "address": stack_addr,
            "data_hex": "4142",
        })
        assert write_mem["ok"]
        assert write_mem["result"]["class"] == "done"

        search = app.dispatch("search_memory", {
            "session_id": session.session_id,
            "debug_id": debug_id,
            "start_address": stack_addr,
            "end_address": hex(int(stack_addr, 16) + 0x100),
            "pattern_hex": "4142",
        })
        assert search["ok"]
        assert search["result"]["matches"]

        # Get backtrace
        bt = app.dispatch("get_backtrace", {
            "session_id": session.session_id,
            "debug_id": debug_id,
        })
        assert bt["ok"]
    finally:
        app.dispatch("stop_debug_session", {
            "session_id": session.session_id,
            "debug_id": debug_id,
        })


# ── GDB CLI command passthrough ───────────────────────────────────────────────

def test_send_cli_command(app, session):
    _skip_if_no_binary("test_hello_x86_64")
    _skip_if_no_gdb()

    start = app.dispatch("start_debug_session", {
        "session_id": session.session_id,
        "binary_path": _binary("test_hello_x86_64"),
        "framework": "vanilla",
    })
    debug_id = start["result"]["debug_id"]

    try:
        result = app.dispatch("send_gdb_command", {
            "session_id": session.session_id,
            "debug_id": debug_id,
            "command": "info functions main",
        })
        assert result["ok"]
    finally:
        app.dispatch("stop_debug_session", {
            "session_id": session.session_id,
            "debug_id": debug_id,
        })
