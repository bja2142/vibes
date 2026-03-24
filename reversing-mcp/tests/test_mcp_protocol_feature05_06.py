from __future__ import annotations

import asyncio
import json
import subprocess
import zipfile
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _build_semantic_sample(workspace_root: Path) -> Path:
    source = workspace_root / "protocol_feature05_sample.cpp"
    binary = workspace_root / "protocol_feature05_sample"
    source.write_text(
        """
        #include <stdio.h>

        struct Config {
            int threshold;
            const char *label;
        };

        static int global_flag = 0;
        static Config g_config = {2, "CFG-LABEL"};

        class Base {
        public:
            virtual int run(int x) { return x + 1; }
            virtual ~Base() = default;
        };

        class Derived : public Base {
        public:
            int run(int x) override { return x * 2; }
        };

        static int inc(int x) { return x + 1; }
        static int dec(int x) { return x - 1; }

        using Fn = int (*)(int);
        static Fn jump_table[2] = {inc, dec};

        int choose_path(int idx, int value) {
            if (idx < 0 || idx > 1) {
                return -1;
            }
            return jump_table[idx](value);
        }

        int score_value(int argc, int value) {
            int local = value + 7;
            if (argc > g_config.threshold) {
                global_flag = 1;
            }
            return local;
        }

        int maybe_throw(int x) {
            try {
                if (x == 13) {
                    throw x;
                }
            } catch (...) {
                return -13;
            }
            return x;
        }

        int main(int argc, char **argv) {
            Derived derived;
            int chosen = choose_path(argc & 1, 10);
            int scored = score_value(argc, chosen);
            int value = derived.run(scored);
            if (global_flag) {
                puts(g_config.label);
            }
            return maybe_throw(value);
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


def _build_signature_sample(workspace_root: Path) -> tuple[Path, Path]:
    source = workspace_root / "protocol_feature06_sample.cpp"
    binary = workspace_root / "protocol_feature06_sample"
    overlay_binary = workspace_root / "protocol_feature06_overlay"
    archive_path = workspace_root / "protocol_feature06_payload.zip"
    source.write_text(
        """
        #include <stdio.h>
        #include <string.h>

        static const char *banner = "FEATURE06-BANNER";
        static const char *encoded_base64 = "U0VDUkVUX01FU1NBR0U=";
        static const char *encoded_hex = "48656c6c6f20576f726c64";
        static const unsigned tea_delta = 0x9e3779b9u;

        __attribute__((noinline)) unsigned mix(unsigned value) {
            return value + tea_delta;
        }

        int main() {
            puts(banner);
            return (int)mix((unsigned)strlen(encoded_base64) + (unsigned)strlen(encoded_hex));
        }
        """,
        encoding="utf-8",
    )
    subprocess.run(
        ["c++", "-g", "-O0", "-fno-inline", str(source), "-o", str(binary)],
        check=True,
        cwd=workspace_root,
    )
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("docs/readme.txt", b"overlay hello")
        archive.writestr("nested/config.txt", b"embedded config")
    overlay_binary.write_bytes(binary.read_bytes() + archive_path.read_bytes())
    return overlay_binary, archive_path


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


async def _call_tool_error(session: ClientSession, name: str, arguments: dict | None = None):
    result = await session.call_tool(name, arguments or {})
    if result.isError:
        return result
    payload = result.structuredContent
    assert isinstance(payload, dict)
    envelope = payload.get("result")
    assert isinstance(envelope, dict)
    assert envelope.get("ok") is False, f"{name} unexpectedly succeeded"
    return result


async def _exercise_protocol_surface(tmp_path: Path) -> None:
    semantic_sample = _build_semantic_sample(tmp_path)
    overlay_binary, archive_path = _build_signature_sample(tmp_path)
    export_path = tmp_path / "exports" / "curated.json"
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
                "get_call_graph",
                "get_control_flow_graph",
                "get_function_variables",
                "get_stack_frame",
                "get_constant_propagation",
                "get_type_information",
                "recover_types",
                "inspect_data_segments",
                "get_indirect_flows",
                "get_exception_metadata",
                "get_calling_convention",
                "get_intermediate_representation",
                "get_runtime_metadata",
                "slice_data_flow",
                "identify_system_calls",
                "navigate_neighborhood",
                "prioritize_functions",
                "classify_functions",
                "save_workflow_item",
                "list_workflow_items",
                "export_curated_analysis",
                "batch_query_artifacts",
                "scan_with_yara",
                "fingerprint_compiler_toolchain",
                "detect_packer",
                "calculate_entropy",
                "deobfuscate_strings",
                "extract_resources",
                "carve_embedded_artifacts",
                "get_artifact_relationships",
                "detect_crypto_constants",
                "recognize_library_code",
            ):
                assert required in tool_names

            created = await _call_tool(session, "create_session", {"name": "protocol-05-06"})
            session_id = created["result"]["session"]["session_id"]

            semantic_added = await _call_tool(
                session,
                "add_artifact",
                {"session_id": session_id, "path": str(semantic_sample), "display_name": "semantic_sample"},
            )
            semantic_artifact = semantic_added["result"]["artifact_id"]

            overlay_added = await _call_tool(
                session,
                "add_artifact",
                {"session_id": session_id, "path": str(overlay_binary), "display_name": "signature_overlay"},
            )
            overlay_artifact = overlay_added["result"]["artifact_id"]

            archive_added = await _call_tool(
                session,
                "add_artifact",
                {"session_id": session_id, "path": str(archive_path), "display_name": "payload_zip"},
            )
            archive_artifact = archive_added["result"]["artifact_id"]

            semantic_job = await _call_tool(session, "start_artifact_analysis", {"session_id": session_id, "artifact_id": semantic_artifact})
            semantic_job_id = semantic_job["result"]["job_id"]
            for _ in range(200):
                polled = await _call_tool(session, "get_job", {"job_id": semantic_job_id})
                if polled["result"]["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.05)
            assert polled["result"]["status"] == "completed"

            overlay_job = await _call_tool(session, "start_artifact_analysis", {"session_id": session_id, "artifact_id": overlay_artifact})
            overlay_job_id = overlay_job["result"]["job_id"]
            for _ in range(200):
                polled = await _call_tool(session, "get_job", {"job_id": overlay_job_id})
                if polled["result"]["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.05)
            assert polled["result"]["status"] == "completed"

            main_fn = (await _call_tool(session, "list_artifact_functions", {"session_id": session_id, "artifact_id": semantic_artifact, "query": "main"}))["result"]["items"][0]
            choose_path = (await _call_tool(session, "list_artifact_functions", {"session_id": session_id, "artifact_id": semantic_artifact, "query": "choose_path"}))["result"]["items"][0]
            score_value = (await _call_tool(session, "list_artifact_functions", {"session_id": session_id, "artifact_id": semantic_artifact, "query": "score_value"}))["result"]["items"][0]

            call_graph = await _call_tool(
                session,
                "get_call_graph",
                {"session_id": session_id, "artifact_id": semantic_artifact, "function_id": main_fn["function_id"], "depth": 2},
            )
            assert call_graph["result"]["edges"]

            cfg = await _call_tool(
                session,
                "get_control_flow_graph",
                {"session_id": session_id, "artifact_id": semantic_artifact, "function_id": main_fn["function_id"]},
            )
            assert cfg["result"]["control_flow_graph"]["nodes"]

            variables = await _call_tool(
                session,
                "get_function_variables",
                {"session_id": session_id, "artifact_id": semantic_artifact, "function_id": score_value["function_id"]},
            )
            assert variables["result"]["variables"]["arguments"]

            stack_frame = await _call_tool(
                session,
                "get_stack_frame",
                {"session_id": session_id, "artifact_id": semantic_artifact, "function_id": score_value["function_id"]},
            )
            assert stack_frame["result"]["stack_frame"]["slots"]

            constants = await _call_tool(
                session,
                "get_constant_propagation",
                {"session_id": session_id, "artifact_id": semantic_artifact, "function_id": score_value["function_id"]},
            )
            assert any(item["value"] == 7 for item in constants["result"]["constant_propagation"]["immediates"])

            type_info = await _call_tool(session, "get_type_information", {"session_id": session_id, "artifact_id": semantic_artifact})
            assert type_info["result"]["type_information"]["function_signatures"]

            recovered_types = await _call_tool(session, "recover_types", {"session_id": session_id, "artifact_id": semantic_artifact})
            assert recovered_types["result"]["recovered_types"]["items"]

            data_segments = await _call_tool(session, "inspect_data_segments", {"session_id": session_id, "artifact_id": semantic_artifact})
            assert data_segments["result"]["data_segments"]["typed_views"]

            indirect_flows = await _call_tool(
                session,
                "get_indirect_flows",
                {"session_id": session_id, "artifact_id": semantic_artifact, "function_id": choose_path["function_id"]},
            )
            assert indirect_flows["result"]["indirect_flows"]["items"]

            exception_metadata = await _call_tool(session, "get_exception_metadata", {"session_id": session_id, "artifact_id": semantic_artifact})
            assert exception_metadata["result"]["exception_metadata"]["available"] is True

            calling_convention = await _call_tool(
                session,
                "get_calling_convention",
                {"session_id": session_id, "artifact_id": semantic_artifact, "function_id": main_fn["function_id"]},
            )
            assert "calling_convention" in calling_convention["result"]

            ir = await _call_tool(
                session,
                "get_intermediate_representation",
                {
                    "session_id": session_id,
                    "artifact_id": semantic_artifact,
                    "function_id": score_value["function_id"],
                    "limit_blocks": 4,
                    "limit_statements": 8,
                },
            )
            assert ir["result"]["intermediate_representation"]["blocks"]

            runtime_metadata = await _call_tool(session, "get_runtime_metadata", {"session_id": session_id, "artifact_id": semantic_artifact})
            assert runtime_metadata["result"]["runtime_metadata"]["languages"]

            data_slice = await _call_tool(
                session,
                "slice_data_flow",
                {"session_id": session_id, "artifact_id": semantic_artifact, "function_id": score_value["function_id"], "radius": 4},
            )
            assert data_slice["result"]["slice"]["items"]

            syscalls = await _call_tool(
                session,
                "identify_system_calls",
                {"session_id": session_id, "artifact_id": semantic_artifact, "function_id": main_fn["function_id"]},
            )
            assert isinstance(syscalls["result"]["system_calls"], list)

            neighborhood = await _call_tool(
                session,
                "navigate_neighborhood",
                {"session_id": session_id, "artifact_id": semantic_artifact, "function_id": main_fn["function_id"], "depth": 2, "radius": 2},
            )
            assert neighborhood["result"]["neighborhood"]["callees"]

            prioritized = await _call_tool(
                session,
                "prioritize_functions",
                {"session_id": session_id, "artifact_id": semantic_artifact, "min_score": 10, "limit": 10},
            )
            assert prioritized["result"]["items"]

            classified = await _call_tool(
                session,
                "classify_functions",
                {"session_id": session_id, "artifact_id": semantic_artifact, "include_tags": ["control_flow"], "limit": 10},
            )
            assert classified["result"]["items"]

            saved = await _call_tool(
                session,
                "save_workflow_item",
                {
                    "session_id": session_id,
                    "kind": "bookmark",
                    "target": {"kind": "function", "object_id": choose_path["function_id"]},
                    "value": {"label": "check jump table"},
                },
            )
            annotation_id = saved["result"]["annotation_id"]

            workflow_items = await _call_tool(session, "list_workflow_items", {"session_id": session_id, "kind": "bookmark"})
            assert workflow_items["result"]["items"]

            curated = await _call_tool(
                session,
                "export_curated_analysis",
                {
                    "session_id": session_id,
                    "artifact_id": semantic_artifact,
                    "function_ids": [main_fn["function_id"], choose_path["function_id"]],
                    "annotation_ids": [annotation_id],
                    "output_path": str(export_path),
                },
            )
            assert curated["result"]["path"] == str(export_path)
            exported = json.loads(export_path.read_text(encoding="utf-8"))
            assert exported["functions"]

            batch = await _call_tool(
                session,
                "batch_query_artifacts",
                {"session_id": session_id, "operation": "prioritize_functions", "min_score": 10, "limit": 5},
            )
            assert any(item["status"] == "completed" for item in batch["result"]["items"])

            yara_scan = await _call_tool(session, "scan_with_yara", {"session_id": session_id, "artifact_id": overlay_artifact})
            assert yara_scan["result"]["total_matches"] >= 1

            fingerprint = await _call_tool(session, "fingerprint_compiler_toolchain", {"session_id": session_id, "artifact_id": overlay_artifact})
            assert fingerprint["result"]["fingerprints"]["matches"]

            packer = await _call_tool(session, "detect_packer", {"session_id": session_id, "artifact_id": overlay_artifact})
            assert packer["result"]["packer_detection"]["overlay"]["present"] is True

            entropy = await _call_tool(session, "calculate_entropy", {"session_id": session_id, "artifact_id": overlay_artifact})
            assert entropy["result"]["entropy"]["whole_file"]["entropy"] > 0

            deobfuscated = await _call_tool(session, "deobfuscate_strings", {"session_id": session_id, "artifact_id": overlay_artifact, "limit": 10})
            assert deobfuscated["result"]["deobfuscated_strings"]["backend"] == "heuristic-fallback"
            decoded_values = {item["decoded_value"] for item in deobfuscated["result"]["deobfuscated_strings"]["items"]}
            assert "SECRET_MESSAGE" in decoded_values
            assert "Hello World" in decoded_values

            crypto = await _call_tool(session, "detect_crypto_constants", {"session_id": session_id, "artifact_id": overlay_artifact})
            assert crypto["result"]["crypto_constants"]["items"]

            libraries = await _call_tool(session, "recognize_library_code", {"session_id": session_id, "artifact_id": overlay_artifact})
            assert libraries["result"]["library_recognition"]["libraries"]

            carved = await _call_tool(
                session,
                "carve_embedded_artifacts",
                {"session_id": session_id, "artifact_id": overlay_artifact, "attach_to_session": True},
            )
            assert carved["result"]["items"]
            assert carved["result"]["attached_artifacts"]

            relationships = await _call_tool(session, "get_artifact_relationships", {"session_id": session_id, "artifact_id": overlay_artifact})
            assert relationships["result"]["children"]

            related_scan = await _call_tool(
                session,
                "scan_with_yara",
                {"session_id": session_id, "artifact_id": overlay_artifact, "include_related": True},
            )
            assert len(related_scan["result"]["items"]) >= 2

            extracted = await _call_tool(
                session,
                "extract_resources",
                {"session_id": session_id, "artifact_id": archive_artifact, "attach_to_session": True},
            )
            assert extracted["result"]["items"]
            assert extracted["result"]["attached_artifacts"]

            instruction_mode_error = await _call_tool_error(
                session,
                "set_artifact_instruction_mode",
                {"session_id": session_id, "artifact_id": semantic_artifact, "mode": "thumb"},
            )
            assert instruction_mode_error.content


def test_protocol_level_mcp_feature_05_and_06_surface(tmp_path: Path) -> None:
    asyncio.run(_exercise_protocol_surface(tmp_path))
