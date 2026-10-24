"""qBittorrent 4.6/5.x Web API adapter. No deletion API is provided."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import aiohttp
from yarl import URL

from self_debrid.domain.models import StreamCandidate
from self_debrid.domain.torrents import (
    DownloadState,
    TorrentError,
    TorrentFile,
    TorrentHandle,
    TorrentSnapshot,
    TorrentSource,
)
from self_debrid.infrastructure.torrent_config import TorrentEngineSettings, http_origin
from self_debrid.infrastructure.torrent_source import MAX_TORRENT_BYTES, metainfo_hash

MAX_API_BYTES = 4 * 1024 * 1024
HASH = re.compile(r"[a-f0-9]{40}")


class QBittorrentClient:
    def __init__(self, settings: TorrentEngineSettings) -> None:
        self.settings = settings
        self._session: aiohttp.ClientSession | None = None
        self._authenticated = False
        self._modern = False
        self._connect_lock = asyncio.Lock()
        self._submit_lock = asyncio.Lock()

    async def __aenter__(self) -> QBittorrentClient:
        try:
            await self.connect()
            return self
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *args) -> None:
        await self.close()

    async def close(self) -> None:
        self._authenticated = False
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _timeout(self) -> aiohttp.ClientTimeout:
        s = self.settings
        return aiohttp.ClientTimeout(
            total=s.request_timeout, connect=s.connect_timeout, sock_read=s.read_timeout
        )

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        data=None,
        params=None,
        json_response=False,
        optional=False,
    ):
        if self._session is None:
            raise TorrentError("connection_failed")
        try:
            async with self._session.request(
                method,
                self.settings.base_url + "/api/v2/" + endpoint,
                data=data,
                params=params,
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise TorrentError("authentication_failed")
                if optional and response.status == 404:
                    return None
                if response.status != 200:
                    raise TorrentError("client_error")
                content = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    content.extend(chunk)
                    if len(content) > MAX_API_BYTES:
                        raise TorrentError("invalid_response")
                if json_response:
                    return json.loads(content)
                return content.decode("utf-8")
        except (asyncio.TimeoutError, TimeoutError):
            raise TorrentError("client_timeout") from None
        except (aiohttp.ClientError, OSError):
            raise TorrentError("connection_failed") from None
        except (UnicodeError, ValueError, RecursionError):
            raise TorrentError("invalid_response") from None

    async def connect(self) -> None:
        async with self._connect_lock:
            if self._authenticated:
                return
            if self._session is None:
                # qBittorrent is commonly addressed by an IP; accept its SID cookie.
                # This jar is never used to retrieve tracker download locators.
                self._session = aiohttp.ClientSession(
                    timeout=self._timeout(),
                    cookie_jar=aiohttp.CookieJar(unsafe=True),
                    headers={"Referer": self.settings.base_url + "/"},
                    trust_env=False,
                )
            result = await self._request(
                "POST",
                "auth/login",
                data={
                    "username": self.settings.username,
                    "password": self.settings.password,
                },
            )
            if result.strip() != "Ok.":
                raise TorrentError("authentication_failed")
            version = await self._request("GET", "app/version")
            match = re.fullmatch(
                r"v?(\d+)\.(\d+)\.\d+(?:[a-zA-Z0-9.+-]*)", version.strip()
            )
            if not match or not (4, 6) <= (int(match[1]), int(match[2])) < (6, 0):
                raise TorrentError("unsupported_client")
            self._modern = int(match[1]) >= 5
            self._authenticated = True

    async def resolve_source(self, candidate: StreamCandidate) -> TorrentSource:
        if candidate.url and len(candidate.url) > 16384:
            raise TorrentError("source_invalid")
        if candidate.info_hash and not HASH.fullmatch(candidate.info_hash):
            raise TorrentError("unsupported_source")
        if candidate.url and candidate.url.startswith("magnet:"):
            return TorrentSource(candidate.info_hash, magnet=candidate.url)
        if candidate.url:
            try:
                origin = http_origin(candidate.url)
            except ValueError:
                raise TorrentError("source_denied") from None
            if origin not in self.settings.allowed_download_origins:
                raise TorrentError("source_denied")
            try:
                # Separate, cookieless session; preserve signed query bytes and never
                # follow a redirect to a host the operator has not authorized.
                async with aiohttp.ClientSession(
                    timeout=self._timeout(),
                    cookie_jar=aiohttp.DummyCookieJar(),
                    trust_env=False,
                ) as session:
                    async with session.get(
                        URL(candidate.url, encoded=True), allow_redirects=False
                    ) as response:
                        if response.status != 200:
                            raise TorrentError("source_invalid")
                        payload = bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            payload.extend(chunk)
                            if len(payload) > MAX_TORRENT_BYTES:
                                raise TorrentError("source_too_large")
                raw = bytes(payload)
                info_hash = metainfo_hash(raw)
                if candidate.info_hash and candidate.info_hash != info_hash:
                    raise TorrentError("hash_mismatch")
                return TorrentSource(info_hash, torrent_bytes=raw)
            except (asyncio.TimeoutError, TimeoutError):
                raise TorrentError("client_timeout") from None
            except (aiohttp.ClientError, OSError):
                raise TorrentError("connection_failed") from None
        if candidate.info_hash:
            return TorrentSource(
                candidate.info_hash, magnet="magnet:?xt=urn:btih:" + candidate.info_hash
            )
        raise TorrentError("unsupported_source")

    async def _info(self, info_hash: str) -> dict | None:
        if not isinstance(info_hash, str) or not HASH.fullmatch(info_hash):
            raise TorrentError("invalid_metadata")
        rows = await self._request(
            "GET", "torrents/info", params={"hashes": info_hash}, json_response=True
        )
        if not isinstance(rows, list) or len(rows) > 1:
            raise TorrentError("invalid_response")
        if not rows:
            return None
        row = rows[0]
        if (
            not isinstance(row, dict)
            or row.get("hash") != info_hash
            or not isinstance(row.get("save_path"), str)
            or not isinstance(row.get("tags"), str)
        ):
            raise TorrentError("invalid_response")
        return row

    async def _owned_info(self, handle: TorrentHandle) -> dict:
        row = await self._info(handle.info_hash)
        if row is None or handle.owner_tag not in {
            tag.strip() for tag in row["tags"].split(",")
        }:
            raise TorrentError("ownership_lost")
        # Both tag and per-session directory must still agree before any mutation.
        if (
            Path(row["save_path"]) != Path(handle.save_directory)
            or row.get("auto_tmm") is not False
        ):
            raise TorrentError("ownership_lost")
        return row

    async def submit(
        self, source: TorrentSource, save_directory: str, owner_tag: str
    ) -> TorrentHandle:
        if (
            not HASH.fullmatch(source.info_hash)
            or not re.fullmatch(r"self-debrid-[a-f0-9]{32}", owner_tag)
            or not Path(save_directory).is_absolute()
        ):
            raise TorrentError("invalid_metadata")
        if bool(source.magnet) == bool(source.torrent_bytes):
            raise TorrentError("source_invalid")
        async with self._submit_lock:
            if await self._info(source.info_hash) is not None:
                raise TorrentError("torrent_exists")
            fields = {
                "savepath": save_directory,
                "autoTMM": "false",
                "useDownloadPath": "false",
                "tags": owner_tag,
                "contentLayout": "Original",
                "stopCondition": "MetadataReceived",
                "stopped" if self._modern else "paused": "true"
                if source.torrent_bytes
                else "false",
            }
            if source.torrent_bytes:
                if metainfo_hash(source.torrent_bytes) != source.info_hash:
                    raise TorrentError("hash_mismatch")
                data = aiohttp.FormData(fields)
                data.add_field(
                    "torrents",
                    source.torrent_bytes,
                    filename="selection.torrent",
                    content_type="application/x-bittorrent",
                )
            else:
                # Revalidate public port arguments, rather than accepting arbitrary URLs.
                try:
                    check = StreamCandidate(
                        "engine",
                        "selection",
                        info_hash=source.info_hash,
                        url=source.magnet,
                    )
                except ValueError:
                    raise TorrentError("source_invalid") from None
                if not check.url.startswith("magnet:"):
                    raise TorrentError("source_invalid")
                data = dict(fields, urls=source.magnet)
            result = await self._request("POST", "torrents/add", data=data)
            if result.strip() != "Ok.":
                raise TorrentError("client_error")
            return TorrentHandle(source.info_hash, owner_tag, save_directory)

    async def inspect(self, info_hash: str) -> TorrentSnapshot | None:
        row = await self._info(info_hash)
        if row is None:
            return None
        raw_files = await self._request(
            "GET", "torrents/files", params={"hash": info_hash}, json_response=True
        )
        if not isinstance(raw_files, list) or len(raw_files) > 10000:
            raise TorrentError("invalid_metadata")
        try:
            files = tuple(
                TorrentFile(
                    f["index"],
                    f["name"],
                    f["size"],
                    f["progress"],
                    None if f.get("availability") == -1 else f.get("availability"),
                )
                for f in raw_files
            )
            if len({f.index for f in files}) != len(files):
                raise TorrentError("invalid_metadata")
        except (TypeError, KeyError):
            raise TorrentError("invalid_metadata") from None
        state = row.get("state")
        if not isinstance(state, str):
            raise TorrentError("invalid_response")
        if state in {"error", "missingFiles", "unknown"}:
            mapped = DownloadState.ERROR
        elif state in {"metaDL", "forcedMetaDL"}:
            mapped = DownloadState.METADATA
        elif state in {"pausedDL", "pausedUP", "stoppedDL", "stoppedUP"}:
            mapped = DownloadState.STOPPED
        elif state in {"uploading", "stalledUP", "forcedUP", "queuedUP"}:
            mapped = DownloadState.COMPLETE
        elif state in {
            "downloading",
            "stalledDL",
            "forcedDL",
            "queuedDL",
            "checkingDL",
            "checkingUP",
            "checkingResumeData",
            "allocating",
            "moving",
        }:
            mapped = DownloadState.DOWNLOADING
        else:
            mapped = DownloadState.UNKNOWN
        return TorrentSnapshot(
            info_hash,
            files,
            mapped,
            row["save_path"],
            frozenset(t.strip() for t in row["tags"].split(",")),
            row.get("seq_dl") if type(row.get("seq_dl")) is bool else None,
            row.get("f_l_piece_prio")
            if type(row.get("f_l_piece_prio")) is bool
            else None,
        )

    async def _mutate(self, handle: TorrentHandle, endpoint: str, data: dict) -> None:
        await self._owned_info(handle)
        await self._request("POST", "torrents/" + endpoint, data=data)

    async def stop(self, handle: TorrentHandle) -> None:
        await self._mutate(
            handle, "stop" if self._modern else "pause", {"hashes": handle.info_hash}
        )

    async def start(self, handle: TorrentHandle) -> None:
        await self._mutate(
            handle, "start" if self._modern else "resume", {"hashes": handle.info_hash}
        )

    async def configure(
        self, handle: TorrentHandle, file_index: int, sequential: bool, first_last: bool
    ) -> tuple[str, ...]:
        await self._owned_info(handle)
        snapshot = await self.inspect(handle.info_hash)
        if snapshot is None or file_index not in {f.index for f in snapshot.files}:
            raise TorrentError("invalid_file_index")
        others = [str(f.index) for f in snapshot.files if f.index != file_index]
        if others:
            await self._mutate(
                handle,
                "filePrio",
                {"hash": handle.info_hash, "id": "|".join(others), "priority": "0"},
            )
        await self._mutate(
            handle,
            "filePrio",
            {"hash": handle.info_hash, "id": str(file_index), "priority": "7"},
        )
        warnings = []
        for flag, wanted, endpoint, warning in (
            (
                snapshot.sequential,
                sequential,
                "toggleSequentialDownload",
                "sequential_unsupported",
            ),
            (
                snapshot.first_last,
                first_last,
                "toggleFirstLastPiecePrio",
                "first_last_unsupported",
            ),
        ):
            if flag is None:
                warnings.append(warning)
            elif flag != wanted:
                # Toggle APIs are not idempotent. Never retry a timed-out mutation.
                await self._mutate(handle, endpoint, {"hashes": handle.info_hash})
        return tuple(warnings)
