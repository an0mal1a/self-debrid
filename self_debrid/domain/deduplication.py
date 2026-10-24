"""Pure, order-independent merging of discovery duplicates."""

from dataclasses import fields
from typing import Iterable

from .models import StreamCandidate


def deduplicate_candidates(
    candidates: Iterable[StreamCandidate],
) -> list[StreamCandidate]:
    groups: dict[str, list[StreamCandidate]] = {}
    for candidate in candidates:
        # Unknown file selection is a torrent-level identity. Do not guess that
        # it describes a known file, or copy a pack's size onto a selected file.
        groups.setdefault(candidate.identity, []).append(candidate)
    return [_merge(groups[key]) for key in sorted(groups)]


def _merge(group: list[StreamCandidate]) -> StreamCandidate:
    def quality(candidate: StreamCandidate) -> tuple:
        return (
            candidate.cached,
            candidate.seeders if candidate.seeders is not None else -1,
            sum(
                getattr(candidate, field.name) is not None
                for field in fields(candidate)
            ),
            candidate.provider,
            candidate.title,
            repr(candidate),
            candidate.url or "",
        )

    ordered = sorted(group, key=quality, reverse=True)
    values = {}
    for field in fields(StreamCandidate):
        if field.name in ("languages", "cached", "seeders"):
            continue
        values[field.name] = next(
            (
                getattr(candidate, field.name)
                for candidate in ordered
                if getattr(candidate, field.name) is not None
            ),
            None,
        )
    seeders = [
        candidate.seeders for candidate in ordered if candidate.seeders is not None
    ]
    return StreamCandidate(
        **values,
        languages=tuple(
            sorted(
                {language for candidate in ordered for language in candidate.languages}
            )
        ),
        cached=any(candidate.cached for candidate in ordered),
        seeders=max(seeders) if seeders else None,
    )
