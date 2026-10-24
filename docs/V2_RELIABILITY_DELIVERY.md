# Phase 2.5 discovery reliability delivery

## Branch and scope

Continues accepted Phase 2 commit b39f5be on v2-development. main and v1-kodi remain
at 9bcabbbb331dfeb10557dde19ce47e978a8840bc. The existing v2 worktree isolates this
work from the original main checkout and its uncommitted user edits. Draft PR #2
remains open and unmerged. No new PR/repository or Phase 3 work is introduced.

## Implemented architecture

- application/metadata.py: application-facing MetadataResolver protocol and
  immutable canonical metadata/result objects. No Stremio handler or Torznab coupling.
- metadata/cinemeta.py: opt-in fixed-host meta API adapter with strict IMDb and
  response identity/type/title validation, optional release year, 1 MiB JSON bound,
  connection/read/total deadlines, no redirects, safe errors, positive/negative
  cache and at most eight active distinct metadata lookups. No catalog requests.
- providers/base.py and torznab.py: optional TitleAwareProvider capability contract;
  reuse the query builder to negotiate whether a missing title is necessary.
  Continue IMDb-first, use q/year/season/ep only as advertised, otherwise episode q.
- providers/registry.py: optional preparation callback inside the provider budget;
  shared concurrency semaphore across requests, failure/cancellation isolation.
- application/search.py: lazy shared metadata hydration, healthy IMDb independence,
  validation before deduplication/ranking, safe exclusion counts and discovery cache.
- domain/validation.py: explicit IMDb/type/year contradictions, conservative
  title-prefix evidence with punctuation/accents/article/group normalization.
  Unknown evidence/numeric titles stay permissive. Pack/index rules are preserved.
- application/cache.py: TTL/LRU with entry/estimated-byte/in-flight limits, identical
  request sharing, cancellation-safe waiters and cleanup. Failure-bearing discovery
  results are not retained. No candidate cached/file-availability flags change.
- infrastructure/reliability.py and config.py: frozen settings, 1–16 named JSON
  endpoints, environment key references, 64 KiB read bound, duplicate/shape checks,
  legacy TORZNAB_URL/API_KEY fallback. File configuration replaces the single setup.
- infrastructure/bootstrap.py: compose independent providers, optional metadata,
  bounded caches and a private configuration fingerprint for cache isolation.
- infrastructure/doctor.py and CLI: local-only diagnostics for caps, endpoint
  response, search errors, metadata status and application/compatibility exclusions.
  No raw endpoints, hashes, locators, unknown capability strings or credentials.
- Existing Stremio routes remain thin over SearchService and keep streams-only
  protocol responses. All configured API keys feed presentation redaction.

Package version is 2.0.0.dev2; manifest SemVer is 2.0.0-dev.2. No runtime dependency
was added: aiohttp and defusedxml remain the only declared direct runtime packages.
JSON fixtures/provider example are included in the source distribution.

## Defaults and operational semantics

CINEMETA_ENABLED=false by default. Enabling it sends requested IMDb IDs and media
types to v3-cinemeta.strem.io when title metadata is needed; doctor explicitly tests
metadata when enabled. It sends no provider keys or discovered candidate lists.
No metadata lookup occurs just because an IMDb search returned no matches.

Metadata caches: positive 3600s, negative 30s, 256 entries and estimated 512 KiB.
Discovery caches: successful results 30s, 128 entries, estimated 8 MiB, 32 distinct
in-flight keys. TTL zero disables retention but still shares active work. Limits
are configurable within documented caps. Byte estimates are not exact heap sizes.

A cache key includes media coordinates, explicit title/year, ranking, provider
names and a private SHA-256 configuration fingerprint covering endpoints,
credentials and reliability settings. Restarting after config changes creates new
resources. Genuine successful empty/fully filtered results can be cached briefly;
provider failures or metadata errors cannot become retained successful snapshots.
Last-waiter cancellation cancels unneeded work; cancelling one client does not
corrupt another client's active lookup. A full budget returns a safe busy code.
There is no persistent cache, file readiness claim, debrid status or prefetch.

