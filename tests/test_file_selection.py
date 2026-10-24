import json
import unittest
from pathlib import Path

from self_debrid.domain.file_selection import select_video_file
from self_debrid.domain.models import MediaRequest, MediaType
from self_debrid.domain.torrents import TorrentError, TorrentFile, relative_parts

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures/torrent_files.json").read_text()
)
MOVIE = MediaRequest(MediaType.MOVIE, "tt2543164", title="Arrival", year=2016)
EPISODE = MediaRequest(
    MediaType.SERIES, "tt1234567", season=2, episode=5, title="Example Show"
)


def files(kind):
    return tuple(
        TorrentFile(
            f["index"], f["name"], f["size"], f["progress"], f.get("availability")
        )
        for f in FIXTURES[kind]
    )


def video(name, index=0, size=100):
    return TorrentFile(index, name, size)


class FileSelectionTests(unittest.TestCase):
    def test_single_movie(self):
        result = select_video_file(MOVIE, (video("Arrival.2016.1080p.mkv"),))
        self.assertEqual(result.file.index, 0)
        self.assertFalse(result.provider_index_verified)

    def test_movie_samples_trailers_and_subtitles(self):
        self.assertEqual(select_video_file(MOVIE, files("movie")).file.index, 4)

    def test_exact_episode_in_season_pack_not_largest_file(self):
        self.assertEqual(select_video_file(EPISODE, files("season")).file.index, 2)

    def test_episode_without_hint_verifies_actual_index(self):
        result = select_video_file(EPISODE, (video("S02E05.mkv", 12),))
        self.assertEqual(result.file.index, 12)

    def test_common_episode_formats(self):
        for name in (
            "Example.Show.S02E05.720p.mkv",
            "Example Show s2e5 1080p.mp4",
            "Example.Show.2x05.mkv",
            "S02.E05.mkv",
            "s02_e05.mkv",
        ):
            with self.subTest(name=name):
                self.assertIsNotNone(select_video_file(EPISODE, (video(name),)).file)

    def test_wrong_episode_rejected_despite_index_hint(self):
        result = select_video_file(EPISODE, (video("Example.Show.S02E06.mkv"),), 0)
        self.assertEqual(result.error, "file_index_mismatch")

    def test_wrong_season_rejected(self):
        self.assertEqual(
            select_video_file(EPISODE, (video("Example.Show.S01E05.mkv"),)).error,
            "no_matching_file",
        )

    def test_different_series_rejected(self):
        self.assertEqual(
            select_video_file(EPISODE, (video("Other.Show.S02E05.mkv"),)).error,
            "no_matching_file",
        )

    def test_series_year_suffix(self):
        self.assertIsNotNone(
            select_video_file(EPISODE, (video("Example.Show.2020.S02E05.mkv"),)).file
        )

    def test_season_pack_with_no_episode_evidence_rejected(self):
        self.assertEqual(
            select_video_file(EPISODE, (video("Example.Show.Season.2.mkv"),)).error,
            "no_matching_file",
        )

    def test_multi_episode_files_rejected(self):
        for name in (
            "S02E05E06.mkv",
            "S02E05-E06.mkv",
            "S02E05-06.mkv",
            "S02E05.S02E06.mkv",
            "2x05-06.mkv",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    select_video_file(EPISODE, (video(name),)).error, "no_matching_file"
                )

    def test_multiple_episode_versions_ambiguous(self):
        values = (video("S02E05.720p.mkv"), video("S02E05.1080p.mkv", 1))
        self.assertEqual(select_video_file(EPISODE, values).error, "ambiguous_files")
        self.assertTrue(select_video_file(EPISODE, values, 1).provider_index_verified)

    def test_provider_index_must_exist(self):
        for index in (999, -1, True):
            with self.subTest(index=index):
                self.assertEqual(
                    select_video_file(MOVIE, files("movie"), index).error,
                    "invalid_file_index",
                )

    def test_provider_index_must_identify_playable_file(self):
        self.assertEqual(
            select_video_file(MOVIE, files("movie"), 2).error, "file_index_mismatch"
        )

    def test_provider_index_can_confirm_identity(self):
        result = select_video_file(MOVIE, files("movie"), 4)
        self.assertTrue(result.provider_index_verified)

    def test_movie_title_and_year_contradictions(self):
        for name in ("Other.Movie.2016.mkv", "Arrival.1996.mkv", "Arrival.S02E05.mkv"):
            with self.subTest(name=name):
                self.assertEqual(
                    select_video_file(MOVIE, (video(name),)).error, "no_matching_file"
                )

    def test_movie_unknown_year_stays_unknown(self):
        self.assertIsNotNone(
            select_video_file(MOVIE, (video("Arrival.1080p.mkv"),)).file
        )

    def test_numeric_movie_title_not_confused_with_year(self):
        request = MediaRequest(MediaType.MOVIE, "tt8579674", title="1917", year=2019)
        self.assertIsNotNone(
            select_video_file(request, (video("1917.2019.1080p.mkv"),)).file
        )

    def test_known_sequel_part_label_is_not_a_split_file(self):
        request = MediaRequest(
            MediaType.MOVIE, "tt1234567", title="Example Part 2", year=2020
        )
        self.assertIsNotNone(
            select_video_file(request, (video("Example.Part.2.2020.mkv"),)).file
        )
        self.assertIsNone(
            select_video_file(request, (video("Example.Part.2.2020.CD1.mkv"),)).file
        )

    def test_release_group_and_unicode_titles(self):
        self.assertIsNotNone(
            select_video_file(MOVIE, (video("[Group] Arrival.2016.mkv"),)).file
        )
        request = MediaRequest(MediaType.MOVIE, "tt1234567", title="電影一")
        self.assertIsNotNone(
            select_video_file(request, (video("電影一.1080p.mkv"),)).file
        )
        self.assertIsNone(select_video_file(request, (video("電影二.1080p.mkv"),)).file)

    def test_movie_generic_filename_uses_immediate_parent(self):
        self.assertIsNotNone(
            select_video_file(MOVIE, (video("Arrival.2016/movie.mkv"),)).file
        )
        self.assertIsNone(
            select_video_file(MOVIE, (video("Arrival.2016/Other.Movie.mkv"),)).file
        )

    def test_movie_variants_require_hint(self):
        values = (video("Arrival.2016.720p.mkv"), video("Arrival.2016.1080p.mkv", 1))
        self.assertEqual(select_video_file(MOVIE, values).error, "ambiguous_files")
        self.assertEqual(select_video_file(MOVIE, values, 1).file.index, 1)

    def test_unknown_movie_identity_cannot_use_hint_to_choose_collection(self):
        values = (video("Movie.One.mkv"), video("Movie.Two.mkv", 1))
        request = MediaRequest(MediaType.MOVIE, "tt1234567")
        self.assertEqual(select_video_file(request, values, 0).error, "ambiguous_files")

    def test_split_movie_not_silently_truncated(self):
        for name in ("Arrival.2016.CD1.mkv", "Arrival.2016.Part.2.mkv"):
            self.assertEqual(
                select_video_file(MOVIE, (video(name),), 0).error, "file_index_mismatch"
            )

    def test_no_video_files(self):
        self.assertEqual(
            select_video_file(
                MOVIE, (video("Arrival.2016.srt"), video("cover.jpg", 1))
            ).error,
            "no_matching_file",
        )

    def test_empty_duplicate_and_zero_size_metadata(self):
        self.assertEqual(select_video_file(MOVIE, ()).error, "invalid_metadata")
        self.assertEqual(
            select_video_file(MOVIE, (video("a.mkv"), video("b.mkv"))).error,
            "invalid_metadata",
        )
        self.assertEqual(
            select_video_file(MOVIE, (video("Arrival.2016.mkv", size=0),)).error,
            "no_matching_file",
        )

    def test_nested_windows_paths(self):
        self.assertEqual(
            select_video_file(
                EPISODE, (video(r"Show\Season 02\S02E05.mkv", 7),)
            ).file.index,
            7,
        )

    def test_extras_are_not_episode_candidates(self):
        for name in ("Extras/S02E05.mkv", "S02E05-sample.mkv", "Trailers/S02E05.mp4"):
            self.assertIsNone(select_video_file(EPISODE, (video(name),)).file)

    def test_unsafe_paths_rejected(self):
        for name in (
            "../a.mkv",
            "/a.mkv",
            r"C:\a.mkv",
            r"\\server\share\a.mkv",
            "a//b.mkv",
            "a/./b.mkv",
            "a/../b.mkv",
            "a.mkv:stream",
            "NUL.mkv",
            "a\x00.mkv",
            "a /b.mkv",
            "a./b.mkv",
        ):
            with self.subTest(name=name), self.assertRaises(TorrentError):
                relative_parts(name)

    def test_invalid_numeric_metadata(self):
        for kwargs in (
            {"index": True},
            {"size_bytes": -1},
            {"progress": 1.1},
            {"availability": float("nan")},
            {"progress": 10**400},
            {"availability": 2},
            {"size_bytes": 2**63},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(TorrentError):
                TorrentFile(
                    **dict(
                        {"index": 0, "path": "movie.mkv", "size_bytes": 100}, **kwargs
                    )
                )

    def test_sensitive_paths_not_in_repr(self):
        self.assertNotIn("private-name", repr(video("private-name.mkv")))
