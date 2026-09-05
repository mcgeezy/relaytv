# RelayTV Jellyfin Operations

## Scope

This document covers runtime configuration, reconnect behavior, and first-line troubleshooting for the RelayTV Jellyfin receiver integration. The same integration also accepts Emby servers (see "Emby Servers" below); everywhere this document says Jellyfin, an Emby server behaves the same unless noted.

RelayTV now treats the native Jellyfin client as the only supported Jellyfin UX in the public release.
The old Jellyfin server plugin is deprecated and no longer ships in the public release.

## Browser Library Experience

The `/ui` remote includes a responsive Jellyfin/Emby library shell for phones
and desktop browsers. Its product behavior remains backed by the existing
Jellyfin routes and integration service; the dedicated browser assets are
`static/ui/jellyfin.js` and `static/ui/jellyfin.css`.

- Home presents Continue Watching, Next Up, Movies, Shows, and recently added
  content as horizontally scrollable rails.
- Movies and TV catalogs load in bounded 48-item pages and append on demand.
- Series pages provide series metadata, season selection, Play All, and an
  episode grid. The season selector is a bottom sheet on phones and a modal on
  wider screens.
- Item details use a phone bottom sheet and desktop side drawer with keyboard
  focus entry and restoration.
- Jellyfin and Emby labels remain driven by detected server type. Unsupported
  Emby rows are omitted without changing supported browse and playback flows.

The modern browse shell is the sole supported presentation. The former
`jfui=modern|classic` comparison switch and its local-storage preference were
removed after design acceptance.

## Cast Target

Each RelayTV device registers itself as one Jellyfin session and appears in the
**Cast** menu of the Jellyfin web and mobile clients. Pick it there and the
server sends playback to that TV; the phone then acts as the remote (play/pause,
scrubber, next/previous, volume).

Nothing needs configuring beyond the normal Jellyfin credentials. RelayTV is one
full install per TV, so a household of three RelayTV boxes shows three cast
targets, each named by its `device_name` setting.

### How it works

Three things must all be true before the server will offer the device:

1. **Authenticated session.** `POST /Users/AuthenticateByName` under this
   device's `DeviceId`/`Device` identity.
2. **Capabilities recorded.** `POST /Sessions/Capabilities/Full` with
   `SupportsMediaControl` and the commands RelayTV implements. RelayTV reads the
   session back afterwards and only reports `last_register_ok` once the server
   confirms it — a 204 is not evidence, because the server returns one whether
   or not the body bound to anything.
3. **A live control socket.** RelayTV holds a WebSocket open on
   `/socket?api_key=…&deviceId=…`. The server computes `SupportsRemoteControl`
   as "media control advertised **and** a socket attached", so without it the
   device is invisible to casting even with perfect capabilities.

The socket answers the server's `ForceKeepAlive` on its stated interval, and
reconnects with exponential backoff (3s doubling to 60s) if the server restarts.
Capabilities live in the server's in-memory session state and do not survive its
restart, so a fresh socket invalidates registration and the next heartbeat
re-posts and re-verifies. Both devices recover in under 20s with no RelayTV
restart; `last_register_reason` reads `socket_connected` when this is why.
Inbound `Play`, `Playstate`, and `GeneralCommand` messages are normalized and
run through the same command ingress as `POST /integrations/jellyfin/command`.
Commands execute on their own worker so a play that takes ten seconds to start
mpv cannot starve the keepalive and drop the session.

### Device identity

Jellyfin keys sessions, capabilities, and playback history on `DeviceId`, so
RelayTV derives it from the persisted install id in `/data/device_id` (see
`ARCHITECTURE.md`) rather than from `device_name`. Renaming a device therefore
changes only the label in the cast list; it keeps the same Jellyfin session and
its history. `RELAYTV_JELLYFIN_DEVICE_ID` still pins the value for cloned
images.

**One-time migration.** Before this change the id was derived from the display
name, so upgrading gives each device a new `DeviceId` once. The old entry stays
in Jellyfin's **Dashboard → Devices** with whatever history it accumulated and
can be deleted there; the device re-registers under the new id within a
heartbeat and casting is unaffected. Installs that had been renamed may have
accumulated several stale entries, all safe to remove.

