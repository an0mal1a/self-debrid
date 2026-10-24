# Self-Debrid v2

Self-Debrid v2 is a standalone Python foundation for a lightweight, self-hosted
media discovery, download, cache and streaming engine. Its core accepts movie or
episode requests, aggregates discovery providers, and returns normalized, ranked
release candidates with structured provider failures.

**Implemented: discovery and a native Stremio torrent addon.** The aiohttp server
returns ranked BitTorrent v1 hash streams for Stremio to play with its own torrent
engine. Self-Debrid does not download torrents or serve local video bytes.
Live Stremio playback and real Prowlarr integration have not been verified.
Phase 2.5 adds optional Cinemeta title/year lookup, multi-indexer configuration,
short-lived shared discovery caching and local diagnostics.

## v1 and v2 are separate applications

- [`main`](https://github.com/an0mal1a/self-debrid/tree/main): stable Kodi /
  AllDebrid-compatible v1 implementation.
- [`v1-kodi`](https://github.com/an0mal1a/self-debrid/tree/v1-kodi): preserved v1
  maintenance branch.
- `v2-development`: independent v2 package, with no v1 modules or runtime imports.

v1 resolves links discovered by a Kodi addon. v2 takes responsibility for source
discovery and will eventually own file selection, downloading, caching and
streaming. A native Stremio HTTP adapter is implemented; a new Kodi adapter
will follow later. v2 does not preserve the v1 API or its configuration.

## Implemented

- Validated movie/episode `MediaRequest` and immutable `StreamCandidate` models.
- Stream-only Stremio manifest and movie/series HTTP resources using IMDb IDs
  (`tt1234567` and `tt1234567:2:5`), plus `/health` liveness.
- Pure Stremio serialization of supported v1 torrent hashes and reliable file
  indices, with useful release metadata and no download URL passthrough.
- Conservative episode filtering before deduplication; ambiguous packs require
  an explicit index and a matching episode mapping.
- Async provider registry with bounded concurrency, per-provider timeouts,
  cancellation propagation and structured failures.
- `SearchService`: provider aggregation, normalization, metadata-preserving
  deduplication, preference filtering and deterministic ranking.
- Async Torznab discovery, including per-indexer Prowlarr Torznab endpoints.
- Capability discovery, IMDb-first searches and optional Cinemeta title/year
  hydration when a configured indexer needs a supported text query.
- Up to 16 independently named endpoints from a small JSON file with environment
  references for API keys; the single-indexer environment setup remains supported.
- Bounded positive/negative metadata caches and short-lived discovery-result cache
  with shared in-flight work, cancellation isolation and no download-cache claims.
- Movie identity/title/year checks and local `doctor` exclusion/error diagnostics.
- Safe XML parsing, bounded response sizes, explicit connection/read timeouts.
- Partial-item recovery: good RSS items survive bad items; one sanitized summary
  records the skipped count and at most five item positions per feed.
- Recognized torrent locators are retained separately from unverified details links.
- Explicit release labels for resolution, codecs, HDR, languages and episodes;
  optional size and seed metadata from the feed.
- CLI, environment configuration and offline `unittest` coverage.

Not implemented: qBittorrent-based local playback, local HTTP/Range streaming,
cache persistence, configure UI, torrent metadata retrieval/exact file verification,
and the Kodi adapter. Additional discovery providers can implement the existing
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
`unittest` is included in Python. The server uses the existing `aiohttp.web`;
no additional runtime dependency, web framework or database was added in Phase 2.

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
| `HTTP_HOST` | `127.0.0.1` | HTTP bind host; use `0.0.0.0` explicitly for LAN access |
| `HTTP_PORT` | `8080` | HTTP port, 1–65535 |
| `PUBLIC_ADDON_URL` | empty | Optional advertised base URL; used only for the startup installation address |
| `TORZNAB_URL` | required without `PROVIDERS_FILE` | Complete per-indexer Torznab endpoint, without query parameters |
| `PROVIDERS_FILE` | empty | Private JSON config of 1–16 named Torznab endpoints; replaces single-endpoint settings |
| `CINEMETA_ENABLED` | `false` | Opt-in lazy IMDb → title/year lookup; sends requested IDs/media types to Cinemeta |
| `METADATA_CONNECT_TIMEOUT` / `METADATA_READ_TIMEOUT` | `3` / `5` | Metadata connection/read deadlines in seconds |
| `METADATA_TTL` / `METADATA_NEGATIVE_TTL` | `3600` / `30` | Positive/negative metadata retention in seconds |
| `METADATA_CACHE_ENTRIES` | `256` | Metadata LRU entry bound; also limited to 512 KiB estimated payload |
| `DISCOVERY_CACHE_TTL` | `30` | Successful discovery retention, seconds; `0` disables retained results |
| `DISCOVERY_CACHE_ENTRIES` / `DISCOVERY_CACHE_BYTES` | `128` / `8388608` | Discovery LRU count/estimated payload byte bounds |
| `DISCOVERY_MAX_INFLIGHT` | `32` | Distinct active discovery requests; identical requests share work |
| `TORZNAB_API_KEY` | empty | API key, sent as an encoded Torznab query parameter |
| `TORZNAB_NAME` | `prowlarr` | Stable provider identifier |
| `CONNECT_TIMEOUT` | `5` | HTTP connection timeout, seconds |
| `READ_TIMEOUT` | `10` | HTTP socket read timeout, seconds |
| `PROVIDER_TIMEOUT` | `20` | Entire active provider search, including capabilities, seconds |
| `MAX_CONCURRENCY` | `4` | Maximum active provider operations across the shared registry |
| `RESOLUTION_ORDER` | `2160p,1080p,720p,480p` | Most preferred first |
| `PREFERRED_LANGUAGES` | empty | Comma-separated codes: `es`, `en`, `es-419`, `multi` |
| `MAX_SIZE_BYTES` | empty | Optional upper size limit, including cached results |
| `MIN_SEEDERS` | `0` | Minimum known seeders for uncached results |

Configuration is read at the CLI boundary, never during imports or inside the domain.
The CLI and server use the same immutable settings and ProviderRegistry. Metadata
resolution is an application-facing protocol, independent of Stremio HTTP handlers
and Torznab. No catalog resource, external API key, database or new runtime dependency
is needed.

### Configure multiple Prowlarr indexers

Copy `providers.example.json` to `providers.json` and edit endpoints:

```json
{
  "providers": [
    {"name": "public-one", "url": "http://127.0.0.1:9696/1/api", "api_key_env": "PROWLARR_API_KEY"},
    {"name": "public-two", "url": "http://127.0.0.1:9696/2/api", "api_key_env": "PROWLARR_API_KEY"}
  ]
}
```

Set `PROVIDERS_FILE=providers.json` and `PROWLARR_API_KEY` locally in your exported
`.env`. Keys may reference different environment variables per indexer. An omitted
`api_key_env` means no key; a referenced variable must exist. Inline `api_key`
fields, duplicate names (case-insensitive), unknown fields, query credentials and
more than 16 providers are rejected. The JSON read is limited to 64 KiB. The local
`providers.json` is gitignored. Config is loaded at startup; restart after edits.
When set, the file replaces `TORZNAB_URL` rather than appending a duplicate provider.
With no file, existing `TORZNAB_URL` / `TORZNAB_API_KEY` / `TORZNAB_NAME` work as before.

Choose separate per-indexer endpoints, not a combined feed with ambiguous
capabilities. API keys stay on the server; never add them to Stremio addon URLs.

The discovery `search` command still returns JSON containing ordered candidates and failures. Download URLs are
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
fallback. With `CINEMETA_ENABLED=true`, missing title/year metadata is resolved
lazily for providers that advertise usable text search but cannot execute the IMDb
request. Failed or invalid capabilities are reported rather than guessed. With
metadata disabled or unavailable, an indexer needing a title returns
`unsupported_search`; IMDb-capable providers still return their results. Library callers may explicitly supply known
`TorznabCapabilities` for an endpoint whose caps API is incompatible.

Capabilities are retained for the lifetime of a provider instance. Only the
indexer's first/default result page is fetched. There are no retries or HTML
scrapers. HTTP redirects are rejected to avoid forwarding API keys.

Stremio normally supplies only an IMDb ID and episode coordinates. To improve
title-only indexers, explicitly enable `CINEMETA_ENABLED=true`. This sends requested
IMDb IDs and media types to `v3-cinemeta.strem.io` when a title is needed. It does
not send provider keys, search history batches or local candidate lists, and does
not use Cinemeta as a catalog. No extra metadata API key is required.

A provider with advertised IMDb support is queried directly. It does not trigger
a metadata lookup just because it returned zero matches. Text-only providers share
one lookup per media type/IMDb ID, with strict payload identity, bounded JSON,
connection/read/total timeouts and negative caching after outages. Season/episode
coordinates are preserved when advertised; otherwise `S02E05` is appended to `q`.
A movie `year` query parameter is sent only when advertised. An explicit CLI
`--title` avoids metadata lookup. Disabling Cinemeta leaves that CLI path available.

## Native Stremio installation

After exporting the configuration above:

```sh
self-debrid serve --host 127.0.0.1 --port 8080
# Equivalent: python -m self_debrid serve --host 127.0.0.1 --port 8080
```

On the same machine, open Stremio Desktop's Addons view, paste
`http://127.0.0.1:8080/manifest.json` into the addon URL/search field, and confirm
installation. Open a movie or episode from an existing IMDb catalog and inspect
its Self-Debrid results. There is no landing page or configure UI.

For LAN access, run `self-debrid serve --host 0.0.0.0 --port 8080`. Use the server's
actual LAN address (for example `192.168.1.50`), not `0.0.0.0` or another device's
`127.0.0.1`. You can check reachability at `http://192.168.1.50:8080/health`.

The [Stremio SDK testing guide](https://github.com/Stremio/stremio-addon-sdk/blob/master/docs/testing.md)
documents HTTPS for addon URLs other than `127.0.0.1`. For LAN/remote clients that
reject HTTP or mixed content, put a trusted HTTPS reverse proxy in front of the
server and install its `https://.../manifest.json` address. Forward the same paths
and CORS headers. An optional `PUBLIC_ADDON_URL=https://addon.example.invalid`
changes only the printed installation address; it does not enable TLS or rewrite
routes. For a proxy path prefix, the proxy must strip it before forwarding.
This phase supplies no public hosting or authentication system.

Use a client with a native torrent engine (such as desktop) for the initial
playback smoke test; a browser alone is not evidence of native torrent playback.
No trackers, passkeys, magnet URIs or torrent download URLs are sent to Stremio.
Private trackers requiring authenticated announce URLs are not supported by this
hash-only handoff. Public peer availability and client codec/platform support
still determine whether playback starts. See the exact
[real-client smoke test](docs/STREMIO_SMOKE_TEST.md).

### HTTP contract and file selection

- `GET /manifest.json`: stable ID `com.selfdebrid.addon`, version `2.0.0-dev.2`,
  movie/series streams only; no catalogs, metadata or configuration resources.
- `GET /stream/movie/tt1234567.json`: ranked native torrent streams.
- `GET /stream/series/tt1234567:2:5.json`: conservatively matched episode streams.
- `GET /health`: local process liveness, without contacting providers.

Successful searches return `{"streams": [...]}` with JSON and CORS headers.
Empty results and provider failures are represented by `{"streams": []}` with HTTP
200; healthy providers' results survive other failures. Invalid IDs return a
sanitized HTTP 400 JSON error. There is no provider failure text in responses.
The registry bounds each active provider; with N providers and concurrency C,
the upper search budget is roughly ceil(N/C) provider timeouts plus cleanup.
Raw HTTP access logs are disabled by the `serve` command.

Only 40-character BitTorrent v1 hashes are serialized as `infoHash`. Base32 v1
and hybrid magnets normalize correctly. V2-only SHA-256 hashes are retained for
discovery but excluded from Stremio output. `fileIdx` is emitted only when known
and within the current Stremio core's supported range (0–65535).

A known index alone does not identify an episode. A pack needs both a known index
and a matching upstream season/episode mapping. Obvious contradictory episode
names/attributes are rejected. Without an index, only a single exact SxxExxx
release name with no season-pack, complete, collection, or multi-episode/range
markers is accepted. **Stremio then selects the largest torrent file**; the stream
description says so. This fallback is evidence from naming, not verified file
selection: misleading names, samples, extras or multiple versions can still pick
the wrong file. Movies without an index use the same largest-file behavior.
Exact torrent metadata inspection belongs to Phase 3.

## Discovery reliability and diagnostics

`self-debrid doctor movie tt1254207` checks configured endpoints/capabilities,
per-provider search results, safe provider error codes, application exclusions and
how many surviving candidates cannot be represented as native Stremio torrents.
For episodes use `self-debrid doctor series tt1234567:2:5` with a real series ID.
It performs an explicit Cinemeta check when enabled, even if every indexer can
search by IMDb. `doctor` is a local command, never a public HTTP route. It prints
no endpoint URLs, candidate hashes/locators or credentials. An exit code of zero
means diagnostics completed; inspect the per-provider error/status fields.

Results can be excluded for a different echoed IMDb ID, explicit episodic content
on movie searches, incompatible episode/pack evidence, a movie year differing by
more than one year, or a clearly different title before a release year/episode/
quality boundary. Punctuation, accents, bracketed release groups and leading
English articles are normalized. A matching upstream IMDb ID takes precedence
over alternate title spelling. Unknown title/year evidence stays unknown and is
not rejected by a fuzzy-score threshold; numeric titles are treated conservatively.
Title aliases without an echoed IMDb ID may still be filtered. If lazy metadata
was already resolved, it validates all providers' results in that search.

Successful discovery results (including genuine empty/fully filtered results)
are retained briefly with media/configuration-specific keys. Results containing
provider failures or metadata errors are not retained. An oversized result is
not retained either. Memory is bounded by entry count, estimated serialized bytes
and in-flight limits; metadata caches have separate bounds. Concurrent identical
requests share work; cancelling one waiter leaves other waiters intact, while the
last waiter cancels unneeded work. TTL zero still permits sharing active work.
A full in-flight budget produces a safe `busy` failure. Provider concurrency is
bounded across all searches of the shared registry, not independently per client.

**Discovery-result caching is not downloaded media caching.** It never changes
`StreamCandidate.cached`, announces file availability or guarantees playback.
The HTTP `Cache-Control: no-store` response remains unchanged. There is no SQLite
or restart persistence; capability snapshots last for the provider instance.

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
external network access. HTTP contract tests use aiohttp test utilities on
loopback with mocked discovery providers/transport; they do not contact real services.

```sh
python -m unittest discover -s tests -v
python -c 'import self_debrid; from self_debrid.application.search import SearchService'
python -m self_debrid --help
self-debrid --version
self-debrid serve --help
self-debrid doctor --help
```

To build distribution artifacts:

```sh
python -m pip install build
python -m build
```

GitHub Actions runs unit tests, builds the package, installs the wheel and checks
imports/CLI outside the source directory for each supported CI Python version.
See [architecture](docs/V2_ARCHITECTURE.md), [roadmap](docs/V2_ROADMAP.md) and
[engineering rules](AGENTS.md). Phase 2.5 details are recorded in
[discovery reliability delivery](docs/V2_RELIABILITY_DELIVERY.md).
