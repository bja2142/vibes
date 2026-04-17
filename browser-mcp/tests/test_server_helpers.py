import asyncio

import pytest


pytest.importorskip("mcp")
pytest.importorskip("playwright.async_api")

from browser_puppet.errors import SemanticError
from browser_puppet.models import ContextState, PageState
from browser_puppet.server import BrowserPuppetApp


def test_diff_states_reports_changed_fields() -> None:
    app = BrowserPuppetApp()

    diff = app.diff_states({"url": "a", "count": 1}, {"url": "b", "count": 1, "title": "T"}, mode="standard")

    assert diff["meaningful_change"] is True
    assert "url" in diff["changed_fields"]
    assert "title" in diff["changed_fields"]


def test_normalize_exception_preserves_semantic_shape() -> None:
    app = BrowserPuppetApp()
    exc = SemanticError("element_not_found", "missing", target={"page_id": "p1"}, retryable=True)

    payload = app.normalize_exception(exc)

    assert payload["error_code"] == "element_not_found"
    assert payload["retryable"] is True
    assert payload["target"] == {"page_id": "p1"}


def test_normalize_exception_explains_blocked_by_client() -> None:
    app = BrowserPuppetApp()
    exc = RuntimeError("Page.goto: net::ERR_BLOCKED_BY_CLIENT at http://10.0.2.2:8081/labs/hlf/")

    payload = app.normalize_exception(exc)

    assert payload["error_code"] == "request_blocked"
    assert "browser-puppet" in payload["message"]
    assert payload["target"] == {
        "url": "http://10.0.2.2:8081/labs/hlf/",
        "hostname": "10.0.2.2",
    }
    assert any("allow_local_network" in step for step in payload["next_steps"])


class FakeRequest:
    def __init__(self, url: str, method: str = "GET", resource_type: str = "document") -> None:
        self.url = url
        self.method = method
        self.resource_type = resource_type
        self.headers = {}


class FakeResponse:
    def __init__(self, request: FakeRequest, status: int, status_text: str = "Error") -> None:
        self.request = request
        self.status = status
        self.status_text = status_text
        self.headers = {}
        self.url = request.url

    async def body(self) -> bytes:
        return b""


class FakeConsoleMessage:
    def __init__(self, text: str, type: str = "error") -> None:
        self.text = text
        self.type = type
        self.location = {"url": "http://app.test/app.js", "lineNumber": 12, "columnNumber": 7}


class FakePage:
    def __init__(self, url: str = "http://app.test/") -> None:
        self.url = url


def register_page() -> tuple[BrowserPuppetApp, PageState]:
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


@pytest.mark.asyncio
async def test_record_response_queues_network_issue_notice_once() -> None:
    app, page_state = register_page()
    request = FakeRequest("http://app.test/api/items?view=full")
    response = FakeResponse(request, status=500, status_text="Server Error")

    await app._record_response(page_state, response)

    payload = app._attach_page_issue_notices(page_state, {"page_id": page_state.page_id, "ok": True})

    assert payload["issue_notices"] == [
        {
            "kind": "network_error",
            "summary": "GET /api/items?view=full: 500",
            "message": "Network request error observed while waiting on this page: GET /api/items?view=full: 500",
            "method": "GET",
            "route": "/api/items?view=full",
            "status": 500,
        }
    ]
    assert app._attach_page_issue_notices(page_state, {"page_id": page_state.page_id}) == {"page_id": page_state.page_id}


def test_record_console_message_queues_console_issue_notice_once() -> None:
    app, page_state = register_page()

    app._record_console_message(page_state, FakeConsoleMessage("ReferenceError: widget is not defined"))

    payload = app._attach_page_issue_notices(page_state, {"page_id": page_state.page_id, "ok": True})

    assert payload["issue_notices"][0]["kind"] == "console_error"
    assert "JavaScript console errors" in payload["issue_notices"][0]["summary"]
    assert payload["issue_notices"][0]["latest_error"] == "ReferenceError: widget is not defined"
    assert app._attach_page_issue_notices(page_state, {"page_id": page_state.page_id}) == {"page_id": page_state.page_id}


@pytest.mark.asyncio
async def test_await_with_page_issue_interrupt_raises_semantic_error() -> None:
    app, page_state = register_page()

    async def trigger_issue() -> None:
        await asyncio.sleep(0.01)
        app._queue_page_issue_notice(
            page_state,
            {
                "key": "console:boom",
                "kind": "console_error",
                "summary": "JavaScript console errors were observed on this page.",
                "message": "JavaScript console errors were observed while waiting on this page. Check console/runtime diagnostics.",
            },
        )

    async def blocked() -> None:
        await asyncio.sleep(60)

    trigger = asyncio.create_task(trigger_issue())
    with pytest.raises(SemanticError) as excinfo:
        await app._await_with_page_issue_interrupt(page_state, blocked())
    await trigger

    assert excinfo.value.error_code == "page_issue_interrupt"
    assert excinfo.value.target == {"page_id": "page-1", "issue": "JavaScript console errors were observed on this page."}


@pytest.mark.asyncio
async def test_execute_page_js_times_out_cleanly() -> None:
    app, page_state = register_page()

    class SlowPage(FakePage):
        async def evaluate(self, script: str):
            await asyncio.sleep(60)

    page_state.playwright_page = SlowPage()

    with pytest.raises(SemanticError) as excinfo:
        await app.execute_page_js(page_state.page_id, "() => 1", timeout_ms=10)

    assert excinfo.value.error_code == "script_timeout"
    assert excinfo.value.target == {"page_id": page_state.page_id, "timeout_ms": 10}
