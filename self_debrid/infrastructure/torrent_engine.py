"""Explicit composition for the backend-only torrent preparation API."""

import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from self_debrid.adapters.qbittorrent import QBittorrentClient
from self_debrid.application.playback import PlaybackService
from self_debrid.domain.torrents import TorrentError
from self_debrid.infrastructure.torrent_config import TorrentEngineSettings
from self_debrid.infrastructure.torrent_paths import safe_local_path


class LocalDownloadWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def create(self, session_id: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{32}", session_id):
            raise TorrentError("unsafe_path")
        try:
            if self.root.is_symlink():
                raise TorrentError("unsafe_path")
            self.root.mkdir(parents=True, exist_ok=True)
            directory = self.root.resolve() / session_id
            directory.mkdir(mode=0o700, exist_ok=False)
            return str(directory)
        except (OSError, ValueError, RuntimeError):
            raise TorrentError("unsafe_path") from None

    def resolve(self, directory: str, relative: str) -> Path:
        return safe_local_path(self.root, directory, relative)


@asynccontextmanager
async def open_torrent_engine(
    settings: TorrentEngineSettings,
) -> AsyncIterator[PlaybackService]:
    """No network or disk I/O occurs until explicitly entered/preparing a session."""
    client = QBittorrentClient(settings)
    service = PlaybackService(
        client,
        LocalDownloadWorkspace(settings.download_directory),
        max_sessions=settings.max_sessions,
        max_records=settings.max_records,
        session_ttl=settings.session_ttl,
        metadata_timeout=settings.metadata_timeout,
        preparation_timeout=settings.preparation_timeout,
        operation_timeout=settings.request_timeout,
        poll_interval=settings.poll_interval,
        sequential=settings.sequential,
        first_last=settings.first_last,
    )
    try:
        yield service
    finally:
        try:
            await service.shutdown()
        finally:
            await client.close()
