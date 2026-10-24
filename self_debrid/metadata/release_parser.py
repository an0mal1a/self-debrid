"""Small, explicit release-label parser. Missing information stays unknown."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from self_debrid.domain.episode_selection import is_ambiguous_episode_name


@dataclass(frozen=True, slots=True)
class ReleaseMetadata:
    resolution: str | None = None
    codec: str | None = None
    hdr: str | None = None
    languages: tuple[str, ...] = ()
    season: int | None = None
    episode: int | None = None
    size_bytes: int | None = None
    seeders: int | None = None


def _label(pattern: str, text: str) -> re.Match | None:
    return re.search(r"(?<![a-z0-9])(?:" + pattern + r")(?![a-z0-9])", text, re.I)


def nonnegative_integer(value: str | None) -> int | None:
    # More than 20 decimal digits cannot be useful size/seed/episode metadata.
    # Bound conversion cost, including on Python 3.10 without int string limits.
    if value is None or not re.fullmatch(r"[0-9]{1,20}", value.strip()):
        return None
    return int(value.strip())


_LANGUAGE_LABELS = (
    (r"spanish|español|castellano|spa|esp", "es"),
    (r"english|eng", "en"),
    (r"latino", "es-419"),
    (r"multi", "multi"),
)


def parse_release(
    title: str, attributes: Mapping[str, str] | None = None
) -> ReleaseMetadata:
    attributes = attributes or {}
    resolution_text = attributes.get("resolution", "") + " " + title
    resolution = next(
        (
            value
            for value in ("2160p", "1080p", "720p", "480p")
            if _label(value, resolution_text)
        ),
        None,
    )
    codec_text = attributes.get("video", "") + " " + title
    codec = next(
        (
            value
            for pattern, value in (
                (r"av1", "AV1"),
                (r"h[ ._-]?265|x265|hevc", "H.265"),
                (r"h[ ._-]?264|x264|avc", "H.264"),
            )
            if _label(pattern, codec_text)
        ),
        None,
    )
    hdr = next(
        (
            value
            for pattern, value in (
                (r"dolby[ ._-]+vision|dovi|dv", "Dolby Vision"),
                (r"hdr10\+?", "HDR10"),
                (r"hdr", "HDR"),
            )
            if _label(pattern, title)
        ),
        None,
    )
    language_text = title + " " + attributes.get("language", "")
    languages = tuple(
        value for pattern, value in _LANGUAGE_LABELS if _label(pattern, language_text)
    )
    episode_match = _label(r"s([0-9]{1,2})e([0-9]{1,3})", title)
    season = nonnegative_integer(attributes.get("season"))
    episode = nonnegative_integer(attributes.get("episode"))
    # A range/pack's first episode is not a mapping from file index to episode.
    # For ambiguous names, preserve only explicit upstream coordinates.
    if episode_match and not is_ambiguous_episode_name(title):
        season = season if season is not None else int(episode_match[1])
        episode = episode if episode is not None else int(episode_match[2])
    return ReleaseMetadata(
        resolution=resolution,
        codec=codec,
        hdr=hdr,
        languages=languages,
        season=season,
        episode=episode,
        size_bytes=nonnegative_integer(attributes.get("size")),
        seeders=nonnegative_integer(attributes.get("seeders")),
    )
