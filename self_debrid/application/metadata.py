"""Application-facing metadata contract; no client or provider dependencies."""

from dataclasses import dataclass
from typing import Protocol

from self_debrid.domain import MediaRequest, MediaType


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    imdb_id: str
    title: str
    media_type: MediaType
    year: int | None = None

    def __post_init__(self) -> None:
        # Reuse strict domain identity/title/year validation, even for series.
        MediaRequest(
            self.media_type,
            self.imdb_id,
            0 if self.media_type is MediaType.SERIES else None,
            0 if self.media_type is MediaType.SERIES else None,
            self.title,
            self.year,
        )


@dataclass(frozen=True, slots=True)
class MetadataResult:
    metadata: MediaMetadata | None = None
    error: str | None = None


class MetadataResolver(Protocol):
    async def resolve(self, request: MediaRequest) -> MetadataResult:
        """Return matching metadata or a safe error code; preserve cancellation."""
        ...
