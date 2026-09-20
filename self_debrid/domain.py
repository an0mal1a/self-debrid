"""Protocol-independent domain models for Self-Debrid v2."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple


class MediaType(str, Enum):
    MOVIE = "movie"
    SERIES = "series"


@dataclass(frozen=True, slots=True)
class MediaRequest:
    media_type: MediaType
    imdb_id: str
    season: Optional[int] = None
    episode: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.imdb_id.startswith("tt") or not self.imdb_id[2:].isdigit():
            raise ValueError("imdb_id must be an IMDb title id such as tt1234567")

        if self.media_type is MediaType.MOVIE:
            if self.season is not None or self.episode is not None:
                raise ValueError("movie requests cannot include season or episode")
            return

        if self.season is None or self.episode is None:
            raise ValueError("series requests require season and episode")

        if self.season < 0 or self.episode < 0:
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
    url: Optional[str] = None
    size_bytes: Optional[int] = None
    seeders: Optional[int] = None
    resolution: Optional[str] = None
    codec: Optional[str] = None
    hdr: Optional[str] = None
    languages: Tuple[str, ...] = field(default_factory=tuple)
    cached: bool = False

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider is required")

        if not self.title.strip():
            raise ValueError("title is required")

        if self.info_hash is None and self.url is None:
            raise ValueError("candidate requires info_hash or url")

        if self.file_index is not None and self.info_hash is None:
            raise ValueError("file_index requires info_hash")

        if self.info_hash is not None:
            normalized_hash = self.info_hash.lower().strip()
            object.__setattr__(self, "info_hash", normalized_hash)

        normalized_languages = tuple(
            language.lower().strip()
            for language in self.languages
            if language.strip()
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


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    candidates: Tuple[StreamCandidate, ...]
    failures: Tuple[ProviderFailure, ...] = field(default_factory=tuple)
