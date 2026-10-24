# Self-Debrid v2

Self-Debrid v2 is a standalone Python foundation for a lightweight, self-hosted
media discovery, download, cache and streaming engine. Its core accepts movie or
episode requests, aggregates discovery providers, and returns normalized, ranked
release candidates with structured provider failures.

**Current implementation: discovery only.** There is no HTTP server, torrent
downloading or playback yet. Stremio playback does not work in this phase.

## v1 and v2 are separate applications

- [`main`](https://github.com/an0mal1a/self-debrid/tree/main): stable Kodi /
  AllDebrid-compatible v1 implementation.
- [`v1-kodi`](https://github.com/an0mal1a/self-debrid/tree/v1-kodi): preserved v1
  maintenance branch.
- `v2-development`: independent v2 package, with no v1 modules or runtime imports.

v1 resolves links discovered by a Kodi addon. v2 takes responsibility for source
discovery and will eventually own file selection, downloading, caching and
streaming. Native Stremio integration is the next direction; a new Kodi adapter
will follow later. v2 does not preserve the v1 API or its configuration.

## Implemented

- Validated movie/episode `MediaRequest` and immutable `StreamCandidate` models.
- Stremio IMDb ID parsing (`tt1234567` and `tt1234567:2:5`) and a manifest helper;
  these are protocol utilities, not an installable Stremio addon.
- Async provider registry with bounded concurrency, per-provider timeouts,
  cancellation propagation and structured failures.
- `SearchService`: provider aggregation, normalization, metadata-preserving
  deduplication, preference filtering and deterministic ranking.
- Async Torznab discovery, including per-indexer Prowlarr Torznab endpoints.
- Capability discovery and conservative title-based fallback.
- Safe XML parsing, bounded response sizes, explicit connection/read timeouts.
- Explicit release labels for resolution, codecs, HDR, languages and episodes;
  optional size and seed metadata from the feed.
- CLI, environment configuration and offline `unittest` coverage.

Planned: native Stremio HTTP integration, exact torrent file selection,
qBittorrent playback sessions and HTTP Range streaming, SQLite persistence, then
Kodi support. Additional discovery providers can implement the existing
`Provider` protocol; only Torznab is implemented. Torrentio, JDownloader, Docker
and client configuration UI are outside this phase.

## Development installation

Use Python **3.10+**. CI covers Python 3.10, 3.11 and 3.12.

```sh
git clone --branch v2-development https://github.com/an0mal1a/self-debrid.git
cd self-debrid
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m self_debrid --help
self-debrid --version
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell.

Runtime dependencies are `aiohttp>=3.10,<4` (cancellable async HTTP) and
`defusedxml>=0.7.1,<1` (XML safety). Dependencies are declared in `pyproject.toml`.
`unittest` is included in Python. There is no web framework or database dependency.

## Configuration and CLI

Copy `.env.example` to `.env` and enter the API key locally. The application reads
**exported environment variables**, not `.env` files automatically. For a trusted
local `.env` file in a POSIX shell:

```sh
cp .env.example .env
# Edit .env, then export its values:
set -a
. ./.env
set +a
self-debrid search movie tt1234567 --title 'Example Movie'
self-debrid search series tt1234567:2:5 --title 'Example Show'
```

In PowerShell, set variables with `$env:TORZNAB_URL = 'http://localhost:9696/1/api'`
and `$env:TORZNAB_API_KEY = 'your-local-key'` before running the same commands.
Never commit your API key or a URL containing one.

| Variable | Default | Meaning |
| --- | --- | --- |
| `TORZNAB_URL` | required | Complete per-indexer Torznab endpoint, without query parameters |
| `TORZNAB_API_KEY` | empty | API key, sent as an encoded Torznab query parameter |
| `TORZNAB_NAME` | `prowlarr` | Stable provider identifier |
| `CONNECT_TIMEOUT` | `5` | HTTP connection timeout, seconds |
| `READ_TIMEOUT` | `10` | HTTP socket read timeout, seconds |
| `PROVIDER_TIMEOUT` | `20` | Entire active provider search, including capabilities, seconds |
| `MAX_CONCURRENCY` | `4` | Maximum active providers per registry search |
| `RESOLUTION_ORDER` | `2160p,1080p,720p,480p` | Most preferred first |
| `PREFERRED_LANGUAGES` | empty | Comma-separated codes: `es`, `en`, `es-419`, `multi` |
| `MAX_SIZE_BYTES` | empty | Optional upper size limit, including cached results |
| `MIN_SEEDERS` | `0` | Minimum known seeders for uncached results |

The CLI configures one Torznab endpoint. Library users can register several named
providers with `ProviderRegistry` and call `await SearchService(registry).search(request)`.
Configuration is read at the CLI boundary, never during imports or inside the domain.

Output is JSON containing ordered candidates and failures. Download URLs are
omitted from CLI output because they may include credentials; library results
retain them. Exit status is `0` for success (including no matches or partial
success), `1` for provider failures without any eligible candidates, and `2` for
invalid arguments/configuration.

### Prowlarr and Torznab queries

Use a **torrent indexer's** Torznab URL copied from Prowlarr. For example:
`http://localhost:9696/1/api` or
`http://localhost:9696/api/v1/indexer/1/newznab`. The Prowlarr web root is not a
Torznab endpoint. Register indexers separately to respect each one's capabilities.
These endpoint routes are listed in the [official Prowlarr API](https://prowlarr.com/docs/api/).

The provider first requests capabilities, following the
[Torznab specification](https://torznab.github.io/spec-1.3-draft/torznab/Specification-v1.3.html).
When the relevant parameters are advertised, the encoded requests look like:

```text
http://localhost:9696/1/api?t=caps&apikey=REDACTED
http://localhost:9696/1/api?t=movie&imdbid=1234567&apikey=REDACTED
http://localhost:9696/1/api?t=tvsearch&imdbid=1234567&season=2&ep=5&apikey=REDACTED
```

The IMDb prefix `tt` is removed for the Torznab query. If the indexer advertises
`q` but not `imdbid`, supply `--title`:

```text
http://localhost:9696/1/api?t=movie&q=Example+Movie&apikey=REDACTED
http://localhost:9696/1/api?t=tvsearch&q=Example+Show&season=2&ep=5&apikey=REDACTED
```

If TV coordinate parameters are unavailable, an advertised `q` search uses
`Example Show S02E05`. An advertised generic `search` is a final title-based
fallback. No title lookup is performed. An IMDb-only request on an indexer without
IMDb support returns `unsupported_search`. Failed or invalid capabilities are
reported rather than guessed. Library callers may explicitly supply known
`TorznabCapabilities` for an endpoint whose caps API is incompatible.

Capabilities are retained for the lifetime of a provider instance. Only the
indexer's first/default result page is fetched. There are no retries or HTML
scrapers. HTTP redirects are rejected to avoid forwarding API keys.

## Identity, metadata and ranking

v1 torrent hashes normalize from 40-character hex, 32-character base32, URNs or
magnets to lowercase hex. SHA-256 v2 multihashes are supported; hybrid magnets
prefer their v1 hash. Known file indices are part of identity. An unknown file
index remains a torrent-level identity, distinct from any known file; no file
selection is inferred and season-pack sizes are not copied onto selected files.
Duplicates with the same identity merge useful metadata deterministically.
Hashless HTTP URLs use a conservative URL identity that preserves signed queries.

Metadata is based on explicit labels only. `MULTI` stays `multi`; it does not imply
English or Spanish. `Latino` maps to `es-419`. Missing size/seed counts remain
unknown, not zero. Episode labels do not select a torrent file.

Ranking is lexicographic: cached first, resolution preference, language preference,
seeders, then stable identity/provider/metadata tie-breakers. Maximum size and
minimum seeders are eligibility filters. Cached results bypass the seed minimum.
Unknown sizes/seeders remain eligible; preferences do not invent missing metadata.
Torznab does not claim that any result is cached.

## Tests and package validation

All fixtures are synthetic. Tests do not need Prowlarr, Stremio, qBittorrent or
external network access.

```sh
python -m unittest discover -s tests -v
python -c 'import self_debrid; from self_debrid.application.search import SearchService'
python -m self_debrid --help
self-debrid --version
```

To build distribution artifacts:

```sh
python -m pip install build
python -m build
```

GitHub Actions runs unit tests, builds the package, installs the wheel and checks
imports/CLI outside the source directory for each supported CI Python version.
See [architecture](docs/V2_ARCHITECTURE.md), [roadmap](docs/V2_ROADMAP.md) and
[engineering rules](AGENTS.md).
