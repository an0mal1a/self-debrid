import asyncio
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from self_debrid.application.cache import AsyncTTLCache, CacheBusy
from self_debrid.application.search import SearchService
from self_debrid.domain import MediaRequest, MediaType, StreamCandidate
from self_debrid.domain.ranking import RankingPreferences
from self_debrid.providers.registry import ProviderRegistry

REQUEST = MediaRequest(MediaType.MOVIE, "tt1234567")


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_expiration_entry_limit_and_lru(self):
        now = [0.0]
        cache = AsyncTTLCache(max_entries=2, clock=lambda: now[0])
        factory = AsyncMock(return_value="value")

        async def get(key):
            return await cache.get(key, factory, ttl=lambda _: 10, weight=lambda _: 1)

        await get("a")
        await get("b")
        await get("a")
        self.assertEqual(factory.await_count, 2)
        await get("c")
        self.assertEqual(list(cache._entries), ["a", "c"])
        await get("b")
        self.assertEqual(factory.await_count, 4)
        now[0] = 11
        await get("b")
        self.assertEqual(factory.await_count, 5)
        self.assertEqual(cache._bytes, 1)

    async def test_byte_limit_and_oversized_values_are_not_cached(self):
        cache = AsyncTTLCache(max_bytes=5)
        factory = AsyncMock(return_value="value")
        for key in ("a", "b"):
            await cache.get(key, factory, ttl=lambda _: 10, weight=lambda _: 4)
        self.assertEqual(len(cache._entries), 1)
        self.assertEqual(cache._bytes, 4)
        for _ in range(2):
            await cache.get("huge", factory, ttl=lambda _: 10, weight=lambda _: 6)
        self.assertEqual(factory.await_count, 4)
        self.assertNotIn("huge", cache._entries)

    async def test_inflight_limit_and_shared_key(self):
        cache = AsyncTTLCache(max_inflight=1)
        started = asyncio.Event()
        release = asyncio.Event()

        async def factory():
            started.set()
            await release.wait()
            return "done"

        first = asyncio.create_task(
            cache.get("one", factory, ttl=lambda _: 0, weight=lambda _: 1)
        )
        await started.wait()
        second = asyncio.create_task(
            cache.get("one", factory, ttl=lambda _: 0, weight=lambda _: 1)
        )
        with self.assertRaises(CacheBusy):
            await cache.get("other", factory, ttl=lambda _: 0, weight=lambda _: 1)
        release.set()
        self.assertEqual(await asyncio.gather(first, second), ["done", "done"])
        self.assertEqual(cache._flights, {})

    async def test_one_cancelled_waiter_does_not_cancel_others(self):
        cache = AsyncTTLCache()
        started = asyncio.Event()
        release = asyncio.Event()
        factory = AsyncMock()

        async def work():
            started.set()
            await release.wait()
            return "done"

        factory.side_effect = work

        async def get():
            return await cache.get("key", factory, ttl=lambda _: 10, weight=lambda _: 1)

        first = asyncio.create_task(get())
        await started.wait()
        second = asyncio.create_task(get())
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()
        self.assertEqual(await second, "done")
        self.assertEqual(await get(), "done")
        self.assertEqual(factory.await_count, 1)

    async def test_last_cancelled_waiter_cancels_work_and_allows_retry(self):
        cache = AsyncTTLCache()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def work():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        first = asyncio.create_task(
            cache.get("key", work, ttl=lambda _: 10, weight=lambda _: 1)
        )
        await started.wait()
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertTrue(cancelled.is_set())
        self.assertEqual(cache._flights, {})
        self.assertEqual(cache._entries, {})
        self.assertEqual(
            await cache.get(
                "key",
                AsyncMock(return_value="retry"),
                ttl=lambda _: 10,
                weight=lambda _: 1,
            ),
            "retry",
        )

    async def test_exceptions_are_shared_but_never_cached(self):
        cache = AsyncTTLCache()
        factory = AsyncMock(side_effect=ValueError("failure"))
        for _ in range(2):
            with self.assertRaises(ValueError):
                await cache.get("key", factory, ttl=lambda _: 10, weight=lambda _: 1)
        self.assertEqual(factory.await_count, 2)
        self.assertEqual(cache._entries, {})
        self.assertEqual(cache._flights, {})


