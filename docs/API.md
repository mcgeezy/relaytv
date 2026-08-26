# RelayTV API

Base URL (default): `http://<host>:8787`

RelayTV serves its HTTP API from the root path. Most endpoints return JSON. HTML, SVG, image, SSE, and WebSocket endpoints are called out explicitly below.

This file is the active endpoint reference for the native Qt runtime. Historical compat-only endpoints are removed from the active tree and are not documented here.

## Network trust model

RelayTV's API is intended for trusted LAN use. Write endpoints can control
playback, mutate queue/history state, upload local media, change settings, send
TV notifications, and interact with Jellyfin. Do not expose RelayTV directly to
the public internet. Use a VPN, trusted reverse proxy, or Home Assistant access
layer if remote access is required.

### Optional API token

By default every endpoint is open (trusted LAN). Setting `RELAYTV_API_TOKEN`
in the `.env` in your RelayTV directory enables bearer auth for all write
requests (`POST`/`PUT`/`PATCH`/`DELETE`):

```bash
# .env
RELAYTV_API_TOKEN=use-a-long-random-value
```

```bash
docker compose up -d --force-recreate relaytv
```

With the token enabled:

- Write requests must send the header, or they receive
  `401 {"detail": "api token required"}` with `WWW-Authenticate: Bearer`:

  ```bash
  curl -X POST http://<host>:8787/pause \
    -H "Authorization: Bearer use-a-long-random-value"
  ```

- Reads stay open: all `GET` endpoints (`/health`, `/status`, `/ui`, static
  assets, SSE streams, Jellyfin browse) behave exactly as before, so
  dashboards and health checks keep working.
- The web UI prompts for the token on the first rejected control action and
  stores it in browser localStorage (`relaytv_api_token`); it can also be
  pre-set from the browser console with
  `localStorage.setItem('relaytv_api_token', '<token>')`.
- The token is env-only. It is never persisted with settings and never
  returned by `/settings`.

Unset or blank `RELAYTV_API_TOKEN` restores the fully open behavior.

Clients can validate write credentials without changing server state by
calling `POST /auth/check`. A successful response is
`{"ok": true, "token_required": true|false}`; an incorrect or missing token on
a protected server receives the same `401` response as other writes.

The token protects control actions; it is not transport security. For
exposure beyond the trusted LAN, terminate TLS and (optionally) an extra
auth layer at a reverse proxy. Example with Caddy:

```caddyfile
relay.example.com {
    reverse_proxy 127.0.0.1:8787
}
```

Example with nginx:

```nginx
server {
    listen 443 ssl;
    server_name relay.example.com;
    # ssl_certificate / ssl_certificate_key ...

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Required for WebSocket realtime delivery:
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        # Required for /ui/events (Server-Sent Events):
        proxy_buffering off;
        proxy_read_timeout 1h;
    }
}
```

RelayTV's stock Uvicorn process trusts forwarded headers from loopback, which
matches the example above. If the reverse proxy reaches RelayTV from another
source address, trust only that proxy address with Uvicorn's
`--forwarded-allow-ips` option; forwarded scheme headers from untrusted clients
must not control the WebSocket origin check.

## UI and utility endpoints

- `GET /ui`: main web UI HTML
- `GET /realtime/capabilities`
  - uncached discovery document for the versioned realtime protocol
  - clients select only transports whose `enabled` field is true
  - a `404` identifies a legacy server and clients should use SSE or polling
- `WEBSOCKET /ui/ws`
  - preferred versioned live-state channel for the browser UI and native clients
  - requires the `relaytv.realtime.v1` WebSocket subprotocol
  - server-to-client only; playback commands remain authenticated HTTP writes
- `GET /ui/events`
  - compatible Server-Sent Events fallback for the main web UI and companion clients
- `GET /static/ui/{asset_name}`: static CSS/JavaScript used by `/ui`
- `GET /idle`: idle dashboard HTML
- `GET /`: redirects to `/ui`
- `GET /health`: `{"ok": true}`
- `GET /app/info`: returns RelayTV version, image revision, changelog/release
  links, and a cached GitHub latest-release update check for the About UI.
