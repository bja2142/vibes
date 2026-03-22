from __future__ import annotations

import pytest

from browser_puppet.models import ContextState, PageState
from browser_puppet.server import BrowserPuppetApp


class FakePage:
    def __init__(self, payload: dict, url: str = "https://example.test") -> None:
        self.payload = payload
        self.url = url

    def is_closed(self) -> bool:
        return False

    async def evaluate(self, script: str, arg=None):
        if "mode: 'cors'" in script:
            return self.payload
        raise AssertionError("Unexpected script")


class FakeCDPSession:
    async def send(self, method: str, params: dict) -> dict:
        return {
            "securityState": "secure",
            "visibleSecurityState": {
                "certificateSecurityState": {
                    "protocol": "TLS 1.3",
                    "issuer": "Example CA",
                    "subjectName": "example.test",
                    "validFrom": 1,
                    "validTo": 2,
                    "sanList": ["example.test"],
                }
            },
        }


def register_app(*, browser_name: str = "chromium", cors_payload: dict | None = None) -> tuple[BrowserPuppetApp, ContextState, PageState]:
    app = BrowserPuppetApp()
    context = ContextState(
        context_id="context-1",
        browser_name=browser_name,
        browser=None,
        playwright_context=None,
        artifact_dir=app.state.artifacts_root,
    )
    page_state = PageState(
        page_id="page-1",
        context_id=context.context_id,
        playwright_page=FakePage(cors_payload or {"allowed": True, "status": 200, "type": "cors", "redirected": False, "url": "https://api.test"}),
    )
    if browser_name == "chromium":
        page_state.cdp_session = FakeCDPSession()
    context.pages[page_state.page_id] = page_state
    app.state.contexts[context.context_id] = context
    return app, context, page_state


@pytest.mark.asyncio
async def test_check_cors_reports_allowed_response_and_headers() -> None:
    app, _, page_state = register_app()
    page_state.buffers.network.append(
        {
            "url": "https://api.test",
            "response": {
                "status": 200,
                "headers": {
                    "access-control-allow-origin": "https://example.test",
                    "vary": "Origin",
                },
            },
        }
    )

    result = await app.check_cors("page-1", "https://api.test", "GET")

    assert result["allowed"] is True
    assert result["status"] == 200
    assert result["headers"]["access-control-allow-origin"] == "https://example.test"


@pytest.mark.asyncio
async def test_check_cors_reports_blocked_response() -> None:
    app, _, _ = register_app(cors_payload={"allowed": False, "error": "TypeError: Failed to fetch"})

    result = await app.check_cors("page-1", "https://blocked.test", "POST")

    assert result["allowed"] is False
    assert "Failed to fetch" in result["error"]


@pytest.mark.asyncio
async def test_get_certificate_info_uses_cdp_for_chromium() -> None:
    app, _, _ = register_app(browser_name="chromium")

    result = await app.get_certificate_info("page-1")

    assert result["supported"] is True
    assert result["protocol"] == "TLS 1.3"
    assert result["issuer"] == "Example CA"


@pytest.mark.asyncio
async def test_get_certificate_info_reports_unsupported_browser() -> None:
    app, _, _ = register_app(browser_name="firefox")

    result = await app.get_certificate_info("page-1")

    assert result["supported"] is False
    assert result["browser"] == "firefox"
