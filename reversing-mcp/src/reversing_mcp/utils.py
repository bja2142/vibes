from __future__ import annotations

import base64
import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_clone(value: Any) -> Any:
    return copy.deepcopy(value)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def stable_json_dumps(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def new_uuid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def short_token(value: str, length: int = 8) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9]", "", value)
    if sanitized:
        return sanitized[:length].lower()
    return uuid4().hex[:length]


def normalize_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_LIMIT
    return max(1, min(MAX_PAGE_LIMIT, int(limit)))


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        return max(0, int(raw))
    except Exception as exc:  # pragma: no cover - normalized by caller
        raise ValueError("Cursor is not valid.") from exc


def paginate(items: list[Any], cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
    normalized_limit = normalize_limit(limit)
    start = decode_cursor(cursor)
    end = start + normalized_limit
    page = items[start:end]
    next_cursor = encode_cursor(end) if end < len(items) else None
    return {
        "items": page,
        "page": {
            "limit": normalized_limit,
            "cursor": cursor,
            "next_cursor": next_cursor,
            "returned": len(page),
            "total": len(items),
        },
    }
