"""Immutable bounded reliability settings and private multi-indexer file loading."""

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

MAX_PROVIDERS = 16


@dataclass(frozen=True, slots=True)
class MetadataSettings:
    enabled: bool = False
    connect_timeout: float = 3.0
    read_timeout: float = 5.0
    ttl: float = 3600.0
    negative_ttl: float = 30.0
    max_entries: int = 256

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("metadata enabled must be boolean")
        if any(
            not math.isfinite(v) or not 0 < v <= 86400
            for v in (
                self.connect_timeout,
                self.read_timeout,
                self.ttl,
                self.negative_ttl,
            )
        ):
            raise ValueError(
                "metadata deadlines and TTLs must be positive and at most 86400"
            )
        if type(self.max_entries) is not int or not 1 <= self.max_entries <= 2048:
            raise ValueError("metadata cache entries must be between 1 and 2048")


@dataclass(frozen=True, slots=True)
class DiscoveryCacheSettings:
    ttl: float = 30.0
    max_entries: int = 128
    max_bytes: int = 8 * 1024 * 1024
    max_inflight: int = 32

    def __post_init__(self) -> None:
        if not math.isfinite(self.ttl) or not 0 <= self.ttl <= 300:
            raise ValueError("discovery cache TTL must be between 0 and 300 seconds")
        for value, upper in (
            (self.max_entries, 1024),
            (self.max_bytes, 64 * 1024 * 1024),
            (self.max_inflight, 64),
        ):
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError("discovery cache limit out of range")


def load_provider_records(
    path: str, environ: Mapping[str, str]
) -> tuple[dict[str, str], ...]:
    """Read at most 64 KiB. Credentials must come from named environment refs."""
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(65537)
        if len(data) > 65536:
            raise ValueError()
        payload = json.loads(data)
        if not isinstance(payload, dict) or set(payload) != {"providers"}:
            raise ValueError()
        records = payload["providers"]
        if not isinstance(records, list) or not 1 <= len(records) <= MAX_PROVIDERS:
            raise ValueError()
        resolved = []
        names = set()
        for record in records:
            if not isinstance(record, dict) or not {"name", "url"} <= set(record) <= {
                "name",
                "url",
                "api_key_env",
            }:
                raise ValueError()
            if not all(isinstance(v, str) for v in record.values()):
                raise ValueError()
            name = record["name"]
            if (
                not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", name)
                or name.casefold() in names
            ):
                raise ValueError()
            names.add(name.casefold())
            reference = record.get("api_key_env")
            if reference is not None and (
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", reference)
                or reference not in environ
            ):
                raise ValueError()
            resolved.append(
                {
                    "name": name,
                    "url": record["url"],
                    "api_key": environ[reference] if reference else "",
                }
            )
        return tuple(resolved)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError(
            "Invalid PROVIDERS_FILE: use 1–16 uniquely named endpoints and defined API-key environment references"
        ) from None
