# Phase 3A: torrent preparation foundation

This is an opt-in backend Python API, not a video player or media server. It starts
the selected file's download after metadata verification. Native Stremio routes,
manifest, torrent serialization and discovery CLI behavior remain unchanged.
Live qBittorrent integration has not been exercised; all automated fixtures are
synthetic and run offline. Phase 3B requires separate Lead Developer authorization.

## Boundaries

```text
Explicitly selected StreamCandidate + MediaRequest
    -> PlaybackService (bounded volatile sessions)
       -> TorrentClient protocol -> QBittorrentClient -> private Web API
       -> select_video_file(actual TorrentFile list) [pure]
       -> DownloadWorkspace protocol -> LocalDownloadWorkspace [private paths]
    -> PlaybackSession.public_view() [no filesystem paths or locators]
```

`infrastructure/torrent_engine.py` composes the ports only when explicitly called.
`TorrentEngineSettings.from_env()` reads configuration separately from discovery;
no Torznab configuration is required for a manually selected candidate. There are
no extra runtime dependencies. Do not serialize the private dataclass fields with
`asdict()` or publish the library API directly without an authorization design.

## Selection policy

The authoritative index is the `index` supplied with the actual torrent file list,
not its array position or the index originally advertised by a provider. The
session preserves `provider_file_index` separately from `verified_file_index`.

| Situation | Result |
| --- | --- |
| One eligible movie, samples/trailers/subtitles alongside | Select the movie |
| Trusted movie title/year supplied | Explicit filename evidence must agree |
| Generic movie/video/main/feature filename | Its immediate parent can provide title/year |
| Several unrelated movies, even with a provider hint | Ambiguous without matching title evidence |
| Several matching encodes | Ambiguous unless a hint independently identifies a matching file |
| CD/part split movie | Reject; combining parts is not supported |
| Season pack with one exact episode file | Select its actual metadata index |
| Bare S02E05 filename under nested directories | Exact coordinates suffice |
| Wrong season/episode or explicit different series prefix | Reject |
| Multi-episode/range file or unidentified season-pack video | Reject |
| Duplicate/missing index or unsafe path metadata | Structured metadata/path error |
| Hint to a missing file, subtitle, extra or wrong episode | Reject the hint; do not silently substitute |

Video extensions are an eligibility list, not codec or content verification. No
byte sniffing, subtitle selection, media probing, transcoding or DRM checks occur.
Without a canonical movie title the single-video policy verifies the file index
but cannot prove IMDb identity. Canonical metadata must be supplied on MediaRequest
when available; preparation does not trigger an additional Cinemeta lookup. Exact
title normalization preserves Unicode, ignores punctuation and accent marks, and
does not perform fuzzy alias matching. Year contradictions fail when both are
known; missing year remains unknown. Release groups, aliases, unusual naming,
multipart movies and episodes expressed only as E05 can be false negatives.
Samples/extras tokens are deliberately conservative, including directory names.
There is no largest-file fallback anywhere in backend selection.

## qBittorrent API operations

The adapter supports qBittorrent 4.6 and 5.x. It rejects older and unknown major
versions rather than assuming compatible metadata controls or file indices.
The private Web UI session uses form login and its SID cookie; connections have
separate connect/read and overall request deadlines. HTTP redirects are rejected.

| Purpose | Web API operation |
| --- | --- |
| Authenticate and negotiate version | POST auth/login; GET app/version |
| Check identity/state and fetch actual files | GET torrents/info; GET torrents/files |
| Submit magnet or validated multipart metainfo | POST torrents/add |
| Stop/start owned torrent | POST torrents/pause/resume on 4.6; torrents/stop/start on 5.x |
| Skip unrelated files; prioritize verified selection | POST torrents/filePrio, priorities 0 and 7 |
| Supported playback preferences | POST torrents/toggleSequentialDownload and toggleFirstLastPiecePrio |

The [official Web API documentation](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-%28qBittorrent-5.0%29)
describes the API. Preference calls are toggles: the adapter reads current flags,
changes only known differing values and never retries a timed-out toggle. Missing
flags produce bounded warning codes instead of guessed support. A failed strategy
request stops owned work and fails preparation; it is not silently ignored.

