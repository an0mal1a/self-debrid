"""Exact selection from actual torrent metadata. Never select by file size."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath

from .models import MediaRequest, MediaType
from .torrents import TorrentFile

VIDEO = frozenset(
    {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".webm", ".mpg", ".mpeg", ".ts", ".m2ts"}
)
EXTRA = re.compile(
    r"(?:^|[\W_])(samples?|trailers?|extras?|featurettes?|bonus|deleted[ ._-]scenes|behind[ ._-]the[ ._-]scenes)(?:$|[\W_])",
    re.I,
)
EPISODE = re.compile(
    r"(?<![a-z0-9])(?:s(\d{1,3})[ ._-]*e(\d{1,3})|(\d{1,3})x(\d{1,3}))(?!\d)", re.I
)
MULTI_EPISODE = re.compile(
    r"^(?:[ ._-]*e\d|[ ._-]*s\d|[ ._-]+\d{1,3}(?![a-z0-9]))", re.I
)
YEAR = re.compile(r"(?<![a-z0-9])((?:18|19|20)\d{2})(?![a-z0-9])", re.I)
TECH = re.compile(
    r"(?<![a-z0-9])(?:2160p|1080p|720p|480p|bluray|blu[ ._-]?ray|web[ ._-]?dl|webrip|dvdrip|hdtv|x26[45]|h26[45]|hevc|av1)(?![a-z0-9])",
    re.I,
)
PART = re.compile(r"(?<![a-z0-9])(?:cd|disc|disk|part)[ ._-]*\d+(?![a-z0-9])", re.I)


def _words(value: str) -> str:
    value = re.sub(r"^(?:\s*\[[^\]\r\n]{1,80}\]\s*)+", "", value)
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join(
        re.findall(
            r"[^\W_]+", "".join(c for c in value if not unicodedata.combining(c))
        )
    )


def _movie_identity(name: str) -> tuple[str, int | None]:
    # A numeric title (e.g. 1917.2019) is not itself a release year.
    year = next((m for m in YEAR.finditer(name) if m.start() > 0), None)
    tech = TECH.search(name)
    end = min([match.start() for match in (year, tech) if match] or [len(name)])
    return _words(name[:end]), int(year.group()) if year else None


@dataclass(frozen=True, slots=True)
class FileSelection:
    file: TorrentFile | None = None
    error: str | None = None
    eligible_indices: tuple[int, ...] = ()
    provider_index_verified: bool = False


def select_video_file(
    request: MediaRequest,
    files: tuple[TorrentFile, ...],
    provider_index: int | None = None,
) -> FileSelection:
    """Provider indices are hints, usable only after independent identity checks."""
    indices = [f.index for f in files]
    if not files or len(indices) != len(set(indices)):
        return FileSelection(error="invalid_metadata")
    if provider_index is not None and (
        type(provider_index) is not int or provider_index not in indices
    ):
        return FileSelection(error="invalid_file_index")
    eligible = []
    for file in files:
        path = PurePosixPath(file.path.replace("\\", "/"))
        name = path.stem
        if (
            path.suffix.lower() not in VIDEO
            or not file.size_bytes
            or EXTRA.search(str(path))
        ):
            continue
        episodes = list(EPISODE.finditer(name))
        if request.media_type is MediaType.SERIES:
            if len(episodes) != 1:
                continue
            match = episodes[0]
            season, episode = (
                int(x)
                for x in (
                    match.group(1) or match.group(3),
                    match.group(2) or match.group(4),
                )
            )
            if (season, episode) != (
                request.season,
                request.episode,
            ) or MULTI_EPISODE.match(name[match.end() :]):
                continue
            # An explicit series prefix must agree; a bare S02E05 is sufficient.
            prefix = _words(name[: match.start()])
            if request.title and prefix and prefix != _words(request.title):
                # A release year immediately after the series title is common.
                if re.sub(r" (?:18|19|20)\d{2}$", "", prefix) != _words(request.title):
                    continue
        else:
            title_parts = {
                _words(match.group()).replace(" ", "")
                for match in PART.finditer(request.title or "")
            }
            split_parts = [
                _words(match.group()).replace(" ", "") for match in PART.finditer(name)
            ]
            if episodes or any(part not in title_parts for part in split_parts):
                continue
            title, year = _movie_identity(name)
            if request.year and year and request.year != year:
                continue
            if request.title and title != _words(request.title):
                # Generic video filenames may take identity from their immediate directory.
                if (
                    title not in {"video", "movie", "main", "feature"}
                    or len(path.parts) < 2
                ):
                    continue
                parent_title, parent_year = _movie_identity(path.parent.name)
                if parent_title != _words(request.title) or (
                    request.year and parent_year and request.year != parent_year
                ):
                    continue
        eligible.append(file)
    accepted = tuple(file.index for file in eligible)
    if provider_index is not None:
        selected = next((f for f in eligible if f.index == provider_index), None)
        if selected is None:
            return FileSelection(error="file_index_mismatch", eligible_indices=accepted)
        # Without a trusted movie title, an index cannot resolve multiple unrelated films.
        if (
            len(eligible) > 1
            and request.media_type is MediaType.MOVIE
            and not request.title
        ):
            return FileSelection(error="ambiguous_files", eligible_indices=accepted)
        return FileSelection(
            selected, eligible_indices=accepted, provider_index_verified=True
        )
    if len(eligible) != 1:
        return FileSelection(
            error="ambiguous_files" if eligible else "no_matching_file",
            eligible_indices=accepted,
        )
    return FileSelection(eligible[0], eligible_indices=accepted)
