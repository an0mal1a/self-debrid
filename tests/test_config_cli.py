import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from self_debrid.__main__ import main
from self_debrid.domain import (
    DiscoveryResult,
    MediaRequest,
    MediaType,
    ProviderFailure,
    StreamCandidate,
)
from self_debrid.infrastructure.config import AppConfig


class ConfigTests(unittest.TestCase):
    def test_minimal_configuration_and_defaults(self):
        config = AppConfig.from_env({"TORZNAB_URL": "http://localhost:9696/1/api"})
        self.assertEqual(config.torznab.name, "prowlarr")
        self.assertEqual(config.torznab.connect_timeout, 5)
        self.assertEqual(config.provider_timeout, 20)
        self.assertEqual(
            config.ranking.resolution_order, ("2160p", "1080p", "720p", "480p")
        )

    def test_preferences_and_timeouts_from_environment(self):
        config = AppConfig.from_env(
            {
                "TORZNAB_URL": "http://localhost:9696/1/api",
                "TORZNAB_API_KEY": "secret-do-not-leak",
                "TORZNAB_NAME": "my-indexer",
                "CONNECT_TIMEOUT": "2",
                "READ_TIMEOUT": "4",
                "PROVIDER_TIMEOUT": "8",
                "MAX_CONCURRENCY": "2",
                "RESOLUTION_ORDER": "1080p,720p",
                "PREFERRED_LANGUAGES": "ES, en",
                "MAX_SIZE_BYTES": "1000",
                "MIN_SEEDERS": "3",
            }
        )
        self.assertEqual(config.torznab.api_key, "secret-do-not-leak")
        self.assertNotIn("secret-do-not-leak", repr(config))
        self.assertEqual(config.ranking.preferred_languages, ("es", "en"))
        self.assertEqual(config.ranking.max_size_bytes, 1000)
        self.assertEqual(config.ranking.min_seeders, 3)
        self.assertEqual(config.torznab.connect_timeout, 2)
        self.assertEqual(config.torznab.read_timeout, 4)
        self.assertEqual(config.max_concurrency, 2)

    def test_invalid_config_fails_without_echoing_values(self):
        cases = (
            {},
            {"TORZNAB_URL": "http://host/api?apikey=secret-do-not-leak"},
            {"READ_TIMEOUT": "secret-do-not-leak"},
            {"PROVIDER_TIMEOUT": "nan"},
            {"MIN_SEEDERS": "-1"},
            {"MAX_SIZE_BYTES": "1.5"},
            {"MAX_CONCURRENCY": "0"},
        )
        for values in cases:
            env = {"TORZNAB_URL": "http://host/api", **values} if values else {}
            with self.subTest(values=values), self.assertRaises(ValueError) as caught:
                AppConfig.from_env(env)
            self.assertNotIn("secret-do-not-leak", str(caught.exception))