New magnets use `stopCondition=MetadataReceived` while resolving metadata. Uploads
start paused/stopped with original content layout. Automatic torrent management
and use of the client's separate incomplete directory are disabled per submission;
no global preferences are changed. The stop condition and version-specific add/
control verbs were checked against pinned official source:
[4.6 add controller](https://github.com/qbittorrent/qBittorrent/blob/release-4.6.0/src/webui/api/torrentscontroller.cpp),
[4.6 metadata stop implementation](https://github.com/qbittorrent/qBittorrent/blob/release-4.6.0/src/base/bittorrent/torrentimpl.cpp),
[5.0 add controller](https://github.com/qbittorrent/qBittorrent/blob/release-5.0.0/src/webui/api/torrentscontroller.cpp).
The stop-condition parameter is source-backed and is not fully described by the
wiki; confirm the behavior during the real smoke test, especially on patched builds.

Metadata may require reachable peers and private tracker credentials. A hash-only
magnet may never obtain metadata; preserve a selected private magnet internally
or use an authorized torrent locator. Metadata polling is bounded by its own
deadline and the overall preparation deadline. No retries, tracker scraping,
tracker credential injection or arbitrary remote URL submission are performed.
File priority and sequential/first-last preferences do not guarantee smooth
playback; torrent piece boundaries can include bytes belonging to skipped files.

## Ownership, paths and sessions

Each session gets an opaque UUID, a new directory and a unique `self-debrid-UUID`
tag. Before adding, refuse any existing torrent with that hash. Before each
mutation, require the tag, original directory and disabled automatic management.
Two simultaneous submissions through this adapter serialize their existence check
and add. PlaybackService also permits only one active session per torrent hash,
including identities learned from hashless locators, to prevent competing file
priority strategies before an add becomes visible. Independent clients/processes can still race because qBittorrent has no
atomic create-only add operation. A dedicated instance is recommended. External
changes to tags, management or location cause ownership failure, never forced
relocation or adoption. Do not edit owned torrents while a session is active.

Cleanup only stops verified owned work. There is no delete/remove method, data
deletion, directory cleanup, global pause or ownership of unrelated torrents.
Closing an active session releases its slot once a stop succeeds; uncertain stop
failures reserve capacity for an explicit retry. An uncertain add response retains
a provisional ownership identity for guarded cleanup. If the torrent never becomes
visible, an uncertain failure can retain capacity until operator recovery/restart.
Stopped torrents/directories remain for manual operator management. Metadata-only
stops are best effort across network failures; inspect qBittorrent after a failure.

Defaults: 4 active sessions, 32 records, terminal retention up to one hour (lazy
pruning on new reservations), metadata deadline 60 seconds, preparation 120 seconds,
HTTP operations 20 seconds and cleanup stop at most 5 seconds. Active records are
never evicted. Per-session locks serialize preparation, refresh and close. Waiting
on a busy record may include its bounded preparation plus cleanup time. Caller
cancellation stops owned work and remains cancellation. There are no background
tasks or automatic monitoring; callers explicitly refresh/close sessions. Shutdown
closes retained sessions and the HTTP client. State and ownership are not recovered
after a process crash; previously added torrents are rejected on the next run.

qBittorrent and Self-Debrid must see the configured absolute download directory at
the same path; remote path mapping is not implemented. Torrent paths reject absolute
names, parent traversal, Windows drive/UNC/ADS forms, control characters, symlinks,
reserved devices, trailing dots/spaces and excessive nesting. All file-list paths
are validated, not only the selected one. Selected locations are resolved beneath
the session directory and rechecked on refresh. This phase does not open media for
streaming: future delivery must use safe atomic opening to address filesystem races.
Use a trusted download root not writable by untrusted local users.
Per-session directories are created with mode 0700. Run both processes under the
same OS user (or deliberately provision suitable shared ACLs); cross-user directory
permission management is outside this phase.

`progress` is the client's selected-file fraction. `peerAvailability` is the fraction
of file pieces available among peers (0–1), not locally readable bytes. qBittorrent's
unknown sentinel -1 is mapped to null, including seeding-only torrents; see the
[4.6 availability implementation](https://github.com/qbittorrent/qBittorrent/blob/release-4.6.0/src/base/bittorrent/torrentimpl.cpp).
`availableBytes` stays null
until progress is exactly 1 and the expected local file exists with the right size.
This is complete-file evidence, not contiguous piece verification, a cached/debrid
flag, proof of decodable content or a playback URL.

## Supported torrent locator workflow

`TORRENT_DOWNLOAD_ORIGINS` is empty by default. HTTP(S) retrieval requires an exact
operator-authorized origin (scheme, hostname, effective port); never put signed
queries or credentials in this allowlist. Only an explicitly selected candidate
is fetched, with no qBittorrent cookie or authentication headers and no redirects.
Responses and metainfo are limited to 4 MiB. The bounded bencode parser rejects
duplicate keys, excessive nesting/counts, malformed sizes/pieces, invalid v1 file
structure, symlinks and unsafe paths. It hashes the original `info` bytes, checks
any supplied hash and uploads the bytes instead of letting qBittorrent fetch URLs.
HTML details pages and v2-only metainfo are rejected. v1-compatible hybrid metainfo
must include valid v1 fields; v2-only magnets/hashes are unsupported in this phase.
API JSON responses are also capped at 4 MiB, with at most 10,000 torrent files.

Allowed origins are a trust decision, not a general URL proxy or an SSRF sandbox:
the operator must control the endpoints/DNS they authorize. Private LAN indexers
are supported deliberately. A redirecting or login-dependent download endpoint
will fail; do not broaden trust automatically. Signed queries remain unchanged.
Errors expose fixed local codes only; no raw response text, paths, usernames,
passwords, API keys, passkeys, magnet locators or download URLs are logged.

## Real operator smoke test (not yet performed)

1. Keep PR #2 draft/unmerged. Use a dedicated private qBittorrent 4.6/5.x instance,
   a writable shared test directory and torrents you are authorized to download.
   Configure QBITTORRENT_URL/USERNAME/PASSWORD and TORRENT_DOWNLOAD_DIRECTORY from
   `.env.example` in the local environment. Do not commit the environment file.
2. Create a small test movie torrent with a named movie, a sample, trailer and
   subtitle; seed it from a separate test client. Create another torrent with
   S02E04, S02E05 and S02E06 video files of deliberately different sizes. Record
   the real qBittorrent metadata indices. Avoid a torrent already in the target
   client; the preparation policy rejects it.
3. Run a small local script using `open_torrent_engine` as in the README, passing
   the explicit selected `StreamCandidate` and matching `MediaRequest`. Supply a
   private magnet through an environment variable, never a command-line argument
   or log. Movie requests should include the known title/year. For series use
   `MediaRequest(MediaType.SERIES, "tt1234567", 2, 5, title="Example Show")` and
   files named `Example.Show.S02E05...` or bare `S02E05...`.
4. Check `session.public_view()`: downloading state, correct verifiedFileIndex,
   no local path or locator. Inspect the private qBittorrent UI: correct unique
   tag/directory, metadata stop before strategy, selected priority 7 and others
   0, requested download preferences. Use an explicit pause/input in the local
   script to inspect before context exit; no HTTP playback test is possible yet.
5. Call `refresh` while incomplete: progress changes, availableBytes remains null.
   After completion, verify local existence/size and complete-file availability.
   Close the session/context: its torrent stops and both torrent and data remain.
   Confirm an unrelated pre-existing torrent and its files remain unchanged.
6. Repeat with wrong episode, missing/incorrect hint, multiple matching encodes,
   ambiguous movie and unavailable metadata. Expect structured errors, no forced
   selection or unrelated file priority changes. Repeat an existing hash: expect
   torrent_exists without modification. Remove only your own test artifacts
   manually if you wish to reuse the hash.
7. Optionally authorize only your fixture indexer's origin and repeat using a
   signed torrent locator. Confirm no redirect, SID forwarding or secret output;
   a mismatched hash, HTML page or unauthorized origin must fail before upload.
8. Run the existing [Stremio smoke test](STREMIO_SMOKE_TEST.md) separately to
   confirm native behavior. Stremio will still use its own torrent engine and
   receives no local Self-Debrid playback URL. Record client/server versions and
   outcomes before claiming live compatibility to the Lead Developer.
