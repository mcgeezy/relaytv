# RelayTV API

Base URL (default): `http://<host>:8787`

RelayTV serves its HTTP API from the root path. Most endpoints return JSON. HTML, SVG, image, and SSE endpoints are called out explicitly below.

This file is the active endpoint reference for the native Qt runtime. Historical compat-only endpoints are removed from the active tree and are not documented here.

## UI and utility endpoints

- `GET /ui`: main web UI HTML
- `GET /ui/events`
  - Server-Sent Events stream for the main web UI
- `GET /idle`: idle dashboard HTML
- `GET /`: redirects to `/ui`
- `GET /health`: `{"ok": true}`
- `GET /manifest.json`: PWA manifest
- `GET /sw.js`: service worker
- `GET /thumbs/{filename}`: cached thumbnail image
- `GET /snapshots/{filename}`: saved JPEG snapshot
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
- `GET /pwa/{asset_path:path}`
- `GET /favicon.ico`

`GET /ui/events` is the stable browser-state push path for the native UI. It is not a durable event log and does not support replay cursors or sequence resumption. The contract is:

- snapshot events remain authoritative
- hint events trigger targeted refresh/render work
- clients should reconnect on disconnect and keep `/status` as bootstrap/fallback

Current `/ui/events` event types:

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
- `POST /history/clear`

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
- `GET /x11/overlay/events`
  - Server-Sent Events stream for X11 overlay clients
- `POST /x11/overlay/client_state`
  - body: `{"state", "reason"?, "client_event"?, "client_reason"?, "active_toasts"?}`
- `GET /x11/host_urls`

These X11 overlay endpoints remain active for overlay/runtime diagnostics and browser-side overlay clients. They are not the primary native Qt control surface.

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
- `POST /settings/youtube/cookies/clear`

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

## Operational notes

- Queue/history/session/settings persistence lives under `/data`.
- Playback/state endpoints are server-authoritative; the web UI should not invent state locally.
- `/ui/events` is the preferred hot-state delivery path for the browser UI, but `/status` remains the supported reconnect/bootstrap fallback.
- Some control endpoints return `400` for invalid user actions such as empty queue or no resumable session.
- Some playback-dependent endpoints return `409` when active playback is required and unavailable, for example `/snapshot`.
- Existing aliases remain active where noted for backward compatibility.
