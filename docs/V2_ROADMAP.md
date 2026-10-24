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

Accepted as an architectural foundation at b39f5be. Phase 2.5 improves discovery
before any local playback work; real playback validation remains outstanding.

## Phase 2.5 — Discovery reliability

Status: implemented and validated offline; operator Prowlarr/Stremio validation
and Lead Developer review remain required before playback reliability claims.

- Optional application-facing MetadataResolver and Cinemeta metadata adapter.
- Lazy title/year hydration for text-only indexers, positive/negative TTL cache,
  identity/schema validation, explicit deadlines and response-size bounds.
- IMDb-first capability policy without unnecessary metadata calls or blind retries.
- Multiple immutable named endpoints from JSON/environment references, at most 16,
  retaining the legacy single-indexer environment configuration.
- Shared bounded discovery TTL/LRU and in-flight coalescing with cancellation safety;
  no failure-bearing snapshot retention and no downloaded-file cache status.
- Conservative identity/title/year/type validation before deduplication and ranking.
- Local doctor capability, failure, filter and native-compatibility diagnostics.
- Offline metadata/cache/mixed-provider/CLI/HTTP regression tests.

Accepted at 8c8f1ce. The Lead Developer authorized Phase 3A below; live discovery
and Stremio smoke validation remain outstanding.

## Phase 3A — Torrent engine foundation

Status: implemented and validated offline; awaiting Lead Developer review and a
real qBittorrent smoke test. No managed playback or streaming endpoint is claimed.

- Protocol-independent torrent client and workspace interfaces.
- qBittorrent 4.6/5.x login, bounded metainfo submission/metadata polling, actual
  file indices, download state, priorities and guarded download preferences.
- Exact pure movie/episode file selection, season packs and ambiguity errors.
- Bounded volatile sessions, opaque identifiers, safe paths, separate provider
  hints and verified indices, progress and conservative local availability.
- Owned-new-torrent policy, per-mutation ownership checks and non-deleting cleanup.
- Explicit origin authorization for bounded, hash-verified torrent downloads.
- Offline client/selection/lifecycle/security tests and unchanged Stremio behavior.

Stop after Phase 3A for Lead Developer review. No Phase 3B work is authorized.
See [backend design and smoke procedure](TORRENT_ENGINE.md).

## Phase 3B — Managed playback HTTP delivery (not implemented)

- Stable stream identifiers, MIME types, HEAD and HTTP Range support.
- Seek behavior and cancellation while downloading.
- Verified contiguous piece availability and safe file opening for media delivery.
- Future client integration of managed playback, separate from native torrent streams.

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

Additional stable API providers, optional direct-download resolvers, richer diagnostics,
Docker packaging, plugin loading and prefetch policies. Torrentio, JDownloader,
HTML scraper collections and heavyweight infrastructure are outside this phase.
