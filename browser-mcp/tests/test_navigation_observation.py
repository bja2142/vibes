from __future__ import annotations

import pytest

from browser_puppet.models import ContextState, PageState
from browser_puppet.server import BrowserPuppetApp


class FakePage:
    def __init__(self, url: str = "https://final.test") -> None:
        self.url = url

    def is_closed(self) -> bool:
        return False

    async def title(self) -> str:
        return "Final"


def register_app() -> tuple[BrowserPuppetApp, PageState]:
    app = BrowserPuppetApp()
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
    return app, page_state


def test_build_redirect_chain_orders_requests_from_first_hop_to_final() -> None:
    app, page_state = register_app()
    page_state.request_map = {
        "req-1": {
            "request_id": "req-1",
            "timestamp": 1,
            "url": "https://start.test",
            "resource_type": "document",
            "response": {"status": 302, "status_text": "Found", "url": "https://start.test"},
            "redirect_from_request_id": None,
        },
        "req-2": {
            "request_id": "req-2",
            "timestamp": 2,
            "url": "https://middle.test",
            "resource_type": "document",
            "response": {"status": 302, "status_text": "Found", "url": "https://middle.test"},
            "redirect_from_request_id": "req-1",
        },
        "req-3": {
            "request_id": "req-3",
            "timestamp": 3,
            "url": "https://final.test",
            "resource_type": "document",
            "response": {"status": 200, "status_text": "OK", "url": "https://final.test"},
            "redirect_from_request_id": "req-2",
        },
    }

    chain = app._build_redirect_chain(page_state, "https://final.test")

    assert [item["request_id"] for item in chain] == ["req-1", "req-2", "req-3"]
    assert [item["status"] for item in chain] == [302, 302, 200]


@pytest.mark.asyncio
async def test_action_outcome_includes_redirect_chain() -> None:
    app, page_state = register_app()
    page_state.request_map = {
        "req-1": {
            "request_id": "req-1",
            "timestamp": 1,
            "url": "https://start.test",
            "resource_type": "document",
            "response": {"status": 302, "status_text": "Found", "url": "https://start.test"},
            "redirect_from_request_id": None,
        },
        "req-2": {
            "request_id": "req-2",
            "timestamp": 2,
            "url": "https://final.test",
            "resource_type": "document",
            "response": {"status": 200, "status_text": "OK", "url": "https://final.test"},
            "redirect_from_request_id": "req-1",
        },
    }

    async def fake_digest(page_id: str, mode: str = "compact"):
        return {"url": "https://final.test", "title": "Final"}

    app.get_page_digest = fake_digest  # type: ignore[method-assign]

    outcome = await app.action_outcome(page_state, "navigate", {"url": "https://start.test"})

    assert outcome["redirect_chain"][0]["request_id"] == "req-1"
    assert outcome["redirect_chain"][-1]["status"] == 200
