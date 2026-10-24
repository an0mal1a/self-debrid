"""Thin aiohttp implementation of the stream-only Stremio addon protocol."""

import logging
from typing import Awaitable, Callable

from aiohttp import web

from self_debrid.adapters.stremio import build_manifest, parse_stream_request
from self_debrid.adapters.stremio.streams import candidate_to_stream
from self_debrid.application.search import SearchService

logger = logging.getLogger(__name__)


@web.middleware
async def protocol_headers(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    try:
        if request.method == "OPTIONS":
            response = web.Response(status=204)
        else:
            response = await handler(request)
    except web.HTTPException as exc:
        response = web.json_response(
            {"error": "not_found" if exc.status == 404 else "http_error"},
            status=exc.status,
        )
    except Exception:
        # Raw exceptions can include request URLs or provider credentials.
        logger.error("Stremio request failed")
        response = web.json_response({"error": "internal_error"}, status=500)
    response.headers.update(
        {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Cache-Control": "no-store",
        }
    )
    return response


def create_app(
    service: SearchService, *, secrets: tuple[str, ...] = ()
) -> web.Application:
    """Use an injected, bounded SearchService; creating the app performs no I/O."""
    app = web.Application(middlewares=[protocol_headers])

    async def manifest(request: web.Request) -> web.Response:
        return web.json_response(build_manifest())

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "mode": "stremio-native-torrent"})

    async def streams(request: web.Request) -> web.Response:
        try:
            raw_id = request.match_info["id"]
            if len(raw_id) > 128:
                raise ValueError("identifier too long")
            media_request = parse_stream_request(
                request.match_info["media_type"], raw_id
            )
        except ValueError:
            return web.json_response(
                {
                    "error": "invalid_id",
                    "message": "Use an IMDb movie ID or imdb_id:season:episode for series",
                },
                status=400,
            )
        # ProviderRegistry enforces per-provider deadlines and retains healthy
        # results. No adapter-specific provider querying or ranking occurs here.
        result = await service.search(media_request)
        if result.failures:
            logger.warning(
                "Discovery completed with %d provider failures", len(result.failures)
            )
        output = [
            stream
            for candidate in result.candidates
            if (
                stream := candidate_to_stream(candidate, media_request, secrets=secrets)
            )
            is not None
        ]
        return web.json_response({"streams": output})

    app.add_routes(
        [
            web.get("/manifest.json", manifest),
            web.get("/health", health),
            web.get("/stream/{media_type}/{id}.json", streams),
        ]
    )
    return app


def serve(app: web.Application, *, host: str, port: int) -> None:
    # Disable raw access logs (which include request paths/queries). Cancel
    # searches when clients disconnect; the registry propagates cancellation.
    web.run_app(
        app,
        host=host,
        port=port,
        access_log=None,
        print=None,
        handler_cancellation=True,
    )
