from __future__ import annotations

import base64
from pathlib import Path

import pytest

from browser_puppet.models import ContextState, ElementRecord, PageState
from browser_puppet.server import BrowserPuppetApp
from browser_puppet.utils import utc_ts


PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(b"fake-png-bytes").decode("ascii")


class FakeLocator:
    def __init__(self) -> None:
        self.last_media_action = None

    async def evaluate(self, script: str, arg=None):
        if "toDataURL" in script:
            return PNG_DATA_URL
        if "currentTime" in script and "HTMLMediaElement" in script and "return {" in script:
            return {
                "currentTime": 1.5,
                "duration": 10.0,
                "paused": False,
                "ended": False,
                "muted": False,
                "volume": 0.8,
                "playbackRate": 1.0,
                "buffered": [{"start": 0.0, "end": 2.0}],
            }
        if "Unsupported media action" in script:
            self.last_media_action = arg
            return None
        raise AssertionError("Unexpected locator script")


class FakePage:
    def __init__(self, tmp_path: Path, url: str = "https://example.test/page") -> None:
        self.url = url
        self.tmp_path = tmp_path
        self.last_pdf_path = None

    def is_closed(self) -> bool:
        return False

    async def evaluate(self, script: str, arg=None):
        if 'embed[type="application/pdf"]' in script:
            return None
        raise AssertionError("Unexpected page script")

    async def pdf(self, path: str, **options) -> None:
        self.last_pdf_path = path
        Path(path).write_bytes(b"%PDF-1.4 test")


def register_app(tmp_path: Path, *, browser_name: str = "chromium", page_url: str = "https://example.test/page") -> tuple[BrowserPuppetApp, str, str]:
    app = BrowserPuppetApp()
    app.state.artifacts_root = tmp_path
    context = ContextState(
        context_id="context-1",
        browser_name=browser_name,
        browser=None,
        playwright_context=None,
        artifact_dir=tmp_path,
    )
    page_state = PageState(page_id="page-1", context_id=context.context_id, playwright_page=FakePage(tmp_path, url=page_url))
    element_id = "el-1"
    page_state.element_cache.set(
        element_id,
        ElementRecord(
            element_id=element_id,
            page_id=page_state.page_id,
            frame_id=None,
            selector=None,
            hints={"selector": "canvas"},
            created_at=utc_ts(),
        ),
    )
    context.pages[page_state.page_id] = page_state
    app.state.contexts[context.context_id] = context
    app._locator_from_record = lambda _page_state, _record: FakeLocator()  # type: ignore[method-assign]
    return app, page_state.page_id, element_id


@pytest.mark.asyncio
async def test_capture_canvas_writes_artifact(tmp_path: Path) -> None:
    app, _, element_id = register_app(tmp_path)

    result = await app.capture_canvas(element_id)

    assert Path(result["path"]).exists()
    assert result["artifact"]["kind"] == "canvas_capture"


@pytest.mark.asyncio
async def test_media_state_and_control_return_state(tmp_path: Path) -> None:
    app, _, element_id = register_app(tmp_path)

    state = await app.get_media_state(element_id)
    controlled = await app.control_media(element_id, "pause")

    assert state["duration"] == 10.0
    assert controlled["success"] is True
    assert controlled["state"]["duration"] == 10.0


@pytest.mark.asyncio
async def test_mock_media_devices_stores_config(tmp_path: Path) -> None:
    app, _, _ = register_app(tmp_path)

    result = await app.mock_media_devices("context-1", {"video": True, "audio": False})

    assert result["success"] is True
    assert result["config"] == {"video": True, "audio": False}


@pytest.mark.asyncio
async def test_print_to_pdf_creates_artifact(tmp_path: Path) -> None:
    app, page_id, _ = register_app(tmp_path)

    result = await app.print_to_pdf(page_id)

    assert Path(result["path"]).exists()
    assert result["artifact"]["kind"] == "pdf"


@pytest.mark.asyncio
async def test_print_to_pdf_rejects_non_chromium(tmp_path: Path) -> None:
    app, page_id, _ = register_app(tmp_path, browser_name="firefox")

    with pytest.raises(Exception) as excinfo:
        await app.print_to_pdf(page_id)

    assert getattr(excinfo.value, "error_code", None) == "unsupported_browser"


@pytest.mark.asyncio
async def test_get_pdf_content_falls_back_to_generated_pdf(tmp_path: Path) -> None:
    app, page_id, _ = register_app(tmp_path)

    result = await app.get_pdf_content(page_id)

    assert "generated_pdf" in result
    assert Path(result["generated_pdf"]["path"]).exists()
