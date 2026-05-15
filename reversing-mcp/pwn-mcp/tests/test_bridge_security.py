from __future__ import annotations

import json
from pathlib import Path

import pytest

from pwn_mcp.app import PwnMcpApp
from pwn_mcp.errors import PwnMcpError


def test_import_static_analysis_requires_manifest_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    manifest = outside / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "source": "reversing-mcp"}), encoding="utf-8")

    app = PwnMcpApp(
        workspace_root=workspace,
        output_root=tmp_path / "output",
        sessions_root=tmp_path / "sessions",
    )
    session = app.sessions.create()

    with pytest.raises(PwnMcpError) as exc_info:
        app.dispatch(
            "import_static_analysis",
            {"session_id": session.session_id, "manifest_path": str(manifest)},
        )

    assert exc_info.value.code == "path_outside_workspace"