### Advertised commands

`PlayState` (the whole playstate family: pause, unpause, play/pause toggle,
stop, seek, next, previous), `Play`, `PlayNext`, `SetVolume`, `Mute`, `Unmute`,
`ToggleMute`.

Only commands RelayTV actually executes are advertised. A command listed here
without a handler would put a dead button on every Jellyfin remote in the house.
The list must contain `GeneralCommandType` members only — `Stop`/`Pause`/`Seek`
and friends are `PlaystateCommand` values and the server rejects a body mixing
the two with a 400.

### Environment

- `RELAYTV_JELLYFIN_WS_ENABLED=1` (default; set `0` to keep the library
  integration without offering the device for casting)
- `RELAYTV_JELLYFIN_WS_CONNECT_TIMEOUT_SEC=8`
- `RELAYTV_JELLYFIN_WS_RETRY_BASE_SEC=3`
- `RELAYTV_JELLYFIN_WS_RETRY_MAX_SEC=60`

### Verifying

```bash
curl -s http://<host>:8787/integrations/jellyfin/status \
  | jq '{cast_target_ready, media_control_verified, ws_connected, ws_last_error, ws_reconnects}'
```

`cast_target_ready: true` is the single field that answers "can I cast to this
device". Server-side confirmation, from a machine with an admin token:

```bash
curl -s "http://<jellyfin>:8096/Sessions" -H 'Authorization: MediaBrowser Token="<token>"' \
  | jq '.[] | select(.DeviceId | startswith("relaytv"))
        | {DeviceName, SupportsRemoteControl, SupportsMediaControl, SupportedCommands}'
```

### Troubleshooting

**The device never appears in the Cast menu.** Check `cast_target_ready`. If
`media_control_verified` is false the capabilities POST is being rejected or
ignored — `last_register_error` says which. If `ws_connected` is false, see the
next two entries.

**`ws_available: false`.** The `websockets` package is missing from the image.
The library integration keeps working; only casting is unavailable.

**`ws_connected` flaps, `ws_reconnects` climbing.** The server is restarting, or
a proxy between RelayTV and Jellyfin is closing idle sockets faster than the
keepalive interval. `ws_keepalive_sec` shows the interval the server asked for.

**The device appears but commands do nothing.** Sessions are scoped per user:
the device is only offered to the account RelayTV authenticated as, plus admins.
Check `auth_user` matches the account casting from.

**`ws_commands_dropped` is non-zero.** Commands arrived faster than playback
could start and the backlog was capped. Expected under a burst of remote taps;
sustained growth means playback starts are hanging.

Discovery:

- RelayTV can advertise itself on LAN via mDNS (`_relaytv._tcp`) for server-side auto-discovery/bridge workflows.

Shared URL behavior:

- When `/smart` or Jellyfin command ingress receives a Jellyfin media URL, RelayTV now auto-enriches playback items from Jellyfin APIs:
  - title
  - thumbnail
  - resume position (if available)
- Callers can share only the URL; metadata fields are optional.
- Jellyfin share links like `/Items/<id>/Download?api_key=...` are supported:
  - RelayTV extracts `<id>`
  - prefers share-link `api_key` for metadata/stream normalization when present
  - rewrites playback URL to `/Videos/<id>/stream?...` for consistent playback flow
- Metadata presentation defaults:
  - TV episodes: `title = Series Name`, `channel = SxxExx · Episode Name`
  - Movies: `title = Movie Name`, `channel = Movie · Year`

## Shared Cast Target Authentication

Settings separates **Client login** from **Shared cast target**:

- **Client login** uses a username and password for RelayTV's media browser,
  personal library, and resume points. It remains active when shared casting
  is enabled. Leave the preferred user ID blank to use the signed-in account.
- **Share this cast target using an API key** enables a userless cast device
  for every Jellyfin user whose policy allows shared-device control. Create a
  key in **Dashboard → Advanced → API Keys**, paste it, and apply. Name the key
  **RelayTV** to label the cast target `<configured device name> - RelayTV`.

