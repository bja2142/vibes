from __future__ import annotations

from pathlib import Path

import pytest

from browser_puppet.models import ContextState, PageState
from browser_puppet.server import BrowserPuppetApp


class FakeVideo:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_bytes(b"video-bytes")

    async def path(self) -> str:
        return str(self._path)


class FakePage:
    def __init__(self, video: FakeVideo | None = None) -> None:
        self.video = video

    def is_closed(self) -> bool:
        return False

    async def close(self) -> None:
        return None


def register_context(tmp_path: Path, *, with_capture: bool) -> tuple[BrowserPuppetApp, ContextState]:
    app = BrowserPuppetApp()
    app.state.artifacts_root = tmp_path
    video = FakeVideo(tmp_path / "videos" / "page-1.webm") if with_capture else None
    context = ContextState(
        context_id="context-1",
        browser_name="chromium",
        browser=None,
        playwright_context=None,
        artifact_dir=tmp_path,
        har_path=str((tmp_path / "session.har").resolve()) if with_capture else None,
        video_recording_enabled=with_capture,
        config={},
    )
    if with_capture:
        Path(context.har_path).write_text("{}")
    page_state = PageState(page_id="page-1", context_id=context.context_id, playwright_page=FakePage(video))
    context.pages[page_state.page_id] = page_state
    context.active_page_id = page_state.page_id
    app.state.contexts[context.context_id] = context
    return app, context


@pytest.mark.asyncio
async def test_record_video_stop_registers_artifact(tmp_path: Path) -> None:
    app, context = register_context(tmp_path, with_capture=True)

    result = await app.record_video(context.context_id, "stop")
    artifacts = await app.list_artifacts(context.context_id)

    assert result["status"] == "stopped"
    assert result["artifacts"][0]["kind"] == "video"
    assert any(item["kind"] == "video" for item in artifacts["artifacts"])


@pytest.mark.asyncio
async def test_export_har_registers_artifact(tmp_path: Path) -> None:
    app, context = register_context(tmp_path, with_capture=True)

    result = await app.export_har(context.context_id)
    artifacts = await app.list_artifacts(context.context_id)

    assert Path(result["path"]).exists()
    assert any(item["kind"] == "har" for item in artifacts["artifacts"])


@pytest.mark.asyncio
async def test_record_video_rejects_disabled_context(tmp_path: Path) -> None:
    app, context = register_context(tmp_path, with_capture=False)

    with pytest.raises(Exception) as excinfo:
        await app.record_video(context.context_id, "stop")

    assert getattr(excinfo.value, "error_code", None) == "capture_not_enabled"


@pytest.mark.asyncio
async def test_export_har_rejects_disabled_context(tmp_path: Path) -> None:
    app, context = register_context(tmp_path, with_capture=False)

    with pytest.raises(Exception) as excinfo:
        await app.export_har(context.context_id)

    assert getattr(excinfo.value, "error_code", None) == "capture_not_enabled"