class CliTests(unittest.TestCase):
    def test_cli_runs_real_search_pipeline_with_fixture_transport(self):
        fixtures = Path(__file__).parent / "fixtures"
        responses = [
            (fixtures / "caps.xml").read_bytes(),
            (fixtures / "results.xml").read_bytes(),
        ]
        output = StringIO()
        with (
            patch.dict("os.environ", {"TORZNAB_URL": "http://host/api"}, clear=True),
            patch(
                "self_debrid.providers.torznab.TorznabProvider._fetch",
                AsyncMock(side_effect=responses),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(main(["search", "movie", "tt123"]), 0)
        data = json.loads(output.getvalue())
        self.assertEqual(len(data["candidates"]), 1)
        self.assertEqual(data["candidates"][0]["seeders"], 51)
        self.assertEqual(data["failures"], [])
        self.assertNotIn("private-download-token", output.getvalue())

    def test_help_and_version_do_not_need_configuration(self):
        for args, expected in (
            (["--help"], "discovery CLI"),
            (["--version"], "2.0.0.dev2"),
        ):
            output = StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
                main(args)
            self.assertEqual(caught.exception.code, 0)
            self.assertIn(expected, output.getvalue())

    def test_movie_search_outputs_ranked_metadata_without_private_url(self):
        result = DiscoveryResult(
            (
                StreamCandidate(
                    "test",
                    "Title",
                    info_hash="a" * 40,
                    url="https://tracker.invalid/get?apikey=secret-do-not-leak",
                ),
            )
        )
        output = StringIO()
        config = AppConfig.from_env({"TORZNAB_URL": "http://host/api"})
        with (
            patch("self_debrid.__main__.AppConfig.from_env", return_value=config),
            patch(
                "self_debrid.__main__.run_search", AsyncMock(return_value=result)
            ) as run,
            redirect_stdout(output),
        ):
            self.assertEqual(main(["search", "movie", "tt123", "--title", "Title"]), 0)
        run.assert_awaited_once_with(
            config, MediaRequest(MediaType.MOVIE, "tt123", title="Title")
        )
        data = json.loads(output.getvalue())
        self.assertEqual(data["candidates"][0]["identity"], "torrent:" + "a" * 40)
        self.assertNotIn("secret-do-not-leak", output.getvalue())
        self.assertNotIn("url", data["candidates"][0])

    def test_episode_cli_and_partial_failure_exit_status(self):
        result = DiscoveryResult(
            (StreamCandidate("test", "Title", url="https://host/get?apikey=secret"),),
            (ProviderFailure("broken", "Timed out", "timeout"),),
        )
        output = StringIO()
        config = AppConfig.from_env({"TORZNAB_URL": "http://host/api"})
        with (
            patch("self_debrid.__main__.AppConfig.from_env", return_value=config),
            patch(
                "self_debrid.__main__.run_search", AsyncMock(return_value=result)
            ) as run,
            redirect_stdout(output),
        ):
            self.assertEqual(main(["search", "series", "tt123:2:5"]), 0)
        run.assert_awaited_once_with(
            config, MediaRequest(MediaType.SERIES, "tt123", 2, 5)
        )
        self.assertEqual(
            json.loads(output.getvalue())["candidates"][0]["identity"], "url:[redacted]"
        )
        self.assertNotIn("apikey", output.getvalue())

    def test_total_provider_failure_has_nonzero_exit_status(self):
        result = DiscoveryResult(
            (), (ProviderFailure("test", "Failed", "connection_error"),)
        )
        with (
            patch("self_debrid.__main__.AppConfig.from_env"),
            patch("self_debrid.__main__.run_search", AsyncMock(return_value=result)),
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(main(["search", "movie", "tt123"]), 1)

    def test_invalid_request_or_missing_configuration_exits_before_network(self):
        with patch.dict("os.environ", {}, clear=True):
            for args in (["search", "movie", "tt123"], ["search", "series", "bad"]):
                with (
                    self.subTest(args=args),
                    redirect_stderr(StringIO()),
                    self.assertRaises(SystemExit) as caught,
                ):
                    main(args)
                self.assertEqual(caught.exception.code, 2)


class ServerConfigCliTests(unittest.TestCase):
    def test_server_defaults_and_explicit_lan_configuration(self):
        config = AppConfig.from_env({"TORZNAB_URL": "http://host/api"})
        self.assertEqual((config.server.host, config.server.port), ("127.0.0.1", 8080))
        self.assertEqual(
            config.server.manifest_url, "http://127.0.0.1:8080/manifest.json"
        )
        config = AppConfig.from_env(
            {
                "TORZNAB_URL": "http://host/api",
                "HTTP_HOST": "0.0.0.0",
                "HTTP_PORT": "9090",
                "PUBLIC_ADDON_URL": "https://addon.invalid/prefix/",
            }
        )
        self.assertEqual(config.server.host, "0.0.0.0")
        self.assertEqual(
            config.server.manifest_url, "https://addon.invalid/prefix/manifest.json"
        )

    def test_invalid_server_settings_are_sanitized(self):
        from self_debrid.infrastructure.config import ServerSettings

        for values in (
            {"host": "https://key@host"},
            {"port": 0},
            {"port": 65536},
            {"port": True},
            {"public_base_url": "https://host/?apikey=private-key"},
            {"public_base_url": "https://user:private-key@host"},
            {"public_base_url": "https://[bad"},
            {"public_base_url": "https://host:99999"},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError) as captured:
                ServerSettings(**values)
            self.assertNotIn("private-key", str(captured.exception))
        self.assertEqual(
            ServerSettings(host="::1").manifest_url, "http://[::1]:8080/manifest.json"
        )

    def test_serve_command_honors_cli_over_environment_without_printing_secrets(self):
        output = StringIO()
        with (
            patch.dict(
                "os.environ",
                {
                    "TORZNAB_URL": "http://host/api",
                    "TORZNAB_API_KEY": "private-key",
                    "HTTP_HOST": "0.0.0.0",
                    "HTTP_PORT": "9090",
                },
                clear=True,
            ),
            patch("self_debrid.__main__.serve") as serve,
            redirect_stdout(output),
        ):
            self.assertEqual(
                main(["serve", "--host", "127.0.0.1", "--port", "8080"]), 0
            )
        self.assertEqual(serve.call_args.kwargs, {"host": "127.0.0.1", "port": 8080})
        self.assertIn("http://127.0.0.1:8080/manifest.json", output.getvalue())
        self.assertNotIn("private-key", output.getvalue())

    def test_serve_help_does_not_require_provider_configuration(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            redirect_stdout(StringIO()),
            self.assertRaises(SystemExit) as captured,
        ):
            main(["serve", "--help"])
        self.assertEqual(captured.exception.code, 0)

    def test_serve_invalid_config_fails_before_startup(self):
        with (
            patch.dict("os.environ", {"TORZNAB_URL": "http://host/api"}, clear=True),
            patch("self_debrid.__main__.serve") as serve,
            redirect_stderr(StringIO()),
            self.assertRaises(SystemExit) as captured,
        ):
            main(["serve", "--port", "0"])
        self.assertEqual(captured.exception.code, 2)
        serve.assert_not_called()
