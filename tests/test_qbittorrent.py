import asyncio
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestServer

from self_debrid.adapters.qbittorrent import QBittorrentClient
from self_debrid.application.playback import PlaybackService, SessionState
from self_debrid.domain.models import MediaRequest, MediaType, StreamCandidate
from self_debrid.domain.torrents import (
    DownloadState,
    TorrentError,
    TorrentHandle,
    TorrentSource,
)
from self_debrid.infrastructure.torrent_config import TorrentEngineSettings
from self_debrid.infrastructure.torrent_engine import LocalDownloadWorkspace
from self_debrid.infrastructure.torrent_source import MAX_TORRENT_BYTES, metainfo_hash

HASH = "a" * 40
TAG = "self-debrid-" + "b" * 32
DIRECTORY = "/tmp/self-debrid-test/" + "b" * 32
SECRET = "tracker-passkey-and-api-secret"
INFO = b"d6:lengthi4e4:name9:movie.mkv12:piece lengthi16384e6:pieces20:aaaaaaaaaaaaaaaaaaaae"
TORRENT = b"d4:info" + INFO + b"e"


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    return (
        b"d"
        + b"".join(bencode(key) + bencode(item) for key, item in sorted(value.items()))
        + b"e"
    )


class MetainfoTests(unittest.TestCase):
    def test_multifile_metainfo_and_paths(self):
        info = {
            b"name": b"Season 2",
            b"piece length": 16384,
            b"pieces": b"a" * 20,
            b"files": [{b"length": 4, b"path": [b"Season 02", b"S02E05.mkv"]}],
        }
        self.assertEqual(
            metainfo_hash(bencode({b"info": info})),
            hashlib.sha1(bencode(info)).hexdigest(),
        )
        info[b"files"][0][b"path"] = [b"..", b"secret.mkv"]
        with self.assertRaises(TorrentError):
            metainfo_hash(bencode({b"info": info}))

    def test_metainfo_symlinks_rejected(self):
        info = {
            b"name": b"Movie",
            b"piece length": 16384,
            b"pieces": b"a" * 20,
            b"files": [{b"length": 4, b"path": [b"movie.mkv"], b"attr": b"l"}],
        }
        with self.assertRaisesRegex(TorrentError, "source_invalid"):
            metainfo_hash(bencode({b"info": info}))

    def test_hash_uses_original_info_slice(self):
        self.assertEqual(metainfo_hash(TORRENT), hashlib.sha1(INFO).hexdigest())

    def test_announcer_credentials_do_not_affect_hash(self):
        prefix = (
            b"d8:announce"
            + str(len(SECRET)).encode()
            + b":"
            + SECRET.encode()
            + b"4:info"
        )
        self.assertEqual(metainfo_hash(prefix + INFO + b"e"), metainfo_hash(TORRENT))

    def test_invalid_bencode_is_sanitized(self):
        for payload in (
            b"",
            b"<html>" + SECRET.encode(),
            TORRENT + b"junk",
            b"d4:infode4:infodee",
            b"l" * 40 + b"e" * 40,
            b"d4:infod4:name1:ae",
            b"i99999999999999999999999999e",
        ):
            with (
                self.subTest(payload=payload),
                self.assertRaises(TorrentError) as error,
            ):
                metainfo_hash(payload)
            self.assertNotIn(SECRET, str(error.exception))

    def test_oversized_metainfo(self):
        with self.assertRaisesRegex(TorrentError, "source_too_large"):
            metainfo_hash(b"a" * (MAX_TORRENT_BYTES + 1))

    def test_v2_only_not_misrepresented_as_v1(self):
        with self.assertRaisesRegex(TorrentError, "source_invalid"):
            metainfo_hash(b"d4:infod12:meta versioni2e4:name1:aee")


class QBittorrentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.cookies = []
        self.version = "v5.0.0"
        self.auth = "Ok."
        self.rows = []
        self.raw_files = json.loads(
            (Path(__file__).parent / "fixtures/torrent_files.json").read_text()
        )["movie"]
        self.statuses = {}
        self.raw_responses = {}
        self.delays = {}
        self.torrent_status = 200
        self.torrent_payload = TORRENT
        self.auto_add = False
        app = web.Application()
        app.router.add_route("*", "/api/v2/{endpoint:.*}", self.api)
        app.router.add_get("/download", self.download)
        self.server = TestServer(app)
        await self.server.start_server()
        self.settings = TorrentEngineSettings(
            base_url=str(self.server.make_url("/")),
            username="operator",
            password=SECRET,
            request_timeout=0.3,
            read_timeout=0.2,
        )
        self.client = QBittorrentClient(self.settings)
        await self.client.connect()
        self.calls.clear()
        self.handle = TorrentHandle(HASH, TAG, DIRECTORY)

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()

    def owned(self):
        self.rows = [
            {
                "hash": HASH,
                "save_path": DIRECTORY,
                "tags": TAG,
                "auto_tmm": False,
                "state": "downloading",
                "seq_dl": False,
                "f_l_piece_prio": False,
            }
        ]

    async def api(self, request):
        endpoint = request.match_info["endpoint"]
        data = (
            dict(await request.post())
            if request.method == "POST"
            else dict(request.query)
        )
        self.calls.append((request.method, endpoint, data))
        if endpoint != "auth/login":
            self.assertEqual(request.cookies.get("SID"), "session-cookie")
        self.assertEqual(request.headers["Referer"], self.settings.base_url + "/")
        if endpoint in self.delays:
            await asyncio.sleep(self.delays[endpoint])
        if endpoint in self.statuses:
            return web.Response(status=self.statuses[endpoint], text=SECRET)
        if endpoint in self.raw_responses:
            return web.Response(text=self.raw_responses[endpoint])
        if endpoint == "auth/login":
            response = web.Response(text=self.auth)
            response.set_cookie("SID", "session-cookie")
            return response
        if endpoint == "app/version":
            return web.Response(text=self.version)
        if endpoint == "torrents/info":
            return web.json_response(self.rows)
        if endpoint == "torrents/files":
            return web.json_response(self.raw_files)
        if endpoint == "torrents/add":
            if isinstance(data.get("torrents"), web.FileField):
                self.uploaded = data["torrents"].file.read()
            if self.auto_add:
                self.rows = [
                    {
                        "hash": HASH,
                        "save_path": data["savepath"],
                        "tags": data["tags"],
                        "auto_tmm": False,
                        "state": "stoppedDL",
                        "seq_dl": False,
                        "f_l_piece_prio": False,
                    }
                ]
            return web.Response(text="Ok.")
        return web.Response()

    async def download(self, request):
        self.cookies.append(dict(request.cookies))
        self.download_query = request.raw_path
        return web.Response(
            status=self.torrent_status,
            body=self.torrent_payload,
            headers={"Location": "http://untrusted.invalid/"},
        )

    async def test_connection_login_uses_private_post_and_sid(self):
        await self.client.connect()
        self.assertEqual(self.calls, [])  # Existing authenticated connection is reused.
        await self.client.inspect(HASH)
        self.assertEqual(self.calls[0][1], "torrents/info")

    async def test_auth_failure_sanitized(self):
        await self.client.close()
        self.auth = SECRET
        with self.assertRaisesRegex(TorrentError, "authentication_failed") as error:
            await self.client.connect()
        self.assertNotIn(SECRET, str(error.exception))

    async def test_banned_client_auth_and_no_redirect(self):
        for status in (302, 403, 401, 500):
            self.statuses["torrents/info"] = status
            with self.subTest(status=status), self.assertRaises(TorrentError) as error:
                await self.client.inspect(HASH)
            self.assertNotIn(SECRET, str(error.exception))

    async def test_connection_error_and_read_timeout(self):
        self.delays["torrents/info"] = 0.25
        with self.assertRaisesRegex(TorrentError, "client_timeout"):
            await self.client.inspect(HASH)
        await self.server.close()
        with self.assertRaisesRegex(TorrentError, "connection_failed"):
            await self.client.inspect(HASH)

    async def test_request_cancellation_propagates(self):
        self.delays["torrents/info"] = 0.1
        task = asyncio.create_task(self.client.inspect(HASH))
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_version_floor(self):
        for version in ("v4.5.5", "v6.0.0", SECRET):
            await self.client.close()
            self.version = version
            with (
                self.subTest(version=version),
                self.assertRaisesRegex(TorrentError, "unsupported_client"),
            ):
                await self.client.connect()

    async def test_submit_magnet_stops_at_metadata_with_private_directory(self):
        magnet = "magnet:?xt=urn:btih:" + HASH + "&tr=https://tracker.invalid/" + SECRET
        source = await self.client.resolve_source(
            StreamCandidate("test", "movie", url=magnet)
        )
        handle = await self.client.submit(source, DIRECTORY, TAG)
        self.assertEqual(handle.info_hash, HASH)
        add = self.calls[-1][2]
        self.assertEqual(add["urls"], magnet)
        self.assertEqual(add["stopCondition"], "MetadataReceived")
        self.assertEqual(add["stopped"], "false")
        self.assertEqual(add["autoTMM"], "false")
        self.assertEqual(add["useDownloadPath"], "false")
        self.assertEqual(add["tags"], TAG)
        self.assertNotIn(SECRET, repr(source))

    async def test_legacy_pause_resume_and_add_parameter(self):
        await self.client.close()
        self.version = "v4.6.0"
        await self.client.connect()
        await self.client.submit(
            TorrentSource(HASH, magnet="magnet:?xt=urn:btih:" + HASH), DIRECTORY, TAG
        )
        self.assertIn("paused", self.calls[-1][2])
        self.owned()
        await self.client.stop(self.handle)
        await self.client.start(self.handle)
        self.assertEqual(
            [endpoint for method, endpoint, data in self.calls if method == "POST"][
                -2:
            ],
            ["torrents/pause", "torrents/resume"],
        )

    async def test_upload_torrent_stopped(self):
        info_hash = metainfo_hash(TORRENT)
        await self.client.submit(
            TorrentSource(info_hash, torrent_bytes=TORRENT), DIRECTORY, TAG
        )
        self.assertEqual(self.uploaded, TORRENT)
        self.assertEqual(self.calls[-1][2]["stopped"], "true")

    async def test_existing_torrent_never_modified_even_with_our_tag(self):
        self.owned()
        with self.assertRaisesRegex(TorrentError, "torrent_exists"):
            await self.client.submit(
                TorrentSource(HASH, magnet="magnet:?xt=urn:btih:" + HASH),
                DIRECTORY,
                TAG,
            )
        self.assertFalse(any(method == "POST" for method, _, _ in self.calls))

    async def test_ownership_loss_prevents_mutation(self):
        for changes in (
            {"tags": "operator"},
            {"save_path": "/somewhere-else"},
            {"auto_tmm": True},
        ):
            self.owned()
            self.rows[0].update(changes)
            with (
                self.subTest(changes=changes),
                self.assertRaisesRegex(TorrentError, "ownership_lost"),
            ):
                await self.client.stop(self.handle)
        self.assertFalse(any(method == "POST" for method, _, _ in self.calls))

    async def test_file_metadata_uses_actual_index_not_position(self):
        self.owned()
        snapshot = await self.client.inspect(HASH)
        self.assertEqual(snapshot.files[0].index, 4)
        self.assertEqual(snapshot.files[0].progress, 0.25)
        self.assertEqual(snapshot.files[0].availability, 0.8)
        self.assertEqual(snapshot.state, DownloadState.DOWNLOADING)

    async def test_unknown_peer_availability_sentinel_is_not_an_error_or_zero(self):
        self.owned()
        self.raw_files[0]["availability"] = -1
        snapshot = await self.client.inspect(HASH)
        self.assertIsNone(snapshot.files[0].availability)

    async def test_priority_strategy_and_safe_toggles(self):
        self.owned()
        self.assertEqual(await self.client.configure(self.handle, 4, True, True), ())
        changes = [
            (endpoint, data)
            for method, endpoint, data in self.calls
            if method == "POST"
        ]
        self.assertEqual(
            changes[0],
            ("torrents/filePrio", {"hash": HASH, "id": "0|9|2", "priority": "0"}),
        )
        self.assertEqual(
            changes[1],
            ("torrents/filePrio", {"hash": HASH, "id": "4", "priority": "7"}),
        )
        self.assertEqual(
            [endpoint for endpoint, _ in changes[2:]],
            ["torrents/toggleSequentialDownload", "torrents/toggleFirstLastPiecePrio"],
        )

    async def test_full_preparation_through_mock_web_api(self):
        self.auto_add = True
        with tempfile.TemporaryDirectory() as directory:
            engine = PlaybackService(
                self.client, LocalDownloadWorkspace(Path(directory) / "downloads")
            )
            session = await engine.prepare(
                MediaRequest(MediaType.MOVIE, "tt2543164", title="Arrival", year=2016),
                StreamCandidate("test", "Arrival.2016", info_hash=HASH),
            )
            self.assertEqual(session.state, SessionState.DOWNLOADING)
            self.assertEqual(session.verified_file_index, 4)
            await engine.close_session(session.id)
            self.assertEqual(self.calls[-1][1], "torrents/stop")
            self.assertFalse(any("delete" in endpoint for _, endpoint, _ in self.calls))

    async def test_source_and_api_size_limits(self):
        await self.allow_download()
        self.torrent_payload = b"a" * (MAX_TORRENT_BYTES + 1)
        with self.assertRaisesRegex(TorrentError, "source_too_large"):
            await self.client.resolve_source(
                StreamCandidate(
                    "test", "movie", url=str(self.server.make_url("/download"))
                )
            )
        self.raw_responses["torrents/info"] = "x" * (MAX_TORRENT_BYTES + 1)
        with self.assertRaisesRegex(TorrentError, "invalid_response"):
            await self.client.inspect(HASH)

    async def test_already_enabled_preferences_are_not_toggled(self):
        self.owned()
        self.rows[0].update(seq_dl=True, f_l_piece_prio=True)
        await self.client.configure(self.handle, 4, True, True)
        self.assertFalse(any("toggle" in endpoint for _, endpoint, _ in self.calls))

    async def test_unsupported_flags_are_reported_without_blind_toggle(self):
        self.owned()
        self.rows[0].pop("seq_dl")
        self.rows[0].pop("f_l_piece_prio")
        self.assertEqual(
            await self.client.configure(self.handle, 4, True, True),
            ("sequential_unsupported", "first_last_unsupported"),
        )
        self.assertFalse(any("toggle" in endpoint for _, endpoint, _ in self.calls))

    async def test_invalid_index_never_sets_priorities(self):
        self.owned()
        with self.assertRaisesRegex(TorrentError, "invalid_file_index"):
            await self.client.configure(self.handle, 1, True, True)
        self.assertFalse(any(method == "POST" for method, _, _ in self.calls))

    async def test_malformed_metadata_is_not_reindexed(self):
        self.owned()
        self.raw_files[0].pop("index")
        with self.assertRaisesRegex(TorrentError, "invalid_metadata"):
            await self.client.inspect(HASH)

    async def test_duplicate_indices_and_unsafe_paths_rejected(self):
        self.owned()
        self.raw_files[0]["index"] = self.raw_files[1]["index"]
        with self.assertRaisesRegex(TorrentError, "invalid_metadata"):
            await self.client.inspect(HASH)
        self.raw_files[0]["index"] = 4
        self.raw_files[0]["name"] = "../private/" + SECRET
        with self.assertRaisesRegex(TorrentError, "unsafe_path"):
            await self.client.inspect(HASH)
        # Cleanup remains possible even if file metadata is unsafe.
        await self.client.stop(self.handle)

    async def test_invalid_json_and_response_shape(self):
        for value in (SECRET, "{}", "null", "[null]"):
            self.raw_responses["torrents/info"] = value
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(TorrentError, "invalid_response"),
            ):
                await self.client.inspect(HASH)

    async def test_hash_only_source_and_v2_rejection(self):
        source = await self.client.resolve_source(
            StreamCandidate("test", "movie", info_hash=HASH)
        )
        self.assertEqual(source.magnet, "magnet:?xt=urn:btih:" + HASH)
        with self.assertRaisesRegex(TorrentError, "unsupported_source"):
            await self.client.resolve_source(
                StreamCandidate("test", "movie", info_hash="1220" + "b" * 64)
            )

    async def test_download_origin_is_explicit_opt_in(self):
        url = str(self.server.make_url("/download?apikey=" + SECRET))
        candidate = StreamCandidate("test", "movie", url=url)
        with self.assertRaisesRegex(TorrentError, "source_denied"):
            await self.client.resolve_source(candidate)
        self.assertEqual(self.cookies, [])

    async def allow_download(self):
        await self.client.close()
        self.client = QBittorrentClient(
            replace(
                self.settings,
                allowed_download_origins=(str(self.server.make_url("/")),),
            )
        )
        await self.client.connect()
        self.calls.clear()

    async def test_private_download_no_cookies_or_url_rewriting(self):
        await self.allow_download()
        url = (
            str(self.server.make_url("/download"))
            + "?signature=a%2Fb%2Bc&apikey="
            + SECRET
        )
        source = await self.client.resolve_source(
            StreamCandidate("test", "movie", url=url)
        )
        self.assertEqual(source.info_hash, metainfo_hash(TORRENT))
        self.assertEqual(source.torrent_bytes, TORRENT)
        self.assertEqual(self.cookies, [{}])
        self.assertIn("signature=a%2Fb%2Bc", self.download_query)
        self.assertNotIn(SECRET, repr(source))

    async def test_download_redirect_or_html_never_used_as_torrent(self):
        await self.allow_download()
        candidate = StreamCandidate(
            "test", "movie", url=str(self.server.make_url("/download?apikey=" + SECRET))
        )
        for status, payload in ((302, TORRENT), (200, ("<html>" + SECRET).encode())):
            self.torrent_status, self.torrent_payload = status, payload
            with (
                self.subTest(status=status),
                self.assertRaisesRegex(TorrentError, "source_invalid") as error,
            ):
                await self.client.resolve_source(candidate)
            self.assertNotIn(SECRET, str(error.exception))

    async def test_download_hash_mismatch_rejected_before_submission(self):
        await self.allow_download()
        candidate = StreamCandidate(
            "test", "movie", info_hash=HASH, url=str(self.server.make_url("/download"))
        )
        with self.assertRaisesRegex(TorrentError, "hash_mismatch"):
            await self.client.resolve_source(candidate)
        self.assertFalse(any(method == "POST" for method, endpoint, data in self.calls))

    async def test_source_and_credentials_hidden_in_repr(self):
        self.assertNotIn(SECRET, repr(self.settings))
        self.assertNotIn(DIRECTORY, repr(self.handle))
        self.assertEqual(str(TorrentError("remote private " + SECRET)), "client_error")

    async def test_no_delete_interface(self):
        self.assertFalse(hasattr(self.client, "delete"))
        self.assertFalse(hasattr(self.client, "remove"))
