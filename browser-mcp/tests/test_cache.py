from browser_puppet.cache import TTLCache


def test_ttl_cache_round_trip_and_pop() -> None:
    cache = TTLCache[str](ttl_seconds=60, max_entries=4)
    cache.set("a", "one")

    assert cache.get("a") == "one"
    assert cache.pop("a") == "one"
    assert cache.get("a") is None


def test_ttl_cache_evicts_lru_non_permanent_entries() -> None:
    cache = TTLCache[str](ttl_seconds=60, max_entries=2)
    cache.set("a", "one")
    cache.set("b", "two")
    cache.set("p", "permanent", permanent=True)

    stats = cache.stats()

    assert cache.get("a") is None
    assert cache.get("b") == "two"
    assert cache.get("p") == "permanent"
    assert stats["evictions"] >= 1
