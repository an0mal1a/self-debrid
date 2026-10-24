"""Compose bounded discovery resources for CLI and HTTP entry points."""

import hashlib
import json
from dataclasses import asdict

from self_debrid.application.cache import AsyncTTLCache
from self_debrid.application.search import SearchService
from self_debrid.infrastructure.config import AppConfig
from self_debrid.metadata.cinemeta import CinemetaResolver
from self_debrid.providers.registry import ProviderRegistry
from self_debrid.providers.torznab import TorznabProvider


def build_providers(config: AppConfig) -> tuple[TorznabProvider, ...]:
    return tuple(
        TorznabProvider(
            settings.name,
            settings.base_url,
            settings.api_key,
            connect_timeout=settings.connect_timeout,
            read_timeout=settings.read_timeout,
        )
        for settings in config.configured_providers
    )


def build_metadata_resolver(config: AppConfig) -> CinemetaResolver | None:
    settings = config.metadata
    if not settings.enabled:
        return None
    return CinemetaResolver(
        connect_timeout=settings.connect_timeout,
        read_timeout=settings.read_timeout,
        ttl=settings.ttl,
        negative_ttl=settings.negative_ttl,
        max_entries=settings.max_entries,
    )


def build_search_service(config: AppConfig) -> SearchService:
    registry = ProviderRegistry(
        build_providers(config),
        max_concurrency=config.max_concurrency,
        provider_timeout=config.provider_timeout,
    )
    settings = config.discovery_cache
    # Fingerprint includes endpoints, credentials, metadata options and ranking.
    # Neither raw config nor this internal fingerprint is written to diagnostics.
    namespace = hashlib.sha256(
        json.dumps(asdict(config), sort_keys=True).encode()
    ).hexdigest()
    return SearchService(
        registry,
        config.ranking,
        metadata=build_metadata_resolver(config),
        metadata_timeout=config.metadata.connect_timeout + config.metadata.read_timeout,
        cache=AsyncTTLCache(
            max_entries=settings.max_entries,
            max_bytes=settings.max_bytes,
            max_inflight=settings.max_inflight,
        ),
        cache_ttl=settings.ttl,
        cache_namespace=namespace,
    )
