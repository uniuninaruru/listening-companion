"""Session-only provider/result/cursor caches."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from typing import Generic, TypeVar

from .errors import CacheMiss
from .models import CachedResult
from .security import constant_time_equal, random_opaque_id


T = TypeVar("T")


class TTLCache(Generic[T]):
    def __init__(self, ttl_seconds: int, clock: Callable[[], float] | None = None, max_entries: int = 512) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max(1, max_entries)
        self._clock = clock or time.time
        self._items: dict[str, tuple[float, T]] = {}
        self._lock = threading.RLock()

    def put(self, key: str, value: T, ttl_seconds: int | None = None) -> None:
        expires = self._clock() + (ttl_seconds if ttl_seconds is not None else self.ttl_seconds)
        with self._lock:
            now = self._clock()
            for old_key, (old_expires, _) in list(self._items.items()):
                if old_expires <= now:
                    self._items.pop(old_key, None)
            while key not in self._items and len(self._items) >= self.max_entries:
                oldest_key = min(self._items, key=lambda item: self._items[item][0])
                self._items.pop(oldest_key, None)
            self._items[key] = (expires, value)

    def get(self, key: str) -> T | None:
        now = self._clock()
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            expires, value = entry
            if expires <= now:
                self._items.pop(key, None)
                return None
            return value

    def pop(self, key: str) -> T | None:
        value = self.get(key)
        with self._lock:
            self._items.pop(key, None)
        return value

    def put_cursor(self, value: T, ttl_seconds: int | None = None) -> str:
        key = random_opaque_id(24)
        self.put(key, value, ttl_seconds)
        return key

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def purge(self) -> None:
        now = self._clock()
        with self._lock:
            for key, (expires, _) in list(self._items.items()):
                if expires <= now:
                    self._items.pop(key, None)

    def __len__(self) -> int:
        self.purge()
        with self._lock:
            return len(self._items)


class ResultCache:
    """Opaque model receipts backed by in-memory provider records only."""

    def __init__(
        self,
        ttl_seconds: int,
        viewer_base_url: str,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._cache: TTLCache[CachedResult] = TTLCache(ttl_seconds, clock, max_entries=256)
        self._viewer_base_url = viewer_base_url.rstrip("/")
        self._ttl_seconds = ttl_seconds
        self._clock = clock or time.time

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def store(
        self,
        operation: str,
        data: tuple[object, ...],
        warnings: tuple[dict[str, str], ...] = (),
        ttl_seconds: int | None = None,
    ) -> CachedResult:
        result_id = random_opaque_id(24)
        capability = random_opaque_id(32)
        now = self._clock()
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl_seconds
        result = CachedResult(
            result_id=result_id,
            capability=capability,
            operation=operation,
            data=data,
            created_at=now,
            expires_at=now + ttl,
            warnings=warnings,
        )
        self._cache.put(result_id, result, ttl)
        return result

    def internal_get(self, result_id: str) -> CachedResult:
        result = self._cache.get(result_id)
        if result is None:
            raise CacheMiss("result is expired or unknown")
        return result

    def authorize_view(self, result_id: str, capability: str | None) -> CachedResult:
        result = self.internal_get(result_id)
        if not capability or not constant_time_equal(capability, result.capability):
            raise CacheMiss("viewer capability is invalid or expired")
        return result

    def public_summary(
        self,
        result: CachedResult,
        *,
        next_cursor: str | None = None,
        status: str = "ok",
    ) -> dict[str, object]:
        remaining = max(0, int(result.expires_at - self._clock()))
        viewer_url = f"{self._viewer_base_url}/view/{result.result_id}?token={result.capability}"
        output: dict[str, object] = {
            "status": status,
            "result_id": result.result_id,
            "count": len(result.data),
            "viewer_url": viewer_url,
            "expires_in_seconds": remaining,
            "warnings": list(result.warnings),
        }
        if next_cursor:
            output["next_cursor"] = next_cursor
        return output

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)
