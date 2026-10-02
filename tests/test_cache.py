import asyncio

import pytest

from freshdesk_connector.cache import TTLCache


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Loader:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> int:
        self.calls += 1
        await asyncio.sleep(0)
        return self.calls


async def test_returns_cached_value_until_expiry() -> None:
    clock, load = Clock(), Loader()
    cache: TTLCache[int] = TTLCache(ttl_seconds=10, max_entries=10, clock=clock)

    assert await cache.get_or_load("k", load) == 1
    assert await cache.get_or_load("k", load) == 1
    clock.now = 11
    assert await cache.get_or_load("k", load) == 2


async def test_concurrent_misses_share_one_load() -> None:
    load = Loader()
    cache: TTLCache[int] = TTLCache(ttl_seconds=10, max_entries=10)

    results = await asyncio.gather(*(cache.get_or_load("k", load) for _ in range(5)))

    assert results == [1] * 5
    assert load.calls == 1


async def test_failures_are_not_cached() -> None:
    cache: TTLCache[int] = TTLCache(ttl_seconds=10, max_entries=10)

    async def fail() -> int:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await cache.get_or_load("k", fail)
    assert await cache.get_or_load("k", Loader()) == 1


async def test_evicts_least_recently_used_entry() -> None:
    cache: TTLCache[int] = TTLCache(ttl_seconds=10, max_entries=2)
    a, b, c, a_again = Loader(), Loader(), Loader(), Loader()

    await cache.get_or_load("a", a)
    await cache.get_or_load("b", b)
    await cache.get_or_load("a", a)  # touch "a" so "b" becomes the oldest
    await cache.get_or_load("c", c)
    await cache.get_or_load("a", a_again)

    assert a_again.calls == 0


async def test_zero_ttl_disables_caching() -> None:
    load = Loader()
    cache: TTLCache[int] = TTLCache(ttl_seconds=0, max_entries=10)

    await cache.get_or_load("k", load)
    await cache.get_or_load("k", load)

    assert load.calls == 2
