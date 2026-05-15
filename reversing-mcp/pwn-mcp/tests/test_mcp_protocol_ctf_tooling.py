from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

TEST_BINARIES = Path(__file__).resolve().parents[1] / "test_binaries"

NEW_CTF_TOOLS = {
    "run_angr_script",
    "get_angr_project_info",
    "angr_find_path",
    "emulate_blob_unicorn",
    "run_qiling_script",
    "assemble_code",
    "disassemble_bytes",
    "disassemble_file_region",
    "run_capa",
    "run_floss",
    "run_yara_scan",
    "run_radare2_command",
}


def _require_binary(name: str) -> None:
    if not (TEST_BINARIES / name).exists():
        pytest.skip(f"{name} not built")


def _find_symbol(binary: str, symbol: str) -> str:
    if not shutil.which("nm"):
        pytest.skip("nm not available")
    proc = subprocess.run(
        ["nm", str(TEST_BINARIES / binary)],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == symbol:
            return f"0x{parts[0]}"
    pytest.fail(f"{symbol} not found in {binary}")


def _extract_payload(result) -> dict:
    assert result.isError is False, result.content
    assert result.content, "tool returned no content"
    payload = json.loads(result.content[0].text)
    assert payload["ok"], payload
    return payload


async def _call_tool(session: ClientSession, name: str, arguments: dict | None = None) -> dict:
    return _extract_payload(await session.call_tool(name, arguments or {}))


async def _exercise_protocol_ctf_tools(tmp_path: Path) -> None:
    _require_binary("test_hello_x86_64")
    _require_binary("test_angr_x86_64")

    source_root = Path(__file__).resolve().parents[1] / "src"
    server = StdioServerParameters(
        command="python3",
        args=["-m", "pwn_mcp.server", "--transport", "stdio"],
        cwd=TEST_BINARIES,
        env={
            "HOME": os.environ.get("HOME", "/tmp"),
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(source_root),
            "PWN_MCP_WORKSPACE_ROOT": str(TEST_BINARIES),
            "PWN_MCP_OUTPUT_ROOT": str(tmp_path / "output"),
            "PWN_MCP_SESSIONS_ROOT": str(tmp_path / "sessions"),
            "PWN_MCP_LOG_LEVEL": "WARNING",
        },
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            assert NEW_CTF_TOOLS <= tool_names

            created = await _call_tool(session, "create_execution_session")
            session_id = created["result"]["session_id"]

            assembled = await _call_tool(
                session,
                "assemble_code",
                {
                    "session_id": session_id,
                    "arch": "x86_64",
                    "assembly": "mov rax, 0x2a; nop",
                },
            )
            assert assembled["result"]["bytes_hex"]

            disassembled = await _call_tool(
                session,
                "disassemble_bytes",
                {
                    "session_id": session_id,
                    "arch": "x86_64",
                    "code_hex": assembled["result"]["bytes_hex"],
                },
            )
            assert disassembled["result"]["instructions"][0]["mnemonic"] == "mov"

            file_disasm = await _call_tool(
                session,
                "disassemble_file_region",
                {
                    "session_id": session_id,
                    "binary_path": "test_hello_x86_64",
                    "offset": 0,
                    "length": 64,
                    "arch": "x86_64",
                    "max_instructions": 4,
                },
            )
            assert file_disasm["result"]["binary_path"].endswith("test_hello_x86_64")

            emulated = await _call_tool(
                session,
                "emulate_blob_unicorn",
                {
                    "session_id": session_id,
                    "arch": "x86_64",
                    "code_hex": "48c7c02a00000090",
                },
            )
            assert emulated["result"]["execution_error"] is None
            assert emulated["result"]["registers"]["rax"] == "0x2a"

            angr_script = await _call_tool(
                session,
                "run_angr_script",
                {"session_id": session_id, "script": "print(angr.__version__)"},
            )
            assert angr_script["result"]["return_code"] == 0
            assert angr_script["result"]["stdout"].strip()

            project_info = await _call_tool(
                session,
                "get_angr_project_info",
                {"session_id": session_id, "binary_path": "test_hello_x86_64"},
            )
            assert project_info["result"]["bits"] in (32, 64)

            win_addr = _find_symbol("test_angr_x86_64", "win")
            solved = await _call_tool(
                session,
                "angr_find_path",
                {
                    "session_id": session_id,
                    "binary_path": "test_angr_x86_64",
                    "find_address": win_addr,
                    "stdin_size": 4,
                    "timeout_seconds": 60,
                },
            )
            assert solved["result"]["return_code"] == 0
            assert solved["result"]["parsed"]["stdin_ascii"] == "CTF!"

            qiling_script = await _call_tool(
                session,
                "run_qiling_script",
                {"session_id": session_id, "script": "print(Qiling.__name__)"},
            )
            assert qiling_script["result"]["return_code"] == 0
            assert "Qiling" in qiling_script["result"]["stdout"]

            yara = await _call_tool(
                session,
                "run_yara_scan",
                {
                    "session_id": session_id,
                    "target_path": "test_hello_x86_64",
                    "rule_source": 'rule HelloWorld { strings: $s = "hello world" condition: $s }',
                },
            )
            assert any("HelloWorld" in item for item in yara["result"]["matches"])

            r2 = await _call_tool(
                session,
                "run_radare2_command",
                {"session_id": session_id, "binary_path": "test_hello_x86_64", "commands": ["ij"]},
            )
            assert json.loads(r2["result"]["stdout"])

            capa = await _call_tool(
                session,
                "run_capa",
                {
                    "session_id": session_id,
                    "binary_path": "test_hello_x86_64",
                    "output_format": "json",
                    "timeout_seconds": 60,
                },
            )
            assert capa["result"]["return_code"] == 0
            assert capa["result"]["stdout"].strip().startswith("{")

            floss = await _call_tool(
                session,
                "run_floss",
                {
                    "session_id": session_id,
                    "binary_path": "test_hello_x86_64",
                    "output_format": "json",
                    "timeout_seconds": 60,
                },
            )
            assert floss["result"]["return_code"] == 0
            assert floss["result"]["stdout"].strip().startswith("{")

            await _call_tool(session, "destroy_execution_session", {"session_id": session_id})


@pytest.mark.slow
def test_new_ctf_tools_are_accessible_over_mcp_protocol(tmp_path):
    asyncio.run(_exercise_protocol_ctf_tools(tmp_path))
