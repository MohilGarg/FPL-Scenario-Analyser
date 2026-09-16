from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from threading import Lock
from time import monotonic


@dataclass(frozen=True, slots=True)
class _CacheItem[Value]:
    value: Value
    expires_at: float


class TTLCache[Key: Hashable, Value]:
    def __init__(self, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.ttl_seconds = ttl_seconds
        self._items: dict[Key, _CacheItem[Value]] = {}
        self._lock = Lock()

    def get(self, key: Key) -> Value | None:
        now = monotonic()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            if item.expires_at <= now:
                del self._items[key]
                return None
            return item.value

    def set(self, key: Key, value: Value) -> None:
        with self._lock:
            self._items[key] = _CacheItem(value, monotonic() + self.ttl_seconds)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
