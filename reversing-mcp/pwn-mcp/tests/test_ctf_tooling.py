from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

TEST_BINARIES = Path(__file__).resolve().parents[1] / "test_binaries"


def _require_binary(name: str) -> None:
    if not (TEST_BINARIES / name).exists():
        pytest.skip(f"{name} not built")


def test_keystone_capstone_round_trip(app, session):
    result = app.dispatch("assemble_code", {
        "session_id": session.session_id,
        "arch": "x86_64",
        "assembly": "mov rax, 0x2a; nop",
    })
    assert result["ok"]
    assert result["result"]["bytes_hex"]

    disasm = app.dispatch("disassemble_bytes", {
        "session_id": session.session_id,
        "arch": "x86_64",
        "code_hex": result["result"]["bytes_hex"],
    })
    assert disasm["ok"]
    assert disasm["result"]["instructions"][0]["mnemonic"] == "mov"


def test_unicorn_blob_emulation(app, session):
    result = app.dispatch("emulate_blob_unicorn", {
        "session_id": session.session_id,
        "arch": "x86_64",
        "code_hex": "48c7c02a00000090",  # mov rax, 0x2a; nop
    })
    assert result["ok"]
    assert result["result"]["execution_error"] is None
    assert result["result"]["registers"]["rax"] == "0x2a"


def test_angr_project_info_and_script(app, session):
    _require_binary("test_hello_x86_64")
    info = app.dispatch("get_angr_project_info", {
        "session_id": session.session_id,
        "binary_path": "test_hello_x86_64",
    })
    assert info["ok"]
    assert info["result"]["bits"] in (32, 64)

    script = app.dispatch("run_angr_script", {
        "session_id": session.session_id,
        "script": "print(angr.__version__)",
    })
    assert script["ok"]
    assert script["result"]["return_code"] == 0
    assert script["result"]["stdout"].strip()


@pytest.mark.slow
def test_angr_find_path_solves_symbolic_stdin(app, session):
    _require_binary("test_angr_x86_64")
    if not shutil.which("nm"):
        pytest.skip("nm not available")
    nm = subprocess.run(["nm", str(TEST_BINARIES / "test_angr_x86_64")], capture_output=True, text=True, check=True)
    win_addr = None
    for line in nm.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == "win":
            win_addr = f"0x{parts[0]}"
            break
    assert win_addr is not None

    solved = app.dispatch("angr_find_path", {
        "session_id": session.session_id,
        "binary_path": "test_angr_x86_64",
        "find_address": win_addr,
        "stdin_size": 4,
        "timeout_seconds": 60,
    })
    assert solved["ok"]
    assert solved["result"]["return_code"] == 0
    assert solved["result"]["parsed"]["found"] is True
    assert solved["result"]["parsed"]["stdin_ascii"] == "CTF!"


def test_qiling_script_runner_imports_qiling(app, session):
    result = app.dispatch("run_qiling_script", {
        "session_id": session.session_id,
        "script": "print(Qiling.__name__)",
    })
    assert result["ok"]
    assert result["result"]["return_code"] == 0
    assert "Qiling" in result["result"]["stdout"]


def test_yara_and_radare2_triage_tools(app, session):
    _require_binary("test_hello_x86_64")
    yara = app.dispatch("run_yara_scan", {
        "session_id": session.session_id,
        "target_path": "test_hello_x86_64",
        "rule_source": 'rule HelloWorld { strings: $s = "hello world" condition: $s }',
    })
    assert yara["ok"]
    assert yara["result"]["return_code"] == 0
    assert any("HelloWorld" in item for item in yara["result"]["matches"])

    r2 = app.dispatch("run_radare2_command", {
        "session_id": session.session_id,
        "binary_path": "test_hello_x86_64",
        "commands": ["ij"],
    })
    assert r2["ok"]
    assert r2["result"]["return_code"] == 0
    assert json.loads(r2["result"]["stdout"])


@pytest.mark.slow
def test_capa_and_floss_wrappers_execute(app, session):
    _require_binary("test_hello_x86_64")
    capa = app.dispatch("run_capa", {
        "session_id": session.session_id,
        "binary_path": "test_hello_x86_64",
        "output_format": "json",
        "timeout_seconds": 60,
    })
    assert capa["ok"]
    assert capa["result"]["return_code"] == 0
    assert capa["result"]["stdout"].strip().startswith("{")

    floss = app.dispatch("run_floss", {
        "session_id": session.session_id,
        "binary_path": "test_hello_x86_64",
        "output_format": "json",
        "timeout_seconds": 60,
    })
    assert floss["ok"]
    assert floss["result"]["return_code"] == 0
    assert floss["result"]["stdout"].strip().startswith("{")
