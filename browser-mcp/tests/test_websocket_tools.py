from __future__ import annotations

import pytest

from browser_puppet.models import ContextState, PageState
from browser_puppet.server import BrowserPuppetApp


class FakePage:
    url = "https://example.test"

    def is_closed(self) -> bool:
        return False


class FakeSocket:
    def __init__(self, url: str) -> None:
        self.url = url
        self.listeners: dict[str, list] = {}

    def on(self, event_name: str, callback) -> None:
        self.listeners.setdefault(event_name, []).append(callback)

    def emit(self, event_name: str, payload=None) -> None:
        for callback in self.listeners.get(event_name, []):
            if payload is None:
                callback()
            else:
                callback(payload)


def register_page(app: BrowserPuppetApp) -> PageState:
    context = ContextState(
        context_id="context-1",
        browser_name="chromium",
        browser=None,
        playwright_context=None,
        artifact_dir=app.state.artifacts_root,
    )
    page_state = PageState(page_id="page-1", context_id=context.context_id, playwright_page=FakePage())
    context.pages[page_state.page_id] = page_state
    app.state.contexts[context.context_id] = context
    return page_state


@pytest.mark.asyncio
async def test_websocket_tools_track_messages_and_pagination() -> None:
    app = BrowserPuppetApp()
    page_state = register_page(app)
    socket = FakeSocket("wss://example.test/socket")

    app._record_websocket(page_state, socket)
    socket_id = next(iter(page_state.websocket_map))
    socket.emit("framesent", "hello")
    socket.emit("framereceived", b"\x00\x01")

    listed = await app.list_websockets("page-1")
    messages = await app.get_websocket_messages(socket_id, limit=1)

    assert listed["websockets"][0]["socket_id"] == socket_id
    assert listed["websockets"][0]["message_count"] == 2
    assert messages["items"][0]["direction"] == "sent"
    assert messages["items"][0]["payload"]["kind"] == "text"
    assert messages["next_cursor"] == "1"


@pytest.mark.asyncio
async def test_websocket_close_updates_socket_state() -> None:
    app = BrowserPuppetApp()
    page_state = register_page(app)
    socket = FakeSocket("wss://example.test/socket")

    app._record_websocket(page_state, socket)
    socket.emit("close")

    listed = await app.list_websockets("page-1")

    assert listed["websockets"][0]["status"] == "closed"


@pytest.mark.asyncio
async def test_get_websocket_messages_rejects_unknown_socket() -> None:
    app = BrowserPuppetApp()
    register_page(app)

    with pytest.raises(Exception) as excinfo:
        await app.get_websocket_messages("ws-missing")

    assert getattr(excinfo.value, "error_code", None) == "websocket_not_found"
