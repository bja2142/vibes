from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
import zipfile
from pathlib import Path

import reversing_mcp.server as server
from reversing_mcp.app import ReversingMCPApp, TOOL_CATALOG

EXPECTED_MCP_TOOLS = {
    "describe_tools",
    "get_capabilities",
    "get_runtime_policies",
    "run_parser_probe",
    "create_session",
    "load_session",
    "list_sessions",
    "destroy_session",
    "update_session_settings",
    "add_artifact",
    "triage_artifact",
    "list_artifact_strings",
    "translate_artifact_address",
    "list_artifact_children",
    "lookup_external_enrichment",
    "scan_with_yara",
    "fingerprint_compiler_toolchain",
    "detect_packer",
    "calculate_entropy",
    "deobfuscate_strings",
    "extract_resources",
    "carve_embedded_artifacts",
    "get_artifact_relationships",
    "start_artifact_analysis",
    "get_analysis_synopsis",
    "list_artifact_symbols",
    "list_artifact_functions",
    "get_artifact_instruction_mode",
    "set_artifact_instruction_mode",
    "disassemble_function",
    "disassemble_range",
    "decompile_function",
    "read_artifact_bytes",
    "list_artifact_xrefs",
    "search_artifact",
    "get_artifact_linkage",
    "get_artifact_debug_info",
    "detect_crypto_constants",
    "recognize_library_code",
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
    "list_artifacts",
    "remove_artifact",
    "register_provisional_function",
    "register_provisional_string",
    "get_object_reference",
    "put_annotation",
    "list_annotations",
    "get_annotation_history",
    "revert_annotation",
    "create_session_snapshot",
    "list_session_snapshots",
    "restore_session_snapshot",
    "start_artifact_reanalysis",
    "get_job",
    "list_jobs",
    "cancel_job",
    "export_session_state",
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
    "ingest_and_triage_artifact",
    "analyze_and_summarize",
    "hunt_interesting_regions",
    "trace_capability",
    "prepare_patch_plan",
    "artifact_relationship_brief",
    "ghidra_decompile",
    "ghidra_analyze",
    "run_ghidra_script",
    "export_dynamic_manifest",
}


def _exposed_tool_names() -> set[str]:
    text = Path(server.__file__).read_text(encoding="utf-8")
    return set(re.findall(r'@expose\("([^"]+)"\)', text))


def _session_id(payload: dict) -> str:
    return payload["result"]["session"]["session_id"]


def _artifact_id(payload: dict) -> str:
    return payload["result"]["artifact_id"]


def _job_id(payload: dict) -> str:
    return payload["result"]["job_id"]


def _annotation_id(payload: dict) -> str:
    return payload["result"]["annotation_id"]


async def _acall(invoked: set[str], tool_name: str, *args, **kwargs) -> dict:
    payload = await getattr(server, f"_{tool_name}")(*args, **kwargs)
    invoked.add(tool_name)
    assert payload["schema_version"] == "1.0"
    assert payload["server"]["name"] == "reversing-mcp"
    assert payload["provenance"]["tool"] == tool_name
    return payload


