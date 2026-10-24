"""Immutable torrent metadata; no client or filesystem dependencies."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class TorrentError(Exception):
    """Only a local, allowlisted code crosses the client boundary."""

    CODES = frozenset(
        {
            "authentication_failed",
            "connection_failed",
            "client_timeout",
            "invalid_response",
            "unsupported_client",
            "client_error",
            "unsupported_source",
            "source_denied",
            "source_invalid",
            "source_too_large",
            "hash_mismatch",
            "torrent_exists",
            "ownership_lost",
            "metadata_timeout",
            "unsafe_path",
            "invalid_metadata",
            "invalid_file_index",
            "file_index_mismatch",
            "no_matching_file",
            "ambiguous_files",
            "session_limit",
            "session_not_found",
            "session_closed",
            "preparation_timeout",
        }
    )

    def __init__(self, code: str):
        self.code = code if code in self.CODES else "client_error"
        super().__init__(self.code)


def relative_parts(value: str) -> tuple[str, ...]:
    """Portable torrent paths: reject traversal, absolute paths and Windows ADS."""
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise TorrentError("unsafe_path")
    parts = tuple(value.replace("\\", "/").split("/"))
    reserved = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.I)
    if len(parts) > 64 or any(
        not part
        or part in (".", "..")
        or part.endswith((" ", "."))
        or any(ord(c) < 32 or c in ':<>"|?*' or ord(c) == 127 for c in part)
        or reserved.match(part)
        for part in parts
    ):
        raise TorrentError("unsafe_path")
    return parts


@dataclass(frozen=True, slots=True)
class TorrentFile:
    index: int
    path: str = field(repr=False)
    size_bytes: int
    progress: float | None = None
    availability: float | None = None  # Peer piece fraction, not local byte ranges.

    def __post_init__(self) -> None:
        relative_parts(self.path)
        if type(self.index) is not int or not 0 <= self.index <= 100000:
            raise TorrentError("invalid_metadata")
        if type(self.size_bytes) is not int or not 0 <= self.size_bytes <= 2**63 - 1:
            raise TorrentError("invalid_metadata")
        for number in (self.progress, self.availability):
            if number is not None and (
                type(number) not in (int, float) or not 0 <= number <= 1
            ):
                raise TorrentError("invalid_metadata")


class DownloadState(str, Enum):
    METADATA = "metadata"
    STOPPED = "stopped"
    DOWNLOADING = "downloading"
    COMPLETE = "complete"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TorrentSnapshot:
    info_hash: str
    files: tuple[TorrentFile, ...]
    state: DownloadState
    save_directory: str = field(repr=False)
    tags: frozenset[str] = field(default_factory=frozenset, repr=False)
    sequential: bool | None = None
    first_last: bool | None = None

    def __post_init__(self) -> None:
        if (
            type(self.files) is not tuple
            or len(self.files) > 10000
            or any(not isinstance(file, TorrentFile) for file in self.files)
            or len({file.index for file in self.files}) != len(self.files)
            or not isinstance(self.state, DownloadState)
            or type(self.tags) is not frozenset
            or any(not isinstance(tag, str) for tag in self.tags)
            or not isinstance(self.save_directory, str)
            or any(
                flag is not None and type(flag) is not bool
                for flag in (self.sequential, self.first_last)
            )
        ):
            raise TorrentError("invalid_metadata")


@dataclass(frozen=True, slots=True)
class TorrentSource:
    info_hash: str
    magnet: str | None = field(default=None, repr=False)
    torrent_bytes: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class TorrentHandle:
    info_hash: str
    owner_tag: str = field(repr=False)
    save_directory: str = field(repr=False)