- `GET /manifest.json`: PWA manifest
- `GET /sw.js`: service worker
- `GET /thumbs/{filename}`: cached thumbnail image
- `GET /snapshots/{filename}`: saved JPEG snapshot
- `GET /postlive/{token}.mkv`: internal loopback stream for still-processing
  YouTube replays (see `POSTLIVE_REPLAY.md`). The player mints a
  single-use token per playback and hands the URL to mpv; the stream is
  progressive-only, so a consumed, unknown, or superseded token returns 404.
  Once the underlying download completes, the player swaps mpv onto the
  finalized local spool file (gaining seeking) and this route drops out.
  Not part of the public API surface — clients should never call it.
- `GET /snapshot` and `POST /snapshot`: capture a snapshot of active playback
- `GET /share`
  - query: `url` or `link`, optional `cec`
  - share-target compatible immediate play helper

Selected SVG/asset helpers also exist for the UI:

- `GET /qr/connect.svg`
- `GET /assets/logo.svg`
- `GET /assets/banner.svg`
- `GET /assets/banner.png`
- `GET /pwa/brand/logo.svg`
- `GET /pwa/brand/banner.svg`
- `GET /pwa/brand/banner.png`
- `GET /pwa/weather/{asset_name}`
- `GET /pwa/icon.svg`
- `GET /pwa/splash.svg`
- `GET /pwa/jellyfin.svg`
- `GET /pwa/emby.svg`
- `GET /pwa/{asset_path:path}`
- `GET /favicon.ico`

`WEBSOCKET /ui/ws` is the preferred browser-state push path. `GET /ui/events`
preserves the compatible SSE payload contract for older or WebSocket-blocked
clients. Neither transport is a durable event log or supports replay cursors.
The contract is:

- snapshot events remain authoritative
- hint events trigger targeted refresh/render work
- clients should reconnect on disconnect and keep `/status` as bootstrap/fallback

Current UI realtime event types:

- `hello`
  - initial connection confirmation
- `ping`
  - keepalive event when no other data was emitted recently
- `playback`
  - compact fast snapshot derived from `/playback/state`
  - intended for hot now-playing/progress/volume/session updates
- `status`
  - full server-authoritative snapshot equivalent to `/status`
- `queue`
  - queue mutation hint with current queue snapshot and `queue_length`
  - currently emitted for add, remove, move, dedupe, clear, and Jellyfin queue mutations
- `jellyfin`
  - Jellyfin browse/runtime refresh hint
  - currently emitted for connect, disconnect, register, catalog cache clear, play, and queue-only Jellyfin actions

Clients should treat `status` as the authoritative full-state refresh, use `playback` for fast-path UI updates, and treat `queue` / `jellyfin` as immediate refresh hints rather than a standalone source of truth.

WebSocket messages use this envelope:

```json
{
  "version": 1,
  "event": "playback",
  "sequence": 42,
  "timestamp": 1787420000.0,
  "data": {}
}
```

Clients request the `relaytv.realtime.v1` subprotocol and refresh `/status`
after reconnect or a sequence gap. Application events are delivered in
increasing sequence order; clients should discard a duplicate or older
application event. A `ping` may repeat or observe the latest sequence without
advancing the client's last applied application sequence. Browser handshakes
must be same-origin. Origin-less native clients are accepted because this
channel exposes the same open read data as `GET /status`; native clients may
still send their configured bearer header. Bearer credentials must be sent in
the `Authorization` header and must never be placed in a WebSocket URL or query
string.

## IPTV catalog and playback

IPTV is disabled by default. Source and directory reads remain available for
management, while channel browse/check/play calls return `503` until IPTV is
enabled. Catalog responses expose opaque IDs and metadata, never playlist or
stream URLs or request headers.

- `GET /integrations/iptv/status`
- `GET /iptv/directory?q=` and `POST /iptv/directory/{preset_id}/add`
- `GET /iptv/sources`
- `POST /iptv/sources`
  - body: `{"name", "location"?, "content"?, "refresh_interval_sec"?, "refresh_now"?}`
  - supply either an HTTP/HTTPS `location` or pasted M3U `content`
- `PATCH /iptv/sources/{source_id}` and `DELETE /iptv/sources/{source_id}`
- `POST /iptv/sources/{source_id}/refresh`
- `GET /iptv/channels`
  - filters: `source_id`, `q`, `group`, `visibility`, `favorites`, `added_only`,
    `availability`, `include_unavailable`, `sort`, `offset`, `limit`
  - `added_only` returns only channels added to My Channels; `favorites` returns
    only pinned favorites
  - sorts: `manual`, `playlist`, `name`, `group`
