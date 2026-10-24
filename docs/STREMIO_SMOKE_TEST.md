# Real Stremio smoke test — Phase 2.5 discovery reliability

This is a procedure for a human with a real Prowlarr endpoint and Stremio Desktop.
It has **not** been executed against those real services during implementation.
The automated suite uses synthetic fixtures and loopback HTTP only. A green
contract test is not a claim that a particular indexer/client combination plays.

## 1. Install and configure

Use `v2-development` and Python 3.10+ in a virtual environment:

```sh
python -m pip install -e .
cp .env.example .env
```

Edit `.env` locally. Set `TORZNAB_URL` to the complete Torznab endpoint for a
**torrent** indexer in Prowlarr, such as `http://127.0.0.1:9696/1/api`, and set its
API key separately in `TORZNAB_API_KEY`. Do not paste an API-key-bearing URL into
Stremio. For a mixed-provider reliability test, copy providers.example.json to a local
providers.json and configure two independently named endpoints (one advertising
IMDb search and another advertising title q). Set PROVIDERS_FILE=providers.json;
the file replaces the single TORZNAB_URL endpoint. Reference API keys by exported
environment variable names. Use public/legal test content.

Set CINEMETA_ENABLED=true to exercise optional title/year lookup. This sends
requested IMDb IDs/media types to Cinemeta when a title is needed. No metadata API
key is required. You can leave it false for an IMDb-only privacy-preserving path.

Export a trusted local `.env` file in a POSIX shell:

```sh
set -a
. ./.env
set +a
```

PowerShell users can set the same variables as `$env:TORZNAB_URL` and
`$env:TORZNAB_API_KEY`. The application does not automatically load `.env`.

Choose a movie and standalone episode present on your configured indexer, with
publicly discoverable peers and media you may access. Set these variables using
their real IMDb IDs; the values below illustrate the required formats and do
not promise availability on your indexer:

```sh
MOVIE_ID=tt1254207
EPISODE_ID=tt1234567:2:5
self-debrid search movie "$MOVIE_ID"
self-debrid search series "$EPISODE_ID"
```

Check that discovery returns matching candidates with supported v1 hashes.
Run self-debrid doctor movie "$MOVIE_ID" and self-debrid doctor series "$EPISODE_ID".
Verify endpoint status, advertised parameters, metadata status, safe error codes,
application exclusions and compatibility counts. No private endpoints, candidate
locators or keys should appear. An unsupported_search code means the advertised
queries cannot execute without missing/disabled/unavailable title metadata.
The CLI --title path remains available if metadata is disabled or unavailable.
A `.torrent` download locator without an info hash is valid discovery data but
will not appear in Stremio; this phase does not download metadata to extract it.

## 2. Start the addon and check HTTP responses

In the configured shell:

```sh
self-debrid serve --host 127.0.0.1 --port 8080
```

In a second terminal, set the same movie/episode test IDs and run:

```sh
curl -i http://127.0.0.1:8080/health
curl -i http://127.0.0.1:8080/manifest.json
curl -fsS "http://127.0.0.1:8080/stream/movie/$MOVIE_ID.json" | python -m json.tool
curl -fsS "http://127.0.0.1:8080/stream/series/$EPISODE_ID.json" | python -m json.tool
curl -i http://127.0.0.1:8080/stream/series/invalid.json
```

Expected observations:

- Health is HTTP 200 JSON with status `ok`; it does not check indexer health.
- Manifest is HTTP 200 JSON, addon ID `com.selfdebrid.addon`, version
  `2.0.0-dev.2`, movie/series stream resource only, empty catalogs and no config UI.
- Responses have `Access-Control-Allow-Origin: *` and `Cache-Control: no-store`.
- Stream resources are HTTP 200 with a `streams` array. With available eligible
  results it is nonempty; empty is valid for unavailable/filtered/failed searches.
- Every stream has a 40-character hexadecimal `infoHash`. A known file index is
  `fileIdx` (0–65535); it is omitted when unknown.
