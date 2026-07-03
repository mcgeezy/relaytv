# RelayTV Jellyfin Operations

## Scope

This document covers runtime configuration, reconnect behavior, and first-line troubleshooting for the RelayTV Jellyfin receiver integration.

RelayTV now treats the native Jellyfin client as the only supported Jellyfin UX in the public release.
The old Jellyfin server plugin is deprecated and no longer ships in the public release.

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

## Required Environment

- `RELAYTV_JELLYFIN_ENABLED=1`
- `RELAYTV_JELLYFIN_SERVER_URL=http://<jellyfin-host>:8096`

Preferred authentication:

- `RELAYTV_JELLYFIN_AUTH_ENABLED=1` (default enabled)
- `RELAYTV_JELLYFIN_USERNAME=<jellyfin-user>`
- `RELAYTV_JELLYFIN_PASSWORD=<jellyfin-password>`

Optional fallback authentication:

- `RELAYTV_JELLYFIN_API_KEY=<token>`
  - Supported for compatibility and for API-key-only deployments.
  - The Settings UI path prefers username/password auth and masks the password
    on reads.

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
- `last_register_ts`, `last_register_ok`, `last_register_error`
- `last_progress_ts`, `last_progress_ok`, `last_progress_error`
- `register_retry_failures`
- `next_register_retry_ts`
- `last_register_backoff_sec`
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
   - Verify username/password auth is valid, or verify
     `RELAYTV_JELLYFIN_API_KEY` when using the optional fallback.
   - Check `last_register_error` and `register_retry_failures` in status.
2. Connected flips false after startup:
   - Inspect `last_progress_error`; progress posts can mark receiver disconnected on transport errors.
   - Confirm Jellyfin accepts `/Sessions/Playing/Progress` from this client identity.
3. Commands arrive but playback does not start:
   - Check RelayTV `/status` (`player_runtime_engine`, `backend_ready`).
   - Check `/integrations/jellyfin/command` response body for `reason` or suppression flags.

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
