from __future__ import annotations

import os
from pathlib import Path


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_default_artifact_dir() -> Path:
    return Path(os.environ.get("BROWSER_PUPPET_ARTIFACT_DIR", "artifacts"))


def get_default_transient_retry_delay_ms() -> int:
    return int(os.environ.get("BROWSER_PUPPET_TRANSIENT_RETRY_DELAY_MS", "100"))


def get_default_allow_local_network() -> bool:
    return _env_flag("BROWSER_PUPPET_ALLOW_LOCAL_NETWORK", default=False)


def get_default_max_contexts() -> int:
    return int(os.environ.get("BROWSER_PUPPET_MAX_CONTEXTS", "10"))


def get_default_auto_close_stale_contexts() -> bool:
    return _env_flag("BROWSER_PUPPET_AUTO_CLOSE_STALE_CONTEXTS", default=True)


def get_default_stale_context_timeout_seconds() -> int:
    return int(os.environ.get("BROWSER_PUPPET_STALE_CONTEXT_TIMEOUT_SECONDS", "3600"))


DEFAULT_ARTIFACT_DIR = get_default_artifact_dir()
DEFAULT_TRANSIENT_RETRY_DELAY_MS = get_default_transient_retry_delay_ms()
DEFAULT_ALLOW_LOCAL_NETWORK = get_default_allow_local_network()
DEFAULT_MAX_CONTEXTS = get_default_max_contexts()
DEFAULT_AUTO_CLOSE_STALE_CONTEXTS = get_default_auto_close_stale_contexts()
DEFAULT_STALE_CONTEXT_TIMEOUT_SECONDS = get_default_stale_context_timeout_seconds()
DEFAULT_SSE_HOST = "0.0.0.0"
DEFAULT_SSE_PORT = 8000
MAX_INLINE_TEXT = 4000
DEFAULT_TIMEOUT_MS = 10000
DEFAULT_NAVIGATION_TIMEOUT_MS = 30000
DEFAULT_TEXT_TRANSFER_MAX_BYTES = 65536
