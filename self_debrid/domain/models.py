"""Protocol-independent domain models for Self-Debrid v2."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple

from .identity import (
    magnet_file_index,
    magnet_info_hashes,
    normalize_info_hash,
    normalize_url,
)


class MediaType(str, Enum):
    MOVIE = "movie"
    SERIES = "series"


@dataclass(frozen=True, slots=True)
class MediaRequest:
    media_type: MediaType
    imdb_id: str
    season: Optional[int] = None
    episode: Optional[int] = None
    title: Optional[str] = None
    year: Optional[int] = None

    def __post_init__(self) -> None:
        if self.year is not None and (
            type(self.year) is not int or not 1800 <= self.year <= 2099
        ):
            raise ValueError("year must be between 1800 and 2099")
        if not isinstance(self.media_type, MediaType):
            raise ValueError("media_type must be a MediaType")
        if not isinstance(self.imdb_id, str) or not re.fullmatch(
            r"tt[0-9]+", self.imdb_id
        ):
            raise ValueError("imdb_id must be an IMDb title id such as tt1234567")
        if self.title is not None:
            if (
                not isinstance(self.title, str)
                or not self.title.strip()
                or len(self.title) > 300
            ):
                raise ValueError("title must not be blank")
            object.__setattr__(self, "title", self.title.strip())

        if self.media_type is MediaType.MOVIE:
            if self.season is not None or self.episode is not None:
                raise ValueError("movie requests cannot include season or episode")
            return

        if self.season is None or self.episode is None:
            raise ValueError("series requests require season and episode")

        if any(
            type(value) is not int or value < 0 for value in (self.season, self.episode)
        ):
            raise ValueError("season and episode must be non-negative")

    @property
    def key(self) -> str:
        if self.media_type is MediaType.MOVIE:
            return f"movie:{self.imdb_id}"
        return f"series:{self.imdb_id}:{self.season}:{self.episode}"


@dataclass(frozen=True, slots=True)
class StreamCandidate:
    provider: str
    title: str
    info_hash: Optional[str] = None
    file_index: Optional[int] = None
    url: Optional[str] = field(default=None, repr=False)
    size_bytes: Optional[int] = None
    seeders: Optional[int] = None
    resolution: Optional[str] = None
    codec: Optional[str] = None
    hdr: Optional[str] = None
    languages: Tuple[str, ...] = field(default_factory=tuple)
    cached: bool = False
    season: Optional[int] = None
    episode: Optional[int] = None
    year: Optional[int] = None
    imdb_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider is required")

        if not self.title.strip():
            raise ValueError("title is required")
        if self.year is not None and (
            type(self.year) is not int or not 1800 <= self.year <= 2099
        ):
            raise ValueError("year must be between 1800 and 2099")
        if self.imdb_id is not None and not re.fullmatch(r"tt[0-9]+", self.imdb_id):
            raise ValueError("candidate IMDb ID must be a title ID")

        if self.url is not None:
            object.__setattr__(self, "url", normalize_url(self.url))
        normalized_hash = (
            normalize_info_hash(self.info_hash) if self.info_hash is not None else None
        )
        magnet_hashes = magnet_info_hashes(self.url) if self.url else ()
        if self.url and self.url.startswith("magnet:") and not magnet_hashes:
            raise ValueError("magnet has no supported torrent hash")
        if normalized_hash and magnet_hashes and normalized_hash not in magnet_hashes:
            raise ValueError("candidate hash conflicts with magnet hash")
        object.__setattr__(
            self, "info_hash", magnet_hashes[0] if magnet_hashes else normalized_hash
        )
        selected_file = magnet_file_index(self.url) if self.url else None
        if (
            self.file_index is not None
            and selected_file is not None
            and self.file_index != selected_file
        ):
            raise ValueError("candidate file index conflicts with magnet selection")
        if self.file_index is None:
            object.__setattr__(self, "file_index", selected_file)

        if self.info_hash is None and self.url is None:
            raise ValueError("candidate requires info_hash or url")

        if self.file_index is not None and self.info_hash is None:
            raise ValueError("file_index requires info_hash")

        for name in ("file_index", "size_bytes", "seeders", "season", "episode"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")

        normalized_languages = tuple(
            dict.fromkeys(
                language.lower().strip()
                for language in self.languages
                if language.strip()
            )
        )
        object.__setattr__(self, "languages", normalized_languages)

    @property
    def identity(self) -> str:
        if self.info_hash is not None:
            if self.file_index is not None:
                return f"torrent:{self.info_hash}:{self.file_index}"
            return f"torrent:{self.info_hash}"
        return f"url:{self.url}"


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    provider: str
    error: str
    code: str = "provider_error"


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    candidates: Tuple[StreamCandidate, ...]
    failures: Tuple[ProviderFailure, ...] = field(default_factory=tuple)
    exclusions: Tuple[Tuple[str, int], ...] = field(default_factory=tuple)
    metadata_error: Optional[str] = None