- `PATCH /iptv/channels/{channel_id}`
  - body: `{"source_id", "favorite"?, "added"?, "hidden"?}`
  - `added` toggles My Channels membership; `favorite` pins within My Channels
- `POST /iptv/channels/visibility`
  - body: `{"source_id", "group", "hidden"}`
- `POST /iptv/channels/reorder`
  - body: `{"source_id", "channel_id", "before_channel_id"?}` or
    `{"source_id", "channel_id", "after_channel_id"?}`
- `POST /iptv/channels/remove-unavailable`
  - body: `{"source_id"?}`; an empty source selects all sources
- `POST /iptv/channels/{channel_id}/check`
  - body: `{"source_id"}`
- `POST /iptv/channels/{channel_id}/action`
  - body: `{"source_id", "command": "play_now"|"play_next"|"play_last"}`

See `IPTV_OPERATIONS.md` for source, availability, and credential behavior.

## Playback and session control

Primary play-family endpoints:

- `POST /play`
  - body: `{"url", "use_ytdlp"?, "cec"?}`
  - immediate play, clears queue
- `POST /smart`
  - body: same as `/play`
  - if already playing, enqueues; otherwise plays immediately
- `POST /play_now`
  - body: `{"url", "preserve_current"?, "preserve_to"?, "resume_current"?, "reason"?, "title"?, "thumbnail"?}`
  - immediate play with optional preserve-current semantics
- `POST /play_temporary`
  - body: `{"url", "resume"?, "resume_mode"?, "timeout_sec"?, "volume_override"?}`
- `POST /play_temporary/cancel`
- `POST /play_at`
  - body: `{"url", "start_at": epoch_seconds}`

## Uploaded media ingest

RelayTV supports direct media upload for Android share targets and other local automations that have file bytes instead of a public URL. Upload clients should not send Android `content://` URIs to RelayTV and should not weaken the normal URL validators. Send file bytes to the ingest endpoints below, then use the returned RelayTV media URL for playback or queueing.

All ingest endpoints accept `multipart/form-data`:

- `file`: required uploaded media file
- `title`: optional display title

Supported uploads are selected by MIME type and/or safe file extension. Current accepted media families include:

- video: `video/mp4`, `video/webm`
- audio: `audio/mpeg`, `audio/mp4`, `audio/m4a`, `audio/aac`, `audio/ogg`, `audio/opus`, `audio/wav`, `audio/flac`
- Ogg/generic: `application/ogg`
- `application/octet-stream` when the filename has an allowed media extension such as `.mp3`, `.m4a`, `.aac`, `.wav`, `.flac`, `.ogg`, `.opus`, `.mp4`, `.m4v`, or `.webm`

Upload endpoints:

- `POST /ingest/media`
  - stores the uploaded file and returns a RelayTV-local media URL
  - does not automatically queue or play the item
  - use the returned `url` with existing `/enqueue` or `/play_now` flows
- `POST /ingest/media/enqueue`
  - stores the uploaded file and appends it to the queue in one call
  - response includes `action: "enqueue"` and `result` from queue insertion
- `POST /ingest/media/play`
  - stores the uploaded file and starts playback in one call
  - for eligible `video/mp4` and `video/webm`, RelayTV may start playback progressively once enough bytes have arrived and the upload remains healthy
  - if progressive start is not safe, RelayTV falls back to full-upload-before-play and may show a toast: `Upload still in progress. Waiting for full file for reliable playback.`
- `GET /media/uploads/{upload_id}/{filename}`
  - serves the stored file URL returned by ingest
  - returns `410` when the upload expired or was removed
  - returns `404` when the filename does not match the stored upload metadata
  - uses `Cache-Control: private, max-age=60`

Typical `POST /ingest/media` response:

```json
{
  "ok": true,
  "media_id": "u_0123456789abcdef0123",
  "media_path": "/media/uploads/u_0123456789abcdef0123/clip.mp4",
  "url": "http://relaytv.local:8787/media/uploads/u_0123456789abcdef0123/clip.mp4",
  "item": {
    "url": "http://relaytv.local:8787/media/uploads/u_0123456789abcdef0123/clip.mp4",
    "provider": "upload",
    "title": "Shared Clip",
    "mime_type": "video/mp4",
    "size_bytes": 123456
  },
  "cleanup": {
    "removed": 0
  }
}
```

