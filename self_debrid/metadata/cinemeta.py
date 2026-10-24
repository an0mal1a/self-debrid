"""Optional Cinemeta metadata lookup, not catalog integration."""

import asyncio
import json
import re
from datetime import datetime

import aiohttp

from self_debrid.application.cache import AsyncTTLCache, CacheBusy
from self_debrid.application.metadata import MediaMetadata, MetadataResult
from self_debrid.domain import MediaRequest

CINEMETA_URL = "https://v3-cinemeta.strem.io"
MAX_RESPONSE_BYTES = 1024 * 1024


def parse_metadata(data: bytes, request: MediaRequest) -> MetadataResult:
    if len(data) > MAX_RESPONSE_BYTES:
        return MetadataResult(error="response_too_large")
    try:
        payload = json.loads(data)
        meta = payload.get("meta") if isinstance(payload, dict) else None
        if not isinstance(meta, dict):
            return MetadataResult(error="invalid_response")
        if (
            meta.get("id") != request.imdb_id
            or meta.get("type") != request.media_type.value
        ):
            return MetadataResult(error="identity_mismatch")
        if meta.get("imdb_id", request.imdb_id) != request.imdb_id:
            return MetadataResult(error="identity_mismatch")
        title = meta.get("name")
        if (
            not isinstance(title, str)
            or not title.strip()
            or len(title) > 300
            or any(ord(c) < 32 for c in title)
        ):
            return MetadataResult(error="invalid_response")
        year = None
        release = meta.get("releaseInfo")
        if isinstance(release, str):
            match = re.fullmatch(
                r"((?:18|19|20)[0-9]{2})(?:\s*[-–]\s*(?:[0-9]{4})?)?", release.strip()
            )
            if match:
                year = int(match[1])
        if year is None and isinstance(meta.get("released"), str):
            try:
                year = datetime.fromisoformat(
                    meta["released"].replace("Z", "+00:00")
                ).year
            except ValueError:
                pass
        return MetadataResult(
            MediaMetadata(request.imdb_id, title.strip(), request.media_type, year)
        )
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return MetadataResult(error="invalid_response")


class CinemetaResolver:
    def __init__(
        self,
        *,
        connect_timeout: float = 3,
        read_timeout: float = 5,
        ttl: float = 3600,
        negative_ttl: float = 30,
        max_entries: int = 256,
    ) -> None:
        import math

        if any(
            not math.isfinite(v) or v <= 0
            for v in (connect_timeout, read_timeout, ttl, negative_ttl)
        ):
            raise ValueError("metadata timeouts and TTLs must be finite and positive")
        self._timeout = aiohttp.ClientTimeout(
            total=connect_timeout + read_timeout,
            connect=connect_timeout,
            sock_connect=connect_timeout,
            sock_read=read_timeout,
        )
        self._ttl = ttl
        self._negative_ttl = negative_ttl
        self._cache: AsyncTTLCache[MetadataResult] = AsyncTTLCache(
            max_entries=max_entries,
            max_bytes=512 * 1024,
            max_inflight=8,
        )

    async def resolve(self, request: MediaRequest) -> MetadataResult:
        # Request validation occurs before URL construction. Use only the title
        # ID, never episode coordinates, free-form title, or user URL input.
        try:
            MediaRequest(
                request.media_type, request.imdb_id, request.season, request.episode
            )
            if not re.fullmatch(r"tt[0-9]{7,10}", request.imdb_id):
                return MetadataResult(error="invalid_id")
        except (ValueError, TypeError, AttributeError):
            return MetadataResult(error="invalid_id")
        try:
            return await self._cache.get(
                (request.media_type.value, request.imdb_id),
                lambda: self._lookup(request),
                ttl=lambda result: self._ttl if result.metadata else self._negative_ttl,
                weight=lambda result: 256 + len(repr(result).encode()),
            )
        except CacheBusy:
            return MetadataResult(error="busy")

    async def _lookup(self, request: MediaRequest) -> MetadataResult:
        try:
            data = await self._fetch(
                f"{CINEMETA_URL}/meta/{request.media_type.value}/{request.imdb_id}.json"
            )
            return parse_metadata(data, request)
        except (asyncio.TimeoutError, TimeoutError):
            return MetadataResult(error="timeout")
        except aiohttp.ClientError:
            return MetadataResult(error="connection_error")
        except _LookupError as exc:
            return MetadataResult(error=exc.code)
        except Exception:
            return MetadataResult(error="metadata_error")

    async def _fetch(self, url: str) -> bytes:
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    raise _LookupError(
                        "not_found" if response.status == 404 else "http_error"
                    )
                chunks = []
                size = 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise _LookupError("response_too_large")
                    chunks.append(chunk)
                return b"".join(chunks)


class _LookupError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
