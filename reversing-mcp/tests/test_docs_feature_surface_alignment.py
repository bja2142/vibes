from __future__ import annotations

from pathlib import Path

from reversing_mcp.app import ReversingMCPApp, TOOL_CATALOG


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
TOOL_REFERENCE = ROOT / "docs" / "tool-reference.md"
REQUIREMENTS_MATRIX = ROOT / "docs" / "requirements-matrix.md"
FEATURE_07 = ROOT / "features" / "07-patching-multi-artifact-and-interop.md"
FEATURE_08 = ROOT / "features" / "08-transport-ops-and-polish.md"
FEATURE_09 = ROOT / "features" / "09-composite-brief-workflows-and-token-budgeting.md"

FEATURE_07_TOOLS = {
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
}

FEATURE_09_TOOLS = {
    "ingest_and_triage_artifact",
    "analyze_and_summarize",
    "hunt_interesting_regions",
    "trace_capability",
    "prepare_patch_plan",
    "artifact_relationship_brief",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_feature07_08_09_docs_align_with_tool_surface(tmp_path: Path) -> None:
    catalog_tools = {item["name"] for item in TOOL_CATALOG}
    app = ReversingMCPApp(workspace_root=tmp_path)
    capabilities = app.get_capabilities()
    assert capabilities["ok"] is True

    readme = _read(README)
    tool_reference = _read(TOOL_REFERENCE)
    requirements_matrix = _read(REQUIREMENTS_MATRIX)
    feature_07 = _read(FEATURE_07)
    feature_08 = _read(FEATURE_08)
    feature_09 = _read(FEATURE_09)

    assert FEATURE_07_TOOLS <= catalog_tools
    assert FEATURE_09_TOOLS <= catalog_tools

    for tool_name in FEATURE_07_TOOLS | FEATURE_09_TOOLS:
        assert f"`{tool_name}`" in tool_reference or tool_name in tool_reference

    for phrase in (
        "Feature 07 workflows including byte and assembly patching",
        "Feature 08 operational polish including stdio and streamable HTTP transport",
        "Feature 09 composite brief workflows including one-shot intake",
    ):
        assert phrase in readme

    for phrase in (
        "Implement byte patching by file offset and virtual address",
        "Implement streamable HTTP transport for remote service deployment",
        "Add shared response-budget controls for composite tools",
    ):
        assert phrase in feature_07 + feature_08 + feature_09

    for requirement_row in (
        "| Byte Patching | Implemented |",
        "| Assembly-Assisted Patching | Implemented |",
        "| MCP Transport | Implemented |",
        "| Analysis Synopsis | Implemented |",
        "| Suggested Next Actions | Implemented |",
        "| Token-Efficient References | Implemented |",
    ):
        assert requirement_row in requirements_matrix

    patching = capabilities["result"]["patching"]
    assert set(patching["supported_isas"]) >= {"x86", "x86_64", "aarch64", "arm", "thumb"}

    transports = capabilities["result"]["transports"]
    assert transports["stdio"]["enabled"] is True
    assert transports["streamable_http"]["enabled"] is True
    assert transports["streamable_http"]["authentication"]["supported"] is True
    assert transports["streamable_http"]["session_isolation"]["single_agent_per_session"] is True

    features = capabilities["result"]["features"]
    assert features["command_log_export"] is True
    assert features["analysis_report_export"] is True
    assert features["http_authentication"] is True
    assert features["request_rate_limiting"] is True
    assert features["composite_brief_workflows"] is True
    assert features["response_budget_controls"] is True

    composite = capabilities["result"]["composite_workflows"]
    assert set(composite["supported_tools"]) == FEATURE_09_TOOLS
    assert composite["response_budget"]["verbosity"] == ["brief", "normal", "deep"]
    assert composite["response_budget"]["supports_token_budget_hint"] is True
    assert composite["response_budget"]["supports_raw_section_opt_in"] is True
    assert composite["focus_presets"] == ["general", "malware", "patching", "diffing", "firmware", "extraction"]