Both credential sections remain visible. Use **Apply Jellyfin / Emby** or the
main Settings **Apply** button to save both together. Blank password/key fields
preserve stored secrets; the explicit clear switches remove them. Client login
and cast-target status are shown separately, so failed login remains visible
while shared casting is connected.

With shared casting enabled, the API key owns the cast websocket, capability
registration, session readback, and playing/progress/stopped reports. Local client
catalog/media requests use the login-session token when login is configured.
Incoming server-socket casts resolve media with the cast API key, independently
of the local login; their cached catalog results are kept separate from browser
results.

The browsing login uses a separate device identity (`<device ID>-client`) so
it does not attach a user to the shared cast device. A pending or failed login
does not fall back to the administrator API key for catalog access.

Existing API-key-only setups remain supported: leave username/password empty
for casting only, or supply a preferred user ID for API-based catalog browsing.
With shared casting off, the login session provides both browsing and the
existing user-scoped cast target; a stored API key remains inactive. Other users
can only control that session when their Jellyfin policies permit it. The
existing `jellyfin_auth_mode` setting is retained for API compatibility and
selects cast identity (`shared_api_key` or `user_login`).

Jellyfin API keys are administrator-level credentials. RelayTV stores the key
server-side and returns only `jellyfin_api_key_configured` to Settings. Keys and
passwords are omitted from status responses and logs.

Jellyfin still enforces shared-device control permissions. Its cast menu's
second row is reserved for an authenticated session user; the shared target is
userless, so the API-key name appears as the client suffix on the first row.

### Caller-specific watch state

A command from another Jellyfin user reaches the shared target, including its
`ControllingUserId`, but RelayTV does not yet use that identity to change the
catalog profile or attribute playback reports. The on-device catalog continues
to use the client login or configured preferred user, and resume/played state
is not guaranteed to update for the person who initiated a remote cast.
User-login mode is one fixed operator-configured account, not per-browser
caller identity. Dynamically attributing shared casts to the initiating caller
remains a follow-up.

## Required Environment

- `RELAYTV_JELLYFIN_ENABLED=1`
- `RELAYTV_JELLYFIN_SERVER_URL=http://<jellyfin-host>:8096`

Shared cast-target authentication:

- `RELAYTV_JELLYFIN_API_KEY=<token>`
  - Equivalent to the Server API key field in Settings.
  - Preferred for a Jellyfin target shared across permitted users.
  - An environment-provided API key implies shared mode for backward
    compatibility; the shared cast toggle persists the explicit mode when configured in the UI.

Client login (also used for casting when shared casting is off):

- `RELAYTV_JELLYFIN_AUTH_ENABLED=1` (default enabled)
- `RELAYTV_JELLYFIN_USERNAME=<jellyfin-user>`
- `RELAYTV_JELLYFIN_PASSWORD=<jellyfin-password>`

- Configure **Client login** in Settings. Username/password without an API key
  defaults to user-login mode for backward compatibility. Enable the separate
  shared cast toggle and add an API key to use both roles together.

Optional identity:

- `RELAYTV_JELLYFIN_DEVICE_NAME=RelayTV`
- `RELAYTV_JELLYFIN_DEVICE_ID=relaytv-...`
- `RELAYTV_JELLYFIN_CLIENT_NAME=RelayTV`
- `RELAYTV_JELLYFIN_CLIENT_VERSION=1.0`

Optional mDNS advertising:

- `RELAYTV_MDNS_ENABLED=1` (default enabled)
- `RELAYTV_MDNS_SERVICE_TYPE=_relaytv._tcp.local.`
- `RELAYTV_MDNS_HOST=<advertised-ip>` (optional override)
- `RELAYTV_MDNS_INSTANCE_SUFFIX=<text>` (optional label suffix)

Recommended:

- Set device names in RelayTV Settings UI (`device_name`) per TV instance.
- RelayTV propagates this name to Jellyfin client/session identity so each TV appears distinctly.

Optional catalog profile override:

- `RELAYTV_JELLYFIN_USER_ID=<optional-jellyfin-user-id>`
  - Optional per-device catalog profile override.
  - When set, RelayTV browses Jellyfin catalog rows/detail/search as this user profile instead of the authenticated session user.

Optional playback compatibility policy:

