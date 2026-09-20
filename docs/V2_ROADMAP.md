# Self-Debrid v2 roadmap

## Phase 0 - Foundation

Status: in progress.

- v2-development branch
- architecture and engineering rules
- MediaRequest and StreamCandidate domain models
- provider protocol and registry
- deterministic ranking
- Stremio ID parsing and manifest helper
- unit tests

Exit condition: the core contracts are stable enough that provider and client work can proceed independently.

## Phase 1 - Discovery vertical slice

- SearchService orchestration
- provider timeouts and health reporting
- first real provider: Torznab/Prowlarr
- candidate deduplication across providers
- configuration model for quality, language and size
- fixture-based provider tests

Exit condition: a movie or episode request returns a reliable ranked candidate list without downloading anything.

## Phase 2 - Native Stremio MVP

- HTTP route for manifest.json
- stream/movie endpoint
- stream/series endpoint
- readable stream names/descriptions
- configure page with basic preferences
- local-network/public base URL handling
- install-in-Stremio flow

Exit condition: Stremio can request a title and display ranked Self-Debrid results.

## Phase 3 - Torrent playback engine

- torrent metadata fetch
- exact movie file matching
- exact SxxExx matching for series and season packs
- qBittorrent file priority management
- playback-oriented piece/file strategy
- stable playback sessions
- correct MIME, HEAD and Range support
- seek behavior while downloading

Exit condition: selecting a Stremio result starts the correct file consistently and supports normal playback/seeking.

## Phase 4 - Persistent cache

- SQLite schema and migrations
- candidate cache
- torrent metadata cache
- file/download session records
- true cache identity
- LRU/size-aware eviction
- startup recovery

Exit condition: restarts do not lose useful state and repeated playback can reuse existing files instantly.

## Phase 5 - Kodi convergence

- adapter around the v2 application service
- remove localhost assumptions
- preserve pairing flow
- compatibility tests for expected AllDebrid endpoints
- deprecate duplicated legacy download logic

Exit condition: Kodi and Stremio use the same discovery/resolver/cache/streaming core.

## Later

- additional providers
- optional direct-download providers/resolvers
- metrics and diagnostics
- Docker-first install
- provider plugin loading
- optional prefetch/warm-cache policies

## Explicitly deferred

Do not spend early development time on a large collection of brittle site-specific HTML scrapers. Prefer Torznab/Prowlarr and stable provider APIs first.
