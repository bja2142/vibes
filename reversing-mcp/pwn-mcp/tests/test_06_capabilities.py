"""
Test 06 — Server capabilities and tool registration.
"""
from __future__ import annotations


def test_get_capabilities(app):
    caps = app.get_capabilities()
    assert caps["ok"]
    assert caps["result"]["tool_count"] > 50
    assert "x86_64" in caps["result"]["supported_architectures"]
    assert "aarch64" in caps["result"]["supported_architectures"]


def test_all_tools_have_schemas(app):
    for name, entry in app._tools.items():
        assert "schema" in entry, f"Tool '{name}' missing schema"
        assert "handler" in entry, f"Tool '{name}' missing handler"
        schema = entry["schema"]
        assert schema.name == name, f"Tool '{name}' schema name mismatch: {schema.name}"


def test_tool_definitions_list(app):
    defs = app.tool_definitions()
    names = [d.name for d in defs]
    # Spot-check key tools exist
    for expected in [
        "create_execution_session", "launch_binary", "send_input", "read_output",
        "start_debug_session", "set_breakpoint", "read_registers",
        "run_with_strace", "run_with_valgrind",
        "checksec", "get_rop_gadgets", "run_pwntools_script",
        "analyze_seccomp",
        "start_rr_record", "start_rr_replay",
        "run_with_coverage",
        "start_afl_session", "get_crash_inputs",
        "start_frida_session", "inject_script",
        "get_job", "cancel_job",
    ]:
        assert expected in names, f"Expected tool '{expected}' not registered"


def test_dispatch_unknown_tool(app):
    import pytest
    from pwn_mcp.errors import PwnMcpError
    with pytest.raises(PwnMcpError) as exc_info:
        app.dispatch("nonexistent_tool", {})
    assert exc_info.value.code == "unknown_tool"
