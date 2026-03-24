from __future__ import annotations

import subprocess
import time
from pathlib import Path

from starlette.testclient import TestClient

import reversing_mcp.server as server
from reversing_mcp.app import ReversingMCPApp
from reversing_mcp.server import _build_request_context, build_network_app
from reversing_mcp.transport import RequestContext, load_http_transport_config, request_context


def _session_id(payload: dict) -> str:
    return payload["result"]["session"]["session_id"]


def _artifact_id(payload: dict) -> str:
    return payload["result"]["artifact_id"]


def _job_id(payload: dict) -> str:
    return payload["result"]["job_id"]


def _wait_for_job(app: ReversingMCPApp, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = app.get_job(job_id)
        if payload["result"]["status"] in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not finish before timeout.")


def _sample_file(workspace_root: Path, name: str = "sample.bin") -> Path:
    sample_path = workspace_root / name
    sample_path.write_bytes(b"\x7fELFplaceholder-data")
    return sample_path


def _build_sample_binary(workspace_root: Path, name: str = "feature08_sample") -> Path:
    source = workspace_root / f"{name}.c"
    binary = workspace_root / name
    source.write_text(
        """
        #include <stdio.h>

        __attribute__((noinline)) int helper(int x) {
            return x + 4;
        }

        int main(void) {
            puts("feature08");
            return helper(3);
        }
        """,
        encoding="utf-8",
    )
    subprocess.run(
        ["cc", "-g", "-O0", "-fno-inline", str(source), "-o", str(binary)],
        check=True,
        cwd=workspace_root,
    )
    return binary


def test_http_transport_auth_and_rate_limit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "APP", ReversingMCPApp(workspace_root=tmp_path))
    monkeypatch.setenv("REVERSING_MCP_HTTP_TOKENS", "tenant-a=secret-token")
    monkeypatch.setenv("REVERSING_MCP_HTTP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("REVERSING_MCP_HTTP_REQUESTS_PER_MINUTE_PER_AGENT", "1")

    with TestClient(build_network_app("http")) as client:
        unauthorized = client.get("/mcp")
        assert unauthorized.status_code == 401
        assert unauthorized.json()["error"]["code"] == "http_auth_required"

        headers = {
            "Authorization": "Bearer secret-token",
            "X-Reversing-Agent-Id": "agent-a",
        }
        allowed = client.get("/mcp", headers=headers)
        assert allowed.status_code != 401

        limited = client.get("/mcp", headers=headers)
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "http_rate_limit_exceeded"


def test_http_tokens_do_not_enable_auth_without_explicit_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REVERSING_MCP_HTTP_TOKENS", "tenant-a=secret-token")
    config = load_http_transport_config()
    context = _build_request_context({"x-reversing-agent-id": "agent-a"}, config=config)
    assert config.auth_enabled is False
    assert context.authenticated is False
    assert context.tenant_id == "anonymous"
    assert context.agent_id == "agent-a"


def test_http_session_and_job_isolation(tmp_path: Path) -> None:
    app = ReversingMCPApp(workspace_root=tmp_path)

    with request_context(RequestContext(transport="http", authenticated=True, tenant_id="tenant-a", agent_id="agent-a")):
        session_id = _session_id(app.create_session("tenant-a-session"))
        artifact_id = _artifact_id(app.add_artifact(session_id, str(_sample_file(tmp_path, "tenant-a.bin"))))
        job_id = _job_id(app.start_artifact_reanalysis(session_id, artifact_id))

    with request_context(RequestContext(transport="http", authenticated=True, tenant_id="tenant-a", agent_id="agent-b")):
        sessions = app.list_sessions()
        assert sessions["ok"] is True
        assert sessions["result"]["items"] == []

        same_tenant_conflict = app.load_session(session_id=session_id)
        assert same_tenant_conflict["ok"] is False
        assert same_tenant_conflict["error"]["code"] == "session_agent_conflict"

        blocked_job = app.get_job(job_id)
        assert blocked_job["ok"] is False
        assert blocked_job["error"]["code"] == "job_agent_conflict"

    with request_context(RequestContext(transport="http", authenticated=True, tenant_id="tenant-b", agent_id="agent-z")):
        sessions = app.list_sessions()
        assert sessions["ok"] is True
        assert sessions["result"]["items"] == []

        wrong_tenant = app.load_session(session_id=session_id)
        assert wrong_tenant["ok"] is False
        assert wrong_tenant["error"]["code"] == "session_tenant_forbidden"

        wrong_tenant_job = app.get_job(job_id)
        assert wrong_tenant_job["ok"] is False
        assert wrong_tenant_job["error"]["code"] == "job_tenant_forbidden"

    with request_context(RequestContext(transport="http", authenticated=True, tenant_id="tenant-a", agent_id="agent-a")):
        finished = _wait_for_job(app, job_id)
        assert finished["result"]["status"] == "completed"


def test_feature08_capabilities_synopsis_and_correlation_pagination(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REVERSING_MCP_HTTP_TOKENS", "tenant-a=secret-token")
    monkeypatch.setenv("REVERSING_MCP_HTTP_MAX_SESSIONS_PER_TENANT", "3")
    monkeypatch.setenv("REVERSING_MCP_HTTP_MAX_ACTIVE_JOBS_PER_TENANT", "2")

    app = ReversingMCPApp(workspace_root=tmp_path)
    caps = app.get_capabilities()
    assert caps["ok"] is True
    assert caps["result"]["transports"]["streamable_http"]["authentication"]["enabled"] is False
    assert caps["result"]["transports"]["streamable_http"]["authentication"]["required"] is False
    assert caps["result"]["transports"]["streamable_http"]["session_isolation"]["single_agent_per_session"] is True
    assert caps["result"]["features"]["tenant_isolation"] is True

    sample = _build_sample_binary(tmp_path)
    with request_context(RequestContext(transport="http", authenticated=True, tenant_id="tenant-a", agent_id="agent-a")):
        session_id = _session_id(app.create_session("feature08"))
        artifact_id = _artifact_id(app.add_artifact(session_id, str(sample)))

        started = app.start_artifact_analysis(session_id, artifact_id)
        job_id = _job_id(started)
        finished = _wait_for_job(app, job_id, timeout=30.0)
        assert finished["result"]["status"] == "completed"

        assert app.scan_with_yara(session_id, artifact_id)["ok"] is True
        assert app.carve_embedded_artifacts(session_id, artifact_id, attach_to_session=True)["ok"] is True

        patched_one = app.patch_artifact_bytes(session_id, artifact_id, "file_offset", 0, "9090", attach_to_session=True, display_name="patched-one.bin")
        patched_two = app.patch_artifact_bytes(session_id, artifact_id, "file_offset", 2, "9090", attach_to_session=True, display_name="patched-two.bin")
        assert patched_one["ok"] is True
        assert patched_two["ok"] is True

        synopsis = app.get_analysis_synopsis(session_id, artifact_id)
        assert synopsis["ok"] is True
        assert synopsis["result"]["analysis_state"]["matched_signatures"]["count"] >= 1
        assert "extraction_history" in synopsis["result"]["analysis_state"]
        suggested_tools = {item["tool"] for item in synopsis["suggested_next_actions"]}
        assert "list_artifact_functions" in suggested_tools

        correlations = app.correlate_session_artifacts(
            session_id,
            [artifact_id, patched_one["result"]["attached_artifact"]["artifact_id"], patched_two["result"]["attached_artifact"]["artifact_id"]],
            cursor=0,
            limit=1,
        )
        assert correlations["ok"] is True
        assert correlations["result"]["correlations"]["page"]["limit"] == 1
        assert correlations["result"]["correlations"]["page"]["returned"] <= 1
