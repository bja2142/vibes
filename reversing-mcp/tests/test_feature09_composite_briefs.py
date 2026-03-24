from __future__ import annotations

import subprocess
from pathlib import Path

from reversing_mcp.app import ReversingMCPApp


def _session_id(payload: dict) -> str:
    return payload["result"]["session"]["session_id"]


def _artifact_id(payload: dict) -> str:
    return payload["result"]["artifact"]["artifact_id"]


def _build_feature09_sample(workspace_root: Path) -> Path:
    source = workspace_root / "feature09_sample.cpp"
    binary = workspace_root / "feature09_sample"
    source.write_text(
        """
        #include <stdio.h>
        #include <string.h>

        static const char *banner = "https://feature09.local/api";
        static const unsigned tea_delta = 0x9e3779b9u;

        __attribute__((noinline)) int helper(int x) {
            if (x == 3) {
                puts("secret-token");
            }
            return x + 7 + tea_delta;
        }

        int main(void) {
            puts(banner);
            return helper((int)strlen("cmd.exe"));
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


def test_feature09_composite_briefs_reduce_round_trips(tmp_path: Path) -> None:
    sample = _build_feature09_sample(tmp_path)
    app = ReversingMCPApp(workspace_root=tmp_path)
    session_id = _session_id(app.create_session("feature09"))

    ingested = app.ingest_and_triage_artifact(
        session_id,
        str(sample),
        "feature09_sample",
        analyze=False,
        verbosity="brief",
        token_budget_hint=400,
    )
    assert ingested["ok"] is True
    artifact_id = _artifact_id(ingested)
    assert ingested["result"]["triage_brief"]["file_type"]["format"] == "ELF"

    analyzed = app.analyze_and_summarize(
        session_id,
        artifact_id,
        focus="malware",
        wait_timeout_seconds=30.0,
        verbosity="deep",
        token_budget_hint=400,
    )
    assert analyzed["ok"] is True
    assert analyzed["result"]["analysis_status"] == "completed"
    assert analyzed["result"]["response_profile"]["effective_verbosity"] == "brief"
    assert analyzed["result"]["summary"]["top_functions"]["items"]

    hunt = app.hunt_interesting_regions(
        session_id,
        artifact_id,
        objective="malware",
        limit=5,
        include_next_actions=False,
    )
    assert hunt["ok"] is True
    assert hunt["suggested_next_actions"] == []
    assert hunt["result"]["interesting_regions"]["suspicious_strings"]["items"]

    top_function = hunt["result"]["interesting_regions"]["top_functions"]["items"][0]
    traced = app.trace_capability(
        session_id,
        artifact_id,
        {"function_id": top_function["function_id"]},
        depth=1,
        include_raw_sections=True,
    )
    assert traced["ok"] is True
    assert traced["result"]["trace"]["variables"]["arguments"] is not None
    assert traced["result"]["raw_sections"]["instruction_preview"]

    patch_plan = app.prepare_patch_plan(
        session_id,
        artifact_id,
        "bypass_guard",
        {"function_id": top_function["function_id"]},
        min_code_cave_size=8,
    )
    assert patch_plan["ok"] is True
    assert patch_plan["result"]["patch_plan"]["candidate_patch_points"]

    patched = app.patch_artifact_bytes(
        session_id,
        artifact_id,
        "file_offset",
        0,
        "9090",
        attach_to_session=True,
        display_name="feature09_patched.bin",
    )
    assert patched["ok"] is True

    relationship = app.artifact_relationship_brief(
        session_id,
        artifact_id,
        focus="diffing",
        include_raw_sections=True,
    )
    assert relationship["ok"] is True
    assert relationship["result"]["relationship_brief"]["diff_candidates"]["items"]
    assert "dependency_summary" in relationship["result"]["raw_sections"]
