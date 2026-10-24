"""Discovery CLI and native Stremio addon server entry points."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict, replace
from typing import Sequence

from self_debrid.adapters.stremio import parse_stream_request
from self_debrid.adapters.stremio.http import create_app, serve
from self_debrid.domain import DiscoveryResult, MediaRequest
from self_debrid.infrastructure.bootstrap import build_search_service
from self_debrid.infrastructure.config import AppConfig


async def run_search(config: AppConfig, request: MediaRequest) -> DiscoveryResult:
    return await build_search_service(config).search(request)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Self-Debrid v2 discovery CLI and Stremio addon"
    )
    parser.add_argument("--version", action="version", version="2.0.0.dev1")
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser(
        "search", help="Discover ranked releases; no downloading"
    )
    search.add_argument("media_type", choices=("movie", "series"))
    search.add_argument("id", help="IMDb ID; series use tt1234567:2:5")
    search.add_argument(
        "--title", help="Explicit title for indexers that need text search"
    )
    server = commands.add_parser("serve", help="Serve the native Stremio torrent addon")
    server.add_argument("--host", help="Bind address; default 127.0.0.1 or HTTP_HOST")
    server.add_argument("--port", type=int, help="Bind port; default 8080 or HTTP_PORT")
    args = parser.parse_args(argv)
    if args.command == "serve":
        environ = dict(os.environ)
        if args.host is not None:
            environ["HTTP_HOST"] = args.host
        if args.port is not None:
            environ["HTTP_PORT"] = str(args.port)
        try:
            config = AppConfig.from_env(environ)
        except ValueError as exc:
            parser.error(str(exc))
        app = create_app(
            build_search_service(config), secrets=(config.torznab.api_key,)
        )
        print(
            "Self-Debrid native torrent addon: " + config.server.manifest_url,
            flush=True,
        )
        try:
            serve(app, host=config.server.host, port=config.server.port)
        except OSError:
            parser.error("Unable to bind HTTP server; check host and port")
        return 0
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
