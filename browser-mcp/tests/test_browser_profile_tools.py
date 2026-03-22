from __future__ import annotations

import json
from pathlib import Path

import pytest

from browser_puppet.models import ContextState, PageState
from browser_puppet.server import BrowserPuppetApp


class FakeRuntime:
    def __init__(self) -> None:
        self.cookies_added = []
        self.storage_state_payload = {
            "cookies": [{"name": "sid", "value": "123", "domain": "localhost", "path": "/"}],
            "origins": [{"origin": "http://localhost:8080", "localStorage": [{"name": "theme", "value": "dark"}]}],
        }

    def on(self, *_args, **_kwargs) -> None:
        return None

    async def add_cookies(self, cookies) -> None:
        self.cookies_added.extend(cookies)

    async def storage_state(self, path=None):
        if path:
            Path(path).write_text(json.dumps(self.storage_state_payload))
        return self.storage_state_payload


class FakePage:
    def __init__(self, url: str = "http://localhost:8080") -> None:
        self.url = url
        self.init_scripts = []
        self.storage_payload = {
            "localStorage": {"theme": "dark"},
            "sessionStorage": {"token": "abc"},
        }
        self.fingerprint_payload = {
            "navigator": {"userAgent": "FakeBrowser/1.0", "language": "en-US"},
            "viewport": {"innerWidth": 1280, "innerHeight": 800},
            "screen": {"width": 1280, "height": 800},
            "locale": {"timezone": "UTC"},
            "storage": {"localStorageKeys": ["theme"], "sessionStorageKeys": ["token"]},
            "permissions": {"geolocation": "granted"},
        }
        self.seed_applied = None

    def is_closed(self) -> bool:
        return False

    async def add_init_script(self, script: str, arg=None) -> None:
        self.init_scripts.append((script, arg))

    async def evaluate(self, script: str, arg=None):
        if "window.localStorage.setItem" in script:
            self.seed_applied = arg
            return {"localStorage": len(arg.get("localStorage", {})), "sessionStorage": len(arg.get("sessionStorage", {}))}
        if "Object.fromEntries(Object.entries(window.localStorage))" in script:
            return self.storage_payload
        if "const nav = navigator" in script:
            return self.fingerprint_payload
        raise AssertionError(f"Unexpected script: {script[:80]}")


def build_app(tmp_path: Path) -> tuple[BrowserPuppetApp, ContextState, PageState, FakeRuntime, FakePage]:
    app = BrowserPuppetApp()
    runtime = FakeRuntime()
    context = ContextState(
        context_id="context-1",
        browser_name="chromium",
        browser=None,
        playwright_context=runtime,
        artifact_dir=tmp_path,
    )
    page = FakePage()
    page_state = PageState(page_id="page-1", context_id=context.context_id, playwright_page=page)
    context.pages[page_state.page_id] = page_state
    app.state.contexts[context.context_id] = context
    page_state.buffers.network.append(
        {
            "request_id": "req-1",
            "resource_type": "document",
            "headers": {"user-agent": "FakeBrowser/1.0", "accept-language": "en-US,en;q=0.9"},
        }
    )
    return app, context, page_state, runtime, page


@pytest.mark.asyncio
async def test_import_browser_profile_seeds_cookies_and_storage(tmp_path: Path) -> None:
    app, context, _page_state, runtime, page = build_app(tmp_path)

    result = await app.import_browser_profile(
        context.context_id,
        {
            "cookies": [{"name": "sid", "value": "123", "domain": "localhost", "path": "/"}],
            "origins": [
                {
                    "origin": "http://localhost:8080",
                    "localStorage": [{"name": "theme", "value": "dark"}],
                    "sessionStorage": [{"name": "token", "value": "abc"}],
                }
            ],
        },
    )

    assert result["success"] is True
    assert runtime.cookies_added[0]["name"] == "sid"
    assert context.storage_seed["http://localhost:8080"]["sessionStorage"]["token"] == "abc"
    assert page.seed_applied["localStorage"]["theme"] == "dark"


@pytest.mark.asyncio
async def test_export_browser_profile_writes_profile_artifact(tmp_path: Path) -> None:
    app, context, _page_state, _runtime, _page = build_app(tmp_path)
    context.storage_seed = {
        "http://localhost:8080": {
            "localStorage": {"theme": "dark"},
            "sessionStorage": {"token": "abc"},
        }
    }

    result = await app.export_browser_profile(context.context_id)

    payload = json.loads(Path(result["path"]).read_text())
    assert result["artifact"]["kind"] == "browser_profile"
    assert payload["cookies"][0]["name"] == "sid"
    origin = payload["origins"][0]
    assert origin["origin"] == "http://localhost:8080"
    assert {"name": "token", "value": "abc"} in origin["sessionStorage"]


@pytest.mark.asyncio
async def test_install_storage_seed_script_uses_context_seed(tmp_path: Path) -> None:
    app, context, page_state, _runtime, page = build_app(tmp_path)
    context.storage_seed = {
        "http://localhost:8080": {
            "localStorage": {"theme": "dark"},
            "sessionStorage": {"token": "abc"},
        }
    }

    await app._install_storage_seed_script(page_state)

    assert page.init_scripts
    assert page.init_scripts[-1][1]["http://localhost:8080"]["localStorage"]["theme"] == "dark"


@pytest.mark.asyncio
async def test_get_fingerprint_report_returns_runtime_and_request_headers(tmp_path: Path) -> None:
    app, _context, page_state, _runtime, _page = build_app(tmp_path)

    result = await app.get_fingerprint_report(page_state.page_id)

    assert result["report"]["navigator"]["userAgent"] == "FakeBrowser/1.0"
    assert result["document_request_headers"]["accept-language"] == "en-US,en;q=0.9"