Typical direct-play response adds:

```json
{
  "playback_mode": "progressive",
  "fallback_reason": "",
  "now_playing": {
    "provider": "upload",
    "title": "Shared Clip"
  }
}
```

Common errors:

- `400`: unsupported media type or empty upload
- `410`: returned media URL points to an upload that expired or was removed
- `413`: upload exceeds the configured storage size limit
- `500`: storage or playback handoff failure

Upload storage defaults:

- root directory: `RELAYTV_UPLOADS_DIR`, default `/data/uploads`
- max upload storage size: settings `uploads.max_size_gb`, default `5`
- retention: settings `uploads.retention_hours`, default `24`
- cleanup runs before/after ingest and removes uploads by configured size or retention limit, whichever comes first

Progressive direct-play tuning:

- `RELAYTV_UPLOAD_PROGRESSIVE_MP4_READY_MB`, default `24`
- `RELAYTV_UPLOAD_PROGRESSIVE_WEBM_READY_MB`, default `12`
- `RELAYTV_UPLOAD_PROGRESSIVE_MAX_STALL_SEC`, default `2`
- `RELAYTV_UPLOAD_PROGRESSIVE_MIN_THROUGHPUT_KBPS`, default `256`

Example upload-only flow:

```bash
curl -F "title=Shared Clip" \
  -F "file=@clip.mp4;type=video/mp4" \
  http://relaytv.local:8787/ingest/media
```

Example one-call queue:

```bash
curl -F "title=Queued Clip" \
  -F "file=@clip.mp4;type=video/mp4" \
  http://relaytv.local:8787/ingest/media/enqueue
```

Example one-call play:

```bash
curl -F "title=Play Now Clip" \
  -F "file=@clip.mp4;type=video/mp4" \
  http://relaytv.local:8787/ingest/media/play
```

Transport/control endpoints:

- `POST /playback/play`
- `POST /playback/toggle`
- `POST /pause`
- `POST /resume`
- `POST /toggle_pause`
- `POST /next`
- `POST /previous`
- `POST /seek`
  - body: `{"sec": number}`
- `POST /seek_abs`
  - body: `{"sec": number}`
- `POST /volume`
  - body: `{"set": number}` or `{"delta": number}`
- `POST /mute`
  - body: optional `{"set": boolean}`
- `POST /close`
  - close playback but retain resumable session state
- `POST /stop`
  - stop playback and return to idle visuals while retaining resume metadata
- `POST /resume_session`
  - resume the retained closed session
- `POST /resume/clear`
  - clear retained resume state and return to idle

Most playback control responses include compact control-ack fields when available:

- `request_id`
- `ack_observed`
- `ack_reason`

## Queue and history

Queue endpoints:

- `POST /enqueue`
- `POST /queue/add`
- `POST /api/queue/add`
- `POST /v1/queue/add`
  - body: `{"url"}`
- `GET /queue`
- `POST /queue/remove`
  - body: `{"index": int}`
- `POST /queue/move`
  - body: `{"from_index": int, "to_index": int}`
- `POST /queue/dedupe`
- `POST /clear`
  - clears the queue

History endpoints:

- `GET /history`
- `POST /history/play`
  - body: `{"index": int}`
- `POST /history/requeue`
  - body: `{"index": int}`
  - queues the item using the server-stored URL; use this instead of
    `POST /enqueue` with a history payload URL, since public history
    URLs are display-safe copies with credentials stripped
- `POST /history/clear`

## Peer devices and queue transfer

Send what is playing, the queue, or any subset of both to another RelayTV
device on the same network. Peers are added by address and verified before they
are saved; nothing is discovered or added automatically.

Two gestures sit on top of these endpoints, differing only in what happens on
the sending device: **Send** gives the session away and stops here, **Copy**
leaves this device untouched. The payload is identical either way.

Devices found over mDNS are also reflected in `GET /discovery/status`, whose
`mdns.browse` block carries the same discovery state as advertising status.

Identity:

- `GET /peers/identity`
  - returns `{"device_id", "device_name", "base_url", "version"}`
  - anonymous and read-only by design: a device must be able to confirm what
    it is talking to before any token is exchanged
  - `device_id` is generated once into `/data/device_id`; set `RELAYTV_DEVICE_ID`
    only to pin an identity across cloned images

