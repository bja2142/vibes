from __future__ import annotations

import time
from pathlib import Path

from reversing_mcp.app import ReversingMCPApp
from reversing_mcp.server import build_network_app


def _session_id(payload: dict) -> str:
    return payload["result"]["session"]["session_id"]


def _artifact_id(payload: dict) -> str:
    return payload["result"]["artifact_id"]


def _job_id(payload: dict) -> str:
    return payload["result"]["job_id"]


def _wait_for_job(app: ReversingMCPApp, job_id: str, timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = app.get_job(job_id)
        status = payload["result"]["status"]
        if status in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"Job {job_id} did not finish before timeout.")


def _sample_file(workspace_root: Path, name: str = "sample.bin") -> Path:
    sample_path = workspace_root / name
    sample_path.write_bytes(b"\x7fELFplaceholder-data")
    return sample_path


def test_session_persists_across_app_instances(tmp_path: Path) -> None:
    app = ReversingMCPApp(workspace_root=tmp_path)
    created = app.create_session("alpha", description="persistent")
    assert created["ok"] is True

    reloaded = ReversingMCPApp(workspace_root=tmp_path)
    loaded = reloaded.load_session(session_id=_session_id(created))

    assert loaded["ok"] is True
    assert loaded["result"]["session"]["name"] == "alpha"
    assert (tmp_path / ".reversing-mcp" / "sessions" / _session_id(created) / "session.json").exists()


def test_provisional_object_ids_expire_after_reanalysis_and_removal(tmp_path: Path) -> None:
    app = ReversingMCPApp(workspace_root=tmp_path)
    session_id = _session_id(app.create_session("beta"))
    artifact_id = _artifact_id(app.add_artifact(session_id, str(_sample_file(tmp_path))))

    function_payload = app.register_provisional_function(session_id, artifact_id, "entry", "0x401000")
    function_id = function_payload["result"]["function_id"]
    assert app.get_object_reference(session_id, function_id)["ok"] is True

    reanalysis_job = app.start_artifact_reanalysis(session_id, artifact_id)
    finished = _wait_for_job(app, _job_id(reanalysis_job))
    assert finished["result"]["status"] == "completed"

    expired = app.get_object_reference(session_id, function_id)
    assert expired["ok"] is False
    assert expired["error"]["category"] == "invalid_id"

    string_payload = app.register_provisional_string(session_id, artifact_id, "hello")
    string_id = string_payload["result"]["string_id"]
    removed = app.remove_artifact(session_id, artifact_id=artifact_id)
    assert removed["ok"] is True

    after_remove = app.get_object_reference(session_id, string_id)
    assert after_remove["ok"] is False
    assert after_remove["error"]["category"] == "invalid_id"


def test_annotation_history_and_revert(tmp_path: Path) -> None:
    app = ReversingMCPApp(workspace_root=tmp_path)
    session_id = _session_id(app.create_session("gamma"))
    artifact_id = _artifact_id(app.add_artifact(session_id, str(_sample_file(tmp_path, "gamma.bin"))))

    first = app.put_annotation(
        session_id,
        target={"kind": "address", "artifact_id": artifact_id, "address": "0x1000"},
        annotation_type="comment",
        value={"text": "first"},
    )
    annotation_id = first["result"]["annotation_id"]
    second = app.put_annotation(
        session_id,
        target={"kind": "address", "artifact_id": artifact_id, "address": "0x1000"},
        annotation_type="comment",
        value={"text": "second"},
        annotation_id=annotation_id,
    )
    assert second["result"]["revision_count"] == 2

    history = app.get_annotation_history(session_id, annotation_id)
    assert [item["value"]["text"] for item in history["result"]["history"]] == ["first", "second"]

    reverted = app.revert_annotation(session_id, annotation_id)
    assert reverted["ok"] is True
    assert reverted["result"]["revision_count"] == 3
    assert reverted["result"]["value"]["text"] == "first"


def test_snapshot_restore_reverts_session_state(tmp_path: Path) -> None:
    app = ReversingMCPApp(workspace_root=tmp_path)
    session_id = _session_id(app.create_session("delta", settings={"mode": "initial"}))
    artifact_id = _artifact_id(app.add_artifact(session_id, str(_sample_file(tmp_path, "delta.bin"))))
    app.put_annotation(
        session_id,
        target={"kind": "artifact", "artifact_id": artifact_id},
        annotation_type="tag",
        value={"label": "keep"},
    )

    snapshot = app.create_session_snapshot(session_id, "baseline")
    assert snapshot["ok"] is True

    app.update_session_settings(session_id, {"mode": "mutated", "nested": {"flag": True}})
    app.put_annotation(
        session_id,
        target={"kind": "session"},
        annotation_type="note",
        value={"text": "after snapshot"},
    )

    restored = app.restore_session_snapshot(session_id, name="baseline")
    assert restored["ok"] is True
    assert restored["result"]["session"]["settings"] == {"mode": "initial"}
    assert restored["result"]["session"]["session"]["annotation_count"] == 1


def test_jobs_report_partial_progress_and_can_be_cancelled(tmp_path: Path) -> None:
    app = ReversingMCPApp(workspace_root=tmp_path)
    session_id = _session_id(app.create_session("epsilon"))
    artifact_id = _artifact_id(app.add_artifact(session_id, str(_sample_file(tmp_path, "epsilon.bin"))))

    started = app.start_artifact_reanalysis(session_id, artifact_id)
    job_id = _job_id(started)
    poll = app.get_job(job_id)
    assert poll["partial"] is True

    cancelled = app.cancel_job(job_id)
    assert cancelled["ok"] is True

    finished = _wait_for_job(app, job_id)
    assert finished["result"]["status"] == "cancelled"
    assert finished["result"]["partial_result"] is not None


def test_export_and_network_routes(tmp_path: Path) -> None:
    app = ReversingMCPApp(workspace_root=tmp_path)
    session_id = _session_id(app.create_session("zeta"))
    export_path = tmp_path / "exports" / "session.json"

    exported = app.export_session_state(session_id, str(export_path))
    assert exported["ok"] is True
    assert export_path.exists()

    app_routes = {getattr(route, "path", None) for route in build_network_app("both").routes}
    assert "/mcp" in app_routes
    assert "/sse" in app_routes
    assert "/messages" in app_routes
