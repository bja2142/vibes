#!/usr/bin/env python3

from __future__ import annotations

import os

import anyio
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main() -> None:
    port = os.getenv("MODEL_MCP_PORT", "7777")
    results: dict[str, dict] = {}
    async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as streams:
        read_stream, write_stream, _get_session_id = streams
        session = ClientSession(read_stream, write_stream)
        async with session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = sorted(tool.name for tool in tools.tools)
            expected = {
                "ask_claude",
                "ask_codex",
                "ask_gemini",
                "list_presets",
                "validate_presets",
                "reload_presets",
                "get_preset",
                "run_preset_review",
                "run_cross_model_preset",
                "share_conversation_with_model",
                "list_sessions",
                "get_session",
                "update_session",
                "export_session",
                "delete_session",
                "start_async_job",
                "get_job",
                "list_jobs",
                "cancel_job",
                "run_helper",
            }
            missing = expected.difference(tool_names)
            if missing:
                raise RuntimeError(f"Missing expected tools: {sorted(missing)}")

            validation = await session.call_tool("validate_presets", {})
            validation_payload = validation.structuredContent or {}
            if validation_payload.get("invalid_count") != 0:
                raise RuntimeError(f"validate_presets reported invalid presets: {validation_payload!r}")

            presets = await session.call_tool("list_presets", {})
            preset_payload = presets.structuredContent or {}
            preset_items = preset_payload.get("items", [])
            if not any(item.get("name") == "security_assessment" for item in preset_items):
                raise RuntimeError("list_presets did not include security_assessment")

            helper = await session.call_tool(
                "run_helper",
                {
                    "provider": "claude",
                    "prompt": "Reply with exactly HELPER",
                },
            )
            helper_payload = helper.structuredContent or {}
            if helper_payload.get("mode") != "raw-provider":
                raise RuntimeError(f"run_helper returned unexpected mode: {helper_payload!r}")
            helper_result = helper_payload.get("result") or {}
            if str(helper_result.get("output", "")).strip() != "HELPER":
                raise RuntimeError(f"run_helper returned unexpected output: {helper_payload!r}")
            if int(helper_result.get("timeout_seconds_used", 0)) < 240:
                raise RuntimeError(f"run_helper returned an unexpectedly low timeout_seconds_used: {helper_payload!r}")

            async_started = await session.call_tool(
                "start_async_job",
                {
                    "provider": "claude",
                    "prompt": "Reply with exactly ASYNC",
                },
            )
            async_started_payload = async_started.structuredContent or {}
            async_job_id = str(async_started_payload.get("job_id", "")).strip()
            if not async_job_id:
                raise RuntimeError(f"start_async_job did not return a job_id: {async_started_payload!r}")

            async_payload = {}
            for _ in range(50):
                await anyio.sleep(0.2)
                async_result = await session.call_tool(
                    "get_job",
                    {
                        "job_id": async_job_id,
                    },
                )
                async_payload = async_result.structuredContent or {}
                async_summary = async_payload.get("summary") or {}
                if async_summary.get("status") in {"completed", "failed", "cancelled"}:
                    break
            async_summary = async_payload.get("summary") or {}
            if async_summary.get("status") != "completed":
                raise RuntimeError(f"async job did not complete successfully: {async_payload!r}")
            async_job = async_payload.get("job") or {}
            async_result_payload = async_job.get("result") or {}
            if async_result_payload.get("mode") != "raw-provider":
                raise RuntimeError(f"async job returned unexpected mode: {async_payload!r}")
            async_inner = async_result_payload.get("result") or {}
            if str(async_inner.get("output", "")).strip() != "ASYNC":
                raise RuntimeError(f"async job returned unexpected output: {async_payload!r}")

            listed_jobs = await session.call_tool("list_jobs", {})
            listed_jobs_payload = listed_jobs.structuredContent or {}
            listed_job_items = listed_jobs_payload.get("items", [])
            if not any(item.get("job_id") == async_job_id for item in listed_job_items):
                raise RuntimeError("list_jobs did not include the async job")

            for tool_name in ("ask_claude", "ask_codex", "ask_gemini"):
                result = await session.call_tool(
                    tool_name,
                    {
                        "prompt": "Reply with exactly PONG",
                    },
                )
                payload = result.structuredContent or {}
                output = str(payload.get("output", "")).strip()
                session_id = str(payload.get("session_id", "")).strip()
                step = payload.get("step")
                if output != "PONG":
                    raise RuntimeError(f"{tool_name} returned unexpected output: {output!r}")
                if not session_id:
                    raise RuntimeError(f"{tool_name} did not return a session_id")
                if step != 1:
                    raise RuntimeError(f"{tool_name} returned unexpected step: {step!r}")
                if int(payload.get("timeout_seconds_used", 0)) < 240:
                    raise RuntimeError(f"{tool_name} returned an unexpectedly low timeout_seconds_used")
                results[tool_name] = payload

            claude_session_id = str(results["ask_claude"]["session_id"])
            continued = await session.call_tool(
                "ask_claude",
                {
                    "prompt": "Reply with exactly PONG2",
                    "session_id": claude_session_id,
                    "save_session": True,
                },
            )
            continued_payload = continued.structuredContent or {}
            continued_output = str(continued_payload.get("output", "")).strip()
            if continued_output != "PONG2":
                raise RuntimeError(
                    f"ask_claude continuation returned unexpected output: {continued_output!r}"
                )
            if continued_payload.get("step") != 2:
                raise RuntimeError(
                    f"ask_claude continuation returned unexpected step: {continued_payload.get('step')!r}"
                )
            saved_to = str(continued_payload.get("saved_to", "")).strip()
            if not saved_to:
                raise RuntimeError("ask_claude continuation did not return a save path")
            if not os.path.exists(saved_to):
                raise RuntimeError(f"Expected saved session export at {saved_to}")

            fetched = await session.call_tool(
                "get_session",
                {
                    "session_id": claude_session_id,
                },
            )
            fetched_payload = fetched.structuredContent or {}
            summary = fetched_payload.get("summary", {})
            if summary.get("step_count") != 2:
                raise RuntimeError(f"get_session returned unexpected step_count: {summary!r}")

            listed = await session.call_tool("list_sessions", {"provider": "claude"})
            listed_payload = listed.structuredContent or {}
            listed_items = listed_payload.get("items", [])
            if not any(item.get("session_id") == claude_session_id for item in listed_items):
                raise RuntimeError("list_sessions did not include the Claude session")

            updated = await session.call_tool(
                "update_session",
                {
                    "session_id": claude_session_id,
                    "label": "Smoke Test Session",
                    "notes": "Created by test_mcp_client",
                    "metadata_patch": {"suite": "smoke"},
                },
            )
            updated_payload = updated.structuredContent or {}
            updated_summary = updated_payload.get("summary", {})
            if updated_summary.get("label") != "Smoke Test Session":
                raise RuntimeError(f"update_session did not persist label: {updated_payload!r}")

            deleted = await session.call_tool(
                "delete_session",
                {
                    "session_id": claude_session_id,
                    "delete_exports": True,
                },
            )
            deleted_payload = deleted.structuredContent or {}
            if deleted_payload.get("deleted") is not True:
                raise RuntimeError(f"delete_session failed: {deleted_payload!r}")

    print("mcp client smoke test passed")


if __name__ == "__main__":
    anyio.run(main)
