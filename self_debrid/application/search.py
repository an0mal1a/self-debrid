"""Discovery orchestration without client, downloader or server dependencies."""

from self_debrid.domain import DiscoveryResult, MediaRequest
from self_debrid.domain.deduplication import deduplicate_candidates
from self_debrid.domain.episode_selection import matches_episode
from self_debrid.domain.ranking import RankingPreferences, rank_candidates
from self_debrid.providers.registry import ProviderRegistry


class SearchService:
    def __init__(
        self,
        registry: ProviderRegistry,
        preferences: RankingPreferences = RankingPreferences(),
    ) -> None:
        self._registry = registry
        self._preferences = preferences

    async def search(self, request: MediaRequest) -> DiscoveryResult:
        result = await self._registry.search(request)
        return DiscoveryResult(
            candidates=tuple(
                rank_candidates(
                    deduplicate_candidates(
                        candidate
                        for candidate in result.candidates
                        if matches_episode(candidate, request)
                    ),
                    self._preferences,
                )
            ),
            failures=result.failures,
        )
