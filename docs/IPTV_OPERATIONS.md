# IPTV Operations

RelayTV can import generic extended M3U playlists into a local, searchable
channel catalog. IPTV is disabled by default and makes no provider requests
until it is enabled and an operator adds a source.

## Enable and Add a Source

Open **Settings → IPTV Integration**, enable IPTV, and select **Apply IPTV**.
The IPTV launcher then appears in the RelayTV header.

Inside IPTV, navigation is link-driven with a Back control — there is no tab
bar:

- **My Channels** is the home: the channels you have added, with favorites
  pinned to the top. The star pins a channel and is only available here. My
  Channels keeps showing added channels that become unavailable so you can
  retry or remove them.
- **Discover** browses the full catalog pulled from your enabled sources.
  Each channel has **+** (add to My Channels), Play, and an overflow menu
  (play next, add to queue, check availability); Refresh pulls fresh channels
  from your sources. Reached from the Discover link on My Channels.
- **Sources** adds a playlist (HTTP/HTTPS M3U URL or pasted M3U content), lists
  your sources with refresh/enable/remove plus a Remove-unavailable action, and
  folds in the reviewed free-provider directory below. Adding a preset is
  opt-in; merely opening or searching it does not fetch a playlist. Reached
  from the Sources link on Discover.

The bundled directory contains selected iptv-org country, language, and
category playlists plus Free-TV/IPTV. These projects index publicly available
streams; RelayTV does not host or redistribute their content. Availability,
licensing, geo-restrictions, and provider terms remain the operator's
responsibility.

## Catalog Behavior

Each source refresh is transactional. A failed download or invalid playlist
leaves the last successful catalog available. HTTP sources use ETag and
Last-Modified conditional requests when the provider supports them.

My Channels membership, favorites, and manual order live in RelayTV's SQLite
catalog and survive source refreshes and container restarts. Stable identity
prefers a unique `tvg-id`, then channel name/group, then the stream URL as a
last resort; an existing channel keeps its identity when a duplicate `tvg-id`
later appears, so its membership and favorite are not detached. Changing a
provider's IDs and names can still produce a new channel.

My Channels lists only the channels you have added, favorites first, and keeps
showing added channels that go unavailable or whose source dropped them so you
can retry or remove them. **Remove unavailable** (on the Sources page)
permanently removes unavailable or source-removed records in the selected
source, or in every source when no source filter is selected.

## Availability

An explicit **Check** performs a bounded HTTP GET. A channel transitions to
`suspect` after a failed check and to `unavailable` after three failures. A
later successful check restores it immediately; playing a channel does not
change its recorded availability, because launching the player is not proof the
stream loads. The background worker checks favorites in small sequential
batches; it does not probe an entire imported catalog.

Source refreshes run at their configured interval with per-source jitter.
Failed sources back off to twice that interval. Disabling a source stops both
its refreshes and its background availability checks. A missing channel becomes
inactive immediately but remains in My Channels until the operator removes it.

## Playback and Credentials

Extended M3U `http-user-agent` and `http-referrer` directives are passed to mpv
for that channel. RelayTV returns opaque source/channel IDs from catalog APIs
and resolves the actual stream server-side at play time. Raw playlist and
stream URLs and request headers are omitted from status, queue, history,
session, and IPTV action responses.

The catalog file and uploaded playlist content are stored owner-only at
`/data/iptv.sqlite3` by default. They are not encrypted at rest. Protect the
host filesystem and backups accordingly. `RELAYTV_API_TOKEN`, when configured,
protects all IPTV mutations through the same write-auth middleware as other
RelayTV controls.

## Environment Tuning

| Variable | Default | Purpose |
| --- | ---: | --- |
| `RELAYTV_IPTV_ENABLED` | `0` | Initial enabled state; the Settings toggle applies live. |
| `RELAYTV_IPTV_DB_PATH` | `/data/iptv.sqlite3` | SQLite catalog location. |
| `RELAYTV_IPTV_FETCH_TIMEOUT_SEC` | `15` | Playlist request timeout. |
| `RELAYTV_IPTV_PROBE_TIMEOUT_SEC` | `8` | On-demand/background channel check timeout. |
| `RELAYTV_IPTV_MAX_PLAYLIST_BYTES` | `20971520` | Maximum playlist response/upload size. |
| `RELAYTV_IPTV_MAX_CHANNELS` | `100000` | Maximum parsed channels per playlist. |
| `RELAYTV_IPTV_CHECK_INTERVAL_SEC` | `21600` | Minimum interval between favorite checks. |
| `RELAYTV_IPTV_CHECK_BATCH` | `3` | Favorite checks per worker pass (maximum 20). |

Set environment values in the `.env` in your RelayTV directory, then recreate
the container for env-only changes. Enabling/disabling through Settings is live
and persisted.

## Troubleshooting

- **Source refresh fails:** verify the URL from the RelayTV host and inspect the
  source's safe error type. Credentials and source URLs are intentionally not
  logged or returned.
- **A public channel will not play:** it may be offline, geo-blocked, require a
  provider-specific header, or disallow your network. Use Check, then try the
  provider's official player.
- **A channel is missing from My Channels:** you may not have added it — add it
  from Discover with **+**. Added channels that go inactive or unavailable still
  appear, marked with a status badge, so you can retry Check or Remove them.
- **Reset the catalog:** stop RelayTV, back up and remove `data/iptv.sqlite3`
  in your RelayTV directory, then start RelayTV. This removes sources, My
  Channels membership, favorites, and ordering.
