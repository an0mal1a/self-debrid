# Self-Debrid v2 roadmap

## Phase 0 — Standalone foundation

Status: implemented and validated offline.

- Completely separate v2 tree; v1 remains in `main` and `v1-kodi`.
- Installable Python 3.10+ package, CLI and environment configuration.
- Immutable validated domain models and deterministic ranking.
- Provider protocol and registry.
- Stremio ID parsing and manifest helpers only.
- English documentation and Python 3.10/3.11/3.12 CI.

## Phase 1 — Discovery vertical slice

Status: implemented and validated with synthetic fixtures and mocked providers;
live Prowlarr validation remains an integration follow-up.

- SearchService aggregation, deduplication and preference ranking.
- Bounded concurrency, per-provider deadlines and structured failures.
- Async Torznab/Prowlarr provider with capability discovery and explicit fallback.
- Hardened XML parsing and connection/read deadlines.
- Explicit release metadata parsing and canonical torrent/file identities.
- Fixture-based offline unit tests, packaging and CLI validation.

Exit condition: a movie or episode request returns ranked discovery candidates
without downloading anything. Lead Developer review is required before starting
the next implementation phase. The Lead Developer accepted this foundation at
83d44df and authorized Phase 2. Draft PR #2 stays open and unmerged.

## Phase 2 — Native Stremio addon

Status: implemented and validated offline; awaiting real Prowlarr/Stremio smoke
validation and Lead Developer review. No local playback compatibility is claimed.

- Recoverable malformed RSS items with bounded safe diagnostics.
- Conservative torrent locator recognition; details pages are not download URLs.
- Episode evidence filtering before deduplication; safe pack/file handling.
- aiohttp manifest, movie/series stream and liveness routes, JSON/CORS responses.
- Pure native v1 torrent serialization, explicit fileIdx when usable, no URL or
  tracker passthrough; v2-only hashes are excluded.
- Loopback defaults, explicit LAN binding, optional advertised public base URL.
- `self-debrid serve`, existing discovery CLI, offline HTTP/pipeline tests.
- Installation guide and [real smoke procedure](STREMIO_SMOKE_TEST.md).

The handoff uses Stremio's native torrent engine. A standalone episode with no
index can use Stremio's largest-file fallback only under the documented naming
policy. Self-Debrid does not select, download or serve the file. Packs require a
known index and matching upstream episode mapping. Configure UI is deferred.

Stop here for Lead Developer review; do not start Phase 3.

## Phase 3 — Torrent playback engine (not implemented)

- Torrent metadata retrieval and exact movie/episode file selection.
- Correct handling of season packs; no largest-file shortcut.
- qBittorrent file priorities and playback-oriented download sessions.
- Stable stream identifiers, MIME types, HEAD and HTTP Range support.
- Seek behavior and cancellation while downloading.

## Phase 4 — Persistent cache (not implemented)

- SQLite schema/migrations and candidate/torrent/file records.
- Active session and cached-file state, restart recovery.
- Cache identity and size-aware eviction.

## Phase 5 — New Kodi adapter (not implemented)

- Protocol translation around the v2 application services.
- Pairing and compatibility contract design without importing v1.
- Compatibility tests and usable network URLs.

v1 remains independently maintained. There is no in-tree legacy migration path.

## Later

Additional stable API providers, optional direct-download resolvers, diagnostics,
Docker packaging, plugin loading and prefetch policies. Torrentio, JDownloader,
HTML scraper collections and heavyweight infrastructure are outside this phase.
