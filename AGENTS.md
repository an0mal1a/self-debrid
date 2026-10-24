# Self-Debrid v2 engineering instructions

## Source of truth and branch separation

- `main` is stable v1 Kodi; `v1-kodi` preserves v1 maintenance.
- `v2-development` contains a completely independent v2 application.
- Do not change `main` or `v1-kodi` during v2 work. Preserve local user changes;
  use a separate worktree if necessary.
- Do not restore legacy `api/`, `core/`, `services/`, `config/` or root `main.py`,
  or move them under `legacy/`. There is no shared runtime with v1.
- Keep Draft PR #2 open and unmerged. Do not create a new repository.

The product direction is a lightweight self-hosted discovery, download, cache
and streaming engine. Native Stremio is the next client integration; a new Kodi
adapter is future work. Neither client owns core discovery or download logic.

## Current scope

Standalone packaging, Torznab discovery and the Phase 2 native Stremio HTTP addon
are implemented. Domain/ranking/metadata/deduplication and episode evidence remain
pure. SearchService owns orchestration and episode filtering before deduplication.
The aiohttp adapter translates requests and serializes native torrent streams.

The Lead Developer accepted Phase 2 at b39f5be and authorized Phase 2.5 discovery
reliability: optional metadata resolution, multi-indexer config, bounded memory
caches, conservative validation and local doctor diagnostics. Stop after Phase 2.5. Do not begin Phase 3 or add Torrentio, torrent downloading, qBittorrent
playback, JDownloader, local HTTP Range streaming, configure UI, SQLite, Docker,
a frontend/custom player, or a Kodi adapter.

## Architecture invariants

1. Domain/application logic must not depend on web frameworks, Stremio, Kodi or
   download clients. Adapters translate protocols only.
2. Providers implement `Provider` and return immutable normalized `StreamCandidate`
   objects. Provider-specific transport/configuration stays outside the domain.
3. Hash identity is canonical hash plus known file index. Unknown selection
   remains torrent-level. Never infer a file index. The adapter may emit a single
   clearly named episode without an index, explicitly describing Stremio's
   largest-file fallback. This is not verified file selection or local playback.
4. Pure ranking/deduplication must be deterministic, including tie-breaking and
   conflicting metadata. Never mutate candidates or perform I/O there.
5. Missing metadata stays unknown. No broad language guesses from substrings.
6. The registry bounds concurrency and times out each active provider. Preserve
   caller cancellation and successful results when other providers fail.
7. MetadataResolver is an application contract. Cinemeta is an optional adapter,
   disabled by default; disclose requested IDs sent externally. Resolve lazily
   only when a provider requires supported title search, except explicit doctor
   checks. Metadata outages must preserve IMDb-capable results.
8. Capabilities determine supported Torznab queries. No blind IMDb or season/ep
   assumptions; text fallback requires an explicit title and advertised `q`.
9. Network boundaries need explicit connection/read and overall time bounds.
   Bound XML responses and use hardened XML parsing. Do not scrape HTML.
10. Never log API keys, download URLs, raw network exceptions or remote XML error
   descriptions. `ProviderError` messages must be safe for user diagnostics.
11. Configuration is read explicitly at the application boundary, not on import.
12. Bad feed items must not discard good items. Bound diagnostics to one summary
    per feed and at most five item positions, without remote titles/URLs/errors.
13. Torrent locators are not HTTP video URLs. Emit only supported v1 hash streams;
    do not serialize provider URLs, trackers, API keys or v2-only hashes.
14. Series candidates need matching episode evidence. Packs require both a known
    usable index and an exact upstream episode mapping; obvious mismatches fail.
15. Bind HTTP to loopback by default; LAN access is explicit. Preserve CORS and
    sanitized JSON errors and disable raw request access logs.
16. Keep runtime dependencies minimal. SQLite comes later; no PostgreSQL, Redis,
    Celery or dependency injection framework is needed.

## Code quality and validation

- Python 3.10+; four spaces; type public APIs; prefer dataclasses and small services.
- No broad silent exception handling; the registry's sanitized isolation boundary
  must report structured failures. External exceptions must not expose secrets.
- Meaningful behavior needs offline `unittest` tests with synthetic fixtures.
- Run `python -m unittest discover -s tests -v` after installing with
  `python -m pip install -e .`.
- Validate package imports, `python -m self_debrid --help`, `self-debrid --version`,
  and wheel/sdist builds when packaging changes.
- Before committing: review the diff, check for dangling legacy imports, ensure
  docs distinguish working features from plans, and verify protected branch refs
  remain unchanged. Commit and push only the authorized v2 branch.

## Phase 2.5 reliability invariants

- Use immutable configuration with at most 16 uniquely named endpoints. JSON
  references environment key names; credentials never belong in addon URLs.
- Discovery cache keys separate media coordinates/title/year and configuration.
  Bound entries, estimated bytes and in-flight work. Do not retain failure-bearing
  results; sharing active work must survive cancellation of individual waiters.
- Discovery caching never sets downloaded-file cache/availability flags.
- Metadata caches retain positive and negative lookups with bounded TTL and size.
- Reject explicit identity/year/media contradictions; keep unknown evidence
  unknown, preserve season-pack policy and avoid fuzzy matching thresholds.
- Doctor is local only. No endpoint URLs, hashes, locators, credentials or raw
  remote strings in diagnostic output. Capability output uses a known allowlist.
- All tests are offline; loopback HTTP is allowed. Do not claim real playback
  reliability until an operator performs the documented Prowlarr/Stremio test.
