"""In-process TTL cache with LRU eviction and single-flight loading."""

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import Generic, TypeVar

V = TypeVar("V")


class TTLCache(Generic[V]):
    """Caches successful loads for a fixed time. Failures are never cached.

    Concurrent callers asking for the same missing key share one load, so a burst of
    identical tool calls costs a single API request.
    """

    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[Hashable, tuple[float, V]] = OrderedDict()
        self._inflight: dict[Hashable, asyncio.Task[V]] = {}

    async def get_or_load(self, key: Hashable, load: Callable[[], Awaitable[V]]) -> V:
        if self._ttl == 0:
            return await load()

        entry = self._entries.get(key)
        if entry is not None:
            expires_at, value = entry
            if expires_at > self._clock():
                self._entries.move_to_end(key)
                return value
            del self._entries[key]

        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._load(key, load))
            self._inflight[key] = task
        # Shield the shared load so one cancelled caller does not cancel it for the others.
        return await asyncio.shield(task)

    async def _load(self, key: Hashable, load: Callable[[], Awaitable[V]]) -> V:
        try:
            value = await load()
        finally:
            del self._inflight[key]
        self._entries[key] = (self._clock() + self._ttl, value)
        if len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        return value
