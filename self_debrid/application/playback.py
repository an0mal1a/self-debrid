"""Bounded preparation sessions. This module serves no media or HTTP routes."""

from __future__ import annotations

import asyncio
import math
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from time import monotonic
from typing import Protocol

from self_debrid.domain.file_selection import select_video_file
from self_debrid.domain.models import MediaRequest, MediaType, StreamCandidate
from self_debrid.domain.torrents import (
    DownloadState,
    TorrentError,
    TorrentFile,
    TorrentHandle,
    TorrentSnapshot,
)

from .torrent_client import TorrentClient


class DownloadWorkspace(Protocol):
    def create(self, session_id: str) -> str: ...

    def resolve(self, directory: str, relative: str) -> Path: ...


class SessionState(str, Enum):
    PREPARING = "preparing"
    WAITING_METADATA = "waiting_metadata"
    DOWNLOADING = "downloading"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class PlaybackSession:
    id: str
    media_key: str
    state: SessionState = SessionState.PREPARING
    info_hash: str | None = None
    provider_file_index: int | None = None
    verified_file_index: int | None = None
    local_path: Path | None = field(default=None, repr=False)
    size_bytes: int | None = None
    download_state: DownloadState = DownloadState.UNKNOWN
    progress: float | None = None
    peer_availability: float | None = None
    available_bytes: int | None = None
    error: str | None = None
    warnings: tuple[str, ...] = ()

    def public_view(self) -> dict:
        """An explicit allowlist, ready for a future adapter; never return asdict()."""
        return {
            "id": self.id,
            "media": self.media_key,
            "state": self.state.value,
            "infoHash": self.info_hash,
            "providerFileIndex": self.provider_file_index,
            "verifiedFileIndex": self.verified_file_index,
            "sizeBytes": self.size_bytes,
            "downloadState": self.download_state.value,
            "progress": self.progress,
            "peerAvailability": self.peer_availability,
            "availableBytes": self.available_bytes,
            "error": self.error,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class _Record:
    session: PlaybackSession
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    handle: TorrentHandle | None = None
    selected: TorrentFile | None = None
    active: bool = True
    finished_at: float | None = None


class PlaybackService:
    def __init__(
        self,
        client: TorrentClient,
        workspace: DownloadWorkspace,
        *,
        max_sessions: int = 4,
        max_records: int = 32,
        session_ttl: float = 3600,
        metadata_timeout: float = 60,
        preparation_timeout: float = 120,
        operation_timeout: float = 20,
        poll_interval: float = 0.5,
        sequential: bool = True,
        first_last: bool = True,
    ) -> None:
        if (
            type(max_sessions) is not int
            or type(max_records) is not int
            or not 1 <= max_sessions <= 32
            or not max_sessions <= max_records <= 256
        ):
            raise ValueError("invalid session bounds")
        if any(
            type(n) not in (int, float) or not math.isfinite(n) or not 0 < n <= 86400
            for n in (
                session_ttl,
                metadata_timeout,
                preparation_timeout,
                operation_timeout,
                poll_interval,
            )
        ):
            raise ValueError("invalid session time bounds")
        self.client, self.workspace = client, workspace
        self.max_sessions, self.max_records = max_sessions, max_records
        self.session_ttl, self.metadata_timeout = session_ttl, metadata_timeout
        self.preparation_timeout, self.operation_timeout = (
            preparation_timeout,
            operation_timeout,
        )
        self.poll_interval = poll_interval
        self.sequential, self.first_last = sequential, first_last
        self._records: dict[str, _Record] = {}
        self._closing = False

    def _prune(self) -> None:
        now = monotonic()
        for key, record in tuple(self._records.items()):
            if (
                not record.active
                and not record.lock.locked()
                and record.finished_at is not None
                and now - record.finished_at >= self.session_ttl
            ):
                del self._records[key]
        while len(self._records) >= self.max_records:
            key = next(
                (
                    key
                    for key, record in self._records.items()
                    if not record.active and not record.lock.locked()
                ),
                None,
            )
            if key is None:
                break
            del self._records[key]

    def _reserve(self, request: MediaRequest, candidate: StreamCandidate) -> _Record:
        if self._closing:
            raise TorrentError("session_closed")
        self._prune()
        if (
            sum(record.active for record in self._records.values()) >= self.max_sessions
            or len(self._records) >= self.max_records
        ):
            raise TorrentError("session_limit")
        if candidate.info_hash and any(
            record.active and record.session.info_hash == candidate.info_hash
            for record in self._records.values()
        ):
            raise TorrentError("torrent_exists")
        record = _Record(
            PlaybackSession(
                uuid.uuid4().hex,
                request.key,
                info_hash=candidate.info_hash,
                provider_file_index=candidate.file_index,
            )
        )
        self._records[record.session.id] = record
        return record

    def _record(self, session_id: str) -> _Record:
        record = self._records.get(session_id)
        if record is None:
            raise TorrentError("session_not_found")
        return record

    def get(self, session_id: str) -> PlaybackSession:
        return self._record(session_id).session

    def _terminal(
        self,
        record: _Record,
        state: SessionState,
        error: str | None = None,
        *,
        retain_slot: bool = False,
    ) -> None:
        record.session = replace(
            record.session, state=state, error=error, available_bytes=None
        )
        record.active = retain_slot
        record.finished_at = None if retain_slot else monotonic()

    async def _stop(self, record: _Record) -> str | None:
        if record.handle is None:
            return None
        try:
            await asyncio.wait_for(
                self.client.stop(record.handle), timeout=min(5, self.operation_timeout)
            )
        except TorrentError as error:
            return error.code
        except asyncio.TimeoutError:
            return "client_timeout"
        return None

    async def prepare(
        self, request: MediaRequest, candidate: StreamCandidate
    ) -> PlaybackSession:
        record = self._reserve(request, candidate)
        async with record.lock:
            try:
                await asyncio.wait_for(
                    self._prepare(record, request, candidate), self.preparation_timeout
                )
            except asyncio.CancelledError:
                cleanup_error = await self._stop(record)
                self._terminal(
                    record,
                    SessionState.CANCELLED,
                    cleanup_error,
                    retain_slot=cleanup_error is not None,
                )
                raise
            except (TorrentError, asyncio.TimeoutError) as error:
                code = (
                    error.code
                    if isinstance(error, TorrentError)
                    else "preparation_timeout"
                )
                cleanup_error = await self._stop(record)
                self._terminal(
                    record,
                    SessionState.FAILED,
                    code,
                    retain_slot=cleanup_error is not None,
                )
                if cleanup_error:
                    record.session = replace(
                        record.session,
                        warnings=record.session.warnings
                        + ("cleanup_" + cleanup_error,),
                    )
            except BaseException:
                # Preserve programming errors and cancellation while still retaining a
                # bounded, sanitized failed record and attempting an owned stop.
                cleanup_error = await self._stop(record)
                self._terminal(
                    record,
                    SessionState.FAILED,
                    "client_error",
                    retain_slot=cleanup_error is not None,
                )
                raise
        return record.session

    async def _prepare(
        self, record: _Record, request: MediaRequest, candidate: StreamCandidate
    ) -> None:
        # Explicit candidate contradictions must fail before any side effect.
        if candidate.imdb_id and candidate.imdb_id != request.imdb_id:
            raise TorrentError("no_matching_file")
        if request.media_type is MediaType.MOVIE and (
            candidate.season is not None or candidate.episode is not None
        ):
            raise TorrentError("no_matching_file")
        if request.media_type is MediaType.SERIES and (
            (candidate.season is not None and candidate.season != request.season)
            or (candidate.episode is not None and candidate.episode != request.episode)
        ):
            raise TorrentError("no_matching_file")
        if (
            request.media_type is MediaType.MOVIE
            and request.year
            and candidate.year
            and abs(request.year - candidate.year) > 1
        ):
            raise TorrentError("no_matching_file")
        await self.client.connect()
        source = await self.client.resolve_source(candidate)
        # Hashless locators gain their identity only after metainfo resolution.
        # Reserve it before yielding to submission: one session per torrent avoids
        # conflicting priorities even while qBittorrent's add is not yet visible.
        if any(
            other is not record
            and other.active
            and other.session.info_hash == source.info_hash
            for other in self._records.values()
        ):
            raise TorrentError("torrent_exists")
        record.session = replace(record.session, info_hash=source.info_hash)
        directory = self.workspace.create(record.session.id)
        # Retain a provisional identity if add succeeds remotely but the response
        # is lost. Cleanup still needs both the unique owner tag and directory.
        record.handle = TorrentHandle(
            source.info_hash, "self-debrid-" + record.session.id, directory
        )
        try:
            record.handle = await self.client.submit(
                source, directory, record.handle.owner_tag
            )
        except TorrentError as error:
            if error.code == "torrent_exists":
                record.handle = None  # Never attempt to stop an existing user's work.
            raise
        record.session = replace(
            record.session,
            info_hash=source.info_hash,
            state=SessionState.WAITING_METADATA,
        )

        async def metadata() -> TorrentSnapshot:
            while True:
                snapshot = await self.client.inspect(source.info_hash)
                if snapshot is not None:
                    if snapshot.state is DownloadState.ERROR:
                        raise TorrentError("client_error")
                    if snapshot.files:
                        return snapshot
                await asyncio.sleep(self.poll_interval)

        try:
            snapshot = await asyncio.wait_for(metadata(), self.metadata_timeout)
        except asyncio.TimeoutError:
            raise TorrentError("metadata_timeout") from None
        await self.client.stop(record.handle)
        selection = select_video_file(request, snapshot.files, candidate.file_index)
        if selection.error:
            raise TorrentError(selection.error)
        selected = selection.file
        location = self.workspace.resolve(record.handle.save_directory, selected.path)
        # Client must still own this torrent and directory when priorities change.
        warnings = await self.client.configure(
            record.handle, selected.index, self.sequential, self.first_last
        )
        record.selected = selected
        record.session = replace(
            record.session,
            verified_file_index=selected.index,
            local_path=location,
            size_bytes=selected.size_bytes,
            progress=selected.progress,
            peer_availability=selected.availability,
            warnings=warnings,
        )
        await self.client.start(record.handle)
        record.session = replace(
            record.session,
            state=SessionState.DOWNLOADING,
            # Start was accepted; refresh obtains the actual subsequent client state.
            download_state=DownloadState.UNKNOWN,
        )

    async def refresh(self, session_id: str) -> PlaybackSession:
        record = self._record(session_id)
        async with record.lock:
            if (
                not record.active
                or record.session.state
                not in (SessionState.DOWNLOADING, SessionState.COMPLETE)
                or not record.handle
                or not record.selected
            ):
                return record.session
            try:
                snapshot = await asyncio.wait_for(
                    self.client.inspect(record.handle.info_hash), self.operation_timeout
                )
                if (
                    snapshot is None
                    or snapshot.save_directory != record.handle.save_directory
                    or record.handle.owner_tag not in snapshot.tags
                ):
                    raise TorrentError("ownership_lost")
                selected = next(
                    (f for f in snapshot.files if f.index == record.selected.index),
                    None,
                )
                if (
                    selected is None
                    or selected.path != record.selected.path
                    or selected.size_bytes != record.selected.size_bytes
                ):
                    raise TorrentError("invalid_metadata")
                if snapshot.state is DownloadState.ERROR:
                    raise TorrentError("client_error")
                location = self.workspace.resolve(
                    record.handle.save_directory, selected.path
                )
                complete = (
                    selected.progress == 1
                    and location.is_file()
                    and location.stat().st_size == selected.size_bytes
                )
                record.session = replace(
                    record.session,
                    state=SessionState.COMPLETE
                    if complete
                    else SessionState.DOWNLOADING,
                    download_state=snapshot.state,
                    local_path=location,
                    progress=selected.progress,
                    peer_availability=selected.availability,
                    available_bytes=selected.size_bytes if complete else None,
                )
            except (TorrentError, asyncio.TimeoutError, OSError) as error:
                code = (
                    error.code
                    if isinstance(error, TorrentError)
                    else "client_timeout"
                    if isinstance(error, asyncio.TimeoutError)
                    else "unsafe_path"
                )
                cleanup_error = await self._stop(record)
                self._terminal(
                    record,
                    SessionState.FAILED,
                    code,
                    retain_slot=cleanup_error is not None,
                )
            return record.session

    async def close_session(self, session_id: str) -> PlaybackSession:
        record = self._record(session_id)
        async with record.lock:
            if record.session.state is SessionState.CLOSED and not record.active:
                return record.session
            error = await self._stop(record)
            self._terminal(
                record, SessionState.CLOSED, error, retain_slot=error is not None
            )
            return record.session

    async def shutdown(self) -> None:
        self._closing = True
        await asyncio.gather(*(self.close_session(key) for key in tuple(self._records)))
