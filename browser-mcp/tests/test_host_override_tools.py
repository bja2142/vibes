from __future__ import annotations

from types import SimpleNamespace

import pytest

from browser_puppet.models import ContextState, PageState
from browser_puppet.server import BrowserPuppetApp


class FakeRuntime:
    def __init__(self) -> None:
        self.routes = []
        self.unroute_calls = 0

    async def route(self, pattern: str, handler) -> None:
        self.routes.append((pattern, handler))

    async def unroute_all(self, behavior: str | None = None) -> None:
        self.unroute_calls += 1


class FakePage:
    url = "https://example.test"

    def is_closed(self) -> bool:
        return False


class FakeRoute:
    def __init__(self) -> None:
        self.calls = []

    async def continue_(self, **kwargs) -> None:
        self.calls.append(kwargs)


def register_app() -> tuple[BrowserPuppetApp, ContextState, PageState]:
    app = BrowserPuppetApp()
    runtime = FakeRuntime()
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


@pytest.mark.asyncio
async def test_set_host_overrides_registers_context_route() -> None:
    app, context, _ = register_app()

    result = await app.set_host_overrides(context.context_id, {"app.test": "127.0.0.1"})

    assert result["success"] is True
    assert context.host_overrides == {"app.test": "127.0.0.1"}
    assert context.playwright_context.routes


@pytest.mark.asyncio
async def test_apply_host_override_route_rewrites_request_and_sets_resolution() -> None:
    app, context, _ = register_app()
    context.host_overrides = {"app.test": "127.0.0.1"}
    route = FakeRoute()
    request = SimpleNamespace(
        url="https://app.test/login?x=1",
        headers={"accept": "*/*"},
    )

    await app._apply_host_override_route(context, route, request)

    assert route.calls[0]["url"].startswith("https://127.0.0.1")
    assert route.calls[0]["headers"]["host"] == "app.test"
    assert request._browser_puppet_resolution["override_hit"] is True
    assert request._browser_puppet_resolution["effective_ip"] == "127.0.0.1"


@pytest.mark.asyncio
async def test_get_dns_resolution_reports_matching_requests() -> None:
    app, context, page_state = register_app()
    context.host_overrides = {"app.test": "127.0.0.1"}
    page_state.buffers.network.append(
        {
            "request_id": "req-1",
            "url": "https://app.test/login",
            "resolution": {
                "hostname": "app.test",
                "override_hit": True,
                "effective_ip": "127.0.0.1",
                "rewritten_url": "https://127.0.0.1/login",
            },
        }
    )

    result = await app.get_dns_resolution(page_state.page_id, "app.test")

    assert result["override_hit"] is True
    assert result["effective_ip"] == "127.0.0.1"
    assert result["matches"][0]["request_id"] == "req-1"
