from __future__ import annotations

import asyncio
import json

from pwn_mcp.app import PwnMcpApp
from pwn_mcp.server import build_network_app


def test_network_app_root_reports_mcp_endpoint(tmp_path):
    app = build_network_app(
        PwnMcpApp(
            workspace_root=tmp_path / "workspace",
            output_root=tmp_path / "output",
            sessions_root=tmp_path / "sessions",
        ),
        "http",
    )
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
    }
    asyncio.run(app(scope, receive, send))

    start = next(item for item in sent if item["type"] == "http.response.start")
    body = next(item for item in sent if item["type"] == "http.response.body")
    assert start["status"] == 200
    payload = json.loads(body["body"].decode("utf-8"))
    assert payload["endpoints"]["streamable_http"] == "/mcp"
    assert "sse" not in payload["endpoints"]
