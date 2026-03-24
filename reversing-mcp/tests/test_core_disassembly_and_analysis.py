from __future__ import annotations

import subprocess
import time
from pathlib import Path

from reversing_mcp.app import ReversingMCPApp


def _session_id(payload: dict) -> str:
    return payload["result"]["session"]["session_id"]


def _artifact_id(payload: dict) -> str:
    return payload["result"]["artifact_id"]


def _job_id(payload: dict) -> str:
    return payload["result"]["job_id"]


def _wait_for_job(app: ReversingMCPApp, job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = app.get_job(job_id)
        if payload["result"]["status"] in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not complete before timeout.")


def _build_sample_binary(workspace_root: Path) -> Path:
    source = workspace_root / "feature04_sample.cpp"
    binary = workspace_root / "feature04_sample"
    source.write_text(
        """
        #include <stdio.h>

        namespace demo {
        int compute_sum(int x) {
            return x + 7;
        }
        }

        static const char *banner = "FEATURE04-BANNER";

        int main() {
            puts(banner);
            return demo::compute_sum(5);
        }
        """,
        encoding="utf-8",
    )
    subprocess.run(
        ["c++", "-g", "-O0", "-fno-inline", str(source), "-o", str(binary)],
        check=True,
        cwd=workspace_root,
    )
    return binary


def test_core_analysis_queries_cover_functions_symbols_disassembly_decompilation_xrefs_and_debug(tmp_path: Path) -> None:
    sample = _build_sample_binary(tmp_path)

    app = ReversingMCPApp(workspace_root=tmp_path)
    session_id = _session_id(app.create_session("feature04"))
    artifact_id = _artifact_id(app.add_artifact(session_id, str(sample)))

    started = app.start_artifact_analysis(session_id, artifact_id)
    finished = _wait_for_job(app, _job_id(started))
    assert finished["result"]["status"] == "completed"

    synopsis = app.get_analysis_synopsis(session_id, artifact_id)
    assert synopsis["ok"] is True
    assert synopsis["result"]["summary"]["function_count"] > 0
    assert synopsis["result"]["capabilities"]["features"]["decompilation"] is True

    functions = app.list_artifact_functions(session_id, artifact_id, query="demo::compute_sum")
    assert functions["ok"] is True
    assert functions["result"]["items"]
    function = functions["result"]["items"][0]
    assert function["demangled_name"] == "demo::compute_sum(int)"

    symbols = app.list_artifact_symbols(session_id, artifact_id, kind="import", query="puts")
    assert symbols["ok"] is True
    assert any(item["name"] == "puts" for item in symbols["result"]["items"])

    disassembly = app.disassemble_function(session_id, artifact_id, function_id=function["function_id"], limit=20)
    assert disassembly["ok"] is True
    assert disassembly["result"]["items"]
    assert disassembly["result"]["instruction_set_mode"]["current"]

    decompilation = app.decompile_function(session_id, artifact_id, function_id=function["function_id"], line_limit=40)
    assert decompilation["ok"] is True
    assert decompilation["result"]["status"] == "completed"
    assert decompilation["result"]["source"]

    raw_bytes = app.read_artifact_bytes(session_id, artifact_id, "virtual_address", function["address"], 8)
    assert raw_bytes["ok"] is True
    assert len(raw_bytes["result"]["bytes_hex"]) >= 2

    xrefs = app.list_artifact_xrefs(session_id, artifact_id, function_id=function["function_id"])
    assert xrefs["ok"] is True
    assert xrefs["result"]["items"]

    search_name = app.search_artifact(session_id, artifact_id, "name", query="compute_sum")
    assert search_name["ok"] is True
    assert any(item["result_kind"] == "function" for item in search_name["result"]["items"])

    search_string = app.search_artifact(session_id, artifact_id, "string", query="FEATURE04-BANNER")
    assert search_string["ok"] is True
    assert search_string["result"]["items"]

    search_immediate = app.search_artifact(session_id, artifact_id, "immediate", query="7")
    assert search_immediate["ok"] is True
    assert search_immediate["result"]["items"]

    first_bytes = disassembly["result"]["items"][0]["bytes"]
    search_bytes = app.search_artifact(session_id, artifact_id, "byte_pattern", query=first_bytes)
    assert search_bytes["ok"] is True
    assert search_bytes["result"]["items"]

    search_range = app.search_artifact(
        session_id,
        artifact_id,
        "address_range",
        start_address=function["address"],
        end_address=function["end_address"],
    )
    assert search_range["ok"] is True
    assert any(item["result_kind"] == "function" for item in search_range["result"]["items"])

    linkage = app.get_artifact_linkage(session_id, artifact_id)
    assert linkage["ok"] is True
    assert linkage["result"]["linkage"]["imports"]

    debug_info = app.get_artifact_debug_info(session_id, artifact_id)
    assert debug_info["ok"] is True
    assert debug_info["result"]["debug_info"]["available"] is True
    assert any("feature04_sample.cpp" in item for item in debug_info["result"]["debug_info"]["source_files"])

    mode = app.get_artifact_instruction_mode(session_id, artifact_id)
    assert mode["ok"] is True
    assert mode["result"]["instruction_set_mode"]["supported"]
