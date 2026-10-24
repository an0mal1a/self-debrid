import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from self_debrid.application.metadata import MediaMetadata
from self_debrid.domain import MediaRequest, MediaType
from self_debrid.metadata.cinemeta import (
    MAX_RESPONSE_BYTES,
    CinemetaResolver,
    parse_metadata,
)

FIXTURES = Path(__file__).parent / "fixtures"
MOVIE = MediaRequest(MediaType.MOVIE, "tt1234567")
SERIES = MediaRequest(MediaType.SERIES, "tt1234567", 2, 5)


class MetadataParsingTests(unittest.TestCase):
    def test_movie_canonical_title_type_id_and_year(self):
        result = parse_metadata((FIXTURES / "cinemeta_movie.json").read_bytes(), MOVIE)
        self.assertEqual(
            result.metadata,
            MediaMetadata("tt1234567", "Example Movie", MediaType.MOVIE, 2024),
        )
        self.assertIsNone(result.error)

    def test_series_release_range_uses_start_year(self):
        result = parse_metadata(
            (FIXTURES / "cinemeta_series.json").read_bytes(), SERIES
        )
        self.assertEqual(
            result.metadata,
            MediaMetadata("tt1234567", "Example Show", MediaType.SERIES, 2019),
        )

    def test_identity_and_type_mismatch_are_rejected(self):
        for data in (
            (FIXTURES / "cinemeta_invalid.json").read_bytes(),
            json.dumps(
                {"meta": {"id": "tt1234567", "type": "series", "name": "Title"}}
            ).encode(),
            json.dumps(
                {
                    "meta": {
                        "id": "tt1234567",
                        "imdb_id": "tt9999999",
                        "type": "movie",
                        "name": "Title",
                    }
                }
            ).encode(),
        ):
            self.assertEqual(parse_metadata(data, MOVIE).error, "identity_mismatch")

    def test_invalid_json_shapes_and_title(self):
        for data in (
            b"bad json",
            b"<html/>",
            b"[]",
            b"null",
            b"{}",
            b'{"meta":null}',
            b'{"meta":[]}',
            b"\xff",
        ):
            self.assertIsNone(parse_metadata(data, MOVIE).metadata)
        for title in (None, 3, [], "", " ", "a" * 301, "Title\x00"):
            data = json.dumps(
                {"meta": {"id": "tt1234567", "type": "movie", "name": title}}
            ).encode()
            self.assertEqual(parse_metadata(data, MOVIE).error, "invalid_response")

    def test_unknown_year_stays_unknown_and_released_date_is_validated(self):
        payload = {
            "meta": {
                "id": "tt1234567",
                "type": "movie",
                "name": "Title",
                "releaseInfo": "unknown",
            }
        }
        self.assertIsNone(
            parse_metadata(json.dumps(payload).encode(), MOVIE).metadata.year
        )
        payload["meta"]["released"] = "2023-06-07T00:00:00Z"
        self.assertEqual(
            parse_metadata(json.dumps(payload).encode(), MOVIE).metadata.year, 2023
        )
        payload["meta"]["released"] = "2023-99-99"
        self.assertIsNone(
            parse_metadata(json.dumps(payload).encode(), MOVIE).metadata.year
        )

    def test_payload_size_bound(self):
        self.assertEqual(
            parse_metadata(b" " * (MAX_RESPONSE_BYTES + 1), MOVIE).error,
            "response_too_large",
        )


class CinemetaResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_positive_cache_and_different_media_types(self):
        resolver = CinemetaResolver()
        resolver._fetch = AsyncMock(
            side_effect=[
                (FIXTURES / "cinemeta_movie.json").read_bytes(),
                (FIXTURES / "cinemeta_series.json").read_bytes(),
            ]
        )
        first = await resolver.resolve(MOVIE)
        self.assertEqual(first, await resolver.resolve(MOVIE))
        self.assertEqual(resolver._fetch.await_count, 1)
        self.assertEqual(
            (await resolver.resolve(SERIES)).metadata.title, "Example Show"
        )
        self.assertEqual(
            resolver._fetch.await_args_list[0].args,
            ("https://v3-cinemeta.strem.io/meta/movie/tt1234567.json",),
        )
        self.assertEqual(
            resolver._fetch.await_args_list[1].args,
            ("https://v3-cinemeta.strem.io/meta/series/tt1234567.json",),
        )

    async def test_negative_cache_and_expiration(self):
        resolver = CinemetaResolver(negative_ttl=10)
        now = [100.0]
        resolver._cache._clock = lambda: now[0]
        resolver._fetch = AsyncMock(side_effect=asyncio.TimeoutError())
        self.assertEqual((await resolver.resolve(MOVIE)).error, "timeout")
        self.assertEqual((await resolver.resolve(MOVIE)).error, "timeout")
        self.assertEqual(resolver._fetch.await_count, 1)
        now[0] += 11
        await resolver.resolve(MOVIE)
        self.assertEqual(resolver._fetch.await_count, 2)

    async def test_invalid_identity_is_negative_cached(self):
        resolver = CinemetaResolver()
        resolver._fetch = AsyncMock(
            return_value=(FIXTURES / "cinemeta_invalid.json").read_bytes()
        )
        self.assertEqual((await resolver.resolve(MOVIE)).error, "identity_mismatch")
        await resolver.resolve(MOVIE)
        self.assertEqual(resolver._fetch.await_count, 1)

    async def test_strict_imdb_ids_do_not_reach_network(self):
        resolver = CinemetaResolver()
        resolver._fetch = AsyncMock()
        self.assertEqual(
            (await resolver.resolve(MediaRequest(MediaType.MOVIE, "tt123"))).error,
            "invalid_id",
        )
        self.assertEqual((await resolver.resolve(None)).error, "invalid_id")
        resolver._fetch.assert_not_awaited()

    async def test_connection_errors_are_sanitized(self):
        for error, code in (
            (
                aiohttp.ClientConnectionError("https://secret.invalid/?apikey=secret"),
                "connection_error",
            ),
            (RuntimeError("secret"), "metadata_error"),
        ):
            resolver = CinemetaResolver()
            resolver._fetch = AsyncMock(side_effect=error)
            result = await resolver.resolve(MOVIE)
            self.assertEqual(result.error, code)
            self.assertNotIn("secret", repr(result))

    async def test_concurrent_metadata_lookups_share_work(self):
        resolver = CinemetaResolver()

        async def fetch(url):
            await asyncio.sleep(0.01)
            return (FIXTURES / "cinemeta_movie.json").read_bytes()

        resolver._fetch = AsyncMock(side_effect=fetch)
        results = await asyncio.gather(*(resolver.resolve(MOVIE) for _ in range(5)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(resolver._fetch.await_count, 1)

    def transport(self, chunks=(), status=200, error=None):
        async def iterate(size):
            for chunk in chunks:
                yield chunk

        response = MagicMock(status=status)
        response.content.iter_chunked = iterate
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response, side_effect=error)
        context.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.get.return_value = context
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        return factory, session

    async def test_transport_has_explicit_deadlines_and_no_redirects(self):
        resolver = CinemetaResolver(connect_timeout=2, read_timeout=4)
        factory, session = self.transport(
            [(FIXTURES / "cinemeta_movie.json").read_bytes()]
        )
        with patch("self_debrid.metadata.cinemeta.aiohttp.ClientSession", factory):
            self.assertIsNotNone((await resolver.resolve(MOVIE)).metadata)
        timeout = factory.call_args.kwargs["timeout"]
        self.assertEqual((timeout.connect, timeout.sock_read, timeout.total), (2, 4, 6))
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])

    async def test_transport_status_size_and_read_timeout_fail_safely(self):
        for chunks, status, error, code in (
            ((), 404, None, "not_found"),
            ((), 500, None, "http_error"),
            ((), 302, None, "http_error"),
            ((b"x" * MAX_RESPONSE_BYTES, b"x"), 200, None, "response_too_large"),
            ((), 200, asyncio.TimeoutError(), "timeout"),
        ):
            resolver = CinemetaResolver()
            factory, _ = self.transport(chunks, status, error)
            with patch("self_debrid.metadata.cinemeta.aiohttp.ClientSession", factory):
                self.assertEqual((await resolver.resolve(MOVIE)).error, code)

    async def test_metadata_concurrency_and_entry_limits_remain_bounded(self):
        resolver = CinemetaResolver(max_entries=2)
        active = 0
        peak = 0

        async def fetch(url):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.01)
                imdb = url.rsplit("/", 1)[-1].removesuffix(".json")
                return json.dumps(
                    {"meta": {"id": imdb, "type": "movie", "name": "Title"}}
                ).encode()
            finally:
                active -= 1

        resolver._fetch = AsyncMock(side_effect=fetch)
        values = await asyncio.gather(
            *(
                resolver.resolve(MediaRequest(MediaType.MOVIE, f"tt12345{i:02d}"))
                for i in range(12)
            )
        )
        self.assertLessEqual(peak, 8)
        self.assertLessEqual(len(resolver._cache._entries), 2)
        self.assertTrue(any(value.error == "busy" for value in values))
