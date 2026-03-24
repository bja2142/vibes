from __future__ import annotations

import subprocess
import time
from pathlib import Path

from reversing_mcp.app import ReversingMCPApp
from reversing_mcp.feature07 import assemble_patch


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


def _build_feature07_sample(workspace_root: Path) -> Path:
    source = workspace_root / "feature07_sample.cpp"
    binary = workspace_root / "feature07_sample"
    source.write_text(
        """
        #include <stdio.h>

        struct Config {
            int threshold;
            const char *label;
        };

        static Config g_config = {7, "FEATURE07"};

        __attribute__((noinline)) int compute_sum(int x) {
            return x + g_config.threshold;
        }

        int main() {
            puts(g_config.label);
            return compute_sum(5);
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


def test_feature07_patching_overrides_reports_and_correlation(tmp_path: Path) -> None:
    sample = _build_feature07_sample(tmp_path)
    app = ReversingMCPApp(workspace_root=tmp_path)
    session_id = _session_id(app.create_session("feature07"))
    artifact_id = _artifact_id(app.add_artifact(session_id, str(sample)))

    analysis_job = app.start_artifact_analysis(session_id, artifact_id)
    finished = _wait_for_job(app, _job_id(analysis_job))
    assert finished["result"]["status"] == "completed"

    function = app.list_artifact_functions(session_id, artifact_id, query="compute_sum")["result"]["items"][0]

    caves = app.find_code_caves(session_id, artifact_id, min_size=8)
    assert caves["ok"] is True
    assert "summary" in caves["result"]["code_caves"]

    rename = app.edit_artifact_metadata(
        session_id,
        artifact_id,
        "function_name",
        {"function_id": function["function_id"]},
        {"name": "patched_compute_sum"},
    )
    assert rename["ok"] is True

    callconv = app.edit_artifact_metadata(
        session_id,
        artifact_id,
        "calling_convention",
        {"function_id": function["function_id"]},
        {"name": "sysv_abi_override"},
    )
    assert callconv["ok"] is True

    imported = app.import_type_definitions(
        session_id,
        artifact_id,
        "c_header",
        "typedef struct Settings { int enabled; } Settings;\nint compute_sum(int x);",
    )
    assert imported["ok"] is True

    patched_view = app.list_artifact_functions(session_id, artifact_id, query="patched_compute_sum")
    assert patched_view["ok"] is True
    assert patched_view["result"]["items"][0]["name"] == "patched_compute_sum"

    calling_convention = app.get_calling_convention(session_id, artifact_id, function_id=function["function_id"])
    assert calling_convention["ok"] is True
    assert calling_convention["result"]["calling_convention"]["name"] == "sysv_abi_override"

    type_information = app.get_type_information(session_id, artifact_id)
    assert type_information["ok"] is True
    assert any(item["name"] == "Settings" for item in type_information["result"]["type_information"]["named_types"])
    assert type_information["result"]["type_information"]["imports"]

    byte_patch_path = tmp_path / "outputs" / "byte_patch.bin"
    byte_patch = app.patch_artifact_bytes(session_id, artifact_id, "file_offset", 0, "9090", str(byte_patch_path), True, "byte_patch.bin")
    assert byte_patch["ok"] is True
    assert byte_patch_path.exists()
    patched_byte_artifact = byte_patch["result"]["attached_artifact"]["artifact_id"]

    asm_patch_path = tmp_path / "outputs" / "asm_patch.bin"
    asm_patch = app.patch_artifact_assembly(session_id, artifact_id, "file_offset", 0, "nop; ret", "x86_64", str(asm_patch_path), True, "asm_patch.bin")
    assert asm_patch["ok"] is True
    assert asm_patch_path.exists()
    patched_asm_artifact = asm_patch["result"]["attached_artifact"]["artifact_id"]

    dependencies = app.list_artifact_dependencies(session_id, artifact_id)
    assert dependencies["ok"] is True
    assert "imports" in dependencies["result"]["dependencies"]

    correlations = app.correlate_session_artifacts(session_id, [artifact_id, patched_byte_artifact, patched_asm_artifact])
    assert correlations["ok"] is True
    assert isinstance(correlations["result"]["correlations"]["items"], list)

    diff = app.diff_artifacts(session_id, artifact_id, patched_byte_artifact)
    assert diff["ok"] is True
    assert diff["result"]["diff"]["structural"]["sha256_changed"] is True

    command_log_path = tmp_path / "outputs" / "command-log.txt"
    command_log = app.export_command_log(session_id, format="text", output_path=str(command_log_path))
    assert command_log["ok"] is True
    assert command_log_path.exists()

    report_path = tmp_path / "outputs" / "report.json"
    report = app.export_analysis_report(session_id, artifact_id, format="json", output_path=str(report_path))
    assert report["ok"] is True
    assert report_path.exists()


def test_feature07_mini_assembler_supports_common_arm_variants() -> None:
    aarch64 = assemble_patch("aarch64", "nop; ret; brk 0")
    assert aarch64["isa"] == "aarch64"
    assert aarch64["bytes"].hex() == "1f2003d5c0035fd6000020d4"

    aarch64_branch = assemble_patch("arm64", "b 0x1008; bl 0x1010", origin_virtual_address=0x1000)
    assert aarch64_branch["bytes"].hex() == "0200001403000094"

    arm = assemble_patch("arm", "nop; ret; bkpt 0")
    assert arm["isa"] == "arm"
    assert arm["bytes"].hex() == "00f020e31eff2fe1700020e1"

    arm_branch = assemble_patch("arm32", "b 0x1010; bl 0x1018", origin_virtual_address=0x1000)
    assert arm_branch["bytes"].hex() == "020000ea030000eb"

    thumb = assemble_patch("thumb", "nop; ret; bkpt 0")
    assert thumb["isa"] == "thumb"
    assert thumb["bytes"].hex() == "00bf704700be"

    thumb_branch = assemble_patch("thumb2", "b 0x1008", origin_virtual_address=0x1000)
    assert thumb_branch["bytes"].hex() == "02e0"
