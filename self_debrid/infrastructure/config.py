"""Explicit environment configuration, read only at the application boundary."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import Mapping, TypeVar
from urllib.parse import urlsplit

from self_debrid.domain.ranking import DEFAULT_RESOLUTION_ORDER, RankingPreferences
from self_debrid.infrastructure.reliability import (
    MAX_PROVIDERS,
    DiscoveryCacheSettings,
    MetadataSettings,
    load_provider_records,
)

Number = TypeVar("Number", int, float)


@dataclass(frozen=True, slots=True)
class ServerSettings:
    host: str = "127.0.0.1"
    port: int = 8080
    public_base_url: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9._:-]{1,253}", self.host):
            raise ValueError("HTTP_HOST must be an IP address or hostname")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("HTTP_PORT must be an integer from 1 to 65535")
        if self.public_base_url is not None:
            try:
                parts = urlsplit(self.public_base_url)
                valid = (
                    parts.scheme in ("http", "https")
                    and parts.hostname
                    and not (
                        parts.username
                        or parts.password
                        or parts.query
                        or parts.fragment
                    )
                    and (parts.port is None or 1 <= parts.port <= 65535)
                    and not any(
                        character.isspace() for character in self.public_base_url
                    )
                )
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(
                    "PUBLIC_ADDON_URL must be an HTTP(S) base URL without credentials or query"
                )
            object.__setattr__(
                self, "public_base_url", self.public_base_url.rstrip("/")
            )

    @property
    def manifest_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return (self.public_base_url or f"http://{host}:{self.port}") + "/manifest.json"


@dataclass(frozen=True, slots=True)
class TorznabSettings:
    base_url: str
    api_key: str = field(default="", repr=False)
    name: str = "prowlarr"
    connect_timeout: float = 5.0
    read_timeout: float = 10.0

    def __post_init__(self) -> None:
        parts = urlsplit(self.base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("TORZNAB_URL must be a complete HTTP(S) Torznab endpoint")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError(
                "TORZNAB_URL must not contain credentials, query or fragment"
            )
        if not self.name.strip():
            raise ValueError("TORZNAB_NAME must not be blank")
        for value in (self.connect_timeout, self.read_timeout):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("connection/read timeouts must be finite and positive")


@dataclass(frozen=True, slots=True)
class AppConfig:
    torznab: TorznabSettings | None = None
    ranking: RankingPreferences = RankingPreferences()
    max_concurrency: int = 4
    provider_timeout: float = 20.0
    server: ServerSettings = ServerSettings()
    providers: tuple[TorznabSettings, ...] = ()
    metadata: MetadataSettings = MetadataSettings()
    discovery_cache: DiscoveryCacheSettings = DiscoveryCacheSettings()

    @property
    def configured_providers(self) -> tuple[TorznabSettings, ...]:
        return self.providers or ((self.torznab,) if self.torznab else ())

    def __post_init__(self) -> None:
        configured = self.configured_providers
        if not 1 <= len(configured) <= MAX_PROVIDERS:
            raise ValueError("configure between 1 and 16 providers")
        if len({provider.name.casefold() for provider in configured}) != len(
            configured
        ):
            raise ValueError("provider names must be unique")
        if type(self.providers) is not tuple:
            raise ValueError("providers must be an immutable tuple")
        if (
            type(self.max_concurrency) is not int
            or not 1 <= self.max_concurrency <= MAX_PROVIDERS
        ):
            raise ValueError("MAX_CONCURRENCY must be positive")
        if not math.isfinite(self.provider_timeout) or self.provider_timeout <= 0:
            raise ValueError("PROVIDER_TIMEOUT must be finite and positive")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AppConfig:
        env = os.environ if environ is None else environ

        # Conversion errors deliberately omit raw values, which may contain secrets.
        def number(name: str, default: str, converter: type[Number]) -> Number:
            try:
                return converter(env.get(name, default))
            except (ValueError, TypeError):
                raise ValueError(f"{name} must be numeric") from None

        def labels(name: str, default: str) -> tuple[str, ...]:
            return tuple(
                value.strip()
                for value in env.get(name, default).split(",")
                if value.strip()
            )

        def boolean(name: str) -> bool:
            value = env.get(name, "false").lower().strip()
            if value not in ("true", "false"):
                raise ValueError(f"{name} must be true or false")
            return value == "true"

        maximum = env.get("MAX_SIZE_BYTES", "").strip()
        providers = ()
        if env.get("PROVIDERS_FILE"):
            providers = tuple(
                TorznabSettings(
                    base_url=record["url"],
                    api_key=record["api_key"],
                    name=record["name"],
                    connect_timeout=number("CONNECT_TIMEOUT", "5", float),
                    read_timeout=number("READ_TIMEOUT", "10", float),
                )
                for record in load_provider_records(env["PROVIDERS_FILE"], env)
            )
        return cls(
            providers=providers,
            torznab=None
            if providers
            else TorznabSettings(
                base_url=env.get("TORZNAB_URL", ""),
                api_key=env.get("TORZNAB_API_KEY", ""),
                name=env.get("TORZNAB_NAME", "prowlarr"),
                connect_timeout=number("CONNECT_TIMEOUT", "5", float),
                read_timeout=number("READ_TIMEOUT", "10", float),
            ),
            metadata=MetadataSettings(
                enabled=boolean("CINEMETA_ENABLED"),
                connect_timeout=number("METADATA_CONNECT_TIMEOUT", "3", float),
                read_timeout=number("METADATA_READ_TIMEOUT", "5", float),
                ttl=number("METADATA_TTL", "3600", float),
                negative_ttl=number("METADATA_NEGATIVE_TTL", "30", float),
                max_entries=number("METADATA_CACHE_ENTRIES", "256", int),
            ),
            discovery_cache=DiscoveryCacheSettings(
                ttl=number("DISCOVERY_CACHE_TTL", "30", float),
                max_entries=number("DISCOVERY_CACHE_ENTRIES", "128", int),
                max_bytes=number("DISCOVERY_CACHE_BYTES", "8388608", int),
                max_inflight=number("DISCOVERY_MAX_INFLIGHT", "32", int),
            ),
            ranking=RankingPreferences(
                resolution_order=labels(
                    "RESOLUTION_ORDER", ",".join(DEFAULT_RESOLUTION_ORDER)
                ),
                preferred_languages=labels("PREFERRED_LANGUAGES", ""),
                max_size_bytes=number("MAX_SIZE_BYTES", "0", int) if maximum else None,
                min_seeders=number("MIN_SEEDERS", "0", int),
            ),
            max_concurrency=number("MAX_CONCURRENCY", "4", int),
            provider_timeout=number("PROVIDER_TIMEOUT", "20", float),
            server=ServerSettings(
                host=env.get("HTTP_HOST", "127.0.0.1"),
                port=number("HTTP_PORT", "8080", int),
                public_base_url=env.get("PUBLIC_ADDON_URL") or None,
            ),
        )