Registry:

- `GET /peers`
  - returns `{"device", "peers", "discovered", "discovery"}`
  - `discovered` lists devices seen over mDNS that are not saved yet, each
    `{"device_id", "device_name", "base_url", "version", "source", "last_seen_at"}`.
    Candidates are suggestions only — adding one is always an explicit action,
    and saved devices are filtered out by id or address
  - `discovery` reports `{"enabled", "active", "ttl_sec", "found", "last_error"}`
    so a client can tell "nothing found yet" apart from "discovery is not
    running". Browsing needs multicast and therefore host networking; on a
    bridged container `active` stays false
- `POST /peers`
  - body: `{"base_url", "name"?, "token"?, "verify"?}`
  - probes `GET /peers/identity` on the address first and refuses this device's
    own id, an already-registered device (`409`), a URL with embedded
    credentials, and any scheme other than http/https
  - `token` is the peer's `RELAYTV_API_TOKEN`, needed only when that device
    requires one. It is stored in the mode-0600 peer file, never in
    `settings.json`, and never returned by any endpoint: peer payloads carry
    `has_token` instead
- `PATCH /peers/{peer_id}`
  - body: `{"name"?, "base_url"?, "token"?}`; an empty `token` clears it
- `DELETE /peers/{peer_id}`
- `POST /peers/probe`
  - body: `{"base_url", "token"?}`
  - tests an address without saving it; returns
    `{"online", "error", "device_id", "device_name", "version", "is_self"}`
- `POST /peers/{peer_id}/probe`
  - probes a saved peer and folds the result into its record
    (`last_seen_at`, `last_ok_at`, `last_error`)

Send:

- `POST /peers/{peer_id}/send`
  - body: `{"mode": "append"|"replace"|"move", "index"?, "indexes"?}`
  - omit both selectors to send the whole queue. `indexes` sends just those
    queue positions, in queue order; `index` is the older single-item form and
    still works. An explicit empty `indexes` is a `400`, not a no-op, and any
    out-of-range position is a `400` before anything is sent
  - `append` and `replace` are copies: the sending device keeps its queue.
    `move` gives up ownership — it imports as `append` on the peer and then
    drops the sent items locally, but only after the peer confirms the import,
    so a transfer that fails in transit loses nothing. Unselected items stay.
    A `move` response adds `{"moved": true, "local_queue_length"}`
  - `move` drops only what the peer **accepted**, matched against the queue as
    it stands when the response arrives, never by the positions captured before
    the request. Items the peer rejected, items that could not travel (IPTV),
    and items that shifted position while the send was in flight are all left
    alone — a send can take tens of seconds, and reusing a stale index would
    delete whatever had moved into that slot. If a peer reports no per-item
    results and its `accepted` count does not cover everything sent, nothing is
    dropped locally
  - returns `{"sent", "accepted", "rejected", "queue_length", "peer", "mode"}`
  - IPTV items are reported in `rejected`, not sent: their stream URLs may
    carry credentials anywhere in the path, so no portable URL exists
- `POST /peers/{peer_id}/handoff`
  - body: `{"indexes"?, "keep_local"?}`; an empty body (or none) hands over the
    current playback plus the whole queue and stops here
  - `indexes` restricts which queue items travel with the session; the rest stay
    here and this device advances into them instead of going idle
  - `keep_local` sends exactly the same payload but skips every local teardown,
    so both devices play the same thing from the same position. This is the UI's
    **Copy**; it defaults to `false` so an unasked-for handoff still moves the
    session rather than duplicating it
  - `409` when nothing is playing. IPTV sessions cannot be handed off: their
    stream URLs are re-resolved from a local catalog the peer does not have
  - ordering is deliberate — playback stops locally only after the peer reports
    it took over, so a failed handoff leaves this device playing
  - without `keep_local` the local session is cleared, not closed: the session
    moved to the peer, so this device returns to idle with nothing to resume
    rather than showing the item it gave away
  - returns `{"playing", "resume_pos", "sent", "accepted", "rejected",
    "queue_length", "local_stopped", "local_queue_length", "kept_local", "peer"}`

Receive:

