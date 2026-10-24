# Self-Debrid v2 architecture

## Complete separation

`main` (stable Kodi v1) and `v1-kodi` (v1 maintenance) are separate implementations
from `v2-development`. v2 contains no legacy application, compatibility shim or
legacy archive. It imports nothing from v1. There is no shared database, startup
module or configuration contract. Draft PR #2 is a review vehicle, not a merge
instruction.

## Current package

```text
self_debrid/
    __init__.py
    __main__.py
    domain/
        __init__.py
        models.py
        identity.py
        deduplication.py
        ranking.py
    application/
        __init__.py
        search.py
    providers/
        __init__.py
        base.py
        registry.py
        torznab.py
    metadata/
        __init__.py
        release_parser.py
    adapters/
        __init__.py
        stremio/
            __init__.py
    infrastructure/
        __init__.py
        config.py
```

`identity.py` and `deduplication.py` are separate pure modules because they are
shared rules, not Torznab transport behavior. The existing immutable models,
lexicographic ranking and Stremio parsing/manifest helpers have been preserved
and tightened in their new locations. Old experimental `self_debrid.ranking` and
`self_debrid.stremio` import paths are replaced by the package layout above.

## Dependency boundaries and data flow

1. The CLI explicitly reads environment configuration and validates a movie or
   episode request using the adapter's IMDb parser.
2. `SearchService` accepts `MediaRequest`, calls `ProviderRegistry`, deduplicates
   normalized candidates and applies pure ranking preferences.
3. `ProviderRegistry` executes `Provider.search` asynchronously with a semaphore
   and per-active-provider timeout. It aggregates candidates and structured
   `ProviderFailure(provider, error, code)` objects in registration order.
4. `TorznabProvider` obtains capabilities, constructs a supported query, fetches
   XML and returns `StreamCandidate` objects using the release parser.
5. The CLI prints JSON metadata and failures. It excludes private download URLs;
   library results retain them for future resolver use.

The registry intentionally does not deduplicate or rank. Application search owns
those use-case steps. Domain and metadata modules use only the standard library.
Application code has no client-protocol, web framework or downloader imports.
Infrastructure reads environment variables only when called. Runtime external
dependencies are `aiohttp` and `defusedxml`; `setuptools` builds the package.

## Media requests and release metadata

Movie requests contain a `MediaType.MOVIE` and an ASCII IMDb title ID. Series
requests also require non-negative integer season and episode coordinates.
Specials (`season=0`) and episode zero remain valid, preserving the foundation
contract. An optional explicit `title` supports text searches without adding a
metadata service. A title is a search hint, not part of the media identity.

Recognized release labels are bounded tokens: 2160p/1080p/720p/480p;
H.264/x264/AVC, H.265/x265/HEVC, AV1; HDR/HDR10/Dolby Vision/DoVi/DV;
Spanish/SPA/ESP/Castellano/Español, English/ENG, Latino and MULTI; SxxExxx.
Languages map to `es`, `en`, `es-419` and `multi`. MULTI does not imply a list of
languages. Ambiguous short title tokens such as EN, ES and LAT are not languages.
Attributes provide optional size, seeders, season, episode, video, resolution and
language. Subtitle attributes are not treated as audio languages. There is no
inference from "4K", file extensions, absent seed counts or release popularity.

## Torznab and Prowlarr

Configure a complete per-indexer Torznab endpoint, for example
`http://localhost:9696/1/api` or
`http://localhost:9696/api/v1/indexer/1/newznab`, with its API key separately.
Use torrent indexers. The CLI configures one provider; library composition can
register several endpoints with distinct names. There is no Prowlarr REST
indexer auto-discovery or assumption that its web root is a Torznab endpoint.

Capabilities policy:

- Query `t=caps` before the first search and retain successful caps in memory for
  the instance lifetime. Concurrent initialization is protected by a lock.
- Respect `available` and `supportedParams`. Missing parameters are unsupported.
- Prefer `t=movie` or `t=tvsearch` with numeric `imdbid` only when advertised.
- For TV IMDb queries, both `season` and `ep` must be advertised.
- If an explicit title and `q` are available, use text in the specific mode.
  Send season/ep only when both are supported; otherwise append SxxExx to `q`.
- An advertised generic `search&q=...` is the last title-based fallback.
- If no safe query is possible, report `unsupported_search`. There is no IMDb to
  title/TVDB lookup and no blind retry when caps discovery fails.
- Library users may inject explicit `TorznabCapabilities` when an endpoint's caps
  implementation is incompatible. The CLI does not silently override caps.

