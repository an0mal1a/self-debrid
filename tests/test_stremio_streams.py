import base64
import json
import unittest
from dataclasses import replace

from self_debrid.adapters.stremio import build_manifest
from self_debrid.adapters.stremio.streams import MAX_FILE_INDEX, candidate_to_stream
from self_debrid.domain import MediaRequest, MediaType, StreamCandidate
from self_debrid.domain.episode_selection import matches_episode

MOVIE = MediaRequest(MediaType.MOVIE, "tt1234567")
EPISODE = MediaRequest(MediaType.SERIES, "tt1234567", 2, 5)
HASH = "1234567890abcdef1234567890abcdef12345678"


def candidate(**values):
    return StreamCandidate(
        **{
            "provider": "test",
            "title": "Example.Movie.1080p",
            "info_hash": HASH,
            **values,
        }
    )


class StreamMapperTests(unittest.TestCase):
    def test_manifest_is_stream_only_and_semver(self):
        manifest = build_manifest()
        self.assertEqual(manifest["id"], "com.selfdebrid.addon")
        self.assertRegex(
            manifest["version"], r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?$"
        )
        self.assertEqual(manifest["types"], ["movie", "series"])
        self.assertEqual(manifest["catalogs"], [])
        self.assertEqual(
            manifest["resources"],
            [{"name": "stream", "types": ["movie", "series"], "idPrefixes": ["tt"]}],
        )
        self.assertIn("native Stremio", manifest["description"])
        self.assertNotIn("config", manifest)

    def test_movie_emits_useful_metadata_and_known_file_index_zero(self):
        value = candidate(
            file_index=0,
            resolution="1080p",
            codec="H.265",
            hdr="HDR10",
            languages=("es", "en"),
            size_bytes=2 * 1024**3,
            seeders=0,
        )
        stream = candidate_to_stream(value, MOVIE)
        self.assertEqual(stream["infoHash"], HASH)
        self.assertEqual(stream["fileIdx"], 0)
        self.assertEqual(stream["name"], "Self-Debrid · 1080p")
        for text in (
            value.title,
            "H.265",
            "HDR10",
            "es, en",
            "2.00 GiB",
            "Seeders: 0",
            "Provider: test",
        ):
            self.assertIn(text, stream["description"])
        self.assertEqual(set(stream), {"infoHash", "fileIdx", "name", "description"})

    def test_unknown_movie_file_index_is_omitted_with_largest_file_notice(self):
        stream = candidate_to_stream(candidate(), MOVIE)
        self.assertNotIn("fileIdx", stream)
        self.assertIn("largest file", stream["description"])
        self.assertNotIn("Seeders", stream["description"])
        self.assertNotIn("Size:", stream["description"])

    def test_download_urls_never_become_video_streams(self):
        for url in (
            "https://tracker.invalid/release.torrent?apikey=private-key",
            "https://prowlarr.invalid/1/download?apikey=private-key",
            "https://tracker.invalid/details/1",
        ):
            self.assertIsNone(
                candidate_to_stream(candidate(info_hash=None, url=url), MOVIE)
            )
            stream = candidate_to_stream(candidate(url=url), MOVIE)
            self.assertNotIn("url", stream)
            self.assertNotIn("private-key", json.dumps(stream))

    def test_magnet_trackers_are_not_exposed(self):
        url = f"magnet:?xt=urn:btih:{HASH}&so=3&tr=https%3A%2F%2Ftracker.invalid%2Fannounce%3Fpasskey%3Dprivate-key"
        stream = candidate_to_stream(candidate(info_hash=None, url=url), MOVIE)
        self.assertEqual(stream["fileIdx"], 3)
        for value in ("private-key", "tracker.invalid", "magnet:", "sources", "url"):
            self.assertNotIn(value, json.dumps(stream))

    def test_base32_and_hybrid_work_but_v2_only_does_not(self):
        encoded = base64.b32encode(bytes.fromhex(HASH)).decode()
        self.assertEqual(
            candidate_to_stream(candidate(info_hash=encoded), MOVIE)["infoHash"], HASH
        )
        v2 = "1220" + "ab" * 32
        self.assertIsNone(candidate_to_stream(candidate(info_hash=v2), MOVIE))
        self.assertIsNone(
            candidate_to_stream(
                candidate(info_hash=None, url=f"magnet:?xt=urn:btmh:{v2}"), MOVIE
            )
        )
        hybrid = candidate(
            info_hash=v2, url=f"magnet:?xt=urn:btmh:{v2}&xt=urn:btih:{HASH}"
        )
        self.assertEqual(candidate_to_stream(hybrid, MOVIE)["infoHash"], HASH)

    def test_unusable_indices_are_rejected_without_largest_file_fallback(self):
        self.assertIsNone(
            candidate_to_stream(candidate(file_index=MAX_FILE_INDEX + 1), MOVIE)
        )
        self.assertEqual(
            candidate_to_stream(candidate(file_index=MAX_FILE_INDEX), MOVIE)["fileIdx"],
            MAX_FILE_INDEX,
        )

    def test_remote_metadata_and_configured_secret_are_redacted(self):
        value = candidate(
            title="Example https://tracker.invalid/get?apikey=private-key Token="
            + "unknown-secret",
            provider="private-key",
            resolution="1080p private-key",
            languages=("https://host/private-key",),
        )
        stream = candidate_to_stream(value, MOVIE, secrets=("private-key",))
        encoded = json.dumps(stream)
        for secret in ("private-key", "unknown-secret", "https://", "tracker.invalid"):
            self.assertNotIn(secret, encoded)
        self.assertIn("[redacted]", encoded)
        encoded_title = candidate(
            title="Movie https%253A%252F%252Ftracker.invalid%252Fget%253Fpasskey%253Dhidden"
        )
        self.assertNotIn(
            "hidden", json.dumps(candidate_to_stream(encoded_title, MOVIE))
        )

    def test_metadata_is_bounded_and_has_no_remote_control_characters(self):
        value = candidate(
            title="x" * 10000 + "\r\nInjected: value", provider="test\x00\r\n"
        )
        stream = candidate_to_stream(value, MOVIE)
        self.assertLess(len(stream["description"]), 1000)
        self.assertNotIn("\r", stream["description"])
        self.assertNotIn("\x00", stream["description"])


