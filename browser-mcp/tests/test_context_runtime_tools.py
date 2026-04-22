from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from browser_puppet.models import ContextState, PageState
from browser_puppet.server import BrowserPuppetApp


class FakePage:
    url = "https://example.test"

    def __init__(self) -> None:
        self.init_scripts = []
        self.notifications = [
            {"title": "Build complete", "body": "Done", "tag": "job-1", "icon": None},
        ]
        self.clipboard_result = None

    def is_closed(self) -> bool:
        return False

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def evaluate(self, script: str, arg=None):
        if "window.__bp_n" in script:
            items = list(self.notifications)
            self.notifications.clear()
            return items
        if "permissionName" in script and "navigator.clipboard" in script:
            if callable(self.clipboard_result):
                return self.clipboard_result(script, arg)
            if self.clipboard_result is not None:
                return self.clipboard_result
        raise AssertionError("Unexpected page script")


class FakeContextRuntime:
    def __init__(self) -> None:
        self.granted = []
        self.cleared = 0
        self.last_geo = None
        self.closed = 0

    def on(self, event_name: str, callback) -> None:
        return None

    async def grant_permissions(self, permissions):
        self.granted.append(list(permissions))

    async def clear_permissions(self):
        self.cleared += 1

    async def set_geolocation(self, payload):
        self.last_geo = payload

    async def close(self):
        self.closed += 1

    async def add_init_script(self, script: str) -> None:
        return None


