"""Stremio protocol helpers for the v2 client adapter."""

from __future__ import annotations

import re

from self_debrid.domain import MediaRequest, MediaType


def build_manifest(version: str = "2.0.0-dev.2") -> dict:
    return {
        "id": "com.selfdebrid.addon",
        "version": version,
        "name": "Self-Debrid",
        "description": "Torznab discovery with native Stremio torrent playback. No local playback or cache.",
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

    if not all(re.fullmatch(r"[0-9]+", text) for text in (season_text, episode_text)):
        raise ValueError("season and episode must be non-negative integers")
    season = int(season_text)
    episode = int(episode_text)

    return MediaRequest(
        media_type=MediaType.SERIES,
        imdb_id=imdb_id,
        season=season,
        episode=episode,
    )
