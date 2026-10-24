"""Discovery orchestration without client, downloader or server dependencies."""

import asyncio
import json
from collections import Counter
from dataclasses import asdict, replace

from self_debrid.application.cache import AsyncTTLCache, CacheBusy
from self_debrid.application.metadata import MetadataResolver, MetadataResult
from self_debrid.domain import DiscoveryResult, MediaRequest, ProviderFailure
from self_debrid.domain.deduplication import deduplicate_candidates
from self_debrid.domain.ranking import RankingPreferences, rank_candidates
from self_debrid.domain.validation import exclusion_reason
from self_debrid.providers.base import Provider, TitleAwareProvider
from self_debrid.providers.registry import ProviderRegistry


class SearchService:
    def __init__(
        self,
        registry: ProviderRegistry,
        preferences: RankingPreferences = RankingPreferences(),
        *,
        metadata: MetadataResolver | None = None,
        metadata_timeout: float = 8,
        cache: AsyncTTLCache[DiscoveryResult] | None = None,
        cache_ttl: float = 0,
        cache_namespace: str = "",
    ) -> None:
        self._registry = registry
        self._preferences = preferences
        self._metadata = metadata
        self._metadata_timeout = metadata_timeout
        self._cache = cache
        self._cache_ttl = cache_ttl
        # Cache is owned by this immutable composition; namespace fingerprints the
        # full config. Provider registration changes are included in each key.
        self._cache_namespace = cache_namespace

    async def search(self, request: MediaRequest) -> DiscoveryResult:
        if self._cache is None:
            return await self._search(request)
        key = (
            self._cache_namespace,
            request,
            self._preferences,
            tuple(p.name for p in self._registry.providers),
        )
        try:
            return await self._cache.get(
                key,
                lambda: self._search(request),
                ttl=lambda result: (
                    self._cache_ttl
                    if not result.failures and not result.metadata_error
                    else 0
                ),
                weight=lambda result: len(json.dumps(asdict(result)).encode()) + 512,
            )
        except CacheBusy:
            return DiscoveryResult(
                (), (ProviderFailure("discovery", "Too many active searches", "busy"),)
            )

    async def _search(self, request: MediaRequest) -> DiscoveryResult:
        lookup: asyncio.Task[MetadataResult] | None = None
        metadata_error = None
        resolved = None

        async def resolve() -> MetadataResult:
            try:
                result = await asyncio.wait_for(
                    self._metadata.resolve(request), self._metadata_timeout
                )
                if result.metadata and (
                    result.metadata.imdb_id != request.imdb_id
                    or result.metadata.media_type != request.media_type
                ):
                    return MetadataResult(error="identity_mismatch")
                return result
            except (asyncio.TimeoutError, TimeoutError):
                return MetadataResult(error="timeout")
            except Exception:
                return MetadataResult(error="metadata_error")

        async def prepare(provider: Provider, original: MediaRequest) -> MediaRequest:
            nonlocal lookup, metadata_error, resolved
            enriched = original
            if (
                self._metadata
                and not original.title
                and isinstance(provider, TitleAwareProvider)
            ):
                if await provider.requires_title(original):
                    if lookup is None:
                        lookup = asyncio.create_task(resolve())
                    result = await asyncio.shield(lookup)
                    metadata_error = result.error
                    if result.metadata:
                        resolved = result.metadata
                        enriched = replace(
                            original,
                            title=result.metadata.title,
                            year=result.metadata.year,
                        )
            return enriched

        try:
            result = await self._registry.search(request, prepare=prepare)
        finally:
            if lookup is not None:
                if not lookup.done():
                    lookup.cancel()
                await asyncio.gather(lookup, return_exceptions=True)
        counts = Counter()
        valid = []
        validation_request = (
            replace(request, title=resolved.title, year=resolved.year)
            if resolved
            else request
        )
        for candidate in result.candidates:
            reason = exclusion_reason(candidate, validation_request)
            if reason:
                counts[reason] += 1
            else:
                valid.append(candidate)
        unique = deduplicate_candidates(valid)
        ranked = rank_candidates(unique, self._preferences)
        counts["duplicate"] = len(valid) - len(unique)
        counts["ranking_preferences"] = len(unique) - len(ranked)
        return DiscoveryResult(
            candidates=tuple(ranked),
            failures=result.failures,
            exclusions=tuple(
                sorted((key, count) for key, count in counts.items() if count)
            ),
            metadata_error=metadata_error,
        )