## Example provider requests

After each independently configured endpoint advertises its capabilities:

```text
# IMDb-capable movie indexer; no Cinemeta lookup needed
http://prowlarr:9696/1/api?t=movie&imdbid=1234567&apikey=REDACTED

# Title-only movie indexer, using validated Example Movie / 2024 metadata
# year is included ONLY if advertised
http://prowlarr:9696/2/api?t=movie&q=Example+Movie&year=2024&apikey=REDACTED

# IMDb-capable TV indexer
http://prowlarr:9696/1/api?t=tvsearch&imdbid=1234567&season=2&ep=5&apikey=REDACTED

# Title-only TV indexer with advertised coordinates
http://prowlarr:9696/2/api?t=tvsearch&q=Example+Show&season=2&ep=5&apikey=REDACTED

# Advertised text TV search without coordinate support
http://prowlarr:9696/2/api?t=tvsearch&q=Example+Show+S02E05&apikey=REDACTED
```

Optional metadata lookup URLs contain no provider credentials:

```text
https://v3-cinemeta.strem.io/meta/movie/tt1234567.json
https://v3-cinemeta.strem.io/meta/series/tt1234567.json
```

An explicit CLI --title bypasses metadata lookup. Unknown capabilities are never
guessed. A supported generic search&q may be used when the specific mode cannot
query. Metadata outages/disabled lookups leave IMDb-capable providers usable.

## Validation results

| Local Python | Passed | Failures | Errors | Skipped |
| --- | ---: | ---: | ---: | ---: |
| 3.10.22 | 192 | 0 | 0 | 0 |
| 3.11.17 | 192 | 0 | 0 | 0 |
| 3.12.15 | 192 | 0 | 0 | 0 |

192 distinct tests, 576 successful executions across Python 3.10–3.12. External
connections and DNS were blocked; loopback was allowed for aiohttp HTTP utilities.
The standard test command is python -m unittest discover -s tests -v.

Coverage includes positive/negative Cinemeta lookup, payload identity/schema/size,
metadata timeout/error isolation, capability-aware title fallback, multiple
providers, no results after validation, year/numeric-title behavior, TTL/LRU/byte
bounds, in-flight budgets, single/multiple waiter cancellation, failure snapshot
exclusion, secret redaction, doctor and full Stremio HTTP regression/composition.
Python 3.10 built-in versus asyncio timeout types are both handled safely.

Additional checks passed:

- Wheel and source-distribution builds.
- Every package module imported from installed wheels outside the source tree
  on Python 3.10/3.11/3.12; both CLI entry points and search/serve/doctor help checks.
- Source-distribution extraction includes 13 XML fixtures, three JSON fixtures and
  providers.example.json; its full 192-test suite passes on Python 3.12.
- Installed doctor, search and serve exercised against two loopback fixture
  indexers, including real JSON/CORS stream responses, graceful shutdown and
  credential/access-log checks. No real remote indexers or playback were contacted.
- Syntax/undefined-name, whitespace and AST architectural dependency checks.
- Original checkout status and protected branch refs checked unchanged.

GitHub Actions retains Python 3.10/3.11/3.12 and adds Cinemeta imports/doctor help
to package checks. No live external services are required by any unit test.

## Operator smoke test and limitations

Follow [STREMIO_SMOKE_TEST.md](STREMIO_SMOKE_TEST.md) with real public/legal content.
Configure one IMDb-capable and one title-only Prowlarr indexer in providers.json,
export the referenced keys privately, opt into Cinemeta if desired, then run:

```sh
self-debrid doctor movie tt1254207
self-debrid doctor series YOUR_REAL_SERIES_ID:2:5
self-debrid serve --host 127.0.0.1 --port 8080
```

Install http://127.0.0.1:8080/manifest.json in Stremio Desktop. Test an available
movie and episode, repeat within the TTL, check actual episode selection, and
observe controlled provider/metadata failures without losing healthy results.
For LAN clients, use an explicitly bound LAN host and trusted HTTPS reverse proxy
where required. Never put provider API keys in the addon installation URL.
Doctor exit zero means diagnostics completed; inspect status/errors/counts.

