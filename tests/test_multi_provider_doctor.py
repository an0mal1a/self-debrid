import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from self_debrid.__main__ import main
from self_debrid.application.metadata import MediaMetadata, MetadataResult
from self_debrid.domain import MediaRequest, MediaType
from self_debrid.infrastructure.bootstrap import build_search_service
from self_debrid.infrastructure.config import AppConfig, TorznabSettings
from self_debrid.infrastructure.doctor import diagnose
from self_debrid.infrastructure.reliability import (
    DiscoveryCacheSettings,
    MetadataSettings,
)
from self_debrid.providers.torznab import TorznabProvider

FIXTURES = Path(__file__).parent / "fixtures"
REQUEST = MediaRequest(MediaType.MOVIE, "tt1234567")


class MultiProviderConfigTests(unittest.TestCase):
    def config(self, payload, extra=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "providers.json"
            path.write_text(json.dumps(payload))
            return AppConfig.from_env(
                {
                    "PROVIDERS_FILE": str(path),
                    "INDEXER_KEY": "private-key",
                    **(extra or {}),
                }
            )

    def test_multi_provider_env_references_are_immutable_and_legacy_still_works(self):
        config = self.config(
            {
                "providers": [
                    {
                        "name": "one",
                        "url": "http://host/1/api",
                        "api_key_env": "INDEXER_KEY",
                    },
                    {"name": "two", "url": "http://host/2/api"},
                ]
            }
        )
        self.assertIsNone(config.torznab)
        self.assertEqual([p.name for p in config.configured_providers], ["one", "two"])
        self.assertEqual(config.providers[0].api_key, "private-key")
        self.assertNotIn("private-key", repr(config))
        self.assertIsInstance(config.providers, tuple)
        legacy = AppConfig.from_env(
            {"TORZNAB_URL": "http://host/1/api", "TORZNAB_API_KEY": "private-key"}
        )
        self.assertEqual(legacy.configured_providers, (legacy.torznab,))

    def test_file_configuration_explicitly_replaces_legacy_endpoint(self):
        config = self.config(
            {"providers": [{"name": "file", "url": "http://host/2/api"}]},
            {"TORZNAB_URL": "http://host/1/api"},
        )
        self.assertEqual([p.name for p in config.configured_providers], ["file"])

    def test_bad_shapes_duplicates_limits_and_secret_fields_are_rejected_safely(self):
        for payload in (
            {},
            [],
            {"providers": []},
            {"providers": "wrong"},
            {
                "providers": [
                    {"name": "one", "url": "http://host/api", "api_key": "private-key"}
                ]
            },
            {
                "providers": [
                    {"name": "one", "url": "http://host/api", "api_key_env": "MISSING"}
                ]
            },
            {
                "providers": [
                    {"name": "one", "url": "http://host/api"},
                    {"name": "ONE", "url": "http://host/other"},
                ]
            },
            {
                "providers": [
                    {"name": "one", "url": "http://host/api?apikey=private-key"}
                ]
            },
            {"providers": [{"name": "bad name", "url": "http://host/api"}]},
            {
                "providers": [
                    {"name": str(i), "url": "http://host/api"} for i in range(17)
                ]
            },
        ):
            with (
                self.subTest(payload=payload),
                self.assertRaises(ValueError) as captured,
            ):
                self.config(payload)
            self.assertNotIn("private-key", str(captured.exception))

    def test_missing_oversized_or_malformed_file_is_sanitized(self):
        with self.assertRaises(ValueError):
            AppConfig.from_env({"PROVIDERS_FILE": "/does-not-exist/private-key"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for contents in ("not JSON private-key", " " * 65537):
                path.write_text(contents)
                with self.assertRaises(ValueError) as captured:
                    AppConfig.from_env({"PROVIDERS_FILE": str(path)})
                self.assertNotIn("private-key", str(captured.exception))

    def test_cinemeta_is_opt_in_and_limits_are_validated(self):
        config = AppConfig.from_env({"TORZNAB_URL": "http://host/api"})
        self.assertFalse(config.metadata.enabled)
        config = AppConfig.from_env(
            {
                "TORZNAB_URL": "http://host/api",
                "CINEMETA_ENABLED": "true",
                "DISCOVERY_CACHE_TTL": "0",
            }
        )
        self.assertTrue(config.metadata.enabled)
        self.assertEqual(config.discovery_cache.ttl, 0)
        for options in (
            {"CINEMETA_ENABLED": "yes"},
            {"METADATA_TTL": "nan"},
            {"DISCOVERY_CACHE_TTL": "301"},
            {"DISCOVERY_CACHE_ENTRIES": "0"},
            {"DISCOVERY_MAX_INFLIGHT": "65"},
            {"MAX_CONCURRENCY": "17"},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                AppConfig.from_env({"TORZNAB_URL": "http://host/api", **options})

    def test_duplicate_names_are_rejected_for_direct_immutable_config(self):
        provider = TorznabSettings("http://host/api", name="one")
        with self.assertRaises(ValueError):
            AppConfig(providers=(provider, provider))
        with self.assertRaises(ValueError):
            AppConfig(providers=[provider])

    def test_composition_builds_multiple_providers_and_config_specific_cache_namespace(
        self,
    ):
        providers = (
            TorznabSettings("http://host/1/api", name="one"),
            TorznabSettings("http://host/2/api", name="two"),
        )
        config = AppConfig(providers=providers)
        service = build_search_service(config)
        self.assertEqual([p.name for p in service._registry.providers], ["one", "two"])
        self.assertIsNone(service._metadata)
        same = build_search_service(config)
        self.assertEqual(service._cache_namespace, same._cache_namespace)
        for changed in (
            replace(
                config,
                providers=(replace(providers[0], api_key="different"), providers[1]),
            ),
            replace(
                config,
                providers=(
                    replace(providers[0], base_url="http://other/api"),
                    providers[1],
                ),
            ),
            replace(config, metadata=MetadataSettings(enabled=True)),
            replace(config, discovery_cache=DiscoveryCacheSettings(ttl=10)),
        ):
            self.assertNotEqual(
                service._cache_namespace, build_search_service(changed)._cache_namespace
            )


class DoctorTests(unittest.IsolatedAsyncioTestCase):
    async def test_diagnostic_reports_caps_search_exclusions_and_no_credentials(self):
        provider = TorznabProvider("one", "http://host/1/api", "private-key")
        provider._fetch = AsyncMock(
            side_effect=[
                (FIXTURES / "caps_title.xml").read_bytes(),
                (FIXTURES / "reliability_results.xml").read_bytes(),
            ]
        )
        metadata = AsyncMock()
        metadata.resolve.return_value = MetadataResult(
            MediaMetadata("tt1234567", "Example Movie", MediaType.MOVIE, 2024)
        )
        config = AppConfig(
            providers=(
                TorznabSettings("http://host/1/api", "private-key", name="one"),
            ),
            metadata=MetadataSettings(enabled=True),
        )
        with (
            patch(
                "self_debrid.infrastructure.doctor.build_providers",
                return_value=(provider,),
            ),
            patch(
                "self_debrid.infrastructure.doctor.build_metadata_resolver",
                return_value=metadata,
            ),
        ):
            report = await diagnose(config, REQUEST)
        self.assertEqual(report["metadata"]["status"], "ok")
        value = report["providers"][0]
        self.assertEqual(value["endpoint"], "responding")
        self.assertEqual(value["capabilities"]["movie"], ["q", "year"])
        self.assertEqual((value["candidates"], value["compatible"]), (1, 1))
        self.assertEqual(value["exclusions"]["year_mismatch"], 1)
        for secret in ("private-key", "http://host", "infoHash", "https://"):
            self.assertNotIn(secret, json.dumps(report))

    async def test_failed_endpoint_is_isolated_and_metadata_can_be_disabled(self):
        bad = TorznabProvider("bad", "http://host/1/api")
        bad._fetch = AsyncMock(side_effect=TimeoutError("private-key"))
        good = TorznabProvider("good", "http://host/2/api")
        good._fetch = AsyncMock(
            side_effect=[
                (FIXTURES / "caps_imdb.xml").read_bytes(),
                b"<rss><channel/></rss>",
            ]
        )
        config = AppConfig(
            providers=(
                TorznabSettings("http://host/1/api", name="bad"),
                TorznabSettings("http://host/2/api", name="good"),
            )
        )
        with patch(
            "self_debrid.infrastructure.doctor.build_providers",
            return_value=(bad, good),
        ):
            report = await diagnose(config, REQUEST)
        self.assertEqual(report["providers"][0]["errors"], ["timeout"])
        self.assertEqual(report["providers"][1]["endpoint"], "responding")
        self.assertEqual(report["providers"][1]["candidates"], 0)
        self.assertEqual(report["metadata"], {"status": "disabled"})
        self.assertNotIn("private-key", json.dumps(report))

    async def test_compatibility_filter_exclusions_are_visible(self):
        provider = TorznabProvider("one", "http://host/api")
        feed = (
            b'<rss xmlns:t="http://torznab.com/schemas/2015/feed"><channel><item><title>Movie</title><t:attr name="infohash" value="'
            + b"ab" * 32
            + b'"/></item></channel></rss>'
        )
        provider._fetch = AsyncMock(
            side_effect=[(FIXTURES / "caps_imdb.xml").read_bytes(), feed]
        )
        config = AppConfig(TorznabSettings("http://host/api"))
        with patch(
            "self_debrid.infrastructure.doctor.build_providers",
            return_value=(provider,),
        ):
            report = await diagnose(config, REQUEST)
        self.assertEqual(report["providers"][0]["compatibility_excluded"], 1)
        self.assertEqual(report["providers"][0]["compatible"], 0)


class DoctorCliTests(unittest.TestCase):
    def test_doctor_command_runs_local_diagnostic_and_has_no_public_route(self):
        output = StringIO()
        with (
            patch.dict("os.environ", {"TORZNAB_URL": "http://host/api"}, clear=True),
            patch(
                "self_debrid.__main__.diagnose",
                AsyncMock(return_value={"providers": []}),
            ) as diagnose_mock,
            redirect_stdout(output),
        ):
            self.assertEqual(main(["doctor", "movie", "tt1234567"]), 0)
        diagnose_mock.assert_awaited_once()
        self.assertEqual(json.loads(output.getvalue()), {"providers": []})

    def test_doctor_invalid_id_and_help_require_no_network(self):
        with redirect_stdout(StringIO()), self.assertRaises(SystemExit) as captured:
            main(["doctor", "--help"])
        self.assertEqual(captured.exception.code, 0)
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as captured:
            main(["doctor", "movie", "private-key"])
        self.assertEqual(captured.exception.code, 2)

    def test_doctor_output_does_not_echo_unknown_capability_values(self):
        import asyncio

        provider = TorznabProvider("one", "http://host/api")
        provider._fetch = AsyncMock(
            side_effect=[
                b'<caps><searching><movie-search available="yes" supportedParams="imdbid,private-passkey,https://tracker.invalid/secret"/></searching></caps>',
                b"<rss><channel/></rss>",
            ]
        )
        with patch(
            "self_debrid.infrastructure.doctor.build_providers",
            return_value=(provider,),
        ):
            report = asyncio.run(
                diagnose(AppConfig(TorznabSettings("http://host/api")), REQUEST)
            )
        self.assertEqual(report["providers"][0]["capabilities"]["movie"], ["imdbid"])
        self.assertNotIn("private-passkey", json.dumps(report))
        self.assertNotIn("tracker.invalid", json.dumps(report))
