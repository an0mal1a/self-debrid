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
        episode_selection.py
        validation.py
        ranking.py
    application/
        __init__.py
        search.py
        metadata.py
        cache.py
    providers/
        __init__.py
        base.py
        registry.py
        torznab.py
    metadata/
        __init__.py
        release_parser.py
        cinemeta.py
    adapters/
        __init__.py
        stremio/
            __init__.py
            streams.py
            http.py
    infrastructure/
        __init__.py
        config.py
        bootstrap.py
        reliability.py
        doctor.py
```

`identity.py` and `deduplication.py` are separate pure modules because they are
shared rules, not Torznab transport behavior. The existing immutable models,
lexicographic ranking and Stremio parsing/manifest helpers have been preserved
and tightened in their new locations. Old experimental `self_debrid.ranking` and
`self_debrid.stremio` import paths are replaced by the package layout above.

## Dependency boundaries and data flow

1. The CLI explicitly reads environment configuration and validates a movie or
   episode request using the adapter's IMDb parser.
2. `SearchService` accepts `MediaRequest`, calls `ProviderRegistry`, filters series
   episode evidence before duplicate metadata can mask mismatches, deduplicates
   normalized candidates and applies pure ranking preferences.
3. `ProviderRegistry` executes `Provider.search` asynchronously with a semaphore
   and per-active-provider timeout. It aggregates candidates and structured
   `ProviderFailure(provider, error, code)` objects in registration order.
4. `TorznabProvider` obtains capabilities, constructs a supported query, fetches
   XML and returns `StreamCandidate` objects using the release parser.
5. The discovery CLI prints JSON metadata and failures. It excludes private
   download URLs; library results retain recognized locators for future resolvers.
6. The HTTP adapter invokes the same SearchService and a pure mapper, returning
   native Stremio hash streams. Composition lives in infrastructure/bootstrap.py;
   there is no provider querying or ranking implementation inside handlers.

The registry intentionally does not deduplicate or rank. Application search owns
those use-case steps. Domain, cache orchestration and release parsing use only the standard library.
The optional Cinemeta adapter uses aiohttp inside the metadata package.
Application code has no client-protocol, web framework or downloader imports.
It depends on the application-facing MetadataResolver contract, not Cinemeta.
Infrastructure reads environment variables only when called. Runtime external
dependencies are `aiohttp` and `defusedxml`; `setuptools` builds the package.

## Media requests and release metadata

Movie requests contain a `MediaType.MOVIE` and an ASCII IMDb title ID. Series
requests also require non-negative integer season and episode coordinates.
Specials (`season=0`) and episode zero remain valid, preserving the foundation
contract. Optional title/year fields support text searches; explicit titles avoid external
metadata lookup. The optional Cinemeta adapter can hydrate these fields lazily. A title is a search hint, not part of the media identity.

Recognized release labels are bounded tokens: 2160p/1080p/720p/480p;
H.264/x264/AVC, H.265/x265/HEVC, AV1; HDR/HDR10/Dolby Vision/DoVi/DV;
Spanish/SPA/ESP/Castellano/Español, English/ENG, Latino and MULTI; SxxExxx.
Languages map to `es`, `en`, `es-419` and `multi`. MULTI does not imply a list of
languages. Ambiguous short title tokens such as EN, ES and LAT are not languages.
Attributes provide optional size, seeders, season, episode, video, resolution and
language. Subtitle attributes are not treated as audio languages. Pack/range names do not
populate episode coordinates from their first label; only explicit upstream
attributes can supply that mapping for ambiguous releases. There is no
inference from "4K", file extensions, absent seed counts or release popularity.

## Torznab and Prowlarr

Configure a complete per-indexer Torznab endpoint, for example
`http://localhost:9696/1/api` or
`http://localhost:9696/api/v1/indexer/1/newznab`, with its API key separately.
Use torrent indexers. JSON provider configuration composes 1–16 endpoints with
unique names; the legacy single-endpoint environment setup remains supported. There is no Prowlarr REST
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
- If a title is missing and advertised text capabilities can execute the request,
  TitleAwareProvider.requires_title reports that need. The application may obtain
  title/year metadata through MetadataResolver, then use normal provider.search.