class FakeManagedContext(FakeContextRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.init_scripts = []
        self.page_callbacks = []

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    def on(self, event_name: str, callback) -> None:
        if event_name == "page":
            self.page_callbacks.append(callback)


class FakeBrowser:
    version = "145.0.0.0"

    def __init__(self) -> None:
        self.new_context_calls = []
        self.contexts: list[FakeManagedContext] = []

    async def new_context(self, **kwargs):
        self.new_context_calls.append(kwargs)
        context = FakeManagedContext()
        self.contexts.append(context)
        return context


def register_app() -> tuple[BrowserPuppetApp, ContextState, PageState]:
    app = BrowserPuppetApp()
    runtime = FakeContextRuntime()
    context = ContextState(
        context_id="context-1",
        browser_name="chromium",
        browser=None,
        playwright_context=runtime,
        artifact_dir=app.state.artifacts_root,
    )
    page_state = PageState(page_id="page-1", context_id=context.context_id, playwright_page=FakePage())
    context.pages[page_state.page_id] = page_state
    app.state.contexts[context.context_id] = context
    return app, context, page_state


def test_local_network_is_allowed_by_default_and_can_be_disabled() -> None:
    app, context, _ = register_app()

    assert app._blocked_target_reason("localhost", context) is None
    assert app._blocked_target_reason("127.0.0.1", context) is None
    assert app._blocked_target_reason("10.0.2.15", context) is None
    assert app._blocked_target_reason("169.254.169.254", context) is None

    context.config["allow_local_network"] = False

    assert app._blocked_target_reason("localhost", context) == "localhost access is blocked by default policy"
    assert app._blocked_target_reason("127.0.0.1", context) == "loopback access is blocked by default policy"
    assert app._blocked_target_reason("10.0.2.15", context) == "RFC1918/private network access is blocked by default policy"
    assert app._blocked_target_reason("169.254.169.254", context) == "RFC1918/private network access is blocked by default policy"


@pytest.mark.asyncio
async def test_notification_capture_script_and_retrieval() -> None:
    app, _, page_state = register_app()

    await app._install_notification_capture(page_state)
    result = await app.get_pending_notifications("page-1")

    assert page_state.playwright_page.init_scripts
    assert result["notifications"][0]["title"] == "Build complete"


@pytest.mark.asyncio
async def test_set_permission_updates_runtime_and_config() -> None:
    app, context, _ = register_app()

    granted = await app.set_permission("context-1", "clipboard-read", "granted")
    denied = await app.set_permission("context-1", "notifications", "denied")

    assert granted["success"] is True
    assert context.playwright_context.granted == [["clipboard-read"]]
    assert denied["success"] is True
    assert context.playwright_context.cleared == 1
    assert context.config["permission_overrides"]["notifications"] == "denied"


@pytest.mark.asyncio
async def test_update_geolocation_updates_runtime_and_config() -> None:
    app, context, _ = register_app()

    result = await app.update_geolocation("context-1", 1.23, 4.56, 7.89)

    assert result["success"] is True
    assert context.playwright_context.last_geo == {"latitude": 1.23, "longitude": 4.56, "accuracy": 7.89}
    assert context.config["geolocation"]["latitude"] == 1.23


@pytest.mark.asyncio
async def test_set_context_persistence_updates_context_config() -> None:
    app, context, _ = register_app()

    result = await app.set_context_persistence("context-1", True)

    assert result["success"] is True
    assert result["persistent_context"] is True
    assert context.config["persistent_context"] is True


@pytest.mark.asyncio
async def test_set_permission_rejects_invalid_state() -> None:
    app, _, _ = register_app()

    with pytest.raises(Exception) as excinfo:
        await app.set_permission("context-1", "notifications", "unsupported")

    assert getattr(excinfo.value, "error_code", None) == "invalid_permission_state"


@pytest.mark.asyncio
async def test_clipboard_read_reports_unavailable_api_as_semantic_error() -> None:
    app, _, page_state = register_app()
    page_state.playwright_page.url = "http://app.test"
    page_state.playwright_page.clipboard_result = {
        "ok": False,
        "error_code": "clipboard_unavailable",
        "url": "http://app.test",
        "secure_context": False,
        "has_clipboard": False,
        "permission_name": "clipboard-read",
        "permission_state": "granted",
    }

    with pytest.raises(Exception) as excinfo:
        await app.clipboard_read("page-1")

    assert getattr(excinfo.value, "error_code", None) == "clipboard_unavailable"
    assert excinfo.value.target["secure_context"] is False
    assert "secure context" in excinfo.value.likely_causes[0]
    assert "set_insecure_origins_as_secure" in excinfo.value.next_steps


@pytest.mark.asyncio
async def test_clipboard_write_reports_denied_access_as_semantic_error() -> None:
    app, _, page_state = register_app()
    page_state.playwright_page.clipboard_result = {
        "ok": False,
        "error_code": "clipboard_access_denied",
        "url": "https://example.test",
        "secure_context": True,
        "has_clipboard": True,
        "permission_name": "clipboard-write",
        "permission_state": "prompt",
        "error_name": "NotAllowedError",
        "error_message": "Write permission denied.",
    }

    with pytest.raises(Exception) as excinfo:
        await app.clipboard_write("page-1", "hello")

    assert getattr(excinfo.value, "error_code", None) == "clipboard_access_denied"
    assert excinfo.value.target["permission_name"] == "clipboard-write"
    assert "permission is currently prompt" in excinfo.value.likely_causes[0]


@pytest.mark.asyncio
async def test_set_insecure_origins_as_secure_recreates_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app = BrowserPuppetApp()
    runtime = FakeContextRuntime()
    context = ContextState(
        context_id="context-1",
        browser_name="chromium",
        browser=None,
        playwright_context=runtime,
        artifact_dir=tmp_path,
        config={
            "headless": True,
            "allow_local_network": True,
            "permission_overrides": {"clipboard-read": "granted"},
        },
    )
    page_state = PageState(page_id="page-1", context_id=context.context_id, playwright_page=FakePage())
    context.pages[page_state.page_id] = page_state
    context.active_page_id = page_state.page_id
    app.state.contexts[context.context_id] = context
    app.state.current_page_id = page_state.page_id
    browser = FakeBrowser()
    monkeypatch.setattr(app, "ensure_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr(app, "_refresh_context_routes", AsyncMock(return_value=None))

    result = await app.set_insecure_origins_as_secure("context-1", ["http://10.0.2.15:3000/"])

    assert result["success"] is True
    assert result["recreated_context"] is True
    assert result["cleared_pages"] == 1
    assert result["treat_insecure_origins_as_secure"] == ["http://10.0.2.15:3000"]
    assert context.pages == {}
    assert context.active_page_id is None
    assert app.state.current_page_id is None
    assert runtime.closed == 1
    assert browser.new_context_calls[0]["viewport"] == {"width": 1280, "height": 800}
    assert context.config["treat_insecure_origins_as_secure"] == ["http://10.0.2.15:3000"]
    assert context.playwright_context.granted == [["clipboard-read"]]
