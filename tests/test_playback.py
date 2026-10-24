import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from self_debrid.application.playback import PlaybackService, SessionState
from self_debrid.domain.models import MediaRequest, MediaType, StreamCandidate
from self_debrid.domain.torrents import (
    DownloadState,
    TorrentError,
    TorrentFile,
    TorrentHandle,
    TorrentSnapshot,
    TorrentSource,
)
from self_debrid.infrastructure.torrent_config import TorrentEngineSettings
from self_debrid.infrastructure.torrent_engine import (
    LocalDownloadWorkspace,
    open_torrent_engine,
)

HASH = "c" * 40
MOVIE = MediaRequest(MediaType.MOVIE, "tt2543164", title="Arrival", year=2016)
CANDIDATE = StreamCandidate("fixture", "Arrival.2016", info_hash=HASH)


class FakeClient:
    def __init__(self):
        self.events = []
        self.files = (TorrentFile(7, "Arrival.2016/Arrival.2016.mkv", 100, 0.25, 0.8),)
        self.metadata_ready = True
        self.handle = None
        self.state = DownloadState.DOWNLOADING
        self.failure = None
        self.stop_failure = None
        self.connected = asyncio.Event()

    async def connect(self):
        self.events.append("connect")
        self.connected.set()
        if self.failure:
            raise TorrentError(self.failure)

    async def close(self):
        self.events.append("close")

    async def resolve_source(self, candidate):
        self.events.append("source")
        return TorrentSource(
            candidate.info_hash, magnet="magnet:?xt=urn:btih:" + candidate.info_hash
        )

    async def submit(self, source, directory, tag):
        self.events.append("submit")
        self.handle = TorrentHandle(source.info_hash, tag, directory)
        return self.handle

    async def inspect(self, info_hash):
        self.events.append("inspect")
        if self.handle is None:
            return None
        return TorrentSnapshot(
            info_hash,
            self.files if self.metadata_ready else (),
            self.state,
            self.handle.save_directory,
            frozenset({self.handle.owner_tag}),
        )

    async def stop(self, handle):
        self.events.append("stop")
        if self.stop_failure:
            raise TorrentError(self.stop_failure)

    async def configure(self, handle, index, sequential, first_last):
        self.events.append(("configure", index, sequential, first_last))
        return ()

    async def start(self, handle):
        self.events.append("start")


class PlaybackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "downloads"
        self.client = FakeClient()
        self.service = PlaybackService(
            self.client,
            LocalDownloadWorkspace(self.root),
            max_sessions=2,
            max_records=3,
            metadata_timeout=0.05,
            preparation_timeout=0.5,
            operation_timeout=0.1,
            poll_interval=0.001,
        )

    async def test_prepares_verified_index_in_correct_order(self):
        session = await self.service.prepare(MOVIE, CANDIDATE)
        self.assertEqual(session.state, SessionState.DOWNLOADING)
        self.assertIsNone(session.provider_file_index)
        self.assertEqual(session.verified_file_index, 7)
        self.assertEqual(session.media_key, MOVIE.key)
        self.assertEqual(session.info_hash, HASH)
        self.assertEqual(
            self.client.events,
            [
                "connect",
                "source",
                "submit",
                "inspect",
                "stop",
                ("configure", 7, True, True),
                "start",
            ],
        )
        self.assertTrue(session.local_path.is_relative_to(self.root))
        self.assertIsNone(session.available_bytes)
        self.assertEqual(session.download_state, DownloadState.UNKNOWN)
        self.assertRegex(session.id, r"^[a-f0-9]{32}$")
        self.assertEqual(self.service.get(session.id), session)

    async def test_provider_index_distinct_from_verified_selection(self):
        session = await self.service.prepare(MOVIE, replace(CANDIDATE, file_index=7))
        self.assertEqual(session.provider_file_index, session.verified_file_index)
        self.assertEqual(session.verified_file_index, 7)

    async def test_mismatched_index_fails_before_priorities(self):
        session = await self.service.prepare(MOVIE, replace(CANDIDATE, file_index=0))
        self.assertEqual(session.state, SessionState.FAILED)
        self.assertEqual(session.error, "invalid_file_index")
        self.assertIsNone(session.verified_file_index)
        self.assertNotIn("start", self.client.events)
        self.assertFalse(any(isinstance(e, tuple) for e in self.client.events))

    async def test_exact_episode_in_pack_selected_from_actual_files(self):
        self.client.files = (
            TorrentFile(10, "Show/S02E04.mkv", 9999),
            TorrentFile(4, "Show/S02E05.mkv", 100),
        )
        request = MediaRequest(MediaType.SERIES, "tt1234567", 2, 5)
        session = await self.service.prepare(
            request, replace(CANDIDATE, title="Season pack")
        )
        self.assertEqual(session.verified_file_index, 4)

    async def test_ambiguous_movie_rejected_and_stopped(self):
        self.client.files += (TorrentFile(1, "Arrival.2016.720p.mkv", 999999),)
        session = await self.service.prepare(MOVIE, CANDIDATE)
        self.assertEqual(session.error, "ambiguous_files")
        self.assertNotIn("start", self.client.events)
        self.assertEqual(self.client.events[-1], "stop")

    async def test_candidate_identity_contradiction_has_no_side_effect(self):
        session = await self.service.prepare(
            MOVIE, replace(CANDIDATE, imdb_id="tt9999999")
        )
        self.assertEqual(session.error, "no_matching_file")
        self.assertEqual(self.client.events, [])
        self.assertFalse(self.root.exists())

    async def test_candidate_media_and_episode_contradictions(self):
        for request, candidate in (
            (MOVIE, replace(CANDIDATE, episode=5)),
            (MOVIE, replace(CANDIDATE, year=1990)),
            (
                MediaRequest(MediaType.SERIES, "tt1234567", 2, 5),
                replace(CANDIDATE, episode=6),
            ),
        ):
            with self.subTest(request=request):
                self.assertEqual(
                    (await self.service.prepare(request, candidate)).error,
                    "no_matching_file",
                )
        self.assertEqual(self.client.events, [])

    async def test_authentication_failure(self):
        self.client.failure = "authentication_failed"
        session = await self.service.prepare(MOVIE, CANDIDATE)
        self.assertEqual(session.error, "authentication_failed")
        self.assertEqual(self.client.events, ["connect"])

    async def test_uncertain_submission_response_still_stops_owned_work(self):
        submit = self.client.submit

        async def uncertain(*args):
            await submit(*args)
            raise TorrentError("client_timeout")

        self.client.submit = uncertain
        session = await self.service.prepare(MOVIE, CANDIDATE)
        self.assertEqual(session.error, "client_timeout")
        self.assertEqual(self.client.events[-1], "stop")

    async def test_existing_torrent_failure_never_attempts_stop(self):
        async def existing(*args):
            raise TorrentError("torrent_exists")

        self.client.submit = existing
        session = await self.service.prepare(MOVIE, CANDIDATE)
        self.assertEqual(session.error, "torrent_exists")
        self.assertNotIn("stop", self.client.events)

    async def test_metadata_timeout_keeps_bounded_failed_record(self):
        self.client.metadata_ready = False
        session = await self.service.prepare(MOVIE, CANDIDATE)
        self.assertEqual(session.error, "metadata_timeout")
        self.assertEqual(session.state, SessionState.FAILED)
        self.assertEqual(self.client.events[-1], "stop")
        self.assertIsNone(session.verified_file_index)

    async def test_delayed_metadata_can_arrive(self):
        self.client.metadata_ready = False
        task = asyncio.create_task(self.service.prepare(MOVIE, CANDIDATE))
        await self.client.connected.wait()
        await asyncio.sleep(0.005)
        self.client.metadata_ready = True
        self.assertEqual((await task).state, SessionState.DOWNLOADING)

    async def test_overall_preparation_timeout(self):
        self.service.preparation_timeout = 0.005
        self.client.metadata_ready = False
        self.assertEqual(
            (await self.service.prepare(MOVIE, CANDIDATE)).error, "preparation_timeout"
        )

    async def test_cancellation_stops_owned_work_and_preserves_cancel(self):
        self.client.metadata_ready = False
        task = asyncio.create_task(self.service.prepare(MOVIE, CANDIDATE))
        await self.client.connected.wait()
        await asyncio.sleep(0.005)
        session_id = next(iter(self.service._records))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.service.get(session_id).state, SessionState.CANCELLED)
        self.assertEqual(self.client.events[-1], "stop")

    async def test_concurrent_session_limit_fails_without_queuing(self):
        self.service.max_sessions = 1
        self.client.metadata_ready = False
        task = asyncio.create_task(self.service.prepare(MOVIE, CANDIDATE))
        await self.client.connected.wait()
        with self.assertRaisesRegex(TorrentError, "session_limit"):
            await self.service.prepare(MOVIE, CANDIDATE)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_active_torrent_cannot_get_conflicting_second_session(self):
        await self.service.prepare(MOVIE, CANDIDATE)
        with self.assertRaisesRegex(TorrentError, "torrent_exists"):
            await self.service.prepare(MOVIE, CANDIDATE)
        self.assertEqual(self.client.events.count("submit"), 1)

    async def test_hashless_sources_share_torrent_exclusivity_after_resolution(self):
        async def resolved(candidate):
            return TorrentSource(HASH, magnet="magnet:?xt=urn:btih:" + HASH)

        self.client.resolve_source = resolved
        self.client.metadata_ready = False
        candidate = StreamCandidate(
            "test", "Arrival.2016", url="https://indexer.invalid/download"
        )
        first = asyncio.create_task(self.service.prepare(MOVIE, candidate))
        await self.client.connected.wait()
        second = await self.service.prepare(MOVIE, candidate)
        self.assertEqual(second.error, "torrent_exists")
        self.assertEqual(self.client.events.count("submit"), 1)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first

    async def test_close_preserves_files_and_frees_active_slot(self):
        self.service.max_sessions = 1
        session = await self.service.prepare(MOVIE, CANDIDATE)
        session.local_path.parent.mkdir(parents=True)
        session.local_path.write_bytes(b"keep operator data")
        closed = await self.service.close_session(session.id)
        self.assertEqual(closed.state, SessionState.CLOSED)
        self.assertEqual(session.local_path.read_bytes(), b"keep operator data")
        calls = len(self.client.events)
        self.assertEqual(await self.service.close_session(session.id), closed)
        self.assertEqual(len(self.client.events), calls)
        following = await self.service.prepare(MOVIE, CANDIDATE)
        self.assertNotEqual(session.id, following.id)

    async def test_failed_stop_keeps_capacity_reserved_until_recovered(self):
        self.service.max_sessions = 1
        session = await self.service.prepare(MOVIE, CANDIDATE)
        self.client.stop_failure = "client_timeout"
        closed = await self.service.close_session(session.id)
        self.assertEqual(closed.error, "client_timeout")
        with self.assertRaisesRegex(TorrentError, "session_limit"):
            await self.service.prepare(MOVIE, CANDIDATE)
        self.client.stop_failure = None
        await self.service.close_session(session.id)
        self.assertIsNone((await self.service.prepare(MOVIE, CANDIDATE)).error)

    async def test_registry_records_bounded_and_terminal_ttl(self):
        self.client.failure = "authentication_failed"
        sessions = [await self.service.prepare(MOVIE, CANDIDATE) for _ in range(5)]
        self.assertLessEqual(len(self.service._records), 3)
        with self.assertRaisesRegex(TorrentError, "session_not_found"):
            self.service.get(sessions[0].id)
        with patch("self_debrid.application.playback.monotonic", return_value=10**12):
            newest = await self.service.prepare(MOVIE, CANDIDATE)
        self.assertEqual(list(self.service._records), [newest.id])

    async def test_progress_does_not_claim_contiguous_bytes(self):
        session = await self.service.prepare(MOVIE, CANDIDATE)
        refreshed = await self.service.refresh(session.id)
        self.assertEqual(refreshed.progress, 0.25)
        self.assertEqual(refreshed.peer_availability, 0.8)
        self.assertIsNone(refreshed.available_bytes)

    async def test_complete_requires_local_file_and_full_progress(self):
        session = await self.service.prepare(MOVIE, CANDIDATE)
        self.client.files = (replace(self.client.files[0], progress=1),)
        self.assertEqual(
            (await self.service.refresh(session.id)).state, SessionState.DOWNLOADING
        )
        session.local_path.parent.mkdir(parents=True)
        session.local_path.write_bytes(b"a" * 99)
        self.assertIsNone((await self.service.refresh(session.id)).available_bytes)
        session.local_path.write_bytes(b"a" * 100)
        complete = await self.service.refresh(session.id)
        self.assertEqual(complete.state, SessionState.COMPLETE)
        self.assertEqual(complete.available_bytes, 100)

    async def test_refresh_rejects_renamed_files(self):
        session = await self.service.prepare(MOVIE, CANDIDATE)
        self.client.files = (replace(self.client.files[0], path="private/other.mkv"),)
        refreshed = await self.service.refresh(session.id)
        self.assertEqual(refreshed.error, "invalid_metadata")

    async def test_download_error_is_structured(self):
        self.client.state = DownloadState.ERROR
        session = await self.service.prepare(MOVIE, CANDIDATE)
        self.assertEqual(session.error, "client_error")
        self.assertNotIn("start", self.client.events)

    async def test_public_view_and_repr_exclude_locations_and_urls(self):
        session = await self.service.prepare(MOVIE, CANDIDATE)
        output = json.dumps(session.public_view()) + repr(session)
        self.assertNotIn(str(self.root), output)
        self.assertNotIn("Arrival.2016", output)
        self.assertNotIn("magnet:", output)
        self.assertNotIn("local_path", output)

    async def test_unknown_session_and_shutdown(self):
        with self.assertRaisesRegex(TorrentError, "session_not_found"):
            await self.service.refresh("missing")
        session = await self.service.prepare(MOVIE, CANDIDATE)
        await self.service.shutdown()
        self.assertEqual(self.service.get(session.id).state, SessionState.CLOSED)
        with self.assertRaisesRegex(TorrentError, "session_closed"):
            await self.service.prepare(MOVIE, CANDIDATE)

    async def test_context_factory_is_opt_in_and_has_no_eager_network(self):
        settings = TorrentEngineSettings(download_directory=self.root)
        with patch(
            "self_debrid.infrastructure.torrent_engine.QBittorrentClient.connect"
        ) as connect:
            async with open_torrent_engine(settings) as engine:
                self.assertIsInstance(engine, PlaybackService)
                self.assertFalse(self.root.exists())
            connect.assert_not_called()

    async def test_symlink_target_rejected(self):
        directory = self.service.workspace.create("a" * 32)
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (Path(directory) / "nested").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(TorrentError, "unsafe_path"):
            self.service.workspace.resolve(directory, "nested/movie.mkv")

    async def test_workspace_traversal_and_external_directory(self):
        directory = self.service.workspace.create("a" * 32)
        for folder, relative in (
            (directory, "../outside.mkv"),
            (self.tmp.name, "movie.mkv"),
            (str(self.root), "movie.mkv"),
        ):
            with (
                self.subTest(folder=folder),
                self.assertRaisesRegex(TorrentError, "unsafe_path"),
            ):
                self.service.workspace.resolve(folder, relative)

    async def test_workspace_does_not_reuse_directory(self):
        self.service.workspace.create("a" * 32)
        with self.assertRaisesRegex(TorrentError, "unsafe_path"):
            self.service.workspace.create("a" * 32)


