from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _build_feature09_sample(workspace_root: Path) -> Path:
    source = workspace_root / "protocol_feature09_sample.cpp"
    binary = workspace_root / "protocol_feature09_sample"
    source.write_text(
        """
        #include <stdio.h>

        static const char *banner = "https://protocol-feature09.local";

        __attribute__((noinline)) int compute_sum(int x) {
            puts("secret-key");
            return x + 7;
        }

        int main() {
            puts(banner);
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


def _extract_payload(result) -> dict:
    payload = result.structuredContent
    assert isinstance(payload, dict)
    envelope = payload.get("result")
    assert isinstance(envelope, dict)
    return envelope


async def _call_tool(session: ClientSession, name: str, arguments: dict | None = None) -> dict:
    result = await session.call_tool(name, arguments or {})
    assert result.isError is False, f"{name} failed: {result.content!r}"
    return _extract_payload(result)


async def _exercise_protocol_feature09(tmp_path: Path) -> None:
    sample = _build_feature09_sample(tmp_path)
    source_root = Path(__file__).resolve().parents[1] / "src"

    server = StdioServerParameters(
        command="python3.11",
        args=["-m", "reversing_mcp.server"],
        cwd=tmp_path,
        env={
            "PYTHONPATH": str(source_root),
            "REVERSING_MCP_WORKSPACE_ROOT": str(tmp_path),
            "REVERSING_MCP_LOG_LEVEL": "WARNING",
        },
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            for required in (
                "ingest_and_triage_artifact",
                "analyze_and_summarize",
                "hunt_interesting_regions",
                "trace_capability",
                "prepare_patch_plan",
                "artifact_relationship_brief",
            ):
                assert required in tool_names

            created = await _call_tool(session, "create_session", {"name": "protocol-09"})
            session_id = created["result"]["session"]["session_id"]

            ingested = await _call_tool(
                session,
                "ingest_and_triage_artifact",
                {
                    "session_id": session_id,
                    "path": str(sample),
                    "display_name": "protocol_feature09",
                    "analyze": False,
                    "verbosity": "brief",
                    "token_budget_hint": 400,
                },
            )
            artifact_id = ingested["result"]["artifact"]["artifact_id"]

            summary = await _call_tool(
                session,
                "analyze_and_summarize",
                {
                    "session_id": session_id,
                    "artifact_id": artifact_id,
                    "focus": "malware",
                    "wait_timeout_seconds": 30.0,
                    "verbosity": "deep",
                    "token_budget_hint": 400,
                },
            )
            assert summary["result"]["analysis_status"] == "completed"
            assert summary["result"]["response_profile"]["effective_verbosity"] == "brief"

            hunt = await _call_tool(
                session,
                "hunt_interesting_regions",
                {
                    "session_id": session_id,
                    "artifact_id": artifact_id,
                    "objective": "malware",
                    "limit": 5,
                },
            )
            function_id = hunt["result"]["interesting_regions"]["top_functions"]["items"][0]["function_id"]

            trace = await _call_tool(
                session,
                "trace_capability",
                {
                    "session_id": session_id,
                    "artifact_id": artifact_id,
                    "target": {"function_id": function_id},
                    "include_raw_sections": True,
                },
            )
            assert trace["result"]["raw_sections"]["instruction_preview"]

            patch_plan = await _call_tool(
                session,
                "prepare_patch_plan",
                {
                    "session_id": session_id,
                    "artifact_id": artifact_id,
                    "objective": "bypass_guard",
                    "target": {"function_id": function_id},
                    "min_code_cave_size": 8,
                },
            )
            assert patch_plan["result"]["patch_plan"]["candidate_patch_points"]

            patched = await _call_tool(
                session,
                "patch_artifact_bytes",
                {
                    "session_id": session_id,
                    "artifact_id": artifact_id,
                    "input_kind": "file_offset",
                    "value": 0,
                    "bytes_hex": "9090",
                    "output_path": str(tmp_path / "protocol-feature09-patched.bin"),
                    "attach_to_session": True,
                },
            )
            assert patched["result"]["attached_artifact"]["artifact_id"]

            relationship = await _call_tool(
                session,
                "artifact_relationship_brief",
                {
                    "session_id": session_id,
                    "artifact_id": artifact_id,
                    "focus": "diffing",
                },
            )
            assert relationship["result"]["relationship_brief"]["diff_candidates"]["items"]


def test_mcp_protocol_feature09_surface(tmp_path: Path) -> None:
    asyncio.run(_exercise_protocol_feature09(tmp_path))
