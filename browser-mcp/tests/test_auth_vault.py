from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from browser_puppet.models import ContextState, PageState
from browser_puppet.server import BrowserPuppetApp


class FakeLocator:
    def __init__(self) -> None:
        self.filled = []
        self.clicked = 0
        self.submitted = 0

    async def fill(self, value: str) -> None:
        self.filled.append(value)

    async def select_option(self, value=None, **kwargs):
        self.filled.append(value)

    async def check(self) -> None:
        return None

    async def uncheck(self) -> None:
        return None

    async def click(self, **kwargs) -> None:
        self.clicked += 1

    async def evaluate(self, script: str, *args) -> dict[str, bool]:
        if "requestSubmit" in script:
            self.submitted += 1
            return {"submitted": True}
        return {"submitted": False}


class FakePage:
    url = "https://example.test"

    class Keyboard:
        def __init__(self) -> None:
            self.typed = []
            self.pressed = []

        async def type(self, text: str, delay: int | None = None) -> None:
            self.typed.append((text, delay))

        async def press(self, key: str) -> None:
            self.pressed.append(key)

    def __init__(self) -> None:
        self.keyboard = self.Keyboard()

    def is_closed(self) -> bool:
        return False


def register_app() -> tuple[BrowserPuppetApp, ContextState, PageState, FakeLocator]:
    app = BrowserPuppetApp()
    context = ContextState(
        context_id="context-1",
        browser_name="chromium",
        browser=None,
        playwright_context=None,
        artifact_dir=Path(app.state.artifacts_root),
    )
    page_state = PageState(page_id="page-1", context_id=context.context_id, playwright_page=FakePage())
    context.pages[page_state.page_id] = page_state
    app.state.contexts[context.context_id] = context
    locator = FakeLocator()
    async def fake_resolve_locator(*args, **kwargs):
        return page_state, locator
    async def fake_before_mutation(*args, **kwargs):
        return {}
    async def fake_action_outcome(*args, **kwargs):
        return {"success": True}
    app.resolve_locator = fake_resolve_locator  # type: ignore[method-assign]
    app.before_mutation = fake_before_mutation  # type: ignore[method-assign]
    app.action_outcome = fake_action_outcome  # type: ignore[method-assign]
    return app, context, page_state, locator


@pytest.mark.asyncio
async def test_store_list_and_delete_credentials() -> None:
    app, context, _, _ = register_app()

    await app.store_credential(context.context_id, "admin_password", "secret")
    aliases = await app.list_credentials(context.context_id)
    deleted = await app.delete_credential(context.context_id, "admin_password")

    assert aliases["aliases"] == ["admin_password"]
    assert deleted["success"] is True


@pytest.mark.asyncio
async def test_type_text_resolves_credential_alias() -> None:
    app, context, _, locator = register_app()
    context.credentials["admin_password"] = "secret"

    await app.type_text("{{cred:admin_password}}", page_id="page-1", target={"selector": "#admin-password"})

    assert locator.filled[-1] == "secret"


@pytest.mark.asyncio
async def test_type_text_keystroke_defaults_choose_randomized_timings() -> None:
    app, _, page_state, _ = register_app()

    with patch("browser_puppet.server.random.randint", side_effect=[41, 13, -3, 7, 0]):
        await app.type_text("abc", page_id=page_state.page_id, clear_first=False, typing_mode="keystrokes")

    assert page_state.playwright_page.keyboard.typed == [("a", 38), ("b", 48), ("c", 41)]


@pytest.mark.asyncio
async def test_fill_form_resolves_credential_aliases() -> None:
    app, context, _, locator = register_app()
    context.credentials["username"] = "alice"

    await app.fill_form(
        "page-1",
        fields=[{"target": {"selector": "#user"}, "value": "{{cred:username}}"}],
    )

    assert locator.filled[-1] == "alice"


@pytest.mark.asyncio
async def test_fill_and_click_resolves_credential_aliases() -> None:
    app, context, _, locator = register_app()
    context.credentials["username"] = "alice"

    await app.fill_and_click(
        "page-1",
        fields=[{"target": {"selector": "#user"}, "value": "{{cred:username}}"}],
        click_target={"selector": "#submit"},
    )

    assert locator.filled[-1] == "alice"
    assert locator.clicked == 1


@pytest.mark.asyncio
async def test_submit_form_triggers_form_submission() -> None:
    app, _, _, locator = register_app()

    await app.submit_form(page_id="page-1", target={"selector": "form.form-stack"})

    assert locator.submitted == 1


@pytest.mark.asyncio
async def test_missing_credential_alias_raises_semantic_error() -> None:
    app, _, _, _ = register_app()

    with pytest.raises(Exception) as excinfo:
        await app.type_text("{{cred:missing}}", page_id="page-1")

    assert getattr(excinfo.value, "error_code", None) == "credential_not_found"


def test_create_context_rejects_missing_ca_bundle_path() -> None:
    app = BrowserPuppetApp()

    with pytest.raises(Exception) as excinfo:
        # synchronous validation path before browser launch
        import asyncio
        asyncio.run(app.create_context("chromium", {"ca_bundle_path": "/tmp/does-not-exist.pem"}))

    assert getattr(excinfo.value, "error_code", None) == "ca_bundle_not_found"
