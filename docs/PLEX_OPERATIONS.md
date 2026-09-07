# Plex integration operations

RelayTV's Plex integration provides account linking, direct server discovery,
server selection, connection testing, personal-library browsing, and direct
playback of movie and episode media that the selected server exposes.

## Set up Plex

1. Open **Settings → Plex Integration** and enable Plex.
2. Choose **Link Plex account**, then open **Continue on Plex**.
3. Approve RelayTV on Plex's site and return to RelayTV.
4. Choose **Check link**.
5. Select a library server and choose **Apply Plex**.
6. Choose **Test server** to confirm the saved connection and PMS version.
7. Choose a playback mode. **Automatic** prefers the original file and uses
   Plex conversion when necessary. **Direct play** always requests the original
   file. **Always transcode** makes Plex convert video and audio for RelayTV.
8. Optionally choose a maximum video bitrate. Automatic mode converts a file
   when PMS determines it exceeds the cap. Original quality removes the cap;
   Direct play ignores it.
9. Close Settings and choose **Plex** in the RelayTV header to browse Home,
   movie and TV libraries, search, details, seasons, and episodes.
10. Open a movie or episode and choose **Play now**, **Resume**, **Play next**,
   or **Add to queue**. Resume appears when Plex reports saved progress; Play
   now starts over.
11. If Plex exposes more than one media version, choose the intended resolution
   and container before starting or queueing it. RelayTV remembers that
   encrypted version reference with the item.
12. When a version exposes multiple audio tracks or subtitles, choose the
    intended audio language and optional subtitle before starting or queueing.
    **Plex default** leaves audio selection to the server; **Off** requests no
    subtitles. Changing the version refreshes its available track choices.

RelayTV uses Plex's Ed25519 device-key and PIN flow. It does not collect a Plex
password. Server discovery prefers direct local connections, with verified
HTTPS connections preferred when a server advertises more than one local
address. Relay connections are excluded in this phase.

Catalog responses expose authenticated encrypted IDs tied to the linked
account and selected server. Artwork is fetched by RelayTV with the saved
server token and returned through a private no-store image route. Plex tokens
and upstream paths are not sent to the browser. Changing the account or
selected server invalidates an in-flight browse result and all older item IDs.

Playback resolves the selected Plex item immediately before it starts. The
player reads it through a private loopback relay that forwards byte ranges for
the original file or a session-bound Matroska stream for Plex conversion, so
neither the browser nor persisted queue/history state receives a Plex token,
raw media-part path, or transcode session. Automatic and Always-transcode modes
require a successful PMS decision before RelayTV changes active playback.
Direct-play mode bypasses that decision and requests the selected original
file. The optional 4, 8, 12, or 20 Mbps maximum is sent to PMS with Automatic
and Always-transcode decisions. Automatic also requests conversion when the
source exceeds the active display height or RelayTV's measured AV1, bit-depth,
software-decoder, or high-bitrate limits. RelayTV sends resume
offsets to the transcoder in seconds and reports playing, paused, and stopped
positions to Plex in milliseconds. Stop, replacement, natural end, HTTP close,
and process shutdown all retire the transient PMS session. Explicit audio or
subtitle selection uses Plex conversion in Automatic and Always transcode
modes, and burned subtitles provide the same result on every RelayTV player
backend. Direct play rejects an explicit track choice because it bypasses
conversion. Track references are encrypted, version-scoped, and persisted with
the item so queued and interrupted playback keeps the requested choice.

## Credential storage and backup

Plex private state is stored in `/data/plex_auth.json` with mode `0600`. The
file contains the device private key, renewable account token, and selected
server token. Treat backups of this file as secrets. The ordinary
`/data/settings.json` file contains the enable switch, selected server machine
identifier, playback mode, and bitrate preference.

Unlinking removes the linked account and server credentials from RelayTV. It
does not currently revoke the device through Plex's account service. You can
remove an authorization separately from the Plex account's authorized-device
controls.

When cloning a RelayTV data directory for another physical device, remove both
`plex_auth.json` and `device_id` from the clone before startup. Each TV should
create its own device identity and Plex authorization.

## Troubleshooting

