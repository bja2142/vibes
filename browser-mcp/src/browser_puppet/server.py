from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import AsyncExitStack, asynccontextmanager, suppress
import functools
import fnmatch
import inspect
import ipaddress
import json
import logging
import mcp.types as mcp_types
import os
import random
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

from mcp.server.fastmcp import FastMCP
from mcp.server.models import InitializationOptions
from mcp.server.session import InitializationState, ServerSession
from pydantic import ConfigDict, model_validator
from playwright.async_api import Browser, BrowserContext, ElementHandle, Frame, Locator, Page, async_playwright

from .config import (
    DEFAULT_AUTO_CLOSE_STALE_CONTEXTS,
    DEFAULT_ALLOW_LOCAL_NETWORK,
    DEFAULT_MAX_CONTEXTS,
    DEFAULT_NAVIGATION_TIMEOUT_MS,
    DEFAULT_SSE_HOST,
    DEFAULT_SSE_PORT,
    DEFAULT_STALE_CONTEXT_TIMEOUT_SECONDS,
    DEFAULT_TEXT_TRANSFER_MAX_BYTES,
    DEFAULT_TIMEOUT_MS,
    DEFAULT_TRANSIENT_RETRY_DELAY_MS,
    MAX_INLINE_TEXT,
    get_default_artifact_dir,
)
from .errors import SemanticError
from .models import ArtifactRecord, ContextState, ElementRecord, PageState, ServerState, SessionDefaults
from .utils import compute_totp, ensure_dir, new_id, origin_from_url, safe_json, summarize_text, utc_ts


TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")
BLOCKED_BY_CLIENT_URL_RE = re.compile(r"ERR_BLOCKED_BY_CLIENT at (?P<url>\S+)")


def _trace(self: logging.Logger, message: str, *args: Any, **kwargs: Any) -> None:
    if self.isEnabledFor(TRACE_LEVEL):
        self._log(TRACE_LEVEL, message, args, **kwargs)


logging.Logger.trace = _trace  # type: ignore[attr-defined]
LOGGER = logging.getLogger("browser_puppet")
TRANSIENT_INTERNAL_ERROR_PATTERNS = (
    re.compile(r"function takes exactly \d+ arguments \(\d+ given\)"),
)
_ORIGINAL_SERVER_SESSION_RECEIVED_REQUEST = ServerSession._received_request


def tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    return payload


def summarize_payload(value: Any, limit: int = 1200) -> str:
    text = json.dumps(safe_json(value), sort_keys=True, default=str)
    return summarize_text(text, limit=limit)


def normalize_log_level(value: str | None) -> int:
    if not value:
        return logging.INFO
    normalized = value.strip().upper().replace("-", "_")
    if normalized == "TRACE":
        return TRACE_LEVEL
    level = getattr(logging, normalized, None)
    if isinstance(level, int):
        return level
    raise ValueError(f"Unsupported log level '{value}'.")


def configure_logging(level_name: str | None = None) -> int:
    level = normalize_log_level(level_name or os.environ.get("BROWSER_PUPPET_LOG_LEVEL", "INFO"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    logging.getLogger("uvicorn").setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)
    logging.getLogger("mcp").setLevel(level)
    LOGGER.setLevel(level)
    LOGGER.info("logging_configured level=%s", logging.getLevelName(level))
    return level


def unwrap_mcp_tool_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[tuple[Any, ...], dict[str, Any]]:
    if args:
        return args, kwargs
    if set(kwargs.keys()) != {"args", "kwargs"}:
        return args, kwargs
    raw_args = kwargs.get("args", [])
    raw_kwargs = kwargs.get("kwargs", {})
    if isinstance(raw_args, str):
        raw_args = json.loads(raw_args)
    if isinstance(raw_kwargs, str):
        raw_kwargs = json.loads(raw_kwargs)
    if not isinstance(raw_args, list):
        raise TypeError("Wrapped MCP args must decode to a list.")
    if not isinstance(raw_kwargs, dict):
        raise TypeError("Wrapped MCP kwargs must decode to an object.")
    return tuple(raw_args), raw_kwargs


TARGET_QUERY_KEYS = (
    "selector",
    "role",
    "label",
    "placeholder",
    "text",
    "xpath",
    "css",
    "exact",
    "visible",
    "scope",
    "limit",
)
TARGET_QUERY_KEYS_WITHOUT_TEXT = tuple(key for key in TARGET_QUERY_KEYS if key != "text")
WAIT_FOR_TARGET_KEYS = TARGET_QUERY_KEYS + ("pattern", "timeout_ms")
TARGET_COMPAT_TOOLS = (
    "click",
    "tap",
    "hover",
    "select_dropdown",
    "set_checkbox",
    "upload_file",
    "long_press",
    "fill_contenteditable",
    "select_date",
    "set_input_value",
    "scroll_element",
    "submit_form",
)
FILL_FORM_FIELD_KEYS = TARGET_QUERY_KEYS + ("value", "action", "checked")


def normalize_string_query_payload(value: Any, *, field_name: str, string_key: str = "selector") -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        return {string_key: value}
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} must decode to an object.")
    return dict(value)


