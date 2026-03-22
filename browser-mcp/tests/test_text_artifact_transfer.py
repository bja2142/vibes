from __future__ import annotations

from pathlib import Path

import pytest

from browser_puppet.models import ContextState
from browser_puppet.server import BrowserPuppetApp


def register_context(tmp_path: Path) -> tuple[BrowserPuppetApp, ContextState]:
    app = BrowserPuppetApp()
    context = ContextState(
        context_id="context-1",
        browser_name="chromium",
        browser=None,
        playwright_context=None,
        artifact_dir=tmp_path,
    )
    app.state.contexts[context.context_id] = context
    return app, context


@pytest.mark.asyncio
async def test_upload_and_download_text_artifact_round_trip(tmp_path: Path) -> None:
    app, context = register_context(tmp_path)

    upload = await app.upload_text_artifact(context.context_id, "notes/result.txt", "hello\nworld")
    download = await app.download_text_artifact(context.context_id, "notes/result.txt")

    assert upload["success"] is True
    assert upload["artifact"]["kind"] == "uploaded_text"
    assert download["content"] == "hello\nworld"
    assert download["relative_path"] == "notes/result.txt"


@pytest.mark.asyncio
async def test_list_context_files_returns_relative_paths_with_cursor(tmp_path: Path) -> None:
    app, context = register_context(tmp_path)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.json").write_text("{}", encoding="utf-8")

    result = await app.list_context_files(context.context_id, limit=1)

    assert result["next_cursor"] == "1"
    assert result["remaining_count"] == 1
    assert result["items"][0]["relative_path"] == "a.txt"


@pytest.mark.asyncio
async def test_upload_text_artifact_rejects_path_escape(tmp_path: Path) -> None:
    app, context = register_context(tmp_path)

    with pytest.raises(Exception) as excinfo:
        await app.upload_text_artifact(context.context_id, "../escape.txt", "nope")

    assert getattr(excinfo.value, "error_code", None) == "invalid_artifact_path"


@pytest.mark.asyncio
async def test_download_text_artifact_rejects_binary_file(tmp_path: Path) -> None:
    app, context = register_context(tmp_path)
    (tmp_path / "image.bin").write_bytes(b"\x00\x01\x02")

    with pytest.raises(Exception) as excinfo:
        await app.download_text_artifact(context.context_id, "image.bin")

    assert getattr(excinfo.value, "error_code", None) == "binary_artifact_not_supported"


@pytest.mark.asyncio
async def test_download_text_artifact_rejects_large_file(tmp_path: Path) -> None:
    app, context = register_context(tmp_path)
    (tmp_path / "large.txt").write_text("x" * 32, encoding="utf-8")

    with pytest.raises(Exception) as excinfo:
        await app.download_text_artifact(context.context_id, "large.txt", max_bytes=16)

    assert getattr(excinfo.value, "error_code", None) == "artifact_too_large"