- If no safe query is possible, report unsupported_search. There is no blind
  retry after empty IMDb results or failed caps discovery, and no TVDB lookup.
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
skip only that item. The parser emits one sanitized warning per affected feed:
exact skipped count plus at most five 1-based item positions. No item titles,
provider names, URLs, raw exception text or remote messages enter this diagnostic.
Malformed/unsafe XML, authentication/HTTP errors, invalid capability documents and
non-RSS responses remain provider-level failures. An all-invalid feed returns
zero candidates plus the same bounded warning, not a fabricated provider failure.

Only magnet values in magnet attributes, typed torrent enclosures and recognized
download URL shapes are retained as candidate locators. Known shapes include
.torrent paths, download/get paths, Torznab t=get&id and Jackett /dl/ proxies.
These are protocol/route heuristics, not downloaded-content verification. Plain
RSS details links and untyped HTML enclosures are discarded; an item with a valid
hash remains useful without a URL. Magnet links take precedence so a single-file
BEP 53 selection survives an accompanying HTTP enclosure. No locator, including
a torrent endpoint, is treated as an HTTP video stream.

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

Concurrency is bounded across searches sharing one registry by a shared semaphore. Timeouts start when a provider gets
a semaphore slot; waiting in the queue does not consume its active budget. For
N providers and concurrency C, providers can consume roughly ceil(N/C) timeout
windows overall for an isolated request. Concurrent distinct requests also wait
for the shared registry slots; discovery in-flight bounds cap that queued work. Providers must cooperate with asyncio cancellation and avoid
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

## Phase 2 native Stremio adapter

`adapters/stremio/http.py` creates an aiohttp application with an injected
SearchService. It provides GET /manifest.json, /stream/movie/{id}.json,
/stream/series/{id}.json and /health. Creating the app has no network side effects.
The manifest ID stays com.selfdebrid.addon and uses SemVer 2.0.0-dev.2; Python
packaging uses the equivalent prerelease version 2.0.0.dev2.

The pure mapper in streams.py emits infoHash, optional fileIdx, name and
description. Current Stremio core encodes Torrent hashes as 20 bytes and fileIdx
as u16, so the adapter allows only 40 hex digits and indices 0–65535. V2-only
hashes remain in discovery but cannot be emitted as v1 streams; hybrid magnets
use their v1 identity. HTTP/magnet URLs, tracker sources, passkeys and provider
failure messages never appear as playable sources. Public metadata is bounded
and strips URL strings, common credential assignments and configured API-key
representations. No authenticated announce URLs are forwarded; private trackers
requiring them are outside this hash-only mode.

Series policy is pure in domain/episode_selection.py and enforced before
application deduplication, then checked again by the mapper:

- Reject contradictory season/episode attributes and clear single-episode names.
- A known file index requires a matching unambiguous release name or exact
  upstream season/episode mapping. A number by itself is not episode evidence.
- Packs/ranges/multiple episodes without a known usable index are excluded.
  A pack with an index also needs the explicit matching episode mapping.
- Without an index, only a single exact SxxExxx name without pack/complete/
  collection/range markers is eligible. Stremio chooses its largest file, and
  the description explicitly states this. Filename evidence cannot prove that
  the largest file is the requested episode. Samples, extras, multiple versions
  and misleading names remain limitations until torrent metadata inspection.
- Movie serialization excludes explicitly episodic candidates. Movies without
  file indices likewise use Stremio's largest-file behavior.