- `RELAYTV_JELLYFIN_PLAYBACK_MODE=auto|direct|transcode` (default `auto`)
  - `auto`: use direct stream unless RelayTV detects compatibility risk (for example AV1 not allowed by current host profile, or source exceeds display cap).
  - `direct`: always prefer Jellyfin direct stream URL.
  - `transcode`: always request Jellyfin transcoding stream URL.

## Emby Servers

The integration is wire-compatible with Emby: point `jellyfin_server_url` (or
`RELAYTV_JELLYFIN_SERVER_URL`) at an Emby base URL and everything else works
unchanged — auth (username/password or API key), catalog browsing, playback,
track selection, and progress/stopped reporting.

The shared/userless session behavior and per-user shared-device permission
described above are verified against Jellyfin. Emby accepts the same API-key
transport, but operators should validate cast-target visibility for their Emby
version and user-policy configuration.

Server-type detection:

- On connect/apply, RelayTV probes the unauthenticated
  `GET /System/Info/Public` endpoint and classifies the server from
  `ProductName` ("Jellyfin Server" vs "Emby Server"; old Emby builds that omit
  the field also classify as Emby).
- The result is stored as the `jellyfin_server_type` setting
  (`jellyfin | emby`, default `jellyfin`, bus var
  `RELAYTV_JELLYFIN_SERVER_TYPE`) and drives the UI branding: the settings
  section, buttons, browse shell, and now-playing provider label read
  "Emby" when an Emby server is configured, and the neutral "Jellyfin / Emby"
  before any server URL is set.
- If the probe fails (server down mid-apply, proxy hides the endpoint), the
  current server type is kept and the receiver retries once the server is
  reachable (at most every 300s). `/integrations/jellyfin/status` exposes
  `server_type`, `server_product_name`, and `last_detect_ts/ok/error`.

Notes:

- Base URLs like `https://host/emby` also work: Emby serves its API both bare
  and under `/emby`. Reverse proxies that rewrite API paths are not supported.
- All settings keys, env vars, and API routes keep their `jellyfin_*` names
  regardless of server type; only user-facing labels change.
- Emby-only features (Emby Connect, Premiere) and Jellyfin-only features
  (SyncPlay) are out of scope.

### Emby Live-Verification Checklist

Run against a real Emby server (first bring-up, or after integration
changes):

1. Enter the Emby URL + credentials in settings → the section relabels to
   "Emby Integration" without a page reload;
   `/integrations/jellyfin/status` shows `server_type: "emby"`.
2. Auth both ways: username/password (`/Users/AuthenticateByName`) and
   API key (`X-Emby-Token`).
3. Browse home/movies/TV and series → season → episode; posters render.
4. Play a movie and an episode (direct and transcode modes); audio and
   subtitle track selection.
5. Progress and Stopped visible on the Emby dashboard; now-playing/queue
   show provider "Emby" with the Emby icon.
6. Regression: point back at a Jellyfin server → relabels back to
   "Jellyfin"; full browse/play/progress flow re-verified;
   `PYTHONPATH=app pytest -q` green.

## Settings UI Credentials (Current)

RelayTV Settings now supports Jellyfin credential and playback policy management directly:

1. Credentials can be set/updated in RelayTV settings.
2. Password remains write-only in API/UI responses (`jellyfin_password` is masked on reads).
3. Runtime connect/re-auth is attempted immediately when settings are complete.
4. Playback mode (`auto|direct|transcode`) can be changed in the same settings section and applies without requiring restart.
5. `/integrations/jellyfin/status` remains source of truth for auth/sync diagnostics.

## Heartbeat and Registration Retry

Heartbeat:

- `RELAYTV_JELLYFIN_HEARTBEAT_SEC=5` (minimum `2`)

Registration retry policy:

- `RELAYTV_JELLYFIN_REGISTER_RETRY=1` (default enabled)
- `RELAYTV_JELLYFIN_REGISTER_RETRY_BASE_SEC=3`
- `RELAYTV_JELLYFIN_REGISTER_RETRY_MAX_SEC=60`

Behavior:

1. Receiver attempts registration when running and credentials are present.
2. If registration fails, retries use exponential backoff (`base * 2^(n-1)`), capped at `MAX_SEC`.
3. On success, retry counters are cleared.

