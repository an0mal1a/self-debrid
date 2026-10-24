"""Local-only diagnostics; never output credentials, endpoint paths or candidates."""

import asyncio

from self_debrid.adapters.stremio.streams import candidate_to_stream, redact_text
from self_debrid.application.search import SearchService
from self_debrid.domain import MediaRequest
from self_debrid.infrastructure.bootstrap import (
    build_metadata_resolver,
    build_providers,
)
from self_debrid.infrastructure.config import AppConfig
from self_debrid.providers.base import ProviderError
from self_debrid.providers.registry import ProviderRegistry
from self_debrid.providers.torznab import TorznabProvider

KNOWN_PARAMS = frozenset(
    {
        "q",
        "imdbid",
        "year",
        "season",
        "ep",
        "tvdbid",
        "tmdbid",
        "rid",
        "tvmazeid",
        "genre",
        "cat",
        "limit",
        "offset",
        "extended",
    }
)


async def diagnose(config: AppConfig, request: MediaRequest) -> dict[str, object]:
    resolver = build_metadata_resolver(config)
    semaphore = asyncio.Semaphore(config.max_concurrency)
    secrets = tuple(provider.api_key for provider in config.configured_providers)

    async def inspect(provider: TorznabProvider) -> dict[str, object]:
        report: dict[str, object] = {"name": redact_text(provider.name, secrets)}
        async with semaphore:
            try:
                caps = await asyncio.wait_for(
                    provider.get_capabilities(), config.provider_timeout
                )
                report.update(
                    endpoint="responding",
                    capabilities={
                        "movie": sorted(caps.movie & KNOWN_PARAMS)
                        if caps.movie is not None
                        else None,
                        "tv": sorted(caps.tv & KNOWN_PARAMS)
                        if caps.tv is not None
                        else None,
                        "search": sorted(caps.search & KNOWN_PARAMS)
                        if caps.search is not None
                        else None,
                    },
                )
                service = SearchService(
                    ProviderRegistry(
                        [provider], provider_timeout=config.provider_timeout
                    ),
                    config.ranking,
                    metadata=resolver,
                    metadata_timeout=config.metadata.connect_timeout
                    + config.metadata.read_timeout,
                )
                result = await service.search(request)
                compatible = sum(
                    candidate_to_stream(c, request, secrets=secrets) is not None
                    for c in result.candidates
                )
                report.update(
                    candidates=len(result.candidates),
                    compatible=compatible,
                    compatibility_excluded=len(result.candidates) - compatible,
                    exclusions=dict(result.exclusions),
                    errors=[failure.code for failure in result.failures],
                    metadata_error=result.metadata_error,
                )
            except (asyncio.TimeoutError, TimeoutError):
                report.update(endpoint="failed", errors=["timeout"])
            except ProviderError as exc:
                report.update(endpoint="failed", errors=[exc.code])
            except Exception:
                report.update(endpoint="failed", errors=["provider_error"])
        return report

    # These are local, intentional operator diagnostics, not a public route.
    providers = await asyncio.gather(
        *(inspect(provider) for provider in build_providers(config))
    )
    if resolver is None:
        metadata = {"status": "disabled"}
    else:
        result = await resolver.resolve(request)
        metadata = {
            "status": "ok" if result.metadata else "unavailable",
            "error": result.error,
        }
    return {
        "media": request.key,
        "metadata": metadata,
        "providers": providers,
        "mode": "native-torrent-discovery; no playback verification",
    }