class TorrentConfigTests(unittest.TestCase):
    def test_service_rejects_unbounded_or_noninteger_limits(self):
        for limits in (
            {"max_records": 257},
            {"max_sessions": 33, "max_records": 33},
            {"max_sessions": True},
            {"max_records": 3.5},
            {"poll_interval": 0},
            {"metadata_timeout": float("inf")},
        ):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                PlaybackService(
                    FakeClient(), LocalDownloadWorkspace(Path("/tmp/unused")), **limits
                )

    def test_defaults_private_and_loopback(self):
        settings = TorrentEngineSettings.from_env({})
        self.assertEqual(settings.base_url, "http://127.0.0.1:8080")
        self.assertEqual(settings.allowed_download_origins, ())
        self.assertTrue(settings.download_directory.is_absolute())

    def test_explicit_environment_and_immutable_configuration(self):
        settings = TorrentEngineSettings.from_env(
            {
                "QBITTORRENT_URL": "https://qbit.example/qbit",
                "QBITTORRENT_USERNAME": "operator",
                "QBITTORRENT_PASSWORD": "do-not-log-me",
                "TORRENT_DOWNLOAD_DIRECTORY": "/srv/self-debrid",
                "TORRENT_DOWNLOAD_ORIGINS": "https://indexer.example",
                "TORRENT_MAX_SESSIONS": "2",
                "TORRENT_SEQUENTIAL": "false",
            }
        )
        self.assertEqual(settings.max_sessions, 2)
        self.assertFalse(settings.sequential)
        self.assertEqual(
            settings.allowed_download_origins, ("https://indexer.example:443",)
        )
        self.assertNotIn("do-not-log-me", repr(settings))

    def test_invalid_settings_sanitized(self):
        for values in (
            {"QBITTORRENT_URL": "http://user:secret@host"},
            {"TORRENT_DOWNLOAD_DIRECTORY": "relative"},
            {"TORRENT_DOWNLOAD_ORIGINS": "https://host/path?key=secret"},
            {"TORRENT_MAX_SESSIONS": "33"},
            {"TORRENT_READ_TIMEOUT": "nan"},
            {"TORRENT_SEQUENTIAL": "secret"},
            {"TORRENT_MAX_RECORDS": "secret"},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError) as error:
                TorrentEngineSettings.from_env(values)
            self.assertNotIn("secret", str(error.exception))