Watched-completion snapping policy:

- `RELAYTV_JELLYFIN_COMPLETE_RATIO=0.98`
  - When progress/stopped position reaches this ratio of runtime, RelayTV snaps `PositionTicks` to full runtime.
- `RELAYTV_JELLYFIN_COMPLETE_REMAINING_SEC=0`
  - Optional absolute-time snap window. When remaining runtime is less than or equal to this many seconds, RelayTV snaps completion even if ratio is not met.
  - Keep `0` to disable absolute remaining-time snapping.

Episode adjacency resilience:

- `RELAYTV_JELLYFIN_ADJACENT_SEASON_PROBE_MAX=8`
  - When Jellyfin season records are missing stable season IDs, RelayTV can probe adjacent season numbers (bounded) to maintain prev/next episode traversal.
  - Increase only if your library has unusually sparse season numbering.

## Runtime Status Fields

`GET /integrations/jellyfin/status` includes:

- `enabled`
- `running`
- `connected`
- `last_error`
- `api_key_configured`
- `auth_mode` (`shared_api_key` or `user_login`)
- `control_auth_source` (`api_key`, `user_session`, or `none`)
- `cast_target_scope` (`shared`, `user_scoped`, or `unavailable`)
- `cast_target_ready`
- `catalog_auth_source` (`user_session`, `api_key`, or `none`)
- `catalog_ready`
- `last_register_ts`, `last_register_ok`, `last_register_error`
- `last_progress_ts`, `last_progress_ok`, `last_progress_error`
- `register_retry_failures`
- `next_register_retry_ts`
- `last_register_backoff_sec`
- `media_control_verified` (session readback; `null` until attempted)
- `last_register_reason` (why registration was last invalidated)
- `last_playing_ts`, `last_playing_ok`, `last_playing_error`
- `ws_enabled`, `ws_available`, `ws_connected`
- `ws_last_connect_ts`, `ws_last_error`, `ws_reconnects`, `ws_keepalive_sec`
- `ws_commands_received`, `ws_commands_dropped`
- `auth_user_configured`
- `authenticated`
- `auth_user`
- `auth_user_id`
- `auth_session_id`
- `catalog_user_id`
- `catalog_user_source` (`preferred`, `authenticated`, or `none`)
- `catalog_cache_entries`, `catalog_cache_max_entries`
- `catalog_ttl_home_sec`, `catalog_ttl_search_sec`, `catalog_ttl_detail_sec`, `catalog_ttl_metadata_sec`
- `catalog_cache_clears`, `catalog_cache_last_cleared_ts`, `catalog_cache_last_cleared_reason`
- `last_auth_ts`, `last_auth_ok`, `last_auth_error`

Discovery runtime status:

- `GET /discovery/status`

## Troubleshooting

1. Registration failing repeatedly:
   - Verify `RELAYTV_JELLYFIN_SERVER_URL` is reachable from container.
   - Verify `RELAYTV_JELLYFIN_API_KEY` for shared mode, or verify the
     username/password login for user-login mode.
   - Check `last_register_error` and `register_retry_failures` in status.
2. Connected flips false after startup:
   - Inspect `last_progress_error`; progress posts can mark receiver disconnected on transport errors.
   - Confirm Jellyfin accepts `/Sessions/Playing/Progress` from this client identity.
3. Commands arrive but playback does not start:
   - Check RelayTV `/status` (`player_runtime_engine`, `backend_ready`).
   - Check `/integrations/jellyfin/command` response body for `reason` or suppression flags.
4. RelayTV appears for its configured account but not other users:
   - Check `auth_mode` and `cast_target_scope`; `user_scoped` is intentionally
     limited to the stored login identity.
   - Select shared mode, add a Server API key in RelayTV Settings, and wait for
     `cast_target_scope: shared` and `cast_target_ready: true`.
   - In Jellyfin, enable shared-device control for each user who should see the
     target.
5. The wrong identity appears active:
   - Check `auth_mode`, `control_auth_source`, and `catalog_auth_source`.
   - Both auth-source fields must match the selected mode; inactive stored
     credentials are never used as fallback.