- `POST /queue/import`
  - body: `{"items": [{"url", "title"?, "thumbnail"?, "channel"?, "duration"?,
    "provider"?}], "mode": "append"|"replace", "from": {"device_id", "name",
    "base_url"}?}`
  - a write endpoint, so it is covered by `RELAYTV_API_TOKEN` when set
  - items are references, not recipes: the receiving device re-resolves each
    URL with its own provider configuration, cookies, and quality policy, and
    only the listed display hints are carried over. Resolved stream URLs and
    provider tokens are sender-scoped and never travel
  - per-item outcomes come back in `results` as
    `{"url", "title", "accepted", "reason"}`, so a partial import reports
    honestly instead of failing the whole request
  - imported items record `peer_origin` (`{"device_id", "name"}`) and are never
    auto-forwarded to another peer
  - `provider: "upload"` marks media the sending device hosts itself; the
    receiver streams it over HTTP from that device and marks the item
    `peer_hosted` so local upload resolution stays out of the way
- `POST /queue/handoff`
  - body: `{"now_playing": {…item…}, "resume_pos"?, "items": [{…item…}], "from"?}`
  - items use the same shape and rebuilding rules as `/queue/import`
  - this device starts playing `now_playing` at `resume_pos`, then imports the
    rest of the queue. Playback is taken over first on purpose: if it cannot
    start, the error propagates and the queue is not imported, so the sending
    device keeps both its playback and its items
  - whatever was playing here is preserved to the front of the queue rather
    than discarded, so a handoff never destroys the receiver's own session
  - returns `{"playing", "now_playing", "accepted", "results", "queue_length",
    "queue"}`

## Notifications and overlay delivery

Notification entrypoints:

- `POST /overlay`
- `POST /toast`
- `POST /notify`

`/toast` and `/notify` are aliases of `/overlay`.

Overlay request body:

- `text?`
- `duration?`
- `position?`
- `style?`
- `image_url?`
- `level?`
- `icon?`
- `link_url?`
- `link_text?`

Overlay responses include:

- `ok`
- `duration_ms`
- `position`
- `style`
- `visual_runtime_mode`
- `notification_strategy`
- `notifications_available`
- `notifications_reason`
- `overlay_subscribers`
- `notifications_deliverable`
- `delivery_mode`
- `native_qt_idle_deprecated`
- `native_qt_idle_status`
- `native_qt_idle_override_enabled`
- `native_qt_toasts_deprecated`
- `native_qt_toasts_status`
- `native_qt_toasts_override_enabled`

`POST /overlay` returns `503` in headless runtime when notifications are unavailable.

Notification/runtime introspection:

- `GET /notifications/capabilities`
  - includes native Qt idle/toast deprecation and override-only metadata

Advanced X11 overlay runtime endpoints:

- `GET /x11/overlay`
- `WEBSOCKET /x11/overlay/ws`
  - preferred versioned toast stream; requires `relaytv.realtime.v1`
- `GET /x11/overlay/events`
  - compatible Server-Sent Events fallback for X11 overlay clients
- `POST /x11/overlay/client_state`
  - body: `{"state", "reason"?, "client_event"?, "client_reason"?, "active_toasts"?}`
- `GET /x11/host_urls`

The embedded overlay automatically selects WebSocket, then SSE. Playback-state
visibility polling remains separate, and client delivery telemetry continues
through `/x11/overlay/client_state`. These X11 endpoints are not the primary
native Qt control surface.

## Runtime status and diagnostics

- `GET /status`
  - full server-authoritative runtime state
- `GET /playback/state`
  - compact fast playback-state endpoint for the web UI
- `GET /runtime/capabilities`
  - backend/runtime capability snapshot
- `GET /tv/status`
  - HDMI-CEC / TV control status
- `GET /devices`
  - discovered device/runtime capability helpers
- `GET /discovery/status`
  - mDNS discovery status

## Settings and configuration

- `GET /settings`
  - returns a UI-safe settings view
  - secrets are masked; configured-state flags are exposed instead
