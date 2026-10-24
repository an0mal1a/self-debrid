"""Torrent engine port. The application never imports a particular client."""

from typing import Protocol

from self_debrid.domain.models import StreamCandidate
from self_debrid.domain.torrents import TorrentHandle, TorrentSnapshot, TorrentSource


class TorrentClient(Protocol):
    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def resolve_source(self, candidate: StreamCandidate) -> TorrentSource: ...

    async def submit(
        self, source: TorrentSource, save_directory: str, owner_tag: str
    ) -> TorrentHandle: ...

    async def inspect(self, info_hash: str) -> TorrentSnapshot | None: ...

    async def stop(self, handle: TorrentHandle) -> None: ...

    async def configure(
        self, handle: TorrentHandle, file_index: int, sequential: bool, first_last: bool
    ) -> tuple[str, ...]: ...

    async def start(self, handle: TorrentHandle) -> None: ...
