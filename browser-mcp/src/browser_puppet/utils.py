from __future__ import annotations

import base64
import hashlib
import hmac
import json
import struct
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def utc_ts() -> float:
    return time.time()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def summarize_text(value: str | None, limit: int = 240) -> str:
    if not value:
        return ""
    trimmed = " ".join(value.split())
    if len(trimmed) <= limit:
        return trimmed
    return f"{trimmed[: limit - 1]}…"


def safe_json(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): safe_json(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [safe_json(item) for item in value]
        return repr(value)


def compute_totp(secret: str, digits: int = 6, period: int = 30, algorithm: str = "SHA1", for_time: int | None = None) -> str:
    normalized = secret.strip().replace(" ", "").upper()
    padded = normalized + "=" * ((8 - len(normalized) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    counter = int((for_time or time.time()) // period)
    digest_name = algorithm.lower()
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, getattr(hashlib, digest_name)).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def origin_from_url(url: str) -> str:
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else url