- `POST /settings`
  - partial settings update
  - request body supports the current `SettingsReq` fields, including:
    - `device_name`
    - `video_mode`
    - `drm_connector`
    - `drm_mode`
    - `audio_device`
    - `quality_mode`
    - `quality_cap`
    - `ytdlp_format`
    - `youtube_cookies_path`
    - `youtube_use_invidious`
    - `youtube_invidious_base`
    - `sub_lang`
    - `cec_enabled`
    - `tv_takeover_enabled`
    - `tv_pause_on_input_change`
    - `tv_auto_resume_on_return`
    - `volume`
    - `idle_dashboard_enabled`
    - `idle_notifications_enabled`
    - `idle_qr_enabled`
    - `idle_qr_size`
    - `idle_panels`
    - `weather`
    - `uploads`
    - `jellyfin_enabled`
    - `jellyfin_server_url`
    - `jellyfin_username`
    - `jellyfin_password`
    - `jellyfin_user_id`
    - `jellyfin_audio_lang`
    - `jellyfin_sub_lang`
    - `jellyfin_playback_mode`
    - `jellyfin_server_type` (`jellyfin | emby`; normally set by server-type
      detection, not by hand)
    - `seerr_enabled`
    - `seerr_server_url`
    - `seerr_api_key` (write-only; an empty value preserves the stored key)
    - `seerr_api_key_clear` (explicitly clears the stored key)
    - `seerr_request_mode` (`disabled | shared_admin | caller_session`)
    - `seerr_request_user_id` (optional shared-mode attribution)
    - `apply_now`
  - supports `apply_now` for settings that can be applied live
  - response includes:
    - `ok`
    - `playing`
    - `apply_now`
    - `apply_performed`
    - `apply_succeeded`
    - `settings`
    - `now_playing`
    - `live_applied`
    - `live_apply_failed`
    - `restart_sensitive_pending`
    - `restart_recommended`
  - idle visual settings are applied live when playback is idle:
    - enabling `idle_dashboard_enabled` starts the idle dashboard immediately
    - disabling `idle_dashboard_enabled` can still leave a transparent
      notification surface active when `idle_notifications_enabled` is true
    - disabling both idle dashboard and idle notifications stops idle visual
      surfaces and returns control to the desktop/session background

Upload settings shape:

```json
{
  "uploads": {
    "max_size_gb": 5,
    "retention_hours": 24
  }
}
```

Upload setting bounds:

- `max_size_gb`: `0.25` to `500`
- `retention_hours`: `1` to `2160`

YouTube cookie helpers:

- `POST /settings/youtube/cookies`
  - body: `{"cookies_text", "filename"?}`
  - stores a Netscape-format cookies file and updates `youtube_cookies_path`
  - responses mask the stored path and expose `youtube_cookies_configured`
- `POST /settings/youtube/cookies/clear`
  - removes RelayTV's uploaded cookies file when it owns that path and clears
    `youtube_cookies_path`

When YouTube cookies are configured, RelayTV passes them to yt-dlp and avoids
yt-dlp client fallbacks that do not support cookie auth.

## Jellyfin integration and browse API

Integration status and operator helpers:

- `GET /integrations/jellyfin/status`
- `POST /integrations/jellyfin/catalog/cache_clear`
- `POST /integrations/jellyfin/connect`
- `POST /integrations/jellyfin/disconnect`
- `POST /integrations/jellyfin/register`
- `POST /integrations/jellyfin/command`
- `POST /integrations/jellyfin/heartbeat`
- `GET /integrations/jellyfin/progress_snapshot`
- `POST /integrations/jellyfin/stopped`
- `GET /integrations/jellyfin/stopped_snapshot`

Legacy compatibility endpoint:

- `POST /integrations/jellyfin/push`
  - deprecated legacy Jellyfin plugin ingress
  - returns `410 Gone`

Native Jellyfin browse/detail endpoints:

- `GET /jellyfin/home`
- `GET /jellyfin/search`
  - query: `q`, optional `limit`, optional `refresh`
- `GET /jellyfin/movies`
- `GET /jellyfin/tv/series`
- `GET /jellyfin/tv/series/{series_id}/seasons`
- `GET /jellyfin/tv/series/{series_id}/episodes`
- `POST /jellyfin/tv/series/{series_id}/play_all`
- `GET /jellyfin/item/{item_id}`
- `GET /jellyfin/item/{item_id}/adjacent`
- `GET /jellyfin/audio/options`
- `POST /jellyfin/audio/select`
  - body: `{"index": int}`
- `GET /jellyfin/subtitle/options`
- `POST /jellyfin/subtitle/select`
  - body: `{"index": int}` where `-1` turns subtitles off
- `POST /jellyfin/action`
  - item play command wrapper (`play_now`, `play_next`, `play_last`, `resume`)

