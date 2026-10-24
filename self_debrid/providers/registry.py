"""Bounded, cancellable provider execution with structured failure isolation."""

from __future__ import annotations

import asyncio
import math
from typing import Iterable, Sequence

from self_debrid.domain import (
    DiscoveryResult,
    MediaRequest,
    ProviderFailure,
    StreamCandidate,
)
from self_debrid.providers.base import Provider, ProviderError


class ProviderRegistry:
    def __init__(
        self,
        providers: Iterable[Provider] = (),
        *,
        max_concurrency: int = 4,
        provider_timeout: float = 15.0,
    ) -> None:
        if type(max_concurrency) is not int or max_concurrency < 1:
            raise ValueError("max_concurrency must be a positive integer")
        if not math.isfinite(provider_timeout) or provider_timeout <= 0:
            raise ValueError("provider_timeout must be finite and positive")
        self._providers: list[Provider] = []
        self._max_concurrency = max_concurrency
        self._provider_timeout = provider_timeout
        for provider in providers:
            self.register(provider)

    @property
    def providers(self) -> Sequence[Provider]:
        return tuple(self._providers)

    def register(self, provider: Provider) -> None:
        if not provider.name.strip():
            raise ValueError("provider name must not be blank")
        if any(existing.name == provider.name for existing in self._providers):
            raise ValueError(f"provider already registered: {provider.name}")
        self._providers.append(provider)

    async def search(self, request: MediaRequest) -> DiscoveryResult:
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def run(
            provider: Provider,
        ) -> tuple[Sequence[StreamCandidate], ProviderFailure | None]:
            async with semaphore:
                try:
                    candidates = await asyncio.wait_for(
                        provider.search(request),
                        timeout=self._provider_timeout,
                    )
                    candidates = tuple(candidates)
                    if not all(
                        isinstance(candidate, StreamCandidate)
                        for candidate in candidates
                    ):
                        raise TypeError("provider returned invalid candidates")
                    return candidates, None
                except asyncio.TimeoutError:
                    return (), ProviderFailure(
                        provider.name, "Provider search timed out", "timeout"
                    )
                except ProviderError as exc:
                    return (), ProviderFailure(provider.name, str(exc), exc.code)
                except Exception:
                    # Third-party exceptions may contain URLs, API keys or HTML.
                    return (), ProviderFailure(
                        provider.name, "Provider search failed", "provider_error"
                    )

        results = await asyncio.gather(*(run(provider) for provider in self.providers))
        return DiscoveryResult(
            candidates=tuple(
                candidate for candidates, _ in results for candidate in candidates
            ),
            failures=tuple(failure for _, failure in results if failure is not None),
        )
