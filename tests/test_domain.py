import base64
import itertools
import unittest
from dataclasses import replace

from self_debrid.adapters.stremio import parse_stream_request
from self_debrid.domain import MediaRequest, MediaType, StreamCandidate
from self_debrid.domain.deduplication import deduplicate_candidates
from self_debrid.domain.identity import magnet_file_index, normalize_info_hash
from self_debrid.domain.ranking import RankingPreferences, rank_candidates
from self_debrid.metadata.release_parser import parse_release

HASH = "abcdef0123456789abcdef0123456789abcdef01"
BASE32 = base64.b32encode(bytes.fromhex(HASH)).decode()


def candidate(**kwargs):
    return StreamCandidate(
        **{"provider": "test", "title": "Example", "info_hash": HASH, **kwargs}
    )


class MediaRequestTests(unittest.TestCase):
    def test_movies_reject_episode_coordinates(self):
        for coordinates in ({"season": 0}, {"episode": 1}, {"season": 1, "episode": 2}):
            with self.subTest(coordinates=coordinates), self.assertRaises(ValueError):
                MediaRequest(MediaType.MOVIE, "tt1234567", **coordinates)

    def test_imdb_requires_ascii_digits_and_prefix(self):
        for value in ("1234567", "tt", "tt1:1:2", "tt１２３", "tt123\n", "TT123", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MediaRequest(MediaType.MOVIE, value)

    def test_media_type_must_be_enum(self):
        with self.assertRaises(ValueError):
            MediaRequest("movie", "tt1234567")

    def test_series_coordinates_are_nonnegative_integers(self):
        for season, episode in (
            (None, 1),
            (1, None),
            (-1, 1),
            (1, -1),
            (True, 1),
            (1, 1.5),
        ):
            with (
                self.subTest(season=season, episode=episode),
                self.assertRaises(ValueError),
            ):
                MediaRequest(MediaType.SERIES, "tt123", season, episode)
        self.assertEqual(
            MediaRequest(MediaType.SERIES, "tt123", 0, 0).key, "series:tt123:0:0"
        )

    def test_title_is_optional_but_not_empty(self):
        self.assertEqual(
            MediaRequest(MediaType.MOVIE, "tt123", title=" Title ").title, "Title"
        )
        with self.assertRaises(ValueError):
            MediaRequest(MediaType.MOVIE, "tt123", title=" ")

    def test_stremio_rejects_invalid_ids(self):
        for media_type, raw_id in (
            ("other", "tt123"),
            ("movie", "tt123:1:2"),
            ("series", "tt123"),
            ("series", "tt123:x:2"),
            ("series", "tt123:1:-1"),
            ("series", "tt123:1:2:3"),
        ):
            with self.subTest(raw_id=raw_id), self.assertRaises(ValueError):
                parse_stream_request(media_type, raw_id)


class IdentityTests(unittest.TestCase):
    def test_v1_hex_base32_and_urn_equivalence(self):
        for value in (
            HASH.upper(),
            " " + HASH + " ",
            BASE32,
            BASE32.lower(),
            "urn:btih:" + HASH,
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_info_hash(value), HASH)

    def test_encoded_magnet_repeated_topics(self):
        uri = f"magnet:?xt=urn:sha1:irrelevant&xt=urn%3Abtih%3Abad&xt=urn%3Abtih%3A{BASE32}&dn=Title"
        self.assertEqual(normalize_info_hash(uri), HASH)
        self.assertEqual(candidate(info_hash=None, url=uri).info_hash, HASH)

    def test_v2_multihash_and_hybrid_magnet(self):
        digest = "ab" * 32
        v2 = "1220" + digest
        for value in (digest, v2, "urn:btmh:" + v2, "magnet:?xt=urn:btmh:" + v2):
            self.assertEqual(normalize_info_hash(value), v2)
        self.assertEqual(
            normalize_info_hash(f"magnet:?xt=urn:btmh:{v2}&xt=urn:btih:{HASH}"), HASH
        )
        hybrid = f"magnet:?xt=urn:btmh:{v2}&xt=urn:btih:{HASH}"
        self.assertEqual(candidate(info_hash=v2, url=hybrid).info_hash, HASH)

    def test_invalid_hashes_are_rejected(self):
        for value in (
            "",
            "abc",
            "g" * 40,
            "0" * 32,
            "urn:btih:" + "a" * 64,
            "magnet:?dn=only-title",
            "urn:btmh:1320" + "a" * 64,
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_info_hash(value)
        with self.assertRaises(ValueError):
            normalize_info_hash(f"magnet:?xt=urn:btih:{HASH}&xt=urn:btih:" + "a" * 40)

    def test_magnet_single_file_selection(self):
        uri = f"magnet:?xt=urn:btih:{HASH}&so=0"
        self.assertEqual(candidate(info_hash=None, url=uri).file_index, 0)
        for suffix in ("so=1-3", "so=1,2", "so=1&so=2", "so=-1"):
            self.assertIsNone(magnet_file_index(uri.split("&so=")[0] + "&" + suffix))

    def test_candidate_validates_metadata_and_hash_conflicts(self):
        for field in ("file_index", "size_bytes", "seeders", "season", "episode"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                candidate(**{field: -1})
        with self.assertRaises(ValueError):
            candidate(url="magnet:?xt=urn:btih:" + "1" * 40)
        with self.assertRaises(ValueError):
            candidate(
                info_hash=None, url="https://example.invalid/torrent", file_index=1
            )
        with self.assertRaises(ValueError):
            candidate(url="file:///private")

    def test_url_normalization_preserves_signed_query(self):
        value = candidate(
            info_hash=None, url="HTTPS://EXAMPLE.INVALID/file?b=2&a=%2F#fragment"
        )
        self.assertEqual(value.identity, "url:https://example.invalid/file?b=2&a=%2F")
        self.assertEqual(
            candidate(info_hash=None, url="https://User:Token@HOST/file").url,
            "https://User:Token@host/file",
        )
        self.assertNotIn("Token", repr(candidate(url="https://host/get?apikey=Token")))


class DeduplicationTests(unittest.TestCase):
    def test_equivalent_hashes_merge_useful_metadata_deterministically(self):
        a = candidate(
            provider="a",
            languages=("ES",),
            seeders=1,
            resolution="1080p",
            size_bytes=100,
        )
        b = candidate(
            provider="b", info_hash=BASE32, languages=("en",), seeders=10, cached=True
        )
        result = deduplicate_candidates([a, b])
        self.assertEqual(result, deduplicate_candidates([b, a]))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].provider, "b")
        self.assertEqual(result[0].languages, ("en", "es"))
        self.assertEqual(result[0].resolution, "1080p")
        self.assertEqual(result[0].size_bytes, 100)
        self.assertEqual(result[0].seeders, 10)
        self.assertTrue(result[0].cached)

    def test_different_files_never_collapse(self):
        results = deduplicate_candidates(
            [
                candidate(file_index=0),
                candidate(file_index=1),
                candidate(provider="b", file_index=1),
            ]
        )
        self.assertEqual({result.file_index for result in results}, {0, 1})

    def test_unknown_file_does_not_guess_selection_or_copy_pack_size(self):
        results = deduplicate_candidates(
            [candidate(size_bytes=1000), candidate(file_index=5)]
        )
        self.assertEqual({item.file_index for item in results}, {None, 5})
        self.assertIsNone(
            next(item for item in results if item.file_index == 5).size_bytes
        )
        results = deduplicate_candidates(
            [candidate(), candidate(file_index=1), candidate(file_index=2)]
        )
        self.assertEqual({result.file_index for result in results}, {None, 1, 2})

    def test_url_identity_collapses_across_providers(self):
        a = candidate(info_hash=None, url="https://example.invalid/torrent")
        self.assertEqual(
            len(deduplicate_candidates([a, replace(a, provider="other")])), 1
        )

    def test_duplicates_with_unknown_seeders_remain_unknown(self):
        self.assertIsNone(
            deduplicate_candidates([candidate(), candidate(provider="other")])[
                0
            ].seeders
        )

    def test_explicit_file_index_conflicts_are_rejected(self):
        with self.assertRaises(ValueError):
            candidate(file_index=5, url=f"magnet:?xt=urn:btih:{HASH}&so=3")


class ReleaseParserTests(unittest.TestCase):
    def test_supported_resolutions_and_codecs(self):
        for value in ("2160p", "1080p", "720p", "480p"):
            with self.subTest(value=value):
                self.assertEqual(parse_release(f"Title_{value}_WEB").resolution, value)
        for label, value in (
            ("h264", "H.264"),
            ("H.264", "H.264"),
            ("x264", "H.264"),
            ("H265", "H.265"),
            ("HEVC", "H.265"),
            ("x265", "H.265"),
            ("AV1", "AV1"),
        ):
            with self.subTest(label=label):
                self.assertEqual(parse_release("Title." + label).codec, value)

    def test_hdr_labels(self):
        for label, value in (
            ("HDR", "HDR"),
            ("HDR10", "HDR10"),
            ("Dolby.Vision", "Dolby Vision"),
            ("DoVi", "Dolby Vision"),
            ("DV.HDR10", "Dolby Vision"),
        ):
            self.assertEqual(parse_release("Title." + label).hdr, value)

    def test_explicit_language_labels(self):
        result = parse_release("Title.Spanish.ENG.Latino.MULTI")
        self.assertEqual(result.languages, ("es", "en", "es-419", "multi"))
        self.assertEqual(parse_release("Title.CASTELLANO").languages, ("es",))
        self.assertEqual(parse_release("Title.Español").languages, ("es",))

    def test_no_guessed_metadata_or_substring_matches(self):
        for title in (
            "Spartacus.2010",
            "Engagement.2024",
            "Multiplication",
            "The.EN.ES.LAT",
            "Title.12160p.x2650.S02E0500",
            "Unknown.4K.UHD.DUAL",
        ):
            result = parse_release(title)
            self.assertIsNone(result.resolution)
            self.assertIsNone(result.codec)
            self.assertEqual(result.languages, ())
            self.assertIsNone(result.season)

    def test_episode_identifier_and_attributes(self):
        result = parse_release("Show.S02E05.1080p", {"size": "1000", "seeders": "0"})
        self.assertEqual(
            (result.season, result.episode, result.size_bytes, result.seeders),
            (2, 5, 1000, 0),
        )
        result = parse_release(
            "Show", {"season": "0", "episode": "1", "language": "English Spanish"}
        )
        self.assertEqual(
            (result.season, result.episode, result.languages), (0, 1, ("es", "en"))
        )

    def test_missing_and_invalid_numeric_attributes(self):
        for attrs in (
            {},
            {"size": "unknown", "seeders": "-1"},
            {"size": "1.2GB", "seeders": "1.5"},
            {"size": "1" * 5000, "seeders": "1" * 5000},
        ):
            result = parse_release("Unknown", attrs)
            self.assertIsNone(result.size_bytes)
            self.assertIsNone(result.seeders)


class RankingTests(unittest.TestCase):
    def test_preferences_resolution_language_and_seeds_are_lexicographic(self):
        prefs = RankingPreferences(
            resolution_order=("1080p", "2160p"), preferred_languages=(" ES ", "en")
        )
        candidates = [
            candidate(info_hash=str(i) * 40, **values)
            for i, values in enumerate(
                (
                    {"resolution": "2160p", "languages": ("es",), "seeders": 100},
                    {"resolution": "1080p", "languages": ("en",), "seeders": 100},
                    {"resolution": "1080p", "languages": ("es",), "seeders": 2},
                    {"resolution": "1080p", "languages": ("es",), "seeders": 10},
                )
            )
        ]
        self.assertEqual(rank_candidates(candidates, prefs), list(reversed(candidates)))

    def test_unknown_metadata_is_not_filtered_cached_ignores_seed_minimum(self):
        prefs = RankingPreferences(max_size_bytes=100, min_seeders=5)
        unknown = candidate(info_hash="0" * 40)
        cached = candidate(cached=True, seeders=0, size_bytes=100)
        self.assertEqual(rank_candidates([unknown, cached], prefs), [cached, unknown])
        self.assertEqual(rank_candidates([replace(cached, size_bytes=101)], prefs), [])

    def test_ties_are_stable_under_all_input_orders(self):
        candidates = [candidate(info_hash=str(i) * 40) for i in range(3)]
        expected = rank_candidates(candidates)
        for permuted in itertools.permutations(candidates):
            self.assertEqual(rank_candidates(permuted), expected)
        self.assertEqual(candidates[0].info_hash, "0" * 40)

    def test_invalid_preferences(self):
        for values in (
            {"max_size_bytes": -1},
            {"min_seeders": -1},
            {"min_seeders": True},
            {"resolution_order": ("",)},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                RankingPreferences(**values)