Real Prowlarr/Cinemeta/Stremio playback has not been validated by this implementation
run. Offline fixtures and a loopback fixture process do not establish live playback
reliability. An operator must execute and record the linked smoke procedure.

Known limitations:

- Cinemeta is an optional external dependency when enabled. Negative lookups can
  delay recovery for the short negative TTL. No aliases/TVDB translation or retry
  after empty IMDb search is implemented.
- Title aliases without a matching upstream IMDb ID may be filtered. Unknown
  evidence and numeric titles are deliberately permissive; returned candidates
  are still not proof of correct or playable torrent content.
- First/default Torznab page only, no retries; capabilities last for one process
  instance. Restart after changes; no hot reload or SQLite persistence.
- Metadata series documents above 1 MiB are rejected rather than parsed without
  bounds. Cache bytes are estimated serialized payload, not exact heap accounting.
- Native Stremio output still excludes v2-only hashes, hashless download locators
  and incompatible season packs. No authenticated tracker sources are forwarded.
- Existing standalone episode largest-file fallback remains explicitly documented;
  only real torrent metadata/file inspection can verify the actual selected file.
- No qBittorrent, local video streaming, downloader, SQLite, Torrentio, JDownloader,
  Docker, Kodi adapter, configure UI, user accounts or automatic tracker scraping.

Implementation stops after discovery reliability for Lead Developer review.

## Changed files

No files were removed in this phase. Existing user files outside the v2 worktree
were not staged or modified. The complete inventory follows.

| Change | File |
| --- | --- |
| Modified | `.env.example` |
| Modified | `.github/workflows/tests.yml` |
| Modified | `.gitignore` |
| Modified | `AGENTS.md` |
| Modified | `MANIFEST.in` |
| Modified | `README.md` |
| Modified | `V2.md` |
| Modified | `docs/STREMIO_SMOKE_TEST.md` |
| Modified | `docs/V2_ARCHITECTURE.md` |
| Modified | `docs/V2_ROADMAP.md` |
| Modified | `docs/V2_STREMIO_DELIVERY.md` |
| Added | `providers.example.json` |
| Modified | `pyproject.toml` |
| Modified | `self_debrid/__main__.py` |
| Modified | `self_debrid/adapters/stremio/__init__.py` |
| Modified | `self_debrid/adapters/stremio/streams.py` |
| Added | `self_debrid/application/cache.py` |
| Added | `self_debrid/application/metadata.py` |
| Modified | `self_debrid/application/search.py` |
| Modified | `self_debrid/domain/models.py` |
| Added | `self_debrid/domain/validation.py` |
| Modified | `self_debrid/infrastructure/bootstrap.py` |
| Modified | `self_debrid/infrastructure/config.py` |
| Added | `self_debrid/infrastructure/doctor.py` |
| Added | `self_debrid/infrastructure/reliability.py` |
| Added | `self_debrid/metadata/cinemeta.py` |
| Modified | `self_debrid/metadata/release_parser.py` |
| Modified | `self_debrid/providers/base.py` |
| Modified | `self_debrid/providers/registry.py` |
| Modified | `self_debrid/providers/torznab.py` |
| Added | `tests/fixtures/caps_imdb.xml` |
| Added | `tests/fixtures/caps_title.xml` |
| Added | `tests/fixtures/cinemeta_invalid.json` |
| Added | `tests/fixtures/cinemeta_movie.json` |
| Added | `tests/fixtures/cinemeta_series.json` |
| Added | `tests/fixtures/reliability_results.xml` |
| Modified | `tests/test_config_cli.py` |
| Modified | `tests/test_discovery.py` |
| Added | `tests/test_metadata.py` |
| Added | `tests/test_multi_provider_doctor.py` |
| Added | `tests/test_reliability.py` |
| Added | `tests/test_search_cache.py` |
| Modified | `tests/test_stremio_http.py` |
| Added | `docs/V2_RELIABILITY_DELIVERY.md` |
