# Standalone v2 discovery delivery

Historical report for accepted commit 83d44df. Phase 2 subsequently changes item
recovery, locator recognition and episode safety and adds native Stremio HTTP.
Use the current README/architecture and STREMIO_SMOKE_TEST.md for current behavior.

## Scope and branch safety

Implemented only on `v2-development` in a separate worktree because the original
checkout was on `main` with user edits. Those edits remain untouched. Both stable
remote branches (`main` and `v1-kodi`) remain at
`9bcabbbb331dfeb10557dde19ce47e978a8840bc`. LICENSE is unchanged.
Draft PR #2 remains open and unmerged. No new repository or PR was created.

## Working capabilities

An installable Python package and CLI now accept movie/episode IMDb requests,
query capability-aware async Torznab providers, normalize and deduplicate
candidates, apply pure deterministic ranking and return structured failures.
Prowlarr uses per-indexer Torznab endpoints. Connection/read/overall timeouts,
bounded concurrency, caller cancellation, XML safety and response limits are
implemented. Release metadata is deterministic and unknown values stay unknown.
The Stremio helpers parse IDs/build a manifest; there is no HTTP addon or playback.

## Architecture tree

```text
.env.example
.github/workflows/tests.yml
.gitignore
AGENTS.md
LICENSE
MANIFEST.in
README.md
V2.md
pyproject.toml
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
        stremio/__init__.py
    infrastructure/
        __init__.py
        config.py
tests/
    test_v2_core.py
    test_domain.py
    test_discovery.py
    test_torznab.py
    test_config_cli.py
    fixtures/
        caps.xml
        caps_text.xml
        results.xml
        missing_attributes.xml
        error.xml
        malformed.xml
        unsafe.xml
docs/
    V2_ARCHITECTURE.md
    V2_ROADMAP.md
    V2_DISCOVERY_DELIVERY.md
```

## Example queries

After `t=caps`, when the relevant parameters are advertised:

```text
http://localhost:9696/1/api?t=movie&imdbid=1234567&apikey=REDACTED
http://localhost:9696/1/api?t=tvsearch&imdbid=1234567&season=2&ep=5&apikey=REDACTED
```

Without IMDb support, an explicitly supplied title can use advertised `q`.
No title/identifier translation service or guessed capabilities are used. See
[README](../README.md) for text-query examples and CLI/environment configuration.

## Dependencies

Removed legacy requirements: Flask, qbittorrent-api, myjdapi and python-dotenv.
Runtime dependencies: `aiohttp>=3.10,<4` and `defusedxml>=0.7.1,<1`.
Build backend: `setuptools>=77`. The optional `build` tool produces wheel/sdist.
Tests use standard-library unittest and mocks, with no additional test dependency.
`MANIFEST.in` includes synthetic XML fixtures and documentation in the sdist.

## Validation results

The full suite was executed with outbound socket connections patched to fail:

| Python | Tests passed | Failures | Errors | Skipped | Network |
| --- | ---: | ---: | ---: | ---: | --- |
| 3.10.22 | 87 | 0 | 0 | 0 | blocked |
| 3.11.17 | 87 | 0 | 0 | 0 | blocked |
| 3.12.15 | 87 | 0 | 0 | 0 | blocked |

Total: **261 successful test executions**, 87 distinct tests on each Python.
The standard command is `python -m unittest discover -s tests -v`.

Additional checks passed:

- Wheel and source-distribution builds.
- All package modules imported from the installed wheel outside the source tree
  on Python 3.10, 3.11 and 3.12.
- `python -m self_debrid --help` and `self-debrid --version` from those wheels.
- The extracted source distribution includes all seven XML fixtures and passes
  all 87 tests on Python 3.12.
- CLI movie/episode paths, JSON redaction and the real application pipeline with
  fixture-backed transport are covered by unit tests.
- AST audit found no imports from removed legacy packages; diff whitespace and
  Python syntax/undefined-name checks passed.

GitHub Actions is configured for the same three Python versions, with dependency
installation, unit tests, distribution builds and installed-wheel imports/CLI.
Live Prowlarr, Stremio and qBittorrent were not contacted.

## Limitations and decisions for Lead Developer review

- Only the first/default Torznab page is fetched; no retries or live integration
  verification. Capabilities stay in memory for one provider instance.
