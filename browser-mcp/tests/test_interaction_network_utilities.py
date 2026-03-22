from __future__ import annotations

import base64
from pathlib import Path

import pytest

from browser_puppet.models import ContextState, PageState
from browser_puppet.server import BrowserPuppetApp


class FakeRoute:
    def __init__(self) -> None:
        self.aborted = None
        self.fulfilled = None
        self.continued = None

    async def abort(self, code: str) -> None:
        self.aborted = code

    async def fulfill(self, **kwargs) -> None:
        self.fulfilled = kwargs

    async def continue_(self, **kwargs) -> None:
        self.continued = kwargs


class FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url
        self.headers = {}


class FakeCdpSession:
    def __init__(self, responses: dict[str, object] | None = None) -> None:
        self.responses = responses or {}
        self.calls = []
        self.listeners = {}

    def on(self, event_name: str, handler) -> None:
        self.listeners[event_name] = handler

    async def send(self, method: str, params=None):
        self.calls.append((method, params or {}))
        return self.responses.get(method, {})


class FakePage:
    def __init__(self, evaluate_result=None) -> None:
        self._evaluate_result = evaluate_result or {}
        self.viewport_size = {"width": 1200, "height": 800}
        self.closed = False
        self.url = "https://example.test"

    async def evaluate(self, script, payload=None):
        return self._evaluate_result

    async def close(self) -> None:
        self.closed = True

    def is_closed(self) -> bool:
        return self.closed


class FakeRuntime:
    def __init__(self, page: FakePage | None = None) -> None:
        self.page = page
        self.route_calls = []
        self.unroute_calls = []

    async def route(self, pattern: str, handler) -> None:
        self.route_calls.append((pattern, handler))

    async def unroute_all(self, behavior=None) -> None:
        self.unroute_calls.append(behavior)

    async def new_page(self) -> FakePage:
        assert self.page is not None
        return self.page

    def on(self, *_args, **_kwargs) -> None:
        return None


def build_context(app: BrowserPuppetApp, tmp_path: Path, browser_name: str = "chromium", runtime=None) -> ContextState:
    context = ContextState(
        context_id="context-1",
        browser_name=browser_name,
        browser=None,
        playwright_context=runtime,
        artifact_dir=tmp_path,
    )
    app.state.contexts[context.context_id] = context
    return context


def build_page(context: ContextState, cdp_session: FakeCdpSession | None = None, page=None) -> PageState:
    page_state = PageState(
        page_id="page-1",
        context_id=context.context_id,
        playwright_page=page or FakePage(),
        cdp_session=cdp_session,
    )
    context.pages[page_state.page_id] = page_state
    return page_state


@pytest.mark.asyncio
async def test_block_routes_aborts_matching_requests(tmp_path: Path) -> None:
    app = BrowserPuppetApp()
    runtime = FakeRuntime()
    context = build_context(app, tmp_path, runtime=runtime)

    await app.block_routes(context.context_id, ["*blocked.test/*"])

    route = FakeRoute()
    request = FakeRequest("https://blocked.test/api")
    await app._handle_context_route(context, route, request)

    assert route.aborted == "blockedbyclient"
    assert runtime.route_calls


@pytest.mark.asyncio
async def test_mock_routes_fulfills_matching_requests(tmp_path: Path) -> None:
    app = BrowserPuppetApp()
    runtime = FakeRuntime()
    context = build_context(app, tmp_path, runtime=runtime)

    await app.mock_routes(
        context.context_id,
        [{"pattern": "*example.test/data*", "status": 201, "body": '{"ok":true}', "headers": {"x-test": "1"}}],
    )

    route = FakeRoute()
    request = FakeRequest("https://example.test/data?id=1")
    await app._handle_context_route(context, route, request)

    assert route.fulfilled["status"] == 201
    assert route.fulfilled["body"] == '{"ok":true}'
    assert route.fulfilled["headers"]["x-test"] == "1"


@pytest.mark.asyncio
async def test_set_user_agent_applies_cdp_override_to_existing_pages(tmp_path: Path) -> None:
    app = BrowserPuppetApp()
    context = build_context(app, tmp_path, runtime=FakeRuntime())
    session = FakeCdpSession(
        responses={
            "Profiler.enable": {},
            "Profiler.startPreciseCoverage": {},
            "CSS.enable": {},
            "CSS.startRuleUsageTracking": {},
            "Emulation.setUserAgentOverride": {},
        }
    )
    build_page(context, cdp_session=session)

    result = await app.set_user_agent(context.context_id, "Agent/1.0")

    assert result["success"] is True
    assert ("Emulation.setUserAgentOverride", {"userAgent": "Agent/1.0"}) in session.calls
    assert context.config["user_agent_override"] == "Agent/1.0"