## Seerr integration, discovery, and request API

RelayTV supports Seerr `3.1.0` and newer (development baseline: `3.4.1`). The
integration is disabled by default. See [Seerr operations](SEERR_OPERATIONS.md)
for setup, request-identity choices, and troubleshooting.

Integration status and operator helpers:

- `GET /integrations/seerr/status`
  - returns only sanitized configuration, reachability, version,
    media-server, request-mode, and caller-session state
  - never returns the Seerr API key, upstream cookie, flow secret, or raw user
    record
- `POST /integrations/seerr/test`
  - validates the configured origin and active authentication strategy
- `GET /integrations/seerr/users`
  - returns sanitized `id`, `display_name`, and `username` values for the
    operator-controlled shared-mode attribution selector

Caller-specific session endpoints:

- `POST /integrations/seerr/session/quick-connect`
  - starts Jellyfin Quick Connect and returns an approval `code`, opaque
    RelayTV `flow_id`, and expiry
- `POST /integrations/seerr/session/quick-connect/complete`
  - body: `{"flow_id": "..."}`
  - returns `pending: true` until approved; after approval, sets an `HttpOnly`,
    `SameSite=Strict` RelayTV session cookie
- `GET /integrations/seerr/session`
  - returns caller connection state, sanitized identity, and remaining lifetime
- `POST /integrations/seerr/session/logout`
  - retires the in-memory session and clears the browser cookie

Caller sessions last at most 12 hours, are bound to the configured Seerr
origin, and do not survive a RelayTV restart. They are available only in
`caller_session` mode and require Seerr to use Jellyfin as its media server.

Discovery and request endpoints:

- `GET /seerr/discover`
  - query: `section=trending|movies|tv`, optional `page` (`1` to `500`)
- `GET /seerr/search`
  - query: `query` (1 to 200 characters), optional `page` (`1` to `500`)
- `GET /seerr/item/{media_type}/{media_id}`
  - `media_type`: `movie | tv`; `media_id`: positive TMDB ID
  - `playback_available` is true only after an exact active-server Jellyfin
    type and TMDB-provider-ID match
- `GET /seerr/requests`
  - query: `take` (`1` to `100`), `skip`, and an allowlisted `filter`
  - returns sanitized request state plus recognizable title, year, rating, and
    proxied artwork metadata when Seerr detail lookup succeeds
- `POST /seerr/requests`
  - example body:

    ```json
    {
      "media_type": "tv",
      "media_id": 123,
      "seasons": [1, 2],
      "is_4k": false
    }
    ```

  - `seasons` may be a list of season numbers, `"all"`, or `null`; it is valid
    only for TV requests
  - accepts no caller-selected user, server, quality profile, root folder,
    tags, quota override, or approval action
  - may return `created: false, reason: "no_requestable_seasons"` as a
    successful semantic response
- `POST /seerr/playback`
  - example body:

    ```json
    {
      "media_type": "movie",
      "media_id": 123,
      "command": "play_now"
    }
    ```

  - `command` accepts `play_now`, `play_next`, or `play_last`
  - refetches the Seerr item and revalidates the active Jellyfin item before
    acting; callers cannot provide a Jellyfin item ID, stream URL, or token
- `GET /seerr/image/{size}/{image_path}`
  - proxies only a validated TMDB image filename at `w185`, `w342`, `w500`,
    `w780`, or `original`

Every Seerr POST route uses the normal optional `RELAYTV_API_TOKEN` write
guard. Upstream errors use `detail: {"code": "...", "message": "..."}`;
common statuses are `400` for invalid input/configuration, `403` for disabled
requests or permission failures, `404` for missing items/expired flows, `409`
for duplicates or unavailable playback validation, `502` for upstream/auth
failures, `503` for a disabled/unconfigured integration, and `504` for timeouts.

## Operational notes

- Queue/history/session/settings persistence lives under `/data`.
- Playback/state endpoints are server-authoritative; the web UI should not invent state locally.
- `/ui/ws` is the preferred hot-state delivery path; `/ui/events` and then
  `/status` remain the supported compatibility and reconnect fallbacks.
- Some control endpoints return `400` for invalid user actions such as empty queue or no resumable session.
- Some playback-dependent endpoints return `409` when active playback is required and unavailable, for example `/snapshot`.
- Existing aliases remain active where noted for backward compatibility.
