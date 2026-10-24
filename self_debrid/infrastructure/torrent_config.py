"""Opt-in backend settings, independent of discovery and the HTTP addon."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


def http_origin(url: str, *, origin_only: bool = False) -> str:
    try:
        parts = urlsplit(url)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.fragment
            or any(c.isspace() or ord(c) < 32 for c in url)
            or (origin_only and (parts.path not in ("", "/") or parts.query))
        ):
            raise ValueError
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError
        port = parts.port or (443 if parts.scheme == "https" else 80)
        host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        return f"{parts.scheme}://{host}:{port}"
    except (ValueError, TypeError):
        raise ValueError("invalid HTTP endpoint") from None


@dataclass(frozen=True, slots=True)
class TorrentEngineSettings:
    base_url: str = field(default="http://127.0.0.1:8080", repr=False)
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    download_directory: Path = field(
        default_factory=lambda: Path.home() / ".cache/self-debrid/downloads", repr=False
    )
    allowed_download_origins: tuple[str, ...] = field(default=(), repr=False)
    connect_timeout: float = 5
    read_timeout: float = 10
    request_timeout: float = 20
    metadata_timeout: float = 60
    preparation_timeout: float = 120
    poll_interval: float = 0.5
    max_sessions: int = 4
    max_records: int = 32
    session_ttl: float = 3600
    sequential: bool = True
    first_last: bool = True

    def __post_init__(self) -> None:
        http_origin(self.base_url)
        parts = urlsplit(self.base_url)
        if parts.query:
            raise ValueError("qBittorrent endpoint must not contain a query")
        if (
            not isinstance(self.download_directory, Path)
            or not self.download_directory.is_absolute()
        ):
            raise ValueError("download directory must be an absolute local path")
        if (
            type(self.allowed_download_origins) is not tuple
            or len(self.allowed_download_origins) > 16
        ):
            raise ValueError("at most 16 immutable download origins are supported")
        origins = tuple(
            dict.fromkeys(
                http_origin(o, origin_only=True) for o in self.allowed_download_origins
            )
        )
        object.__setattr__(self, "allowed_download_origins", origins)
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        for value in (
            self.connect_timeout,
            self.read_timeout,
            self.request_timeout,
            self.metadata_timeout,
            self.preparation_timeout,
            self.poll_interval,
            self.session_ttl,
        ):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 < value <= 86400
            ):
                raise ValueError("engine timeouts must be positive and bounded")
        if type(self.max_sessions) is not int or not 1 <= self.max_sessions <= 32:
            raise ValueError("engine supports 1 to 32 active sessions")
        if (
            type(self.max_records) is not int
            or not self.max_sessions <= self.max_records <= 256
        ):
            raise ValueError(
                "session record limit must cover active sessions and be at most 256"
            )
        if type(self.sequential) is not bool or type(self.first_last) is not bool:
            raise ValueError("download preferences must be booleans")

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> TorrentEngineSettings:
        env = os.environ if environ is None else environ
        values = {}
        for key, default in (
            ("CONNECT_TIMEOUT", "5"),
            ("READ_TIMEOUT", "10"),
            ("REQUEST_TIMEOUT", "20"),
            ("METADATA_TIMEOUT", "60"),
            ("PREPARATION_TIMEOUT", "120"),
            ("POLL_INTERVAL", "0.5"),
            ("SESSION_TTL", "3600"),
            ("MAX_SESSIONS", "4"),
            ("MAX_RECORDS", "32"),
        ):
            try:
                values[key.lower()] = (int if key.startswith("MAX_") else float)(
                    env.get("TORRENT_" + key, default)
                )
            except (TypeError, ValueError):
                raise ValueError("invalid numeric torrent engine setting") from None
        for key in ("SEQUENTIAL", "FIRST_LAST"):
            value = env.get("TORRENT_" + key, "true").strip().lower()
            if value not in ("true", "false"):
                raise ValueError("torrent preferences must be true or false")
            values[key.lower()] = value == "true"
        return cls(
            base_url=env.get("QBITTORRENT_URL", "http://127.0.0.1:8080"),
            username=env.get("QBITTORRENT_USERNAME", ""),
            password=env.get("QBITTORRENT_PASSWORD", ""),
            download_directory=Path(
                env.get(
                    "TORRENT_DOWNLOAD_DIRECTORY",
                    str(Path.home() / ".cache/self-debrid/downloads"),
                )
            ),
            allowed_download_origins=tuple(
                x.strip()
                for x in env.get("TORRENT_DOWNLOAD_ORIGINS", "").split(",")
                if x.strip()
            ),
            **values,
        )
