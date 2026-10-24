import asyncio
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

from self_debrid.application.search import SearchService
from self_debrid.domain import MediaRequest, MediaType, StreamCandidate
from self_debrid.domain.ranking import RankingPreferences
from self_debrid.providers.base import ProviderError
from self_debrid.providers.registry import ProviderRegistry
from self_debrid.providers.torznab import TorznabProvider

REQUEST = MediaRequest(MediaType.MOVIE, "tt1234567")
FIXTURES = Path(__file__).parent / "fixtures"


class StubProvider:
    def __init__(self, name, behavior):
        self.name = name
        self.search = AsyncMock(side_effect=behavior)


class RegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_registry_returns_empty_result(self):
        result = await ProviderRegistry().search(REQUEST)
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.failures, ())

    async def test_duplicate_and_empty_names_rejected_including_constructor(self):
        provider = StubProvider("one", lambda request: [])
        with self.assertRaises(ValueError):
            ProviderRegistry([provider, provider])
        with self.assertRaises(ValueError):
            ProviderRegistry([StubProvider(" ", lambda request: [])])
        registry = ProviderRegistry([provider])
        with self.assertRaises(ValueError):
            registry.register(provider)
        self.assertEqual(registry.providers, (provider,))

    async def test_invalid_execution_limits(self):
        for values in (
            {"max_concurrency": 0},
            {"max_concurrency": True},
            {"provider_timeout": 0},
            {"provider_timeout": float("inf")},
            {"provider_timeout": float("nan")},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ProviderRegistry(**values)

    async def test_timeouts_are_cancelled_and_healthy_candidates_survive(self):
        cancelled = asyncio.Event()

        async def slow(request):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        good = StreamCandidate("healthy", "Good", info_hash="a" * 40)
        providers = [
            StubProvider("slow", slow),
            StubProvider("healthy", lambda request: [good]),
            StubProvider("broken", RuntimeError("apikey=secret-do-not-leak")),
        ]
        result = await asyncio.wait_for(
            ProviderRegistry(providers, provider_timeout=0.02).search(REQUEST), 1
        )
        self.assertEqual(result.candidates, (good,))
        self.assertEqual(
            [failure.code for failure in result.failures], ["timeout", "provider_error"]
        )
        self.assertTrue(cancelled.is_set())
        self.assertNotIn("secret-do-not-leak", repr(result))

    async def test_concurrency_is_bounded(self):
        active = 0
        peak = 0

        async def behavior(request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.01)
                return []
            finally:
                active -= 1

        providers = [StubProvider(str(index), behavior) for index in range(8)]
        result = await ProviderRegistry(
            providers, max_concurrency=2, provider_timeout=5
        ).search(REQUEST)
        self.assertEqual(peak, 2)
        self.assertEqual(result.failures, ())
        self.assertEqual(active, 0)

    async def test_caller_cancellation_reaches_providers(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def behavior(request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        task = asyncio.create_task(
            ProviderRegistry([StubProvider("slow", behavior)]).search(REQUEST)
        )
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())

    async def test_invalid_provider_results_are_isolated(self):
        for value in (None, ["not a candidate"]):
            registry = ProviderRegistry(
                [StubProvider("invalid", lambda request: value)]
            )
            self.assertEqual(
                (await registry.search(REQUEST)).failures[0].code, "provider_error"
            )

    async def test_structured_provider_errors_preserve_safe_reason(self):
        result = await ProviderRegistry(
            [
                StubProvider(
                    "unsupported",
                    ProviderError("unsupported_search", "No supported search mode"),
                ),
            ]
        ).search(REQUEST)
        self.assertEqual(result.failures[0].code, "unsupported_search")
        self.assertEqual(result.failures[0].error, "No supported search mode")


class SearchServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_end_to_end_normalizes_deduplicates_filters_ranks_and_reports_errors(
        self,
    ):
        provider = TorznabProvider(
            "prowlarr", "http://prowlarr.invalid/1/api", "secret"
        )
        provider._fetch = AsyncMock(
            side_effect=[
                (FIXTURES / "caps.xml").read_bytes(),
                (FIXTURES / "results.xml").read_bytes(),
            ]
        )
        duplicate = StreamCandidate(
            "second",
            "Duplicate",
            info_hash="abcdef0123456789abcdef0123456789abcdef01",
            seeders=100,
            cached=True,
            languages=("en",),
        )
        oversized = StreamCandidate(
            "second", "Too big", info_hash="3" * 40, size_bytes=20_000_000_000
        )
        registry = ProviderRegistry(
            [
                provider,
                StubProvider("second", lambda request: [duplicate, oversized]),
                StubProvider("broken", RuntimeError("private URL")),
            ]
        )
        result = await SearchService(
            registry, RankingPreferences(max_size_bytes=10_000_000_000)
        ).search(REQUEST)
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(result.candidates[0].provider, "second")
        self.assertTrue(result.candidates[0].cached)
        self.assertEqual(result.candidates[0].seeders, 100)
        self.assertEqual(result.candidates[0].size_bytes, 8_000_000_000)
        self.assertEqual(result.candidates[0].resolution, "2160p")
        self.assertEqual(result.candidates[1].file_index, 5)
        self.assertEqual(result.failures[0].provider, "broken")

    async def test_completion_order_does_not_change_search_results(self):
        candidate = StreamCandidate("a", "Same", info_hash="a" * 40)

        async def delayed(request):
            await asyncio.sleep(0.005)
            return [candidate]

        a = StubProvider("a", delayed)
        b = StubProvider(
            "b", lambda request: [replace(candidate, provider="b", seeders=2)]
        )
        first = await SearchService(ProviderRegistry([a, b])).search(REQUEST)
        second = await SearchService(ProviderRegistry([b, a])).search(REQUEST)
        self.assertEqual(first, second)
