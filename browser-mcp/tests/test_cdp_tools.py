from __future__ import annotations

import pytest

from browser_puppet.models import ContextState, PageState
from browser_puppet.server import BrowserPuppetApp


class FakePage:
    url = "https://example.test"

    def is_closed(self) -> bool:
        return False


class FakeCDPSession:
    def __init__(self) -> None:
        self.listeners: dict[str, list] = {}

    async def send(self, method: str, params: dict) -> dict:
        return {"method": method, "params": params, "ok": True}

    def on(self, event_name: str, callback) -> None:
        self.listeners.setdefault(event_name, []).append(callback)

    def emit(self, event_name: str, payload: dict) -> None:
        for callback in self.listeners.get(event_name, []):
            callback(payload)


class FakePlaywrightContext:
    def __init__(self, session: FakeCDPSession) -> None:
        self.session = session

    async def new_cdp_session(self, page) -> FakeCDPSession:
        return self.session


def register_page(app: BrowserPuppetApp, *, browser_name: str = "chromium") -> tuple[PageState, FakeCDPSession]:
    session = FakeCDPSession()
    playwright_context = FakePlaywrightContext(session)
    context = ContextState(
        context_id="context-1",
        browser_name=browser_name,
        browser=None,
        playwright_context=playwright_context,
        artifact_dir=app.state.artifacts_root,
    )
    page_state = PageState(page_id="page-1", context_id=context.context_id, playwright_page=FakePage())
    context.pages[page_state.page_id] = page_state
    app.state.contexts[context.context_id] = context
    return page_state, session


@pytest.mark.asyncio
async def test_send_cdp_command_returns_session_result() -> None:
    app = BrowserPuppetApp()
    register_page(app)

    result = await app.send_cdp_command("page-1", "Runtime.evaluate", {"expression": "1 + 1"})

    assert result["method"] == "Runtime.evaluate"
    assert result["result"]["ok"] is True
    assert result["result"]["params"] == {"expression": "1 + 1"}


@pytest.mark.asyncio
async def test_subscribe_and_get_cdp_events_buffer_messages() -> None:
    app = BrowserPuppetApp()
    _, session = register_page(app)

    subscription = await app.subscribe_cdp_events("page-1", ["Network.requestWillBeSent"])
    session.emit("Network.requestWillBeSent", {"requestId": "1"})
    session.emit("Network.requestWillBeSent", {"requestId": "2"})

    result = await app.get_cdp_events(subscription["subscription_id"], limit=1)

    assert result["subscription_id"] == subscription["subscription_id"]
    assert result["items"][0]["event"] == "Network.requestWillBeSent"
    assert result["items"][0]["payload"] == {"requestId": "1"}
    assert result["next_cursor"] == "1"


@pytest.mark.asyncio
async def test_cdp_tools_reject_non_chromium_contexts() -> None:
    app = BrowserPuppetApp()
    register_page(app, browser_name="firefox")

    with pytest.raises(Exception) as excinfo:
        await app.send_cdp_command("page-1", "Runtime.evaluate", {"expression": "1"})

    assert getattr(excinfo.value, "error_code", None) == "unsupported_browser"