async def _wait_for_job(invoked: set[str], job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = await _acall(invoked, "get_job", job_id)
        if payload["result"]["status"] in {"completed", "failed", "cancelled"}:
            return payload
        await asyncio.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not finish before timeout.")


def _build_sample_binary(workspace_root: Path) -> Path:
    source = workspace_root / "mcp_surface_sample.cpp"
    binary = workspace_root / "mcp_surface_sample"
    overlay = workspace_root / "mcp_surface_sample_overlay"
    overlay_zip = workspace_root / "mcp_surface_sample_overlay.zip"
    source.write_text(
        """
        #include <stdio.h>
        #include <string.h>

        namespace demo {
        static const unsigned tea_delta = 0x9e3779b9u;
        int compute_sum(int x) {
            return x + 7 + tea_delta;
        }
        }

        static const char *banner = "FEATURE04-BANNER";
        static const char *encoded_base64 = "U0VDUkVUX01FU1NBR0U=";
        static const char *encoded_hex = "48656c6c6f20576f726c64";

        int main() {
            puts(banner);
            return demo::compute_sum(5 + (int)strlen(encoded_base64) + (int)strlen(encoded_hex));
        }
        """,
        encoding="utf-8",
    )
    subprocess.run(
        ["c++", "-g", "-O0", "-fno-inline", str(source), "-o", str(binary)],
        check=True,
        cwd=workspace_root,
    )
    with zipfile.ZipFile(overlay_zip, "w") as archive:
        archive.writestr("overlay.txt", b"overlay-child")
    overlay.write_bytes(binary.read_bytes() + overlay_zip.read_bytes())
    return overlay


def _build_zip_artifact(workspace_root: Path) -> Path:
    archive_path = workspace_root / "bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("setup.exe", b"MZ-not-real")
        archive.writestr("docs/readme.txt", b"hello")
    return archive_path


def test_mcp_tool_contract_stays_in_sync() -> None:
    exposed_tools = _exposed_tool_names()
    catalog_tools = {item["name"] for item in TOOL_CATALOG}

    assert exposed_tools == EXPECTED_MCP_TOOLS
    assert catalog_tools == EXPECTED_MCP_TOOLS


async def _exercise_all_exposed_tools_via_mcp_wrappers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "APP", ReversingMCPApp(workspace_root=tmp_path))

    invoked: set[str] = set()
    binary = _build_sample_binary(tmp_path)
    archive_path = _build_zip_artifact(tmp_path)

    describe = await _acall(invoked, "describe_tools")
    assert {item["name"] for item in describe["result"]["tools"]} == EXPECTED_MCP_TOOLS

    capabilities = await _acall(invoked, "get_capabilities")
    assert capabilities["ok"] is True
    assert capabilities["result"]["features"]["session_persistence"] is True
    assert set(capabilities["result"]["patching"]["supported_isas"]) >= {"x86", "x86_64", "aarch64", "arm", "thumb"}

    runtime = await _acall(invoked, "get_runtime_policies")
    assert runtime["ok"] is True
    assert runtime["result"]["workspace_root"] == str(tmp_path)

    probe = await _acall(invoked, "run_parser_probe", str(binary))
    assert probe["ok"] is True
    assert probe["result"]["ok"] is True
    assert probe["result"]["result"]["size_bytes"] > 0

    created = await _acall(
        invoked,
        "create_session",
        args=json.dumps(["mcp-primary"]),
        kwargs=json.dumps({"description": "Created through the MCP wrapper argument shim."}),
    )
    assert created["ok"] is True
    session_id = _session_id(created)

    secondary = await _acall(invoked, "create_session", "mcp-secondary")
    assert secondary["ok"] is True
    secondary_session_id = _session_id(secondary)

    loaded = await _acall(invoked, "load_session", name="mcp-primary")
    assert loaded["ok"] is True
    assert loaded["result"]["session"]["session_id"] == session_id

    sessions = await _acall(invoked, "list_sessions")
    assert sessions["ok"] is True
    assert {item["session_id"] for item in sessions["result"]["items"]} >= {session_id, secondary_session_id}

    binary_artifact = _artifact_id(await _acall(invoked, "add_artifact", session_id, str(binary), "mcp-sample"))
    zip_artifact = _artifact_id(await _acall(invoked, "add_artifact", session_id, str(archive_path), "bundle.zip"))

    artifacts = await _acall(invoked, "list_artifacts", session_id)
    assert artifacts["ok"] is True
    assert {item["artifact_id"] for item in artifacts["result"]["items"]} == {binary_artifact, zip_artifact}

    triage = await _acall(invoked, "triage_artifact", session_id, binary_artifact, string_preview_limit=5)
    assert triage["ok"] is True
    assert triage["result"]["file_type"]["format"] == "ELF"

    strings = await _acall(invoked, "list_artifact_strings", session_id, binary_artifact, query="FEATURE04-BANNER", limit=10)
    assert strings["ok"] is True
    assert any(item["value"] == "FEATURE04-BANNER" for item in strings["result"]["items"])

    translated = await _acall(invoked, "translate_artifact_address", session_id, binary_artifact, "file_offset", 0)
    assert translated["ok"] is True
    assert translated["result"]["matches"]

    children = await _acall(invoked, "list_artifact_children", session_id, zip_artifact, limit=10)
    assert children["ok"] is True
    assert {item["name"] for item in children["result"]["items"]} == {"setup.exe", "docs/readme.txt"}

    enrichment = await _acall(invoked, "lookup_external_enrichment", session_id, zip_artifact, providers=["virustotal"], opt_in=True)
    assert enrichment["ok"] is True
    assert enrichment["result"]["status"] == "disabled"

    yara_scan = await _acall(invoked, "scan_with_yara", session_id, binary_artifact)
    assert yara_scan["ok"] is True
    assert yara_scan["result"]["items"]

    fingerprint = await _acall(invoked, "fingerprint_compiler_toolchain", session_id, binary_artifact)
    assert fingerprint["ok"] is True
    assert fingerprint["result"]["fingerprints"]["matches"]

    packer = await _acall(invoked, "detect_packer", session_id, binary_artifact)
    assert packer["ok"] is True
    assert "overlay" in packer["result"]["packer_detection"]

    entropy = await _acall(invoked, "calculate_entropy", session_id, binary_artifact)
    assert entropy["ok"] is True
    assert entropy["result"]["entropy"]["sections"]

    deobfuscated = await _acall(invoked, "deobfuscate_strings", session_id, binary_artifact, limit=10)
    assert deobfuscated["ok"] is True
    assert deobfuscated["result"]["deobfuscated_strings"]["backend"] == "heuristic-fallback"
    assert "items" in deobfuscated["result"]["deobfuscated_strings"]

    extracted = await _acall(invoked, "extract_resources", session_id, zip_artifact)
    assert extracted["ok"] is True
    assert extracted["result"]["items"]

    carved = await _acall(invoked, "carve_embedded_artifacts", session_id, binary_artifact, attach_to_session=True)
    assert carved["ok"] is True
    assert carved["result"]["items"]

    relationships = await _acall(invoked, "get_artifact_relationships", session_id, binary_artifact)
    assert relationships["ok"] is True
    assert "children" in relationships["result"]

    started = await _acall(invoked, "start_artifact_analysis", session_id, binary_artifact)
    assert started["ok"] is True
    analysis_job_id = _job_id(started)

    jobs = await _acall(invoked, "list_jobs", session_id=session_id)
    assert jobs["ok"] is True
    assert any(item["job_id"] == analysis_job_id for item in jobs["result"]["items"])

    finished = await _wait_for_job(invoked, analysis_job_id)
    assert finished["result"]["status"] == "completed"

    synopsis = await _acall(invoked, "get_analysis_synopsis", session_id, binary_artifact)
    assert synopsis["ok"] is True
    assert synopsis["result"]["summary"]["function_count"] > 0

    symbols = await _acall(invoked, "list_artifact_symbols", session_id, binary_artifact, kind="import", query="puts")
    assert symbols["ok"] is True
    assert any(item["name"] == "puts" for item in symbols["result"]["items"])

    functions = await _acall(invoked, "list_artifact_functions", session_id, binary_artifact, query="demo::compute_sum")
    assert functions["ok"] is True
    assert functions["result"]["items"]
    function = functions["result"]["items"][0]

    mode = await _acall(invoked, "get_artifact_instruction_mode", session_id, binary_artifact)
    assert mode["ok"] is True
    current_mode = mode["result"]["instruction_set_mode"]["current"]

    mode_override = await _acall(invoked, "set_artifact_instruction_mode", session_id, binary_artifact, current_mode)
    assert mode_override["ok"] is False
    assert mode_override["error"]["code"] == "instruction_mode_fixed"

    disassembly = await _acall(invoked, "disassemble_function", session_id, binary_artifact, function_id=function["function_id"], limit=20)
    assert disassembly["ok"] is True
    assert disassembly["result"]["items"]

    disassembly_range = await _acall(
        invoked,
        "disassemble_range",
        session_id,
        binary_artifact,
        "virtual_address",
        function["address"],
        16,
        limit=10,
    )
    assert disassembly_range["ok"] is True
    assert disassembly_range["result"]["items"]

    decompilation = await _acall(invoked, "decompile_function", session_id, binary_artifact, function_id=function["function_id"], line_limit=40)
    assert decompilation["ok"] is True
    assert decompilation["result"]["status"] == "completed"

    raw_bytes = await _acall(invoked, "read_artifact_bytes", session_id, binary_artifact, "virtual_address", function["address"], 8)
    assert raw_bytes["ok"] is True
    assert raw_bytes["result"]["bytes_hex"]

    xrefs = await _acall(invoked, "list_artifact_xrefs", session_id, binary_artifact, function_id=function["function_id"])
    assert xrefs["ok"] is True
    assert xrefs["result"]["items"]

    search = await _acall(invoked, "search_artifact", session_id, binary_artifact, "string", query="FEATURE04-BANNER")
    assert search["ok"] is True
    assert search["result"]["items"]

    linkage = await _acall(invoked, "get_artifact_linkage", session_id, binary_artifact)
    assert linkage["ok"] is True
    assert linkage["result"]["linkage"]["imports"]

    debug_info = await _acall(invoked, "get_artifact_debug_info", session_id, binary_artifact)
    assert debug_info["ok"] is True
    assert debug_info["result"]["debug_info"]["available"] is True

    crypto_constants = await _acall(invoked, "detect_crypto_constants", session_id, binary_artifact)
    assert crypto_constants["ok"] is True
    assert "items" in crypto_constants["result"]["crypto_constants"]

    library_recognition = await _acall(invoked, "recognize_library_code", session_id, binary_artifact)
    assert library_recognition["ok"] is True
    assert "libraries" in library_recognition["result"]["library_recognition"]

    call_graph = await _acall(invoked, "get_call_graph", session_id, binary_artifact, function_id=function["function_id"], depth=2)
    assert call_graph["ok"] is True
    assert call_graph["result"]["nodes"]

    cfg = await _acall(invoked, "get_control_flow_graph", session_id, binary_artifact, function_id=function["function_id"])
    assert cfg["ok"] is True
    assert cfg["result"]["control_flow_graph"]["nodes"]

    variables = await _acall(invoked, "get_function_variables", session_id, binary_artifact, function_id=function["function_id"])
    assert variables["ok"] is True
    assert "arguments" in variables["result"]["variables"]

    stack_frame = await _acall(invoked, "get_stack_frame", session_id, binary_artifact, function_id=function["function_id"])
    assert stack_frame["ok"] is True
    assert "slots" in stack_frame["result"]["stack_frame"]

    constants = await _acall(invoked, "get_constant_propagation", session_id, binary_artifact, function_id=function["function_id"])
    assert constants["ok"] is True
    assert "immediates" in constants["result"]["constant_propagation"]

    type_information = await _acall(invoked, "get_type_information", session_id, binary_artifact)
    assert type_information["ok"] is True
    assert type_information["result"]["type_information"]["function_signatures"]

    recovered_types = await _acall(invoked, "recover_types", session_id, binary_artifact)
    assert recovered_types["ok"] is True
    assert "items" in recovered_types["result"]["recovered_types"]

    data_segments = await _acall(invoked, "inspect_data_segments", session_id, binary_artifact)
    assert data_segments["ok"] is True
    assert "sections" in data_segments["result"]["data_segments"]

    indirect_flows = await _acall(invoked, "get_indirect_flows", session_id, binary_artifact, function_id=function["function_id"])
    assert indirect_flows["ok"] is True
    assert "items" in indirect_flows["result"]["indirect_flows"]

    exception_metadata = await _acall(invoked, "get_exception_metadata", session_id, binary_artifact)
    assert exception_metadata["ok"] is True
    assert "available" in exception_metadata["result"]["exception_metadata"]

    calling_convention = await _acall(invoked, "get_calling_convention", session_id, binary_artifact, function_id=function["function_id"])
    assert calling_convention["ok"] is True
    assert calling_convention["result"]["calling_convention"]["name"]

    ir = await _acall(invoked, "get_intermediate_representation", session_id, binary_artifact, function_id=function["function_id"], limit_blocks=2, limit_statements=6)
    assert ir["ok"] is True
    assert ir["result"]["intermediate_representation"]["blocks"]

    runtime_metadata = await _acall(invoked, "get_runtime_metadata", session_id, binary_artifact)
    assert runtime_metadata["ok"] is True
    assert runtime_metadata["result"]["runtime_metadata"]["languages"]

    data_slice = await _acall(invoked, "slice_data_flow", session_id, binary_artifact, function_id=function["function_id"], radius=4)
    assert data_slice["ok"] is True
    assert data_slice["result"]["slice"]["items"]

    syscalls = await _acall(invoked, "identify_system_calls", session_id, binary_artifact, function_id=function["function_id"])
    assert syscalls["ok"] is True
    assert isinstance(syscalls["result"]["system_calls"], list)

    neighborhood = await _acall(invoked, "navigate_neighborhood", session_id, binary_artifact, function_id=function["function_id"], depth=1, radius=1)
    assert neighborhood["ok"] is True
    assert "target_function" in neighborhood["result"]["neighborhood"]

    prioritized = await _acall(invoked, "prioritize_functions", session_id, binary_artifact, min_score=0, limit=5)
    assert prioritized["ok"] is True
    assert prioritized["result"]["items"]

    classified = await _acall(invoked, "classify_functions", session_id, binary_artifact, limit=5)
    assert classified["ok"] is True
    assert classified["result"]["items"]

    provisional_function = await _acall(invoked, "register_provisional_function", session_id, binary_artifact, "entry", "0x401000")
    assert provisional_function["ok"] is True

    provisional_string = await _acall(invoked, "register_provisional_string", session_id, binary_artifact, "hello")
    assert provisional_string["ok"] is True

    object_reference = await _acall(invoked, "get_object_reference", session_id, provisional_function["result"]["function_id"])
    assert object_reference["ok"] is True
    assert object_reference["result"]["function_id"] == provisional_function["result"]["function_id"]

    first_annotation = await _acall(
        invoked,
        "put_annotation",
        session_id,
        {"kind": "artifact", "artifact_id": binary_artifact},
        "tag",
        {"label": "interesting"},
    )
    assert first_annotation["ok"] is True
    annotation_id = _annotation_id(first_annotation)

    second_annotation = await _acall(
        invoked,
        "put_annotation",
        session_id,
        {"kind": "artifact", "artifact_id": binary_artifact},
        "tag",
        {"label": "favorite"},
        annotation_id=annotation_id,
    )
    assert second_annotation["ok"] is True
    assert second_annotation["result"]["revision_count"] == 2

    annotations = await _acall(invoked, "list_annotations", session_id, artifact_id=binary_artifact, target_kind="artifact", annotation_type="tag")
    assert annotations["ok"] is True
    assert [item["annotation_id"] for item in annotations["result"]["items"]] == [annotation_id]

    history = await _acall(invoked, "get_annotation_history", session_id, annotation_id)
    assert history["ok"] is True
    assert len(history["result"]["history"]) == 2

    workflow_item = await _acall(
        invoked,
        "save_workflow_item",
        session_id,
        "note",
        {"kind": "artifact", "artifact_id": binary_artifact},
        {"text": "semantic workflow item"},
    )
    assert workflow_item["ok"] is True

    workflow_items = await _acall(invoked, "list_workflow_items", session_id, kind="note", artifact_id=binary_artifact)
    assert workflow_items["ok"] is True
    assert workflow_items["result"]["items"]

    reverted = await _acall(invoked, "revert_annotation", session_id, annotation_id)
    assert reverted["ok"] is True
    assert reverted["result"]["value"]["label"] == "interesting"

    snapshot = await _acall(invoked, "create_session_snapshot", session_id, "baseline", description="Before mutation")
    assert snapshot["ok"] is True

    snapshots = await _acall(invoked, "list_session_snapshots", session_id)
    assert snapshots["ok"] is True
    assert [item["name"] for item in snapshots["result"]["items"]] == ["baseline"]

    updated = await _acall(invoked, "update_session_settings", session_id, {"mode": "mutated"})
    assert updated["ok"] is True
    assert updated["result"]["settings"]["mode"] == "mutated"

    restored = await _acall(invoked, "restore_session_snapshot", session_id, name="baseline")
    assert restored["ok"] is True
    assert restored["result"]["session"]["settings"] == {}

    reanalysis = await _acall(invoked, "start_artifact_reanalysis", session_id, binary_artifact)
    assert reanalysis["ok"] is True
    reanalysis_job_id = _job_id(reanalysis)

    jobs_after_reanalysis = await _acall(invoked, "list_jobs", session_id=session_id)
    assert jobs_after_reanalysis["ok"] is True
    assert any(item["job_id"] == reanalysis_job_id for item in jobs_after_reanalysis["result"]["items"])

    cancelled = await _acall(invoked, "cancel_job", reanalysis_job_id)
    assert cancelled["ok"] is True

    terminal = await _wait_for_job(invoked, reanalysis_job_id)
    assert terminal["result"]["status"] in {"cancelled", "completed"}

    export_path = tmp_path / "exports" / "session.json"
    exported = await _acall(invoked, "export_session_state", session_id, str(export_path))
    assert exported["ok"] is True
    assert export_path.exists()

    caves = await _acall(invoked, "find_code_caves", session_id, binary_artifact, 8)
    assert caves["ok"] is True
    assert "items" in caves["result"]["code_caves"]

    metadata_edit = await _acall(
        invoked,
        "edit_artifact_metadata",
        session_id,
        binary_artifact,
        "calling_convention",
        {"function_id": function["function_id"]},
        {"name": "sysv_abi_override"},
    )
    assert metadata_edit["ok"] is True

    imported_types = await _acall(
        invoked,
        "import_type_definitions",
        session_id,
        binary_artifact,
        "c_header",
        "typedef struct Config { int threshold; const char *label; } Config;\nint helper(Config *cfg);",
    )
    assert imported_types["ok"] is True
    assert imported_types["result"]["imported"]["named_types"]["structs"]["Config"]["name"] == "Config"

    patched_bytes_path = tmp_path / "exports" / "mcp_surface_byte_patch.bin"
    patched_bytes = await _acall(
        invoked,
        "patch_artifact_bytes",
        session_id,
        binary_artifact,
        "file_offset",
        0,
        "9090",
        str(patched_bytes_path),
        True,
        "mcp_surface_byte_patch.bin",
    )
    assert patched_bytes["ok"] is True
    assert patched_bytes_path.exists()
    patched_bytes_artifact = patched_bytes["result"]["attached_artifact"]["artifact_id"]

    patched_asm_path = tmp_path / "exports" / "mcp_surface_asm_patch.bin"
    patched_asm = await _acall(
        invoked,
        "patch_artifact_assembly",
        session_id,
        binary_artifact,
        "file_offset",
        0,
        "nop; ret",
        "x86_64",
        str(patched_asm_path),
        True,
        "mcp_surface_asm_patch.bin",
    )
    assert patched_asm["ok"] is True
    assert patched_asm_path.exists()
    patched_asm_artifact = patched_asm["result"]["attached_artifact"]["artifact_id"]

    dependencies = await _acall(invoked, "list_artifact_dependencies", session_id, binary_artifact)
    assert dependencies["ok"] is True
    assert "imports" in dependencies["result"]["dependencies"]

    correlations = await _acall(invoked, "correlate_session_artifacts", session_id, [binary_artifact, patched_bytes_artifact, patched_asm_artifact])
    assert correlations["ok"] is True
    assert "items" in correlations["result"]["correlations"]

    diff = await _acall(invoked, "diff_artifacts", session_id, binary_artifact, patched_bytes_artifact)
    assert diff["ok"] is True
    assert diff["result"]["diff"]["available_levels"]["structural"] is True

    composite_ingest = await _acall(
        invoked,
        "ingest_and_triage_artifact",
        session_id,
        str(binary),
        "mcp_surface_ingested.bin",
        None,
        False,
        "brief",
        400,
        True,
        False,
    )
    assert composite_ingest["ok"] is True
    composite_artifact = composite_ingest["result"]["artifact"]["artifact_id"]

    analyze_brief = await _acall(
        invoked,
        "analyze_and_summarize",
        session_id,
        binary_artifact,
        "patching",
        30.0,
        "deep",
        400,
        True,
        False,
    )
    assert analyze_brief["ok"] is True
    assert analyze_brief["result"]["analysis_status"] == "completed"
    assert analyze_brief["result"]["response_profile"]["effective_verbosity"] == "brief"

    hunt = await _acall(
        invoked,
        "hunt_interesting_regions",
        session_id,
        binary_artifact,
        "malware",
        5,
        "brief",
        None,
        False,
        False,
    )
    assert hunt["ok"] is True
    assert hunt["suggested_next_actions"] == []

    trace = await _acall(
        invoked,
        "trace_capability",
        session_id,
        binary_artifact,
        {"function_id": function["function_id"]},
        1,
        "brief",
        None,
        True,
        True,
    )
    assert trace["ok"] is True
    assert trace["result"]["trace"]["neighborhood"]["nearby_functions"]["items"] is not None

    patch_plan = await _acall(
        invoked,
        "prepare_patch_plan",
        session_id,
        binary_artifact,
        "bypass_guard",
        {"function_id": function["function_id"]},
        8,
        "brief",
        None,
        True,
        False,
    )
    assert patch_plan["ok"] is True
    assert patch_plan["result"]["patch_plan"]["candidate_patch_points"]

    relationship_brief = await _acall(
        invoked,
        "artifact_relationship_brief",
        session_id,
        binary_artifact,
        "diffing",
        "brief",
        None,
        True,
        False,
    )
    assert relationship_brief["ok"] is True
    assert relationship_brief["result"]["relationship_brief"]["diff_candidates"]["items"]

    dynamic_manifest_path = tmp_path / "exports" / "dynamic-manifest.json"
    dynamic_manifest = await _acall(invoked, "export_dynamic_manifest", session_id, binary_artifact, str(dynamic_manifest_path))
    assert dynamic_manifest["ok"] is True
    assert dynamic_manifest_path.exists()

    ghidra_analysis = await _acall(invoked, "ghidra_analyze", session_id, binary_artifact, 60)
    if ghidra_analysis["ok"]:
        assert "functions" in ghidra_analysis["result"]
    else:
        assert ghidra_analysis["error"]["code"] in {"ghidra_not_installed", "pyghidra_not_installed"}

    ghidra_decompilation = await _acall(invoked, "ghidra_decompile", session_id, binary_artifact, function["address"], 60)
    if ghidra_decompilation["ok"]:
        assert "source" in ghidra_decompilation["result"]
    else:
        assert ghidra_decompilation["error"]["code"] in {
            "ghidra_not_installed",
            "pyghidra_not_installed",
            "ghidra_decompiler_unavailable",
        }

    ghidra_script = await _acall(invoked, "run_ghidra_script", session_id, binary_artifact, "print('surface-contract')", 60)
    if ghidra_script["ok"]:
        assert "surface-contract" in ghidra_script["result"]["stdout"]
    else:
        assert ghidra_script["error"]["code"] in {"ghidra_not_installed", "pyghidra_not_installed"}

    command_log_path = tmp_path / "exports" / "command-log.txt"
    command_log = await _acall(invoked, "export_command_log", session_id, "text", str(command_log_path))
    assert command_log["ok"] is True
    assert command_log_path.exists()

    analysis_report_path = tmp_path / "exports" / "analysis-report.json"
    analysis_report = await _acall(invoked, "export_analysis_report", session_id, binary_artifact, "json", str(analysis_report_path))
    assert analysis_report["ok"] is True
    assert analysis_report_path.exists()

    curated_export_path = tmp_path / "exports" / "curated.json"
    curated = await _acall(
        invoked,
        "export_curated_analysis",
        session_id,
        binary_artifact,
        function_ids=[function["function_id"]],
        output_path=str(curated_export_path),
    )
    assert curated["ok"] is True
    assert curated_export_path.exists()

    batch = await _acall(invoked, "batch_query_artifacts", session_id, "analysis_synopsis", limit=5)
    assert batch["ok"] is True
    assert batch["result"]["items"]

    removed = await _acall(invoked, "remove_artifact", session_id, artifact_id=zip_artifact)
    assert removed["ok"] is True
    assert removed["result"]["artifact_id"] == zip_artifact

    removed_composite = await _acall(invoked, "remove_artifact", session_id, artifact_id=composite_artifact)
    assert removed_composite["ok"] is True

    destroyed = await _acall(invoked, "destroy_session", name="mcp-secondary")
    assert destroyed["ok"] is True
    assert destroyed["result"]["session_id"] == secondary_session_id

    assert invoked == EXPECTED_MCP_TOOLS


def test_all_exposed_tools_are_callable_via_mcp_wrappers(tmp_path: Path, monkeypatch) -> None:
    asyncio.run(_exercise_all_exposed_tools_via_mcp_wrappers(tmp_path, monkeypatch))
