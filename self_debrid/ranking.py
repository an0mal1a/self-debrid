"""Deterministic ranking for normalized stream candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from self_debrid.domain import StreamCandidate


DEFAULT_RESOLUTION_ORDER = (
    "2160p",
    "1080p",
    "720p",
    "480p",
)


@dataclass(frozen=True, slots=True)
class RankingPreferences:
    resolution_order: Tuple[str, ...] = DEFAULT_RESOLUTION_ORDER
    preferred_languages: Tuple[str, ...] = ()
    max_size_bytes: Optional[int] = None
    min_seeders: int = 0


def rank_candidates(
    candidates: Iterable[StreamCandidate],
    preferences: RankingPreferences = RankingPreferences(),
) -> list[StreamCandidate]:
    eligible = [
        candidate
        for candidate in candidates
        if _is_eligible(candidate, preferences)
    ]

    return sorted(
        eligible,
        key=lambda candidate: _sort_key(candidate, preferences),
        reverse=True,
    )


def _is_eligible(
    candidate: StreamCandidate,
    preferences: RankingPreferences,
) -> bool:
    if (
        preferences.max_size_bytes is not None
        and candidate.size_bytes is not None
        and candidate.size_bytes > preferences.max_size_bytes
    ):
        return False

    if (
        candidate.seeders is not None
        and candidate.seeders < preferences.min_seeders
        and not candidate.cached
    ):
        return False

    return True


def _sort_key(
    candidate: StreamCandidate,
    preferences: RankingPreferences,
) -> tuple[int, int, int, int, str]:
    return (
        1 if candidate.cached else 0,
        _resolution_score(candidate.resolution, preferences.resolution_order),
        _language_score(candidate.languages, preferences.preferred_languages),
        candidate.seeders or 0,
        candidate.identity,
    )


def _resolution_score(
    resolution: Optional[str],
    resolution_order: Tuple[str, ...],
) -> int:
    if resolution is None:
        return 0

    normalized = resolution.lower().strip()
    try:
        index = resolution_order.index(normalized)
    except ValueError:
        return 0

    return len(resolution_order) - index


def _language_score(
    languages: Tuple[str, ...],
    preferred_languages: Tuple[str, ...],
) -> int:
    if not preferred_languages:
        return 0

    normalized_preferences = tuple(
        language.lower().strip()
        for language in preferred_languages
    )

    best = 0
    for language in languages:
        if language not in normalized_preferences:
            continue
        index = normalized_preferences.index(language)
        score = len(normalized_preferences) - index
        best = max(best, score)

    return best
