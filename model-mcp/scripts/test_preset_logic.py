#!/usr/bin/env python3

from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import time


def write_fake_cli(path: Path, name: str) -> None:
    if name == "codex":
        template = """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
out_path = args[args.index("--output-last-message") + 1]
prompt = args[-1]

if "force codex failure" in prompt:
    print("forced codex failure", file=sys.stderr)
    raise SystemExit(9)

def choose_reply(prompt: str) -> str:
    if "<draft_model_outputs>" in prompt and "raw answer from codex" in prompt and "raw answer from gemini" in prompt:
        return "cross model ok"
    if "<draft_model_outputs>" in prompt and "raw answer from gemini" in prompt and "forced codex failure" in prompt:
        return "cross model partial ok"
    if "<preset_name>\\nconversation_handoff\\n</preset_name>" in prompt:
        return "handoff ok"
    if "<preset_name>\\nsecurity_assessment\\n</preset_name>" in prompt and "<task_prompt>\\ncheck auth flow\\n</task_prompt>" in prompt:
        return "preset ok"
    if "seed conversation" in prompt:
        return "seed reply"
    return "raw answer from {name}"

pathlib.Path(out_path).write_text(choose_reply(prompt), encoding="utf-8")
"""
    else:
        template = """#!/usr/bin/env python3
import sys

args = sys.argv[1:]
if "-p" in args:
    prompt_index = args.index("-p") + 1
    if prompt_index < len(args) and not args[prompt_index].startswith("-"):
        prompt = args[prompt_index]
    else:
        prompt = args[-1]
else:
    prompt = args[-1]

if "force claude failure" in prompt and "{name}" == "claude":
    print("forced claude failure", file=sys.stderr)
    raise SystemExit(9)
if "force gemini failure" in prompt and "{name}" == "gemini":
    print("forced gemini failure", file=sys.stderr)
    raise SystemExit(9)

def choose_reply(prompt: str) -> str:
    if "<draft_model_outputs>" in prompt and "raw answer from codex" in prompt and "raw answer from gemini" in prompt:
        return "cross model ok"
    if "<draft_model_outputs>" in prompt and "raw answer from gemini" in prompt and "forced codex failure" in prompt:
        return "cross model partial ok"
    if "<preset_name>\\nconversation_handoff\\n</preset_name>" in prompt:
        return "handoff ok"
    if "<preset_name>\\nsecurity_assessment\\n</preset_name>" in prompt and "<task_prompt>\\ncheck auth flow\\n</task_prompt>" in prompt:
        return "preset ok"
    if "seed conversation" in prompt:
        return "seed reply"
    return "raw answer from {name}"

print(choose_reply(prompt))
"""
    path.write_text(textwrap.dedent(template.format(name=name)), encoding="utf-8")
    path.chmod(0o755)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        for name in ("claude", "codex", "gemini"):
            write_fake_cli(fake_bin / name, name)

        state_dir = tmp_path / "state"
        os.environ["PATH"] = f"{fake_bin}:{os.environ['PATH']}"
        os.environ["MODEL_MCP_STATE_DIR"] = str(state_dir)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

        server = importlib.import_module("model_mcp.server")

        validation = server.validate_presets()
        assert validation["valid_count"] >= 1, validation
        assert validation["invalid_count"] == 0, validation
        reloaded = server.reload_presets()
        assert reloaded["invalid_count"] == 0, reloaded

        presets = server.list_presets()
        assert any(item["name"] == "security_assessment" for item in presets), presets
        assert any(item["name"] == "cross_model_dissent" for item in presets), presets

        preset = server.get_preset("security_assessment")
        assert preset["category"] == "security", preset
        assert preset["mode"] == "single-model", preset

        preset_result = server.run_preset_review(
            "claude",
            preset_name="security_assessment",
            prompt="check auth flow",
            input_text="Auth system uses bearer tokens.",
        )
        assert preset_result["output"] == "preset ok", preset_result
        assert preset_result["preset_name"] == "security_assessment", preset_result
        assert preset_result["timeout_seconds_used"] >= server.MIN_TIMEOUT_SECONDS, preset_result

        session_data = server.get_session(preset_result["session_id"])
        assert session_data["summary"]["step_count"] == 1, session_data
        assert session_data["session"]["turns"][0]["meta"]["preset_name"] == "security_assessment", session_data

        updated = server.update_session(
            preset_result["session_id"],
            label="Security review",
            notes="Focus on bearer token risks.",
            metadata_patch={"owner": "tests"},
        )
        assert updated["summary"]["label"] == "Security review", updated
        assert updated["summary"]["metadata"]["owner"] == "tests", updated

        sessions = server.list_sessions(provider="claude")
        assert any(item["session_id"] == preset_result["session_id"] for item in sessions), sessions

        source = server.run_provider_turn("claude", prompt="seed conversation")
        assert source["output"] == "seed reply", source

        handoff = server.share_conversation_with_model(
            source_session_id=source["session_id"],
            target_provider="gemini",
            prompt="continue thread",
        )
        assert handoff["output"] == "handoff ok", handoff
        assert handoff["source_session_id"] == source["session_id"], handoff

        cross = server.run_cross_model_preset(
            preset_name="cross_model_dissent",
            prompt="evaluate design",
            draft_providers=["codex", "gemini"],
            judge_provider="claude",
        )
        assert cross["judge_result"]["output"] == "cross model ok", cross
        assert cross["draft_errors"] == [], cross
        assert [item["output"] for item in cross["draft_results"]] == [
            "raw answer from codex",
            "raw answer from gemini",
        ], cross

        partial_cross = server.run_cross_model_preset(
            preset_name="cross_model_dissent",
            prompt="force codex failure",
            draft_providers=["codex", "gemini"],
            judge_provider="claude",
        )
        assert partial_cross["judge_result"]["output"] == "cross model partial ok", partial_cross
        assert [item["provider"] for item in partial_cross["draft_results"]] == ["gemini"], partial_cross
        assert partial_cross["draft_errors"][0]["provider"] == "codex", partial_cross
        assert partial_cross["judge_result"]["timeout_seconds_used"] >= server.MIN_TIMEOUT_SECONDS, partial_cross

        export_path = tmp_path / "exports" / "preset-session.json"
        export = server.export_session(source["session_id"], save_path=str(export_path))
        assert export["saved_to"] == str(export_path), export
        assert export_path.exists(), export

        raw_helper = server.run_helper(provider="claude", prompt="seed conversation")
        assert raw_helper["mode"] == "raw-provider", raw_helper
        assert raw_helper["result"]["output"] == "seed reply", raw_helper
        assert raw_helper["result"]["timeout_seconds_used"] >= server.MIN_TIMEOUT_SECONDS, raw_helper

        preset_helper = server.run_helper(
            provider="claude",
            preset_name="security_assessment",
            prompt="check auth flow",
            input_text="Auth system uses bearer tokens.",
        )
        assert preset_helper["mode"] == "single-model-preset", preset_helper
        assert preset_helper["result"]["output"] == "preset ok", preset_helper

        handoff_helper = server.run_helper(
            preset_name="conversation_handoff",
            source_session_id=source["session_id"],
            target_provider="gemini",
            prompt="continue thread",
        )
        assert handoff_helper["mode"] == "conversation-handoff", handoff_helper
        assert handoff_helper["result"]["output"] == "handoff ok", handoff_helper

        cross_helper = server.run_helper(
            preset_name="cross_model_dissent",
            prompt="evaluate design",
            draft_providers=["codex", "gemini"],
            judge_provider="claude",
        )
        assert cross_helper["mode"] == "cross-model-preset", cross_helper
        assert cross_helper["result"]["judge_result"]["output"] == "cross model ok", cross_helper

        async_job = server.start_async_job(
            provider="claude",
            prompt="seed conversation",
            save_session=True,
        )
        assert async_job["status"] == "queued", async_job
        job_id = async_job["job_id"]

        deadline = time.time() + 5
        job_payload = server.get_job(job_id)
        while job_payload["summary"]["status"] in {"queued", "running"} and time.time() < deadline:
            time.sleep(0.05)
            job_payload = server.get_job(job_id)
        assert job_payload["summary"]["status"] == "completed", job_payload
        assert job_payload["job"]["result"]["mode"] == "raw-provider", job_payload
        assert job_payload["job"]["result"]["result"]["output"] == "seed reply", job_payload

        jobs = server.list_jobs()
        assert any(item["job_id"] == job_id for item in jobs), jobs

        deleted = server.delete_session(source["session_id"], delete_exports=True)
        assert deleted["deleted"] is True, deleted
        remaining = server.list_sessions()
        assert all(item["session_id"] != source["session_id"] for item in remaining), remaining

    print("preset logic test passed")


if __name__ == "__main__":
    main()
