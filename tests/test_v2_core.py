import unittest

from self_debrid.domain import MediaRequest, MediaType, StreamCandidate
from self_debrid.providers.registry import ProviderRegistry
from self_debrid.ranking import RankingPreferences, rank_candidates
from self_debrid.stremio import build_manifest, parse_stream_request


class FakeProvider:
    def __init__(self, name, candidates=None, error=None):
        self._name = name
        self._candidates = candidates or []
        self._error = error

    @property
    def name(self):
        return self._name

    async def search(self, request):
        if self._error is not None:
            raise RuntimeError(self._error)
        return self._candidates


class DomainTests(unittest.TestCase):
    def test_movie_request_key(self):
        request = MediaRequest(MediaType.MOVIE, "tt1234567")
        self.assertEqual(request.key, "movie:tt1234567")

    def test_series_requires_episode_coordinates(self):
        with self.assertRaises(ValueError):
            MediaRequest(MediaType.SERIES, "tt1234567")

    def test_torrent_identity_uses_file_index(self):
        candidate = StreamCandidate(
            provider="test",
            title="Example",
            info_hash="ABCDEF",
            file_index=3,
        )
        self.assertEqual(candidate.identity, "torrent:abcdef:3")


class StremioTests(unittest.TestCase):
    def test_manifest_only_advertises_stream_resource(self):
        manifest = build_manifest()
        self.assertEqual(manifest["catalogs"], [])
        self.assertEqual(manifest["resources"][0]["name"], "stream")
        self.assertEqual(manifest["resources"][0]["idPrefixes"], ["tt"])

    def test_parse_movie(self):
        request = parse_stream_request("movie", "tt1234567")
        self.assertEqual(request, MediaRequest(MediaType.MOVIE, "tt1234567"))

    def test_parse_series(self):
        request = parse_stream_request("series", "tt1234567:2:5")
        self.assertEqual(
            request,
            MediaRequest(MediaType.SERIES, "tt1234567", season=2, episode=5),
        )


class RankingTests(unittest.TestCase):
    def test_cached_candidate_wins_before_resolution(self):
        candidates = [
            StreamCandidate(
                provider="one",
                title="4K",
                info_hash="a",
                resolution="2160p",
                seeders=100,
            ),
            StreamCandidate(
                provider="two",
                title="Cached 1080p",
                info_hash="b",
                resolution="1080p",
                seeders=1,
                cached=True,
            ),
        ]

        ranked = rank_candidates(candidates)
        self.assertEqual(ranked[0].title, "Cached 1080p")

    def test_size_and_seed_filters(self):
        preferences = RankingPreferences(
            max_size_bytes=10_000,
            min_seeders=5,
        )
        candidates = [
            StreamCandidate(
                provider="one",
                title="Too large",
                info_hash="a",
                size_bytes=20_000,
                seeders=100,
            ),
            StreamCandidate(
                provider="one",
                title="Too few seeders",
                info_hash="b",
                size_bytes=5_000,
                seeders=2,
            ),
            StreamCandidate(
                provider="one",
                title="Eligible",
                info_hash="c",
                size_bytes=5_000,
                seeders=10,
            ),
        ]

        ranked = rank_candidates(candidates, preferences)
        self.assertEqual([candidate.title for candidate in ranked], ["Eligible"])


class ProviderRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_deduplicates_and_isolates_provider_failure(self):
        weaker = StreamCandidate(
            provider="one",
            title="Same torrent",
            info_hash="ABC",
            seeders=5,
        )
        stronger = StreamCandidate(
            provider="two",
            title="Same torrent",
            info_hash="abc",
            seeders=20,
        )

        registry = ProviderRegistry(
            [
                FakeProvider("one", [weaker]),
                FakeProvider("two", [stronger]),
                FakeProvider("broken", error="offline"),
            ]
        )

        result = await registry.search(
            MediaRequest(MediaType.MOVIE, "tt1234567"),
        )

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].provider, "two")
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].provider, "broken")


if __name__ == "__main__":
    unittest.main()