def lift_fields_into_nested_payload(
    payload: dict[str, Any],
    *,
    field_name: str,
    candidate_keys: tuple[str, ...],
    string_key: str = "selector",
    hoist_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    normalized = dict(payload)
    nested = normalize_string_query_payload(normalized.get(field_name), field_name=field_name, string_key=string_key)

    for hoist_key in hoist_keys:
        if hoist_key not in normalized and hoist_key in nested:
            normalized[hoist_key] = nested.pop(hoist_key)

    for key in candidate_keys:
        if key in normalized and key not in nested:
            nested[key] = normalized.pop(key)

    if nested:
        normalized[field_name] = nested

    return normalized


def build_nested_payload_normalizer(
    *,
    field_name: str,
    candidate_keys: tuple[str, ...],
    string_key: str = "selector",
    hoist_keys: tuple[str, ...] = (),
    defaults_factory: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def normalize(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = lift_fields_into_nested_payload(
            payload,
            field_name=field_name,
            candidate_keys=candidate_keys,
            string_key=string_key,
            hoist_keys=hoist_keys,
        )
        if defaults_factory is not None:
            normalized = defaults_factory(normalized)
        return normalized

    return normalize


def normalize_find_interactive_candidates_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return lift_fields_into_nested_payload(
        payload,
        field_name="filters",
        candidate_keys=TARGET_QUERY_KEYS,
    )


def normalize_press_key_chord_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    keys = normalized.get("keys")
    if isinstance(keys, str):
        normalized["keys"] = [part.strip() for part in keys.split("+") if part.strip()]
    return normalized


def normalize_fill_form_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    fields = normalized.get("fields")

    def normalize_form_target_fields(current: dict[str, Any]) -> dict[str, Any]:
        if "form_target" in current:
            current = lift_fields_into_nested_payload(
                current,
                field_name="form_target",
                candidate_keys=TARGET_QUERY_KEYS,
            )
        return current

    if isinstance(fields, dict):
        fields = [fields]
    if isinstance(fields, list):
        normalized_fields = []
        for field in fields:
            if not isinstance(field, dict):
                raise TypeError("fill_form fields must decode to objects.")
            item = dict(field)
            item = lift_fields_into_nested_payload(
                item,
                field_name="target",
                candidate_keys=TARGET_QUERY_KEYS,
            )
            if "checked" in item and "value" not in item:
                item["value"] = item.pop("checked")
                item.setdefault("action", "check")
            normalized_fields.append(item)
        normalized["fields"] = normalized_fields
        return normalize_form_target_fields(normalized)

    loose_field = {
        key: normalized.pop(key)
        for key in tuple(normalized.keys())
        if key in FILL_FORM_FIELD_KEYS
    }
    if loose_field:
        loose_field = lift_fields_into_nested_payload(
            loose_field,
            field_name="target",
            candidate_keys=TARGET_QUERY_KEYS,
        )
        if "checked" in loose_field and "value" not in loose_field:
            loose_field["value"] = loose_field.pop("checked")
            loose_field.setdefault("action", "check")
        normalized["fields"] = [loose_field]

    return normalize_form_target_fields(normalized)


def normalize_fill_and_click_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_fill_form_payload(payload)
    return lift_fields_into_nested_payload(
        normalized,
        field_name="click_target",
        candidate_keys=TARGET_QUERY_KEYS,
    )


def normalize_click_and_wait_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = lift_fields_into_nested_payload(
        payload,
        field_name="target",
        candidate_keys=TARGET_QUERY_KEYS,
        hoist_keys=("page_id",),
    )
    return lift_fields_into_nested_payload(
        normalized,
        field_name="wait_target",
        candidate_keys=WAIT_FOR_TARGET_KEYS,
    )


def normalize_step_payload(payload: dict[str, Any], *, step_field: str, reserved_keys: tuple[str, ...]) -> dict[str, Any]:
    normalized = dict(payload)
    step = normalized.get(step_field)

    if step is None and "tool" in normalized:
        step = {key: normalized.pop(key) for key in tuple(normalized.keys()) if key not in reserved_keys}
    elif isinstance(step, str):
        step = {"tool": step}
    elif isinstance(step, dict):
        step = dict(step)
    elif step is not None:
        raise TypeError(f"{step_field} must decode to an object.")

    if not step:
        return normalized

    tool_name = step.get("tool")
    if isinstance(tool_name, str):
        step = normalize_mcp_tool_payload(step, tool_name, ())
    normalized[step_field] = step
    return normalized


def normalize_run_action_and_describe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return normalize_step_payload(payload, step_field="action", reserved_keys=("expect", "mode", "action"))


def normalize_mcp_tool_payload(payload: Any, fn_name: str, param_names: tuple[str, ...]) -> Any:
    if not isinstance(payload, dict):
        return payload

    normalized = dict(payload)
    if "args" in normalized or "kwargs" in normalized:
        raw_args = normalized.pop("args", [])
        raw_kwargs = normalized.pop("kwargs", {})
        call_args, call_kwargs = unwrap_mcp_tool_call((), {"args": raw_args, "kwargs": raw_kwargs})
        compat_payload = dict(call_kwargs)
        for index, value in enumerate(call_args):
            if index >= len(param_names):
                break
            compat_payload.setdefault(param_names[index], value)
        compat_payload.update(normalized)
        normalized = compat_payload

    for normalizer in TOOL_PAYLOAD_NORMALIZERS.get(fn_name, ()):
        normalized = normalizer(normalized)

    return normalized


def normalize_create_context_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    extra_profile_keys = [key for key in normalized if key not in {"browser", "profile"}]

    if extra_profile_keys:
        profile = normalized.get("profile")
        if profile is None:
            profile = {}
        elif not isinstance(profile, dict):
            raise TypeError("create_context profile must decode to an object.")
        else:
            profile = dict(profile)
        for key in extra_profile_keys:
            profile[key] = normalized.pop(key)
        normalized["profile"] = profile

    if "browser" not in normalized:
        normalized["browser"] = "chromium"

    return normalized


def normalize_wait_for_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = lift_fields_into_nested_payload(
        payload,
        field_name="target",
        candidate_keys=WAIT_FOR_TARGET_KEYS,
        hoist_keys=("page_id",),
    )
    target = normalize_string_query_payload(normalized.get("target"), field_name="wait_for target")

    if "state" not in normalized:
        if "pattern" in target:
            normalized["state"] = "url"
        elif target:
            normalized["state"] = "visible"

    return normalized


TOOL_PAYLOAD_NORMALIZERS: dict[str, tuple[Callable[[dict[str, Any]], dict[str, Any]], ...]] = {
    "create_context": (normalize_create_context_payload,),
    "find_elements": (
        build_nested_payload_normalizer(
            field_name="query",
            candidate_keys=TARGET_QUERY_KEYS,
        ),
    ),
    "find_interactive_candidates": (normalize_find_interactive_candidates_payload,),
    "fill_form": (normalize_fill_form_payload,),
    "fill_and_click": (normalize_fill_and_click_payload,),
    "click_and_wait": (normalize_click_and_wait_payload,),
    "press_key_chord": (normalize_press_key_chord_payload,),
    "type_text": (
        build_nested_payload_normalizer(
            field_name="target",
            candidate_keys=TARGET_QUERY_KEYS_WITHOUT_TEXT,
            hoist_keys=("page_id",),
        ),
    ),
    "run_action_and_describe": (normalize_run_action_and_describe_payload,),
    "wait_for": (normalize_wait_for_payload,),
    "drag_and_drop": (
        build_nested_payload_normalizer(
            field_name="source_target",
            candidate_keys=TARGET_QUERY_KEYS,
        ),
        build_nested_payload_normalizer(
            field_name="dest_target",
            candidate_keys=TARGET_QUERY_KEYS,
        ),
    ),
}
for _tool_name in TARGET_COMPAT_TOOLS:
    TOOL_PAYLOAD_NORMALIZERS[_tool_name] = (
        build_nested_payload_normalizer(
            field_name="target",
            candidate_keys=WAIT_FOR_TARGET_KEYS,
            hoist_keys=("page_id",),
        ),
    )


def build_compat_arg_model(base_model: type[Any], fn_name: str, param_names: tuple[str, ...]) -> type[Any]:
    class CompatArgModel(base_model):  # type: ignore[misc, valid-type]
        args: list[Any] | None = None
        kwargs: dict[str, Any] | None = None

        model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

        @model_validator(mode="before")
        @classmethod
        def _normalize_payload(cls, payload: Any) -> Any:
            return normalize_mcp_tool_payload(payload, fn_name, param_names)

        def model_dump_one_level(self) -> dict[str, Any]:
            payload = super().model_dump_one_level()
            payload.pop("args", None)
            payload.pop("kwargs", None)
            return payload

    CompatArgModel.__name__ = f"{fn_name}CompatArguments"
    CompatArgModel.model_rebuild(force=True)
    return CompatArgModel


async def _compat_server_session_received_request(
    self: ServerSession, responder: Any
) -> None:
    request = responder.request.root
    match request:
        case mcp_types.InitializeRequest(params=params):
            requested_version = params.protocolVersion
            self._initialization_state = InitializationState.Initializing
            self._client_params = params
            with responder:
                await responder.respond(
                    mcp_types.ServerResult(
                        mcp_types.InitializeResult(
                            protocolVersion=requested_version,
                            capabilities=self._init_options.capabilities,
                            serverInfo=mcp_types.Implementation(
                                name=self._init_options.server_name,
                                version=self._init_options.server_version,
                                websiteUrl=self._init_options.website_url,
                                icons=self._init_options.icons,
                            ),
                            instructions=self._init_options.instructions,
                        )
                    )
                )
            self._initialization_state = InitializationState.Initialized
        case mcp_types.PingRequest():
            return
        case _:
            if self._initialization_state != InitializationState.Initialized:
                LOGGER.warning(
                    "compat_auto_initialize request_type=%s",
                    type(request).__name__,
                )
                self._initialization_state = InitializationState.Initialized
                if getattr(self, "_init_options", None) is None:
                    self._init_options = InitializationOptions(
                        server_name="browser-puppet",
                        server_version="compat",
                        capabilities={},
                    )


def enable_legacy_initialization_compatibility() -> None:
    if ServerSession._received_request is _compat_server_session_received_request:
        return
    ServerSession._received_request = _compat_server_session_received_request


enable_legacy_initialization_compatibility()


class BrowserPuppetApp:
    def __init__(self) -> None:
        self.state = ServerState(artifacts_root=ensure_dir(get_default_artifact_dir()))
        self.max_contexts = DEFAULT_MAX_CONTEXTS
        self.max_pages_per_context = 20
        self.auto_close_stale_contexts = DEFAULT_AUTO_CLOSE_STALE_CONTEXTS
        self.stale_context_timeout_seconds = DEFAULT_STALE_CONTEXT_TIMEOUT_SECONDS
        self.transient_retry_delay_ms = DEFAULT_TRANSIENT_RETRY_DELAY_MS
        self.allow_local_network_by_default = DEFAULT_ALLOW_LOCAL_NETWORK
        self._stale_context_reaper_task: asyncio.Task[None] | None = None
        self._stale_context_cleanup_lock: asyncio.Lock | None = None

    async def start(self) -> None:
        if self.state.playwright is None:
            self.state.playwright = await async_playwright().start()
        if self._stale_context_reaper_task is None or self._stale_context_reaper_task.done():
            self._stale_context_reaper_task = asyncio.create_task(
                self._run_stale_context_reaper(),
                name="browser-puppet-stale-context-reaper",
            )

    async def stop(self) -> None:
        if self._stale_context_reaper_task is not None:
            self._stale_context_reaper_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._stale_context_reaper_task
            self._stale_context_reaper_task = None
        for context_id in list(self.state.contexts):
            await self.close_context(context_id)
        if self.state.playwright is not None:
            await self.state.playwright.stop()
            self.state.playwright = None

    @staticmethod
    def _browser_pool_key(browser_name: str, headless: bool, treat_insecure_origins_as_secure: tuple[str, ...] = ()) -> tuple[str, bool, tuple[str, ...]]:
        return browser_name, headless, treat_insecure_origins_as_secure

    def _normalize_insecure_origins_as_secure(self, origins: Any) -> tuple[str, ...]:
        if origins is None:
            return ()
        if isinstance(origins, str):
            raw_items = [origins]
        elif isinstance(origins, (list, tuple, set)):
            raw_items = list(origins)
        else:
            raise SemanticError(
                "invalid_origin_list",
                "treat_insecure_origins_as_secure must be a string or list of origin strings.",
                target={"value": safe_json(origins)},
            )
        normalized: list[str] = []
        for item in raw_items:
            if not isinstance(item, str):
                raise SemanticError(
                    "invalid_origin",
                    "Each insecure origin must be a string.",
                    target={"value": safe_json(item)},
                )
            value = item.strip()
            if not value:
                continue
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise SemanticError(
                    "invalid_origin",
                    f"Invalid origin '{item}'. Expected http(s)://host[:port].",
                    target={"origin": item},
                )
            try:
                port = parsed.port
            except ValueError as exc:
                raise SemanticError(
                    "invalid_origin",
                    f"Invalid origin '{item}': {exc}.",
                    target={"origin": item},
                ) from exc
            host = parsed.hostname
            assert host is not None
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            default_port = 443 if parsed.scheme == "https" else 80
            netloc = f"{host}:{port}" if port and port != default_port else host
            normalized.append(urlunparse((parsed.scheme, netloc, "", "", "", "")))
        return tuple(sorted(set(normalized)))

    async def ensure_browser(
        self,
        browser_name: str,
        *,
        headless: bool = False,
        treat_insecure_origins_as_secure: tuple[str, ...] = (),
    ) -> Browser:
        await self.start()
        pool_key = self._browser_pool_key(browser_name, headless, treat_insecure_origins_as_secure)
        browser = self.state.browser_pool.get(pool_key)
        if browser is not None:
            return browser
        assert self.state.playwright is not None
        browser_type = getattr(self.state.playwright, browser_name)
        launch_kwargs: dict[str, Any] = {"headless": headless}
        if browser_name == "chromium":
            launch_kwargs["args"] = [
                "--disable-blink-features=AutomationControlled",
            ]
            if treat_insecure_origins_as_secure:
                launch_kwargs["args"].append(
                    "--unsafely-treat-insecure-origin-as-secure=" + ",".join(treat_insecure_origins_as_secure)
                )
        browser = await browser_type.launch(**launch_kwargs)
        self.state.browser_pool[pool_key] = browser
        return browser

    def _get_context_record(self, context_id: str) -> ContextState:
        context = self.state.contexts.get(context_id)
        if context is None:
            raise SemanticError(
                "context_not_found",
                f"Unknown context_id '{context_id}'.",
                target={"context_id": context_id},
                next_steps=["create_context"],
            )
        return context

    def _touch_context(self, context: ContextState) -> None:
        context.last_used_at = utc_ts()

    def _context_is_persistent(self, context: ContextState) -> bool:
        return bool(context.config.get("persistent_context", False))

    def get_context(self, context_id: str) -> ContextState:
        context = self._get_context_record(context_id)
        self._touch_context(context)
        return context

    def get_page_state(self, page_id: str) -> PageState:
        for context in self.state.contexts.values():
            page_state = context.pages.get(page_id)
            if page_state is not None:
                self._touch_context(context)
                return page_state
        raise SemanticError(
            "page_not_found",
            f"Unknown page_id '{page_id}'.",
            target={"page_id": page_id},
            next_steps=["list_pages", "open_page"],
        )

    async def register_page(self, context: ContextState, page: Page) -> PageState:
        await self._close_stale_contexts(exclude_context_ids={context.context_id})
        self._check_page_limit(context)
        page_id = new_id("page")
        page_state = PageState(page_id=page_id, context_id=context.context_id, playwright_page=page)
        context.pages[page_id] = page_state
        context.active_page_id = page_id
        self.state.current_page_id = page_id
        self._bind_page_listeners(page_state)
        await self._install_notification_capture(page_state)
        await self._install_storage_seed_script(page_state)
        await self._apply_page_runtime_overrides(page_state)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
        except Exception:
            pass
        await self.capture_page_meta(page_state)
        return page_state

    async def _install_notification_capture(self, page_state: PageState) -> None:
        page = page_state.playwright_page
        try:
            await page.add_init_script(
                """
                (() => {
                  if (window.__bp_n_i) return;
                  window.__bp_n_i = true;
                  window.__bp_n = [];
                  const NativeNotification = window.Notification;
                  if (!NativeNotification) return;
                  window.Notification = new Proxy(NativeNotification, {
                    construct(target, args) {
                      const [title, options] = args;
                      window.__bp_n.push({
                        title,
                        body: options && options.body ? options.body : "",
                        tag: options && options.tag ? options.tag : null,
                        icon: options && options.icon ? options.icon : null,
                        timestamp: Date.now(),
                      });
                      return new target(...args);
                    },
                    get(target, prop, receiver) {
                      return Reflect.get(target, prop, receiver);
                    },
                  });
                })();
                """
            )
        except Exception:
            pass

    def _stale_context_cleanup_lock_instance(self) -> asyncio.Lock:
        if self._stale_context_cleanup_lock is None:
            self._stale_context_cleanup_lock = asyncio.Lock()
        return self._stale_context_cleanup_lock

    def _stale_context_reaper_interval_seconds(self) -> float:
        timeout = max(1, self.stale_context_timeout_seconds)
        return min(60.0, max(5.0, timeout / 6))

    def _stale_context_candidates(
        self,
        *,
        now: float | None = None,
        exclude_context_ids: set[str] | None = None,
        respect_auto_close: bool = True,
    ) -> list[dict[str, Any]]:
        if respect_auto_close and not self.auto_close_stale_contexts:
            return []
        now = utc_ts() if now is None else now
        exclude = exclude_context_ids or set()
        timeout = max(1, self.stale_context_timeout_seconds)
        candidates = []
        for context in self.state.contexts.values():
            if context.context_id in exclude:
                continue
            if self._context_is_persistent(context):
                continue
            idle_seconds = now - context.last_used_at
            if idle_seconds <= timeout:
                continue
            candidates.append(
                {
                    "context_id": context.context_id,
                    "idle_seconds": round(idle_seconds, 2),
                }
            )
        return candidates

    async def _run_stale_context_reaper(self) -> None:
        while True:
            await asyncio.sleep(self._stale_context_reaper_interval_seconds())
            try:
                closed = await self._close_stale_contexts()
                if closed:
                    LOGGER.info("stale_context_reaper_closed count=%s", len(closed))
            except Exception:
                LOGGER.exception("stale_context_reaper_cycle_failed")

    async def _close_context_record(self, context: ContextState, *, reason: str) -> dict[str, Any]:
        closed_page_ids = list(context.pages)
        for page_state in list(context.pages.values()):
            await self._collect_page_video_artifact(context, page_state)
        with suppress(Exception):
            await context.playwright_context.close()
        context.pages.clear()
        context.active_page_id = None
        self.state.contexts.pop(context.context_id, None)
        if self.state.current_page_id in closed_page_ids:
            self.state.current_page_id = next(
                (item.active_page_id for item in self.state.contexts.values() if item.active_page_id),
                None,
            )
        return {
            "context_id": context.context_id,
            "closed_page_count": len(closed_page_ids),
            "reason": reason,
        }

    async def _close_stale_contexts(
        self,
        *,
        exclude_context_ids: set[str] | None = None,
        respect_auto_close: bool = True,
    ) -> list[dict[str, Any]]:
        candidates = self._stale_context_candidates(
            exclude_context_ids=exclude_context_ids,
            respect_auto_close=respect_auto_close,
        )
        if not candidates:
            return []
        async with self._stale_context_cleanup_lock_instance():
            candidates = self._stale_context_candidates(
                exclude_context_ids=exclude_context_ids,
                respect_auto_close=respect_auto_close,
            )
            closed = []
            for item in candidates:
                context = self.state.contexts.get(item["context_id"])
                if context is None:
                    continue
                result = await self._close_context_record(context, reason="stale_timeout")
                result["idle_seconds"] = item["idle_seconds"]
                closed.append(result)
                LOGGER.info(
                    "stale_context_closed context_id=%s idle_seconds=%s",
                    item["context_id"],
                    item["idle_seconds"],
                )
            return closed

    def _check_context_limit(self) -> None:
        if len(self.state.contexts) >= self.max_contexts:
            raise SemanticError(
                "resource_limit",
                f"Maximum concurrent contexts exceeded ({self.max_contexts}).",
                target={"limit": self.max_contexts},
            )

    def _check_page_limit(self, context: ContextState) -> None:
        if len(context.pages) >= self.max_pages_per_context:
            raise SemanticError(
                "resource_limit",
                f"Maximum pages per context exceeded ({self.max_pages_per_context}).",
                target={"context_id": context.context_id, "limit": self.max_pages_per_context},
            )

    def _rate_limit(self, tool_name: str, window_seconds: int, max_calls: int) -> None:
        now = utc_ts()
        values = self.state.rate_limits.setdefault(tool_name, [])
        values[:] = [value for value in values if now - value <= window_seconds]
        if len(values) >= max_calls:
            raise SemanticError(
                "rate_limited",
                f"Tool '{tool_name}' exceeded {max_calls} calls per {window_seconds} seconds.",
                target={"tool": tool_name},
                retryable=True,
            )
        values.append(now)

    def _bind_page_listeners(self, page_state: PageState) -> None:
        page = page_state.playwright_page
        context = self.get_context(page_state.context_id)

        def add_page_event(kind: str, payload: dict[str, Any]) -> None:
            page_state.buffers.page_events.append({"kind": kind, "timestamp": utc_ts(), **payload})

        page.on("console", lambda msg: self._record_console_message(page_state, msg))
        page.on("pageerror", lambda exc: self._record_page_error(page_state, exc))
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
            lambda socket: self._record_websocket(page_state, socket),
        )
        page.on(
            "worker",
            lambda worker: self._record_worker(page_state, worker),
        )
        try:
            context.playwright_context.on(
                "serviceworker",
                lambda worker: self._record_service_worker(context, worker),
            )
        except Exception:
            pass

    def _record_request(self, page_state: PageState, request: Any) -> None:
        request_id = new_id("req")
        resolution = getattr(request, "_browser_puppet_resolution", None)
        redirected_from = self._get_redirected_from_request(request)
        record = {
            "request_id": request_id,
            "timestamp": utc_ts(),
            "url": request.url,
            "method": request.method,
            "resource_type": request.resource_type,
            "headers": dict(request.headers),
            "post_data": summarize_text(request.post_data or "", 500),
            "frame_url": request.frame.url if request.frame else None,
            "resolution": safe_json(resolution) if resolution else None,
            "redirect_from_request_id": getattr(redirected_from, "_browser_puppet_request_id", None),
        }
        page_state.buffers.network.append(record)
        page_state.request_map[request_id] = record
        setattr(request, "_browser_puppet_request_id", request_id)

    def _get_redirected_from_request(self, request: Any) -> Any | None:
        redirected_from = getattr(request, "redirected_from", None)
        if callable(redirected_from):
            try:
                return redirected_from()
            except Exception:
                return None
        return redirected_from

    def _queue_page_issue_notice(self, page_state: PageState, notice: dict[str, Any]) -> None:
        key = notice["key"]
        if key in page_state.pending_issue_keys:
            return
        page_state.pending_issue_keys.add(key)
        stored = {k: v for k, v in notice.items() if k != "key"}
        page_state.pending_issue_notices.append(stored)
        for waiter in list(page_state.issue_waiters):
            if not waiter.done():
                waiter.set_result(stored)
        page_state.issue_waiters = [waiter for waiter in page_state.issue_waiters if not waiter.done()]

    def _route_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        route = parsed.path or "/"
        if parsed.query:
            route = f"{route}?{parsed.query}"
        return route or url

    def _record_console_message(self, page_state: PageState, msg: Any) -> None:
        entry = {
            "timestamp": utc_ts(),
            "type": msg.type,
            "text": summarize_text(msg.text, 500),
            "location": safe_json(msg.location),
        }
        page_state.buffers.console.append(entry)
        if msg.type == "error":
            self._queue_page_issue_notice(
                page_state,
                {
                    "key": f"console:{entry['text']}",
                    "kind": "console_error",
                    "summary": "JavaScript console errors were observed on this page.",
                    "message": "JavaScript console errors were observed while waiting on this page. Check console/runtime diagnostics.",
                    "latest_error": entry["text"],
                    "location": entry["location"],
                },
            )

    def _record_page_error(self, page_state: PageState, exc: Exception) -> None:
        entry = {"timestamp": utc_ts(), "message": summarize_text(str(exc), 800)}
        page_state.buffers.errors.append(entry)
        self._queue_page_issue_notice(
            page_state,
            {
                "key": f"pageerror:{entry['message']}",
                "kind": "console_error",
                "summary": "JavaScript console errors were observed on this page.",
                "message": "JavaScript console errors were observed while waiting on this page. Check console/runtime diagnostics.",
                "latest_error": entry["message"],
            },
        )

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
        if response.status >= 400:
            route = self._route_from_url(response.url or request.url)
            summary = f"{request.method} {route}: {response.status}"
            self._queue_page_issue_notice(
                page_state,
                {
                    "key": f"network:{request.method}:{route}:{response.status}",
                    "kind": "network_error",
                    "summary": summary,
                    "message": f"Network request error observed while waiting on this page: {summary}",
                    "method": request.method,
                    "route": route,
                    "status": response.status,
                },
            )
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

    def _consume_pending_issue_notices(self, page_state: PageState) -> list[dict[str, Any]]:
        notices = list(page_state.pending_issue_notices)
        page_state.pending_issue_notices.clear()
        page_state.pending_issue_keys.clear()
        return notices

    def _attach_page_issue_notices(self, page_state: PageState, payload: dict[str, Any]) -> dict[str, Any]:
        notices = self._consume_pending_issue_notices(page_state)
        if notices:
            payload["issue_notices"] = notices
        return payload

    def _resolve_page_state_for_payload(
        self, tool_signature: inspect.Signature, call_args: tuple[Any, ...], call_kwargs: dict[str, Any], payload: dict[str, Any]
    ) -> PageState | None:
        page_id = payload.get("page_id")
        if page_id is None:
            target = payload.get("target")
            if isinstance(target, dict):
                page_id = target.get("page_id")
        if page_id is None:
            try:
                bound = tool_signature.bind_partial(*call_args, **call_kwargs)
            except TypeError:
                bound = None
            if bound is not None:
                page_id = bound.arguments.get("page_id")
        if page_id is None:
            return None
        try:
            return self.get_page_state(page_id)
        except SemanticError:
            return None

    async def _await_with_page_issue_interrupt(self, page_state: PageState, awaitable: Any) -> Any:
        task = asyncio.create_task(awaitable)
        loop = asyncio.get_running_loop()
        issue_future = loop.create_future()
        page_state.issue_waiters.append(issue_future)
        try:
            done, _ = await asyncio.wait({task, issue_future}, return_when=asyncio.FIRST_COMPLETED)
            if issue_future in done and not task.done():
                issue = issue_future.result()
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                raise SemanticError(
                    "page_issue_interrupt",
                    issue.get("message", "A page issue was detected while waiting on this page."),
                    target={"page_id": page_state.page_id, "issue": issue.get("summary")},
                    likely_causes=[issue.get("summary")] if issue.get("summary") else [],
                    next_steps=["get_console_logs", "get_page_errors", "get_network_traffic"],
                )
            return await task
        finally:
            page_state.issue_waiters = [waiter for waiter in page_state.issue_waiters if waiter is not issue_future and not waiter.done()]
            if not issue_future.done():
                issue_future.cancel()

    def _build_redirect_chain(self, page_state: PageState, final_url: str | None = None) -> list[dict[str, Any]]:
        if not page_state.request_map:
            return []
        candidates = [
            entry
            for entry in page_state.request_map.values()
            if entry.get("resource_type") == "document"
            and entry.get("response")
            and (final_url is None or entry.get("response", {}).get("url") == final_url or entry.get("url") == final_url)
        ]
        if not candidates:
            return []
        final_entry = max(candidates, key=lambda item: item.get("timestamp", 0))
        chain = []
        seen = set()
        current = final_entry
        while current and current["request_id"] not in seen:
            seen.add(current["request_id"])
            chain.append(
                {
                    "request_id": current["request_id"],
                    "url": current.get("url"),
                    "status": current.get("response", {}).get("status"),
                    "status_text": current.get("response", {}).get("status_text"),
                }
            )
            previous_id = current.get("redirect_from_request_id")
            current = page_state.request_map.get(previous_id) if previous_id else None
        chain.reverse()
        return chain

    def _url_matches_pattern(self, url: str, pattern: str) -> bool:
        return fnmatch.fnmatch(url, pattern) or pattern in url

    def _match_mock_route(self, context: ContextState, url: str) -> dict[str, Any] | None:
        for entry in context.mocked_routes:
            if self._url_matches_pattern(url, entry["pattern"]):
                return entry
        return None

    async def _handle_context_route(self, context: ContextState, route: Any, request: Any) -> None:
        mock = self._match_mock_route(context, request.url)
        if mock is not None:
            setattr(
                request,
                "_browser_puppet_resolution",
                {
                    "hostname": urlparse(request.url).hostname,
                    "mocked": True,
                    "mock_pattern": mock["pattern"],
                    "blocked": False,
                },
            )
            fulfill_kwargs: dict[str, Any] = {
                "status": mock.get("status", 200),
                "headers": mock.get("headers", {}),
            }
            if mock.get("content_type"):
                fulfill_kwargs["content_type"] = mock["content_type"]
            body = mock.get("body", "")
            if mock.get("body_base64"):
                fulfill_kwargs["body"] = base64.b64decode(mock["body_base64"])
            else:
                fulfill_kwargs["body"] = body
            await route.fulfill(**fulfill_kwargs)
            return
        matched_block = next((pattern for pattern in context.blocked_routes if self._url_matches_pattern(request.url, pattern)), None)
        if matched_block is not None:
            setattr(
                request,
                "_browser_puppet_resolution",
                {
                    "hostname": urlparse(request.url).hostname,
                    "blocked": True,
                    "blocked_reason": f"matched blocked route pattern '{matched_block}'",
                    "blocked_pattern": matched_block,
                },
            )
            await route.abort("blockedbyclient")
            return
        await self._apply_host_override_route(context, route, request)

    async def _apply_host_override_route(self, context: ContextState, route: Any, request: Any) -> None:
        parsed = urlparse(request.url)
        hostname = parsed.hostname
        if not hostname:
            await route.continue_()
            return
        blocked_reason = self._blocked_target_reason(hostname, context)
        if blocked_reason:
            setattr(
                request,
                "_browser_puppet_resolution",
                {
                    "hostname": hostname,
                    "override_hit": False,
                    "effective_ip": None,
                    "blocked": True,
                    "blocked_reason": blocked_reason,
                },
            )
            await route.abort("blockedbyclient")
            return
        mapped_ip = context.host_overrides.get(hostname)
        if not mapped_ip:
            setattr(
                request,
                "_browser_puppet_resolution",
                {
                    "hostname": hostname,
                    "override_hit": False,
                    "effective_ip": None,
                    "blocked": False,
                },
            )
            await route.continue_()
            return
        port = parsed.port
        default_port = 443 if parsed.scheme == "https" else 80
        target_netloc = mapped_ip if port in {None, default_port} else f"{mapped_ip}:{port}"
        rewritten_url = urlunparse(parsed._replace(netloc=target_netloc))
        headers = dict(request.headers)
        headers["host"] = hostname if port is None else f"{hostname}:{port}"
        setattr(
            request,
            "_browser_puppet_resolution",
            {
                "hostname": hostname,
                "override_hit": True,
                "effective_ip": mapped_ip,
                "original_url": request.url,
                "rewritten_url": rewritten_url,
                "blocked": False,
            },
        )
        await route.continue_(url=rewritten_url, headers=headers)

    async def _refresh_context_routes(self, context: ContextState) -> None:
        runtime = context.playwright_context
        if hasattr(runtime, "unroute_all"):
            try:
                await runtime.unroute_all(behavior="ignoreErrors")
            except Exception:
                pass
        if hasattr(runtime, "route"):
            await runtime.route("**/*", lambda route, request: self._handle_context_route(context, route, request))

    def _blocked_target_reason(self, hostname: str, context: ContextState) -> str | None:
        allowlist = set(context.config.get("network_allowlist", []))
        if hostname in allowlist:
            return None
        allow_local_network = bool(context.config.get("allow_local_network", True))
        lowered = hostname.lower()
        if lowered in {"localhost", "::1"}:
            if allow_local_network:
                return None
            return "localhost access is blocked by default policy"
        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            return None
        if ip.is_loopback:
            if allow_local_network:
                return None
            return "loopback access is blocked by default policy"
        if ip.is_private:
            if allow_local_network:
                return None
            return "RFC1918/private network access is blocked by default policy"
        if ip.is_link_local:
            if allow_local_network:
                return None
            return "link-local access is blocked by default policy"
        return None

    async def _ensure_cdp_session(self, page_state: PageState) -> Any:
        context = self.get_context(page_state.context_id)
        if context.browser_name != "chromium":
            raise SemanticError(
                "unsupported_browser",
                "CDP tools are only supported for Chromium contexts.",
                target={"page_id": page_state.page_id, "browser": context.browser_name},
                next_steps=["create_context"],
            )
        if page_state.playwright_page.is_closed():
            raise SemanticError(
                "page_closed",
                f"Page '{page_state.page_id}' is closed.",
                target={"page_id": page_state.page_id},
                retryable=False,
            )
        if page_state.cdp_session is None:
            page_state.cdp_session = await context.playwright_context.new_cdp_session(page_state.playwright_page)
        return page_state.cdp_session

    async def _apply_page_runtime_overrides(self, page_state: PageState) -> None:
        context = self.get_context(page_state.context_id)
        if context.browser_name != "chromium":
            return
        session = await self._ensure_cdp_session(page_state)
        ua = context.config.get("user_agent_override") or context.config.get("user_agent")
        if ua:
            override_params: dict[str, Any] = {"userAgent": ua}
            ua_metadata = context.config.get("user_agent_metadata")
            if ua_metadata:
                override_params["userAgentMetadata"] = ua_metadata
            else:
                chrome_match = re.search(r"Chrome/([\d.]+)", ua)
                if chrome_match:
                    version = chrome_match.group(1)
                    major = version.split(".")[0]
                    platform = "Windows"
                    if "Macintosh" in ua:
                        platform = "macOS"
                    elif "Linux" in ua:
                        platform = "Linux"
                    override_params["userAgentMetadata"] = {
                        "brands": [
                            {"brand": "Chromium", "version": major},
                            {"brand": "Google Chrome", "version": major},
                            {"brand": "Not-A.Brand", "version": "99"},
                        ],
                        "fullVersionList": [
                            {"brand": "Chromium", "version": version},
                            {"brand": "Google Chrome", "version": version},
                            {"brand": "Not-A.Brand", "version": "99.0.0.0"},
                        ],
                        "platform": platform,
                        "platformVersion": "",
                        "architecture": "x86_64" if "x86_64" in ua or "Win64" in ua else "",
                        "model": "",
                        "mobile": False,
                    }
            await session.send("Emulation.setUserAgentOverride", override_params)
        if context.config.get("network_profile"):
            profile = context.config["network_profile"]
            await session.send("Network.enable", {})
            await session.send(
                "Network.emulateNetworkConditions",
                {
                    "offline": bool(profile.get("offline", False)),
                    "latency": int(profile.get("latency_ms", 0)),
                    "downloadThroughput": int(profile.get("download_bps", -1)),
                    "uploadThroughput": int(profile.get("upload_bps", -1)),
                },
            )
        if context.config.get("enable_coverage") and not page_state.coverage_started:
            await self._ensure_coverage_started(page_state)

    async def _ensure_coverage_started(self, page_state: PageState) -> None:
        context = self.get_context(page_state.context_id)
        if context.browser_name != "chromium" or page_state.coverage_started:
            return
        session = await self._ensure_cdp_session(page_state)

        def stylesheet_added(payload: Any) -> None:
            header = payload.get("header", {}) if isinstance(payload, dict) else {}
            stylesheet_id = header.get("styleSheetId")
            if stylesheet_id:
                page_state.coverage_stylesheets[stylesheet_id] = safe_json(header)

        session.on("CSS.styleSheetAdded", stylesheet_added)
        await session.send("DOM.enable", {})
        await session.send("Profiler.enable", {})
        await session.send("Profiler.startPreciseCoverage", {"callCount": False, "detailed": True})
        await session.send("CSS.enable", {})
        await session.send("CSS.startRuleUsageTracking", {})
        page_state.coverage_started = True

    def _coverage_bytes(self, ranges: list[dict[str, Any]]) -> tuple[int, int]:
        used_ranges = sorted(
            [(int(item["startOffset"]), int(item["endOffset"])) for item in ranges if int(item.get("count", 0)) > 0 and int(item["endOffset"]) > int(item["startOffset"])],
            key=lambda item: item[0],
        )
        merged: list[list[int]] = []
        for start, end in used_ranges:
            if not merged or start > merged[-1][1]:
                merged.append([start, end])
            else:
                merged[-1][1] = max(merged[-1][1], end)
        used = sum(end - start for start, end in merged)
        total = max((int(item["endOffset"]) for item in ranges), default=0)
        return used, max(total - used, 0)

    def _get_cdp_subscription(self, subscription_id: str) -> dict[str, Any]:
        for context in self.state.contexts.values():
            for page_state in context.pages.values():
                subscription = page_state.cdp_subscriptions.get(subscription_id)
                if subscription is not None:
                    return subscription
        raise SemanticError(
            "subscription_not_found",
            f"Unknown subscription_id '{subscription_id}'.",
            target={"subscription_id": subscription_id},
            next_steps=["subscribe_cdp_events"],
        )

    async def send_cdp_command(self, page_id: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        session = await self._ensure_cdp_session(page_state)
        result = await session.send(method, params or {})
        return tool_result({"page_id": page_id, "method": method, "result": safe_json(result)})

    async def subscribe_cdp_events(self, page_id: str, events: list[str]) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        session = await self._ensure_cdp_session(page_state)
        subscription_id = new_id("cdp-sub")
        subscription = {
            "subscription_id": subscription_id,
            "page_id": page_id,
            "events": events,
            "buffer": [],
            "listeners": {},
            "created_at": utc_ts(),
        }
        for event_name in events:
            def handler(payload: Any, *, current_event: str = event_name, sub: dict[str, Any] = subscription) -> None:
                sub["buffer"].append(
                    {
                        "timestamp": utc_ts(),
                        "event": current_event,
                        "payload": safe_json(payload),
                    }
                )
                if len(sub["buffer"]) > 500:
                    del sub["buffer"][:-500]

            session.on(event_name, handler)
            subscription["listeners"][event_name] = handler
        page_state.cdp_subscriptions[subscription_id] = subscription
        return tool_result({"subscription_id": subscription_id, "page_id": page_id, "events": events})

    async def get_cdp_events(self, subscription_id: str, limit: int | None = None, cursor: str | None = None) -> dict[str, Any]:
        subscription = self._get_cdp_subscription(subscription_id)
        window = self.apply_cursor(
            subscription["buffer"],
            cursor=cursor,
            limit=limit or self.state.defaults.max_list_length,
        )
        return tool_result(
            {
                "subscription_id": subscription_id,
                "page_id": subscription["page_id"],
                "events": subscription["events"],
                **window,
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

    def _record_websocket(self, page_state: PageState, socket: Any) -> None:
        socket_id = new_id("ws")
        entry = {
            "socket_id": socket_id,
            "page_id": page_state.page_id,
            "url": socket.url,
            "created_at": utc_ts(),
            "status": "open",
            "messages": [],
            "last_error": None,
        }
        page_state.websocket_map[socket_id] = entry
        page_state.buffers.websockets.append(
            {
                "timestamp": entry["created_at"],
                "url": socket.url,
                "socket_id": socket_id,
                "status": "open",
            }
        )

        socket.on("framesent", lambda payload: self._record_websocket_frame(entry, "sent", payload))
        socket.on("framereceived", lambda payload: self._record_websocket_frame(entry, "received", payload))
        socket.on("close", lambda: self._mark_websocket_closed(entry))
        socket.on("socketerror", lambda err: self._mark_websocket_error(entry, err))

    def _record_websocket_frame(self, entry: dict[str, Any], direction: str, payload: Any) -> None:
        raw_payload = getattr(payload, "payload", payload)
        item = {
            "timestamp": utc_ts(),
            "direction": direction,
            "payload": self._summarize_websocket_payload(raw_payload),
        }
        entry["messages"].append(item)
        if len(entry["messages"]) > 500:
            del entry["messages"][:-500]

    def _summarize_websocket_payload(self, payload: Any) -> dict[str, Any]:
        if isinstance(payload, bytes):
            return {"kind": "binary", "size_bytes": len(payload)}
        text = str(payload)
        return {
            "kind": "text",
            "text": summarize_text(text, 500),
            "truncated": len(text) > 500,
        }

    def _mark_websocket_closed(self, entry: dict[str, Any]) -> None:
        entry["status"] = "closed"
        entry["closed_at"] = utc_ts()

    def _mark_websocket_error(self, entry: dict[str, Any], err: Any) -> None:
        entry["last_error"] = summarize_text(str(err), 500)

    def _get_websocket_entry(self, socket_id: str) -> dict[str, Any]:
        for context in self.state.contexts.values():
            for page_state in context.pages.values():
                entry = page_state.websocket_map.get(socket_id)
                if entry is not None:
                    return entry
        raise SemanticError(
            "websocket_not_found",
            f"Unknown socket_id '{socket_id}'.",
            target={"socket_id": socket_id},
            next_steps=["list_websockets"],
        )

    def _record_worker(self, page_state: PageState, worker: Any) -> None:
        worker_id = new_id("worker")
        page_state.worker_map[worker_id] = {
            "worker_id": worker_id,
            "page_id": page_state.page_id,
            "url": getattr(worker, "url", None),
            "created_at": utc_ts(),
            "worker": worker,
            "kind": "web_worker",
        }

    def _record_service_worker(self, context: ContextState, worker: Any) -> None:
        existing = next(
            (
                item
                for item in context.service_worker_map.values()
                if item.get("url") == getattr(worker, "url", None)
            ),
            None,
        )
        if existing is not None:
            existing["worker"] = worker
            return
        worker_id = new_id("service-worker")
        context.service_worker_map[worker_id] = {
            "worker_id": worker_id,
            "url": getattr(worker, "url", None),
            "scope": getattr(worker, "url", None),
            "created_at": utc_ts(),
            "worker": worker,
            "kind": "service_worker",
        }

    def _get_worker_entry(self, worker_id: str) -> dict[str, Any]:
        for context in self.state.contexts.values():
            entry = context.service_worker_map.get(worker_id)
            if entry is not None:
                return entry
            for page_state in context.pages.values():
                item = page_state.worker_map.get(worker_id)
                if item is not None:
                    return item
        raise SemanticError(
            "worker_not_found",
            f"Unknown worker_id '{worker_id}'.",
            target={"worker_id": worker_id},
            next_steps=["list_web_workers", "list_service_workers"],
        )

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

    def _resolve_context_artifact_path(self, context: ContextState, relative_path: str, *, must_exist: bool = False) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise SemanticError(
                "invalid_artifact_path",
                "Artifact transfer paths must be relative to the context artifact directory.",
                target={"context_id": context.context_id, "relative_path": relative_path},
            )
        resolved = (context.artifact_dir / candidate).resolve()
        try:
            resolved.relative_to(context.artifact_dir.resolve())
        except ValueError as exc:
            raise SemanticError(
                "invalid_artifact_path",
                "Artifact transfer path escapes the context artifact directory.",
                target={"context_id": context.context_id, "relative_path": relative_path},
            ) from exc
        if must_exist and not resolved.exists():
            raise SemanticError(
                "artifact_not_found",
                f"Artifact path '{relative_path}' does not exist in the context artifact directory.",
                target={"context_id": context.context_id, "relative_path": relative_path},
            )
        return resolved

    @staticmethod
    def _system_timezone() -> str:
        try:
            tz_path = Path("/etc/timezone")
            if tz_path.exists():
                return tz_path.read_text().strip()
            tz_link = Path("/etc/localtime")
            if tz_link.is_symlink():
                target = str(tz_link.resolve())
                marker = "/zoneinfo/"
                idx = target.find(marker)
                if idx != -1:
                    return target[idx + len(marker):]
        except Exception:
            pass
        return "America/New_York"

    @staticmethod
    def _random_desktop_viewport() -> dict[str, Any]:
        common_viewports = [
            {"width": 1366, "height": 768},
            {"width": 1920, "height": 1080},
            {"width": 1536, "height": 864},
            {"width": 1440, "height": 900},
            {"width": 1280, "height": 720},
        ]
        vp = random.choice(common_viewports)
        chrome_offsets = [0, -1, 1]
        return {
            "width": vp["width"] + random.choice(chrome_offsets),
            "height": vp["height"] - random.randint(70, 90),
        }

    def _browser_profile_presets(self, browser_name: str) -> dict[str, dict[str, Any]]:
        viewport = self._random_desktop_viewport()
        screen_size = {"width": viewport["width"], "height": viewport["height"] + random.randint(70, 90)}
        tz = self._system_timezone()
        presets: dict[str, dict[str, Any]] = {
            "chromium_desktop": {
                "locale": "en-US",
                "timezone": tz,
                "viewport": viewport,
                "screen": screen_size,
                "device_scale_factor": 1,
                "mobile": False,
                "touch": False,
                "color_scheme": "light",
                "reduced_motion": "no-preference",
                "headers": {
                    "Accept-Language": "en-US,en;q=0.9",
                    "Upgrade-Insecure-Requests": "1",
                },
            },
            "firefox_desktop": {
                "locale": "en-US",
                "timezone": tz,
                "viewport": viewport,
                "screen": screen_size,
                "device_scale_factor": 1,
                "mobile": False,
                "touch": False,
                "color_scheme": "light",
                "reduced_motion": "no-preference",
                "headers": {
                    "Accept-Language": "en-US,en;q=0.5",
                    "Upgrade-Insecure-Requests": "1",
                },
            },
        }
        browser_specific = {
            "chromium": ["chromium_desktop"],
            "firefox": ["firefox_desktop"],
            "webkit": ["chromium_desktop"],
        }
        allowed = browser_specific.get(browser_name, [])
        return {key: presets[key] for key in allowed}

    def _default_profile_preset(self, browser_name: str) -> str | None:
        if browser_name == "chromium":
            return "chromium_desktop"
        return None

    def _merge_profile_preset(self, browser: str, profile: dict[str, Any]) -> dict[str, Any]:
        merged = dict(profile)
        preset_name = merged.get("preset") or self._default_profile_preset(browser)
        if not preset_name:
            return merged
        presets = self._browser_profile_presets(browser)
        preset = presets.get(preset_name)
        if preset is None:
            raise SemanticError(
                "invalid_profile_preset",
                f"Unknown profile preset '{preset_name}' for browser '{browser}'.",
                target={"browser": browser, "preset": preset_name, "available_presets": sorted(presets)},
            )
        merged_profile = safe_json(preset)
        merged_profile["preset"] = preset_name
        for key, value in merged.items():
            if key == "headers" and isinstance(value, dict):
                merged_headers = dict(merged_profile.get("headers", {}))
                merged_headers.update(value)
                merged_profile["headers"] = merged_headers
            else:
                merged_profile[key] = value
        return merged_profile

    def _load_profile_payload(self, value: str | dict[str, Any], context: ContextState | None = None) -> dict[str, Any]:
        if isinstance(value, dict):
            return safe_json(value)
        candidate = Path(value)
        if candidate.exists():
            return json.loads(candidate.read_text())
        if context is not None:
            resolved = self._resolve_context_artifact_path(context, value, must_exist=True)
            return json.loads(resolved.read_text())
        raise SemanticError(
            "profile_not_found",
            f"Profile payload path '{value}' does not exist.",
            target={"path": value},
        )

    def _normalize_storage_items(self, items: Any) -> dict[str, str]:
        if items is None:
            return {}
        if isinstance(items, dict):
            return {str(key): str(value) for key, value in items.items()}
        if isinstance(items, list):
            normalized: dict[str, str] = {}
            for entry in items:
                if isinstance(entry, dict) and "name" in entry and "value" in entry:
                    normalized[str(entry["name"])] = str(entry["value"])
            return normalized
        raise SemanticError("invalid_storage_seed", "Storage seed items must be an object or name/value array.")

    def _normalize_storage_seed(self, payload: dict[str, Any]) -> dict[str, dict[str, dict[str, str]]]:
        seed: dict[str, dict[str, dict[str, str]]] = {}
        for origin_entry in payload.get("origins", []):
            origin = origin_entry.get("origin")
            if not origin:
                continue
            origin_seed = seed.setdefault(str(origin), {"localStorage": {}, "sessionStorage": {}})
            origin_seed["localStorage"].update(self._normalize_storage_items(origin_entry.get("localStorage")))
            origin_seed["sessionStorage"].update(self._normalize_storage_items(origin_entry.get("sessionStorage")))
        direct_seed = payload.get("storage_seed")
        if isinstance(direct_seed, dict):
            for origin, value in direct_seed.items():
                origin_seed = seed.setdefault(str(origin), {"localStorage": {}, "sessionStorage": {}})
                if isinstance(value, dict):
                    origin_seed["localStorage"].update(self._normalize_storage_items(value.get("localStorage")))
                    origin_seed["sessionStorage"].update(self._normalize_storage_items(value.get("sessionStorage")))
        return seed

    def _merge_storage_seed(
        self,
        existing: dict[str, dict[str, dict[str, str]]],
        incoming: dict[str, dict[str, dict[str, str]]],
    ) -> dict[str, dict[str, dict[str, str]]]:
        merged = safe_json(existing)
        for origin, payload in incoming.items():
            current = merged.setdefault(origin, {"localStorage": {}, "sessionStorage": {}})
            current["localStorage"].update(payload.get("localStorage", {}))
            current["sessionStorage"].update(payload.get("sessionStorage", {}))
        return merged

    async def _install_storage_seed_script(self, page_state: PageState) -> None:
        context = self.get_context(page_state.context_id)
        if not context.storage_seed:
            return
        await page_state.playwright_page.add_init_script(
            """
            (seed) => {
              const entry = seed[window.location.origin];
              if (!entry) return;
              for (const [key, value] of Object.entries(entry.localStorage || {})) {
                window.localStorage.setItem(key, value);
              }
              for (const [key, value] of Object.entries(entry.sessionStorage || {})) {
                window.sessionStorage.setItem(key, value);
              }
            }
            """,
            safe_json(context.storage_seed),
        )

    async def _apply_storage_seed_to_page(self, page_state: PageState) -> None:
        context = self.get_context(page_state.context_id)
        origin = origin_from_url(page_state.playwright_page.url)
        if not origin or origin == "null":
            return
        payload = context.storage_seed.get(origin)
        if not payload:
            return
        await page_state.playwright_page.evaluate(
            """
            (entry) => {
              for (const [key, value] of Object.entries(entry.localStorage || {})) {
                window.localStorage.setItem(key, value);
              }
              for (const [key, value] of Object.entries(entry.sessionStorage || {})) {
                window.sessionStorage.setItem(key, value);
              }
              return {
                localStorage: Object.keys(entry.localStorage || {}).length,
                sessionStorage: Object.keys(entry.sessionStorage || {}).length,
              };
            }
            """,
            safe_json(payload),
        )

    async def _apply_browser_profile_payload(self, context: ContextState, payload: dict[str, Any]) -> dict[str, Any]:
        cookies = payload.get("cookies", [])
        if cookies:
            await context.playwright_context.add_cookies(cookies)
        incoming_seed = self._normalize_storage_seed(payload)
        if incoming_seed:
            context.storage_seed = self._merge_storage_seed(context.storage_seed, incoming_seed)
        applied_pages = 0
        for page_state in context.pages.values():
            if page_state.playwright_page.is_closed():
                continue
            if origin_from_url(page_state.playwright_page.url) in context.storage_seed:
                await self._apply_storage_seed_to_page(page_state)
                applied_pages += 1
        return {
            "cookies_added": len(cookies),
            "origins_seeded": len(incoming_seed),
            "applied_pages": applied_pages,
        }

    async def _collect_browser_profile_payload(
        self,
        context: ContextState,
        include_session_storage: bool = True,
    ) -> dict[str, Any]:
        payload = await context.playwright_context.storage_state()
        merged_seed = self._merge_storage_seed({}, self._normalize_storage_seed(payload))
        for page_state in context.pages.values():
            if page_state.playwright_page.is_closed():
                continue
            origin = origin_from_url(page_state.playwright_page.url)
            if not origin or origin == "null":
                continue
            storage = await page_state.playwright_page.evaluate(
                """
                () => ({
                  localStorage: Object.fromEntries(Object.entries(window.localStorage)),
                  sessionStorage: Object.fromEntries(Object.entries(window.sessionStorage)),
                })
                """
            )
            merged_seed = self._merge_storage_seed(
                merged_seed,
                {
                    origin: {
                        "localStorage": self._normalize_storage_items(storage.get("localStorage")),
                        "sessionStorage": (
                            self._normalize_storage_items(storage.get("sessionStorage")) if include_session_storage else {}
                        ),
                    }
                },
            )
        merged_seed = self._merge_storage_seed(merged_seed, context.storage_seed)
        origins = []
        for origin, entry in sorted(merged_seed.items()):
            origins.append(
                {
                    "origin": origin,
                    "localStorage": [{"name": key, "value": value} for key, value in sorted(entry.get("localStorage", {}).items())],
                    "sessionStorage": [{"name": key, "value": value} for key, value in sorted(entry.get("sessionStorage", {}).items())],
                }
            )
        payload["origins"] = origins
        return safe_json(payload)

    async def create_context(self, browser: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
        profile = dict(self._merge_profile_preset(browser, profile or {}))
        profile.setdefault("allow_local_network", self.allow_local_network_by_default)
        profile["persistent_context"] = bool(profile.get("persistent_context", False))
        insecure_origins = self._normalize_insecure_origins_as_secure(profile.get("treat_insecure_origins_as_secure"))
        if insecure_origins:
            if browser != "chromium":
                raise SemanticError(
                    "unsupported_browser",
                    "treat_insecure_origins_as_secure is only supported for Chromium contexts.",
                    target={"browser": browser},
                )
            profile["treat_insecure_origins_as_secure"] = list(insecure_origins)
        await self._close_stale_contexts()
        self._check_context_limit()
        ca_bundle = profile.get("ca_bundle_path")
        if ca_bundle is not None and not Path(ca_bundle).exists():
            raise SemanticError(
                "ca_bundle_not_found",
                f"CA bundle path '{ca_bundle}' does not exist.",
                target={"ca_bundle_path": ca_bundle},
            )
        browser_instance = await self.ensure_browser(
            browser,
            headless=bool(profile.get("headless", False)),
            treat_insecure_origins_as_secure=insecure_origins,
        )
        context_id = new_id("context")
        artifact_dir = ensure_dir(self.state.artifacts_root / context_id)
        context_kwargs = self._build_context_kwargs(artifact_dir, profile, browser)
        effective_profile = safe_json(profile)
        effective_profile.update(safe_json(context_kwargs))
        playwright_context = await browser_instance.new_context(**context_kwargs)
        await playwright_context.add_init_script(
            """
            (() => {
              Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
              });
            })();
            """
        )
        context_state = ContextState(
            context_id=context_id,
            browser_name=browser,
            browser=browser_instance,
            playwright_context=playwright_context,
            artifact_dir=artifact_dir,
            har_path=context_kwargs.get("record_har_path"),
            video_recording_enabled=bool(context_kwargs.get("record_video_dir")),
            config=effective_profile,
        )
        if ca_bundle is not None:
            context_state.config["ca_bundle_path"] = str(Path(ca_bundle).resolve())
            context_state.config["ca_bundle_supported"] = False
        self.state.contexts[context_id] = context_state
        await self._refresh_context_routes(context_state)
        playwright_context.on(
            "page",
            lambda page: asyncio.create_task(self.register_page(context_state, page)),
        )
        profile_summary = None
        if profile.get("profile_state") is not None:
            profile_payload = self._load_profile_payload(profile["profile_state"])
            profile_summary = await self._apply_browser_profile_payload(context_state, profile_payload)
        return tool_result(
            {
                "context_id": context_id,
                "effective_config": safe_json(context_state.config),
                "profile_summary": profile_summary,
            }
        )

    def _default_user_agent(self, browser_name: str) -> str:
        version = "134.0.0.0"
        for pool_key, browser in self.state.browser_pool.items():
            if isinstance(pool_key, tuple):
                pool_browser_name = pool_key[0]
            else:
                pool_browser_name = pool_key
            if pool_browser_name != browser_name:
                continue
            try:
                raw = browser.version
                if raw:
                    version = raw
                    break
            except Exception:
                pass
        platform = "Windows NT 10.0; Win64; x64"
        if sys.platform == "darwin":
            platform = "Macintosh; Intel Mac OS X 10_15_7"
        elif sys.platform.startswith("linux"):
            platform = "X11; Linux x86_64"
        return f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36"

    def _build_context_kwargs(self, artifact_dir: Path, profile: dict[str, Any], browser_name: str = "chromium") -> dict[str, Any]:
        viewport = profile.get("viewport")
        if profile.get("mobile") and viewport is None:
            viewport = {"width": 390, "height": 844}
        tz = self._system_timezone()
        kwargs: dict[str, Any] = {
            "viewport": viewport or {"width": 1280, "height": 800},
            "screen": profile.get("screen"),
            "locale": profile.get("locale", "en-US"),
            "timezone_id": profile.get("timezone", tz),
            "user_agent": profile.get("user_agent", self._default_user_agent(browser_name)),
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
            "java_script_enabled": profile.get("java_script_enabled"),
            "accept_downloads": True,
            "record_har_path": str((artifact_dir / "session.har").resolve()) if profile.get("capture_har") else None,
            "record_video_dir": str((artifact_dir / "videos").resolve()) if profile.get("record_video") else None,
        }
        return {key: value for key, value in kwargs.items() if value is not None}

    def _resolve_credential_aliases(self, context: ContextState, text: str) -> str:
        pattern = re.compile(r"\{\{cred:([a-zA-Z0-9_.-]+)\}\}")

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in context.credentials:
                raise SemanticError(
                    "credential_not_found",
                    f"Unknown credential alias '{key}'.",
                    target={"context_id": context.context_id, "credential_alias": key},
                    next_steps=["store_credential"],
                )
            return context.credentials[key]

        return pattern.sub(replace, text)

    def _resolve_input_text(self, page_state: PageState, text: str) -> str:
        context = self.get_context(page_state.context_id)
        return self._resolve_credential_aliases(context, text)

    async def store_credential(self, context_id: str, alias: str, value: str) -> dict[str, Any]:
        context = self.get_context(context_id)
        context.credentials[alias] = value
        return tool_result({"success": True, "context_id": context_id, "alias": alias})

    async def delete_credential(self, context_id: str, alias: str) -> dict[str, Any]:
        context = self.get_context(context_id)
        existed = alias in context.credentials
        context.credentials.pop(alias, None)
        return tool_result({"success": existed, "context_id": context_id, "alias": alias})

    async def list_credentials(self, context_id: str) -> dict[str, Any]:
        context = self.get_context(context_id)
        return tool_result({"aliases": sorted(context.credentials.keys())})

    async def open_page(self, context_id: str, url: str, wait_until: str = "load", timeout_ms: int = DEFAULT_NAVIGATION_TIMEOUT_MS) -> dict[str, Any]:
        context = self.get_context(context_id)
        self._check_page_limit(context)
        page = await context.playwright_context.new_page()
        page_state = await self.register_page(context, page)

        async def run_navigation() -> dict[str, Any]:
            response = await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            redirect_chain = self._build_redirect_chain(page_state, page.url)
            await self.capture_page_meta(page_state)
            return tool_result(
                {
                    "page_id": page_state.page_id,
                    "url": page.url,
                    "status": response.status if response else None,
                    "timeout_ms": timeout_ms,
                    "redirect_chain": redirect_chain,
                    "digest": await self.get_page_digest(page_state.page_id, mode=self.state.defaults.mode),
                }
            )

        return await self._await_with_page_issue_interrupt(page_state, run_navigation())

    async def navigate(
        self,
        page_id: str,
        url: str,
        wait_until: str = "load",
        timeout_ms: int = DEFAULT_NAVIGATION_TIMEOUT_MS,
        observe: str = "off",
    ) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        before = await self.before_mutation(page_state, observe)

        async def run_navigation() -> dict[str, Any]:
            response = await page_state.playwright_page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            return await self.action_outcome(
                page_state,
                "navigate",
                before,
                extra={"status": response.status if response else None, "timeout_ms": timeout_ms},
            )

        return await self._await_with_page_issue_interrupt(page_state, run_navigation())

    async def reload_page(self, page_id: str, ignore_cache: bool = False) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        before = await self.get_lightweight_checkpoint(page_state)

        async def run_reload() -> dict[str, Any]:
            response = await page_state.playwright_page.reload(wait_until="load")
            return await self.action_outcome(
                page_state,
                "reload_page",
                before,
                extra={"status": response.status if response else None, "ignore_cache": ignore_cache},
            )

        return await self._await_with_page_issue_interrupt(page_state, run_reload())

    async def go_back(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        before = await self.get_lightweight_checkpoint(page_state)

        async def run_go_back() -> dict[str, Any]:
            response = await page_state.playwright_page.go_back()
            return await self.action_outcome(page_state, "go_back", before, extra={"status": response.status if response else None})

        return await self._await_with_page_issue_interrupt(page_state, run_go_back())

    async def go_forward(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        before = await self.get_lightweight_checkpoint(page_state)

        async def run_go_forward() -> dict[str, Any]:
            response = await page_state.playwright_page.go_forward()
            return await self.action_outcome(page_state, "go_forward", before, extra={"status": response.status if response else None})

        return await self._await_with_page_issue_interrupt(page_state, run_go_forward())

    async def list_pages(self, context_id: str, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
        context = self.get_context(context_id)
        pages = []
        for page_id, page_state in context.pages.items():
            pages.append(await self._page_summary(context, page_state))
        return tool_result({"pages": pages, **self.apply_cursor(pages, cursor=cursor, limit=limit or self.state.defaults.max_list_length)})

    async def switch_page(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        context = self.get_context(page_state.context_id)
        context.active_page_id = page_id
        self.state.current_page_id = page_id
        return tool_result(await self._page_summary(context, page_state))

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
        await self._collect_page_video_artifact(context, page_state)
        await page_state.playwright_page.close()
        context.pages.pop(page_id, None)
        if context.active_page_id == page_id:
            context.active_page_id = next(iter(context.pages), None)
        if self.state.current_page_id == page_id:
            self.state.current_page_id = context.active_page_id
        return tool_result({"success": True})

    async def close_context(self, context_id: str) -> dict[str, Any]:
        context = self._get_context_record(context_id)
        result = await self._close_context_record(context, reason="manual")
        return tool_result({"success": True, **result})

    async def close_stale_contexts(self) -> dict[str, Any]:
        now = utc_ts()
        stale_persistent_contexts = [
            {
                "context_id": context.context_id,
                "idle_seconds": round(now - context.last_used_at, 2),
            }
            for context in self.state.contexts.values()
            if self._context_is_persistent(context)
            and now - context.last_used_at > max(1, self.stale_context_timeout_seconds)
        ]
        closed = await self._close_stale_contexts(respect_auto_close=False)
        return tool_result(
            {
                "success": True,
                "closed_count": len(closed),
                "closed_contexts": closed,
                "persistent_contexts_skipped": stale_persistent_contexts,
                "timeout_seconds": self.stale_context_timeout_seconds,
                "auto_close_enabled": self.auto_close_stale_contexts,
            }
        )

    async def set_context_persistence(self, context_id: str, persistent: bool) -> dict[str, Any]:
        context = self.get_context(context_id)
        context.config["persistent_context"] = bool(persistent)
        return tool_result(
            {
                "success": True,
                "context_id": context_id,
                "persistent_context": bool(persistent),
            }
        )

    async def _collect_page_video_artifact(self, context: ContextState, page_state: PageState) -> list[dict[str, Any]]:
        artifacts = []
        if not context.video_recording_enabled:
            return artifacts
        video = getattr(page_state.playwright_page, "video", None)
        if video is None:
            return artifacts
        try:
            path = await video.path()
        except Exception:
            return artifacts
        if not path:
            return artifacts
        normalized = str(Path(path).resolve())
        existing = any(item.kind == "video" and item.path == normalized for item in context.artifacts)
        if existing:
            return artifacts
        artifacts.append(self._add_artifact(context, "video", normalized, page_state.page_id, "record_video"))
        return artifacts

    async def save_storage_state(self, context_id: str, path: str | None = None) -> dict[str, Any]:
        context = self.get_context(context_id)
        output = Path(path) if path else context.artifact_dir / "storage-state.json"
        await context.playwright_context.storage_state(path=str(output))
        artifact = self._add_artifact(context, "storage_state", str(output.resolve()), None, "save_storage_state")
        return tool_result({"path": str(output.resolve()), "artifact": artifact})

    async def load_storage_state(self, context_id: str, state: str | dict[str, Any]) -> dict[str, Any]:
        context = self.get_context(context_id)
        payload = self._load_profile_payload(state, context)
        summary = await self._apply_browser_profile_payload(context, payload)
        return tool_result({"success": True, **summary})

    async def export_browser_profile(
        self,
        context_id: str,
        path: str | None = None,
        include_session_storage: bool = True,
    ) -> dict[str, Any]:
        context = self.get_context(context_id)
        payload = await self._collect_browser_profile_payload(context, include_session_storage=include_session_storage)
        output = Path(path) if path else context.artifact_dir / "browser-profile.json"
        ensure_dir(output.parent)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True))
        artifact = self._add_artifact(context, "browser_profile", str(output.resolve()), None, "export_browser_profile")
        return tool_result(
            {
                "path": str(output.resolve()),
                "artifact": artifact,
                "origin_count": len(payload.get("origins", [])),
                "cookie_count": len(payload.get("cookies", [])),
            }
        )

    async def import_browser_profile(self, context_id: str, profile: str | dict[str, Any]) -> dict[str, Any]:
        context = self.get_context(context_id)
        payload = self._load_profile_payload(profile, context)
        summary = await self._apply_browser_profile_payload(context, payload)
        return tool_result({"success": True, **summary})

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
        context = self.get_context(page_state.context_id)
        opener = await self._get_page_opener(page)
        opener_page_id = self._find_opener_page_id(context, opener)
        meta = {
            "page_id": page_state.page_id,
            "url": page.url,
            "title": await page.title(),
            "is_closed": page.is_closed(),
            "viewport": page.viewport_size,
            "origin": origin_from_url(page.url) if page.url else None,
            "opener_page_id": opener_page_id,
            "opener_url": opener.url if opener is not None else None,
            "is_active": context.active_page_id == page_state.page_id,
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
        normalized_query = self._normalize_target_query(query)
        locator = self._locator_from_target(page_state, normalized_query)
        count = await locator.count()
        descriptors = []
        limit = min(count, normalized_query.get("limit", self.state.defaults.max_list_length))
        for index in range(limit):
            item = locator.nth(index)
            descriptors.append(await self.describe_locator(page_state, item, query=normalized_query, nth=index))
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
            nth=nth,
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
        info.update(
            {
                "element_id": element_id,
                "selector": selector,
                "nth": nth,
                "identity": {
                    "selector": selector,
                    "hints": safe_json(query or {}),
                    "frame_id": page_state.frame_focus,
                    "nth": nth,
                },
            }
        )
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
        return self._lookup_element_record(element_id)

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
        if record.shadow_host_element_id and record.shadow_selector:
            host_page_state, host_record = self._lookup_element_record(record.shadow_host_element_id)
            host_locator = self._locator_from_record(host_page_state, host_record)
            return host_locator.locator(f":scope >>> {record.shadow_selector}").nth(record.nth)
        if record.selector:
            return page_state.playwright_page.locator(record.selector)
        return self._locator_from_target(page_state, record.hints)

    def _lookup_element_record(self, element_id: str) -> tuple[PageState, ElementRecord]:
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

    def _normalize_target_query(self, target: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(target, str):
            return {"selector": target}
        return target

    def _locator_from_target(self, page_state: PageState, target: dict[str, Any] | str) -> Locator:
        target = self._normalize_target_query(target)
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
              return Array.from(root.querySelectorAll(selector)).slice(0, 25).map((item, index) => ({
                index,
                tag: item.tagName.toLowerCase(),
                text: (item.innerText || item.textContent || "").trim().slice(0, 160),
                role: item.getAttribute("role"),
                label: item.getAttribute("aria-label"),
                name: item.getAttribute("name"),
              }));
            }
            """,
            selector,
        )
        descriptors = []
        for item in matches:
            element_id = new_id("el")
            shadow_record = ElementRecord(
                element_id=element_id,
                page_id=page_state.page_id,
                frame_id=page_state.frame_focus,
                selector=None,
                hints={"shadow_host_element_id": host_element_id, "shadow_selector": selector},
                created_at=utc_ts(),
                shadow_host_element_id=host_element_id,
                shadow_selector=selector,
                nth=int(item.get("index", 0)),
            )
            page_state.element_cache.set(element_id, shadow_record)
            item["element_id"] = element_id
            item["identity"] = {
                "shadow_host_element_id": host_element_id,
                "shadow_selector": selector,
                "nth": int(item.get("index", 0)),
            }
            descriptors.append(item)
        return tool_result({"matches": descriptors})

    async def get_shadow_root(self, element_id: str) -> dict[str, Any]:
        page_state, record = await self.get_element_record(element_id)
        locator = self._locator_from_record(page_state, record)
        tree = await locator.evaluate(
            """
            (node) => {
              const root = node.shadowRoot;
              if (!root) return null;
              return Array.from(root.children).map((child, index) => ({
                index,
                tag: child.tagName.toLowerCase(),
                text: (child.innerText || child.textContent || "").trim().slice(0, 160),
                role: child.getAttribute("role"),
                label: child.getAttribute("aria-label"),
              }));
            }
            """
        )
        if tree is None:
            return tool_result({"shadow_root": None})
        for item in tree:
            child_element_id = new_id("el")
            child_record = ElementRecord(
                element_id=child_element_id,
                page_id=page_state.page_id,
                frame_id=page_state.frame_focus,
                selector=None,
                hints={"shadow_host_element_id": element_id, "shadow_selector": ":scope > *"},
                created_at=utc_ts(),
                shadow_host_element_id=element_id,
                shadow_selector=":scope > *",
                nth=int(item.get("index", 0)),
            )
            page_state.element_cache.set(child_element_id, child_record)
            item["element_id"] = child_element_id
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

    async def capture_canvas(self, element_id: str, format: str = "png", return_method: str = "disk") -> dict[str, Any]:
        page_state, record = await self.get_element_record(element_id)
        locator = self._locator_from_record(page_state, record)
        context = self.get_context(page_state.context_id)
        data_url = await locator.evaluate(
            """
            (node, imageFormat) => {
              if (!(node instanceof HTMLCanvasElement)) {
                throw new Error("Element is not a canvas");
              }
              const mime = imageFormat === "jpeg" ? "image/jpeg" : "image/png";
              return node.toDataURL(mime);
            }
            """,
            format,
        )
        import base64

        encoded = data_url.split(",", 1)[1]
        image_bytes = base64.b64decode(encoded)
        path = context.artifact_dir / "canvas" / f"{new_id('canvas')}.{ 'jpg' if format == 'jpeg' else 'png'}"
        ensure_dir(path.parent)
        path.write_bytes(image_bytes)
        artifact = self._add_artifact(context, "canvas_capture", str(path.resolve()), page_state.page_id, "capture_canvas")
        if return_method == "base64":
            return tool_result({"base64": encoded, "artifact": artifact})
        return tool_result({"path": artifact["path"], "artifact": artifact})

    async def get_media_state(self, element_id: str) -> dict[str, Any]:
        page_state, record = await self.get_element_record(element_id)
        locator = self._locator_from_record(page_state, record)
        payload = await locator.evaluate(
            """
            (node) => {
              if (!(node instanceof HTMLMediaElement)) {
                throw new Error("Element is not audio/video");
              }
              return {
                currentTime: node.currentTime,
                duration: Number.isFinite(node.duration) ? node.duration : null,
                paused: node.paused,
                ended: node.ended,
                muted: node.muted,
                volume: node.volume,
                playbackRate: node.playbackRate,
                buffered: Array.from({length: node.buffered.length}, (_, index) => ({
                  start: node.buffered.start(index),
                  end: node.buffered.end(index),
                })),
              };
            }
            """
        )
        return tool_result(payload)

    async def control_media(self, element_id: str, action: str, value: float | None = None) -> dict[str, Any]:
        page_state, record = await self.get_element_record(element_id)
        locator = self._locator_from_record(page_state, record)
        await locator.evaluate(
            """
            async (node, input) => {
              const {action, value} = input;
              if (!(node instanceof HTMLMediaElement)) {
                throw new Error("Element is not audio/video");
              }
              if (action === "play") await node.play();
              else if (action === "pause") node.pause();
              else if (action === "seek") node.currentTime = value ?? 0;
              else if (action === "mute") node.muted = true;
              else if (action === "unmute") node.muted = false;
              else throw new Error(`Unsupported media action: ${action}`);
            }
            """,
            {"action": action, "value": value},
        )
        return tool_result({"success": True, "state": await self.get_media_state(element_id)})

    async def mock_media_devices(self, context_id: str, config: dict[str, Any]) -> dict[str, Any]:
        context = self.get_context(context_id)
        context.config["mock_media_devices"] = safe_json(config)
        return tool_result(
            {
                "success": True,
                "note": "Mock media device config is stored and will apply when the context/browser setup is extended to inject fake media streams.",
                "config": context.config["mock_media_devices"],
            }
        )

    async def get_pdf_content(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        page = page_state.playwright_page
        if page.url.lower().endswith(".pdf"):
            return tool_result({"path": page.url, "note": "Direct PDF extraction is not implemented; returning the PDF URL."})
        text = await page.evaluate(
            """
            () => {
              const embed = document.querySelector('embed[type="application/pdf"], iframe[src$=".pdf"], object[type="application/pdf"]');
              if (!embed) return null;
              return {
                src: embed.src || embed.data || null,
                text: document.body.innerText.slice(0, 4000),
              };
            }
            """
        )
        if text:
            return tool_result({"embedded_pdf": text})
        generated = await self.print_to_pdf(page_id)
        return tool_result({"generated_pdf": generated})

    async def print_to_pdf(self, page_id: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        context = self.get_context(page_state.context_id)
        if context.browser_name != "chromium":
            raise SemanticError(
                "unsupported_browser",
                "print_to_pdf is only supported for Chromium contexts.",
                target={"page_id": page_id, "browser": context.browser_name},
            )
        path = context.artifact_dir / "pdf" / f"{new_id('page')}.pdf"
        ensure_dir(path.parent)
        await page_state.playwright_page.pdf(path=str(path), **(options or {}))
        artifact = self._add_artifact(context, "pdf", str(path.resolve()), page_id, "print_to_pdf")
        return tool_result({"path": artifact["path"], "artifact": artifact})

    async def _capture_page_screenshot_bytes(self, page_state: PageState, full_page: bool) -> bytes:
        try:
            return await page_state.playwright_page.screenshot(full_page=full_page)
        except TypeError:
            context = self.get_context(page_state.context_id)
            if context.browser_name != "chromium":
                raise
            session = await self._ensure_cdp_session(page_state)
            result = await session.send(
                "Page.captureScreenshot",
                {
                    "format": "png",
                    "fromSurface": True,
                    "captureBeyondViewport": full_page,
                },
            )
            return base64.b64decode(result["data"])

    async def take_screenshot(
        self, target: str, page_id: str | None = None, element_id: str | None = None, return_method: str = "disk"
    ) -> dict[str, Any]:
        self._rate_limit("take_screenshot", window_seconds=10, max_calls=20)
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
            image = await self._capture_page_screenshot_bytes(page_state, full_page=target == "full_page")
            path.write_bytes(image)
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

    async def record_video(self, context_id: str, action: str) -> dict[str, Any]:
        context = self.get_context(context_id)
        if not context.video_recording_enabled:
            raise SemanticError(
                "capture_not_enabled",
                "Video recording was not enabled for this context.",
                target={"context_id": context_id, "capture": "video"},
                next_steps=["create_context"],
            )
        if action == "start":
            return tool_result(
                {
                    "success": True,
                    "status": "recording",
                    "note": "Video recording is configured at context creation time and is active for pages in this context.",
                }
            )
        if action != "stop":
            raise SemanticError(
                "invalid_action",
                f"Unsupported record_video action '{action}'.",
                target={"context_id": context_id, "action": action},
            )
        artifacts = []
        for page_state in context.pages.values():
            artifacts.extend(await self._collect_page_video_artifact(context, page_state))
        return tool_result({"success": True, "status": "stopped", "artifacts": artifacts})

    async def export_har(self, context_id: str) -> dict[str, Any]:
        context = self.get_context(context_id)
        if not context.har_path:
            raise SemanticError(
                "capture_not_enabled",
                "HAR capture was not enabled for this context.",
                target={"context_id": context_id, "capture": "har"},
                next_steps=["create_context"],
            )
        path = str(Path(context.har_path).resolve())
        artifact = next((item for item in context.artifacts if item.kind == "har" and item.path == path), None)
        if artifact is None:
            self._add_artifact(context, "har", path, context.active_page_id, "export_har")
        return tool_result({"path": path})

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
        typing_mode: str = "auto",
        keystroke_delay_ms: int | None = None,
        keystroke_jitter_ms: int | None = None,
        observe: str = "auto",
    ) -> dict[str, Any]:
        if page_id is None and element_id is None:
            raise SemanticError("missing_target", "type_text requires page_id when element_id is absent.")
        page_state = self.get_page_state(page_id) if page_id is not None else (await self.get_element_record(element_id))[0]
        resolved_text = self._resolve_input_text(page_state, text)
        use_keystrokes = (
            typing_mode == "keystrokes"
            or (
                typing_mode == "auto"
                and (
                    not clear_first
                    or element_id is None and target is None
                    or (keystroke_delay_ms is not None and keystroke_delay_ms > 0)
                    or (keystroke_jitter_ms is not None and keystroke_jitter_ms > 0)
                )
            )
        )
        before = await self.before_mutation(page_state, observe)
        locator = None
        if element_id is not None or target is not None:
            _, locator = await self.resolve_locator(page_id=page_id, element_id=element_id, target=target)
        if use_keystrokes:
            await self._type_text_via_keystrokes(
                page_state,
                resolved_text,
                locator=locator,
                clear_first=clear_first,
                keystroke_delay_ms=keystroke_delay_ms,
                keystroke_jitter_ms=keystroke_jitter_ms,
            )
        else:
            if locator is None:
                raise SemanticError(
                    "missing_target",
                    "type_text with fill mode requires element_id or target. Use typing_mode='keystrokes' to type into the focused element.",
                    target={"page_id": page_state.page_id},
                )
            if clear_first:
                await locator.fill("")
            await locator.fill(resolved_text)
        return await self.action_outcome(page_state, "type_text", before)

    async def _type_text_via_keystrokes(
        self,
        page_state: PageState,
        text: str,
        *,
        locator: Locator | None = None,
        clear_first: bool,
        keystroke_delay_ms: int | None,
        keystroke_jitter_ms: int | None,
    ) -> None:
        page = page_state.playwright_page
        if locator is not None:
            await locator.focus()
            if clear_first:
                try:
                    await locator.fill("")
                except Exception:
                    await page.keyboard.press("Control+A")
                    await page.keyboard.press("Backspace")
        elif clear_first:
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")

        base_delay = random.randint(24, 62) if keystroke_delay_ms is None else max(0, keystroke_delay_ms)
        jitter = random.randint(8, max(8, min(28, base_delay // 2 or 8))) if keystroke_jitter_ms is None else max(0, keystroke_jitter_ms)
        if base_delay == 0 and jitter == 0:
            await page.keyboard.type(text)
            return
        for char in text:
            delay = base_delay
            if jitter:
                delay = max(0, delay + random.randint(-jitter, jitter))
            await page.keyboard.type(char, delay=delay)

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
        result = await self._run_clipboard_operation(page_state, mode="read")
        return tool_result({"text": result["text"]})

    async def clipboard_write(self, page_id: str, text: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        await self._run_clipboard_operation(page_state, mode="write", value=self._resolve_input_text(page_state, text))
        return tool_result({"success": True})

    async def _run_clipboard_operation(self, page_state: PageState, mode: str, value: str | None = None) -> dict[str, Any]:
        context = self.get_context(page_state.context_id)
        permission_name = "clipboard-read" if mode == "read" else "clipboard-write"
        method_name = "readText" if mode == "read" else "writeText"
        result = await page_state.playwright_page.evaluate(
            """
            async ({mode, value, permissionName}) => {
              const details = {
                url: typeof location !== 'undefined' ? location.href : null,
                secure_context: typeof window !== 'undefined' ? !!window.isSecureContext : null,
                has_clipboard: typeof navigator !== 'undefined' && !!navigator.clipboard,
                permission_name: permissionName,
                permission_state: null,
              };
              if (navigator.permissions && navigator.permissions.query) {
                try {
                  const permission = await navigator.permissions.query({name: permissionName});
                  details.permission_state = permission.state;
                } catch (_error) {
                  details.permission_state = 'unsupported';
                }
              }
              const method = mode === 'read' ? 'readText' : 'writeText';
              if (!details.has_clipboard || typeof navigator.clipboard[method] !== 'function') {
                return {ok: false, error_code: 'clipboard_unavailable', ...details};
              }
              try {
                if (mode === 'read') {
                  return {ok: true, text: await navigator.clipboard.readText(), ...details};
                }
                await navigator.clipboard.writeText(value ?? '');
                return {ok: true, success: true, ...details};
              } catch (error) {
                return {
                  ok: false,
                  error_code: error && error.name === 'NotAllowedError' ? 'clipboard_access_denied' : 'clipboard_operation_failed',
                  error_name: error && error.name ? String(error.name) : null,
                  error_message: error && error.message ? String(error.message) : String(error),
                  ...details,
                };
              }
            }
            """,
            {"mode": mode, "value": value, "permissionName": permission_name},
        )
        if result.get("ok"):
            return result
        target = {
            "page_id": page_state.page_id,
            "browser": context.browser_name,
            "url": result.get("url") or getattr(page_state.playwright_page, "url", None),
            "secure_context": result.get("secure_context"),
            "permission_name": result.get("permission_name"),
            "permission_state": result.get("permission_state"),
        }
        clipboard_next_steps = ["set_permission", "open_page"]
        target_url = target.get("url")
        parsed_target_url = urlparse(target_url) if isinstance(target_url, str) else None
        if (
            context.browser_name == "chromium"
            and result.get("secure_context") is False
            and parsed_target_url is not None
            and parsed_target_url.scheme == "http"
        ):
            clipboard_next_steps = ["set_insecure_origins_as_secure", "open_page", "set_permission"]
        if result.get("error_code") == "clipboard_unavailable":
            likely_causes = []
            if result.get("secure_context") is False:
                likely_causes.append("the page is not a secure context, so the Async Clipboard API is unavailable")
                if parsed_target_url is not None and parsed_target_url.scheme == "http" and context.browser_name == "chromium":
                    likely_causes.append(
                        "for local non-TLS testing in Chromium, add this origin to treat_insecure_origins_as_secure when creating or updating the context"
                    )
            likely_causes.append(f"{context.browser_name} did not expose navigator.clipboard.{method_name} on this page")
            if result.get("permission_state") not in {None, "unsupported", "granted"}:
                likely_causes.append(f"{permission_name} permission is currently {result['permission_state']}")
            raise SemanticError(
                "clipboard_unavailable",
                f"Clipboard {mode} is not available on this page because navigator.clipboard.{method_name} is missing.",
                target=target,
                likely_causes=likely_causes,
                next_steps=clipboard_next_steps,
            )
        if result.get("error_code") == "clipboard_access_denied":
            likely_causes = [
                f"{permission_name} access was blocked by the browser for this page",
                "some browsers require a secure context, focused page, or explicit permission grant before clipboard access succeeds",
            ]
            if result.get("permission_state") not in {None, "unsupported"}:
                likely_causes.insert(0, f"{permission_name} permission is currently {result['permission_state']}")
            raise SemanticError(
                "clipboard_access_denied",
                f"Clipboard {mode} was denied by the browser: {result.get('error_message') or 'access denied'}.",
                target=target,
                likely_causes=likely_causes,
                next_steps=clipboard_next_steps,
            )
        raise SemanticError(
            "clipboard_operation_failed",
            (
                f"Clipboard {mode} failed"
                + (f": {result['error_message']}" if result.get("error_message") else ".")
            ),
            target=target,
            likely_causes=["the browser rejected the clipboard operation for this page"],
            next_steps=clipboard_next_steps,
        )

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

    async def submit_form(
        self,
        page_id: str | None = None,
        element_id: str | None = None,
        target: dict[str, Any] | None = None,
        observe: str = "auto",
    ) -> dict[str, Any]:
        page_state, locator = await self.resolve_locator(page_id=page_id, element_id=element_id, target=target)
        before = await self.before_mutation(page_state, observe)
        submitted = await locator.evaluate(
            """
            (node) => {
              const form = node.tagName?.toLowerCase() === 'form' ? node : node.closest('form');
              if (!form) {
                return {submitted: false, reason: 'no_form'};
              }
              if (typeof form.requestSubmit === 'function') {
                form.requestSubmit();
              } else {
                form.submit();
              }
              return {submitted: true};
            }
            """
        )
        if not submitted.get("submitted"):
            raise SemanticError(
                "form_not_found",
                "submit_form target is not a form and is not inside a form.",
                target={"page_id": page_state.page_id, "target": target, "element_id": element_id},
                retryable=True,
                next_steps=["find_elements"],
            )
        return await self.action_outcome(page_state, "submit_form", before)

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
            value = field.get("value")
            resolved_value = self._resolve_input_text(page_state, str(value)) if isinstance(value, str) else value
            if action == "select":
                await locator.select_option(value=resolved_value)
            elif action == "check":
                if resolved_value:
                    await locator.check()
                else:
                    await locator.uncheck()
            else:
                await locator.fill(str(resolved_value))
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

    async def fill_and_click(
        self,
        page_id: str,
        fields: list[dict[str, Any]],
        click_target: dict[str, Any],
        observe: str = "auto",
    ) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        before = await self.before_mutation(page_state, observe)
        results = []
        for field in fields:
            field_target = field["target"]
            action = field.get("action", "fill")
            _, locator = await self.resolve_locator(page_id=page_id, target=field_target)
            value = field.get("value")
            resolved_value = self._resolve_input_text(page_state, str(value)) if isinstance(value, str) else value
            if action == "select":
                await locator.select_option(value=resolved_value)
            elif action == "check":
                if resolved_value:
                    await locator.check()
                else:
                    await locator.uncheck()
            else:
                await locator.fill(str(resolved_value))
            results.append({"target": field_target, "success": True})
        _, click_locator = await self.resolve_locator(page_id=page_id, target=click_target, source_name="click_target")
        await click_locator.click(timeout=DEFAULT_TIMEOUT_MS)
        outcome = await self.action_outcome(page_state, "fill_and_click", before)
        outcome["field_results"] = results
        outcome["click_target"] = click_target
        return outcome

    async def click_and_wait(
        self,
        page_id: str | None = None,
        element_id: str | None = None,
        target: dict[str, Any] | None = None,
        button: str = "left",
        click_count: int = 1,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        wait_for: str = "navigation",
        wait_target: dict[str, Any] | None = None,
        observe: str = "auto",
    ) -> dict[str, Any]:
        page_state, locator = await self.resolve_locator(page_id=page_id, element_id=element_id, target=target)
        before = await self.before_mutation(page_state, observe)
        await locator.click(button=button, click_count=click_count, timeout=timeout_ms)

        async def run_wait() -> dict[str, Any]:
            if wait_for == "navigation":
                await page_state.playwright_page.wait_for_load_state("load", timeout=timeout_ms)
            elif wait_for == "networkidle":
                await page_state.playwright_page.wait_for_load_state("networkidle", timeout=timeout_ms)
            elif wait_for == "url":
                if not wait_target or "pattern" not in wait_target:
                    raise SemanticError(
                        "missing_target",
                        "click_and_wait with wait_for='url' requires wait_target.pattern.",
                        target={"wait_for": wait_for},
                    )
                await page_state.playwright_page.wait_for_url(wait_target["pattern"], timeout=wait_target.get("timeout_ms", timeout_ms))
            elif wait_for in {"element", "hidden"}:
                if not wait_target:
                    raise SemanticError(
                        "missing_target",
                        f"click_and_wait with wait_for='{wait_for}' requires wait_target.",
                        target={"wait_for": wait_for},
                    )
                _, wait_locator = await self.resolve_locator(page_id=page_state.page_id, target=wait_target, source_name="wait_target")
                await wait_locator.wait_for(state="hidden" if wait_for == "hidden" else "visible", timeout=wait_target.get("timeout_ms", timeout_ms))
            else:
                raise SemanticError(
                    "unsupported_wait",
                    f"Unsupported click_and_wait wait_for '{wait_for}'.",
                    target={"wait_for": wait_for},
                )

            outcome = await self.action_outcome(page_state, "click_and_wait", before)
            outcome["wait_for"] = wait_for
            if wait_target:
                outcome["wait_target"] = wait_target
            return outcome

        return await self._await_with_page_issue_interrupt(page_state, run_wait())

    async def wait_for(self, target: dict[str, Any], state: str, page_id: str | None = None, observe: str = "auto") -> dict[str, Any]:
        if state in {"url", "networkidle"}:
            if page_id is None:
                raise SemanticError("missing_target", "wait_for state requires page_id.")
            page_state = self.get_page_state(page_id)
            before = await self.before_mutation(page_state, observe)

            async def run_wait() -> dict[str, Any]:
                if state == "url":
                    await page_state.playwright_page.wait_for_url(target["pattern"], timeout=target.get("timeout_ms", DEFAULT_TIMEOUT_MS))
                else:
                    await page_state.playwright_page.wait_for_load_state("networkidle", timeout=target.get("timeout_ms", DEFAULT_TIMEOUT_MS))
                return await self.action_outcome(page_state, "wait_for", before)

            return await self._await_with_page_issue_interrupt(page_state, run_wait())
        page_state, locator = await self.resolve_locator(page_id=page_id, target=target)
        before = await self.before_mutation(page_state, observe)

        async def run_wait() -> dict[str, Any]:
            await locator.wait_for(state=state, timeout=target.get("timeout_ms", DEFAULT_TIMEOUT_MS))
            return await self.action_outcome(page_state, "wait_for", before)

        return await self._await_with_page_issue_interrupt(page_state, run_wait())

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

    async def set_host_overrides(self, context_id: str, mappings: dict[str, str]) -> dict[str, Any]:
        context = self.get_context(context_id)
        context.host_overrides = dict(mappings)
        await self._refresh_context_routes(context)
        return tool_result({"success": True, "mappings": mappings})

    async def block_routes(self, context_id: str, patterns: list[str]) -> dict[str, Any]:
        context = self.get_context(context_id)
        context.blocked_routes = list(patterns)
        await self._refresh_context_routes(context)
        return tool_result({"success": True, "patterns": context.blocked_routes})

    async def mock_routes(self, context_id: str, routes: list[dict[str, Any]]) -> dict[str, Any]:
        context = self.get_context(context_id)
        normalized = []
        for entry in routes:
            pattern = entry.get("pattern")
            if not pattern:
                raise SemanticError(
                    "invalid_route_mock",
                    "Each mock route requires a pattern.",
                    target={"context_id": context_id, "route": safe_json(entry)},
                )
            normalized.append(
                {
                    "pattern": pattern,
                    "status": int(entry.get("status", 200)),
                    "headers": dict(entry.get("headers", {})),
                    "body": entry.get("body", ""),
                    "body_base64": entry.get("body_base64"),
                    "content_type": entry.get("content_type"),
                }
            )
        context.mocked_routes = normalized
        await self._refresh_context_routes(context)
        return tool_result({"success": True, "routes": safe_json(normalized)})

    async def set_user_agent(self, context_id: str, user_agent: str) -> dict[str, Any]:
        context = self.get_context(context_id)
        if context.browser_name != "chromium":
            raise SemanticError(
                "unsupported_browser",
                "set_user_agent runtime override is only supported for Chromium contexts.",
                target={"context_id": context_id, "browser": context.browser_name},
            )
        context.config["user_agent_override"] = user_agent
        for page_state in context.pages.values():
            await self._apply_page_runtime_overrides(page_state)
        return tool_result(
            {
                "success": True,
                "context_id": context_id,
                "user_agent": user_agent,
                "note": "The override is applied to existing Chromium pages and will be applied to future pages in this context.",
            }
        )

    async def emulate_network(
        self,
        context_id: str,
        profile: dict[str, Any] | None = None,
        preset: str | None = None,
    ) -> dict[str, Any]:
        context = self.get_context(context_id)
        if context.browser_name != "chromium":
            raise SemanticError(
                "unsupported_browser",
                "emulate_network is only supported for Chromium contexts.",
                target={"context_id": context_id, "browser": context.browser_name},
            )
        presets = {
            "offline": {"offline": True, "latency_ms": 0, "download_bps": 0, "upload_bps": 0},
            "slow_3g": {"offline": False, "latency_ms": 400, "download_bps": 50000, "upload_bps": 50000},
            "fast_3g": {"offline": False, "latency_ms": 150, "download_bps": 180000, "upload_bps": 84375},
            "wifi": {"offline": False, "latency_ms": 30, "download_bps": 3750000, "upload_bps": 1875000},
        }
        if preset:
            if preset not in presets:
                raise SemanticError("invalid_preset", f"Unknown emulate_network preset '{preset}'.", target={"preset": preset})
            effective = presets[preset].copy()
        else:
            effective = {"offline": False, "latency_ms": 0, "download_bps": -1, "upload_bps": -1}
        effective.update(profile or {})
        context.config["network_profile"] = effective
        for page_state in context.pages.values():
            await self._apply_page_runtime_overrides(page_state)
        return tool_result({"success": True, "context_id": context_id, "profile": safe_json(effective)})

    async def pinch_zoom(
        self,
        page_id: str,
        scale_factor: float,
        x: int | None = None,
        y: int | None = None,
    ) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        context = self.get_context(page_state.context_id)
        if context.browser_name != "chromium":
            raise SemanticError(
                "unsupported_browser",
                "pinch_zoom is only supported for Chromium contexts.",
                target={"page_id": page_id, "browser": context.browser_name},
            )
        viewport = page_state.playwright_page.viewport_size or {"width": 1280, "height": 800}
        session = await self._ensure_cdp_session(page_state)
        await session.send(
            "Input.synthesizePinchGesture",
            {
                "x": x if x is not None else viewport["width"] // 2,
                "y": y if y is not None else viewport["height"] // 2,
                "scaleFactor": scale_factor,
                "relativeSpeed": 800,
                "gestureSourceType": "touch",
            },
        )
        return tool_result({"success": True, "page_id": page_id, "scale_factor": scale_factor})

    async def get_visual_diff(self, context_id: str, baseline_path: str, candidate_path: str) -> dict[str, Any]:
        context = self.get_context(context_id)
        baseline = Path(baseline_path)
        candidate = Path(candidate_path)
        if not baseline.exists():
            raise SemanticError("artifact_not_found", f"Baseline image '{baseline_path}' does not exist.", target={"path": baseline_path})
        if not candidate.exists():
            raise SemanticError("artifact_not_found", f"Candidate image '{candidate_path}' does not exist.", target={"path": candidate_path})
        created_page = False
        page = next((item.playwright_page for item in context.pages.values() if not item.playwright_page.is_closed()), None)
        if page is None:
            page = await context.playwright_context.new_page()
            created_page = True
        payload = {
            "baseline": f"data:image/png;base64,{base64.b64encode(baseline.read_bytes()).decode('ascii')}",
            "candidate": f"data:image/png;base64,{base64.b64encode(candidate.read_bytes()).decode('ascii')}",
        }
        try:
            result = await page.evaluate(
                """
                async (images) => {
                  function load(src) {
                    return new Promise((resolve, reject) => {
                      const img = new Image();
                      img.onload = () => resolve(img);
                      img.onerror = () => reject(new Error("failed_to_load_image"));
                      img.src = src;
                    });
                  }
                  const [baseline, candidate] = await Promise.all([load(images.baseline), load(images.candidate)]);
                  if (baseline.width !== candidate.width || baseline.height !== candidate.height) {
                    throw new Error("image_dimensions_mismatch");
                  }
                  const width = baseline.width;
                  const height = baseline.height;
                  const a = document.createElement("canvas");
                  const b = document.createElement("canvas");
                  const diff = document.createElement("canvas");
                  a.width = b.width = diff.width = width;
                  a.height = b.height = diff.height = height;
                  const actx = a.getContext("2d");
                  const bctx = b.getContext("2d");
                  const dctx = diff.getContext("2d");
                  actx.drawImage(baseline, 0, 0);
                  bctx.drawImage(candidate, 0, 0);
                  const first = actx.getImageData(0, 0, width, height);
                  const second = bctx.getImageData(0, 0, width, height);
                  const out = dctx.createImageData(width, height);
                  let changedPixels = 0;
                  for (let i = 0; i < first.data.length; i += 4) {
                    const same =
                      first.data[i] === second.data[i] &&
                      first.data[i + 1] === second.data[i + 1] &&
                      first.data[i + 2] === second.data[i + 2] &&
                      first.data[i + 3] === second.data[i + 3];
                    if (same) {
                      out.data[i] = first.data[i];
                      out.data[i + 1] = first.data[i + 1];
                      out.data[i + 2] = first.data[i + 2];
                      out.data[i + 3] = Math.max(30, Math.floor(first.data[i + 3] / 4));
                    } else {
                      changedPixels += 1;
                      out.data[i] = 255;
                      out.data[i + 1] = 0;
                      out.data[i + 2] = 0;
                      out.data[i + 3] = 255;
                    }
                  }
                  dctx.putImageData(out, 0, 0);
                  return {
                    width,
                    height,
                    changed_pixels: changedPixels,
                    total_pixels: width * height,
                    diff_base64: diff.toDataURL("image/png").split(",")[1],
                  };
                }
                """,
                payload,
            )
        except Exception as exc:
            message = str(exc)
            if "image_dimensions_mismatch" in message:
                raise SemanticError(
                    "image_dimensions_mismatch",
                    "Visual diff images must have the same dimensions.",
                    target={"baseline_path": baseline_path, "candidate_path": candidate_path},
                ) from exc
            raise
        finally:
            if created_page:
                await page.close()
        diff_path = context.artifact_dir / "visual-diff" / f"{new_id('visual-diff')}.png"
        ensure_dir(diff_path.parent)
        diff_path.write_bytes(base64.b64decode(result["diff_base64"]))
        artifact = self._add_artifact(context, "visual_diff", str(diff_path.resolve()), context.active_page_id, "get_visual_diff")
        changed = int(result["changed_pixels"])
        total = max(int(result["total_pixels"]), 1)
        return tool_result(
            {
                "artifact": artifact,
                "path": artifact["path"],
                "summary": {
                    "width": int(result["width"]),
                    "height": int(result["height"]),
                    "changed_pixels": changed,
                    "changed_ratio": changed / total,
                },
            }
        )

    async def get_coverage(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        context = self.get_context(page_state.context_id)
        if context.browser_name != "chromium":
            raise SemanticError(
                "unsupported_browser",
                "get_coverage is only supported for Chromium contexts.",
                target={"page_id": page_id, "browser": context.browser_name},
            )
        await self._ensure_coverage_started(page_state)
        session = await self._ensure_cdp_session(page_state)
        js_snapshot = await session.send("Profiler.takePreciseCoverage", {})
        css_usage = await session.send("CSS.stopRuleUsageTracking", {})
        await session.send("CSS.startRuleUsageTracking", {})
        js_entries = []
        js_used = 0
        js_unused = 0
        for item in js_snapshot.get("result", []):
            ranges = []
            for fn in item.get("functions", []):
                ranges.extend(fn.get("ranges", []))
            used, unused = self._coverage_bytes(ranges)
            js_used += used
            js_unused += unused
            js_entries.append(
                {
                    "url": item.get("url") or None,
                    "used_bytes": used,
                    "unused_bytes": unused,
                }
            )
        css_by_sheet: dict[str, list[dict[str, Any]]] = {}
        for item in css_usage.get("ruleUsage", []):
            css_by_sheet.setdefault(item["styleSheetId"], []).append(item)
        css_entries = []
        css_used = 0
        css_unused = 0
        for stylesheet_id, rules in css_by_sheet.items():
            text = await session.send("CSS.getStyleSheetText", {"styleSheetId": stylesheet_id})
            total_bytes = len(text.get("text", ""))
            used_ranges = [
                {
                    "startOffset": item["startOffset"],
                    "endOffset": item["endOffset"],
                    "count": 1 if item.get("used") else 0,
                }
                for item in rules
            ]
            used, unused = self._coverage_bytes(used_ranges)
            used = min(used, total_bytes)
            unused = max(total_bytes - used, 0)
            css_used += used
            css_unused += unused
            css_entries.append(
                {
                    "url": page_state.coverage_stylesheets.get(stylesheet_id, {}).get("sourceURL") or None,
                    "style_sheet_id": stylesheet_id,
                    "used_bytes": used,
                    "unused_bytes": unused,
                }
            )
        return tool_result(
            {
                "page_id": page_id,
                "javascript": {"used_bytes": js_used, "unused_bytes": js_unused, "entries": js_entries},
                "css": {"used_bytes": css_used, "unused_bytes": css_unused, "entries": css_entries},
            }
        )

    async def get_dns_resolution(self, page_id: str, hostname: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        matches = []
        for entry in page_state.buffers.network:
            resolution = entry.get("resolution") or {}
            parsed = urlparse(entry["url"])
            candidate_host = resolution.get("hostname") or parsed.hostname
            if candidate_host == hostname:
                matches.append(
                    {
                        "request_id": entry["request_id"],
                        "url": entry["url"],
                        "override_hit": bool(resolution.get("override_hit")),
                        "effective_ip": resolution.get("effective_ip"),
                        "rewritten_url": resolution.get("rewritten_url"),
                    }
                )
        override_ip = self.get_context(page_state.context_id).host_overrides.get(hostname)
        return tool_result(
            {
                "hostname": hostname,
                "override_hit": override_ip is not None or any(item["override_hit"] for item in matches),
                "effective_ip": override_ip,
                "matches": matches[: self.state.defaults.max_list_length],
                "remaining_count": max(0, len(matches) - self.state.defaults.max_list_length),
            }
        )

    async def list_service_workers(self, context_id: str, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
        context = self.get_context(context_id)
        try:
            workers = getattr(context.playwright_context, "service_workers", None)
            if workers:
                for worker in workers:
                    self._record_service_worker(context, worker)
        except Exception:
            pass
        payload = []
        for entry in context.service_worker_map.values():
            payload.append(
                {
                    "worker_id": entry["worker_id"],
                    "url": entry["url"],
                    "scope": entry["scope"],
                    "kind": entry["kind"],
                    "created_at": entry["created_at"],
                }
            )
        return tool_result({"service_workers": payload, **self.apply_cursor(payload, cursor=cursor, limit=limit or self.state.defaults.max_list_length)})

    async def unregister_service_worker(self, context_id: str, scope: str) -> dict[str, Any]:
        context = self.get_context(context_id)
        target_page = next(iter(context.pages.values()), None)
        if target_page is None:
            raise SemanticError(
                "page_not_found",
                "unregister_service_worker requires at least one page in the context.",
                target={"context_id": context_id},
                next_steps=["open_page"],
            )
        result = await target_page.playwright_page.evaluate(
            """
            async (scope) => {
              const registrations = await navigator.serviceWorker.getRegistrations();
              let removed = 0;
              for (const registration of registrations) {
                if (!scope || registration.scope === scope || registration.scope.startsWith(scope)) {
                  const ok = await registration.unregister();
                  if (ok) removed += 1;
                }
              }
              return {removed};
            }
            """,
            scope,
        )
        for worker_id, entry in list(context.service_worker_map.items()):
            if entry.get("scope") == scope or entry.get("url") == scope:
                context.service_worker_map.pop(worker_id, None)
        return tool_result({"success": result["removed"] > 0, "removed": result["removed"]})

    async def list_web_workers(self, page_id: str, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        payload = []
        for entry in page_state.worker_map.values():
            payload.append(
                {
                    "worker_id": entry["worker_id"],
                    "url": entry["url"],
                    "kind": entry["kind"],
                    "created_at": entry["created_at"],
                }
            )
        return tool_result({"workers": payload, **self.apply_cursor(payload, cursor=cursor, limit=limit or self.state.defaults.max_list_length)})

    async def evaluate_worker(self, worker_id: str, script: str) -> dict[str, Any]:
        entry = self._get_worker_entry(worker_id)
        worker = entry.get("worker")
        if worker is None:
            raise SemanticError(
                "worker_unavailable",
                f"Worker '{worker_id}' is no longer attached.",
                target={"worker_id": worker_id},
                retryable=True,
            )
        result = await worker.evaluate(script)
        return tool_result({"worker_id": worker_id, "result": safe_json(result)})

    async def get_cache_storage(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        payload = await page_state.playwright_page.evaluate(
            """
            async () => {
              if (!('caches' in window)) return {supported: false, caches: []};
              const names = await caches.keys();
              const items = [];
              for (const name of names) {
                const cache = await caches.open(name);
                const requests = await cache.keys();
                items.push({
                  cache_name: name,
                  entry_count: requests.length,
                  sample_urls: requests.slice(0, 5).map((req) => req.url),
                });
              }
              return {supported: true, caches: items};
            }
            """
        )
        return tool_result(payload)

    async def clear_cache_storage(self, page_id: str, cache_name: str | None = None) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        payload = await page_state.playwright_page.evaluate(
            """
            async (cacheName) => {
              if (!('caches' in window)) return {supported: false, removed: 0};
              const names = await caches.keys();
              let removed = 0;
              for (const name of names) {
                if (!cacheName || name === cacheName) {
                  if (await caches.delete(name)) removed += 1;
                }
              }
              return {supported: true, removed};
            }
            """,
            cache_name,
        )
        return tool_result(payload)

    async def get_manifest(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        payload = await page_state.playwright_page.evaluate(
            """
            async () => {
              const link = document.querySelector('link[rel="manifest"]');
              if (!link) return null;
              const href = link.href;
              try {
                const response = await fetch(href, {credentials: 'include'});
                const text = await response.text();
                return {
                  url: href,
                  status: response.status,
                  manifest: JSON.parse(text),
                };
              } catch (error) {
                return {
                  url: href,
                  error: String(error),
                };
              }
            }
            """
        )
        return tool_result({"manifest": safe_json(payload)})

    async def list_websockets(self, page_id: str, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        sockets = []
        for entry in page_state.websocket_map.values():
            sockets.append(
                {
                    "socket_id": entry["socket_id"],
                    "url": entry["url"],
                    "created_at": entry["created_at"],
                    "status": entry["status"],
                    "message_count": len(entry["messages"]),
                    "last_error": entry["last_error"],
                }
            )
        return tool_result({"websockets": sockets, **self.apply_cursor(sockets, cursor=cursor, limit=limit or self.state.defaults.max_list_length)})

    async def get_websocket_messages(self, socket_id: str, limit: int | None = None, cursor: str | None = None) -> dict[str, Any]:
        entry = self._get_websocket_entry(socket_id)
        window = self.apply_cursor(
            entry["messages"],
            cursor=cursor,
            limit=limit or self.state.defaults.max_list_length,
        )
        return tool_result({"socket_id": socket_id, "url": entry["url"], **window})

    async def set_headers(self, context_id: str, headers: dict[str, str]) -> dict[str, Any]:
        context = self.get_context(context_id)
        await context.playwright_context.set_extra_http_headers(headers)
        context.config["headers"] = headers
        return tool_result({"success": True})

    async def get_cookies(self, context_id: str, urls: list[str] | None = None) -> dict[str, Any]:
        context = self.get_context(context_id)
        cookies = await context.playwright_context.cookies(urls=urls)
        return tool_result({"cookies": cookies})

    async def get_fingerprint_report(self, page_id: str) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        report = await page_state.playwright_page.evaluate(
            """
            async () => {
              const nav = navigator;
              const data = {
                navigator: {
                  userAgent: nav.userAgent,
                  language: nav.language,
                  languages: nav.languages,
                  platform: nav.platform,
                  vendor: nav.vendor,
                  webdriver: nav.webdriver,
                  cookieEnabled: nav.cookieEnabled,
                  hardwareConcurrency: nav.hardwareConcurrency,
                  deviceMemory: nav.deviceMemory ?? null,
                  maxTouchPoints: nav.maxTouchPoints,
                },
                viewport: {
                  innerWidth: window.innerWidth,
                  innerHeight: window.innerHeight,
                  outerWidth: window.outerWidth,
                  outerHeight: window.outerHeight,
                  devicePixelRatio: window.devicePixelRatio,
                },
                screen: {
                  width: screen.width,
                  height: screen.height,
                  availWidth: screen.availWidth,
                  availHeight: screen.availHeight,
                  colorDepth: screen.colorDepth,
                  pixelDepth: screen.pixelDepth,
                },
                locale: {
                  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                },
                storage: {
                  localStorageKeys: Object.keys(window.localStorage),
                  sessionStorageKeys: Object.keys(window.sessionStorage),
                },
                permissions: {},
              };
              if (nav.userAgentData) {
                data.userAgentData = {
                  brands: nav.userAgentData.brands || [],
                  mobile: nav.userAgentData.mobile,
                  platform: nav.userAgentData.platform,
                };
              }
              if (window.matchMedia) {
                data.media = {
                  colorSchemeDark: window.matchMedia('(prefers-color-scheme: dark)').matches,
                  reducedMotion: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
                };
              }
              if (navigator.permissions && navigator.permissions.query) {
                for (const name of ['geolocation', 'notifications', 'clipboard-read']) {
                  try {
                    const result = await navigator.permissions.query({name});
                    data.permissions[name] = result.state;
                  } catch (_error) {
                    data.permissions[name] = 'unsupported';
                  }
                }
              }
              try {
                const canvas = document.createElement('canvas');
                const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
                if (gl) {
                  const ext = gl.getExtension('WEBGL_debug_renderer_info');
                  data.webgl = {
                    vendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : null,
                    renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null,
                  };
                }
              } catch (_error) {
                data.webgl = null;
              }
              return data;
            }
            """
        )
        document_request = None
        for entry in reversed(page_state.buffers.network):
            if entry.get("resource_type") == "document":
                document_request = entry
                break
        return tool_result(
            {
                "page_id": page_id,
                "report": safe_json(report),
                "document_request_headers": safe_json((document_request or {}).get("headers", {})),
                "context_profile": safe_json(self.get_context(page_state.context_id).config),
            }
        )

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

    async def get_pending_notifications(self, page_id: str, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        page = page_state.playwright_page
        notifications = []
        try:
            notifications = await page.evaluate(
                """
                () => {
                  const items = Array.isArray(window.__bp_n)
                    ? window.__bp_n
                    : [];
                  return items.splice(0, items.length);
                }
                """
            )
        except Exception:
            notifications = []
        captured = []
        for item in notifications:
            normalized = {
                "timestamp": utc_ts(),
                "title": item.get("title"),
                "body": item.get("body"),
                "tag": item.get("tag"),
                "icon": item.get("icon"),
            }
            page_state.buffers.notifications.append(normalized)
            captured.append(normalized)
        payload = captured or page_state.buffers.notifications
        return tool_result({"notifications": payload, **self.apply_cursor(payload, cursor=cursor, limit=limit or self.state.defaults.max_list_length)})

    async def set_permission(self, context_id: str, permission: str, state: str) -> dict[str, Any]:
        context = self.get_context(context_id)
        context.config.setdefault("permission_overrides", {})[permission] = state
        runtime = context.playwright_context
        if state == "granted":
            if hasattr(runtime, "grant_permissions"):
                await runtime.grant_permissions([permission])
            return tool_result({"success": True, "permission": permission, "state": state})
        if state in {"denied", "prompt"}:
            if hasattr(runtime, "clear_permissions"):
                await runtime.clear_permissions()
            return tool_result(
                {
                    "success": True,
                    "permission": permission,
                    "state": state,
                    "note": "Playwright permission APIs clear grants but do not provide strict denied/prompt simulation per permission in this implementation.",
                }
            )
        raise SemanticError(
            "invalid_permission_state",
            f"Unsupported permission state '{state}'.",
            target={"context_id": context_id, "permission": permission, "state": state},
        )

    async def update_geolocation(self, context_id: str, latitude: float, longitude: float, accuracy: float | None = None) -> dict[str, Any]:
        context = self.get_context(context_id)
        payload = {"latitude": latitude, "longitude": longitude}
        if accuracy is not None:
            payload["accuracy"] = accuracy
        context.config["geolocation"] = payload
        runtime = context.playwright_context
        if hasattr(runtime, "set_geolocation"):
            await runtime.set_geolocation(payload)
        return tool_result({"success": True, "geolocation": payload})

    async def set_insecure_origins_as_secure(self, context_id: str, origins: list[str] | str | None = None) -> dict[str, Any]:
        context = self.get_context(context_id)
        if context.browser_name != "chromium":
            raise SemanticError(
                "unsupported_browser",
                "set_insecure_origins_as_secure is only supported for Chromium contexts.",
                target={"context_id": context_id, "browser": context.browser_name},
            )
        normalized = self._normalize_insecure_origins_as_secure(origins or [])
        current = self._normalize_insecure_origins_as_secure(context.config.get("treat_insecure_origins_as_secure"))
        if normalized == current:
            return tool_result(
                {
                    "success": True,
                    "context_id": context_id,
                    "treat_insecure_origins_as_secure": list(normalized),
                    "recreated_context": False,
                    "cleared_pages": 0,
                }
            )

        old_page_ids = list(context.pages)
        if self.state.current_page_id in old_page_ids:
            self.state.current_page_id = None
        for page_state in list(context.pages.values()):
            await self._collect_page_video_artifact(context, page_state)
        with suppress(Exception):
            await context.playwright_context.close()
        context.pages.clear()
        context.active_page_id = None

        updated_profile = dict(context.config)
        if normalized:
            updated_profile["treat_insecure_origins_as_secure"] = list(normalized)
        else:
            updated_profile.pop("treat_insecure_origins_as_secure", None)
        browser_instance = await self.ensure_browser(
            context.browser_name,
            headless=bool(updated_profile.get("headless", False)),
            treat_insecure_origins_as_secure=normalized,
        )
        context_kwargs = self._build_context_kwargs(context.artifact_dir, updated_profile, context.browser_name)
        context.playwright_context = await browser_instance.new_context(**context_kwargs)
        context.browser = browser_instance
        context.har_path = context_kwargs.get("record_har_path")
        context.video_recording_enabled = bool(context_kwargs.get("record_video_dir"))
        effective_profile = safe_json(updated_profile)
        effective_profile.update(safe_json(context_kwargs))
        context.config = effective_profile
        await context.playwright_context.add_init_script(
            """
            (() => {
              Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
              });
            })();
            """
        )
        await self._refresh_context_routes(context)
        context.playwright_context.on(
            "page",
            lambda page: asyncio.create_task(self.register_page(context, page)),
        )
        granted_permissions = [
            permission
            for permission, state in context.config.get("permission_overrides", {}).items()
            if state == "granted"
        ]
        if granted_permissions and hasattr(context.playwright_context, "grant_permissions"):
            await context.playwright_context.grant_permissions(granted_permissions)
        return tool_result(
            {
                "success": True,
                "context_id": context_id,
                "treat_insecure_origins_as_secure": list(normalized),
                "recreated_context": True,
                "cleared_pages": len(old_page_ids),
                "note": "The Playwright context was recreated in place. Existing pages were closed; open a new page for the updated setting to take effect.",
            }
        )

    async def execute_page_js(self, page_id: str, script: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict[str, Any]:
        self._rate_limit("execute_page_js", window_seconds=10, max_calls=20)
        page_state = self.get_page_state(page_id)

        async def run_script() -> dict[str, Any]:
            try:
                result = await asyncio.wait_for(page_state.playwright_page.evaluate(script), timeout=timeout_ms / 1000)
            except asyncio.TimeoutError as exc:
                raise SemanticError(
                    "script_timeout",
                    f"Page JavaScript execution timed out after {timeout_ms} ms.",
                    target={"page_id": page_id, "timeout_ms": timeout_ms},
                    retryable=True,
                ) from exc
            except Exception as exc:
                message = str(exc)
                if "Execution context was destroyed" in message or "Cannot find context with specified id" in message:
                    raise SemanticError(
                        "page_context_destroyed",
                        "Page JavaScript execution was interrupted because the page navigated or reloaded. Use navigate or reload_page for navigation-causing scripts.",
                        target={"page_id": page_id},
                        next_steps=["navigate", "reload_page"],
                    ) from exc
                raise SemanticError(
                    "page_script_error",
                    f"Page JavaScript execution failed: {summarize_text(message, 500)}",
                    target={"page_id": page_id},
                ) from exc
            return tool_result({"page_id": page_id, "result": safe_json(result), "timeout_ms": timeout_ms})

        return await self._await_with_page_issue_interrupt(page_state, run_script())

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
              window.__bp_tl = [];
              window.__bp_obs && window.__bp_obs.disconnect();
              window.__bp_obs = new PerformanceObserver((list) => {
                window.__bp_tl.push(...list.getEntries().map((entry) => ({
                  name: entry.name,
                  entryType: entry.entryType,
                  startTime: entry.startTime,
                  duration: entry.duration
                })));
              });
              window.__bp_obs.observe({entryTypes: ['paint', 'longtask', 'layout-shift', 'resource']});
            }
            """
        )
        await asyncio.sleep(duration_ms / 1000)
        entries = await page_state.playwright_page.evaluate("() => window.__bp_tl || []")
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
        context = self.get_context(page_state.context_id)
        if context.browser_name != "chromium":
            return tool_result(
                {
                    "url": page_state.playwright_page.url,
                    "supported": False,
                    "browser": context.browser_name,
                    "reason": "Certificate inspection is only available for Chromium contexts in this implementation.",
                }
            )
        try:
            session = await self._ensure_cdp_session(page_state)
            security_state = await session.send("Security.getSecurityState", {})
            visible_certificate = security_state.get("visibleSecurityState", {}).get("certificateSecurityState", {})
            return tool_result(
                {
                    "url": page_state.playwright_page.url,
                    "supported": True,
                    "protocol": visible_certificate.get("protocol"),
                    "issuer": visible_certificate.get("issuer"),
                    "subject_name": visible_certificate.get("subjectName"),
                    "valid_from": visible_certificate.get("validFrom"),
                    "valid_to": visible_certificate.get("validTo"),
                    "san_list": visible_certificate.get("sanList"),
                    "security_state": security_state.get("securityState"),
                }
            )
        except Exception:
            return tool_result(
                {
                    "url": page_state.playwright_page.url,
                    "supported": False,
                    "browser": context.browser_name,
                    "reason": "Certificate details could not be retrieved from the browser runtime.",
                }
            )

    async def check_cors(self, page_id: str, url: str, method: str = "GET") -> dict[str, Any]:
        page_state = self.get_page_state(page_id)
        page = page_state.playwright_page
        result = await page.evaluate(
            """
            async ({targetUrl, method}) => {
              try {
                const response = await fetch(targetUrl, {
                  method,
                  mode: 'cors',
                  credentials: 'include',
                });
                return {
                  allowed: true,
                  status: response.status,
                  type: response.type,
                  redirected: response.redirected,
                  url: response.url,
                };
              } catch (error) {
                return {
                  allowed: false,
                  error: String(error),
                };
              }
            }
            """,
            {"targetUrl": url, "method": method},
        )
        matching = []
        for entry in reversed(page_state.buffers.network):
            if entry.get("url") == url or entry.get("response", {}).get("url") == url:
                matching.append(entry)
            if len(matching) >= 3:
                break
        headers = {}
        status = result.get("status")
        if matching:
            response_headers = matching[0].get("response", {}).get("headers", {})
            headers = {
                key: value
                for key, value in response_headers.items()
                if key.lower().startswith("access-control-") or key.lower() == "vary"
            }
            if status is None:
                status = matching[0].get("response", {}).get("status")
        recent_errors = page_state.buffers.errors[-3:] + [item for item in page_state.buffers.console if item["type"] == "error"][-3:]
        return tool_result(
            {
                "page_id": page_id,
                "url": url,
                "method": method,
                "allowed": bool(result.get("allowed")),
                "status": status,
                "headers": headers,
                "type": result.get("type"),
                "redirected": result.get("redirected"),
                "error": result.get("error"),
                "recent_runtime_errors": recent_errors,
            }
        )

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

    async def list_artifacts(self, context_id: str, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
        context = self.get_context(context_id)
        artifacts = [
            {
                "artifact_id": item.artifact_id,
                "kind": item.kind,
                "path": item.path,
                "page_id": item.page_id,
                "tool": item.tool,
            }
            for item in context.artifacts
        ]
        return tool_result({"artifacts": artifacts, **self.apply_cursor(artifacts, cursor=cursor, limit=limit or self.state.defaults.max_list_length)})

    async def list_context_files(
        self,
        context_id: str,
        cursor: str | None = None,
        limit: int | None = None,
        subdir: str | None = None,
    ) -> dict[str, Any]:
        context = self.get_context(context_id)
        root = context.artifact_dir if subdir is None else self._resolve_context_artifact_path(context, subdir, must_exist=False)
        ensure_dir(root)
        items = []
        for path in sorted(root.rglob("*")):
            if path.is_dir():
                continue
            relative_path = str(path.relative_to(context.artifact_dir))
            items.append(
                {
                    "relative_path": relative_path,
                    "size_bytes": path.stat().st_size,
                    "kind": "text_candidate" if path.suffix.lower() in {".txt", ".md", ".json", ".log", ".csv", ".yaml", ".yml", ".xml", ".html", ".css", ".js"} else "file",
                }
            )
        return tool_result({"files": items, **self.apply_cursor(items, cursor=cursor, limit=limit or self.state.defaults.max_list_length)})

    async def upload_text_artifact(
        self,
        context_id: str,
        relative_path: str,
        content: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        context = self.get_context(context_id)
        payload = content.encode("utf-8")
        if len(payload) > DEFAULT_TEXT_TRANSFER_MAX_BYTES:
            raise SemanticError(
                "artifact_too_large",
                f"Text upload exceeds the {DEFAULT_TEXT_TRANSFER_MAX_BYTES} byte limit.",
                target={"context_id": context_id, "relative_path": relative_path, "size_bytes": len(payload)},
            )
        target = self._resolve_context_artifact_path(context, relative_path, must_exist=False)
        if target.exists() and not overwrite:
            raise SemanticError(
                "artifact_exists",
                f"Artifact path '{relative_path}' already exists.",
                target={"context_id": context_id, "relative_path": relative_path},
            )
        ensure_dir(target.parent)
        target.write_text(content, encoding="utf-8")
        artifact = self._add_artifact(context, "uploaded_text", str(target.resolve()), context.active_page_id, "upload_text_artifact")
        return tool_result(
            {
                "success": True,
                "relative_path": str(target.relative_to(context.artifact_dir)),
                "size_bytes": len(payload),
                "artifact": artifact,
                "note": "This interface is intended for lightweight UTF-8 text files only.",
            }
        )

    async def download_text_artifact(
        self,
        context_id: str,
        relative_path: str,
        max_bytes: int = DEFAULT_TEXT_TRANSFER_MAX_BYTES,
    ) -> dict[str, Any]:
        context = self.get_context(context_id)
        if max_bytes > DEFAULT_TEXT_TRANSFER_MAX_BYTES:
            max_bytes = DEFAULT_TEXT_TRANSFER_MAX_BYTES
        target = self._resolve_context_artifact_path(context, relative_path, must_exist=True)
        size_bytes = target.stat().st_size
        if size_bytes > max_bytes:
            raise SemanticError(
                "artifact_too_large",
                f"Artifact '{relative_path}' exceeds the {max_bytes} byte transfer limit.",
                target={"context_id": context_id, "relative_path": relative_path, "size_bytes": size_bytes},
            )
        raw = target.read_bytes()
        if b"\x00" in raw:
            raise SemanticError(
                "binary_artifact_not_supported",
                "The text transfer interface only supports lightweight UTF-8 text files.",
                target={"context_id": context_id, "relative_path": relative_path},
            )
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SemanticError(
                "binary_artifact_not_supported",
                "The text transfer interface only supports lightweight UTF-8 text files.",
                target={"context_id": context_id, "relative_path": relative_path},
            ) from exc
        return tool_result(
            {
                "relative_path": str(target.relative_to(context.artifact_dir)),
                "size_bytes": size_bytes,
                "content": content,
                "note": "This interface is intended for lightweight UTF-8 text files only.",
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
        before = await self.before_mutation(page_state, observe)
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
        effective_observe = self._resolve_observe_mode(observe)
        if effective_observe == "off":
            return None
        if self.state.defaults.checkpoint_auto or effective_observe == "full":
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
        if before is None:
            redirect_chain = self._build_redirect_chain(page_state, page_state.playwright_page.url)
            response = {
                "success": True,
                "page_id": page_state.page_id,
                "tool": tool_name,
                "changes": {},
                "redirect_chain": redirect_chain,
                "url": page_state.playwright_page.url,
                "observation": "off",
            }
            if extra:
                response.update(extra)
            return tool_result(response)
        current = await self.get_page_digest(page_state.page_id, mode="compact")
        changes = {}
        if before:
            changes = self.diff_states(before, current, mode="compact")
        redirect_chain = self._build_redirect_chain(page_state, page_state.playwright_page.url)
        response = {
            "success": True,
            "page_id": page_state.page_id,
            "tool": tool_name,
            "digest": current,
            "changes": changes,
            "redirect_chain": redirect_chain,
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
        return {
            "items": chunk,
            "next_cursor": str(next_cursor) if next_cursor is not None else None,
            "remaining_count": max(0, len(items) - (start + len(chunk))),
        }

    def _resolve_observe_mode(self, observe: str) -> str:
        if observe != "auto":
            return observe
        default = self.state.defaults.observe_default
        if default == "auto":
            return "light"
        if default == "final_only":
            return "light"
        return default

    def _find_opener_page_id(self, context: ContextState, opener: Any | None) -> str | None:
        if opener is None:
            return None
        for page_id, page_state in context.pages.items():
            if page_state.playwright_page is opener:
                return page_id
        return None

    async def _get_page_opener(self, page: Page) -> Any | None:
        opener = getattr(page, "opener", None)
        if callable(opener):
            try:
                opener = opener()
            except Exception:
                return None
        if asyncio.iscoroutine(opener):
            try:
                opener = await opener
            except Exception:
                return None
        return opener

    async def _page_summary(self, context: ContextState, page_state: PageState) -> dict[str, Any]:
        page = page_state.playwright_page
        opener = await self._get_page_opener(page)
        opener_page_id = self._find_opener_page_id(context, opener)
        return {
            "page_id": page_state.page_id,
            "url": page.url,
            "origin": origin_from_url(page.url) if page.url else None,
            "title": await page.title(),
            "is_active": context.active_page_id == page_state.page_id,
            "opener_page_id": opener_page_id,
            "opener_url": opener.url if opener is not None else None,
        }

    def normalize_exception(self, exc: Exception) -> dict[str, Any]:
        if isinstance(exc, SemanticError):
            return exc.to_dict()
        message = str(exc)
        if "ERR_BLOCKED_BY_CLIENT" in message:
            blocked_url = None
            hostname = None
            match = BLOCKED_BY_CLIENT_URL_RE.search(message)
            if match:
                blocked_url = match.group("url")
                try:
                    hostname = urlparse(blocked_url).hostname
                except Exception:
                    hostname = None
            likely_causes = [
                "browser-puppet route policy aborted the request with blockedbyclient",
                "the target URL matched a blocked route pattern",
                "the context disabled local-network access with allow_local_network=false",
            ]
            next_steps = [
                "check create_context profile.allow_local_network",
                "check block_routes or mocked route configuration on the context",
                "check whether the client is connected to the latest rebuilt MCP container",
            ]
            if hostname:
                likely_causes.insert(1, f"the target host '{hostname}' was treated as blocked by browser-puppet policy")
            return {
                "error_code": "request_blocked",
                "message": (
                    "Request was blocked by browser-puppet before it reached the network. "
                    "This ERR_BLOCKED_BY_CLIENT result usually comes from browser-puppet route.abort('blockedbyclient'), "
                    "not Chromium Private Network Access."
                ),
                "retryable": False,
                "target": {"url": blocked_url, "hostname": hostname},
                "likely_causes": likely_causes,
                "next_steps": next_steps,
            }
        return {
            "error_code": "tool_failure",
            "message": message,
            "retryable": False,
            "likely_causes": [],
            "next_steps": [],
        }

    def should_retry_transient_tool_error(self, exc: Exception) -> bool:
        if isinstance(exc, SemanticError):
            return False
        message = str(exc)
        return any(pattern.search(message) for pattern in TRANSIENT_INTERNAL_ERROR_PATTERNS)


APP = BrowserPuppetApp()
mcp = FastMCP("browser-puppet")


def expose(fn_name: str):
    def decorator(func):
        tool_signature = inspect.signature(func)
        param_names = tuple(tool_signature.parameters.keys())

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            started_at = time.perf_counter()
            call_args, call_kwargs = unwrap_mcp_tool_call(args, kwargs)
            LOGGER.info("tool_request tool=%s", fn_name)
            if LOGGER.isEnabledFor(logging.DEBUG):
                LOGGER.debug(
                    "tool_request_details tool=%s args=%s kwargs=%s",
                    fn_name,
                    summarize_payload(call_args, limit=1600),
                    summarize_payload(call_kwargs, limit=1600),
                )
            for attempt in range(2):
                try:
                    result = await func(*call_args, **call_kwargs)
                    duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
                    LOGGER.info("tool_response tool=%s duration_ms=%s attempt=%s", fn_name, duration_ms, attempt + 1)
                    page_state = APP._resolve_page_state_for_payload(tool_signature, call_args, call_kwargs, result) if isinstance(result, dict) else None
                    if page_state is not None:
                        result = APP._attach_page_issue_notices(page_state, result)
                    if LOGGER.isEnabledFor(logging.DEBUG):
                        LOGGER.debug(
                            "tool_response_details tool=%s result=%s",
                            fn_name,
                            summarize_payload(result, limit=1600),
                        )
                    return result
                except Exception as exc:
                    should_retry = attempt == 0 and APP.should_retry_transient_tool_error(exc)
                    if should_retry:
                        LOGGER.warning(
                            "tool_retry tool=%s delay_ms=%s error=%s",
                            fn_name,
                            APP.transient_retry_delay_ms,
                            summarize_text(str(exc), 800),
                        )
                        await asyncio.sleep(APP.transient_retry_delay_ms / 1000)
                        continue
                    duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
                    LOGGER.error(
                        "tool_error tool=%s duration_ms=%s error=%s",
                        fn_name,
                        duration_ms,
                        summarize_text(str(exc), 800),
                    )
                    error_payload = APP.normalize_exception(exc)
                    page_state = APP._resolve_page_state_for_payload(tool_signature, call_args, call_kwargs, error_payload)
                    if page_state is not None:
                        error_payload = APP._attach_page_issue_notices(page_state, error_payload)
                    if LOGGER.isEnabledFor(logging.DEBUG):
                        LOGGER.debug(
                            "tool_error_details tool=%s args=%s kwargs=%s traceback=%s",
                            fn_name,
                            summarize_payload(call_args, limit=2000),
                            summarize_payload(call_kwargs, limit=2000),
                            traceback.format_exc(),
                        )
                    return error_payload  # type: ignore[return-value]

        wrapper.__name__ = fn_name
        wrapper.__signature__ = tool_signature
        mcp.tool(name=fn_name)(wrapper)
        tool = mcp._tool_manager.get_tool(fn_name)
        if tool is not None:
            compat_model = build_compat_arg_model(tool.fn_metadata.arg_model, fn_name, param_names)
            tool.fn_metadata.arg_model = compat_model
            tool.parameters = compat_model.model_json_schema(by_alias=True)
        return wrapper

    return decorator


def build_network_app(transport: str):
    from starlette.applications import Starlette
    from starlette.middleware.base import BaseHTTPMiddleware

    normalized = "http" if transport == "streamable-http" else transport
    if normalized not in {"sse", "http", "both"}:
        raise ValueError(f"Unsupported network transport '{transport}'.")

    routes = []
    middleware = []
    lifespan_contexts = []

    if normalized in {"sse", "both"}:
        sse_app = mcp.sse_app("/")
        routes.extend(sse_app.routes)
        middleware.extend(sse_app.user_middleware)
        lifespan_contexts.append(sse_app.router.lifespan_context)

    if normalized in {"http", "both"}:
        http_app = mcp.streamable_http_app()
        routes.extend(http_app.routes)
        middleware.extend(http_app.user_middleware)
        lifespan_contexts.append(http_app.router.lifespan_context)

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            for context in lifespan_contexts:
                await stack.enter_async_context(context(app))
            try:
                yield
            finally:
                await APP.stop()

    app = Starlette(routes=routes, middleware=middleware, lifespan=lifespan)

    class RequestLoggingMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            started_at = time.perf_counter()
            LOGGER.info("http_request method=%s path=%s", request.method, request.url.path)
            try:
                response = await call_next(request)
                duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
                LOGGER.info(
                    "http_response method=%s path=%s status=%s duration_ms=%s",
                    request.method,
                    request.url.path,
                    response.status_code,
                    duration_ms,
                )
                return response
            except Exception:
                duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
                LOGGER.exception(
                    "http_error method=%s path=%s duration_ms=%s",
                    request.method,
                    request.url.path,
                    duration_ms,
                )
                raise

    app.add_middleware(RequestLoggingMiddleware)
    return app


@expose("create_context")
async def _create_context(browser: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    return await APP.create_context(browser, profile)


@expose("open_page")
async def _open_page(context_id: str, url: str, wait_until: str = "load", timeout_ms: int = DEFAULT_NAVIGATION_TIMEOUT_MS) -> dict[str, Any]:
    return await APP.open_page(context_id, url, wait_until, timeout_ms)


@expose("navigate")
async def _navigate(
    page_id: str,
    url: str,
    wait_until: str = "load",
    timeout_ms: int = DEFAULT_NAVIGATION_TIMEOUT_MS,
    observe: str = "off",
) -> dict[str, Any]:
    return await APP.navigate(page_id, url, wait_until, timeout_ms, observe)


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
async def _list_pages(context_id: str, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
    return await APP.list_pages(context_id, cursor, limit)


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


@expose("close_stale_contexts")
async def _close_stale_contexts() -> dict[str, Any]:
    return await APP.close_stale_contexts()


@expose("set_context_persistence")
async def _set_context_persistence(context_id: str, persistent: bool) -> dict[str, Any]:
    return await APP.set_context_persistence(context_id, persistent)


@expose("save_storage_state")
async def _save_storage_state(context_id: str, path: str | None = None) -> dict[str, Any]:
    return await APP.save_storage_state(context_id, path)


@expose("load_storage_state")
async def _load_storage_state(context_id: str, state: str | dict[str, Any]) -> dict[str, Any]:
    return await APP.load_storage_state(context_id, state)


@expose("export_browser_profile")
async def _export_browser_profile(
    context_id: str,
    path: str | None = None,
    include_session_storage: bool = True,
) -> dict[str, Any]:
    return await APP.export_browser_profile(context_id, path, include_session_storage)


@expose("import_browser_profile")
async def _import_browser_profile(context_id: str, profile: str | dict[str, Any]) -> dict[str, Any]:
    return await APP.import_browser_profile(context_id, profile)


@expose("set_extra_http_headers")
async def _set_extra_http_headers(page_id: str, headers: dict[str, str]) -> dict[str, Any]:
    return await APP.set_extra_http_headers(page_id, headers)


@expose("set_http_credentials")
async def _set_http_credentials(context_id: str, username: str, password: str) -> dict[str, Any]:
    return await APP.set_http_credentials(context_id, username, password)


@expose("store_credential")
async def _store_credential(context_id: str, alias: str, value: str) -> dict[str, Any]:
    return await APP.store_credential(context_id, alias, value)


@expose("delete_credential")
async def _delete_credential(context_id: str, alias: str) -> dict[str, Any]:
    return await APP.delete_credential(context_id, alias)


@expose("list_credentials")
async def _list_credentials(context_id: str) -> dict[str, Any]:
    return await APP.list_credentials(context_id)


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


@expose("capture_canvas")
async def _capture_canvas(element_id: str, format: str = "png", return_method: str = "disk") -> dict[str, Any]:
    return await APP.capture_canvas(element_id, format, return_method)


@expose("get_media_state")
async def _get_media_state(element_id: str) -> dict[str, Any]:
    return await APP.get_media_state(element_id)


@expose("control_media")
async def _control_media(element_id: str, action: str, value: float | None = None) -> dict[str, Any]:
    return await APP.control_media(element_id, action, value)


@expose("mock_media_devices")
async def _mock_media_devices(context_id: str, config: dict[str, Any]) -> dict[str, Any]:
    return await APP.mock_media_devices(context_id, config)


@expose("get_pdf_content")
async def _get_pdf_content(page_id: str) -> dict[str, Any]:
    return await APP.get_pdf_content(page_id)


@expose("print_to_pdf")
async def _print_to_pdf(page_id: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    return await APP.print_to_pdf(page_id, options)


@expose("get_pending_notifications")
async def _get_pending_notifications(page_id: str, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
    return await APP.get_pending_notifications(page_id, cursor, limit)


@expose("set_permission")
async def _set_permission(context_id: str, permission: str, state: str) -> dict[str, Any]:
    return await APP.set_permission(context_id, permission, state)


@expose("set_insecure_origins_as_secure")
async def _set_insecure_origins_as_secure(context_id: str, origins: list[str] | str | None = None) -> dict[str, Any]:
    return await APP.set_insecure_origins_as_secure(context_id, origins)


@expose("update_geolocation")
async def _update_geolocation(context_id: str, latitude: float, longitude: float, accuracy: float | None = None) -> dict[str, Any]:
    return await APP.update_geolocation(context_id, latitude, longitude, accuracy)


@expose("take_screenshot")
async def _take_screenshot(target: str, page_id: str | None = None, element_id: str | None = None, return_method: str = "disk") -> dict[str, Any]:
    return await APP.take_screenshot(target, page_id, element_id, return_method)


@expose("get_annotated_screenshot")
async def _get_annotated_screenshot(page_id: str, viewport_only: bool = True) -> dict[str, Any]:
    return await APP.get_annotated_screenshot(page_id, viewport_only)


@expose("get_visual_digest")
async def _get_visual_digest(page_id: str, mode: str = "compact") -> dict[str, Any]:
    return await APP.get_visual_digest(page_id, mode)


@expose("record_video")
async def _record_video(context_id: str, action: str) -> dict[str, Any]:
    return await APP.record_video(context_id, action)


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
    typing_mode: str = "auto",
    keystroke_delay_ms: int | None = None,
    keystroke_jitter_ms: int | None = None,
    observe: str = "auto",
) -> dict[str, Any]:
    """Type text into a targeted element or, if no target is provided, into the currently focused element.

    Use typing_mode="keystrokes" for consoles, terminals, or editors that need real key events instead of fill().
    keystroke_delay_ms sets the base delay between keystrokes and keystroke_jitter_ms adds random per-key variance.
    If you omit those timing values, browser-puppet chooses random millisecond defaults for a more natural typing cadence.
    """
    return await APP.type_text(text, page_id, element_id, target, clear_first, typing_mode, keystroke_delay_ms, keystroke_jitter_ms, observe)


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


@expose("submit_form")
async def _submit_form(
    page_id: str | None = None,
    element_id: str | None = None,
    target: dict[str, Any] | None = None,
    observe: str = "auto",
) -> dict[str, Any]:
    return await APP.submit_form(page_id, element_id, target, observe)


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


@expose("fill_and_click")
async def _fill_and_click(
    page_id: str,
    fields: list[dict[str, Any]],
    click_target: dict[str, Any],
    observe: str = "auto",
) -> dict[str, Any]:
    return await APP.fill_and_click(page_id, fields, click_target, observe)


@expose("click_and_wait")
async def _click_and_wait(
    page_id: str | None = None,
    element_id: str | None = None,
    target: dict[str, Any] | None = None,
    button: str = "left",
    click_count: int = 1,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    wait_for: str = "navigation",
    wait_target: dict[str, Any] | None = None,
    observe: str = "auto",
) -> dict[str, Any]:
    return await APP.click_and_wait(page_id, element_id, target, button, click_count, timeout_ms, wait_for, wait_target, observe)


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


@expose("list_websockets")
async def _list_websockets(page_id: str, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
    return await APP.list_websockets(page_id, cursor, limit)


@expose("get_websocket_messages")
async def _get_websocket_messages(socket_id: str, limit: int | None = None, cursor: str | None = None) -> dict[str, Any]:
    return await APP.get_websocket_messages(socket_id, limit, cursor)


@expose("set_headers")
async def _set_headers(context_id: str, headers: dict[str, str]) -> dict[str, Any]:
    return await APP.set_headers(context_id, headers)


@expose("get_cookies")
async def _get_cookies(context_id: str, urls: list[str] | None = None) -> dict[str, Any]:
    return await APP.get_cookies(context_id, urls)


@expose("get_fingerprint_report")
async def _get_fingerprint_report(page_id: str) -> dict[str, Any]:
    return await APP.get_fingerprint_report(page_id)


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
async def _execute_page_js(page_id: str, script: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict[str, Any]:
    return await APP.execute_page_js(page_id, script, timeout_ms)


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


@expose("list_service_workers")
async def _list_service_workers(context_id: str, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
    return await APP.list_service_workers(context_id, cursor, limit)


@expose("unregister_service_worker")
async def _unregister_service_worker(context_id: str, scope: str) -> dict[str, Any]:
    return await APP.unregister_service_worker(context_id, scope)


@expose("list_web_workers")
async def _list_web_workers(page_id: str, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
    return await APP.list_web_workers(page_id, cursor, limit)


@expose("evaluate_worker")
async def _evaluate_worker(worker_id: str, script: str) -> dict[str, Any]:
    return await APP.evaluate_worker(worker_id, script)


@expose("get_cache_storage")
async def _get_cache_storage(page_id: str) -> dict[str, Any]:
    return await APP.get_cache_storage(page_id)


@expose("clear_cache_storage")
async def _clear_cache_storage(page_id: str, cache_name: str | None = None) -> dict[str, Any]:
    return await APP.clear_cache_storage(page_id, cache_name)


@expose("get_manifest")
async def _get_manifest(page_id: str) -> dict[str, Any]:
    return await APP.get_manifest(page_id)


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


@expose("check_cors")
async def _check_cors(page_id: str, url: str, method: str = "GET") -> dict[str, Any]:
    return await APP.check_cors(page_id, url, method)


@expose("send_cdp_command")
async def _send_cdp_command(page_id: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return await APP.send_cdp_command(page_id, method, params)


@expose("subscribe_cdp_events")
async def _subscribe_cdp_events(page_id: str, events: list[str]) -> dict[str, Any]:
    return await APP.subscribe_cdp_events(page_id, events)


@expose("get_cdp_events")
async def _get_cdp_events(subscription_id: str, limit: int | None = None, cursor: str | None = None) -> dict[str, Any]:
    return await APP.get_cdp_events(subscription_id, limit, cursor)


@expose("intercept_download")
async def _intercept_download(page_id: str, trigger: dict[str, Any] | None = None) -> dict[str, Any]:
    return await APP.intercept_download(page_id, trigger)


@expose("list_artifacts")
async def _list_artifacts(context_id: str, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
    return await APP.list_artifacts(context_id, cursor, limit)


@expose("list_context_files")
async def _list_context_files(
    context_id: str,
    cursor: str | None = None,
    limit: int | None = None,
    subdir: str | None = None,
) -> dict[str, Any]:
    return await APP.list_context_files(context_id, cursor, limit, subdir)


@expose("upload_text_artifact")
async def _upload_text_artifact(
    context_id: str,
    relative_path: str,
    content: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    return await APP.upload_text_artifact(context_id, relative_path, content, overwrite)


@expose("download_text_artifact")
async def _download_text_artifact(
    context_id: str,
    relative_path: str,
    max_bytes: int = DEFAULT_TEXT_TRANSFER_MAX_BYTES,
) -> dict[str, Any]:
    return await APP.download_text_artifact(context_id, relative_path, max_bytes)


@expose("export_har")
async def _export_har(context_id: str) -> dict[str, Any]:
    return await APP.export_har(context_id)


@expose("block_routes")
async def _block_routes(context_id: str, patterns: list[str]) -> dict[str, Any]:
    return await APP.block_routes(context_id, patterns)


@expose("mock_routes")
async def _mock_routes(context_id: str, routes: list[dict[str, Any]]) -> dict[str, Any]:
    return await APP.mock_routes(context_id, routes)


@expose("set_host_overrides")
async def _set_host_overrides(context_id: str, mappings: dict[str, str]) -> dict[str, Any]:
    return await APP.set_host_overrides(context_id, mappings)


@expose("get_dns_resolution")
async def _get_dns_resolution(page_id: str, hostname: str) -> dict[str, Any]:
    return await APP.get_dns_resolution(page_id, hostname)


@expose("set_user_agent")
async def _set_user_agent(context_id: str, user_agent: str) -> dict[str, Any]:
    return await APP.set_user_agent(context_id, user_agent)


@expose("emulate_network")
async def _emulate_network(
    context_id: str,
    profile: dict[str, Any] | None = None,
    preset: str | None = None,
) -> dict[str, Any]:
    return await APP.emulate_network(context_id, profile, preset)


@expose("pinch_zoom")
async def _pinch_zoom(page_id: str, scale_factor: float, x: int | None = None, y: int | None = None) -> dict[str, Any]:
    return await APP.pinch_zoom(page_id, scale_factor, x, y)


@expose("get_visual_diff")
async def _get_visual_diff(context_id: str, baseline_path: str, candidate_path: str) -> dict[str, Any]:
    return await APP.get_visual_diff(context_id, baseline_path, candidate_path)


@expose("get_coverage")
async def _get_coverage(page_id: str) -> dict[str, Any]:
    return await APP.get_coverage(page_id)


@expose("generate_report")
async def _generate_report(context_id: str, format: str = "json") -> dict[str, Any]:
    return await APP.generate_report(context_id, format)


@expose("run_steps")
async def _run_steps(page_id: str, steps: list[dict[str, Any]], stop_on_failure: bool = True, observe: str = "final_only") -> dict[str, Any]:
    return await APP.run_steps(page_id, steps, stop_on_failure, observe)


def main() -> None:
    parser = argparse.ArgumentParser(description="Browser Puppet MCP server")
    parser.add_argument("--transport", choices=["stdio", "sse", "http", "streamable-http", "both"], default="stdio")
    parser.add_argument("--host", default=DEFAULT_SSE_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_SSE_PORT)
    parser.add_argument("--log-level", default=os.environ.get("BROWSER_PUPPET_LOG_LEVEL", "INFO"))
    parser.add_argument(
        "--transient-retry-delay-ms",
        type=int,
        default=DEFAULT_TRANSIENT_RETRY_DELAY_MS,
        help="Delay before retrying known transient internal tool failures once.",
    )
    parser.add_argument(
        "--stale-context-timeout-seconds",
        type=int,
        default=DEFAULT_STALE_CONTEXT_TIMEOUT_SECONDS,
        help="Auto-close non-persistent contexts after this many seconds without access.",
    )
    parser.add_argument(
        "--disable-stale-context-cleanup",
        action="store_true",
        help="Disable automatic stale browser-context cleanup.",
    )
    args = parser.parse_args()
    log_level = configure_logging(args.log_level)
    APP.transient_retry_delay_ms = max(0, args.transient_retry_delay_ms)
    APP.stale_context_timeout_seconds = max(1, args.stale_context_timeout_seconds)
    APP.auto_close_stale_contexts = not args.disable_stale_context_cleanup
    LOGGER.info(
        "server_start transport=%s host=%s port=%s stale_context_cleanup=%s stale_context_timeout_seconds=%s",
        args.transport,
        args.host,
        args.port,
        APP.auto_close_stale_contexts,
        APP.stale_context_timeout_seconds,
    )
    if args.transport == "stdio":
        mcp.run()
        return
    if args.transport == "sse":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="sse")
        return
    import uvicorn

    app = build_network_app(args.transport)
    uvicorn_level = "debug" if log_level <= logging.DEBUG else logging.getLevelName(log_level).lower()
    uvicorn.run(app, host=args.host, port=args.port, log_level=uvicorn_level)


if __name__ == "__main__":
    main()
