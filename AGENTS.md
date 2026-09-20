# Self-Debrid development instructions

This file defines the engineering rules for agents working on Self-Debrid v2.

## Source of truth

The active v2 integration branch is v2-development. The current main branch is the stable legacy implementation and must remain usable while v2 is built.

## Product mission

Self-Debrid v2 is a lightweight self-hosted discovery, download, cache and streaming engine for media the user is authorized to access. Stremio and Kodi are clients of the same core; neither client owns discovery or download logic.

## Architecture invariants

1. Keep domain logic independent from Flask, Stremio, Kodi, qBittorrent and JDownloader.
2. Every discovery source implements the Provider contract and returns normalized StreamCandidate objects.
3. Never put provider-specific fields into the core domain model unless they are broadly useful.
4. Ranking is deterministic and independently testable.
5. A torrent is identified by info hash plus file index when the file index is known.
6. Episode selection must never rely on "largest file" once v2 torrent selection is active.
7. Client adapters translate protocols only. They must not scrape, rank or manage downloads.
8. Legacy AllDebrid-compatible behavior stays isolated until it can call the v2 core safely.
9. Prefer SQLite and the Python standard library for the first usable v2. Add infrastructure only when there is a measured need.
10. New behavior requires tests.

## Implementation order

Work in vertical slices:

1. Domain contracts and deterministic ranking.
2. Provider registry and one development/test provider.
3. Stremio stream endpoint.
4. Torrent metadata and exact movie/episode file selection.
5. qBittorrent streaming session management.
6. Persistent SQLite cache/index.
7. Real providers such as Torznab/Prowlarr.
8. Kodi adapter migration.
9. Optional additional providers and direct-download resolvers.

Do not start by adding many scrapers. A small reliable provider surface is preferred over many brittle integrations.

## Code quality

- Python 3.10+.
- Four-space indentation.
- Type all public APIs.
- Prefer dataclasses and small composable services.
- Avoid global mutable state.
- No broad silent exception handling in new v2 code.
- Network boundaries need explicit timeouts.
- Keep ranking weights and defaults visible and documented.
- Keep provider failures isolated so one provider cannot fail the whole search.

## Before a commit

Run the relevant tests and review the diff for accidental changes to legacy behavior. If a task requires changing one of these invariants, document the reason in the PR before implementing it.
