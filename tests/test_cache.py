from __future__ import annotations

from soundcloud_recommender.cache import TTLCache


def test_ttl_cache_expires_entries_and_bounds_unvisited_values() -> None:
    now = [0.0]
    cache: TTLCache[str] = TTLCache(10, clock=lambda: now[0], max_entries=2)
    cache.put("expired", "old", ttl_seconds=1)
    now[0] = 2.0
    cache.put("one", "1")
    cache.put("two", "2")
    cache.put("three", "3")
    assert cache.get("expired") is None
    assert len(cache) == 2
    assert cache.get("one") is None
    assert cache.get("two") == "2"
    assert cache.get("three") == "3"