- The CLI configures one endpoint; the library registry accepts multiple named
  providers. Each endpoint must belong to a torrent indexer.
- Text fallback requires an explicit title and supported `q`; unsupported
  IMDb-only requests fail explicitly. No capability guessing or metadata lookup.
- Unknown file identity stays distinct from known file indices. This can retain
  torrent-level and file-level results for the same hash, avoiding invented file
  selection or incorrectly copying pack sizes onto files.
- Hybrid magnets prefer v1; separate v1-only and v2-only results cannot be linked
  without torrent metadata. No hash alias database is introduced.
- Missing size/seeders remain eligible under limits; cached candidates bypass
  minimum seeders, but not the maximum size.
- Conflicting duplicate metadata uses a deterministic representative. Missing
  fields, languages, seeders and cache flags merge within the same identity.
- Missing required item data or contradictory torrent identities fail that
  provider response; partial malformed feed items are not silently discarded.
- Providers must cooperate with asyncio cancellation and avoid blocking code.
  Concurrency bounds apply per search and queued work gets its own active budget.
- CLI output omits private download URLs; library results retain them.
- No downloading, file selection, Stremio server/playback/configure UI, HTTP Range,
  qBittorrent integration, SQLite, JDownloader, Torrentio, Docker or Kodi adapter.

Implementation stops here pending Lead Developer review of the discovery phase.

## File inventory

Moves preserve the existing domain, ranking and Stremio helper implementations
with validation/behavior improvements. Unchanged files are omitted from this list.

| Change | File |
| --- | --- |
| Modified | `.env.example` |
| Added | `.github/workflows/tests.yml` |
| Modified | `AGENTS.md` |
| Added | `MANIFEST.in` |
| Modified | `README.md` |
| Modified | `V2.md` |
| Removed | `api/__init__.py` |
| Removed | `api/routes.py` |
| Removed | `api/streaming.py` |
| Removed | `config/__init__.py` |
| Removed | `config/settings.py` |
| Removed | `core/__init__.py` |
| Removed | `core/download_manager.py` |
| Removed | `core/pin_auth.py` |
| Modified | `docs/V2_ARCHITECTURE.md` |
| Modified | `docs/V2_ROADMAP.md` |
| Removed | `main.py` |
| Added | `pyproject.toml` |
| Removed | `requirements.txt` |
| Added | `self_debrid/__main__.py` |
| Added | `self_debrid/adapters/__init__.py` |
| Moved/modified | `self_debrid/stremio.py` → `self_debrid/adapters/stremio/__init__.py` |
| Added | `self_debrid/application/__init__.py` |
| Added | `self_debrid/application/search.py` |
| Added | `self_debrid/domain/__init__.py` |
| Added | `self_debrid/domain/deduplication.py` |
| Added | `self_debrid/domain/identity.py` |
| Moved/modified | `self_debrid/domain.py` → `self_debrid/domain/models.py` |
| Moved/modified | `self_debrid/ranking.py` → `self_debrid/domain/ranking.py` |
| Added | `self_debrid/infrastructure/__init__.py` |
| Added | `self_debrid/infrastructure/config.py` |
| Added | `self_debrid/metadata/__init__.py` |
| Added | `self_debrid/metadata/release_parser.py` |
| Modified | `self_debrid/providers/base.py` |
| Modified | `self_debrid/providers/registry.py` |
| Added | `self_debrid/providers/torznab.py` |
| Removed | `services/__init__.py` |
| Removed | `services/jdownloader.py` |
| Removed | `services/qbittorrent.py` |
| Added | `tests/fixtures/caps.xml` |
| Added | `tests/fixtures/caps_text.xml` |
| Added | `tests/fixtures/error.xml` |
| Added | `tests/fixtures/malformed.xml` |
| Added | `tests/fixtures/missing_attributes.xml` |
| Added | `tests/fixtures/results.xml` |
| Added | `tests/fixtures/unsafe.xml` |
| Added | `tests/test_config_cli.py` |
| Added | `tests/test_discovery.py` |
| Added | `tests/test_domain.py` |
| Added | `tests/test_torznab.py` |
| Modified | `tests/test_v2_core.py` |
| Added | `docs/V2_DISCOVERY_DELIVERY.md` |
