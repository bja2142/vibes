from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

from .errors import StructuredToolError


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class RequestContext:
    transport: str = "stdio"
    authenticated: bool = False
    tenant_id: str | None = None
    agent_id: str | None = None

    @property
    def is_http(self) -> bool:
        return self.transport == "http"


@dataclass(frozen=True, slots=True)
class HttpTransportConfig:
    require_auth: bool
    tokens: dict[str, str]
    requests_per_minute_per_agent: int
    max_sessions_per_tenant: int
    max_active_jobs_per_tenant: int
    agent_header: str

    @property
    def auth_enabled(self) -> bool:
        return self.require_auth


_CURRENT_REQUEST_CONTEXT: ContextVar[RequestContext] = ContextVar("reversing_mcp_request_context", default=RequestContext())


def get_request_context() -> RequestContext:
    return _CURRENT_REQUEST_CONTEXT.get()


@contextmanager
def request_context(context: RequestContext) -> Iterator[None]:
    token = _CURRENT_REQUEST_CONTEXT.set(context)
    try:
        yield
    finally:
        _CURRENT_REQUEST_CONTEXT.reset(token)


def load_http_transport_config() -> HttpTransportConfig:
    tokens: dict[str, str] = {}
    raw = os.environ.get("REVERSING_MCP_HTTP_TOKENS", "")
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError("REVERSING_MCP_HTTP_TOKENS entries must use 'tenant=token' format.")
        tenant_id, token = item.split("=", 1)
        normalized_tenant = tenant_id.strip()
        normalized_token = token.strip()
        if not normalized_tenant or not normalized_token:
            raise ValueError("REVERSING_MCP_HTTP_TOKENS entries must include both tenant and token values.")
        tokens[normalized_token] = normalized_tenant
    return HttpTransportConfig(
        require_auth=_env_flag("REVERSING_MCP_HTTP_REQUIRE_AUTH", default=False),
        tokens=tokens,
        requests_per_minute_per_agent=max(1, int(os.environ.get("REVERSING_MCP_HTTP_REQUESTS_PER_MINUTE_PER_AGENT", "120"))),
        max_sessions_per_tenant=max(1, int(os.environ.get("REVERSING_MCP_HTTP_MAX_SESSIONS_PER_TENANT", "32"))),
        max_active_jobs_per_tenant=max(1, int(os.environ.get("REVERSING_MCP_HTTP_MAX_ACTIVE_JOBS_PER_TENANT", "4"))),
        agent_header=os.environ.get("REVERSING_MCP_HTTP_AGENT_HEADER", "x-reversing-agent-id").strip().lower() or "x-reversing-agent-id",
    )


class RequestRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, *, limit: int, window_seconds: int = 60) -> None:
        now = time.time()
        with self._lock:
            bucket = self._entries[key]
            while bucket and bucket[0] <= now - window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                raise StructuredToolError(
                    "timeout_or_resource_limit",
                    "http_rate_limit_exceeded",
                    "HTTP request quota exceeded for the current agent.",
                    details={"limit": limit, "window_seconds": window_seconds, "key": key},
                )
            bucket.append(now)


REQUEST_RATE_LIMITER = RequestRateLimiter()