class EpisodeSafetyTests(unittest.TestCase):
    def test_exact_standalone_episode_can_use_documented_largest_file_fallback(self):
        value = candidate(title="Show.S02E05.1080p", season=2, episode=5)
        self.assertTrue(matches_episode(value, EPISODE))
        stream = candidate_to_stream(value, EPISODE)
        self.assertNotIn("fileIdx", stream)
        self.assertIn("largest file", stream["description"])

    def test_exact_episode_with_known_file_index(self):
        value = candidate(title="Show.S02E05.1080p", file_index=3)
        self.assertEqual(candidate_to_stream(value, EPISODE)["fileIdx"], 3)

    def test_obvious_mismatches_are_rejected_even_with_index(self):
        for value in (
            candidate(title="Show.S02E06.1080p"),
            candidate(title="Show.S01E05", file_index=1),
            candidate(title="Show.S02E06", file_index=1, season=2, episode=5),
            candidate(title="Show.S02E06.Complete", file_index=1, season=2, episode=5),
            candidate(title="Show.S02E05", season=1, episode=5),
            candidate(title="Show.S02E05", episode=6),
            candidate(title="Show.S03.Complete", file_index=1, season=2, episode=5),
            candidate(title="Show.Season.3", file_index=1, season=2, episode=5),
            candidate(title="Show.S03E05E06", file_index=1, season=2, episode=5),
        ):
            with self.subTest(value=value):
                self.assertIsNone(candidate_to_stream(value, EPISODE))

    def test_unknown_and_unindexed_season_pack_results_are_rejected(self):
        for title in (
            "Show",
            "Show.S02.Complete",
            "Show.Season.2",
            "Show.S02E05.Pack",
            "Show.S02E01-S02E10",
            "Show.S02E05E06",
            "Show.S02E05-06",
            "Show.S02E05.E06",
            "Show.S02E05.S02E06",
            "Show.S01-S02.Collection",
        ):
            with self.subTest(title=title):
                value = candidate(title=title, season=2, episode=5)
                self.assertIsNone(candidate_to_stream(value, EPISODE))

    def test_pack_index_needs_matching_episode_mapping_not_just_a_number(self):
        pack = candidate(title="Show.S02.Complete", file_index=5, season=2)
        self.assertIsNone(candidate_to_stream(pack, EPISODE))
        mapped = replace(pack, episode=5)
        self.assertEqual(candidate_to_stream(mapped, EPISODE)["fileIdx"], 5)
        self.assertIsNone(candidate_to_stream(replace(mapped, episode=6), EPISODE))

    def test_known_different_files_are_only_returned_for_matching_episode(self):
        values = [
            candidate(
                title="Show.S02.Complete", file_index=index, season=2, episode=episode
            )
            for index, episode in ((1, 4), (2, 5), (3, 6))
        ]
        streams = [
            stream
            for value in values
            if (stream := candidate_to_stream(value, EPISODE))
        ]
        self.assertEqual([stream["fileIdx"] for stream in streams], [2])

    def test_attribute_only_episode_is_allowed_only_with_an_index(self):
        value = candidate(title="Show", season=2, episode=5)
        self.assertIsNone(candidate_to_stream(value, EPISODE))
        self.assertEqual(
            candidate_to_stream(replace(value, file_index=0), EPISODE)["fileIdx"], 0
        )

    def test_movies_reject_explicit_episode_results(self):
        self.assertIsNone(candidate_to_stream(candidate(title="Show.S02E05"), MOVIE))

    def test_release_parser_does_not_create_mapping_from_first_pack_episode(self):
        from self_debrid.metadata.release_parser import parse_release

        for title in ("Show.S02E01-S02E10", "Show.S02E05.Pack", "Show.S02E01.S02E02"):
            metadata = parse_release(title)
            self.assertEqual((metadata.season, metadata.episode), (None, None))
        metadata = parse_release("Show.S02E01-S02E10", {"season": "2", "episode": "5"})
        self.assertEqual((metadata.season, metadata.episode), (2, 5))