@pytest.mark.asyncio
async def test_emulate_network_uses_preset_and_applies_to_pages(tmp_path: Path) -> None:
    app = BrowserPuppetApp()
    context = build_context(app, tmp_path, runtime=FakeRuntime())
    session = FakeCdpSession(
        responses={
            "Profiler.enable": {},
            "Profiler.startPreciseCoverage": {},
            "CSS.enable": {},
            "CSS.startRuleUsageTracking": {},
            "Network.enable": {},
            "Network.emulateNetworkConditions": {},
        }
    )
    build_page(context, cdp_session=session)

    result = await app.emulate_network(context.context_id, preset="fast_3g")

    assert result["profile"]["latency_ms"] == 150
    assert ("Network.enable", {}) in session.calls
    assert any(call[0] == "Network.emulateNetworkConditions" for call in session.calls)


@pytest.mark.asyncio
async def test_pinch_zoom_sends_cdp_gesture(tmp_path: Path) -> None:
    app = BrowserPuppetApp()
    context = build_context(app, tmp_path, runtime=FakeRuntime())
    session = FakeCdpSession({"Input.synthesizePinchGesture": {}})
    build_page(context, cdp_session=session)

    result = await app.pinch_zoom("page-1", 1.2)

    assert result["success"] is True
    assert session.calls[0][0] == "Input.synthesizePinchGesture"


@pytest.mark.asyncio
async def test_get_visual_diff_registers_artifact(tmp_path: Path) -> None:
    app = BrowserPuppetApp()
    app.state.artifacts_root = tmp_path
    diff_bytes = b"fake-png-diff"
    scratch_page = FakePage(
        {
            "width": 2,
            "height": 2,
            "changed_pixels": 1,
            "total_pixels": 4,
            "diff_base64": base64.b64encode(diff_bytes).decode("ascii"),
        }
    )
    context = build_context(app, tmp_path, runtime=FakeRuntime(page=scratch_page))
    baseline = tmp_path / "baseline.png"
    candidate = tmp_path / "candidate.png"
    baseline.write_bytes(b"baseline")
    candidate.write_bytes(b"candidate")

    result = await app.get_visual_diff(context.context_id, str(baseline), str(candidate))

    assert result["artifact"]["kind"] == "visual_diff"
    assert Path(result["path"]).read_bytes() == diff_bytes
    assert result["summary"]["changed_pixels"] == 1
    assert scratch_page.closed is True


@pytest.mark.asyncio
async def test_get_coverage_returns_js_and_css_usage(tmp_path: Path) -> None:
    app = BrowserPuppetApp()
    context = build_context(app, tmp_path, runtime=FakeRuntime())
    session = FakeCdpSession(
        responses={
            "Profiler.takePreciseCoverage": {
                "result": [
                    {
                        "url": "https://example.test/app.js",
                        "functions": [
                            {
                                "ranges": [
                                    {"startOffset": 0, "endOffset": 100, "count": 1},
                                    {"startOffset": 100, "endOffset": 150, "count": 0},
                                ]
                            }
                        ],
                    }
                ]
            },
            "CSS.stopRuleUsageTracking": {
                "ruleUsage": [
                    {"styleSheetId": "sheet-1", "startOffset": 0, "endOffset": 10, "used": True},
                    {"styleSheetId": "sheet-1", "startOffset": 10, "endOffset": 20, "used": False},
                ]
            },
            "CSS.startRuleUsageTracking": {},
            "CSS.getStyleSheetText": {"text": "x" * 20},
        }
    )
    page_state = build_page(context, cdp_session=session)
    page_state.coverage_started = True
    page_state.coverage_stylesheets["sheet-1"] = {"sourceURL": "https://example.test/app.css"}

    result = await app.get_coverage(page_state.page_id)

    assert result["javascript"]["used_bytes"] == 100
    assert result["javascript"]["unused_bytes"] == 50
    assert result["css"]["used_bytes"] == 10
    assert result["css"]["unused_bytes"] == 10
    assert result["css"]["entries"][0]["url"] == "https://example.test/app.css"


@pytest.mark.asyncio
async def test_get_coverage_rejects_non_chromium(tmp_path: Path) -> None:
    app = BrowserPuppetApp()
    context = build_context(app, tmp_path, browser_name="firefox", runtime=FakeRuntime())
    build_page(context)

    with pytest.raises(Exception) as excinfo:
        await app.get_coverage("page-1")

    assert getattr(excinfo.value, "error_code", None) == "unsupported_browser"
