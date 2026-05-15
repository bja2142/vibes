"""
Test 06 — Server capabilities and tool registration.
"""
from __future__ import annotations


def test_get_capabilities(app):
    caps = app.get_capabilities()
    assert caps["ok"]
    assert caps["result"]["tool_count"] >= 90
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
        "dump_memory_region", "analyze_heap", "find_format_string_vulns",
        "run_with_strace", "run_with_valgrind",
        "checksec", "get_rop_gadgets", "run_pwntools_script",
        "analyze_seccomp",
        "start_rr_record", "start_rr_replay",
        "run_with_coverage",
        "start_frida_session", "inject_script",
        "run_z3_script", "run_boofuzz_script", "validate_toolchain",
        "run_angr_script", "get_angr_project_info", "angr_find_path",
        "emulate_blob_unicorn", "run_qiling_script",
        "assemble_code", "disassemble_bytes", "disassemble_file_region",
        "run_capa", "run_floss", "run_yara_scan", "run_radare2_command",
        "get_job", "cancel_job",
    ]:
        assert expected in names, f"Expected tool '{expected}' not registered"


def test_validate_toolchain_maps_backends_to_mcp_tools(app):
    result = app.dispatch("validate_toolchain", {})
    assert result["ok"]
    assert result["result"]["missing_required"] == []
    external = result["result"]["external_tools"]
    assert external["drcov"]["available"]
    assert external["capa"]["available"]
    assert external["floss"]["available"]
    assert external["yara"]["available"]
    assert external["r2"]["available"]
    assert "start_frida_session" in external["frida"]["mcp_tools"]
    assert "run_with_coverage" in external["drrun"]["mcp_tools"]
    assert "run_z3_script" in result["result"]["python_imports"]["z3"]["mcp_tools"]
    assert "angr_find_path" in result["result"]["python_imports"]["angr"]["mcp_tools"]
    assert "emulate_blob_unicorn" in result["result"]["python_imports"]["unicorn"]["mcp_tools"]
    assert "assemble_code" in result["result"]["python_imports"]["keystone"]["mcp_tools"]


def test_dispatch_unknown_tool(app):
    import pytest
    from pwn_mcp.errors import PwnMcpError
    with pytest.raises(PwnMcpError) as exc_info:
        app.dispatch("nonexistent_tool", {})
    assert exc_info.value.code == "unknown_tool"
