from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from browser_puppet.models import ContextState, PageState
from browser_puppet.server import BrowserPuppetApp


class FakeRuntime:
    def __init__(self) -> None:
        self.routes = []

    async def route(self, pattern: str, handler) -> None:
        self.routes.append((pattern, handler))


class FakePage:
    url = "https://example.test"

    def is_closed(self) -> bool:
        return False

    async def evaluate(self, script: str, arg=None):
        return {"ok": True}


class FakeRoute:
    def __init__(self) -> None:
        self.aborts = []
        self.continues = []

    async def abort(self, code: str) -> None:
        self.aborts.append(code)

    async def continue_(self, **kwargs) -> None:
        self.continues.append(kwargs)


def make_context(app: BrowserPuppetApp, context_id: str = "context-1") -> tuple[ContextState, PageState]:
    context = ContextState(
        context_id=context_id,
        browser_name="chromium",
        browser=None,
        playwright_context=FakeRuntime(),
        artifact_dir=Path(app.state.artifacts_root),
    )
    page_state = PageState(page_id=f"{context_id}-page", context_id=context_id, playwright_page=FakePage())
    context.pages[page_state.page_id] = page_state
    app.state.contexts[context_id] = context
    return context, page_state


@pytest.mark.asyncio
async def test_policy_blocks_localhost_targets() -> None:
    app = BrowserPuppetApp()
    context, _ = make_context(app)
    route = FakeRoute()
    request = SimpleNamespace(url="http://127.0.0.1/admin", headers={})

    await app._apply_host_override_route(context, route, request)

    assert route.aborts == ["blockedbyclient"]
    assert request._browser_puppet_resolution["blocked"] is True


def test_context_limit_enforced() -> None:
    app = BrowserPuppetApp()
    app.max_contexts = 1
    make_context(app, "context-1")

    with pytest.raises(Exception) as excinfo:
        app._check_context_limit()

    assert getattr(excinfo.value, "error_code", None) == "resource_limit"


def test_page_limit_enforced() -> None:
    app = BrowserPuppetApp()
    app.max_pages_per_context = 1
    context, _ = make_context(app)

    with pytest.raises(Exception) as excinfo:
        app._check_page_limit(context)

    assert getattr(excinfo.value, "error_code", None) == "resource_limit"


def test_idle_contexts_are_pruned() -> None:
    app = BrowserPuppetApp()
    context, _ = make_context(app)
    context.last_used_at = 0
    app.idle_timeout_seconds = 1

    app._prune_idle_contexts()

    assert "context-1" not in app.state.contexts


def test_rate_limit_raises_after_threshold() -> None:
    app = BrowserPuppetApp()

    for _ in range(2):
        app._rate_limit("tool", window_seconds=60, max_calls=2)

    with pytest.raises(Exception) as excinfo:
        app._rate_limit("tool", window_seconds=60, max_calls=2)

    assert getattr(excinfo.value, "error_code", None) == "rate_limited"
