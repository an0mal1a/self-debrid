"""Stremio protocol helpers for the v2 client adapter."""

from __future__ import annotations

from self_debrid.domain import MediaRequest, MediaType


def build_manifest(version: str = "2.0.0-dev") -> dict:
    return {
        "id": "com.selfdebrid.addon",
        "version": version,
        "name": "Self-Debrid",
        "description": "Self-hosted discovery, cache and streaming engine.",
        "resources": [
            {
                "name": "stream",
                "types": ["movie", "series"],
                "idPrefixes": ["tt"],
            }
        ],
        "types": ["movie", "series"],
        "catalogs": [],
        "idPrefixes": ["tt"],
    }


def parse_stream_request(media_type: str, raw_id: str) -> MediaRequest:
    if media_type == MediaType.MOVIE.value:
        return MediaRequest(
            media_type=MediaType.MOVIE,
            imdb_id=raw_id,
        )

    if media_type != MediaType.SERIES.value:
        raise ValueError(f"unsupported Stremio media type: {media_type}")

    parts = raw_id.split(":")
    if len(parts) != 3:
        raise ValueError(
            "series stream id must use imdb_id:season:episode",
        )

    imdb_id, season_text, episode_text = parts

    try:
        season = int(season_text)
        episode = int(episode_text)
    except ValueError as exc:
        raise ValueError("season and episode must be integers") from exc

    return MediaRequest(
        media_type=MediaType.SERIES,
        imdb_id=imdb_id,
        season=season,
        episode=episode,
    )
