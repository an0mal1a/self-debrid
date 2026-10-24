import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import aiohttp
from yarl import URL

from self_debrid.domain import MediaRequest, MediaType
from self_debrid.providers.base import ProviderError
from self_debrid.providers.torznab import (
    MAX_XML_BYTES,
    TorznabCapabilities,
    TorznabProvider,
    build_search_params,
    parse_capabilities,
    parse_results,
)

FIXTURES = Path(__file__).parent / "fixtures"
MOVIE = MediaRequest(MediaType.MOVIE, "tt1234567")
EPISODE = MediaRequest(MediaType.SERIES, "tt1234567", 2, 5)


def fixture(name):
    return (FIXTURES / name).read_bytes()


def rss(item):
    return (
        '<rss xmlns:t="http://torznab.com/schemas/2015/feed"><channel>'
        + item
        + "</channel></rss>"
    ).encode()


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.caps = parse_capabilities(fixture("caps.xml"))

    def test_movie_uses_advertised_movie_imdb_search(self):
        self.assertEqual(
            build_search_params(MOVIE, self.caps), {"t": "movie", "imdbid": "1234567"}
        )

    def test_episode_uses_advertised_tv_imdb_and_coordinates(self):
        self.assertEqual(
            build_search_params(EPISODE, self.caps),
            {
                "t": "tvsearch",
                "imdbid": "1234567",
                "season": "2",
                "ep": "5",
            },
        )

    def test_text_only_capabilities_do_not_invent_imdb_support(self):
        caps = parse_capabilities(fixture("caps_text.xml"))
        with self.assertRaises(ProviderError) as caught:
            build_search_params(MOVIE, caps)
        self.assertEqual(caught.exception.code, "unsupported_search")
        request = MediaRequest(MediaType.MOVIE, "tt123", title="Title & Other / Más")
        self.assertEqual(
            build_search_params(request, caps), {"t": "movie", "q": request.title}
        )
        request = MediaRequest(MediaType.SERIES, "tt123", 2, 5, title="Show")
        self.assertEqual(
            build_search_params(request, caps),
            {"t": "tvsearch", "q": "Show", "season": "2", "ep": "5"},
        )

    def test_missing_tv_coordinate_capabilities_use_title_episode_label(self):
        caps = TorznabCapabilities(tv=frozenset({"q", "imdbid", "season"}))
        request = MediaRequest(MediaType.SERIES, "tt123", 2, 5, title="Show")
        self.assertEqual(
            build_search_params(request, caps), {"t": "tvsearch", "q": "Show S02E05"}
        )
        with self.assertRaises(ProviderError):
            build_search_params(EPISODE, caps)

    def test_unavailable_specific_modes_fall_back_to_advertised_generic_text(self):
        caps = parse_capabilities(
            b'<caps><searching><movie-search available="no" supportedParams="imdbid"/>'
            b'<search available="yes" supportedParams="q"/></searching></caps>'
        )
        request = MediaRequest(MediaType.SERIES, "tt123", 0, 1, title="Show")
        self.assertEqual(
            build_search_params(request, caps), {"t": "search", "q": "Show S00E01"}
        )
        with self.assertRaises(ProviderError):
            build_search_params(MOVIE, caps)

    def test_missing_supported_params_not_assumed(self):
        caps = parse_capabilities(
            b'<caps><searching><movie-search available="yes"/></searching></caps>'
        )
        with self.assertRaises(ProviderError):
            build_search_params(MOVIE, caps)

    def test_invalid_caps_document(self):
        for document in (
            b"<caps/>",
            b"<rss><channel/></rss>",
            fixture("malformed.xml"),
            fixture("unsafe.xml"),
        ):
            with self.subTest(document=document), self.assertRaises(ProviderError):
                parse_capabilities(document)


