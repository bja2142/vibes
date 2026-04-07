"""
Test 01 — Process control: launch, I/O, state detection.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest


# conftest.py defines TMP_WORKSPACE, binary(), app, session fixtures
TEST_BINARIES = Path(__file__).resolve().parents[1] / "test_binaries"


def _binary(name: str) -> str:
    return name


def _skip_if_no_binary(name: str):
    p = TEST_BINARIES / name
    if not p.exists():
        pytest.skip(f"Test binary not found: {p}")


# ── Session lifecycle ─────────────────────────────────────────────────────────

def test_create_and_destroy_session(app):
    result = app.dispatch("create_execution_session", {})
    assert result["ok"]
    sid = result["result"]["session_id"]
    assert sid.startswith("sess_")

    result2 = app.dispatch("destroy_execution_session", {"session_id": sid})
    assert result2["ok"]


def test_list_sessions(app):
    result = app.dispatch("create_execution_session", {})
    sid = result["result"]["session_id"]
    try:
        listing = app.dispatch("list_execution_sessions", {})
        assert listing["ok"]
        ids = [s["session_id"] for s in listing["result"]["sessions"]]
        assert sid in ids
    finally:
        app.dispatch("destroy_execution_session", {"session_id": sid})


# ── Process launch & I/O ─────────────────────────────────────────────────────

def test_launch_hello(app, session):
    _skip_if_no_binary("test_hello_x86_64")
    result = app.dispatch("launch_binary", {
        "session_id": session.session_id,
        "binary_path": _binary("test_hello_x86_64"),
    })
    assert result["ok"]
    pid = result["result"]["process_id"]

    # Wait for it to finish and read output
    time.sleep(0.5)
    out = app.dispatch("read_output", {
        "session_id": session.session_id,
        "process_id": pid,
        "stream": "stdout",
        "wait_ms": 2000,
    })
    assert out["ok"]
    assert "hello" in out["result"]["text"].lower()


def test_launch_aarch64(app, session):
    _skip_if_no_binary("test_hello_aarch64")
    result = app.dispatch("launch_binary", {
        "session_id": session.session_id,
        "binary_path": _binary("test_hello_aarch64"),
    })
    assert result["ok"]
    assert result["result"]["arch"] == "aarch64"

    time.sleep(1.0)
    out = app.dispatch("read_output", {
        "session_id": session.session_id,
        "process_id": result["result"]["process_id"],
        "stream": "stdout",
        "wait_ms": 2000,
    })
    assert "hello" in out["result"]["text"].lower()


def test_send_input(app, session):
    _skip_if_no_binary("test_format_x86_64")
    launch = app.dispatch("launch_binary", {
        "session_id": session.session_id,
        "binary_path": _binary("test_format_x86_64"),
    })
    assert launch["ok"]
    pid = launch["result"]["process_id"]

    time.sleep(0.3)
    send = app.dispatch("send_input", {
        "session_id": session.session_id,
        "process_id": pid,
        "data": "hello test",
    })
    assert send["ok"]

    time.sleep(0.3)
    out = app.dispatch("read_output", {
        "session_id": session.session_id,
        "process_id": pid,
        "stream": "stdout",
    })
    assert out["ok"]


def test_process_state_exited(app, session):
    _skip_if_no_binary("test_hello_x86_64")
    launch = app.dispatch("launch_binary", {
        "session_id": session.session_id,
        "binary_path": _binary("test_hello_x86_64"),
    })
    pid = launch["result"]["process_id"]
    time.sleep(0.5)

    state = app.dispatch("get_process_state", {
        "session_id": session.session_id,
        "process_id": pid,
    })
    assert state["ok"]
    assert state["result"]["state"] == "exited"
    assert state["result"]["exit_code"] == 0


def test_terminate_process(app, session):
    _skip_if_no_binary("test_heap_x86_64")
    launch = app.dispatch("launch_binary", {
        "session_id": session.session_id,
        "binary_path": _binary("test_heap_x86_64"),
    })
    pid = launch["result"]["process_id"]
    time.sleep(0.3)

    state = app.dispatch("get_process_state", {
        "session_id": session.session_id,
        "process_id": pid,
    })
    assert state["result"]["state"] == "running"

    term = app.dispatch("terminate_process", {
        "session_id": session.session_id,
        "process_id": pid,
        "sig": "SIGKILL",
    })
    assert term["ok"]


def test_launch_mipsel(app, session):
    _skip_if_no_binary("test_hello_mipsel")
    result = app.dispatch("launch_binary", {
        "session_id": session.session_id,
        "binary_path": _binary("test_hello_mipsel"),
    })
    assert result["ok"]
    assert result["result"]["arch"] == "mipsel"

    time.sleep(1.0)
    out = app.dispatch("read_output", {
        "session_id": session.session_id,
        "process_id": result["result"]["process_id"],
        "stream": "stdout",
        "wait_ms": 2000,
    })
    assert "hello" in out["result"]["text"].lower()


def test_launch_riscv64(app, session):
    _skip_if_no_binary("test_hello_riscv64")
    result = app.dispatch("launch_binary", {
        "session_id": session.session_id,
        "binary_path": _binary("test_hello_riscv64"),
    })
    assert result["ok"]
    assert result["result"]["arch"] == "riscv64"

    time.sleep(1.0)
    out = app.dispatch("read_output", {
        "session_id": session.session_id,
        "process_id": result["result"]["process_id"],
        "stream": "stdout",
        "wait_ms": 2000,
    })
    assert "hello" in out["result"]["text"].lower()


def test_crash_detection(app, session):
    _skip_if_no_binary("test_crash_offset_x86_64")
    launch = app.dispatch("launch_binary", {
        "session_id": session.session_id,
        "binary_path": _binary("test_crash_offset_x86_64"),
    })
    pid = launch["result"]["process_id"]

    app.dispatch("send_input", {
        "session_id": session.session_id,
        "process_id": pid,
        "data": "A" * 200,
    })

    time.sleep(0.5)
    state = app.dispatch("get_process_state", {
        "session_id": session.session_id,
        "process_id": pid,
    })
    assert state["result"]["state"] == "exited"
    assert state["result"]["exit_code"] != 0
