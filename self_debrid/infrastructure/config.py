"""Explicit environment configuration, read only at the application boundary."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Mapping, TypeVar
from urllib.parse import urlsplit

from self_debrid.domain.ranking import DEFAULT_RESOLUTION_ORDER, RankingPreferences

Number = TypeVar("Number", int, float)


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
    torznab: TorznabSettings
    ranking: RankingPreferences = RankingPreferences()
    max_concurrency: int = 4
    provider_timeout: float = 20.0

    def __post_init__(self) -> None:
        if type(self.max_concurrency) is not int or self.max_concurrency < 1:
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

        maximum = env.get("MAX_SIZE_BYTES", "").strip()
        return cls(
            torznab=TorznabSettings(
                base_url=env.get("TORZNAB_URL", ""),
                api_key=env.get("TORZNAB_API_KEY", ""),
                name=env.get("TORZNAB_NAME", "prowlarr"),
                connect_timeout=number("CONNECT_TIMEOUT", "5", float),
                read_timeout=number("READ_TIMEOUT", "10", float),
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
        )