class SearchCacheTests(unittest.IsolatedAsyncioTestCase):
    def service(self, behavior=None, **options):
        class Provider:
            name = "test"

        provider = Provider()
        provider.search = (
            AsyncMock(side_effect=behavior)
            if behavior
            else AsyncMock(
                return_value=[StreamCandidate("test", "Movie", info_hash="a" * 40)]
            )
        )
        cache = options.pop("cache", AsyncTTLCache())
        return (
            SearchService(
                ProviderRegistry([provider]), cache=cache, cache_ttl=30, **options
            ),
            provider,
            cache,
        )

    async def test_repeated_and_simultaneous_searches_share_work_without_download_cache_flag(
        self,
    ):
        async def behavior(request):
            await asyncio.sleep(0.01)
            return [StreamCandidate("test", "Movie", info_hash="a" * 40)]

        service, provider, _ = self.service(behavior)
        results = await asyncio.gather(*(service.search(REQUEST) for _ in range(5)))
        self.assertTrue(all(value == results[0] for value in results))
        self.assertEqual(provider.search.await_count, 1)
        self.assertEqual(await service.search(REQUEST), results[0])
        self.assertEqual(provider.search.await_count, 1)
        self.assertFalse(results[0].candidates[0].cached)

    async def test_failure_and_partial_failures_are_not_cached(self):
        service, provider, cache = self.service()
        provider.search.side_effect = RuntimeError("private-key")
        for _ in range(2):
            result = await service.search(REQUEST)
            self.assertEqual(result.failures[0].code, "provider_error")
        self.assertEqual(provider.search.await_count, 2)
        self.assertEqual(cache._entries, {})

        class Bad:
            name = "bad"

            async def search(self, request):
                raise RuntimeError("key")

        provider.search.side_effect = None
        provider.search.return_value = [
            StreamCandidate("test", "Movie", info_hash="a" * 40)
        ]
        service._registry.register(Bad())
        for _ in range(2):
            result = await service.search(REQUEST)
            self.assertEqual(len(result.candidates), 1)
        self.assertEqual(provider.search.await_count, 4)
        self.assertEqual(cache._entries, {})

    async def test_keys_separate_request_preferences_and_configuration(self):
        shared = AsyncTTLCache()
        one, _, _ = self.service(cache=shared, cache_namespace="config-one")
        two, provider, _ = self.service(cache=shared, cache_namespace="config-two")
        await one.search(REQUEST)
        await two.search(REQUEST)
        self.assertEqual(provider.search.await_count, 1)
        await two.search(replace(REQUEST, title="Explicit title"))
        await two.search(replace(REQUEST, year=2024))
        self.assertEqual(provider.search.await_count, 3)
        another = SearchService(
            two._registry,
            RankingPreferences(min_seeders=4),
            cache=shared,
            cache_ttl=30,
            cache_namespace="config-two",
        )
        await another.search(REQUEST)
        self.assertEqual(provider.search.await_count, 4)

    async def test_successful_empty_filter_result_has_short_ttl(self):
        now = [0.0]
        cache = AsyncTTLCache(clock=lambda: now[0])
        service, provider, _ = self.service(cache=cache)
        provider.search.return_value = [
            StreamCandidate("test", "Show.S02E05", info_hash="a" * 40)
        ]
        result = await service.search(REQUEST)
        self.assertEqual(result.candidates, ())
        self.assertEqual(dict(result.exclusions), {"wrong_media_type": 1})
        self.assertEqual(await service.search(REQUEST), result)
        self.assertEqual(provider.search.await_count, 1)
        now[0] = 31
        await service.search(REQUEST)
        self.assertEqual(provider.search.await_count, 2)

    async def test_global_registry_concurrency_is_shared_across_searches(self):
        active = 0
        peak = 0

        class Provider:
            name = "test"

            async def search(self, request):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                try:
                    await asyncio.sleep(0.01)
                    return []
                finally:
                    active -= 1

        service = SearchService(ProviderRegistry([Provider()], max_concurrency=2))
        await asyncio.gather(
            *(
                service.search(replace(REQUEST, imdb_id=f"tt123456{i}"))
                for i in range(8)
            )
        )
        self.assertEqual(peak, 2)

    async def test_search_busy_is_a_structured_failure(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def behavior(request):
            started.set()
            await release.wait()
            return []

        service, _, _ = self.service(behavior, cache=AsyncTTLCache(max_inflight=1))
        task = asyncio.create_task(service.search(REQUEST))
        await started.wait()
        busy = await service.search(replace(REQUEST, imdb_id="tt9999999"))
        self.assertEqual(busy.failures[0].code, "busy")
        release.set()
        await task

    async def test_discovery_cancelled_waiter_does_not_cancel_other_clients(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def behavior(request):
            started.set()
            await release.wait()
            return [StreamCandidate("test", "Movie", info_hash="a" * 40)]

        service, provider, _ = self.service(behavior)
        first = asyncio.create_task(service.search(REQUEST))
        await started.wait()
        second = asyncio.create_task(service.search(REQUEST))
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()
        self.assertTrue((await second).candidates)
        self.assertEqual(provider.search.await_count, 1)
