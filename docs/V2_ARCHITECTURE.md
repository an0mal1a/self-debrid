# Self-Debrid v2 architecture

## Design goals

Self-Debrid should have one media pipeline and multiple protocol adapters. Discovery, ranking, torrent selection, downloads, cache state and streaming must not depend on whether the caller is Stremio, Kodi or a future HTTP client.

## Layers

### 1. Client adapters

Client adapters convert an external protocol into a MediaRequest and convert core results back into the client protocol.

Initial adapters:

- Stremio
- Kodi / legacy AllDebrid compatibility
- internal HTTP API later if useful

Adapters do not search providers directly and do not talk to qBittorrent.

### 2. Application services

Application services coordinate use cases such as:

- search streams for media;
- prepare a selected candidate for playback;
- inspect download state;
- release or evict cached content.

This layer will join ProviderRegistry, ranking, torrent selection, cache and streaming.

### 3. Domain

The domain contains protocol-independent data:

- MediaRequest
- StreamCandidate
- ProviderFailure
- DiscoveryResult
- RankingPreferences

The domain must be usable in tests without Flask or network services.

### 4. Providers

A provider converts a MediaRequest into zero or more normalized StreamCandidate objects.

Candidate provider types include:

- Torznab / Prowlarr
- Jackett
- compatible upstream aggregators
- Zilean
- Bitmagnet
- future custom plugins

Provider failures are isolated. A timeout or parsing failure in one provider must not discard useful results from another.

### 5. Resolver and download engine

A resolver turns a selected candidate into a playable local stream session.

Torrent work includes:

- obtain metadata safely;
- identify the exact video file;
- match movie title/year or series season/episode;
- set file priorities;
- optimize download order for playback;
- track available byte ranges;
- expose stable stream identifiers.

Direct-download work remains an optional resolver path and can continue to use JDownloader while it is useful.

### 6. Persistence

The first implementation should use SQLite.

Expected records include:

- media lookup/cache keys;
- normalized candidates;
- torrent metadata and files;
- active playback/download sessions;
- cached local files;
- provider health/last error metadata.

The database should describe state, not become a hard dependency for pure ranking and parsing code.

### 7. Streaming

The HTTP streaming layer must support:

- GET and HEAD;
- correct MIME types;
- byte ranges;
- stable content length when known;
- clean handling of bytes that are not downloaded yet;
- cancellation/disconnect behavior;
- no hard-coded localhost URLs in client responses.

## Identity and deduplication

For torrents, the canonical candidate identity is:

    normalized info hash + file index

Before file metadata is available, the normalized info hash alone is the best available torrent identity.

For direct URLs, a normalized URL can be used as a fallback identity.

Provider names are not part of identity. The same torrent found by multiple providers should collapse into one candidate while preserving the best useful metadata.

## Stremio contract

The first adapter targets movie and series stream requests using IMDb IDs.

Movie request:

    /stream/movie/tt1234567.json

Series request:

    /stream/series/tt1234567:2:5.json

Self-Debrid does not need to provide catalogs or metadata for this first integration. The manifest can advertise only the stream resource for movie/series IDs prefixed with tt.

## Ranking

Ranking must be deterministic, transparent and user-configurable.

Initial signals:

- local/cached availability;
- preferred resolution;
- preferred language;
- seed count;
- reasonable file size;
- stable tie breakers.

Ranking must not mutate candidates and must not perform network I/O.

## Migration strategy

Legacy code remains operational while v2 is introduced beside it.

Phase 1 creates the independent core.

Phase 2 exposes Stremio through the new core.

Phase 3 moves torrent selection and streaming into v2.

Phase 4 makes the existing AllDebrid/Kodi adapter call the same application services.

Only after parity should legacy download/streaming paths be removed.
