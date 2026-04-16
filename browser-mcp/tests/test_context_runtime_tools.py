from __future__ import annotations

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

    def is_closed(self) -> bool:
        return False

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def evaluate(self, script: str, arg=None):
        if "window.__bp_n" in script:
            items = list(self.notifications)
            self.notifications.clear()
            return items
        raise AssertionError("Unexpected page script")


class FakeContextRuntime:
    def __init__(self) -> None:
        self.granted = []
        self.cleared = 0
        self.last_geo = None

    def on(self, event_name: str, callback) -> None:
        return None

    async def grant_permissions(self, permissions):
        self.granted.append(list(permissions))

    async def clear_permissions(self):
        self.cleared += 1

    async def set_geolocation(self, payload):
        self.last_geo = payload


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
async def test_set_permission_rejects_invalid_state() -> None:
    app, _, _ = register_app()

    with pytest.raises(Exception) as excinfo:
        await app.set_permission("context-1", "notifications", "unsupported")

    assert getattr(excinfo.value, "error_code", None) == "invalid_permission_state"
