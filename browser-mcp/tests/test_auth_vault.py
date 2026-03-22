from __future__ import annotations

from pathlib import Path

import pytest

from browser_puppet.models import ContextState, PageState
from browser_puppet.server import BrowserPuppetApp


class FakeLocator:
    def __init__(self) -> None:
        self.filled = []

    async def fill(self, value: str) -> None:
        self.filled.append(value)

    async def select_option(self, value=None, **kwargs):
        self.filled.append(value)

    async def check(self) -> None:
        return None

    async def uncheck(self) -> None:
        return None


class FakePage:
    url = "https://example.test"

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

    await app.type_text("{{cred:admin_password}}", page_id="page-1")

    assert locator.filled[-1] == "secret"


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
