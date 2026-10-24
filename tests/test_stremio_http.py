import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from self_debrid.adapters.stremio.http import create_app, serve
from self_debrid.application.search import SearchService
from self_debrid.domain import (
    DiscoveryResult,
    MediaRequest,
    MediaType,
    ProviderFailure,
    StreamCandidate,
)
from self_debrid.providers.registry import ProviderRegistry
from self_debrid.providers.torznab import TorznabProvider

FIXTURES = Path(__file__).parent / "fixtures"
HASH = "1234567890abcdef1234567890abcdef12345678"
SECRET = "provider-key-do-not-leak"


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = SearchService(ProviderRegistry())
        self.service.search = AsyncMock(return_value=DiscoveryResult(()))
        self.client = TestClient(
            TestServer(create_app(self.service, secrets=(SECRET,)))
        )
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_manifest_schema_content_type_and_cors(self):
        response = await self.client.get("/manifest.json")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "application/json")
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        data = await response.json()
        self.assertEqual(data["resources"][0]["name"], "stream")
        self.assertEqual(data["types"], ["movie", "series"])
        self.assertEqual(data["catalogs"], [])
        self.assertRegex(data["version"], r"^2\.0\.0-dev\.2$")
        self.assertNotIn(SECRET, json.dumps(data))
        self.service.search.assert_not_awaited()

    async def test_health_is_liveness_without_upstream_access(self):
        response = await self.client.get("/health")
        self.assertEqual(response.content_type, "application/json")
        self.assertEqual(
            await response.json(), {"status": "ok", "mode": "stremio-native-torrent"}
        )
        self.service.search.assert_not_awaited()

    async def test_movie_endpoint_uses_media_request_and_serializes_candidates(self):
        self.service.search.return_value = DiscoveryResult(
            (StreamCandidate("test", "Movie.1080p", info_hash=HASH, file_index=0),)
        )
        response = await self.client.get("/stream/movie/tt1234567.json")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "application/json")
        data = await response.json()
        self.assertEqual(list(data), ["streams"])
        self.assertEqual(data["streams"][0]["infoHash"], HASH)
        self.assertEqual(data["streams"][0]["fileIdx"], 0)
        self.service.search.assert_awaited_once_with(
            MediaRequest(MediaType.MOVIE, "tt1234567")
        )

    async def test_series_endpoint_accepts_colon_and_url_encoded_identifiers(self):
        value = StreamCandidate(
            "test", "Show.S02E05.1080p", info_hash=HASH, file_index=2
        )
        self.service.search.return_value = DiscoveryResult((value,))
        for raw in ("tt1234567:2:5", "tt1234567%3A2%3A5"):
            response = await self.client.get("/stream/series/" + raw + ".json")
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["streams"][0]["fileIdx"], 2)
            self.service.search.assert_awaited_with(
                MediaRequest(MediaType.SERIES, "tt1234567", 2, 5)
            )

    async def test_invalid_identifiers_are_sanitized_client_errors_without_search(self):
        for path in (
            "/stream/movie/not-an-id.json",
            "/stream/movie/tt123:2:5.json",
            "/stream/series/tt123.json",
            "/stream/series/tt123:2:-1.json",
            "/stream/series/tt123:x:5.json",
            "/stream/other/tt123.json",
            "/stream/movie/tt" + "1" * 200 + ".json",
            "/stream/movie/" + SECRET + ".json",
        ):
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertEqual(response.status, 400)
                self.assertEqual(response.content_type, "application/json")
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
                data = await response.json()
                self.assertEqual(data["error"], "invalid_id")
                self.assertNotIn(SECRET, json.dumps(data))
        self.service.search.assert_not_awaited()

    async def test_empty_results_are_valid_even_when_all_providers_fail(self):
        for failures in (
            (),
            (ProviderFailure("provider", SECRET, "connection_error"),),
        ):
            self.service.search.return_value = DiscoveryResult((), failures)
            response = await self.client.get("/stream/movie/tt123.json")
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), {"streams": []})

    async def test_provider_failure_diagnostics_do_not_enter_responses_or_logs(self):
        candidate = StreamCandidate("test", "Movie", info_hash=HASH)
        self.service.search.return_value = DiscoveryResult(
            (candidate,), (ProviderFailure(SECRET, SECRET),)
        )
        with self.assertLogs(
            "self_debrid.adapters.stremio.http", "WARNING"
        ) as captured:
            response = await self.client.get("/stream/movie/tt123.json")
            data = await response.json()
        self.assertEqual(len(data["streams"]), 1)
        self.assertNotIn(SECRET, json.dumps(data) + repr(captured.output))

    async def test_credentials_and_download_locators_are_not_exposed(self):
        values = (
            StreamCandidate(
                SECRET,
                "Movie " + SECRET + " https://private.invalid/get?passkey=hidden",
                info_hash=HASH,
                url="https://private.invalid/download?apikey=" + SECRET,
            ),
            StreamCandidate(
                "test",
                "Download only",
                url="https://private.invalid/file.torrent?apikey=" + SECRET,
            ),
            StreamCandidate("test", "V2 only", info_hash="ab" * 32),
        )
        self.service.search.return_value = DiscoveryResult(values)
        response = await self.client.get("/stream/movie/tt123.json?apikey=" + SECRET)
        data = await response.json()
        self.assertEqual(len(data["streams"]), 1)
        for value in (
            SECRET,
            "private.invalid",
            "passkey=",
            "apikey=",
            '"url"',
            '"sources"',
        ):
            self.assertNotIn(value, json.dumps(data))

    async def test_series_response_rejects_mismatch_and_unindexed_pack(self):
        self.service.search.return_value = DiscoveryResult(
            tuple(
                StreamCandidate("test", title, info_hash=str(index) * 40)
                for index, title in enumerate(
                    ("Show.S02E05", "Show.S02E06", "Show.S02.Complete")
                )
            )
        )
        response = await self.client.get("/stream/series/tt123:2:5.json")
        streams = (await response.json())["streams"]
        self.assertEqual(len(streams), 1)
        self.assertNotIn("fileIdx", streams[0])
        self.assertIn("largest file", streams[0]["description"])

    async def test_options_head_not_found_and_method_errors_have_protocol_headers(self):
        options = await self.client.options("/manifest.json")
        self.assertEqual(options.status, 204)
        self.assertEqual(options.headers["Access-Control-Allow-Origin"], "*")
        self.assertIn("GET", options.headers["Access-Control-Allow-Methods"])
        head = await self.client.head("/manifest.json")
        self.assertEqual(head.status, 200)
        self.assertEqual(head.content_type, "application/json")
        self.assertEqual(await head.read(), b"")
        for method, path, status in (
            ("get", "/unknown", 404),
            ("post", "/manifest.json", 405),
        ):
            response = await getattr(self.client, method)(path)
            self.assertEqual(response.status, status)
            self.assertEqual(response.content_type, "application/json")
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")

    async def test_unexpected_errors_are_sanitized(self):
        self.service.search.side_effect = RuntimeError(
            "https://provider.invalid/?apikey=" + SECRET
        )
        with self.assertLogs("self_debrid.adapters.stremio.http", "ERROR") as captured:
            response = await self.client.get("/stream/movie/tt123.json")
            data = await response.json()
        self.assertEqual(response.status, 500)
        self.assertEqual(data, {"error": "internal_error"})
        self.assertNotIn(SECRET, str(data) + repr(captured.output))


class HttpPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_torznab_to_http_pipeline_uses_distinct_movie_and_tv_queries(
        self,
    ):
        for media_type, raw_id, expected_mode in (
            ("movie", "tt123", "movie"),
            ("series", "tt123:2:5", "tvsearch"),
        ):
            provider = TorznabProvider(
                "fixture", "http://prowlarr.invalid/1/api", SECRET
            )
            provider._fetch = AsyncMock(
                side_effect=[
                    (FIXTURES / "caps.xml").read_bytes(),
                    (FIXTURES / "mixed_items.xml").read_bytes(),
                ]
            )
            service = SearchService(ProviderRegistry([provider]))
            async with TestClient(
                TestServer(create_app(service, secrets=(SECRET,)))
            ) as client:
                with self.assertLogs("self_debrid.providers.torznab", "WARNING"):
                    response = await client.get(f"/stream/{media_type}/{raw_id}.json")
                    data = await response.json()
                self.assertEqual(response.status, 200)
                self.assertEqual(len(data["streams"]), 1)
                query = provider._fetch.await_args_list[-1].args[0]
                self.assertEqual(query["t"], expected_mode)
                self.assertEqual(query["imdbid"], "123")
                if media_type == "series":
                    self.assertEqual((query["season"], query["ep"]), ("2", "5"))
                    self.assertEqual(data["streams"][0]["fileIdx"], 2)
                self.assertNotIn("private-key", json.dumps(data))

    async def test_timed_out_provider_preserves_healthy_http_streams(self):
        class Slow:
            name = "slow"

            async def search(self, request):
                await asyncio.Event().wait()

        class Healthy:
            name = "healthy"

            async def search(self, request):
                return [StreamCandidate(self.name, "Movie", info_hash=HASH)]

        service = SearchService(
            ProviderRegistry([Slow(), Healthy()], provider_timeout=0.02)
        )
        async with TestClient(TestServer(create_app(service))) as client:
            response = await asyncio.wait_for(
                client.get("/stream/movie/tt123.json"), timeout=2
            )
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["streams"][0]["infoHash"], HASH)

    async def test_serve_disables_access_logs_and_enables_cancellation(self):
        app = create_app(SearchService(ProviderRegistry()))
        with patch("self_debrid.adapters.stremio.http.web.run_app") as run:
            serve(app, host="127.0.0.1", port=8080)
        run.assert_called_once_with(
            app,
            host="127.0.0.1",
            port=8080,
            access_log=None,
            print=None,
            handler_cancellation=True,
        )

    async def test_pack_first_episode_is_not_a_file_mapping(self):
        provider = TorznabProvider("fixture", "http://prowlarr.invalid/1/api")
        feed = (FIXTURES / "episode_items.xml").read_bytes()
        provider._fetch = AsyncMock(
            side_effect=[(FIXTURES / "caps.xml").read_bytes(), feed, feed]
        )
        service = SearchService(ProviderRegistry([provider]))
        async with TestClient(TestServer(create_app(service))) as client:
            response = await client.get("/stream/series/tt123:2:1.json")
            self.assertEqual(await response.json(), {"streams": []})
            response = await client.get("/stream/series/tt123:2:5.json")
            streams = (await response.json())["streams"]
            self.assertEqual(
                {stream["infoHash"] for stream in streams}, {"1" * 40, "3" * 40}
            )
            mapped = next(
                stream for stream in streams if stream["infoHash"] == "3" * 40
            )
            self.assertEqual(mapped["fileIdx"], 4)