6. Rotating the API key:
   - Create the replacement key in Jellyfin first.
   - Apply it in RelayTV Settings and wait for `cast_target_ready: true`.
   - Revoke the previous key only after the replacement target is healthy.

## Quick Verification

```bash
curl -sS http://127.0.0.1:8787/integrations/jellyfin/status
curl -sS -X POST http://127.0.0.1:8787/integrations/jellyfin/catalog/cache_clear
curl -sS -X POST http://127.0.0.1:8787/integrations/jellyfin/register
curl -sS -X POST http://127.0.0.1:8787/integrations/jellyfin/heartbeat
curl -sS -X POST http://127.0.0.1:8787/smart \
  -H 'content-type: application/json' \
  -d '{"url":"http://<jellyfin>/Videos/<item-id>/stream?static=true"}'
```

For a shared target, the status response should include:

```json
{
  "api_key_configured": true,
  "control_auth_source": "api_key",
  "cast_target_scope": "shared",
  "cast_target_ready": true
}
```

## Deprecated Legacy Endpoint

`POST /integrations/jellyfin/push` is deprecated.
It remains registered only so older Jellyfin plugin installs fail with a clear `410 Gone` response instead of an ambiguous `404`.

Use one of these paths instead:

- RelayTV native Jellyfin browse and playback UI
- `POST /integrations/jellyfin/command`
- `/smart` with a Jellyfin media URL

## Restart/Reconnect Soak Checklist

1. Start a Jellyfin cast to RelayTV and confirm playback starts.
2. While playing, restart the RelayTV container.
3. Confirm status fields after restart:
   - `enabled=true`
   - `running=true`
   - `register_retry_failures` increments only on failed registration attempts
   - `next_register_retry_ts` is set only when failures occur
4. Trigger `POST /integrations/jellyfin/register` and verify `ok=true`.
5. Trigger `POST /integrations/jellyfin/heartbeat` and verify either:
   - `ok=true`, or
   - `ok=false` with actionable `reason` (`no_payload` if idle/no active item).
6. Re-issue cast from Jellyfin and confirm:
   - first play command is accepted (no stale dedupe suppression)
   - playback controls still work (`Pause/Unpause/Seek/Stop`).

## Final Validation

Use this section instead of a separate Jellyfin checklist document.

### Product-Branch Validation

```bash
cd /path/to/relaytv
./scripts/host-ops.sh native-ready --wait 25
curl -sS http://127.0.0.1:8787/status
curl -sS http://127.0.0.1:8787/integrations/jellyfin/status
```

From a host with Playwright installed, validate the running server with the
checked-in browser matrix:

```bash
node scripts/jellyfin-ui-smoke.js \
  --ws=ws://PLAYWRIGHT_HOST:3000/ \
  --base=http://RELAYTV_HOST:8787
```

The matrix covers phone-dark and desktop-light layouts, bounded catalog
pagination, search, TV hierarchy, detail/modal focus, viewport overflow,
nested interactive controls, and offline recovery. The WebSocket endpoint is
the Playwright browser server; the browser itself must be able to reach the
RelayTV base URL.

### Multi-TV Naming And Profile Validation

For each RelayTV instance:

1. Set a unique device name in RelayTV settings.
2. Optionally set `jellyfin_user_id` for profile targeting.
3. Confirm:
   - `GET /status` shows expected `device_name`
   - `GET /integrations/jellyfin/status` shows expected `catalog_user_id` and `catalog_user_source`

### Long-Session Playback Validation

Run a playback session of at least 30 minutes and confirm:

1. Progress continues updating in Jellyfin.
2. Stop/close records expected resume and watched behavior.
3. `stopped_suppressed_count` increments only on true duplicate-stop cases.
4. Completion snap policy fields remain sane:
   - `/status`: `jellyfin_complete_ratio`, `jellyfin_complete_remaining_sec`
   - `/integrations/jellyfin/status`: `complete_ratio`, `complete_remaining_sec`

### Episode Navigation Stress

Validate prev/next on multiple series:

1. normal sequential episodes
2. missing episode numbers
3. season boundaries
4. libraries with sparse or partial season metadata

Expected:

- detail navigation stays accurate
- navigation does not dead-end incorrectly at season transitions
