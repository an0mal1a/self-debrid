"""Canonical torrent identities (BEP 9, BEP 52 and single-file BEP 53 selection)."""

from __future__ import annotations

import base64
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit


def normalize_info_hash(value: str) -> str:
    """Return a lowercase v1 hex hash or a v2 SHA-256 multihash (1220...)."""
    value = value.strip()
    lowered = value.lower()
    if lowered.startswith("magnet:"):
        result = magnet_info_hash(value)
        if result is None:
            raise ValueError("magnet has no supported torrent hash")
        return result
    if lowered.startswith("urn:btih:"):
        return _btih(value[9:])
    if lowered.startswith("urn:btmh:"):
        lowered = lowered[9:]
        if re.fullmatch(r"1220[0-9a-f]{64}", lowered):
            return lowered
        raise ValueError("unsupported torrent multihash")
    if re.fullmatch(r"[0-9a-f]{64}", lowered):
        return "1220" + lowered
    if re.fullmatch(r"1220[0-9a-f]{64}", lowered):
        return lowered
    return _btih(value)


def _btih(value: str) -> str:
    if re.fullmatch(r"[0-9a-fA-F]{40}", value):
        return value.lower()
    if re.fullmatch(r"[a-zA-Z2-7]{32}", value):
        return base64.b32decode(value.upper()).hex()
    raise ValueError("invalid torrent info hash")


def magnet_info_hash(url: str) -> str | None:
    hashes = magnet_info_hashes(url)
    return hashes[0] if hashes else None


def magnet_info_hashes(url: str) -> tuple[str, ...]:
    """Return compatible exact topics, preferring v1 for hybrid magnets."""
    parts = urlsplit(url)
    if parts.scheme.lower() != "magnet":
        return ()
    topics = parse_qs(parts.query).get("xt", [])
    hashes = []
    for prefix in ("urn:btih:", "urn:btmh:"):
        mode_hashes = set()
        for topic in topics:
            if topic.lower().startswith(prefix):
                try:
                    mode_hashes.add(normalize_info_hash(topic))
                except ValueError:
                    continue
        if len(mode_hashes) > 1:
            raise ValueError("magnet contains contradictory torrent hashes")
        hashes.extend(sorted(mode_hashes))
    return tuple(hashes)


def magnet_file_index(url: str) -> int | None:
    parts = urlsplit(url)
    if parts.scheme.lower() != "magnet":
        return None
    selections = parse_qs(parts.query).get("so", [])
    if len(selections) == 1 and re.fullmatch(r"[0-9]+", selections[0]):
        return int(selections[0])
    return None


def normalize_url(url: str) -> str:
    """Normalize scheme/authority only; retain signed query order and encoding."""
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https", "magnet"):
        raise ValueError("candidate URL must be HTTP(S) or magnet")
    if parts.scheme.lower() != "magnet" and not parts.hostname:
        raise ValueError("candidate URL requires a hostname")
    authority = parts.netloc
    if "@" in authority:
        userinfo, host = authority.rsplit("@", 1)
        authority = userinfo + "@" + host.lower()
    else:
        authority = authority.lower()
    return urlunsplit((parts.scheme.lower(), authority, parts.path, parts.query, ""))
