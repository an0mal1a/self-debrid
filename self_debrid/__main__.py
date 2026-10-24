"""Discovery CLI. No server or playback is started."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, replace
from typing import Sequence

from self_debrid.adapters.stremio import parse_stream_request
from self_debrid.application.search import SearchService
from self_debrid.domain import DiscoveryResult, MediaRequest
from self_debrid.infrastructure.config import AppConfig
from self_debrid.providers.registry import ProviderRegistry
from self_debrid.providers.torznab import TorznabProvider


async def run_search(config: AppConfig, request: MediaRequest) -> DiscoveryResult:
    settings = config.torznab
    registry = ProviderRegistry(
        [
            TorznabProvider(
                settings.name,
                settings.base_url,
                settings.api_key,
                connect_timeout=settings.connect_timeout,
                read_timeout=settings.read_timeout,
            )
        ],
        max_concurrency=config.max_concurrency,
        provider_timeout=config.provider_timeout,
    )
    return await SearchService(registry, config.ranking).search(request)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Self-Debrid v2 discovery CLI")
    parser.add_argument("--version", action="version", version="2.0.0.dev0")
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser(
        "search", help="Discover ranked releases; no downloading"
    )
    search.add_argument("media_type", choices=("movie", "series"))
    search.add_argument("id", help="IMDb ID; series use tt1234567:2:5")
    search.add_argument(
        "--title", help="Explicit title for indexers that need text search"
    )
    args = parser.parse_args(argv)
    try:
        request = replace(
            parse_stream_request(args.media_type, args.id), title=args.title
        )
        config = AppConfig.from_env()
    except ValueError as exc:
        parser.error(str(exc))
    result = asyncio.run(run_search(config, request))
    output = asdict(result)
    # Download URLs may contain private tracker passkeys or API credentials.
    # Library callers receive them; diagnostics/CLI output only expose identities.
    for record, candidate in zip(output["candidates"], result.candidates):
        record.pop("url")
        record["identity"] = (
            candidate.identity if candidate.info_hash else "url:[redacted]"
        )
    print(json.dumps(output, indent=2))
    return 1 if result.failures and not result.candidates else 0


if __name__ == "__main__":
    raise SystemExit(main())
