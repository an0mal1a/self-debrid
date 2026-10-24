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

Standalone packaging and the Torznab discovery slice are implemented. Domain,
ranking, metadata parsing and deduplication are pure; SearchService orchestrates
the provider registry and ranking. Only adapter helpers exist for Stremio.

Stop at the discovery phase until the Lead Developer authorizes integration.
Do not add Torrentio, downloading, qBittorrent playback, JDownloader, HTTP Range
streaming, Stremio HTTP/configure UI, SQLite, Docker or a Kodi adapter in this phase.

## Architecture invariants

1. Domain/application logic must not depend on web frameworks, Stremio, Kodi or
   download clients. Adapters translate protocols only.
2. Providers implement `Provider` and return immutable normalized `StreamCandidate`
   objects. Provider-specific transport/configuration stays outside the domain.
3. Hash identity is canonical hash plus known file index. Unknown selection
   remains torrent-level. Never infer a file index or use "largest file" selection.
4. Pure ranking/deduplication must be deterministic, including tie-breaking and
   conflicting metadata. Never mutate candidates or perform I/O there.
5. Missing metadata stays unknown. No broad language guesses from substrings.
6. The registry bounds concurrency and times out each active provider. Preserve
   caller cancellation and successful results when other providers fail.
7. Capabilities determine supported Torznab queries. No blind IMDb or season/ep
   assumptions; text fallback requires an explicit title and advertised `q`.
8. Network boundaries need explicit connection/read and overall time bounds.
   Bound XML responses and use hardened XML parsing. Do not scrape HTML.
9. Never log API keys, download URLs, raw network exceptions or remote XML error
   descriptions. `ProviderError` messages must be safe for user diagnostics.
10. Configuration is read explicitly at the application boundary, not on import.
11. Keep runtime dependencies minimal. SQLite comes later; no PostgreSQL, Redis,
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
