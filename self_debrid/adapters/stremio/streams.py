"""Pure mapping to native Stremio torrent streams; never HTTP video URLs."""

import re
from urllib.parse import quote, quote_plus, unquote

from self_debrid.domain import MediaRequest, MediaType, StreamCandidate
from self_debrid.domain.episode_selection import EPISODE_LABEL, matches_episode

# Stremio core's Torrent source uses a 20-byte hash and an optional u16 fileIdx.
MAX_FILE_INDEX = 65535


def _public_text(value: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        if secret:
            for representation in (secret, quote(secret, safe=""), quote_plus(secret)):
                value = value.replace(representation, "[redacted]")
    for _ in range(2):
        value = unquote(value)
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[redacted]")
    # Bound scheme matching and avoid restarting inside long release words.
    value = re.sub(
        r"(?i)(?<![a-z0-9+.-])(?:[a-z][a-z0-9+.-]{0,31}://|magnet:\?)[^\s]+",
        "[redacted URL]",
        value,
    )
    value = re.sub(
        r"(?i)\b(?:api[_-]?key|passkey|token|password)\s*[=:]\s*[^\s,;]+",
        "[redacted]",
        value,
    )
    # Do not pass control characters or unbounded remote metadata to clients.
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    return " ".join(value.split())[:500]


def candidate_to_stream(
    candidate: StreamCandidate,
    request: MediaRequest,
    *,
    secrets: tuple[str, ...] = (),
) -> dict[str, object] | None:
    if candidate.info_hash is None or not re.fullmatch(
        r"[0-9a-f]{40}", candidate.info_hash
    ):
        return None
    if candidate.file_index is not None and candidate.file_index > MAX_FILE_INDEX:
        return None
    if not matches_episode(candidate, request):
        return None
    if request.media_type is MediaType.MOVIE and (
        candidate.season is not None
        or candidate.episode is not None
        or EPISODE_LABEL.search(candidate.title)
    ):
        return None
    information = [candidate.title]
    quality = " · ".join(
        value
        for value in (candidate.resolution, candidate.codec, candidate.hdr)
        if value
    )
    if quality:
        information.append(quality)
    if candidate.languages:
        information.append("Languages: " + ", ".join(candidate.languages))
    if candidate.size_bytes is not None:
        information.append(f"Size: {candidate.size_bytes / (1024**3):.2f} GiB")
    if candidate.seeders is not None:
        information.append(f"Seeders: {candidate.seeders}")
    information.append("Provider: " + candidate.provider)
    if candidate.file_index is None:
        information.append("Native torrent playback; Stremio selects the largest file")
    else:
        information.append(
            f"Native torrent playback; file index {candidate.file_index}"
        )
    stream: dict[str, object] = {
        "infoHash": candidate.info_hash,
        "name": _public_text(
            "Self-Debrid"
            + (" · " + candidate.resolution if candidate.resolution else ""),
            secrets,
        ),
        "description": "\n".join(_public_text(value, secrets) for value in information),
    }
    if candidate.file_index is not None:
        stream["fileIdx"] = candidate.file_index
    # No url, externalUrl, sources, announce URLs, magnet URIs or provider errors.
    return stream