All resource responses include permissive CORS as required by the protocol,
with no-store caching. OPTIONS and HEAD are supported. Empty searches and provider
failures produce HTTP 200 with streams: []; other successful providers survive.
Invalid IDs receive fixed HTTP 400 JSON diagnostics without reflecting input.
Unexpected errors are sanitized to HTTP 500 and a fixed server log message.
Health checks process liveness only, not provider connectivity.

There is no blanket HTTP timeout that could discard already successful providers.
The existing registry bounds each active provider, including capabilities and
query I/O. Total search duration is bounded by a finite provider count and its
concurrency windows, assuming cooperative async providers. The CLI enables
handler cancellation on disconnect. Raw request access logs are disabled;
provider failures are logged only as a count at the HTTP boundary.

ServerSettings adds HTTP_HOST (127.0.0.1), HTTP_PORT (8080) and optional
PUBLIC_ADDON_URL. CLI host/port flags override environment settings. The public
URL affects the startup installation address only; no absolute source URLs are
created and proxy headers are not trusted to construct responses. Reverse
proxies must preserve routes/CORS and strip any external path prefix. TLS is
terminated externally when clients require HTTPS. No auth/configure UI or
public hosting is implemented. No runtime dependency was added to aiohttp and
defusedxml.

## Phase 2.5 metadata, strategies and cache boundaries

application/metadata.py defines immutable MediaMetadata (IMDb ID, canonical title,
media type, optional year), MetadataResult (metadata or a safe error code) and
MetadataResolver. It has no provider or HTTP handler dependency. CinemetaResolver
implements it over the documented meta resource at v3-cinemeta.strem.io. No catalog
is consumed and no metadata API key is required. CINEMETA_ENABLED is false by
default; opting in sends requested IMDb IDs and media types to Cinemeta.

The adapter validates ASCII IMDb IDs with 7–10 digits before constructing URLs,
checks meta object identity/type/name, bounds titles and response size (1 MiB),
and extracts releaseInfo start-year or a valid released ISO date. Unknown years
stay unknown. Redirects are rejected, connect/read/total deadlines are explicit,
and unavailable/invalid data returns safe codes without raw exception details.
Positive TTL defaults to 3600s, negative TTL to 30s; max 256 entries, estimated
512 KiB payload, and at most eight distinct active lookups. Metadata failures are
negative-cached to avoid repeated outage requests. Caller cancellation propagates
through shared work only when there are no other waiters.

ProviderRegistry.search accepts an optional application preparation callback.
Preparation, capability lookup, metadata wait and provider query share the existing
per-active-provider deadline. The registry's semaphore is shared across requests.
Torznab implements the optional TitleAwareProvider extension, negotiating from
advertised capabilities through its existing query builder. SearchService creates
at most one lazy metadata task per search. IMDb-capable providers never wait for it;
text-only providers share it, enrich a new immutable MediaRequest and query only
supported parameters. Known year is sent only on advertised movie year support.
TV coordinates remain explicit when supported, otherwise q includes SxxExx.
No blanket IMDb-zero-result retry is introduced.

Before deduplication, pure validation rejects conflicting echoed IMDb IDs,
episodic content for movies, incompatible episode evidence, and movie year
differences over one year. Title checks run only when canonical/explicit metadata
exists and the release has a clear title boundary before a year/quality/episode
label. Matching upstream IDs override title spelling differences. Punctuation,
accents, leading English articles and bracketed release groups are normalized;
numeric titles and missing boundaries remain permissive. There is no fuzzy-score
threshold or forced year guess. Aliases without IDs remain a known limitation.
Metadata already resolved for one indexer validates all providers in that search.
The current conservative pack/index policy is unchanged.

DiscoveryResult adds immutable exclusion counts and an optional metadata error
code for local diagnostics. It does not expose provider query URLs or change the
Stremio streams response shape. Invalid/failed metadata does not discard healthy
IMDb candidates. Failure-bearing results never enter the retained discovery cache.

