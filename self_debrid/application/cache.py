"""Small bounded TTL store with cancellation-safe shared async work."""

from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, Hashable, TypeVar

T = TypeVar("T")


class CacheBusy(Exception):
    """The bounded number of distinct in-flight requests has been reached."""


@dataclass
class _Flight(Generic[T]):
    task: asyncio.Task[T]
    waiters: int = 0


class AsyncTTLCache(Generic[T]):
    def __init__(
        self,
        *,
        max_entries: int = 128,
        max_bytes: int = 8 * 1024 * 1024,
        max_inflight: int = 32,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if any(
            type(value) is not int or value < 1
            for value in (max_entries, max_bytes, max_inflight)
        ):
            raise ValueError("cache limits must be positive integers")
        self._entries: OrderedDict[Hashable, tuple[float, T, int]] = OrderedDict()
        self._flights: dict[Hashable, _Flight[T]] = {}
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._max_inflight = max_inflight
        self._bytes = 0
        self._clock = clock

    async def get(
        self,
        key: Hashable,
        factory: Callable[[], Awaitable[T]],
        *,
        ttl: Callable[[T], float],
        weight: Callable[[T], int],
    ) -> T:
        now = self._clock()
        for expired in [
            key for key, (until, _, _) in self._entries.items() if until <= now
        ]:
            self._bytes -= self._entries.pop(expired)[2]
        if key in self._entries:
            self._entries.move_to_end(key)
            return self._entries[key][1]
        flight = self._flights.get(key)
        if flight is None:
            if len(self._flights) >= self._max_inflight:
                raise CacheBusy("Too many active lookups")

            async def run() -> T:
                value = await factory()
                lifetime = ttl(value)
                size = weight(value)
                if (
                    math.isfinite(lifetime)
                    and lifetime > 0
                    and 0 <= size <= self._max_bytes
                ):
                    while self._entries and (
                        len(self._entries) >= self._max_entries
                        or self._bytes + size > self._max_bytes
                    ):
                        self._bytes -= self._entries.popitem(last=False)[1][2]
                    self._entries[key] = (self._clock() + lifetime, value, size)
                    self._bytes += size
                return value

            flight = _Flight(asyncio.create_task(run()))
            self._flights[key] = flight
        flight.waiters += 1
        try:
            return await asyncio.shield(flight.task)
        finally:
            flight.waiters -= 1
            if flight.waiters == 0:
                if self._flights.get(key) is flight:
                    del self._flights[key]
                if not flight.task.done():
                    flight.task.cancel()
                await asyncio.gather(flight.task, return_exceptions=True)
