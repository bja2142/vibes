from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cache import TTLCache
from .utils import utc_ts


@dataclass(slots=True)
class SessionDefaults:
    mode: str = "compact"
    max_list_length: int = 10
    exclude_fields: list[str] = field(default_factory=list)
    binary_mode: str = "handle"
    observe_default: str = "auto"
    checkpoint_auto: bool = False
    proactive_events: bool = True


@dataclass(slots=True)
class ArtifactRecord:
    artifact_id: str
    kind: str
    path: str
    page_id: str | None
    context_id: str
    tool: str
    created_at: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ElementRecord:
    element_id: str
    page_id: str
    frame_id: str | None
    selector: str | None
    hints: dict[str, Any]
    created_at: float
    shadow_host_element_id: str | None = None
    shadow_selector: str | None = None
    nth: int = 0


@dataclass(slots=True)
class PageBuffers:
    console: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    network: list[dict[str, Any]] = field(default_factory=list)
    dialogs: list[dict[str, Any]] = field(default_factory=list)
    downloads: list[dict[str, Any]] = field(default_factory=list)
    page_events: list[dict[str, Any]] = field(default_factory=list)
    websockets: list[dict[str, Any]] = field(default_factory=list)
    csp_violations: list[dict[str, Any]] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class PageState:
    page_id: str
    context_id: str
    playwright_page: Any
    frame_focus: str | None = None
    last_meta: dict[str, Any] = field(default_factory=dict)
    last_state: dict[str, Any] = field(default_factory=dict)
    last_tool_event_index: int = 0
    frame_map: dict[str, Any] = field(default_factory=dict)
    buffers: PageBuffers = field(default_factory=PageBuffers)
    element_cache: TTLCache[ElementRecord] = field(default_factory=lambda: TTLCache(ttl_seconds=300, max_entries=512))
    request_map: dict[str, dict[str, Any]] = field(default_factory=dict)
    response_bodies: dict[str, bytes] = field(default_factory=dict)
    pending_issue_notices: list[dict[str, Any]] = field(default_factory=list)
    pending_issue_keys: set[str] = field(default_factory=set)
    issue_waiters: list[Any] = field(default_factory=list)
    cdp_session: Any | None = None
    cdp_subscriptions: dict[str, dict[str, Any]] = field(default_factory=dict)
    websocket_map: dict[str, dict[str, Any]] = field(default_factory=dict)
    worker_map: dict[str, dict[str, Any]] = field(default_factory=dict)
    coverage_started: bool = False
    coverage_stylesheets: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class ContextState:
    context_id: str
    browser_name: str
    browser: Any
    playwright_context: Any
    artifact_dir: Path
    pages: dict[str, PageState] = field(default_factory=dict)
    active_page_id: str | None = None
    artifacts: list[ArtifactRecord] = field(default_factory=list)
    handles: TTLCache[dict[str, Any]] = field(default_factory=lambda: TTLCache(ttl_seconds=300, max_entries=512))
    checkpoints: dict[str, str] = field(default_factory=dict)
    credentials: dict[str, str] = field(default_factory=dict)
    trace_path: str | None = None
    har_path: str | None = None
    video_recording_enabled: bool = False
    host_overrides: dict[str, str] = field(default_factory=dict)
    blocked_routes: list[str] = field(default_factory=list)
    mocked_routes: list[dict[str, Any]] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    service_worker_map: dict[str, dict[str, Any]] = field(default_factory=dict)
    storage_seed: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    last_used_at: float = field(default_factory=utc_ts)


@dataclass(slots=True)
class ServerState:
    artifacts_root: Path
    defaults: SessionDefaults = field(default_factory=SessionDefaults)
    contexts: dict[str, ContextState] = field(default_factory=dict)
    current_page_id: str | None = None
    playwright: Any | None = None
    browser_pool: dict[str, Any] = field(default_factory=dict)
    rate_limits: dict[str, list[float]] = field(default_factory=dict)
