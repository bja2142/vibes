from __future__ import annotations

from pathlib import Path

from reversing_mcp.app import ReversingMCPApp
from reversing_mcp.security import WorkspaceSecurity


def _session_id(payload: dict) -> str:
    return payload["result"]["session"]["session_id"]


def _sample_file(path: Path, content: bytes = b"\x7fELFsecure") -> Path:
    path.write_bytes(content)
    return path


def test_add_artifact_rejects_paths_outside_workspace(tmp_path: Path) -> None:
    app = ReversingMCPApp(workspace_root=tmp_path)
    session_id = _session_id(app.create_session("secure-alpha"))
    external_path = _sample_file(tmp_path.parent / f"{tmp_path.name}-outside.bin")

    payload = app.add_artifact(session_id, str(external_path))

    assert payload["ok"] is False
    assert payload["error"]["category"] == "invalid_request"
    assert payload["error"]["code"] == "path_outside_workspace"


def test_add_artifact_rejects_symlink_escape(tmp_path: Path) -> None:
    app = ReversingMCPApp(workspace_root=tmp_path)
    session_id = _session_id(app.create_session("secure-beta"))
    external_path = _sample_file(tmp_path.parent / f"{tmp_path.name}-escape.bin")
    link_path = tmp_path / "linked.bin"
    link_path.symlink_to(external_path)

    payload = app.add_artifact(session_id, str(link_path))

    assert payload["ok"] is False
    assert payload["error"]["code"] == "path_outside_workspace"


def test_export_rejects_output_escape_and_symlink_escape(tmp_path: Path) -> None:
    app = ReversingMCPApp(workspace_root=tmp_path)
    session_id = _session_id(app.create_session("secure-gamma"))

    outside_export = app.export_session_state(session_id, str(tmp_path.parent / "report.json"))
    assert outside_export["ok"] is False
    assert outside_export["error"]["code"] == "path_outside_workspace"

    external_dir = tmp_path.parent / f"{tmp_path.name}-reports"
    external_dir.mkdir(exist_ok=True)
    linked_dir = tmp_path / "linked-dir"
    linked_dir.symlink_to(external_dir, target_is_directory=True)

    linked_export = app.export_session_state(session_id, str(linked_dir / "report.json"))
    assert linked_export["ok"] is False
    assert linked_export["error"]["code"] == "path_outside_workspace"


def test_oversized_artifact_fails_fast(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REVERSING_MCP_MAX_INPUT_SIZE_BYTES", "4")
    app = ReversingMCPApp(workspace_root=tmp_path)
    session_id = _session_id(app.create_session("secure-delta"))
    sample = _sample_file(tmp_path / "big.bin", b"12345")

    payload = app.add_artifact(session_id, str(sample))

    assert payload["ok"] is False
    assert payload["error"]["category"] == "timeout_or_resource_limit"
    assert payload["error"]["code"] == "input_size_limit_exceeded"


def test_filename_sanitization_preserves_original_name_in_provenance(tmp_path: Path) -> None:
    security = WorkspaceSecurity(workspace_root=tmp_path)

    derived = security.derive_output_file(subdir="outputs", unsafe_name="../bad name?.bin")

    assert derived["file_name"] == "bad_name_.bin"
    assert derived["provenance"]["original_name"] == "../bad name?.bin"
    assert derived["provenance"]["sanitized"] is True
    assert derived["relative_path"] == "outputs/bad_name_.bin"


def test_parser_failures_are_contained_and_structured(tmp_path: Path) -> None:
    app = ReversingMCPApp(workspace_root=tmp_path)
    malformed = _sample_file(tmp_path / "broken.bin", b"BROKEN-parser-input")

    malformed_payload = app.run_parser_probe(str(malformed))
    assert malformed_payload["ok"] is False
    assert malformed_payload["error"]["category"] == "backend_failure"
    assert malformed_payload["error"]["code"] == "parser_worker_failure"

    crashed_payload = app.run_parser_probe(str(malformed), simulate="crash")
    assert crashed_payload["ok"] is False
    assert crashed_payload["error"]["category"] == "backend_failure"
    assert crashed_payload["error"]["code"] == "parser_crashed"

    followup = app.create_session("secure-epsilon")
    assert followup["ok"] is True


def test_runtime_policy_surface_reports_limits_and_versions(tmp_path: Path) -> None:
    app = ReversingMCPApp(workspace_root=tmp_path)

    payload = app.get_runtime_policies()

    assert payload["ok"] is True
    assert payload["result"]["workspace_root"] == str(tmp_path)
    assert payload["result"]["resource_limits"]["max_input_size_bytes"] > 0
    assert payload["result"]["parser_isolation"]["enabled"] is True
    assert payload["result"]["sample_containment"]["shell_execution_allowed"] is False
    assert payload["result"]["tool_versions"]["server"] == "0.1.0"
