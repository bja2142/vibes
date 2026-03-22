from __future__ import annotations

from pathlib import Path

import pytest

from browser_puppet.models import ArtifactRecord, ContextState, ElementRecord, PageState
from browser_puppet.server import BrowserPuppetApp


class FakePage:
    def __init__(self, url: str, title: str, opener=None) -> None:
        self.url = url
        self._title = title
        self.opener = opener
        self.viewport_size = {"width": 1280, "height": 800}

    async def title(self) -> str:
        return self._title

    def is_closed(self) -> bool:
        return False


class FakeLocator:
    def __init__(self, payload) -> None:
        self.payload = payload

    async def evaluate(self, _script, _arg=None):
        return self.payload


@pytest.mark.asyncio
async def test_list_pages_includes_origin_and_opener_metadata_with_cursor(tmp_path: Path) -> None:
    app = BrowserPuppetApp()
    context = ContextState(
        context_id="context-1",
        browser_name="chromium",
        browser=None,
        playwright_context=None,
        artifact_dir=tmp_path,
    )
    opener = FakePage("https://origin.test/home", "Home")
    popup = FakePage("https://popup.test/dialog", "Dialog", opener=opener)
    context.pages["page-1"] = PageState(page_id="page-1", context_id=context.context_id, playwright_page=opener)
    context.pages["page-2"] = PageState(page_id="page-2", context_id=context.context_id, playwright_page=popup)
    context.active_page_id = "page-2"
    app.state.contexts[context.context_id] = context

    result = await app.list_pages(context.context_id, limit=1)

    assert result["next_cursor"] == "1"
    assert result["remaining_count"] == 1
    assert result["items"][0]["origin"] == "https://origin.test"

    second = await app.list_pages(context.context_id, cursor=result["next_cursor"], limit=1)

    assert second["items"][0]["page_id"] == "page-2"
    assert second["items"][0]["opener_page_id"] == "page-1"
    assert second["items"][0]["opener_url"] == "https://origin.test/home"
    assert second["items"][0]["is_active"] is True


@pytest.mark.asyncio
async def test_before_mutation_uses_session_observe_default(tmp_path: Path) -> None:
    app = BrowserPuppetApp()
    context = ContextState(
        context_id="context-1",
        browser_name="chromium",
        browser=None,
        playwright_context=None,
        artifact_dir=tmp_path,
    )
    page_state = PageState(page_id="page-1", context_id=context.context_id, playwright_page=FakePage("https://example.test", "Example"))
    context.pages[page_state.page_id] = page_state
    app.state.contexts[context.context_id] = context
    app.state.defaults.observe_default = "full"

    async def fake_capture_state(*_args, **_kwargs):
        return {"mode": "full"}

    async def fake_lightweight(*_args, **_kwargs):
        return {"mode": "light"}

    app.capture_state = fake_capture_state  # type: ignore[method-assign]
    app.get_lightweight_checkpoint = fake_lightweight  # type: ignore[method-assign]

    result = await app.before_mutation(page_state, "auto")

    assert result == {"mode": "full"}


@pytest.mark.asyncio
async def test_query_shadow_dom_returns_stable_descriptors(tmp_path: Path) -> None:
    app = BrowserPuppetApp()
    context = ContextState(
        context_id="context-1",
        browser_name="chromium",
        browser=None,
        playwright_context=None,
        artifact_dir=tmp_path,
    )
    page_state = PageState(page_id="page-1", context_id=context.context_id, playwright_page=FakePage("https://example.test", "Example"))
    context.pages[page_state.page_id] = page_state
    app.state.contexts[context.context_id] = context

    host_id = "el-host"
    host_record = ElementRecord(
        element_id=host_id,
        page_id=page_state.page_id,
        frame_id=None,
        selector="#host",
        hints={"selector": "#host"},
        created_at=0,
    )
    page_state.element_cache.set(host_id, host_record)

    app._locator_from_record = lambda _page_state, _record: FakeLocator(  # type: ignore[method-assign]
        [
            {"index": 0, "tag": "button", "text": "Save", "role": "button", "label": "Save", "name": None},
            {"index": 1, "tag": "button", "text": "Cancel", "role": "button", "label": "Cancel", "name": None},
        ]
    )

    result = await app.query_shadow_dom(host_id, "button")

    assert len(result["matches"]) == 2
    first = result["matches"][0]
    assert first["identity"]["shadow_host_element_id"] == host_id
    assert first["identity"]["shadow_selector"] == "button"
    page_lookup, record = app._lookup_element_record(first["element_id"])
    assert page_lookup.page_id == page_state.page_id
    assert record.shadow_host_element_id == host_id
    assert record.shadow_selector == "button"
    assert record.nth == 0


@pytest.mark.asyncio
async def test_list_artifacts_shares_cursor_contract(tmp_path: Path) -> None:
    app = BrowserPuppetApp()
    context = ContextState(
        context_id="context-1",
        browser_name="chromium",
        browser=None,
        playwright_context=None,
        artifact_dir=tmp_path,
    )
    context.artifacts.extend(
        [
            ArtifactRecord("artifact-1", "screenshot", "/tmp/1.png", "page-1", context.context_id, "take_screenshot", 1.0),
            ArtifactRecord("artifact-2", "pdf", "/tmp/2.pdf", "page-1", context.context_id, "print_to_pdf", 2.0),
        ]
    )
    app.state.contexts[context.context_id] = context

    result = await app.list_artifacts(context.context_id, limit=1)

    assert result["next_cursor"] == "1"
    assert result["remaining_count"] == 1
    assert result["items"][0]["artifact_id"] == "artifact-1"
