from __future__ import annotations

import pytest

from browser_puppet.models import ContextState, PageState
from browser_puppet.server import BrowserPuppetApp


class FakeWorker:
    def __init__(self, url: str) -> None:
        self.url = url

    async def evaluate(self, script: str):
        return {"script": script, "ok": True}


class FakePage:
    url = "https://example.test"

    def __init__(self) -> None:
        self.cache_payload = {
            "supported": True,
            "caches": [{"cache_name": "v1", "entry_count": 2, "sample_urls": ["https://example.test/a"]}],
        }
        self.clear_payload = {"supported": True, "removed": 1}
        self.manifest_payload = {"url": "https://example.test/manifest.json", "status": 200, "manifest": {"name": "Example"}}
        self.unregister_payload = {"removed": 1}

    def is_closed(self) -> bool:
        return False

    async def evaluate(self, script: str, arg=None):
        if "navigator.serviceWorker.getRegistrations" in script:
            return self.unregister_payload
        if "caches.keys" in script and "caches.delete" in script:
            return self.clear_payload
        if "caches.keys" in script:
            return self.cache_payload
        if "link[rel=\"manifest\"]" in script:
            return self.manifest_payload
        raise AssertionError("Unexpected script")


class FakeContextRuntime:
    def __init__(self) -> None:
        self.service_workers = []
        self.listeners = {}

    def on(self, event_name: str, callback) -> None:
        self.listeners.setdefault(event_name, []).append(callback)


def register_page(app: BrowserPuppetApp) -> tuple[ContextState, PageState]:
    context_runtime = FakeContextRuntime()
    context = ContextState(
        context_id="context-1",
        browser_name="chromium",
        browser=None,
        playwright_context=context_runtime,
        artifact_dir=app.state.artifacts_root,
    )
    page_state = PageState(page_id="page-1", context_id=context.context_id, playwright_page=FakePage())
    context.pages[page_state.page_id] = page_state
    app.state.contexts[context.context_id] = context
    return context, page_state


@pytest.mark.asyncio
async def test_worker_registry_and_evaluate_worker() -> None:
    app = BrowserPuppetApp()
    _, page_state = register_page(app)
    worker = FakeWorker("https://example.test/worker.js")

    app._record_worker(page_state, worker)
    worker_id = next(iter(page_state.worker_map))

    listed = await app.list_web_workers("page-1")
    evaluated = await app.evaluate_worker(worker_id, "() => 1")

    assert listed["workers"][0]["worker_id"] == worker_id
    assert evaluated["result"]["ok"] is True


@pytest.mark.asyncio
async def test_service_worker_registry_and_unregistration() -> None:
    app = BrowserPuppetApp()
    context, _ = register_page(app)
    worker = FakeWorker("https://example.test/sw.js")

    app._record_service_worker(context, worker)
    listed = await app.list_service_workers("context-1")
    removed = await app.unregister_service_worker("context-1", "https://example.test/sw.js")

    assert listed["service_workers"][0]["url"] == "https://example.test/sw.js"
    assert removed["success"] is True


@pytest.mark.asyncio
async def test_cache_storage_and_manifest_tools() -> None:
    app = BrowserPuppetApp()
    register_page(app)

    cache_payload = await app.get_cache_storage("page-1")
    cleared = await app.clear_cache_storage("page-1", "v1")
    manifest = await app.get_manifest("page-1")

    assert cache_payload["supported"] is True
    assert cache_payload["caches"][0]["cache_name"] == "v1"
    assert cleared["removed"] == 1
    assert manifest["manifest"]["manifest"]["name"] == "Example"


@pytest.mark.asyncio
async def test_evaluate_worker_rejects_unknown_worker() -> None:
    app = BrowserPuppetApp()
    register_page(app)

    with pytest.raises(Exception) as excinfo:
        await app.evaluate_worker("worker-missing", "() => 1")

    assert getattr(excinfo.value, "error_code", None) == "worker_not_found"
