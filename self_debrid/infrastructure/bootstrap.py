"""Compose the discovery pipeline for CLI and HTTP entry points."""

from self_debrid.application.search import SearchService
from self_debrid.infrastructure.config import AppConfig
from self_debrid.providers.registry import ProviderRegistry
from self_debrid.providers.torznab import TorznabProvider


def build_search_service(config: AppConfig) -> SearchService:
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
    return SearchService(registry, config.ranking)