- There is no playable `url`, `externalUrl`, `sources`, magnet URI, tracker
  endpoint, passkey, API key or raw provider error in the response.
- Invalid series ID is HTTP 400 with sanitized JSON, without echoing its input.

For an episode, verify that an obvious different episode is absent. Season packs
without an explicit usable index and matching episode mapping must be absent.
A single exact named episode may appear without `fileIdx`: its description must
state that Stremio selects the largest file. This is a limited fallback and can
still select the wrong file if the torrent contents contradict its name.

### Mixed providers, cache and failure observations

Repeat the same movie and episode requests within 30 seconds: identical requests
should reuse discovery work, without a cached/debrid badge or local file promise.
Restart after changing configuration; no persistent cache is kept. During controlled
operator testing, make one provider unavailable and repeat after the short TTL
(or restart). Healthy providers should still return candidates; doctor should show
the failing provider's safe code. Do not publish private service logs to demonstrate
this behavior. Restore the provider and retry: failure-bearing discovery results
must not have been retained as successful snapshots.

To compare title fallback, run doctor with Cinemeta enabled and disabled, restarting
the process between settings. IMDb providers should work in both modes. A title-only
provider should work with enabled valid metadata or explicit CLI --title. A metadata
outage must not discard IMDb results. Negative metadata lookups may be retained for
30 seconds. Inspect exclusions for remakes, different titles and incompatible
season-pack results; empty arrays alone do not establish the cause.

## 3. Install in Stremio Desktop and try playback

1. Leave the addon server running on the same computer as Stremio Desktop.
2. Open the Addons view, paste `http://127.0.0.1:8080/manifest.json` into its addon
   URL/search field, open the addon result and confirm installation.
3. Use an existing IMDb catalog to open the selected movie, and confirm the
   Self-Debrid stream choices and release metadata appear.
4. Select a public, available result and observe whether the client starts
   native torrent playback. If it fails, record client version/platform, the
   sanitized release/hash, peer availability and whether the same torrent works
   independently. Do not assume the HTTP contract guarantees peers or codecs.
5. Open the selected series/season/episode and repeat. Verify the actual played
   episode, especially for index-free results, instead of merely observing video.
6. Confirm the addon does not launch qBittorrent, create Self-Debrid download
   sessions or provide a local video URL. Any torrent fetching/playback belongs
   to Stremio's torrent engine.

Private trackers that require authenticated announce URLs are unsupported in this
hash-only mode: private tracker sources are intentionally not forwarded. Prefer
Stremio Desktop with its torrent engine for the first smoke test. A browser JSON
response or successful addon installation alone does not demonstrate playback.

## 4. Optional LAN test

Start with explicit LAN binding:

```sh
self-debrid serve --host 0.0.0.0 --port 8080
```

From another device, check `http://SERVER_LAN_IP:8080/health`. Use the server's
actual LAN IP, not `0.0.0.0` or the client's loopback address.

The [official Stremio SDK guide](https://github.com/Stremio/stremio-addon-sdk/blob/master/docs/testing.md)
documents HTTPS outside `127.0.0.1`. If the client refuses the LAN HTTP URL, use
a trusted HTTPS reverse proxy reachable by that device and install
`https://YOUR_ADDON_HOST/manifest.json`. Proxy the same manifest/stream/health
routes with CORS intact; strip any external path prefix before forwarding.
`PUBLIC_ADDON_URL` may advertise that base URL at startup, but does not add TLS,
authentication, hosting or routing. No reverse proxy is installed by this phase.
Repeat the movie and episode observations on that device; record compatibility
per client/platform rather than generalizing from the desktop result.

## Review record

Record commit SHA, Python/Prowlarr/Stremio versions, indexer capability support,
installation URL scheme, movie and episode IDs, sanitized HTTP observations,
actual episode verification, index/fallback behavior, and playback outcome.
Keep API keys, private announce URLs and passkeys out of shared logs or reports.
