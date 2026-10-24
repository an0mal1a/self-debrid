"""Conservative episode evidence, independent of torrent clients and adapters."""

import re

from .models import MediaRequest, MediaType, StreamCandidate

EPISODE_LABEL = re.compile(r"(?<![a-z0-9])s([0-9]{1,2})e([0-9]{1,3})(?![a-z0-9])", re.I)
PACK_LABEL = re.compile(
    r"(?<![a-z0-9])(?:complete|pack|collection|seasons?|s[0-9]{1,2})(?![a-z0-9])",
    re.I,
)
EPISODE_RANGE = re.compile(
    r"s[0-9]{1,2}e[0-9]{1,3}(?:e[0-9]{1,3}|[ ._-]+(?:s[0-9]{1,2})?e?[0-9]{1,3})(?![a-z0-9])",
    re.I,
)
SEASON_LABEL = re.compile(
    r"(?<![a-z0-9])s([0-9]{1,2})(?=e[0-9]|[^a-z0-9]|$)"
    r"|(?<![a-z0-9])season[ ._-]+([0-9]{1,2})(?![a-z0-9])",
    re.I,
)


def is_ambiguous_episode_name(title: str) -> bool:
    return bool(
        PACK_LABEL.search(title)
        or EPISODE_RANGE.search(title)
        or len(EPISODE_LABEL.findall(title)) > 1
    )


def matches_episode(candidate: StreamCandidate, request: MediaRequest) -> bool:
    """A selected file still needs evidence that it belongs to this episode.

    Without an index, accept only a standalone, single exact episode name.
    Playback clients may choose the largest file; this is not verified selection.
    """
    if request.media_type is not MediaType.SERIES:
        return True
    target = (request.season, request.episode)
    if candidate.season is not None and candidate.season != request.season:
        return False
    if candidate.episode is not None and candidate.episode != request.episode:
        return False
    if any(
        int(match[1] or match[2]) != request.season
        for match in SEASON_LABEL.finditer(candidate.title)
    ):
        return False
    labels = tuple(
        (int(match[1]), int(match[2]))
        for match in EPISODE_LABEL.finditer(candidate.title)
    )
    multiple_episodes = bool(EPISODE_RANGE.search(candidate.title) or len(labels) > 1)
    ambiguous = is_ambiguous_episode_name(candidate.title)
    if len(labels) == 1 and not multiple_episodes and labels[0] != target:
        return False
    exact_name = len(labels) == 1 and labels[0] == target and not ambiguous
    if candidate.file_index is None:
        return exact_name
    declared_episode = (candidate.season, candidate.episode) == target
    return exact_name or declared_episode