Only the default/first results page is fetched. No retries, result pagination,
HTML scraping or torrent downloads occur. Configuration uses separate connect
and socket read deadlines; each HTTP request also has a total deadline equal to
connect + read timeout. The registry's overall deadline includes caps and search.

RSS parsing handles namespaced Torznab attributes, repeated language attributes,
magnet links, torrent enclosures and optional attributes. Size attributes take
precedence over enclosure lengths. Missing/invalid optional metadata remains
unknown. Missing required title/identity or contradictory hashes/file selection
fail the provider response as `invalid_item`; records are not silently discarded.

## Identity and deduplication

Canonical identities:

- v1: lowercase 40-character hex, decoded from valid hex/base32/URN/magnet forms.
- v2: hex SHA-256 multihash (`1220` plus 64 hex digits), including `urn:btmh:`.
- hybrid magnets: prefer the v1 identity. There is no cross-hash alias database;
  a v2-only result cannot be linked to a v1-only result without torrent metadata.
- known torrent file: canonical hash plus explicit file index (including zero).
- unknown file: hash only. Keep it distinct from known file identities, even if
  only one selected file is present; no selection is guessed from result count.
- magnet BEP 53 `so=<single integer>` preserves that known selection. Ranges or
  multiple selections do not identify one file. Explicit contradictory indices
  are rejected.
- hashless results: HTTP(S) URL identity, normalizing scheme/host and removing
  fragments while retaining signed query order/encoding and userinfo case.

Provider names do not participate in identity. Within one identity, choose a
representative deterministically by cache state, seeders, metadata completeness,
provider/title and full metadata. Fill absent fields from remaining duplicates,
union languages, take maximum known seeders and OR cache flags. Conflicting
nonempty metadata uses the representative. Known files never collapse across
indices. Keeping unknown and known selections distinct also prevents a whole
pack's size being assigned to one selected file.

## Ranking semantics

Filtering happens before sorting. A known size above the maximum is ineligible,
including cached results. A known seed count below the minimum is ineligible
unless cached. Unknown size or seeders remain eligible.

The descending lexicographic sort key is:

1. cached availability;
2. position in resolution preferences (default 2160p, 1080p, 720p, 480p);
3. best matching preferred language;
4. known seed count (unknown sorts like zero);
5. canonical identity, provider, and complete metadata representation.

This preserves the existing deterministic model, adds validated/normalized
preferences, and makes ties independent of provider completion/input order.
It is not an additive weighted score. No I/O or candidate mutation is allowed.
Torznab results default to uncached; checking actual local availability is future
resolver/cache work.

## Failure isolation and credentials

Concurrency is bounded per registry search. Timeouts start when a provider gets
a semaphore slot; waiting in the queue does not consume its active budget. For
N providers and concurrency C, providers can consume roughly ceil(N/C) timeout
windows overall. Providers must cooperate with asyncio cancellation and avoid
blocking the event loop. Caller cancellation propagates to active/queued work.

Failure codes include `timeout`, `connection_error`, `http_error`,
`torznab_error`, `invalid_xml`, `invalid_capabilities`, `invalid_response`,
`invalid_item`, `response_too_large`, `unsupported_search` and `provider_error`.
A healthy provider's candidates survive failures elsewhere. The isolation
boundary catches unexpected exceptions and reports a fixed sanitized message;
provider-specific errors must already carry safe messages.

`defusedxml` rejects DTDs, entities and external references. Both streaming HTTP
reads and parser entry points enforce a 2 MiB response cap. HTTP redirects are
rejected. Remote error descriptions, response bodies and raw transport exceptions
are never logged or copied into failures. API keys are excluded from settings
repr. Library candidate URLs may contain credentials and must be treated as
private; the CLI omits them and redacts hashless URL identities.

## Future layers, outside this phase

Native Stremio HTTP routes will call the existing application service. Torrent
metadata and exact movie/episode file matching will precede qBittorrent session
management and byte-range streaming. SQLite will later persist cache, files and
sessions without becoming a dependency of pure functions. A new Kodi adapter
will translate requests into the same application service; it will not import
or migrate the v1 runtime into this branch. JDownloader/direct-download support
is optional later work.

## Protocol references

- [Torznab capabilities, parameters and attributes](https://torznab.github.io/spec-1.3-draft/torznab/Specification-v1.3.html)
- [Prowlarr API and per-indexer Newznab endpoints](https://prowlarr.com/docs/api/)