application/cache.py provides bounded monotonic TTL/LRU storage and shared async
work. Default discovery limits: 128 entries, estimated 8 MiB serialized payload
(including private locators internally), 30s TTL and 32 distinct in-flight keys.
Capped values and entry counts bound retention; oversized values are not retained.
Memory estimates are not exact Python heap measurements. Configuration caps these
limits, including a maximum 300s discovery TTL. Zero TTL disables retention while
allowing active-work sharing. CacheBusy produces a structured busy discovery
failure instead of an unbounded distinct-task queue.

Keys contain the immutable request (media type, IMDb ID, episode coordinates,
explicit title/year), preferences, configured provider names and a SHA-256
fingerprint of the complete immutable composition. Endpoints and credentials
contribute internally but neither config secrets nor the fingerprint is printed.
Bootstrap owns separate caches per service; restarting or changing config creates
new resources. Genuine empty/fully filtered successful results may be cached;
provider or metadata failures are shared only while active, not retained as
successful snapshots. Cached discovery never sets StreamCandidate.cached or
claims downloaded bytes/file availability. HTTP Cache-Control stays no-store.

Each in-flight task has waiter accounting and is shielded from individual waiter
cancellation. The last waiter removes/cancels unneeded work and waits for cleanup;
exceptions/cancellations do not poison future retries. Metadata has independent
positive/negative caching. No prefetch, polling or detached background scrapers
are introduced.

## Multi-indexer configuration and local diagnostics

PROVIDERS_FILE references a private JSON object with only a providers list. Entries
contain name, url and optional api_key_env. Environment references are resolved at
startup into frozen TorznabSettings tuples. The read is capped at 64 KiB and the
list at 16 providers; names must be unique case-insensitively, identifiers bounded,
and unknown fields/inline API keys/undefined references fail with sanitized errors.
A configured file replaces the single endpoint; no file preserves TORZNAB_URL,
TORZNAB_API_KEY and TORZNAB_NAME compatibility. Existing timeout and ranking
settings apply to all configured providers. Providers remain independent failures
inside the existing registry. There is no dynamic plugin framework or reload loop.

self-debrid doctor movie|series ID is an intentional local network diagnostic:
bounded capability checks, per-provider test search, safe error codes, exclusion
counts and native Stremio compatibility counts. Cinemeta is explicitly checked
when enabled, even if no indexer needs a title. It lists provider names but no
endpoint URLs, hashes, titles, locators, credential-bearing remote descriptions or
unknown remote capability strings. Known capability parameter names are allowlisted.
Doctor does not use the retained discovery cache so failures are visible. There
is no HTTP diagnostic route; no public secret/config surface is introduced.
Doctor exit zero means diagnostics completed, not that playback is verified.

## Future layers, outside this phase

Exact torrent metadata and movie/episode file matching precede qBittorrent
sessions and local byte-range streaming in Phase 3. SQLite will later persist
cache, files and sessions without becoming a dependency of pure functions.
A new Kodi adapter will translate into the same application services without
importing the v1 runtime. Configure UI, extra providers and direct-download
integrations remain future work. Stop after Phase 2.5 for Lead Developer review.

## Protocol references

- [Torznab capabilities, parameters and attributes](https://torznab.github.io/spec-1.3-draft/torznab/Specification-v1.3.html)
- [Prowlarr API and per-indexer Newznab endpoints](https://prowlarr.com/docs/api/)

- [Stremio stream object and largest-file fallback](https://github.com/Stremio/stremio-addon-sdk/blob/master/docs/api/responses/stream.md)
- [Stremio addon HTTP protocol and CORS](https://stremio.github.io/stremio-addon-sdk/protocol.html)
- [Stremio core Torrent representation](https://github.com/Stremio/stremio-core/blob/development/src/types/resource/stream.rs)

- [Stremio metadata object and releaseInfo](https://github.com/Stremio/stremio-addon-sdk/blob/master/docs/api/responses/meta.md)
- [Official Cinemeta manifest](https://v3-cinemeta.strem.io/manifest.json)