- **No servers appear:** confirm the linked Plex account can see a Plex Media
  Server and that Remote Access or a local connection is available. Relays are
  intentionally omitted.
- **Server selection fails:** open Plex once, confirm the server reports
  online, then reload RelayTV settings. RelayTV rejects a connection whose
  reported machine identifier does not match the selected server.
- **Authorization expires:** link the account again. RelayTV renews current
  JWT credentials within 24 hours of their expiry and serializes concurrent
  refresh attempts.
- **A timeout occurs during link or refresh:** retry without deleting the
  private state file. Network failures do not erase credentials.
- **Plex does not appear in the header:** finish all three setup steps: enable
  Plex, link an account, and select a server. The browse entry remains hidden
  until the integration is ready.
- **An item becomes unavailable after changing servers:** reload the Plex
  browser. Item and artwork references are intentionally scoped to the server
  that returned them.
- **Playback says no media part is available:** try another advertised media
  version or choose **Always transcode**. RelayTV still requires PMS to expose
  an accessible part for the selected version.
- **Transcoding fails before playback starts:** confirm the Plex server can run
  a transcode and has free temporary storage, then retry. Choosing **Direct
  play** is useful when RelayTV can decode the original file itself.
- **A track choice requires conversion:** switch from **Direct play** to
  **Automatic** or **Always transcode**. RelayTV asks Plex to produce the
  selected audio and burn the selected subtitle into the video.

## Exercised contract

The initial contract was exercised on 2026-09-05 against Plex Media Server
`1.43.3.10828-00f62d37d`. The server returned JSON for identity, media-provider
features, two video library sections, account resources, and direct connection
probing. Its account exposed one owned server with local secure connections;
RelayTV selected one and verified the server machine identifier. A live modern
PIN/JWK request returned the documented 30-minute strong-PIN response shape.

The read-only catalog path was also exercised against that PMS: Home returned
a Recently Added Movies row; library paging reported 286 movies; item detail,
search, and a 131,340-byte JPEG artwork proxy response all completed. The
sanitized RelayTV results contained no upstream `/library/` paths.

The direct media relay contract was exercised against this PMS with a 1,024-byte
range from a Matroska movie; PMS returned `206 Partial Content` and the
expected content range. The same PMS accepted the complete universal media
decision contract, returned a forced-conversion decision, served a
`video/x-matroska` HTTP transcode, and accepted the matching explicit stop.
Stock mpv decoded its first H.264/AAC frame through an isolated RelayTV route
in 1.25 seconds, and RelayTV observed successful PMS cleanup. A live 4 Mbps
decision kept a 1,092 Kbps HEVC file direct and selected transcoding for a
21,516 Kbps file. Full screen/audio
playback, remux-only media, arbitrary seek, and Plex controller behavior are
not yet claimed. Audio/subtitle selection and version revalidation are
fixture-tested. A later live probe against PMS `1.43.3.10896-cb3ebc72d`
selected one embedded subtitle, received a conversion stream, and read its
first 262,144 bytes before RelayTV closed and cleaned up the session.
Alternate-audio, external-subtitle, and screen/audio acceptance remain.
Timeline request shape,
throttling, and lifecycle ordering are fixture-tested; a live watch-history
mutation was deliberately left for playback acceptance.

The runtime-profile boundary is fixture-tested for AV1 conversion and a
copy-only PMS result is retained as remux. On the live 1080p display, the only
above-cap title in the recent 50-item sample was HEVC Dolby Vision Profile 5;
RelayTV requested conversion and PMS explicitly rejected that color space as
unplayable instead of RelayTV attempting unsafe direct playback.

With no active Plex or RelayTV playback, the YAMS PMS container was stopped
between requests from one authenticated RelayTV client. RelayTV returned its
sanitized unreachable error during the outage and the same client reconnected
on the fourth one-second probe after startup. The machine identifier and PMS
version were unchanged, and identity plus both video libraries were available
afterward.

An isolated RelayTV process also accepted an item reference minted by a second
process and served the movie to stock mpv. A cold, null-output first-frame
decode completed in 0.92 seconds without warnings. The active TV session was
left untouched, so screen/audio output and seamless replacement still require
device acceptance.
