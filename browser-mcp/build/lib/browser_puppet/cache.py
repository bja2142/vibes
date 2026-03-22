from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from time import time
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(slots=True)
class CacheEntry(Generic[T]):
    value: T
    created_at: float
    expires_at: float | None
    permanent: bool = False


class TTLCache(Generic[T]):
    def __init__(self, ttl_seconds: int = 300, max_entries: int = 256) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._data: OrderedDict[str, CacheEntry[T]] = OrderedDict()
        self.evictions = 0

    def set(self, key: str, value: T, *, ttl_seconds: int | None = None, permanent: bool = False) -> None:
        now = time()
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds
        entry = CacheEntry(
            value=value,
            created_at=now,
            expires_at=None if permanent else now + ttl,
            permanent=permanent,
        )
        self._data[key] = entry
        self._data.move_to_end(key)
        self._evict_if_needed()

    def get(self, key: str) -> T | None:
        self.prune()
        entry = self._data.get(key)
        if entry is None:
            return None
        self._data.move_to_end(key)
        return entry.value

    def pop(self, key: str) -> T | None:
        entry = self._data.pop(key, None)
        return None if entry is None else entry.value

    def prune(self) -> None:
        now = time()
        expired = [
            key
            for key, entry in self._data.items()
            if not entry.permanent and entry.expires_at is not None and entry.expires_at <= now
        ]
        for key in expired:
            self._data.pop(key, None)
            self.evictions += 1

    def stats(self) -> dict[str, int]:
        self.prune()
        return {
            "active_handles": len(self._data),
            "evictions": self.evictions,
            "capacity": self.max_entries,
        }

    def _evict_if_needed(self) -> None:
        self.prune()
        while len(self._data) > self.max_entries:
            oldest_key, oldest_entry = next(iter(self._data.items()))
            if oldest_entry.permanent:
                self._data.move_to_end(oldest_key)
                continue
            self._data.pop(oldest_key, None)
            self.evictions += 1
