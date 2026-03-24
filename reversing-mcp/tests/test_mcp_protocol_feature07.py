from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _build_feature07_sample(workspace_root: Path) -> Path:
    source = workspace_root / "protocol_feature07_sample.cpp"
    binary = workspace_root / "protocol_feature07_sample"
    source.write_text(
        """
        #include <stdio.h>

        struct Config {
            int threshold;
            const char *label;
        };

        static Config g_config = {9, "PROTO-F07"};

        __attribute__((noinline)) int compute_sum(int x) {
            return x + g_config.threshold;
        }

        int main() {
            puts(g_config.label);
            return compute_sum(3);
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


async def _exercise_protocol_feature07(tmp_path: Path) -> None:
    sample = _build_feature07_sample(tmp_path)
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
                "patch_artifact_bytes",
                "patch_artifact_assembly",
                "find_code_caves",
                "edit_artifact_metadata",
                "import_type_definitions",
                "export_command_log",
                "export_analysis_report",
                "list_artifact_dependencies",
                "correlate_session_artifacts",
                "diff_artifacts",
            ):
                assert required in tool_names

            created = await _call_tool(session, "create_session", {"name": "protocol-07"})
            session_id = created["result"]["session"]["session_id"]

            added = await _call_tool(session, "add_artifact", {"session_id": session_id, "path": str(sample), "display_name": "protocol_f07"})
            artifact_id = added["result"]["artifact_id"]

            started = await _call_tool(session, "start_artifact_analysis", {"session_id": session_id, "artifact_id": artifact_id})
            job_id = started["result"]["job_id"]
            for _ in range(200):
                polled = await _call_tool(session, "get_job", {"job_id": job_id})
                if polled["result"]["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.05)
            assert polled["result"]["status"] == "completed"

            functions = await _call_tool(session, "list_artifact_functions", {"session_id": session_id, "artifact_id": artifact_id, "query": "compute_sum"})
            function = functions["result"]["items"][0]

            metadata_edit = await _call_tool(
                session,
                "edit_artifact_metadata",
                {
                    "session_id": session_id,
                    "artifact_id": artifact_id,
                    "edit_kind": "calling_convention",
                    "target": {"function_id": function["function_id"]},
                    "value": {"name": "sysv_abi_override"},
                },
            )
            assert metadata_edit["result"]["edit_kind"] == "calling_convention"

            imported = await _call_tool(
                session,
                "import_type_definitions",
                {
                    "session_id": session_id,
                    "artifact_id": artifact_id,
                    "source_format": "c_header",
                    "source_text": "typedef struct Settings { int enabled; } Settings;",
                },
            )
            assert imported["result"]["imported"]["named_types"]["structs"]["Settings"]["name"] == "Settings"

            caves = await _call_tool(session, "find_code_caves", {"session_id": session_id, "artifact_id": artifact_id, "min_size": 8})
            assert "summary" in caves["result"]["code_caves"]

            byte_patch_path = tmp_path / "protocol-byte-patch.bin"
            byte_patch = await _call_tool(
                session,
                "patch_artifact_bytes",
                {
                    "session_id": session_id,
                    "artifact_id": artifact_id,
                    "input_kind": "file_offset",
                    "value": 0,
                    "bytes_hex": "9090",
                    "output_path": str(byte_patch_path),
                    "attach_to_session": True,
                    "display_name": "protocol-byte-patch.bin",
                },
            )
            patched_artifact_id = byte_patch["result"]["attached_artifact"]["artifact_id"]

            asm_patch_path = tmp_path / "protocol-asm-patch.bin"
            asm_patch = await _call_tool(
                session,
                "patch_artifact_assembly",
                {
                    "session_id": session_id,
                    "artifact_id": artifact_id,
                    "input_kind": "file_offset",
                    "value": 0,
                    "assembly": "nop; ret",
                    "isa": "x86_64",
                    "output_path": str(asm_patch_path),
                    "attach_to_session": True,
                    "display_name": "protocol-asm-patch.bin",
                },
            )
            assert asm_patch["result"]["assembly_backend"]["isa"] == "x86_64"

            arm_patch = await _call_tool(
                session,
                "patch_artifact_assembly",
                {
                    "session_id": session_id,
                    "artifact_id": artifact_id,
                    "input_kind": "file_offset",
                    "value": 4,
                    "assembly": "nop; ret",
                    "isa": "aarch64",
                    "output_path": str(tmp_path / "protocol-arm-patch.bin"),
                    "attach_to_session": False,
                },
            )
            assert arm_patch["result"]["assembly_backend"]["isa"] == "aarch64"

            dependencies = await _call_tool(session, "list_artifact_dependencies", {"session_id": session_id, "artifact_id": artifact_id})
            assert "imports" in dependencies["result"]["dependencies"]

            correlations = await _call_tool(
                session,
                "correlate_session_artifacts",
                {"session_id": session_id, "artifact_ids": [artifact_id, patched_artifact_id]},
            )
            assert isinstance(correlations["result"]["correlations"]["items"], list)

            diff = await _call_tool(
                session,
                "diff_artifacts",
                {"session_id": session_id, "left_artifact_id": artifact_id, "right_artifact_id": patched_artifact_id},
            )
            assert diff["result"]["diff"]["available_levels"]["structural"] is True

            report = await _call_tool(
                session,
                "export_analysis_report",
                {"session_id": session_id, "artifact_id": artifact_id, "format": "json", "output_path": str(tmp_path / "report.json")},
            )
            assert report["result"]["output"]["path"]

            command_log = await _call_tool(
                session,
                "export_command_log",
                {"session_id": session_id, "format": "text", "output_path": str(tmp_path / "command-log.txt")},
            )
            assert command_log["result"]["output"]["path"]


def test_mcp_protocol_feature07_surface(tmp_path: Path) -> None:
    asyncio.run(_exercise_protocol_feature07(tmp_path))
