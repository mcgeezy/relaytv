# IPTV Catalog Research and Implementation Plan

Status: proposal

Research date: 2026-07-20

## Milestone Status

- [x] M0 — research, architecture, security model, and delivery plan
- [x] M1 — SQLite catalog, M3U ingestion, source discovery, and catalog APIs
- [ ] M2 — favorites, visibility, manual ordering, availability, and playback
- [ ] M3 — responsive IPTV browse/manage UI and browser smoke coverage
- [ ] M4 — container rebuild, end-to-end verification, and operator docs
- [ ] Later — XMLTV now/next guide data after direct-catalog runtime soak

## Recommendation

Add IPTV as a native, opt-in catalog integration that imports one or more
operator-supplied M3U playlists. Keep the imported playlist immutable and store
RelayTV's display choices as overlays: hidden state, manual rank, and optional
display-name overrides. This makes channel lists sortable and hideable without
losing those choices each time an upstream playlist refreshes.

Use SQLite (from Python's standard library) for the normalized catalog and
overlays. A source can contain thousands of channels, so indexed queries,
pagination, transactional refreshes, and small per-channel updates are a better
fit than rewriting a large JSON document. IPTV playback should still produce a
normal RelayTV media item and use `playback_service.py` for every play or queue
transition.

Start with channel browsing and playback. Treat XMLTV program-guide data as a
follow-up once source refresh, ordering, hiding, and direct playback are solid.
Include first-class `Discover`, `My Channels`, and `Favorites` views.
Availability should be soft state: RelayTV hides repeatedly unavailable
streams from normal browsing but retains their identity and operator choices so
a recovered stream can return without losing its rank or favorite state.

## Product Shape

The first useful release should support:

- multiple named M3U/M3U8 playlist sources over HTTP/HTTPS
- a searchable, opt-in directory of curated free/public source presets,
  including iptv-org country, language, and category playlists
- playlist upload for operators who do not have a hosted URL
- manual and scheduled refresh with last-success and error status
- channel name, group, logo, `tvg-id`, and upstream order
- search, group filtering, and visible/hidden filtering
- source order, alphabetical order, group/name order, and persistent manual
  order
- hide/show one channel and bulk hide/show a group
- favorite/unfavorite actions and a dedicated Favorites view across sources
- a `My Channels` view containing only sources and channels selected by the
  operator
- availability states that suppress repeatedly unavailable streams without
  permanently deleting their favorites, order, or visibility overrides
- play now, play next, and play last through existing RelayTV transitions
- a management mode that reveals hidden channels without mixing them into the
  normal browse view
- mobile/PWA-friendly channel tiles and a paginated or virtualized list

Do not include DVR, recording, timeshift, catch-up, Xtream Codes, Stalker
portals, DRM license handling, stream restreaming, or aggressive continuous
probing of every catalog entry in the first release. Those features change
RelayTV from a receiver into IPTV middleware and are already handled by projects
such as [Threadfin](https://github.com/Threadfin/Threadfin).

## Why This Approach

The local `~/awesome-iptv/README.md` inventory points to three recurring
patterns:

1. Players commonly accept M3U/M3U8, group channels, search, retain favorites,
   and optionally consume XMLTV.
2. Editors and middleware add filtering, ordering, mapping, automatic refresh,
   and proxying. Threadfin explicitly separates source files from channel
   filtering, order, logos, categories, and guide mapping.
3. Public catalogs are large and volatile. The
   [iptv-org playlist index](https://github.com/iptv-org/iptv/blob/master/PLAYLISTS.md)
   offers global, category, language, and country variants; several individual
   variants already contain hundreds or thousands of entries.

RelayTV needs the first pattern and a small part of the second, not an embedded
headend. A native overlay model gives the requested ordering and visibility
controls while leaving proxying and advanced guide management to dedicated
software.

## Alternatives Considered

| Approach | Strengths | Costs and gaps | Decision |
| --- | --- | --- | --- |
| Native generic M3U catalog | Works with public and private sources; small conceptual fit; RelayTV owns the UI | Requires parser, refresh state, stable IDs, and management endpoints | Recommended |
| iptv-org API-only catalog | Rich country, category, language, logo, feed, and header metadata | Ties the product to one catalog and does not serve private/operator playlists | Add later as an optional preset/adapter |
| Threadfin/xTeVe integration | Mature filtering, ordering, XMLTV mapping, proxying, and tuner controls | Another service to deploy; its UI duplicates the requested RelayTV experience | Document as an advanced external source |
| Jellyfin Live TV only | Jellyfin already accepts M3U and XMLTV | Requires a Jellyfin server and does not provide a native RelayTV source manager; RelayTV's current Jellyfin catalog is movie/series-oriented | Keep as an operator alternative |
| Store edited M3U text | Easy to export and inspect | Rewriting generated text loses upstream identity and makes refresh conflict-prone | Do not use as authoritative state |

Jellyfin's official setup guide confirms the useful interoperability baseline:
an M3U tuner accepts a local file or HTTP URL, optional user agent, and stream
limit, while XMLTV is configured and mapped separately. See
[Jellyfin Live TV setup](https://jellyfin.org/docs/general/server/live-tv/setup-guide/).

## Proposed User Experience

Add an `IPTV` launcher beside the existing Jellyfin launcher. When no source is
enabled it opens `Discover`; otherwise it remembers the most recent IPTV view.

The IPTV shell should have four top-level views:

- `Discover`: search curated free/public source presets by name, country,
  language, and category, then explicitly add one to RelayTV
- `My Channels`: browse channels from sources the operator has added
- `Favorites`: show favorited channels across all selected sources
- `Manage`: edit sources, manual order, hidden channels, unavailable channels,
  and refresh status

The normal IPTV view should contain:

- source selector
- search field
- group selector
- sort selector (`Manual`, `Playlist`, `Name`, `Group`)
- channel count and refresh status
- channel tiles with logo, name, group, and an action menu
- favorite toggle plus `Available`, `Checking`, or `Unavailable` status when
  RelayTV has enough evidence to assign one
- `Play`, `Play next`, and `Play last` actions

The management view should add:

- `Visible`, `Hidden`, and `All` filters
- drag handles when `Manual` sorting is active
- hide/show buttons
- bulk group visibility actions
- a `Refresh source` action and source-level error detail

Dragging should send one relative move (`before_channel_id` or
`after_channel_id`) rather than the full visible list. This remains safe when
the UI is filtered or paginated. Keyboard-accessible move up/down controls
should perform the same operation.

New channels discovered by a refresh append to the existing manual order.
Previously known channels retain rank, visibility, and favorite state.
Temporarily missing or repeatedly failing channels become inactive/unavailable
rather than immediately losing their overrides, so a later reappearance or
successful check restores the operator's choices.

## Free Source Discovery

Use the word `source` in the data model and UI detail because many free lists
are community catalogs rather than television providers. `Discover free
providers` can remain user-facing copy where it is clearer.

The discovery directory should be a small, versioned allowlist of preset
definitions, not a scraper or a bundled copy of stream URLs. Each definition
contains a stable preset ID, display name, homepage, playlist template, source
type, country/language/category facets, and a short provenance/legal note. The
directory returns metadata only; RelayTV must not fetch a playlist until the
operator selects `Add`.

Start with:

- [iptv-org](https://github.com/iptv-org/iptv) country, language, and category
  playlist templates, backed by its published playlist/API metadata
- [Free-TV/IPTV](https://github.com/Free-TV/IPTV) as a separate curated
  free-to-air source preset
- `Custom M3U` for an operator-provided URL or upload

Search should cover source name, country, language, category, and description.
Adding a preset creates an ordinary `iptv_sources` row, after which it behaves
exactly like a custom source. Do not label a source `free` solely because it
appears in the local awesome list: inclusion there is discovery evidence, not a
license audit. Each preset needs a maintainer-reviewed provenance note, and
operators remain responsible for regional access rules.

No preset is selected by default, and opening `Discover` performs no stream
health checks. This preserves the no-background-network behavior until an
operator makes a choice.

## M3U Ingestion

Parse an extended M3U channel entry into these fields:

- entry title and duration from `#EXTINF`
- `tvg-id`, `tvg-name`, `tvg-logo`, and `group-title`
- group fallback from `#EXTGRP`
- stream URL
- supported request metadata such as VLC/Kodi user-agent and referrer hints
- source position

An `.m3u8` suffix alone does not prove that a URL is a channel catalog. Reject
HLS media/master manifests as sources when HLS-only tags such as
`#EXT-X-TARGETDURATION` or `#EXT-X-STREAM-INF` are present without catalog
entries. The stream URLs inside a catalog may still point to HLS manifests.

Fetch and parse defensively:

- allow HTTP and HTTPS source URLs only; accept local content through an upload
  endpoint rather than arbitrary server file paths
- use bounded connect/read timeouts, redirect count, response bytes, line
  length, and channel count
- accept UTF-8 with BOM and fall back carefully for common legacy encodings
- validate each stream URL and skip malformed entries with aggregate warnings
- construct a complete candidate catalog before changing the active catalog
- commit a refresh in one SQLite transaction; keep the last good catalog on
  fetch or parse failure
- record an ETag and Last-Modified value when supplied and use conditional
  refresh requests

A practical initial ceiling is 20 MiB and 100,000 entries per source, with both
limits configurable later only if real deployments need it.

## Stable Channel Identity

Ordering and hidden state must survive URL token rotation and upstream
reordering. IDs should be source-scoped and derived in this order:

1. a unique non-empty `tvg-id`
2. a unique normalized tuple of `tvg-name` or title plus `group-title`
3. a hash of the normalized title, group, and stream URL for ambiguous entries

Store the identity inputs as well as the derived ID. During refresh, reconcile
against exact `tvg-id` first, then the unique name/group key. Never use list
position as identity. If multiple entries still collide, keep them distinct by
including a URL hash and report the ambiguity in refresh diagnostics.

This is intentionally source-scoped: two providers may use the same `tvg-id`
for different streams or credentials.

## Storage Model

Create `/data/iptv.sqlite3` (or a path configured through the runtime config
service) with schema migrations from the start. Suggested tables:

```text
iptv_sources
  id, name, kind, location, enabled, refresh_interval_sec,
  last_attempt_at, last_success_at, etag, last_modified, last_error

iptv_channels
  source_id, channel_id, tvg_id, tvg_name, name, group_title, logo_url,
  stream_url, user_agent, referrer, upstream_index, manual_rank,
  hidden, favorite, active, availability, consecutive_failures,
  last_checked_at, last_available_at, first_seen_at, last_seen_at

iptv_source_groups
  source_id, group_title, hidden_override

iptv_schema
  version
```

Use a composite primary key of `(source_id, channel_id)` and indexes for source
plus manual rank, normalized name, group, visibility, and active state.

Assign manual ranks with gaps, for example increments of 1024. A move usually
uses the integer midpoint between neighbors. Rebalance ranks for one source in
a transaction only when no gap remains. Source refresh updates catalog fields
but never overwrites `manual_rank`, `hidden`, `favorite`, or group overrides on
a matched channel.

SQLite should be an IPTV-owned store, not an expansion of `state.py`'s queue,
history, session, and settings globals. This keeps catalog churn away from
playback state and establishes an explicit versioned model rather than another
implicit JSON shape.

## Security and Privacy

Playlist locations and stream URLs often contain usernames, passwords, or
short-lived tokens. Treat both as secrets even though
`RELAYTV_API_TOKEN` remains the only operator authentication secret covered by
the existing env-only contract.

- create the database and uploaded source files with owner-only permissions
- never log source locations, stream URLs, headers, or fetch response bodies
- return opaque source/channel IDs from catalog endpoints, not raw stream URLs
- return `location_configured`, a safe hostname, and source status rather than
  the stored source URI
- resolve the raw stream URL server-side when a channel action is invoked
- pass item headers internally and rely on `public_media.py` to strip them
- extend public media serialization for IPTV so credentials embedded in URL
  path segments cannot appear in queue, history, status, or event payloads
- protect every source, refresh, visibility, reorder, and playback mutation
  with the existing optional write-auth middleware
- keep API behavior unchanged when IPTV is disabled or has no sources

Private/LAN playlist hosts are a valid use case, so a blanket private-address
block would be counterproductive. Source fetching should occur only for stored,
operator-created sources; do not expose a read endpoint that fetches an
arbitrary query-string URL.

Uploaded and UI-created source credentials can be stored server-side because
they must survive refresh, but the lack of encryption at rest should be stated
in operator documentation. An optional env-only bootstrap source can be added
for deployments that do not want a credential-bearing source URI persisted.

## Availability and Removal Policy

`Unavailable` must not mean `one request failed`. Public streams are commonly
geo-restricted, time-limited, temporarily offline, or hostile to `HEAD`
requests. The iptv-org maintainers also note that their former automatic
online/offline checking is no longer available, so RelayTV cannot assume every
published entry is currently reachable. See
[How iptv-org works](https://github.com/orgs/iptv-org/discussions/1318).

Use these states:

```text
unknown -> checking -> available
                    -> suspect -> unavailable
                    -> geo_blocked
```

Evidence and behavior:

- disappearance from a successfully refreshed source marks the channel
  `inactive` immediately and removes it from `My Channels`; a favorite remains
  visible in Favorites with an unavailable badge
- an actual successful playback marks the channel available
- a playback failure attributable to the stream increments its failure count;
  local display or mpv startup failures do not
- an explicit `Check` action performs a bounded HLS/HTTP probe and records the
  result
- scheduled checking covers favorites and selected visible channels first;
  large unselected catalogs are sampled or left `unknown`, not hammered
- mark a channel unavailable only after three attributable failures separated
  across checks; a later success restores it automatically
- physical deletion is delayed by a retention window, for example 30 days
  inactive, and retains a small override tombstone so re-added channels recover
  favorite, hidden, and manual-rank state

Normal channel search excludes inactive/unavailable entries by default and
offers `Include unavailable`. Favorites always keeps unavailable selections
visible so the user can retry, replace, or unfavorite them. A `Remove
unavailable` management action can purge selected stale entries immediately,
but automatic cleanup remains conservative.

Probe implementation should use a short bounded `GET`, not rely on `HEAD`.
For HLS, validate the manifest and optionally the first media segment within a
strict byte/time budget. Never open every stream concurrently; use a small
worker limit, per-host throttling, jitter, and backoff.

## Playback Integration

An IPTV channel action should resolve an internal item resembling:

```json
{
  "provider": "iptv",
  "title": "Example News",
  "channel": "News",
  "thumbnail": "https://example.invalid/logo.png",
  "url": "https://stream.example.invalid/live.m3u8",
  "iptv_source_id": "source-id",
  "iptv_channel_id": "channel-id",
  "http_headers": {
    "User-Agent": "...",
    "Referer": "..."
  }
}
```

The route passes that item to the IPTV product service, which calls only
`playback_service.play_now()` or `playback_service.queue_item()`. The IPTV
service must not write `NOW_PLAYING`, queue state, session state, or auto-next
state itself.

Direct HLS and MPEG-TS URLs should bypass yt-dlp. `player.py` remains the
process/control adapter and needs a narrow per-item request-header input for
streams that require a user agent or referrer. Header support must work both
for initial process startup and `loadfile` queue handoff; otherwise a channel
may play initially and fail when reached through the queue. Header values must
not appear in debug command logging.

Queue/history persistence currently normalizes a limited media-item shape.
Implementation must preserve the opaque IPTV channel reference needed for a
later queue advance, while avoiding public serialization of the raw stream
location. Prefer re-resolving the channel from the IPTV catalog at playback
time so refreshed tokens and URLs are used.

## Module Boundaries

Suggested ownership:

- `app/relaytv_app/integrations/iptv_service.py`: M3U fetch/parse, refresh
  reconciliation, catalog queries, source/channel mutations, and construction
  of playable items
- `app/relaytv_app/integrations/iptv_store.py`: SQLite schema, migrations, and
  transactional repository operations
- `app/relaytv_app/routes/iptv.py`: request models, HTTP validation, endpoint
  registration, redacted response shaping, and UI events
- `app/relaytv_app/static/ui/iptv.js` and `iptv.css`: browse and management UI
- `app/relaytv_app/playback_service.py`: unchanged owner of playback
  transitions
- `app/relaytv_app/player.py`: per-item transport/header application only
- `app/relaytv_app/config.py`: typed enablement, database-path, refresh, and
  ingestion-limit settings

The IPTV service must not import the routes package. Route aliases remain route
owned. A new public route surface should be added to the route inventory rather
than folded into Jellyfin endpoints.

## Proposed API Surface

Names are provisional, but channel actions should be ID-based:

```text
GET    /iptv/sources
GET    /iptv/directory
POST   /iptv/directory/{preset_id}/add
POST   /iptv/sources
PATCH  /iptv/sources/{source_id}
DELETE /iptv/sources/{source_id}
POST   /iptv/sources/{source_id}/refresh

GET    /iptv/channels
PATCH  /iptv/channels/{channel_id}
POST   /iptv/channels/visibility
POST   /iptv/channels/reorder
POST   /iptv/channels/{channel_id}/check
POST   /iptv/channels/{channel_id}/action
```

`GET /iptv/channels` should accept `source_id`, search text, group, visibility,
favorites-only, availability, sort, cursor, and limit. It should return
redacted display objects and a stable cursor. The channel patch accepts
`favorite` as well as display/visibility overrides. The action body can use the
existing vocabulary:
`play_now`, `play_next`, and `play_last`.

Bulk visibility requests should identify a source and explicit group or list
of channel IDs. Reorder requests should identify one channel and one adjacent
anchor. Avoid accepting an entire replacement catalog from the browser.

## EPG Follow-up

XMLTV should be a second-stage, read-only guide overlay:

1. add an optional XMLTV URL or upload to each source
2. fetch with the same bounded, last-known-good rules as M3U
3. parse incrementally so a large guide does not need to be held entirely in
   memory
4. map exact `tvg-id` first
5. allow explicit operator mappings for unmatched channels
6. store only a bounded guide window, such as now through seven days
7. expose current/next program data in channel tiles before attempting a full
   grid guide

Do not perform silent fuzzy guide mapping. Incorrect program data is harder to
diagnose than a clearly unmapped channel. Jellyfin also models XMLTV setup and
channel mapping as separate operations, which supports this boundary.

The [iptv-org API](https://github.com/iptv-org/api) can enrich a future public
catalog adapter: `channels.json`, `feeds.json`, `logos.json`, `streams.json`,
and `guides.json` expose joinable metadata including country, categories,
languages, quality, referrer, and user agent. Generic M3U remains the primary
contract.

## Delivery Plan

### PR 1: Catalog foundation

Suggested title: `feat: add IPTV source catalog`

- add the disabled-by-default runtime config and environment inventory entries
- add SQLite schema/migrations, M3U parser, bounded fetcher, stable identity,
  and transactional refresh
- add redacted source/channel read APIs and source management APIs
- cover parsing, refresh rollback, reconciliation, secret redaction, source
  limits, and migration behavior

Exit criteria: two sources can refresh independently; a failed refresh leaves
the last good catalog queryable; no catalog response exposes raw source or
stream credentials.

### PR 2: Browse, favorites, ordering, hiding, and playback

Suggested title: `feat: add IPTV channel browsing and playback`

- add IPTV UI launcher, browse view, management mode, search/filter/pagination,
  favorites view, visibility controls, and accessible manual reorder
- add ID-based channel actions and playback item construction
- add queue-safe IPTV references and per-item user-agent/referrer transport
  handling
- add UI/API/playback tests and update the route and transition inventories

Exit criteria: favorites, hide, and manual order survive refresh and restart;
a new channel appends without disturbing existing manual order; Favorites spans
multiple selected sources; play-now and queued handoff work for direct HLS with
and without supported request headers.

### PR 3: Free-source discovery and availability

Suggested title: `feat: add IPTV source discovery and availability`

- add scheduled refresh with jitter and backoff
- add source diagnostics, conditional requests, stale/inactive cleanup policy,
  and operator documentation
- add the searchable, opt-in free-source directory, including iptv-org
  country/language/category templates and the reviewed Free-TV/IPTV preset
- add bounded on-demand checks, favorite/selected-channel scheduled checks,
  failure thresholds, recovery, and unavailable cleanup controls
- validate performance with 10,000 and 100,000 synthetic entries

Exit criteria: refresh does not block playback/status handling; catalog queries
remain responsive at the supported ceiling; unavailable channels leave normal
search only after the threshold and automatically return after success;
disabled/default installs make no new network requests.

### Later PR: XMLTV now/next

Suggested title: `feat: add XMLTV now and next guide data`

Only begin after the direct catalog workflow has runtime soak evidence.

## Test Plan

Add focused tests for:

- extended M3U quoting, BOM, CRLF, blank fields, relative/malformed URLs,
  duplicate IDs, duplicate titles, header directives, and HLS-manifest
  rejection
- fetch timeout, redirect and size limits, conditional requests, failed refresh
  rollback, and concurrent refresh coalescing
- stable identity across upstream reorder and URL rotation
- persistence of hidden/group/manual-rank state across refresh and restart
- persistence of favorites across refresh, temporary source disappearance,
  inactive retention, and later recovery
- rank rebalance and filtered/paginated relative moves
- source isolation when identical channel IDs exist in two sources
- redaction of query, userinfo, path credentials, request headers, source URLs,
  queue, history, status, logs, and UI events
- API auth behavior with `RELAYTV_API_TOKEN` set and unset
- play-now, play-next, play-last, natural queue advance, and header application
  on both initial startup and `loadfile`
- discovery search/facets, explicit preset selection, zero pre-selection
  network activity, and preset provenance metadata
- availability attribution, failure thresholds, per-host throttling, recovery,
  favorite unavailable display, and delayed cleanup/tombstones
- 10,000/100,000-entry import and paginated query performance
- browser smoke for source discovery/selection, Favorites, search, hide/show,
  unavailable display, reorder, and play

Before each implementation PR finishes, run the repository gates:

```text
ruff check app tests
PYTHONPATH=app pytest -q
git diff --check
```

Regenerate the environment, route, transition, runtime, or integration
inventories through their owning tests when their checked surfaces change.

## Decisions to Confirm Before Implementation

The recommended defaults are:

- generic M3U first, with iptv-org as an optional preset rather than a special
  product mode
- a searchable free-source directory with no preset selected or fetched by
  default
- SQLite catalog plus overlay state
- multiple sources in the first release
- `Discover`, `My Channels`, `Favorites`, and `Manage` views
- soft removal after repeated attributable failures, delayed physical cleanup,
  and automatic recovery
- no EPG in the first release; add now/next before a full grid
- no default or bundled channel list and no background network activity until
  an operator enables IPTV and adds a source
- persistent server-side source credentials with strict redaction, plus an
  env-only bootstrap option for operators who require it
- Threadfin/Jellyfin interoperability through ordinary M3U sources rather than
  custom coupling

These choices keep the first release narrowly focused on the original need:
reliable, sortable, hideable channel lists that play through RelayTV's existing
receiver and queue model.
