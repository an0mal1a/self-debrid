import asyncio
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

from self_debrid.adapters.stremio.http import create_app
from self_debrid.application.cache import AsyncTTLCache
from self_debrid.application.metadata import MediaMetadata, MetadataResult
from self_debrid.application.search import SearchService
from self_debrid.domain import MediaRequest, MediaType, StreamCandidate
from self_debrid.domain.validation import exclusion_reason
from self_debrid.metadata.release_parser import parse_release
from self_debrid.providers.registry import ProviderRegistry
from self_debrid.providers.torznab import (
    TorznabCapabilities,
    TorznabProvider,
    build_search_params,
)

FIXTURES = Path(__file__).parent / "fixtures"
MOVIE = MediaRequest(MediaType.MOVIE, "tt1234567")
EPISODE = MediaRequest(MediaType.SERIES, "tt1234567", 2, 5)


def provider(name, caps, feed="reliability_results.xml"):
    value = TorznabProvider(name, "http://indexer.invalid/1/api", "private-key")

    async def fetch(params):
        return (FIXTURES / (caps if params["t"] == "caps" else feed)).read_bytes()

    value._fetch = AsyncMock(side_effect=fetch)
    return value


def resolver(request=MOVIE):
    class Resolver:
        resolve = AsyncMock(
            return_value=MetadataResult(
                MediaMetadata(
                    request.imdb_id,
                    "Example Movie"
                    if request.media_type is MediaType.MOVIE
                    else "Example Show",
                    request.media_type,
                    2024 if request.media_type is MediaType.MOVIE else 2019,
                )
            )
        )

    return Resolver()


