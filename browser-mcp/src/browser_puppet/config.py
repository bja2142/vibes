from __future__ import annotations

import os
from pathlib import Path


def get_default_artifact_dir() -> Path:
    return Path(os.environ.get("BROWSER_PUPPET_ARTIFACT_DIR", "artifacts"))


def get_default_transient_retry_delay_ms() -> int:
    return int(os.environ.get("BROWSER_PUPPET_TRANSIENT_RETRY_DELAY_MS", "100"))


DEFAULT_ARTIFACT_DIR = get_default_artifact_dir()
DEFAULT_TRANSIENT_RETRY_DELAY_MS = get_default_transient_retry_delay_ms()
DEFAULT_SSE_HOST = "0.0.0.0"
DEFAULT_SSE_PORT = 8000
MAX_INLINE_TEXT = 4000
DEFAULT_TIMEOUT_MS = 10000
DEFAULT_TEXT_TRANSFER_MAX_BYTES = 65536
