"""Conservative, deterministic rejection of explicit release contradictions."""

import re
import unicodedata

from self_debrid.domain.episode_selection import EPISODE_LABEL, matches_episode
from self_debrid.domain.models import MediaRequest, MediaType, StreamCandidate


def _words(value: str) -> tuple[str, ...]:
    value = re.sub(r"^(?:\s*\[[^\]\r\n]{1,80}\]\s*)+", "", value)
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(c for c in value if not unicodedata.combining(c))
    return tuple(re.findall(r"[^\W_]+", value, re.UNICODE))


def exclusion_reason(candidate: StreamCandidate, request: MediaRequest) -> str | None:
    if candidate.imdb_id and candidate.imdb_id != request.imdb_id:
        return "identity_mismatch"
    if not matches_episode(candidate, request):
        return "episode_mismatch_or_ambiguous"
    if request.media_type is MediaType.MOVIE:
        if (
            candidate.season is not None
            or candidate.episode is not None
            or EPISODE_LABEL.search(candidate.title)
        ):
            return "wrong_media_type"
        title_number = request.title and str(candidate.year) in _words(request.title)
        if (
            request.year
            and candidate.year
            and not title_number
            and abs(request.year - candidate.year) > 1
        ):
            return "year_mismatch"
    # An echoed matching ID is stronger than localized/alternate title spelling.
    if request.title and not candidate.imdb_id:
        boundary = re.search(
            r"(?i)(?<![a-z0-9])(?:(?:18|19|20)[0-9]{2}|s[0-9]{1,2}(?:e[0-9]{1,3})?|2160p|1080p|720p|480p)(?![a-z0-9])",
            candidate.title,
        )
        if boundary and boundary.start() > 0:
            release = _words(candidate.title[: boundary.start()])
            expected = _words(request.title)
            # Leading English articles vary between indexers and metadata.
            if release and release[0] in ("the", "a", "an"):
                release = release[1:]
            if expected and expected[0] in ("the", "a", "an"):
                expected = expected[1:]
            # Exact token prefix after punctuation/accent normalization, no fuzzy
            # threshold. If no reliable release boundary exists, remain permissive.
            # Numeric movie titles may themselves be mistaken for a year boundary.
            if expected and release and not any(word.isdecimal() for word in expected):
                if release[: len(expected)] != expected:
                    return "title_mismatch"
    return None