class SearchStrategyTests(unittest.IsolatedAsyncioTestCase):
    async def test_imdb_capable_provider_never_calls_metadata(self):
        value = provider("imdb", "caps_imdb.xml")
        metadata = resolver()
        result = await SearchService(
            ProviderRegistry([value]), metadata=metadata
        ).search(MOVIE)
        metadata.resolve.assert_not_awaited()
        self.assertEqual(
            value._fetch.await_args.args[0], {"t": "movie", "imdbid": "1234567"}
        )
        self.assertTrue(result.candidates)
        self.assertEqual(dict(result.exclusions)["identity_mismatch"], 1)

    async def test_title_only_provider_resolves_title_year_and_filters_mismatches(self):
        value = provider("title", "caps_title.xml")
        metadata = resolver()
        result = await SearchService(
            ProviderRegistry([value]), metadata=metadata
        ).search(MOVIE)
        metadata.resolve.assert_awaited_once_with(MOVIE)
        self.assertEqual(
            value._fetch.await_args.args[0],
            {"t": "movie", "q": "Example Movie", "year": "2024"},
        )
        self.assertEqual(
            [candidate.info_hash for candidate in result.candidates], ["1" * 40]
        )
        self.assertEqual(
            dict(result.exclusions),
            {
                "title_mismatch": 1,
                "year_mismatch": 1,
                "wrong_media_type": 1,
                "identity_mismatch": 1,
            },
        )

    async def test_mixed_providers_prefer_imdb_and_share_one_metadata_lookup(self):
        values = [
            provider("imdb", "caps_imdb.xml"),
            provider("title1", "caps_title.xml"),
            provider("title2", "caps_title.xml"),
        ]
        metadata = resolver()
        result = await SearchService(
            ProviderRegistry(values), metadata=metadata
        ).search(MOVIE)
        self.assertEqual(metadata.resolve.await_count, 1)
        self.assertEqual(values[0]._fetch.await_args.args[0]["t"], "movie")
        self.assertIn("imdbid", values[0]._fetch.await_args.args[0])
        self.assertIn("q", values[1]._fetch.await_args.args[0])
        self.assertEqual(
            len({candidate.identity for candidate in result.candidates}),
            len(result.candidates),
        )
        self.assertEqual(result.failures, ())

    async def test_metadata_outage_does_not_discard_imdb_results_or_enter_discovery_cache(
        self,
    ):
        values = [
            provider("imdb", "caps_imdb.xml"),
            provider("title", "caps_title.xml"),
        ]
        metadata = resolver()
        metadata.resolve.side_effect = asyncio.TimeoutError()
        cache = AsyncTTLCache()
        service = SearchService(
            ProviderRegistry(values), metadata=metadata, cache=cache, cache_ttl=30
        )
        for _ in range(2):
            result = await service.search(MOVIE)
            self.assertTrue(result.candidates)
            self.assertEqual(result.failures[0].provider, "title")
            self.assertEqual(result.metadata_error, "timeout")
        self.assertEqual(cache._entries, {})
        self.assertEqual(values[0]._fetch.await_count, 3)

    async def test_disabled_metadata_and_unsupported_caps_never_call_resolver(self):
        value = provider("title", "caps_title.xml")
        result = await SearchService(ProviderRegistry([value])).search(MOVIE)
        self.assertEqual(result.failures[0].code, "unsupported_search")
        value = TorznabProvider(
            "none", "https://indexer.invalid/api", capabilities=TorznabCapabilities()
        )
        value._fetch = AsyncMock()
        metadata = resolver()
        result = await SearchService(
            ProviderRegistry([value]), metadata=metadata
        ).search(MOVIE)
        metadata.resolve.assert_not_awaited()
        self.assertEqual(result.failures[0].code, "unsupported_search")

    async def test_explicit_title_does_not_resolve_metadata(self):
        value = provider("title", "caps_title.xml")
        metadata = resolver()
        await SearchService(ProviderRegistry([value]), metadata=metadata).search(
            replace(MOVIE, title="Example Movie")
        )
        metadata.resolve.assert_not_awaited()

    async def test_episode_fallback_preserves_supported_coordinates(self):
        value = provider("title", "caps_title.xml")
        result = await SearchService(
            ProviderRegistry([value]), metadata=resolver(EPISODE)
        ).search(EPISODE)
        self.assertEqual(
            value._fetch.await_args.args[0],
            {"t": "tvsearch", "q": "Example Show", "season": "2", "ep": "5"},
        )
        self.assertEqual(
            [candidate.info_hash for candidate in result.candidates], ["4" * 40]
        )

    async def test_episode_text_label_without_coordinate_capabilities(self):
        value = TorznabProvider(
            "title",
            "https://indexer.invalid/api",
            capabilities=TorznabCapabilities(tv=frozenset({"q"})),
        )
        value._fetch = AsyncMock(
            return_value=(FIXTURES / "reliability_results.xml").read_bytes()
        )
        await SearchService(
            ProviderRegistry([value]), metadata=resolver(EPISODE)
        ).search(EPISODE)
        value._fetch.assert_awaited_once_with(
            {"t": "tvsearch", "q": "Example Show S02E05"}
        )

    async def test_metadata_invalid_identity_or_exception_is_sanitized(self):
        for result in (
            MetadataResult(MediaMetadata("tt9999999", "Wrong", MediaType.MOVIE)),
            RuntimeError("apikey=private-key"),
        ):
            value = provider("title", "caps_title.xml")
            metadata = resolver()
            if isinstance(result, Exception):
                metadata.resolve.side_effect = result
            else:
                metadata.resolve.return_value = result
            output = await SearchService(
                ProviderRegistry([value]), metadata=metadata
            ).search(MOVIE)
            self.assertIsNotNone(output.metadata_error)
            self.assertNotIn("private-key", repr(output))

    async def test_http_repeated_requests_use_title_fallback_cache_and_keep_stremio_contract(
        self,
    ):
        value = provider("title", "caps_title.xml")
        metadata = resolver()
        service = SearchService(
            ProviderRegistry([value]),
            metadata=metadata,
            cache=AsyncTTLCache(),
            cache_ttl=30,
        )
        async with TestClient(
            TestServer(create_app(service, secrets=("private-key",)))
        ) as client:
            responses = await asyncio.gather(
                *(client.get("/stream/movie/tt1234567.json") for _ in range(4))
            )
            for response in responses:
                data = await response.json()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.content_type, "application/json")
                self.assertEqual(set(data), {"streams"})
                self.assertEqual(data["streams"][0]["infoHash"], "1" * 40)
                self.assertNotIn("private-key", json.dumps(data))
        self.assertEqual(value._fetch.await_count, 2)
        self.assertEqual(metadata.resolve.await_count, 1)


class AccuracyTests(unittest.TestCase):
    def test_year_detection_does_not_invent_years_from_quality_or_numeric_titles(self):
        for title, year in (
            ("Example.Movie.2024.1080p", 2024),
            ("Movie.(1999).720p", 1999),
            ("1917.1080p", None),
            ("2001.A.Space.Odyssey.1968.1080p", 1968),
            ("Movie.1080p.2024kbps", None),
            ("Movie", None),
            ("Movie.S02E05", None),
        ):
            self.assertEqual(parse_release(title).year, year)
        self.assertEqual(parse_release("Movie.2020", {"year": "2024"}).year, 2024)

    def test_movie_id_year_and_title_evidence(self):
        request = replace(MOVIE, title="Example Movie", year=2024)
        base = StreamCandidate(
            "test", "Example.Movie.2024.1080p", info_hash="a" * 40, year=2024
        )
        self.assertIsNone(exclusion_reason(base, request))
        self.assertEqual(
            exclusion_reason(replace(base, imdb_id="tt9999999"), request),
            "identity_mismatch",
        )
        self.assertEqual(
            exclusion_reason(replace(base, year=1990), request), "year_mismatch"
        )
        self.assertEqual(
            exclusion_reason(replace(base, title="Unrelated.Film.2024.1080p"), request),
            "title_mismatch",
        )
        self.assertIsNone(
            exclusion_reason(
                replace(base, title="Localized.Title.2024", imdb_id=request.imdb_id),
                request,
            )
        )
        self.assertIsNone(
            exclusion_reason(replace(base, title="Unknown label", year=None), request)
        )
        self.assertIsNone(exclusion_reason(replace(base, year=2023), request))

    def test_accent_punctuation_and_numeric_titles_remain_permissive(self):
        value = StreamCandidate("test", "El.Cafe.2024.1080p", info_hash="a" * 40)
        self.assertIsNone(exclusion_reason(value, replace(MOVIE, title="El Café")))
        self.assertIsNone(
            exclusion_reason(
                replace(value, title="1917.2019.1080p"),
                replace(MOVIE, title="1917", year=2019),
            )
        )
        self.assertIsNone(parse_release("[GROUP] 1917.1080p").year)
        self.assertIsNone(
            exclusion_reason(
                replace(value, title="Blade.Runner.2049.1080p", year=2049),
                replace(MOVIE, title="Blade Runner 2049", year=2017),
            )
        )

    def test_queries_never_send_unadvertised_year(self):
        request = replace(MOVIE, title="Example Movie", year=2024)
        self.assertEqual(
            build_search_params(request, TorznabCapabilities(movie=frozenset({"q"}))),
            {"t": "movie", "q": "Example Movie"},
        )


class BoundedMetadataStrategyTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_metadata_has_overall_deadline_and_healthy_provider_survives(
        self,
    ):
        values = [
            provider("imdb", "caps_imdb.xml"),
            provider("title", "caps_title.xml"),
        ]
        metadata = resolver()
        cancelled = asyncio.Event()

        async def hang(request):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        metadata.resolve.side_effect = hang
        service = SearchService(
            ProviderRegistry(values), metadata=metadata, metadata_timeout=0.02
        )
        result = await asyncio.wait_for(service.search(MOVIE), 2)
        self.assertTrue(result.candidates)
        self.assertEqual(result.metadata_error, "timeout")
        self.assertTrue(cancelled.is_set())

    async def test_cancelled_search_cancels_unneeded_metadata(self):
        value = provider("title", "caps_title.xml")
        metadata = resolver()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def hang(request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        metadata.resolve.side_effect = hang
        service = SearchService(ProviderRegistry([value]), metadata=metadata)
        task = asyncio.create_task(service.search(MOVIE))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())

    async def test_full_http_bootstrap_with_multi_config_and_cinemeta_fixture(self):
        from unittest.mock import patch

        from self_debrid.infrastructure.bootstrap import build_search_service
        from self_debrid.infrastructure.config import AppConfig, TorznabSettings
        from self_debrid.infrastructure.reliability import MetadataSettings

        async def fetch(provider_self, params):
            if params["t"] == "caps":
                fixture = (
                    "caps_imdb.xml"
                    if provider_self.name == "imdb"
                    else "caps_title.xml"
                )
            else:
                fixture = "reliability_results.xml"
            return (FIXTURES / fixture).read_bytes()

        config = AppConfig(
            providers=(
                TorznabSettings("http://host/1/api", "secret-one", name="imdb"),
                TorznabSettings("http://host/2/api", "secret-two", name="title"),
            ),
            metadata=MetadataSettings(enabled=True),
        )
        with (
            patch("self_debrid.providers.torznab.TorznabProvider._fetch", new=fetch),
            patch(
                "self_debrid.metadata.cinemeta.CinemetaResolver._fetch",
                AsyncMock(return_value=(FIXTURES / "cinemeta_movie.json").read_bytes()),
            ) as meta_fetch,
        ):
            service = build_search_service(config)
            async with TestClient(
                TestServer(create_app(service, secrets=("secret-one", "secret-two")))
            ) as client:
                for _ in range(2):
                    response = await client.get("/stream/movie/tt1234567.json")
                    data = await response.json()
                    self.assertEqual(response.status, 200)
                    self.assertTrue(data["streams"])
                    self.assertNotIn("secret-", json.dumps(data))
                self.assertEqual(meta_fetch.await_count, 1)
                doctor_response = await client.get("/doctor")
                self.assertEqual(doctor_response.status, 404)

    async def test_bracketed_release_group_and_article_differences_are_tolerated(self):
        request = replace(MOVIE, title="The Example Movie", year=2024)
        value = StreamCandidate(
            "test", "[GROUP] Example.Movie.2024.1080p", info_hash="a" * 40, year=2024
        )
        self.assertIsNone(exclusion_reason(value, request))

    async def test_metadata_already_resolved_for_fallback_validates_all_provider_results(
        self,
    ):
        values = [
            provider("imdb", "caps_imdb.xml"),
            provider("title", "caps_title.xml"),
        ]
        result = await SearchService(
            ProviderRegistry(values), metadata=resolver()
        ).search(MOVIE)
        self.assertEqual([value.info_hash for value in result.candidates], ["1" * 40])