class XmlTests(unittest.TestCase):
    def test_torznab_xml_metadata(self):
        movie, episode = parse_results(fixture("results.xml"), "prowlarr")
        self.assertEqual(movie.info_hash, "abcdef0123456789abcdef0123456789abcdef01")
        self.assertEqual(movie.size_bytes, 8_000_000_000)
        self.assertEqual(movie.seeders, 51)
        self.assertEqual(
            (movie.resolution, movie.codec, movie.hdr), ("2160p", "H.265", "HDR10")
        )
        self.assertEqual(movie.languages, ("es", "en", "multi"))
        self.assertEqual(
            (episode.season, episode.episode, episode.file_index, episode.seeders),
            (2, 5, 5, 0),
        )
        self.assertEqual(episode.languages, ("es", "en", "es-419"))

    def test_missing_optional_attributes_remain_unknown(self):
        first, second = parse_results(fixture("missing_attributes.xml"), "test")
        self.assertIsNone(first.info_hash)
        self.assertIsNone(first.seeders)
        self.assertIsNone(first.size_bytes)
        self.assertIsNone(first.resolution)
        self.assertEqual(first.languages, ())
        self.assertEqual(second.size_bytes, 12345)

    def test_empty_results(self):
        self.assertEqual(parse_results(b"<rss><channel/></rss>", "test"), ())

    def test_malformed_and_unsafe_xml(self):
        for name in ("malformed.xml", "unsafe.xml"):
            with self.subTest(name=name), self.assertRaises(ProviderError) as caught:
                parse_results(fixture(name), "test")
            self.assertEqual(caught.exception.code, "invalid_xml")

    def test_api_error_does_not_echo_description(self):
        with self.assertRaises(ProviderError) as caught:
            parse_results(fixture("error.xml"), "test")
        self.assertEqual(caught.exception.code, "torznab_error")
        self.assertIn("100", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

    def test_error_code_is_not_trusted_as_text(self):
        with self.assertRaises(ProviderError) as caught:
            parse_results(b'<error code="apikey=secret" description="secret"/>', "test")
        self.assertNotIn("secret", str(caught.exception))

    def test_html_and_non_rss_responses_rejected(self):
        for data in (b"<html><body>Login</body></html>", b"<caps/>", b"<rss/>"):
            with self.assertRaises(ProviderError) as caught:
                parse_results(data, "test")
            self.assertEqual(caught.exception.code, "invalid_response")

    def test_infohash_only_item(self):
        value = parse_results(
            rss(
                '<item><title>Title</title><t:attr name="infohash" value="'
                + "a" * 40
                + '"/></item>'
            ),
            "test",
        )[0]
        self.assertEqual(value.info_hash, "a" * 40)
        self.assertIsNone(value.url)

    def test_magnet_in_enclosure_and_link(self):
        for location in (
            "<link>{url}</link>",
            "<guid>{url}</guid>",
            '<enclosure url="{url}" type="application/x-bittorrent"/>',
        ):
            data = rss(
                "<item><title>Title</title>"
                + location.format(url="magnet:?xt=urn:btih:" + "a" * 40)
                + "</item>"
            )
            self.assertEqual(parse_results(data, "test")[0].info_hash, "a" * 40)

    def test_download_enclosure_precedes_detail_link(self):
        data = rss(
            "<item><title>Title</title><link>https://test.invalid/details/1</link>"
            '<enclosure url="https://test.invalid/download/1" type="application/x-bittorrent"/>'
            "</item>"
        )
        self.assertEqual(
            parse_results(data, "test")[0].url, "https://test.invalid/download/1"
        )

    def test_bad_optional_urls_can_fall_back_to_usable_url(self):
        data = rss(
            '<item><title>Title</title><t:attr name="magneturl" value="magnet:?dn=bad"/>'
            '<enclosure url="https://[invalid" type="application/x-bittorrent"/>'
            "<link>https://test.invalid/download/1</link></item>"
        )
        self.assertEqual(
            parse_results(data, "test")[0].url, "https://test.invalid/download/1"
        )

    def test_invalid_optional_values_remain_unknown(self):
        data = rss(
            "<item><title>Title</title><link>https://test.invalid/download</link>"
            '<t:attr name="size" value="-1"/><t:attr name="seeders" value="many"/>'
            '<t:attr name="infohash" value="bad"/></item>'
        )
        candidate = parse_results(data, "test")[0]
        self.assertIsNone(candidate.size_bytes)
        self.assertIsNone(candidate.seeders)
        self.assertIsNone(candidate.info_hash)

    def test_inconsistent_identity_and_missing_required_fields_reported(self):
        for item in (
            "<item><title>Title</title></item>",
            "<item><link>https://test.invalid</link></item>",
            "<item><title>Title</title><link>magnet:?xt=urn:btih:"
            + "a" * 40
            + "</link>"
            '<t:attr name="infohash" value="' + "b" * 40 + '"/></item>',
        ):
            with self.assertLogs(
                "self_debrid.providers.torznab", level="WARNING"
            ) as captured:
                self.assertEqual(parse_results(rss(item), "test"), ())
            self.assertEqual(captured.records[0].skipped_items, 1)
            self.assertEqual(captured.records[0].item_positions, (1,))

    def test_response_size_limit(self):
        with self.assertRaises(ProviderError) as caught:
            parse_results(b" " * (MAX_XML_BYTES + 1), "test")
        self.assertEqual(caught.exception.code, "response_too_large")


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = TorznabProvider(
            "test", "http://prowlarr.invalid/1/api", "secret-do-not-leak"
        )

    async def test_capabilities_discovered_once_and_reused(self):
        self.provider._fetch = AsyncMock(
            side_effect=[
                fixture("caps.xml"),
                fixture("results.xml"),
                fixture("results.xml"),
            ]
        )
        first = await self.provider.search(MOVIE)
        second = await self.provider.search(EPISODE)
        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 2)
        self.assertEqual(self.provider._fetch.await_args_list[0].args, ({"t": "caps"},))
        self.assertEqual(
            self.provider._fetch.await_args_list[1].args,
            ({"t": "movie", "imdbid": "1234567"},),
        )
        self.assertEqual(self.provider._fetch.await_count, 3)

    async def test_failed_caps_does_not_fall_back_blindly(self):
        self.provider._fetch = AsyncMock(
            side_effect=ProviderError("connection_error", "Failed")
        )
        with self.assertRaises(ProviderError):
            await self.provider.search(MOVIE)
        self.assertEqual(self.provider._fetch.await_count, 1)
        self.assertIsNone(self.provider._capabilities)

    async def test_concurrent_first_searches_share_capability_discovery(self):
        calls = []

        async def fetch(params):
            calls.append(params)
            await asyncio.sleep(0)
            return (
                fixture("caps.xml")
                if params["t"] == "caps"
                else b"<rss><channel/></rss>"
            )

        self.provider._fetch = AsyncMock(side_effect=fetch)
        await asyncio.gather(self.provider.search(MOVIE), self.provider.search(EPISODE))
        self.assertEqual(sum(params["t"] == "caps" for params in calls), 1)
        self.assertEqual(len(calls), 3)

    async def test_explicit_capabilities_can_support_caps_incompatible_indexer(self):
        provider = TorznabProvider(
            "test",
            "https://test.invalid/api",
            capabilities=TorznabCapabilities(movie=frozenset({"imdbid"})),
        )
        provider._fetch = AsyncMock(return_value=b"<rss><channel/></rss>")
        self.assertEqual(await provider.search(MOVIE), ())
        provider._fetch.assert_awaited_once_with({"t": "movie", "imdbid": "1234567"})

    async def test_provider_configuration_validation(self):
        for url in (
            "ftp://test/api",
            "https://test/api?apikey=secret",
            "https://user:secret@test/api",
            "/api",
            "https://test/#x",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                TorznabProvider("test", url)
        for values in (
            {"connect_timeout": 0},
            {"read_timeout": -1},
            {"read_timeout": float("inf")},
        ):
            with self.assertRaises(ValueError):
                TorznabProvider("test", "https://test.invalid/api", **values)

    def transport(self, chunks=(), status=200, error=None):
        async def iterate(size):
            for chunk in chunks:
                yield chunk

        response = MagicMock()
        response.status = status
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

    async def test_transport_encodes_query_and_sets_timeouts_without_redirects(self):
        factory, session = self.transport([b"<rss>", b"<channel/></rss>"])
        with patch("self_debrid.providers.torznab.aiohttp.ClientSession", factory):
            self.assertEqual(
                await self.provider._fetch({"t": "search", "q": "Title & Más/Other"}),
                b"<rss><channel/></rss>",
            )
        timeout = factory.call_args.kwargs["timeout"]
        self.assertEqual(
            (timeout.connect, timeout.sock_connect, timeout.sock_read, timeout.total),
            (5, 5, 10, 15),
        )
        call = session.get.call_args
        self.assertFalse(call.kwargs["allow_redirects"])
        encoded = str(URL(call.args[0]).with_query(call.kwargs["params"]))
        query = parse_qs(urlsplit(encoded).query)
        self.assertEqual(query["q"], ["Title & Más/Other"])
        self.assertEqual(query["apikey"], ["secret-do-not-leak"])
        self.assertIn("%26", encoded)

    async def test_transport_http_errors_and_redirects_are_safe(self):
        for status in (401, 403, 429, 500, 302):
            factory, session = self.transport(status=status)
            with (
                patch("self_debrid.providers.torznab.aiohttp.ClientSession", factory),
                self.assertRaises(ProviderError) as caught,
            ):
                await self.provider._fetch({"t": "caps"})
            self.assertEqual(caught.exception.code, "http_error")
            self.assertIn(str(status), str(caught.exception))
            self.assertNotIn("secret", str(caught.exception))

    async def test_transport_connection_and_read_failures(self):
        for error, code in (
            (asyncio.TimeoutError(), "timeout"),
            (
                aiohttp.ClientConnectionError("URL with secret-do-not-leak"),
                "connection_error",
            ),
        ):
            factory, session = self.transport(error=error)
            with (
                patch("self_debrid.providers.torznab.aiohttp.ClientSession", factory),
                self.assertRaises(ProviderError) as caught,
            ):
                await self.provider._fetch({"t": "caps"})
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("secret", str(caught.exception))

    async def test_transport_bounds_response_size(self):
        factory, session = self.transport([b"x" * MAX_XML_BYTES, b"x"])
        with (
            patch("self_debrid.providers.torznab.aiohttp.ClientSession", factory),
            self.assertRaises(ProviderError) as caught,
        ):
            await self.provider._fetch({"t": "caps"})
        self.assertEqual(caught.exception.code, "response_too_large")


class PartialFeedTests(unittest.TestCase):
    def test_mixed_valid_invalid_items_preserve_usable_results(self):
        with self.assertLogs("self_debrid.providers.torznab", "WARNING") as captured:
            candidates = parse_results(fixture("mixed_items.xml"), "fixture")
        self.assertEqual(len(candidates), 3)
        self.assertEqual(candidates[0].info_hash, "1" * 40)
        self.assertEqual(candidates[1].file_index, 2)
        self.assertIsNone(candidates[2].info_hash)
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured.records[0].skipped_items, 4)
        self.assertEqual(captured.records[0].item_positions, (2, 3, 4, 6))
        for value in ("private-key", "https://", "magnet:", "Contradictory"):
            self.assertNotIn(value, repr(captured.output))

    def test_diagnostics_are_bounded_for_many_bad_items(self):
        from self_debrid.providers.torznab import MAX_ITEM_DIAGNOSTICS

        data = rss(
            "<item><title>https://private.invalid/?apikey=secret</title></item>" * 100
        )
        with self.assertLogs("self_debrid.providers.torznab", "WARNING") as captured:
            self.assertEqual(parse_results(data, "private-api-key"), ())
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured.records[0].skipped_items, 100)
        self.assertEqual(len(captured.records[0].item_positions), MAX_ITEM_DIAGNOSTICS)
        self.assertLess(len(captured.output[0]), 200)
        self.assertNotIn("secret", captured.output[0])
        self.assertNotIn("private", captured.output[0])

    def test_detail_links_are_not_torrent_locators(self):
        with self.assertLogs("self_debrid.providers.torznab", "WARNING") as captured:
            values = parse_results(fixture("locators.xml"), "fixture")
        self.assertEqual(len(values), 7)
        self.assertIsNone(values[0].url)
        self.assertEqual(values[0].info_hash, "1" * 40)
        self.assertIn("/opaque?", values[1].url)
        self.assertIn("/indexer/1/download?", values[2].url)
        self.assertIn("/dl/indexer/", values[3].url)
        self.assertIn("t=get", values[4].url)
        self.assertIn(".torrent?", values[5].url)
        self.assertTrue(values[6].url.startswith("magnet:"))
        self.assertEqual(captured.records[0].skipped_items, 2)
        self.assertTrue(all("/details" not in (value.url or "") for value in values))

    def test_magnet_link_preserves_selection_ahead_of_http_enclosure(self):
        data = rss(
            "<item><title>Show.S02E05</title><link>magnet:?xt=urn:btih:"
            + "a" * 40
            + "&amp;so=5</link>"
            '<enclosure url="https://tracker.invalid/download/1" type="application/x-bittorrent"/></item>'
        )
        value = parse_results(data, "fixture")[0]
        self.assertEqual(value.file_index, 5)
        self.assertTrue(value.url.startswith("magnet:"))

    def test_http_detail_page_in_magnet_attribute_is_not_trusted(self):
        data = rss(
            '<item><title>Release</title><t:attr name="infohash" value="'
            + "a" * 40
            + '"/>'
            '<t:attr name="magneturl" value="https://tracker.invalid/details/1?apikey=private-key"/></item>'
        )
        self.assertIsNone(parse_results(data, "fixture")[0].url)

    def test_item_error_is_recoverable_but_document_auth_error_is_fatal(self):
        good = (
            '<item><title>Good</title><t:attr name="infohash" value="'
            + "a" * 40
            + '"/></item>'
        )
        bad = '<item><error code="201" description="apikey=private-key"/></item>'
        with self.assertLogs("self_debrid.providers.torznab", "WARNING") as captured:
            values = parse_results(rss(bad + good), "fixture")
        self.assertEqual(len(values), 1)
        self.assertEqual(captured.records[0].skipped_items, 1)
        self.assertNotIn("private-key", repr(captured.output))
        with self.assertRaises(ProviderError) as caught:
            parse_results(
                rss('<error code="100" description="private-key"/>' + good), "fixture"
            )
        self.assertEqual(caught.exception.code, "torznab_error")
