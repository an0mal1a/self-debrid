"""Concurrent provider registry with failure isolation and deduplication."""

from __future__ import annotations

import asyncio
from typing import Iterable, Sequence

from self_debrid.domain import (
    DiscoveryResult,
    MediaRequest,
    ProviderFailure,
    StreamCandidate,
)
from self_debrid.providers.base import Provider


class ProviderRegistry:
    def __init__(self, providers: Iterable[Provider] = ()) -> None:
        self._providers = list(providers)

    @property
    def providers(self) -> Sequence[Provider]:
        return tuple(self._providers)

    def register(self, provider: Provider) -> None:
        if any(existing.name == provider.name for existing in self._providers):
            raise ValueError(f"provider already registered: {provider.name}")
        self._providers.append(provider)

    async def search(self, request: MediaRequest) -> DiscoveryResult:
        tasks = [
            self._search_provider(provider, request)
            for provider in self._providers
        ]
        results = await asyncio.gather(*tasks)

        failures = []
        candidates = []

        for provider_candidates, provider_failure in results:
            candidates.extend(provider_candidates)
            if provider_failure is not None:
                failures.append(provider_failure)

        return DiscoveryResult(
            candidates=tuple(self._deduplicate(candidates)),
            failures=tuple(failures),
        )

    async def _search_provider(
        self,
        provider: Provider,
        request: MediaRequest,
    ) -> tuple[Sequence[StreamCandidate], ProviderFailure | None]:
        try:
            return await provider.search(request), None
        except Exception as exc:
            return (), ProviderFailure(
                provider=provider.name,
                error=str(exc),
            )

    def _deduplicate(
        self,
        candidates: Iterable[StreamCandidate],
    ) -> list[StreamCandidate]:
        unique: dict[str, StreamCandidate] = {}

        for candidate in candidates:
            current = unique.get(candidate.identity)
            if current is None or self._is_better_duplicate(candidate, current):
                unique[candidate.identity] = candidate

        return list(unique.values())

    def _is_better_duplicate(
        self,
        candidate: StreamCandidate,
        current: StreamCandidate,
    ) -> bool:
        candidate_key = (
            candidate.cached,
            candidate.seeders or 0,
            candidate.size_bytes or 0,
        )
        current_key = (
            current.cached,
            current.seeders or 0,
            current.size_bytes or 0,
        )
        return candidate_key > current_key
