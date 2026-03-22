from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from playwright.async_api import Browser, BrowserContext, ElementHandle, Frame, Locator, Page, async_playwright

from .config import DEFAULT_ARTIFACT_DIR, DEFAULT_SSE_HOST, DEFAULT_SSE_PORT, DEFAULT_TIMEOUT_MS, MAX_INLINE_TEXT
from .errors import SemanticError
from .models import ArtifactRecord, ContextState, ElementRecord, PageState, ServerState, SessionDefaults
from .utils import compute_totp, ensure_dir, new_id, origin_from_url, safe_json, summarize_text, utc_ts


def tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    return payload


class BrowserPuppetApp:
    def __init__(self) -> None:
        self.state = ServerState(artifacts_root=ensure_dir(DEFAULT_ARTIFACT_DIR))

    async def start(self) -> None:
        if self.state.playwright is None:
            self.state.playwright = await async_playwright().start()

    async def stop(self) -> None:
        for context_id in list(self.state.contexts):
            await self.close_context(context_id)
        if self.state.playwright is not None:
            await self.state.playwright.stop()
            self.state.playwright = None

    async def ensure_browser(self, browser_name: str) -> Browser:
        await self.start()
        browser = self.state.browser_pool.get(browser_name)
        if browser is not None:
            return browser
        assert self.state.playwright is not None
        browser_type = getattr(self.state.playwright, browser_name)
        browser = await browser_type.launch(headless=True)
        self.state.browser_pool[browser_name] = browser
        return browser

    def get_context(self, context_id: str) -> ContextState:
        context = self.state.contexts.get(context_id)
        if context is None:
            raise SemanticError(
                "context_not_found",
                f"Unknown context_id '{context_id}'.",
                target={"context_id": context_id},
                next_steps=["create_context"],
            )
        return context

    def get_page_state(self, page_id: str) -> PageState:
        for context in self.state.contexts.values():
            page_state = context.pages.get(page_id)
            if page_state is not None:
                return page_state
        raise SemanticError(
            "page_not_found",
            f"Unknown page_id '{page_id}'.",
            target={"page_id": page_id},
            next_steps=["list_pages", "open_page"],
        )

    async def register_page(self, context: ContextState, page: Page) -> PageState:
        page_id = new_id("page")
        page_state = PageState(page_id=page_id, context_id=context.context_id, playwright_page=page)
        context.pages[page_id] = page_state
        context.active_page_id = page_id
        self.state.current_page_id = page_id
        self._bind_page_listeners(page_state)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
        except Exception:
            pass
        await self.capture_page_meta(page_state)
        return page_state

    def _bind_page_listeners(self, page_state: PageState) -> None:
        page = page_state.playwright_page

        def add_page_event(kind: str, payload: dict[str, Any]) -> None:
            page_state.buffers.page_events.append({"kind": kind, "timestamp": utc_ts(), **payload})

        page.on(
            "console",
            lambda msg: page_state.buffers.console.append(
                {
                    "timestamp": utc_ts(),
                    "type": msg.type,
                    "text": summarize_text(msg.text, 500),
                    "location": safe_json(msg.location),
                }
            ),
        )
        page.on(
            "pageerror",
            lambda exc: page_state.buffers.errors.append(
                {"timestamp": utc_ts(), "message": summarize_text(str(exc), 800)}
            ),
        )
        page.on(
            "dialog",
            lambda dialog: page_state.buffers.dialogs.append(
                {
                    "timestamp": utc_ts(),
                    "type": dialog.type,
                    "message": summarize_text(dialog.message, 500),
                    "default_value": dialog.default_value,
                }
            ),
        )
        page.on(
            "framenavigated",
            lambda frame: add_page_event(
                "navigation",
                {"url": frame.url, "frame_name": frame.name, "is_main_frame": frame == page.main_frame},
            ),
        )
        page.on("load", lambda: add_page_event("load", {"url": page.url}))
        page.on("domcontentloaded", lambda: add_page_event("domcontentloaded", {"url": page.url}))
        page.on(
            "request",
            lambda request: self._record_request(page_state, request),
        )
        page.on(
            "response",
            lambda response: asyncio.create_task(self._record_response(page_state, response)),
        )
        page.on(
            "download",
            lambda download: asyncio.create_task(self._record_download(page_state, download)),
        )
        page.on(
            "websocket",
            lambda socket: page_state.buffers.websockets.append(
                {"timestamp": utc_ts(), "url": socket.url, "socket_id": new_id("ws")}
            ),
        )

    def _record_request(self, page_state: PageState, request: Any) -> None:
        request_id = new_id("req")
        record = {
            "request_id": request_id,
            "timestamp": utc_ts(),
            "url": request.url,
            "method": request.method,
            "resource_type": request.resource_type,
            "headers": dict(request.headers),
            "post_data": summarize_text(request.post_data or "", 500),
            "frame_url": request.frame.url if request.frame else None,
        }
        page_state.buffers.network.append(record)
        page_state.request_map[request_id] = record
        setattr(request, "_browser_puppet_request_id", request_id)

    async def _record_response(self, page_state: PageState, response: Any) -> None:
        request = response.request
        request_id = getattr(request, "_browser_puppet_request_id", new_id("req"))
        entry = page_state.request_map.setdefault(
            request_id,
            {
                "request_id": request_id,
                "timestamp": utc_ts(),
                "url": request.url,
                "method": request.method,
                "resource_type": request.resource_type,
                "headers": dict(request.headers),
            },
        )
        entry["response"] = {
            "status": response.status,
            "status_text": response.status_text,
            "headers": dict(response.headers),
            "url": response.url,
        }
        if len(page_state.response_bodies) < 50:
            try:
                body = await response.body()
                page_state.response_bodies[request_id] = body[:1_000_000]
            except Exception:
                pass
        csp = response.headers.get("content-security-policy")
        if csp:
            page_state.buffers.csp_violations.append(
                {
                    "timestamp": utc_ts(),
                    "url": response.url,
                    "policy": summarize_text(csp, 600),
                }
            )

    async def _record_download(self, page_state: PageState, download: Any) -> None:
        context = self.get_context(page_state.context_id)
        target_path = context.artifact_dir / "downloads" / (download.suggested_filename or new_id("download"))
        ensure_dir(target_path.parent)
        try:
            await download.save_as(target_path)
        except Exception:
            return
        record = {
            "timestamp": utc_ts(),
            "path": str(target_path.resolve()),
            "suggested_filename": download.suggested_filename,
            "url": download.url,
        }
        page_state.buffers.downloads.append(record)
        self._add_artifact(context, "download", str(target_path.resolve()), page_state.page_id, "download", record)

    def _add_artifact(
        self,
        context: ContextState,
        kind: str,
        path: str,
        page_id: str | None,
        tool: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = ArtifactRecord(
            artifact_id=new_id("artifact"),
            kind=kind,
            path=path,
            page_id=page_id,
            context_id=context.context_id,
            tool=tool,
            created_at=utc_ts(),
            metadata=metadata or {},
        )
        context.artifacts.append(record)
        return {
            "artifact_id": record.artifact_id,
            "kind": kind,
            "path": path,
            "page_id": page_id,
            "tool": tool,
        }

    async def create_context(self, browser: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
        profile = profile or {}
        browser_instance = await self.ensure_browser(browser)
        context_id = new_id("context")
        artifact_dir = ensure_dir(self.state.artifacts_root / context_id)
        context_kwargs = self._build_context_kwargs(artifact_dir, profile)
        playwright_context = await browser_instance.new_context(**context_kwargs)
        context_state = ContextState(
            context_id=context_id,
            browser_name=browser,
            browser=browser_instance,
            playwright_context=playwright_context,
            artifact_dir=artifact_dir,
            config=context_kwargs,
        )
        self.state.contexts[context_id] = context_state
        playwright_context.on(
            "page",
            lambda page: asyncio.create_task(self.register_page(context_state, page)),
        )
        return tool_result({"context_id": context_id, "effective_config": safe_json(context_kwargs)})

    def _build_context_kwargs(self, artifact_dir: Path, profile: dict[str, Any]) -> dict[str, Any]:
        viewport = profile.get("viewport")
        if profile.get("mobile") and viewport is None:
            viewport = {"width": 390, "height": 844}
        kwargs: dict[str, Any] = {
            "viewport": viewport or {"width": 1280, "height": 800},
            "locale": profile.get("locale", "en-US"),
            "timezone_id": profile.get("timezone", "UTC"),
            "user_agent": profile.get("user_agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
            "color_scheme": profile.get("color_scheme"),
            "reduced_motion": profile.get("reduced_motion"),
            "is_mobile": profile.get("mobile", False),
            "has_touch": profile.get("touch", profile.get("mobile", False)),
            "device_scale_factor": profile.get("device_scale_factor", 1),
            "ignore_https_errors": profile.get("ignore_https_errors", False),
            "extra_http_headers": profile.get("headers"),
            "http_credentials": profile.get("http_credentials"),
            "permissions": profile.get("permissions"),
            "geolocation": profile.get("geolocation"),
            "accept_downloads": True,
            "record_har_path": str((artifact_dir / "session.har").resolve()) if profile.get("capture_har") else None,
            "record_video_dir": str((artifact_dir / "videos").resolve()) if profile.get("record_video") else None,
        }
        return {key: value for key, value in kwargs.items() if value is not None}

    async def open_page(self, context_id: str, url: str, wait_until: str = "load") -> dict[str, Any]:
        context = self.get_context(context_id)
        page = await context.playwright_context.new_page()
        page_state = await self.register_page(context, page)
        response = await page.goto(url, wait_until=wait_until)
        await self.capture_page_meta(page_state)
        return tool_result(
            {
                "page_id": page_state.page_id,
                "url": page.url,
                "status": response.status if response else None,
                "digest": await self.get_page_digest(page_state.page_id, mode=self.state.defaults.mode),
            }
        )

    async def navigate(self, page_id: str, url: str, wait_until: str = "load") -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        before = await self.get_lightweight_checkpoint(page_state)
        response = await page_state.playwright_page.goto(url, wait_until=wait_until)
        return await self.action_outcome(page_state, "navigate", before, extra={"status": response.status if response else None})

    async def reload_page(self, page_id: str, ignore_cache: bool = False) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        before = await self.get_lightweight_checkpoint(page_state)
        response = await page_state.playwright_page.reload(wait_until="load")
        return await self.action_outcome(
            page_state,
            "reload_page",
            before,
            extra={"status": response.status if response else None, "ignore_cache": ignore_cache},
        )

    async def go_back(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        before = await self.get_lightweight_checkpoint(page_state)
        response = await page_state.playwright_page.go_back()
        return await self.action_outcome(page_state, "go_back", before, extra={"status": response.status if response else None})

    async def go_forward(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        before = await self.get_lightweight_checkpoint(page_state)
        response = await page_state.playwright_page.go_forward()
        return await self.action_outcome(page_state, "go_forward", before, extra={"status": response.status if response else None})

    async def list_pages(self, context_id: str) -> dict[str, Any]:
        context = self.get_context(context_id)
        pages = []
        for page_id, page_state in context.pages.items():
            pages.append(
                {
                    "page_id": page_id,
                    "url": page_state.playwright_page.url,
                    "title": await page_state.playwright_page.title(),
                    "is_active": context.active_page_id == page_id,
                    "opener_url": page_state.playwright_page.opener.url if page_state.playwright_page.opener else None,
                }
            )
        return tool_result({"pages": pages})

    async def switch_page(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        context = self.get_context(page_state.context_id)
        context.active_page_id = page_id
        self.state.current_page_id = page_id
        return tool_result({"page_id": page_id, "title": await page_state.playwright_page.title(), "url": page_state.playwright_page.url})

    async def resize_viewport(self, page_id: str, width: int, height: int) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        await page_state.playwright_page.set_viewport_size({"width": width, "height": height})
        return await self.get_viewport_state(page_id)

    async def set_emulation(self, page_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        if "viewport" in settings:
            await page_state.playwright_page.set_viewport_size(settings["viewport"])
        context = self.get_context(page_state.context_id)
        context.config.update(settings)
        return tool_result({"page_id": page_id, "effective_settings": safe_json(context.config)})

    async def scroll(self, page_id: str, direction: str, amount_px: int | None = None) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        page = page_state.playwright_page
        script = """
        ({direction, amount}) => {
          const step = amount ?? window.innerHeight;
          if (direction === "top") window.scrollTo(0, 0);
          else if (direction === "bottom") window.scrollTo(0, document.body.scrollHeight);
          else if (direction === "up") window.scrollBy(0, -step);
          else window.scrollBy(0, step);
          return {
            x: window.scrollX,
            y: window.scrollY,
            width: window.innerWidth,
            height: window.innerHeight,
            scrollHeight: document.documentElement.scrollHeight
          };
        }
        """
        metrics = await page.evaluate(script, {"direction": direction, "amount": amount_px})
        return tool_result({"page_id": page_id, "scroll": metrics})

    async def close_page(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        context = self.get_context(page_state.context_id)
        await page_state.playwright_page.close()
        context.pages.pop(page_id, None)
        if context.active_page_id == page_id:
            context.active_page_id = next(iter(context.pages), None)
        if self.state.current_page_id == page_id:
            self.state.current_page_id = context.active_page_id
        return tool_result({"success": True})

    async def close_context(self, context_id: str) -> dict[str, Any]:
        context = self.get_context(context_id)
        await context.playwright_context.close()
        self.state.contexts.pop(context_id, None)
        return tool_result({"success": True})

    async def save_storage_state(self, context_id: str, path: str | None = None) -> dict[str, Any]:
        context = self.get_context(context_id)
        output = Path(path) if path else context.artifact_dir / "storage-state.json"
        await context.playwright_context.storage_state(path=str(output))
        artifact = self._add_artifact(context, "storage_state", str(output.resolve()), None, "save_storage_state")
        return tool_result({"path": str(output.resolve()), "artifact": artifact})

    async def load_storage_state(self, context_id: str, state: str | dict[str, Any]) -> dict[str, Any]:
        context = self.get_context(context_id)
        payload = json.loads(Path(state).read_text()) if isinstance(state, str) and Path(state).exists() else state
        await context.playwright_context.add_cookies(payload.get("cookies", []))
        pages = list(context.pages.values())
        if pages:
            page = pages[0].playwright_page
            for origin in payload.get("origins", []):
                await page.goto(origin["origin"])
                for item in origin.get("localStorage", []):
                    await page.evaluate(
                        "(entry) => localStorage.setItem(entry.name, entry.value)",
                        item,
                    )
        return tool_result({"success": True})

    async def set_extra_http_headers(self, page_id: str, headers: dict[str, str]) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        await page_state.playwright_page.set_extra_http_headers(headers)
        return tool_result({"success": True})

    async def set_http_credentials(self, context_id: str, username: str, password: str) -> dict[str, Any]:
        context = self.get_context(context_id)
        context.config["http_credentials"] = {"username": username, "password": password}
        return tool_result({"success": True, "note": "Credentials will apply to subsequently created contexts/pages per Playwright limitations."})

    async def generate_totp(self, secret: str, algorithm: str = "SHA1", digits: int = 6, period: int = 30) -> dict[str, Any]:
        return tool_result({"code": compute_totp(secret, digits=digits, period=period, algorithm=algorithm)})

    async def configure_session(self, defaults: dict[str, Any] | None = None, proactive_events: bool | None = None) -> dict[str, Any]:
        defaults = defaults or {}
        current = self.state.defaults
        self.state.defaults = SessionDefaults(
            mode=defaults.get("mode", current.mode),
            max_list_length=defaults.get("max_list_length", current.max_list_length),
            exclude_fields=defaults.get("exclude_fields", current.exclude_fields),
            binary_mode=defaults.get("binary_mode", current.binary_mode),
            observe_default=defaults.get("observe_default", current.observe_default),
            checkpoint_auto=defaults.get("checkpoint_auto", current.checkpoint_auto),
            proactive_events=current.proactive_events if proactive_events is None else proactive_events,
        )
        return tool_result({"effective_session_config": safe_json(self.state.defaults.__dict__)})

    async def create_checkpoint(self, page_id: str, name: str, kinds: list[str] | None = None) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        context = self.get_context(page_state.context_id)
        checkpoint = await self.capture_state(page_state, kinds=kinds or ["dom", "aom", "network", "console"])
        handle_id = new_id("checkpoint")
        context.handles.set(handle_id, checkpoint, permanent=True)
        context.checkpoints[name] = handle_id
        return tool_result({"checkpoint_id": handle_id, "name": name})

    async def release_checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        for context in self.state.contexts.values():
            removed = context.handles.pop(checkpoint_id)
            if removed is not None:
                for name, handle_id in list(context.checkpoints.items()):
                    if handle_id == checkpoint_id:
                        context.checkpoints.pop(name, None)
                return tool_result({"success": True})
        raise SemanticError("checkpoint_not_found", f"Unknown checkpoint_id '{checkpoint_id}'.", target={"checkpoint_id": checkpoint_id})

    async def diff_since_checkpoint(
        self, page_id: str, checkpoint_name: str, kinds: list[str] | None = None, mode: str = "compact"
    ) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        context = self.get_context(page_state.context_id)
        handle_id = context.checkpoints.get(checkpoint_name)
        if handle_id is None:
            raise SemanticError(
                "checkpoint_not_found",
                f"Unknown checkpoint_name '{checkpoint_name}'.",
                target={"checkpoint_name": checkpoint_name},
                next_steps=["create_checkpoint"],
            )
        previous = context.handles.get(handle_id)
        if previous is None:
            raise SemanticError(
                "handle_expired",
                f"Checkpoint '{checkpoint_name}' expired.",
                target={"checkpoint_name": checkpoint_name},
                retryable=True,
                next_steps=["create_checkpoint"],
            )
        current = await self.capture_state(page_state, kinds=kinds or previous.get("kinds", []))
        return tool_result({"checkpoint_name": checkpoint_name, "diff": self.diff_states(previous, current, mode=mode)})

    async def get_cache_stats(self, context_id: str) -> dict[str, Any]:
        context = self.get_context(context_id)
        return tool_result(
            {
                "handles": context.handles.stats(),
                "named_checkpoints": list(context.checkpoints.keys()),
                "pages": len(context.pages),
                "artifacts": len(context.artifacts),
            }
        )

    async def capture_page_meta(self, page_state: PageState) -> dict[str, Any]:
        page = page_state.playwright_page
        meta = {
            "url": page.url,
            "title": await page.title(),
            "is_closed": page.is_closed(),
            "viewport": page.viewport_size,
        }
        page_state.last_meta = meta
        return meta

    async def get_page_meta(self, page_id: str) -> dict[str, Any]:
        return tool_result(await self.capture_page_meta(self.get_page_state(page_id)))

    async def get_viewport_state(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        viewport = await page_state.playwright_page.evaluate(
            """
            () => ({
              width: window.innerWidth,
              height: window.innerHeight,
              outerWidth: window.outerWidth,
              outerHeight: window.outerHeight,
              scrollX: window.scrollX,
              scrollY: window.scrollY,
              devicePixelRatio: window.devicePixelRatio,
              orientation: screen.orientation ? screen.orientation.type : null,
              touch: navigator.maxTouchPoints > 0
            })
            """
        )
        return tool_result(viewport)

    async def get_page_outline(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        outline = await page_state.playwright_page.evaluate(
            """
            () => {
              const pick = (selector, limit = 20) =>
                Array.from(document.querySelectorAll(selector)).slice(0, limit).map((node) => ({
                  text: (node.innerText || node.textContent || "").trim().slice(0, 120),
                  role: node.getAttribute("role"),
                  ariaLabel: node.getAttribute("aria-label"),
                  tag: node.tagName.toLowerCase()
                }));
              return {
                headings: pick("h1,h2,h3"),
                forms: pick("form"),
                buttons: pick("button,[role='button']"),
                links: pick("a[href]"),
                dialogs: pick("dialog,[role='dialog'],[aria-modal='true']"),
                iframes: pick("iframe")
              };
            }
            """
        )
        return tool_result(outline)

    async def get_page_digest(self, page_id: str, mode: str = "compact") -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        meta = await self.capture_page_meta(page_state)
        outline = await self.get_page_outline(page_id)
        blockers = await page_state.playwright_page.evaluate(
            """
            () => Array.from(document.querySelectorAll("dialog,[role='dialog'],[aria-modal='true'],[data-testid*='modal']"))
              .slice(0, 5)
              .map((node) => ({
                text: (node.innerText || node.textContent || "").trim().slice(0, 120),
                tag: node.tagName.toLowerCase()
              }))
            """
        )
        digest = {
            "url": meta["url"],
            "title": meta["title"],
            "viewport": meta["viewport"],
            "blockers": blockers,
            "headings": outline["headings"][:5],
            "buttons": outline["buttons"][:5],
            "recent_console_errors": [item for item in page_state.buffers.console if item["type"] == "error"][-3:],
            "recent_page_errors": page_state.buffers.errors[-3:],
        }
        if mode != "compact":
            digest["links"] = outline["links"][:10]
            digest["forms"] = outline["forms"][:10]
        if self.state.defaults.proactive_events:
            digest["_events_since_last_call"] = self.consume_events(page_state)
        return tool_result(digest)

    def consume_events(self, page_state: PageState) -> dict[str, Any]:
        start = page_state.last_tool_event_index
        merged = page_state.buffers.page_events + page_state.buffers.dialogs + page_state.buffers.downloads
        events = merged[start:]
        page_state.last_tool_event_index = len(merged)
        return {"events": events[: self.state.defaults.max_list_length], "remaining_count": max(0, len(events) - self.state.defaults.max_list_length)}

    async def find_elements(self, page_id: str, query: dict[str, Any]) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        locator = self._locator_from_target(page_state, query)
        count = await locator.count()
        descriptors = []
        limit = min(count, query.get("limit", self.state.defaults.max_list_length))
        for index in range(limit):
            item = locator.nth(index)
            descriptors.append(await self.describe_locator(page_state, item, query=query, nth=index))
        return tool_result({"matches": descriptors, "remaining_count": max(0, count - limit)})

    async def find_interactive_candidates(self, page_id: str, intent: str, filters: dict[str, Any] | None = None, limit: int = 10) -> dict[str, Any]:
        filters = filters or {}
        query = self.intent_to_query(intent, filters)
        found = await self.find_elements(page_id, {**query, "limit": limit})
        return tool_result({"intent": intent, "candidates": found["matches"]})

    def intent_to_query(self, intent: str, filters: dict[str, Any]) -> dict[str, Any]:
        lowered = intent.lower()
        if "login" in lowered:
            return {"role": "button", "text": filters.get("text", "log in")}
        if "submit" in lowered:
            return {"role": "button", "text": filters.get("text", "submit")}
        if "search" in lowered:
            return {"role": "textbox", "label": filters.get("label", "search")}
        if "close" in lowered:
            return {"role": "button", "text": filters.get("text", "close")}
        return filters or {"role": "button"}

    async def describe_locator(self, page_state: PageState, locator: Locator, query: dict[str, Any] | None = None, nth: int = 0) -> dict[str, Any]:
        handle = await locator.element_handle()
        if handle is None:
            raise SemanticError("element_not_found", "Target element no longer exists.", target={"page_id": page_state.page_id})
        selector = await self._selector_for_handle(handle)
        element_id = new_id("el")
        record = ElementRecord(
            element_id=element_id,
            page_id=page_state.page_id,
            frame_id=page_state.frame_focus,
            selector=selector,
            hints=query or {},
            created_at=utc_ts(),
        )
        page_state.element_cache.set(element_id, record)
        info = await locator.evaluate(
            """
            (node) => ({
              tag: node.tagName.toLowerCase(),
              text: (node.innerText || node.textContent || "").trim().slice(0, 200),
              role: node.getAttribute("role"),
              label: node.getAttribute("aria-label"),
              placeholder: node.getAttribute("placeholder"),
              name: node.getAttribute("name"),
              value: node.value ?? null,
              visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length)
            })
            """
        )
        info.update({"element_id": element_id, "selector": selector, "nth": nth})
        return info

    async def _selector_for_handle(self, handle: ElementHandle) -> str:
        return await handle.evaluate(
            """
            (node) => {
              const path = [];
              let current = node;
              while (current && current.nodeType === Node.ELEMENT_NODE && path.length < 6) {
                let selector = current.tagName.toLowerCase();
                if (current.id) {
                  selector += "#" + CSS.escape(current.id);
                  path.unshift(selector);
                  break;
                }
                const classes = Array.from(current.classList || []).slice(0, 2).map((value) => "." + CSS.escape(value));
                selector += classes.join("");
                const parent = current.parentElement;
                if (parent) {
                  const siblings = Array.from(parent.children).filter((child) => child.tagName === current.tagName);
                  if (siblings.length > 1) {
                    selector += `:nth-of-type(${siblings.indexOf(current) + 1})`;
                  }
                }
                path.unshift(selector);
                current = current.parentElement;
              }
              return path.join(" > ");
            }
            """
        )

    async def get_element_record(self, element_id: str) -> tuple[PageState, ElementRecord]:
        for context in self.state.contexts.values():
            for page_state in context.pages.values():
                record = page_state.element_cache.get(element_id)
                if record is not None:
                    return page_state, record
        raise SemanticError(
            "handle_expired",
            f"Element handle '{element_id}' expired.",
            target={"element_id": element_id},
            retryable=True,
            next_steps=["find_elements"],
        )

    async def resolve_locator(
        self,
        page_id: str | None = None,
        element_id: str | None = None,
        target: dict[str, Any] | None = None,
        *,
        source_name: str = "target",
    ) -> tuple[PageState, Locator]:
        if element_id:
            page_state, record = await self.get_element_record(element_id)
            return page_state, self._locator_from_record(page_state, record)
        if page_id is None:
            raise SemanticError("missing_target", f"{source_name} requires page_id when element_id is absent.")
        page_state = self.get_page_state(page_id)
        if not target:
            raise SemanticError("missing_target", f"No {source_name} provided.", target={"page_id": page_id})
        locator = self._locator_from_target(page_state, target)
        count = await locator.count()
        if count == 0:
            raise SemanticError(
                "element_not_found",
                f"No element matched {source_name}.",
                target={"page_id": page_id, source_name: target},
                retryable=True,
                likely_causes=["selector mismatch", "element not rendered yet"],
                next_steps=["get_page_digest", "find_elements", "wait_for"],
            )
        return page_state, locator.first

    def _locator_from_record(self, page_state: PageState, record: ElementRecord) -> Locator:
        if record.selector:
            return page_state.playwright_page.locator(record.selector)
        return self._locator_from_target(page_state, record.hints)

    def _locator_from_target(self, page_state: PageState, target: dict[str, Any]) -> Locator:
        page = page_state.playwright_page
        if target.get("selector"):
            return page.locator(target["selector"])
        if target.get("role"):
            return page.get_by_role(target["role"], name=target.get("text") or target.get("label"))
        if target.get("label"):
            return page.get_by_label(target["label"])
        if target.get("placeholder"):
            return page.get_by_placeholder(target["placeholder"])
        if target.get("text"):
            return page.get_by_text(target["text"], exact=target.get("exact", False))
        if target.get("xpath"):
            return page.locator(f"xpath={target['xpath']}")
        if target.get("css"):
            return page.locator(target["css"])
        raise SemanticError("unsupported_query", "Target query requires one of selector, role, label, placeholder, text, xpath, or css.")

    async def get_element_state(self, element_id: str, attribute: str | None = None) -> dict[str, Any]:
        page_state, record = await self.get_element_record(element_id)
        locator = self._locator_from_record(page_state, record)
        payload = {
            "visible": await locator.is_visible(),
            "enabled": await locator.is_enabled(),
            "text": summarize_text(await locator.inner_text(), 400),
            "value": await locator.input_value() if await locator.evaluate("(node) => 'value' in node") else None,
        }
        if attribute:
            payload["attribute"] = {attribute: await locator.get_attribute(attribute)}
        return tool_result(payload)

    async def get_element_box(self, element_id: str) -> dict[str, Any]:
        page_state, record = await self.get_element_record(element_id)
        locator = self._locator_from_record(page_state, record)
        box = await locator.bounding_box()
        return tool_result({"box": box, "selector": record.selector})

    async def get_computed_style(self, element_id: str, properties: list[str] | None = None) -> dict[str, Any]:
        page_state, record = await self.get_element_record(element_id)
        locator = self._locator_from_record(page_state, record)
        props = properties or ["display", "visibility", "position", "z-index", "opacity"]
        values = await locator.evaluate(
            """
            (node, props) => {
              const style = getComputedStyle(node);
              return Object.fromEntries(props.map((prop) => [prop, style.getPropertyValue(prop)]));
            }
            """,
            props,
        )
        return tool_result(values)

    async def get_aom_snapshot(self, page_id: str, include_hidden: bool = False) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        snapshot = await page_state.playwright_page.accessibility.snapshot(interesting_only=not include_hidden)
        return tool_result({"aom": safe_json(snapshot)})

    async def get_dom_snapshot(self, page_id: str, scope: str = "interactive") -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        script = """
        (scope) => {
          const selector = scope === "full"
            ? "body *"
            : scope === "minimal"
              ? "main,h1,h2,h3,button,a,input,select,textarea,[role]"
              : "button,a,input,select,textarea,[role],form,dialog";
          return Array.from(document.querySelectorAll(selector)).slice(0, 300).map((node, index) => ({
            index,
            tag: node.tagName.toLowerCase(),
            text: (node.innerText || node.textContent || "").trim().slice(0, 160),
            id: node.id || null,
            role: node.getAttribute("role"),
            classes: Array.from(node.classList || []).slice(0, 4),
          }));
        }
        """
        nodes = await page_state.playwright_page.evaluate(script, scope)
        return tool_result({"scope": scope, "nodes": nodes, "remaining_count": max(0, len(nodes) - 300)})

    async def get_dom_diff(self, page_id: str, previous_state_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        context = self.get_context(page_state.context_id)
        previous = context.handles.get(previous_state_id)
        if previous is None:
            raise SemanticError("handle_expired", f"Unknown or expired state handle '{previous_state_id}'.", target={"handle_id": previous_state_id})
        current = await self.capture_state(page_state, kinds=["dom"])
        return tool_result({"diff": self.diff_states(previous, current, mode="standard")})

    async def get_state_handle(self, page_id: str, kinds: list[str] | None = None) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        context = self.get_context(page_state.context_id)
        captured = await self.capture_state(page_state, kinds=kinds or ["dom", "aom", "visual", "network", "console"])
        handle_id = new_id("handle")
        context.handles.set(handle_id, captured)
        return tool_result({"handle_id": handle_id, "kinds": captured["kinds"]})

    async def hydrate_state_slice(self, handle_id: str, slice: dict[str, Any]) -> dict[str, Any]:
        for context in self.state.contexts.values():
            payload = context.handles.get(handle_id)
            if payload is None:
                continue
            key = slice.get("kind")
            data = payload.get(key)
            if isinstance(data, list):
                start = slice.get("start", 0)
                limit = slice.get("limit", self.state.defaults.max_list_length)
                return tool_result({"kind": key, "items": data[start : start + limit]})
            if isinstance(data, dict):
                fields = slice.get("fields")
                if fields:
                    return tool_result({"kind": key, "data": {field: data.get(field) for field in fields}})
                return tool_result({"kind": key, "data": data})
            return tool_result({"kind": key, "data": data})
        raise SemanticError("handle_expired", f"Unknown or expired handle '{handle_id}'.", target={"handle_id": handle_id})

    async def list_frames(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        frames = []
        page_state.frame_map = {}
        for index, frame in enumerate(page_state.playwright_page.frames):
            frame_id = f"{page_id}-frame-{index}"
            page_state.frame_map[frame_id] = frame
            frames.append(
                {
                    "frame_id": frame_id,
                    "name": frame.name,
                    "url": frame.url,
                    "is_main_frame": frame == page_state.playwright_page.main_frame,
                }
            )
        page_state.frame_focus = frames[0]["frame_id"] if frames else None
        return tool_result({"frames": frames})

    async def switch_frame(self, frame_id: str) -> dict[str, Any]:
        for context in self.state.contexts.values():
            for page_state in context.pages.values():
                await self.list_frames(page_state.page_id)
                frame = page_state.frame_map.get(frame_id)
                if frame is not None:
                    page_state.frame_focus = frame_id
                    return tool_result({"frame_id": frame_id, "name": frame.name, "url": frame.url})
        raise SemanticError("frame_not_found", f"Unknown frame_id '{frame_id}'.", target={"frame_id": frame_id})

    async def query_shadow_dom(self, host_element_id: str, selector: str) -> dict[str, Any]:
        page_state, record = await self.get_element_record(host_element_id)
        locator = self._locator_from_record(page_state, record)
        matches = await locator.evaluate(
            """
            (node, selector) => {
              const root = node.shadowRoot;
              if (!root) return [];
              return Array.from(root.querySelectorAll(selector)).slice(0, 25).map((item) => ({
                tag: item.tagName.toLowerCase(),
                text: (item.innerText || item.textContent || "").trim().slice(0, 160)
              }));
            }
            """,
            selector,
        )
        return tool_result({"matches": matches})

    async def get_shadow_root(self, element_id: str) -> dict[str, Any]:
        page_state, record = await self.get_element_record(element_id)
        locator = self._locator_from_record(page_state, record)
        tree = await locator.evaluate(
            """
            (node) => {
              const root = node.shadowRoot;
              if (!root) return null;
              return Array.from(root.children).map((child) => ({
                tag: child.tagName.toLowerCase(),
                text: (child.innerText || child.textContent || "").trim().slice(0, 160)
              }));
            }
            """
        )
        return tool_result({"shadow_root": tree})

    async def extract_text(self, page_id: str, scope: str = "visible", element_id: str | None = None) -> dict[str, Any]:
        if element_id:
            page_state, record = await self.get_element_record(element_id)
            locator = self._locator_from_record(page_state, record)
            return tool_result({"text": await locator.inner_text()})
        page_state = self.get_page_state(page_id)
        script = "() => document.body.innerText"
        if scope == "full":
            script = "() => document.documentElement.textContent"
        text = await page_state.playwright_page.evaluate(script)
        return tool_result({"text": text[:MAX_INLINE_TEXT], "truncated": len(text) > MAX_INLINE_TEXT})

    async def extract_table_data(self, element_id: str) -> dict[str, Any]:
        page_state, record = await self.get_element_record(element_id)
        locator = self._locator_from_record(page_state, record)
        rows = await locator.evaluate(
            """
            (table) => Array.from(table.querySelectorAll("tr")).map((row) =>
              Array.from(row.querySelectorAll("th,td")).map((cell) => (cell.innerText || cell.textContent || "").trim())
            )
            """
        )
        return tool_result({"rows": rows})

    async def get_selection(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        selection = await page_state.playwright_page.evaluate(
            """
            () => {
              const sel = window.getSelection();
              return sel ? {text: sel.toString(), rangeCount: sel.rangeCount} : {text: "", rangeCount: 0};
            }
            """
        )
        return tool_result(selection)

    async def take_screenshot(
        self, target: str, page_id: str | None = None, element_id: str | None = None, return_method: str = "disk"
    ) -> dict[str, Any]:
        if element_id:
            page_state, record = await self.get_element_record(element_id)
            locator = self._locator_from_record(page_state, record)
            context = self.get_context(page_state.context_id)
            path = context.artifact_dir / "screenshots" / f"{new_id('shot')}.png"
            ensure_dir(path.parent)
            image = await locator.screenshot(path=str(path))
            artifact = self._add_artifact(context, "screenshot", str(path.resolve()), page_state.page_id, "take_screenshot")
        else:
            if page_id is None:
                raise SemanticError("missing_target", "take_screenshot requires page_id or element_id.")
            page_state = self.get_page_state(page_id)
            context = self.get_context(page_state.context_id)
            path = context.artifact_dir / "screenshots" / f"{new_id('shot')}.png"
            ensure_dir(path.parent)
            image = await page_state.playwright_page.screenshot(path=str(path), full_page=target == "full_page")
            artifact = self._add_artifact(context, "screenshot", str(path.resolve()), page_state.page_id, "take_screenshot")
        if return_method == "base64":
            import base64

            return tool_result({"base64": base64.b64encode(image).decode("ascii"), "artifact": artifact})
        return tool_result({"path": artifact["path"], "artifact": artifact})

    async def get_annotated_screenshot(self, page_id: str, viewport_only: bool = True) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        context = self.get_context(page_state.context_id)
        await page_state.playwright_page.evaluate(
            """
            () => {
              const markers = document.querySelectorAll("[data-browser-puppet-marker]");
              markers.forEach((node) => node.remove());
              const targets = Array.from(document.querySelectorAll("button,a,input,select,textarea,[role='button']")).slice(0, 20);
              targets.forEach((node, index) => {
                const rect = node.getBoundingClientRect();
                const label = document.createElement("div");
                label.dataset.browserPuppetMarker = "1";
                label.textContent = String(index + 1);
                Object.assign(label.style, {
                  position: "fixed",
                  left: `${rect.left}px`,
                  top: `${rect.top}px`,
                  zIndex: "2147483647",
                  background: "#d61f00",
                  color: "#fff",
                  fontSize: "12px",
                  fontFamily: "monospace",
                  padding: "2px 4px",
                  borderRadius: "4px"
                });
                document.body.appendChild(label);
              });
            }
            """
        )
        path = context.artifact_dir / "screenshots" / f"{new_id('annotated')}.png"
        ensure_dir(path.parent)
        await page_state.playwright_page.screenshot(path=str(path), full_page=not viewport_only)
        await page_state.playwright_page.evaluate(
            """() => document.querySelectorAll("[data-browser-puppet-marker]").forEach((node) => node.remove())"""
        )
        artifact = self._add_artifact(context, "annotated_screenshot", str(path.resolve()), page_id, "get_annotated_screenshot")
        return tool_result({"path": artifact["path"], "artifact": artifact})

    async def get_visual_digest(self, page_id: str, mode: str = "compact") -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        digest = await page_state.playwright_page.evaluate(
            """
            () => {
              const sticky = Array.from(document.querySelectorAll("*")).filter((node) => {
                const style = getComputedStyle(node);
                return style.position === "fixed" || style.position === "sticky";
              }).slice(0, 10).map((node) => ({
                tag: node.tagName.toLowerCase(),
                text: (node.innerText || node.textContent || "").trim().slice(0, 100)
              }));
              const modals = Array.from(document.querySelectorAll("dialog,[role='dialog'],[aria-modal='true']")).map((node) => ({
                text: (node.innerText || node.textContent || "").trim().slice(0, 100)
              }));
              return {sticky, modals};
            }
            """
        )
        if mode != "compact":
            digest["screenshot"] = await self.take_screenshot("viewport", page_id=page_id, return_method="disk")
        return tool_result(digest)

    async def start_trace(self, context_id: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        context = self.get_context(context_id)
        await context.playwright_context.tracing.start(
            screenshots=(options or {}).get("screenshots", True),
            snapshots=(options or {}).get("snapshots", True),
            sources=(options or {}).get("sources", True),
        )
        return tool_result({"trace_session_id": new_id("trace")})

    async def stop_trace(self, context_id: str) -> dict[str, Any]:
        context = self.get_context(context_id)
        path = context.artifact_dir / "trace.zip"
        await context.playwright_context.tracing.stop(path=str(path))
        artifact = self._add_artifact(context, "trace", str(path.resolve()), context.active_page_id, "stop_trace")
        return tool_result({"path": artifact["path"], "artifact": artifact})

    async def compare_viewports(self, page_id: str, profiles: list[dict[str, Any]], mode: str = "compact") -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        original = page_state.playwright_page.viewport_size or {"width": 1280, "height": 800}
        comparisons = []
        for profile in profiles:
            await page_state.playwright_page.set_viewport_size(profile["viewport"])
            digest = await self.get_page_digest(page_id, mode=mode)
            comparisons.append({"profile": profile.get("name", str(profile["viewport"])), "digest": digest})
        await page_state.playwright_page.set_viewport_size(original)
        return tool_result({"comparisons": comparisons})

    async def run_accessibility_audit(self, page_id: str, scope: str = "page") -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        issues = await page_state.playwright_page.evaluate(
            """
            () => {
              const violations = [];
              document.querySelectorAll("img").forEach((node) => {
                if (!node.getAttribute("alt")) {
                  violations.push({severity: "medium", rule: "image-alt", text: (node.src || "").slice(0, 120)});
                }
              });
              document.querySelectorAll("input,select,textarea").forEach((node) => {
                const id = node.id;
                const hasLabel = !!node.getAttribute("aria-label") || !!document.querySelector(`label[for="${id}"]`);
                if (!hasLabel) {
                  violations.push({severity: "medium", rule: "form-label", text: node.outerHTML.slice(0, 120)});
                }
              });
              return violations.slice(0, 50);
            }
            """
        )
        return tool_result({"scope": scope, "violations": issues})

    async def get_issue_digest(self, page_id: str, sources: list[str], limit: int = 10) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        issues = []
        if "console" in sources:
            issues.extend({"source": "console", **item} for item in page_state.buffers.console if item["type"] == "error")
        if "network" in sources:
            issues.extend(
                {"source": "network", **entry}
                for entry in page_state.buffers.network
                if entry.get("response", {}).get("status", 200) >= 400
            )
        if "accessibility" in sources:
            issues.extend((await self.run_accessibility_audit(page_id))["violations"])
        if "visual" in sources:
            visual = await self.get_visual_digest(page_id)
            issues.extend({"source": "visual", "severity": "info", "detail": item} for item in visual.get("modals", []))
        return tool_result({"issues": issues[:limit], "remaining_count": max(0, len(issues) - limit)})

    async def get_focus_order(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        focusables = await page_state.playwright_page.evaluate(
            """
            () => Array.from(document.querySelectorAll("a,button,input,select,textarea,[tabindex]")).slice(0, 100).map((node, index) => ({
              index,
              tag: node.tagName.toLowerCase(),
              text: (node.innerText || node.textContent || "").trim().slice(0, 100),
              tabIndex: node.tabIndex
            }))
            """
        )
        return tool_result({"focus_order": focusables})

    async def get_live_regions(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        regions = await page_state.playwright_page.evaluate(
            """
            () => Array.from(document.querySelectorAll("[aria-live]")).slice(0, 20).map((node) => ({
              mode: node.getAttribute("aria-live"),
              text: (node.innerText || node.textContent || "").trim().slice(0, 160)
            }))
            """
        )
        return tool_result({"regions": regions})

    async def click(
        self,
        page_id: str | None = None,
        element_id: str | None = None,
        target: dict[str, Any] | None = None,
        button: str = "left",
        click_count: int = 1,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        observe: str = "auto",
    ) -> dict[str, Any]:
        page_state, locator = await self.resolve_locator(page_id=page_id, element_id=element_id, target=target)
        before = await self.before_mutation(page_state, observe)
        await locator.click(button=button, click_count=click_count, timeout=timeout_ms)
        return await self.action_outcome(page_state, "click", before)

    async def tap(self, page_id: str | None = None, element_id: str | None = None, target: dict[str, Any] | None = None, observe: str = "auto") -> dict[str, Any]:
        page_state, locator = await self.resolve_locator(page_id=page_id, element_id=element_id, target=target)
        before = await self.before_mutation(page_state, observe)
        await locator.tap()
        return await self.action_outcome(page_state, "tap", before)

    async def type_text(
        self,
        text: str,
        page_id: str | None = None,
        element_id: str | None = None,
        target: dict[str, Any] | None = None,
        clear_first: bool = True,
        observe: str = "auto",
    ) -> dict[str, Any]:
        page_state, locator = await self.resolve_locator(page_id=page_id, element_id=element_id, target=target)
        before = await self.before_mutation(page_state, observe)
        if clear_first:
            await locator.fill("")
        await locator.fill(text)
        return await self.action_outcome(page_state, "type_text", before)

    async def press_key(self, page_id: str, key: str, observe: str = "auto") -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        before = await self.before_mutation(page_state, observe)
        await page_state.playwright_page.keyboard.press(key)
        return await self.action_outcome(page_state, "press_key", before, extra={"key": key})

    async def press_key_chord(self, page_id: str, keys: list[str], observe: str = "auto") -> dict[str, Any]:
        return await self.press_key(page_id, "+".join(keys), observe=observe)

    async def hover(self, page_id: str | None = None, element_id: str | None = None, target: dict[str, Any] | None = None) -> dict[str, Any]:
        page_state, locator = await self.resolve_locator(page_id=page_id, element_id=element_id, target=target)
        await locator.hover()
        return tool_result({"success": True, "page_id": page_state.page_id})

    async def drag_and_drop(
        self,
        page_id: str,
        source_element_id: str | None = None,
        source_target: dict[str, Any] | None = None,
        target_element_id: str | None = None,
        dest_target: dict[str, Any] | None = None,
        observe: str = "auto",
    ) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        _, source = await self.resolve_locator(page_id=page_id, element_id=source_element_id, target=source_target, source_name="source_target")
        _, dest = await self.resolve_locator(page_id=page_id, element_id=target_element_id, target=dest_target, source_name="dest_target")
        before = await self.before_mutation(page_state, observe)
        await source.drag_to(dest)
        return await self.action_outcome(page_state, "drag_and_drop", before)

    async def select_dropdown(
        self,
        value: str,
        page_id: str | None = None,
        element_id: str | None = None,
        target: dict[str, Any] | None = None,
        observe: str = "auto",
    ) -> dict[str, Any]:
        page_state, locator = await self.resolve_locator(page_id=page_id, element_id=element_id, target=target)
        before = await self.before_mutation(page_state, observe)
        await locator.select_option(value=value)
        return await self.action_outcome(page_state, "select_dropdown", before)

    async def set_checkbox(
        self,
        checked: bool,
        page_id: str | None = None,
        element_id: str | None = None,
        target: dict[str, Any] | None = None,
        observe: str = "auto",
    ) -> dict[str, Any]:
        page_state, locator = await self.resolve_locator(page_id=page_id, element_id=element_id, target=target)
        before = await self.before_mutation(page_state, observe)
        if checked:
            await locator.check()
        else:
            await locator.uncheck()
        return await self.action_outcome(page_state, "set_checkbox", before)

    async def upload_file(
        self,
        file_path: str,
        page_id: str | None = None,
        element_id: str | None = None,
        target: dict[str, Any] | None = None,
        observe: str = "auto",
    ) -> dict[str, Any]:
        page_state, locator = await self.resolve_locator(page_id=page_id, element_id=element_id, target=target)
        before = await self.before_mutation(page_state, observe)
        await locator.set_input_files(file_path)
        return await self.action_outcome(page_state, "upload_file", before)

    async def handle_dialog(self, action: str, prompt_text: str | None = None) -> dict[str, Any]:
        if self.state.current_page_id is None:
            raise SemanticError("page_not_found", "No active page for handle_dialog.")
        page_state = self.get_page_state(self.state.current_page_id)
        dialog_holder: dict[str, Any] = {}

        async def capture(dialog: Any) -> None:
            dialog_holder["dialog"] = dialog

        page_state.playwright_page.once("dialog", lambda dialog: asyncio.create_task(capture(dialog)))
        await asyncio.sleep(0.1)
        dialog = dialog_holder.get("dialog")
        if dialog is None:
            recent = page_state.buffers.dialogs[-1] if page_state.buffers.dialogs else None
            return tool_result({"result": "no_pending_dialog", "recent": recent})
        if action == "accept":
            await dialog.accept(prompt_text)
        else:
            await dialog.dismiss()
        return tool_result({"result": action, "message": dialog.message})

    async def swipe(self, page_id: str, start: dict[str, int], end: dict[str, int], duration_ms: int = 300, observe: str = "auto") -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        before = await self.before_mutation(page_state, observe)
        await page_state.playwright_page.mouse.move(start["x"], start["y"])
        await page_state.playwright_page.mouse.down()
        await page_state.playwright_page.mouse.move(end["x"], end["y"], steps=max(2, duration_ms // 16))
        await page_state.playwright_page.mouse.up()
        return await self.action_outcome(page_state, "swipe", before)

    async def long_press(
        self,
        page_id: str | None = None,
        element_id: str | None = None,
        target: dict[str, Any] | None = None,
        duration_ms: int = 800,
        observe: str = "auto",
    ) -> dict[str, Any]:
        page_state, locator = await self.resolve_locator(page_id=page_id, element_id=element_id, target=target)
        before = await self.before_mutation(page_state, observe)
        await locator.hover()
        await page_state.playwright_page.mouse.down()
        await asyncio.sleep(duration_ms / 1000)
        await page_state.playwright_page.mouse.up()
        return await self.action_outcome(page_state, "long_press", before)

    async def mouse_move(self, page_id: str, x: int, y: int) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        await page_state.playwright_page.mouse.move(x, y)
        return tool_result({"success": True})

    async def mouse_click_at(self, page_id: str, x: int, y: int, button: str = "left", observe: str = "auto") -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        before = await self.before_mutation(page_state, observe)
        await page_state.playwright_page.mouse.click(x, y, button=button)
        return await self.action_outcome(page_state, "mouse_click_at", before)

    async def mouse_wheel(self, page_id: str, delta_x: int, delta_y: int) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        await page_state.playwright_page.mouse.wheel(delta_x, delta_y)
        return tool_result({"success": True})

    async def scroll_element(
        self, direction: str, amount_px: int = 200, page_id: str | None = None, element_id: str | None = None, target: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        page_state, locator = await self.resolve_locator(page_id=page_id, element_id=element_id, target=target)
        delta_x = amount_px if direction == "right" else -amount_px if direction == "left" else 0
        delta_y = amount_px if direction == "down" else -amount_px if direction == "up" else 0
        metrics = await locator.evaluate(
            """
            (node, args) => {
              const {deltaX, deltaY} = args;
              node.scrollBy(deltaX, deltaY);
              return {scrollLeft: node.scrollLeft, scrollTop: node.scrollTop};
            }
            """,
            {"deltaX": delta_x, "deltaY": delta_y},
        )
        return tool_result(metrics)

    async def clipboard_read(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        value = await page_state.playwright_page.evaluate("() => navigator.clipboard.readText()")
        return tool_result({"text": value})

    async def clipboard_write(self, page_id: str, text: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        await page_state.playwright_page.evaluate("(value) => navigator.clipboard.writeText(value)", text)
        return tool_result({"success": True})

    async def fill_contenteditable(self, html: str, page_id: str | None = None, element_id: str | None = None, target: dict[str, Any] | None = None) -> dict[str, Any]:
        page_state, locator = await self.resolve_locator(page_id=page_id, element_id=element_id, target=target)
        await locator.evaluate("(node, value) => { node.innerHTML = value; node.dispatchEvent(new Event('input', {bubbles: true})); }", html)
        return tool_result({"success": True})

    async def select_date(self, value: str, page_id: str | None = None, element_id: str | None = None, target: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.set_input_value(value, page_id=page_id, element_id=element_id, target=target)

    async def set_input_value(
        self, value: str, page_id: str | None = None, element_id: str | None = None, target: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        page_state, locator = await self.resolve_locator(page_id=page_id, element_id=element_id, target=target)
        await locator.evaluate(
            """
            (node, value) => {
              node.value = value;
              node.dispatchEvent(new Event('input', {bubbles: true}));
              node.dispatchEvent(new Event('change', {bubbles: true}));
            }
            """,
            value,
        )
        return tool_result({"success": True})

    async def fill_form(
        self,
        page_id: str,
        fields: list[dict[str, Any]],
        form_target: dict[str, Any] | None = None,
        submit: bool = False,
        observe: str = "auto",
    ) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        before = await self.before_mutation(page_state, observe)
        results = []
        for field in fields:
            field_target = field["target"]
            action = field.get("action", "fill")
            _, locator = await self.resolve_locator(page_id=page_id, target=field_target)
            if action == "select":
                await locator.select_option(value=field["value"])
            elif action == "check":
                if field["value"]:
                    await locator.check()
                else:
                    await locator.uncheck()
            else:
                await locator.fill(str(field["value"]))
            results.append({"target": field_target, "success": True})
        if submit:
            if form_target:
                _, submit_locator = await self.resolve_locator(page_id=page_id, target=form_target)
                await submit_locator.press("Enter")
            else:
                await page_state.playwright_page.keyboard.press("Enter")
        outcome = await self.action_outcome(page_state, "fill_form", before)
        outcome["field_results"] = results
        return outcome

    async def wait_for(self, target: dict[str, Any], state: str, page_id: str | None = None, observe: str = "auto") -> dict[str, Any]:
        if state in {"url", "networkidle"}:
            if page_id is None:
                raise SemanticError("missing_target", "wait_for state requires page_id.")
            page_state = self.get_page_state(page_id)
            before = await self.before_mutation(page_state, observe)
            if state == "url":
                await page_state.playwright_page.wait_for_url(target["pattern"], timeout=target.get("timeout_ms", DEFAULT_TIMEOUT_MS))
            else:
                await page_state.playwright_page.wait_for_load_state("networkidle", timeout=target.get("timeout_ms", DEFAULT_TIMEOUT_MS))
            return await self.action_outcome(page_state, "wait_for", before)
        page_state, locator = await self.resolve_locator(page_id=page_id, target=target)
        before = await self.before_mutation(page_state, observe)
        await locator.wait_for(state=state, timeout=target.get("timeout_ms", DEFAULT_TIMEOUT_MS))
        return await self.action_outcome(page_state, "wait_for", before)

    async def run_action_and_describe(self, action: dict[str, Any], expect: dict[str, Any] | None = None, mode: str = "compact") -> dict[str, Any]:
        page_id = action.get("page_id") or self.state.current_page_id
        result = await self.dispatch_step(page_id=page_id, step=action)
        if expect:
            result["expectation"] = expect
        if mode == "compact":
            return tool_result({"result": result.get("success", True), "digest": result.get("digest"), "events": result.get("_events_since_last_call")})
        return result

    async def get_network_traffic(
        self, page_id: str, filters: dict[str, Any] | None = None, since: str | None = None, cursor: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        limit = limit or self.state.defaults.max_list_length
        items = self.apply_cursor(page_state.buffers.network, cursor=cursor, limit=limit)
        if filters and filters.get("status_min"):
            items["items"] = [item for item in items["items"] if item.get("response", {}).get("status", 0) >= filters["status_min"]]
        return tool_result(items)

    async def get_request_detail(self, request_id: str) -> dict[str, Any]:
        for context in self.state.contexts.values():
            for page_state in context.pages.values():
                record = page_state.request_map.get(request_id)
                if record is not None:
                    return tool_result(record)
        raise SemanticError("request_not_found", f"Unknown request_id '{request_id}'.", target={"request_id": request_id})

    async def get_response_body(self, request_id: str, encoding: str = "text") -> dict[str, Any]:
        for context in self.state.contexts.values():
            for page_state in context.pages.values():
                body = page_state.response_bodies.get(request_id)
                if body is None:
                    continue
                if encoding == "base64":
                    import base64

                    return tool_result({"body": base64.b64encode(body).decode("ascii"), "encoding": "base64"})
                return tool_result({"body": body.decode("utf-8", errors="replace")[:MAX_INLINE_TEXT], "encoding": "text"})
        raise SemanticError("response_body_not_found", f"No captured response body for request_id '{request_id}'.", target={"request_id": request_id})

    async def get_network_digest(self, page_id: str, window: dict[str, Any] | None = None, mode: str = "compact") -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        entries = page_state.buffers.network[-100:]
        failures = [item for item in entries if item.get("response", {}).get("status", 200) >= 400]
        redirects = [item for item in entries if 300 <= item.get("response", {}).get("status", 0) < 400]
        digest = {"requests": len(entries), "failures": failures[:5], "redirects": redirects[:5]}
        if mode != "compact":
            digest["last_requests"] = entries[-10:]
        return tool_result(digest)

    async def set_headers(self, context_id: str, headers: dict[str, str]) -> dict[str, Any]:
        context = self.get_context(context_id)
        await context.playwright_context.set_extra_http_headers(headers)
        context.config["headers"] = headers
        return tool_result({"success": True})

    async def get_cookies(self, context_id: str, urls: list[str] | None = None) -> dict[str, Any]:
        context = self.get_context(context_id)
        cookies = await context.playwright_context.cookies(urls=urls)
        return tool_result({"cookies": cookies})

    async def set_cookie(self, context_id: str, cookie: dict[str, Any]) -> dict[str, Any]:
        context = self.get_context(context_id)
        await context.playwright_context.add_cookies([cookie])
        return tool_result({"success": True})

    async def clear_cookies(self, context_id: str) -> dict[str, Any]:
        context = self.get_context(context_id)
        await context.playwright_context.clear_cookies()
        return tool_result({"success": True})

    async def manage_storage(self, page_id: str, action: str, type: str, key: str | None = None, value: str | None = None) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        storage_name = "localStorage" if type == "local" else "sessionStorage"
        if action == "get":
            if key:
                result = await page_state.playwright_page.evaluate(f"(key) => {storage_name}.getItem(key)", key)
                return tool_result({"key": key, "value": result})
            result = await page_state.playwright_page.evaluate(
                f"() => Object.fromEntries(Object.entries({storage_name}))"
            )
            return tool_result({"items": result})
        if action == "set":
            await page_state.playwright_page.evaluate(f"([key, value]) => {storage_name}.setItem(key, value)", [key, value])
            return tool_result({"success": True})
        if action == "remove":
            await page_state.playwright_page.evaluate(f"(key) => {storage_name}.removeItem(key)", key)
            return tool_result({"success": True})
        await page_state.playwright_page.evaluate(f"() => {storage_name}.clear()")
        return tool_result({"success": True})

    async def get_indexeddb_summary(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        summary = await page_state.playwright_page.evaluate(
            """
            async () => {
              if (!indexedDB.databases) return {supported: false};
              const dbs = await indexedDB.databases();
              return {supported: true, databases: dbs};
            }
            """
        )
        return tool_result(summary)

    async def get_console_logs(
        self, page_id: str, level: str | None = None, since: str | None = None, cursor: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        entries = page_state.buffers.console
        if level:
            entries = [item for item in entries if item["type"] == level]
        return tool_result(self.apply_cursor(entries, cursor=cursor, limit=limit or self.state.defaults.max_list_length))

    async def get_page_errors(self, page_id: str, since: str | None = None, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        return tool_result(self.apply_cursor(page_state.buffers.errors, cursor=cursor, limit=limit or self.state.defaults.max_list_length))

    async def get_runtime_digest(self, page_id: str, since: str | None = None, mode: str = "compact") -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        digest = {
            "console_error_count": len([item for item in page_state.buffers.console if item["type"] == "error"]),
            "page_error_count": len(page_state.buffers.errors),
            "latest_console_errors": [item for item in page_state.buffers.console if item["type"] == "error"][-3:],
            "latest_page_errors": page_state.buffers.errors[-3:],
        }
        if mode != "compact":
            digest["console"] = page_state.buffers.console[-10:]
        return tool_result(digest)

    async def execute_page_js(self, page_id: str, script: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        result = await page_state.playwright_page.evaluate(script)
        return tool_result({"result": safe_json(result)})

    async def execute_local_python(self, script: str, context_id: str) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.get_context(context_id).artifact_dir),
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
        return tool_result(
            {
                "returncode": process.returncode,
                "stdout": stdout.decode("utf-8", errors="replace")[:MAX_INLINE_TEXT],
                "stderr": stderr.decode("utf-8", errors="replace")[:MAX_INLINE_TEXT],
            }
        )

    async def get_performance_metrics(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        metrics = await page_state.playwright_page.evaluate(
            """
            () => {
              const nav = performance.getEntriesByType("navigation")[0];
              const paint = performance.getEntriesByType("paint");
              return {
                navigation: nav ? {
                  domContentLoaded: nav.domContentLoadedEventEnd,
                  loadEventEnd: nav.loadEventEnd,
                  transferSize: nav.transferSize
                } : null,
                paints: paint.map((item) => ({name: item.name, startTime: item.startTime})),
                timing: performance.timing ? {
                  domComplete: performance.timing.domComplete - performance.timing.navigationStart
                } : null
              };
            }
            """
        )
        return tool_result(metrics)

    async def get_memory_usage(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        usage = await page_state.playwright_page.evaluate(
            """
            async () => {
              if ('measureUserAgentSpecificMemory' in performance) {
                return await performance.measureUserAgentSpecificMemory();
              }
              return performance.memory || null;
            }
            """
        )
        return tool_result({"memory": safe_json(usage)})

    async def get_resource_summary(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        summary = await page_state.playwright_page.evaluate(
            """
            () => {
              const grouped = {};
              for (const entry of performance.getEntriesByType('resource')) {
                const key = entry.initiatorType || 'other';
                grouped[key] = grouped[key] || {count: 0, transferSize: 0, duration: 0};
                grouped[key].count += 1;
                grouped[key].transferSize += entry.transferSize || 0;
                grouped[key].duration += entry.duration || 0;
              }
              return grouped;
            }
            """
        )
        return tool_result({"resources": summary})

    async def capture_performance_timeline(self, page_id: str, duration_ms: int) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        await page_state.playwright_page.evaluate(
            """
            () => {
              window.__browserPuppetTimeline = [];
              window.__browserPuppetObserver && window.__browserPuppetObserver.disconnect();
              window.__browserPuppetObserver = new PerformanceObserver((list) => {
                window.__browserPuppetTimeline.push(...list.getEntries().map((entry) => ({
                  name: entry.name,
                  entryType: entry.entryType,
                  startTime: entry.startTime,
                  duration: entry.duration
                })));
              });
              window.__browserPuppetObserver.observe({entryTypes: ['paint', 'longtask', 'layout-shift', 'resource']});
            }
            """
        )
        await asyncio.sleep(duration_ms / 1000)
        entries = await page_state.playwright_page.evaluate("() => window.__browserPuppetTimeline || []")
        return tool_result({"entries": entries[:200]})

    async def get_security_headers(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        main = next((entry for entry in reversed(page_state.buffers.network) if entry["url"] == page_state.playwright_page.url), None)
        headers = (main or {}).get("response", {}).get("headers", {})
        selected = {
            key: value
            for key, value in headers.items()
            if key.lower() in {"content-security-policy", "strict-transport-security", "x-frame-options", "permissions-policy", "access-control-allow-origin"}
        }
        return tool_result({"headers": selected})

    async def get_csp_violations(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        return tool_result({"violations": page_state.buffers.csp_violations[-20:]})

    async def get_mixed_content(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        items = [
            entry
            for entry in page_state.buffers.network
            if page_state.playwright_page.url.startswith("https://") and entry["url"].startswith("http://")
        ]
        return tool_result({"mixed_content": items[:20]})

    async def get_certificate_info(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        return tool_result({"url": page_state.playwright_page.url, "note": "Certificate details are browser-engine specific and not exposed by this implementation."})

    async def intercept_download(self, page_id: str, trigger: dict[str, Any] | None = None) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        before_count = len(page_state.buffers.downloads)
        if trigger:
            await self.dispatch_step(page_id=page_id, step=trigger)
        for _ in range(50):
            if len(page_state.buffers.downloads) > before_count:
                return tool_result(page_state.buffers.downloads[-1])
            await asyncio.sleep(0.1)
        raise SemanticError("download_timeout", "Timed out waiting for a download.", target={"page_id": page_id}, retryable=True)

    async def list_artifacts(self, context_id: str) -> dict[str, Any]:
        context = self.get_context(context_id)
        return tool_result(
            {
                "artifacts": [
                    {
                        "artifact_id": item.artifact_id,
                        "kind": item.kind,
                        "path": item.path,
                        "page_id": item.page_id,
                        "tool": item.tool,
                    }
                    for item in context.artifacts
                ]
            }
        )

    async def generate_report(self, context_id: str, format: str = "json") -> dict[str, Any]:
        context = self.get_context(context_id)
        payload = {
            "context_id": context_id,
            "pages": [
                {
                    "page_id": page_state.page_id,
                    "url": page_state.playwright_page.url,
                    "console_errors": [item for item in page_state.buffers.console if item["type"] == "error"],
                    "page_errors": page_state.buffers.errors,
                    "downloads": page_state.buffers.downloads,
                }
                for page_state in context.pages.values()
            ],
            "artifacts": [
                {"artifact_id": item.artifact_id, "kind": item.kind, "path": item.path}
                for item in context.artifacts
            ],
        }
        if format == "markdown":
            lines = [f"# Browser Puppet Report", f"", f"- Context: `{context_id}`", f""]
            for page in payload["pages"]:
                lines.append(f"## {page['page_id']}")
                lines.append(f"- URL: {page['url']}")
                lines.append(f"- Console errors: {len(page['console_errors'])}")
                lines.append(f"- Page errors: {len(page['page_errors'])}")
            report_path = context.artifact_dir / "report.md"
            report_path.write_text("\n".join(lines))
            artifact = self._add_artifact(context, "report", str(report_path.resolve()), context.active_page_id, "generate_report")
            return tool_result({"path": artifact["path"], "artifact": artifact})
        report_path = context.artifact_dir / "report.json"
        report_path.write_text(json.dumps(payload, indent=2))
        artifact = self._add_artifact(context, "report", str(report_path.resolve()), context.active_page_id, "generate_report")
        return tool_result({"path": artifact["path"], "artifact": artifact, "report": payload})

    async def run_steps(
        self, page_id: str, steps: list[dict[str, Any]], stop_on_failure: bool = True, observe: str = "final_only"
    ) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        before = await self.before_mutation(page_state, "auto")
        results = []
        for index, step in enumerate(steps):
            try:
                step_page_id = step.get("page_id", page_id)
                result = await self.dispatch_step(step_page_id, step)
                results.append({"index": index, "tool": step["tool"], "result": result})
            except Exception as exc:
                results.append({"index": index, "tool": step["tool"], "error": self.normalize_exception(exc)})
                if stop_on_failure:
                    break
        outcome = await self.action_outcome(page_state, "run_steps", before)
        if observe == "each":
            outcome["steps"] = results
        else:
            outcome["step_count"] = len(results)
            outcome["errors"] = [item for item in results if "error" in item]
        return outcome

    async def dispatch_step(self, page_id: str | None, step: dict[str, Any]) -> dict[str, Any]:
        tool = step["tool"]
        args = {key: value for key, value in step.items() if key != "tool"}
        if page_id and "page_id" not in args:
            args["page_id"] = page_id
        fn = getattr(self, tool, None)
        if fn is None:
            raise SemanticError("unsupported_tool", f"Unknown step tool '{tool}'.", target={"tool": tool})
        return await fn(**args)

    async def before_mutation(self, page_state: PageState, observe: str) -> dict[str, Any] | None:
        if observe == "off":
            return None
        if self.state.defaults.checkpoint_auto or observe == "full":
            return await self.capture_state(page_state, kinds=["dom", "console", "network"])
        return await self.get_lightweight_checkpoint(page_state)

    async def get_lightweight_checkpoint(self, page_state: PageState) -> dict[str, Any]:
        return {
            "url": page_state.playwright_page.url,
            "title": await page_state.playwright_page.title(),
            "console_count": len(page_state.buffers.console),
            "error_count": len(page_state.buffers.errors),
            "network_count": len(page_state.buffers.network),
        }

    async def action_outcome(self, page_state: PageState, tool_name: str, before: dict[str, Any] | None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        current = await self.get_page_digest(page_state.page_id, mode="compact")
        changes = {}
        if before:
            changes = self.diff_states(before, current, mode="compact")
        response = {
            "success": True,
            "page_id": page_state.page_id,
            "tool": tool_name,
            "digest": current,
            "changes": changes,
        }
        if extra:
            response.update(extra)
        return tool_result(response)

    async def capture_state(self, page_state: PageState, kinds: list[str]) -> dict[str, Any]:
        payload: dict[str, Any] = {"page_id": page_state.page_id, "captured_at": utc_ts(), "kinds": kinds}
        if "dom" in kinds:
            payload["dom"] = (await self.get_dom_snapshot(page_state.page_id, scope="interactive"))["nodes"]
        if "aom" in kinds:
            payload["aom"] = (await self.get_aom_snapshot(page_state.page_id))["aom"]
        if "visual" in kinds:
            payload["visual"] = await self.get_visual_digest(page_state.page_id, mode="compact")
        if "network" in kinds:
            payload["network"] = page_state.buffers.network[-50:]
        if "console" in kinds:
            payload["console"] = page_state.buffers.console[-50:]
        return payload

    def diff_states(self, previous: dict[str, Any], current: dict[str, Any], mode: str = "compact") -> dict[str, Any]:
        diff: dict[str, Any] = {"changed_fields": []}
        for key, value in current.items():
            if previous.get(key) != value:
                diff["changed_fields"].append(key)
                if mode != "compact":
                    diff[key] = {"before": safe_json(previous.get(key)), "after": safe_json(value)}
        diff["meaningful_change"] = bool(diff["changed_fields"])
        return diff

    def apply_cursor(self, items: list[dict[str, Any]], cursor: str | None, limit: int) -> dict[str, Any]:
        start = int(cursor or 0)
        chunk = items[start : start + limit]
        next_cursor = start + limit if start + limit < len(items) else None
        return {"items": chunk, "next_cursor": str(next_cursor) if next_cursor is not None else None}

    def normalize_exception(self, exc: Exception) -> dict[str, Any]:
        if isinstance(exc, SemanticError):
            return exc.to_dict()
        return {
            "error_code": "tool_failure",
            "message": str(exc),
            "retryable": False,
            "likely_causes": [],
            "next_steps": [],
        }


APP = BrowserPuppetApp()
mcp = FastMCP("browser-puppet")


def expose(fn_name: str):
    def decorator(func):
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                return APP.normalize_exception(exc)  # type: ignore[return-value]

        wrapper.__name__ = fn_name
        return mcp.tool(name=fn_name)(wrapper)

    return decorator


@expose("create_context")
async def _create_context(browser: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    return await APP.create_context(browser, profile)


@expose("open_page")
async def _open_page(context_id: str, url: str, wait_until: str = "load") -> dict[str, Any]:
    return await APP.open_page(context_id, url, wait_until)


@expose("navigate")
async def _navigate(page_id: str, url: str, wait_until: str = "load") -> dict[str, Any]:
    return await APP.navigate(page_id, url, wait_until)


@expose("reload_page")
async def _reload_page(page_id: str, ignore_cache: bool = False) -> dict[str, Any]:
    return await APP.reload_page(page_id, ignore_cache)


@expose("go_back")
async def _go_back(page_id: str) -> dict[str, Any]:
    return await APP.go_back(page_id)


@expose("go_forward")
async def _go_forward(page_id: str) -> dict[str, Any]:
    return await APP.go_forward(page_id)


@expose("list_pages")
async def _list_pages(context_id: str) -> dict[str, Any]:
    return await APP.list_pages(context_id)


@expose("switch_page")
async def _switch_page(page_id: str) -> dict[str, Any]:
    return await APP.switch_page(page_id)


@expose("resize_viewport")
async def _resize_viewport(page_id: str, width: int, height: int) -> dict[str, Any]:
    return await APP.resize_viewport(page_id, width, height)


@expose("set_emulation")
async def _set_emulation(page_id: str, settings: dict[str, Any]) -> dict[str, Any]:
    return await APP.set_emulation(page_id, settings)


@expose("scroll")
async def _scroll(page_id: str, direction: str, amount_px: int | None = None) -> dict[str, Any]:
    return await APP.scroll(page_id, direction, amount_px)


@expose("close_page")
async def _close_page(page_id: str) -> dict[str, Any]:
    return await APP.close_page(page_id)


@expose("close_context")
async def _close_context(context_id: str) -> dict[str, Any]:
    return await APP.close_context(context_id)


@expose("save_storage_state")
async def _save_storage_state(context_id: str, path: str | None = None) -> dict[str, Any]:
    return await APP.save_storage_state(context_id, path)


@expose("load_storage_state")
async def _load_storage_state(context_id: str, state: str | dict[str, Any]) -> dict[str, Any]:
    return await APP.load_storage_state(context_id, state)


@expose("set_extra_http_headers")
async def _set_extra_http_headers(page_id: str, headers: dict[str, str]) -> dict[str, Any]:
    return await APP.set_extra_http_headers(page_id, headers)


@expose("set_http_credentials")
async def _set_http_credentials(context_id: str, username: str, password: str) -> dict[str, Any]:
    return await APP.set_http_credentials(context_id, username, password)


@expose("generate_totp")
async def _generate_totp(secret: str, algorithm: str = "SHA1", digits: int = 6, period: int = 30) -> dict[str, Any]:
    return await APP.generate_totp(secret, algorithm, digits, period)


@expose("configure_session")
async def _configure_session(defaults: dict[str, Any] | None = None, proactive_events: bool | None = None) -> dict[str, Any]:
    return await APP.configure_session(defaults, proactive_events)


@expose("create_checkpoint")
async def _create_checkpoint(page_id: str, name: str, kinds: list[str] | None = None) -> dict[str, Any]:
    return await APP.create_checkpoint(page_id, name, kinds)


@expose("release_checkpoint")
async def _release_checkpoint(checkpoint_id: str) -> dict[str, Any]:
    return await APP.release_checkpoint(checkpoint_id)


@expose("diff_since_checkpoint")
async def _diff_since_checkpoint(page_id: str, checkpoint_name: str, kinds: list[str] | None = None, mode: str = "compact") -> dict[str, Any]:
    return await APP.diff_since_checkpoint(page_id, checkpoint_name, kinds, mode)


@expose("get_cache_stats")
async def _get_cache_stats(context_id: str) -> dict[str, Any]:
    return await APP.get_cache_stats(context_id)


@expose("get_page_meta")
async def _get_page_meta(page_id: str) -> dict[str, Any]:
    return await APP.get_page_meta(page_id)


@expose("get_viewport_state")
async def _get_viewport_state(page_id: str) -> dict[str, Any]:
    return await APP.get_viewport_state(page_id)


@expose("get_page_outline")
async def _get_page_outline(page_id: str) -> dict[str, Any]:
    return await APP.get_page_outline(page_id)


@expose("get_page_digest")
async def _get_page_digest(page_id: str, mode: str = "compact") -> dict[str, Any]:
    return await APP.get_page_digest(page_id, mode)


@expose("find_elements")
async def _find_elements(page_id: str, query: dict[str, Any]) -> dict[str, Any]:
    return await APP.find_elements(page_id, query)


@expose("find_interactive_candidates")
async def _find_interactive_candidates(page_id: str, intent: str, filters: dict[str, Any] | None = None, limit: int = 10) -> dict[str, Any]:
    return await APP.find_interactive_candidates(page_id, intent, filters, limit)


@expose("get_element_state")
async def _get_element_state(element_id: str, attribute: str | None = None) -> dict[str, Any]:
    return await APP.get_element_state(element_id, attribute)


@expose("get_element_box")
async def _get_element_box(element_id: str) -> dict[str, Any]:
    return await APP.get_element_box(element_id)


@expose("get_computed_style")
async def _get_computed_style(element_id: str, properties: list[str] | None = None) -> dict[str, Any]:
    return await APP.get_computed_style(element_id, properties)


@expose("get_aom_snapshot")
async def _get_aom_snapshot(page_id: str, include_hidden: bool = False) -> dict[str, Any]:
    return await APP.get_aom_snapshot(page_id, include_hidden)


@expose("get_dom_snapshot")
async def _get_dom_snapshot(page_id: str, scope: str = "interactive") -> dict[str, Any]:
    return await APP.get_dom_snapshot(page_id, scope)


@expose("get_dom_diff")
async def _get_dom_diff(page_id: str, previous_state_id: str) -> dict[str, Any]:
    return await APP.get_dom_diff(page_id, previous_state_id)


@expose("get_state_handle")
async def _get_state_handle(page_id: str, kinds: list[str] | None = None) -> dict[str, Any]:
    return await APP.get_state_handle(page_id, kinds)


@expose("hydrate_state_slice")
async def _hydrate_state_slice(handle_id: str, slice: dict[str, Any]) -> dict[str, Any]:
    return await APP.hydrate_state_slice(handle_id, slice)


@expose("list_frames")
async def _list_frames(page_id: str) -> dict[str, Any]:
    return await APP.list_frames(page_id)


@expose("switch_frame")
async def _switch_frame(frame_id: str) -> dict[str, Any]:
    return await APP.switch_frame(frame_id)


@expose("query_shadow_dom")
async def _query_shadow_dom(host_element_id: str, selector: str) -> dict[str, Any]:
    return await APP.query_shadow_dom(host_element_id, selector)


@expose("get_shadow_root")
async def _get_shadow_root(element_id: str) -> dict[str, Any]:
    return await APP.get_shadow_root(element_id)


@expose("extract_text")
async def _extract_text(page_id: str, scope: str = "visible", element_id: str | None = None) -> dict[str, Any]:
    return await APP.extract_text(page_id, scope, element_id)


@expose("extract_table_data")
async def _extract_table_data(element_id: str) -> dict[str, Any]:
    return await APP.extract_table_data(element_id)


@expose("get_selection")
async def _get_selection(page_id: str) -> dict[str, Any]:
    return await APP.get_selection(page_id)


@expose("take_screenshot")
async def _take_screenshot(target: str, page_id: str | None = None, element_id: str | None = None, return_method: str = "disk") -> dict[str, Any]:
    return await APP.take_screenshot(target, page_id, element_id, return_method)


@expose("get_annotated_screenshot")
async def _get_annotated_screenshot(page_id: str, viewport_only: bool = True) -> dict[str, Any]:
    return await APP.get_annotated_screenshot(page_id, viewport_only)


@expose("get_visual_digest")
async def _get_visual_digest(page_id: str, mode: str = "compact") -> dict[str, Any]:
    return await APP.get_visual_digest(page_id, mode)


@expose("start_trace")
async def _start_trace(context_id: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    return await APP.start_trace(context_id, options)


@expose("stop_trace")
async def _stop_trace(context_id: str) -> dict[str, Any]:
    return await APP.stop_trace(context_id)


@expose("compare_viewports")
async def _compare_viewports(page_id: str, profiles: list[dict[str, Any]], mode: str = "compact") -> dict[str, Any]:
    return await APP.compare_viewports(page_id, profiles, mode)


@expose("run_accessibility_audit")
async def _run_accessibility_audit(page_id: str, scope: str = "page") -> dict[str, Any]:
    return await APP.run_accessibility_audit(page_id, scope)


@expose("get_issue_digest")
async def _get_issue_digest(page_id: str, sources: list[str], limit: int = 10) -> dict[str, Any]:
    return await APP.get_issue_digest(page_id, sources, limit)


@expose("get_focus_order")
async def _get_focus_order(page_id: str) -> dict[str, Any]:
    return await APP.get_focus_order(page_id)


@expose("get_live_regions")
async def _get_live_regions(page_id: str) -> dict[str, Any]:
    return await APP.get_live_regions(page_id)


@expose("click")
async def _click(
    page_id: str | None = None,
    element_id: str | None = None,
    target: dict[str, Any] | None = None,
    button: str = "left",
    click_count: int = 1,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    observe: str = "auto",
) -> dict[str, Any]:
    return await APP.click(page_id, element_id, target, button, click_count, timeout_ms, observe)


@expose("tap")
async def _tap(page_id: str | None = None, element_id: str | None = None, target: dict[str, Any] | None = None, observe: str = "auto") -> dict[str, Any]:
    return await APP.tap(page_id, element_id, target, observe)


@expose("type_text")
async def _type_text(
    text: str,
    page_id: str | None = None,
    element_id: str | None = None,
    target: dict[str, Any] | None = None,
    clear_first: bool = True,
    observe: str = "auto",
) -> dict[str, Any]:
    return await APP.type_text(text, page_id, element_id, target, clear_first, observe)


@expose("press_key")
async def _press_key(page_id: str, key: str, observe: str = "auto") -> dict[str, Any]:
    return await APP.press_key(page_id, key, observe)


@expose("press_key_chord")
async def _press_key_chord(page_id: str, keys: list[str], observe: str = "auto") -> dict[str, Any]:
    return await APP.press_key_chord(page_id, keys, observe)


@expose("hover")
async def _hover(page_id: str | None = None, element_id: str | None = None, target: dict[str, Any] | None = None) -> dict[str, Any]:
    return await APP.hover(page_id, element_id, target)


@expose("drag_and_drop")
async def _drag_and_drop(
    page_id: str,
    source_element_id: str | None = None,
    source_target: dict[str, Any] | None = None,
    target_element_id: str | None = None,
    dest_target: dict[str, Any] | None = None,
    observe: str = "auto",
) -> dict[str, Any]:
    return await APP.drag_and_drop(page_id, source_element_id, source_target, target_element_id, dest_target, observe)


@expose("select_dropdown")
async def _select_dropdown(
    value: str,
    page_id: str | None = None,
    element_id: str | None = None,
    target: dict[str, Any] | None = None,
    observe: str = "auto",
) -> dict[str, Any]:
    return await APP.select_dropdown(value, page_id, element_id, target, observe)


@expose("set_checkbox")
async def _set_checkbox(
    checked: bool,
    page_id: str | None = None,
    element_id: str | None = None,
    target: dict[str, Any] | None = None,
    observe: str = "auto",
) -> dict[str, Any]:
    return await APP.set_checkbox(checked, page_id, element_id, target, observe)


@expose("upload_file")
async def _upload_file(
    file_path: str,
    page_id: str | None = None,
    element_id: str | None = None,
    target: dict[str, Any] | None = None,
    observe: str = "auto",
) -> dict[str, Any]:
    return await APP.upload_file(file_path, page_id, element_id, target, observe)


@expose("handle_dialog")
async def _handle_dialog(action: str, prompt_text: str | None = None) -> dict[str, Any]:
    return await APP.handle_dialog(action, prompt_text)


@expose("swipe")
async def _swipe(page_id: str, start: dict[str, int], end: dict[str, int], duration_ms: int = 300, observe: str = "auto") -> dict[str, Any]:
    return await APP.swipe(page_id, start, end, duration_ms, observe)


@expose("long_press")
async def _long_press(
    page_id: str | None = None,
    element_id: str | None = None,
    target: dict[str, Any] | None = None,
    duration_ms: int = 800,
    observe: str = "auto",
) -> dict[str, Any]:
    return await APP.long_press(page_id, element_id, target, duration_ms, observe)


@expose("mouse_move")
async def _mouse_move(page_id: str, x: int, y: int) -> dict[str, Any]:
    return await APP.mouse_move(page_id, x, y)


@expose("mouse_click_at")
async def _mouse_click_at(page_id: str, x: int, y: int, button: str = "left", observe: str = "auto") -> dict[str, Any]:
    return await APP.mouse_click_at(page_id, x, y, button, observe)


@expose("mouse_wheel")
async def _mouse_wheel(page_id: str, delta_x: int, delta_y: int) -> dict[str, Any]:
    return await APP.mouse_wheel(page_id, delta_x, delta_y)


@expose("scroll_element")
async def _scroll_element(
    direction: str,
    amount_px: int = 200,
    page_id: str | None = None,
    element_id: str | None = None,
    target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await APP.scroll_element(direction, amount_px, page_id, element_id, target)


@expose("clipboard_read")
async def _clipboard_read(page_id: str) -> dict[str, Any]:
    return await APP.clipboard_read(page_id)


@expose("clipboard_write")
async def _clipboard_write(page_id: str, text: str) -> dict[str, Any]:
    return await APP.clipboard_write(page_id, text)


@expose("fill_contenteditable")
async def _fill_contenteditable(html: str, page_id: str | None = None, element_id: str | None = None, target: dict[str, Any] | None = None) -> dict[str, Any]:
    return await APP.fill_contenteditable(html, page_id, element_id, target)


@expose("select_date")
async def _select_date(value: str, page_id: str | None = None, element_id: str | None = None, target: dict[str, Any] | None = None) -> dict[str, Any]:
    return await APP.select_date(value, page_id, element_id, target)


@expose("set_input_value")
async def _set_input_value(value: str, page_id: str | None = None, element_id: str | None = None, target: dict[str, Any] | None = None) -> dict[str, Any]:
    return await APP.set_input_value(value, page_id, element_id, target)


@expose("fill_form")
async def _fill_form(
    page_id: str,
    fields: list[dict[str, Any]],
    form_target: dict[str, Any] | None = None,
    submit: bool = False,
    observe: str = "auto",
) -> dict[str, Any]:
    return await APP.fill_form(page_id, fields, form_target, submit, observe)


@expose("wait_for")
async def _wait_for(target: dict[str, Any], state: str, page_id: str | None = None, observe: str = "auto") -> dict[str, Any]:
    return await APP.wait_for(target, state, page_id, observe)


@expose("run_action_and_describe")
async def _run_action_and_describe(action: dict[str, Any], expect: dict[str, Any] | None = None, mode: str = "compact") -> dict[str, Any]:
    return await APP.run_action_and_describe(action, expect, mode)


@expose("get_network_traffic")
async def _get_network_traffic(
    page_id: str, filters: dict[str, Any] | None = None, since: str | None = None, cursor: str | None = None, limit: int | None = None
) -> dict[str, Any]:
    return await APP.get_network_traffic(page_id, filters, since, cursor, limit)


@expose("get_request_detail")
async def _get_request_detail(request_id: str) -> dict[str, Any]:
    return await APP.get_request_detail(request_id)


@expose("get_response_body")
async def _get_response_body(request_id: str, encoding: str = "text") -> dict[str, Any]:
    return await APP.get_response_body(request_id, encoding)


@expose("get_network_digest")
async def _get_network_digest(page_id: str, window: dict[str, Any] | None = None, mode: str = "compact") -> dict[str, Any]:
    return await APP.get_network_digest(page_id, window, mode)


@expose("set_headers")
async def _set_headers(context_id: str, headers: dict[str, str]) -> dict[str, Any]:
    return await APP.set_headers(context_id, headers)


@expose("get_cookies")
async def _get_cookies(context_id: str, urls: list[str] | None = None) -> dict[str, Any]:
    return await APP.get_cookies(context_id, urls)


@expose("set_cookie")
async def _set_cookie(context_id: str, cookie: dict[str, Any]) -> dict[str, Any]:
    return await APP.set_cookie(context_id, cookie)


@expose("clear_cookies")
async def _clear_cookies(context_id: str) -> dict[str, Any]:
    return await APP.clear_cookies(context_id)


@expose("manage_storage")
async def _manage_storage(page_id: str, action: str, type: str, key: str | None = None, value: str | None = None) -> dict[str, Any]:
    return await APP.manage_storage(page_id, action, type, key, value)


@expose("get_indexeddb_summary")
async def _get_indexeddb_summary(page_id: str) -> dict[str, Any]:
    return await APP.get_indexeddb_summary(page_id)


@expose("get_console_logs")
async def _get_console_logs(
    page_id: str, level: str | None = None, since: str | None = None, cursor: str | None = None, limit: int | None = None
) -> dict[str, Any]:
    return await APP.get_console_logs(page_id, level, since, cursor, limit)


@expose("get_page_errors")
async def _get_page_errors(page_id: str, since: str | None = None, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
    return await APP.get_page_errors(page_id, since, cursor, limit)


@expose("get_runtime_digest")
async def _get_runtime_digest(page_id: str, since: str | None = None, mode: str = "compact") -> dict[str, Any]:
    return await APP.get_runtime_digest(page_id, since, mode)


@expose("execute_page_js")
async def _execute_page_js(page_id: str, script: str) -> dict[str, Any]:
    return await APP.execute_page_js(page_id, script)


@expose("execute_local_python")
async def _execute_local_python(script: str, context_id: str) -> dict[str, Any]:
    return await APP.execute_local_python(script, context_id)


@expose("get_performance_metrics")
async def _get_performance_metrics(page_id: str) -> dict[str, Any]:
    return await APP.get_performance_metrics(page_id)


@expose("get_memory_usage")
async def _get_memory_usage(page_id: str) -> dict[str, Any]:
    return await APP.get_memory_usage(page_id)


@expose("get_resource_summary")
async def _get_resource_summary(page_id: str) -> dict[str, Any]:
    return await APP.get_resource_summary(page_id)


@expose("capture_performance_timeline")
async def _capture_performance_timeline(page_id: str, duration_ms: int) -> dict[str, Any]:
    return await APP.capture_performance_timeline(page_id, duration_ms)


@expose("get_security_headers")
async def _get_security_headers(page_id: str) -> dict[str, Any]:
    return await APP.get_security_headers(page_id)


@expose("get_csp_violations")
async def _get_csp_violations(page_id: str) -> dict[str, Any]:
    return await APP.get_csp_violations(page_id)


@expose("get_mixed_content")
async def _get_mixed_content(page_id: str) -> dict[str, Any]:
    return await APP.get_mixed_content(page_id)


@expose("get_certificate_info")
async def _get_certificate_info(page_id: str) -> dict[str, Any]:
    return await APP.get_certificate_info(page_id)


@expose("intercept_download")
async def _intercept_download(page_id: str, trigger: dict[str, Any] | None = None) -> dict[str, Any]:
    return await APP.intercept_download(page_id, trigger)


@expose("list_artifacts")
async def _list_artifacts(context_id: str) -> dict[str, Any]:
    return await APP.list_artifacts(context_id)


@expose("generate_report")
async def _generate_report(context_id: str, format: str = "json") -> dict[str, Any]:
    return await APP.generate_report(context_id, format)


@expose("run_steps")
async def _run_steps(page_id: str, steps: list[dict[str, Any]], stop_on_failure: bool = True, observe: str = "final_only") -> dict[str, Any]:
    return await APP.run_steps(page_id, steps, stop_on_failure, observe)


def main() -> None:
    parser = argparse.ArgumentParser(description="Browser Puppet MCP server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--host", default=DEFAULT_SSE_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_SSE_PORT)
    args = parser.parse_args()
    if args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
