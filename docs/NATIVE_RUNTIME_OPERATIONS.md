# Native Runtime Operations

## Scope

This runbook is the first-line operator workflow for native Qt on desktop hosts.

Primary docs:

- `docs/INSTALL.md`

## Quick Health Snapshot

```bash
curl -sS http://127.0.0.1:8787/status
curl -sS http://127.0.0.1:8787/runtime/capabilities
cd /path/to/relaytv
./scripts/host-ops.sh native-ready --wait 25
```

Use `native-ready` as the main runtime gate. It exits non-zero when the live runtime does not match the expected native profile, when configured/effective backend diverge, or when expected visual runtime mode does not match active runtime mode.

Phase 2 contract gate:

```bash
cd /path/to/relaytv
./scripts/host-ops.sh acceptance --no-up
```

`acceptance` runs `native-ready`, validates native telemetry + control ack contract, verifies overlay API deliverability, and (by default) runs a YouTube pipeline check.

## Standard Bring-Up

Wayland native:

```bash
cd /path/to/relaytv
./scripts/host-ops.sh up --wayland-native --stable-playback
```

X11 native:

```bash
cd /path/to/relaytv
./scripts/host-ops.sh up --x11-native --native-playback
```

The mode flags force a coherent launch profile even if `.env` was generated under a different session type.

## Idle Screen

Default desktop Qt path on current product builds is the embedded `libmpv` player with the browser-backed idle screen rendered through the Qt web overlay.
The older native Qt idle widget layer is deprecated and now a compatibility override only.
Native Qt toast delivery is also deprecated and retained only as an explicit override.

Primary product path:

- browser-backed idle through the Qt web overlay
- overlay toasts for idle/runtime notifications

Compatibility overrides:

- `RELAYTV_QT_NATIVE_IDLE=1`
- `RELAYTV_QT_NATIVE_TOASTS=1`

Status/runtime visibility:

- `/status` and `/notifications/capabilities` now expose `native_qt_idle_deprecated`, `native_qt_idle_status`, `native_qt_idle_override_enabled`
- `/status` and `/notifications/capabilities` also expose `native_qt_toasts_deprecated`, `native_qt_toasts_status`, `native_qt_toasts_override_enabled`

Idle should visually match the current browser composition:

- `banner.png` remains top-centered
- device name remains bottom-left
- time/date sits in the upper hero row instead of drifting to vertical center
- weather renders to the right of time/date when enabled
- forecast row renders below the hero row

Quick idle checks:

```bash
curl -sS http://127.0.0.1:8787/settings
curl -sS http://127.0.0.1:8787/idle/weather
```

Expected:

- `/settings` shows weather enabled under `idle_panels.weather.enabled`
- weather location/units config is populated when weather is intended to display
- `/idle/weather` returns current conditions plus daily forecast data

When validating on the actual display after startup, close, or natural end-of-playback, confirm the idle surface is visible again rather than dropping back to the desktop.

Settings apply checks:

- when `idle_dashboard_enabled` is changed from disabled to enabled while
  playback is idle, clicking Apply should bring the idle dashboard up
  immediately
- when the dashboard is disabled but `idle_notifications_enabled` remains
  enabled, the app should leave the desktop visible while still allowing toast
  delivery
- when both dashboard and idle notifications are disabled, RelayTV should stop
  idle visual surfaces and return to the desktop/session background

Pi refresh sequence:

```bash
cd /path/to/relaytv
git pull origin main
docker compose up -d --build
docker logs --tail 120 relaytv
```

## Native Soak

Short gate:

```bash
cd /path/to/relaytv
./scripts/host-ops.sh soak --native-qt --sec 180 --poll 5 --no-up
```

Evidence runs:

```bash
cd /path/to/relaytv
./scripts/host-ops.sh soak --native-qt --preset 30m --no-up
./scripts/host-ops.sh soak --native-qt --preset overnight --no-up
```

Artifacts are written under `logs/relaytv-hostops-soak` by default.

## Acceptance + Overnight Playbook

Run this exact sequence on each host class (NUC and Raspi):

```bash
cd /path/to/relaytv
./scripts/host-ops.sh acceptance --no-up --wait 5
./scripts/host-ops.sh soak --native-qt --preset overnight --no-up --capture-logs-on-pass
```

Optional report artifact path:

```bash
cd /path/to/relaytv
mkdir -p logs/relaytv-migration-evidence/$(hostname -s)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
./scripts/host-ops.sh acceptance --no-up --wait 5 | tee logs/relaytv-migration-evidence/$(hostname -s)/${STAMP}-acceptance.log
./scripts/host-ops.sh soak --native-qt --preset overnight --no-up --capture-logs-on-pass \
  --report logs/relaytv-migration-evidence/$(hostname -s)/${STAMP}-overnight-summary.json
```

Keep evidence in persistent paths (`logs/relaytv-migration-evidence/...` or `logs/relaytv-hostops-soak/...`) when you want retained soak history across hosts or releases.

Quick post-run check:

```bash
cd /path/to/relaytv
./scripts/host-ops.sh native-ready --wait 5
curl -sS http://127.0.0.1:8787/status | jq '{playing,state,player_backend,player_runtime_engine,visual_runtime_mode,notifications_deliverable}'
```

## Troubleshooting Flow

1. Run `native-ready`.
2. Check:
   - `player_runtime_engine`
   - `backend_ready`
   - `playback_telemetry_source`
   - `playback_telemetry_freshness`
3. If native runtime is unhealthy, capture logs.
4. If queue or playback behavior is wrong, capture `status` and recent logs together.

### YouTube Bot Checks Or Cookie Resolver Errors

If YouTube starts returning bot-check or sign-in challenges after repeated
testing, configure a Netscape-format cookies file through the Settings UI or:

```bash
curl -sS -X POST http://127.0.0.1:8787/settings/youtube/cookies \
  -H 'Content-Type: application/json' \
  --data '{"cookies_text":"# Netscape HTTP Cookie File\n..."}'
```

Then verify RelayTV reports cookies as configured without exposing the stored
path:

```bash
curl -sS http://127.0.0.1:8787/settings | jq '{youtube_cookies_configured}'
```

When cookies are configured, RelayTV passes them to yt-dlp and skips yt-dlp
client fallbacks that do not support cookie auth. Avoid repeated live YouTube
resolve tests while bot checks are active; prefer checking settings and recent
resolver logs first.

### Idle Weather Or Layout Drift

If idle is missing weather, forecast, or expected placement:

1. Verify `/idle/weather` returns `200` with populated `current` and `daily` fields.
2. Verify `/settings` still has weather enabled in `idle_panels.weather`.
3. If backend data is present, treat the next step as a Qt render/application issue rather than a network or provider issue.
4. If `RELAYTV_QT_NATIVE_IDLE=1` is explicitly enabled, add temporary logging in [qt_shell_app.py](../app/relaytv_app/qt_shell_app.py) around:
   - `_refresh_weather()`
   - `_apply_weather()`
   - `_apply_weather_forecast()`

Useful temporary checks to log:

- whether the `/idle/weather` callback received a dict
- whether `current` is present
- whether `daily.time` has entries
- whether the native weather and forecast frames are being set visible

## Logs

RelayTV logs:

```bash
cd /path/to/relaytv
./scripts/host-ops.sh logs relaytv --since 5m
```

Jellyfin correlation:

```bash
cd /path/to/relaytv
./scripts/host-ops.sh logs jellyfin --since 5m
```

Targeted runtime filter:

```bash
cd /path/to/relaytv
./scripts/host-ops.sh logs relaytv --since 5m \
  --grep "qt_shell|native_qt|telemetry|overlay|queue|playlist|control|ack"
```

Logging defaults now bias toward signal over hot-path request noise:

- successful `GET /status`, `GET /playback/state`, `GET /ui/events`, and `GET /integrations/jellyfin/status` access lines are suppressed by default
- playback/control mutations still show up in normal access logs
- slow or failing requests are still logged by the app layer

Useful logging knobs:

- `RELAYTV_LOG_LEVEL`
  - default backend/runtime log level
- `RELAYTV_RESOLVER_LOG_LEVEL`
  - resolver-specific level override
- `RELAYTV_ACCESS_LOG`
  - set `0` to disable Uvicorn access logs completely
- `RELAYTV_ACCESS_LOG_LEVEL`
  - level for remaining access logs when enabled
- `RELAYTV_ACCESS_LOG_HOT_PATHS`
  - comma-separated override for successful hot-path access suppression
- `RELAYTV_SLOW_REQUEST_MS`
  - threshold for app-layer slow request summaries

## Queue Or Transition Drift

If UI `now_playing` or queue state does not match visible playback:

```bash
cd /path/to/relaytv
./scripts/host-ops.sh status
./scripts/host-ops.sh logs relaytv --since 3m --grep "playlist-pos|time-pos|queue|auto_next|loadfile|playlist-next"
```

## Rollback Path

Rollback is now a deploy-time action, not a live compat runtime path.

Use the tagged native baseline (or a later known-good rollback tag) if a decommissioned sidecar path needs to be restored:

```bash
cd /path/to/relaytv
git checkout native-qt-baseline
./scripts/install.sh
./scripts/host-ops.sh up --wayland-native --native-playback
```

No live compat runtime, sidecar wrapper, or compat validation wrapper remains in the active operator surface.

## Useful Knobs

Only use these when the default path is insufficient:

- `SOAK_BASE_URL`
- `SOAK_CAPTURE_LOGS_ON_PASS=1`
- `SOAK_REQUIRE_DIAGNOSTICS_OK=0`
- `SOAK_REQUIRE_SUMMARY_OK=0`
- `SOAK_FAIL_DIAG_LEVELS=error,stale`
- `RELAYTV_MPV_SEAMLESS_REPLACE=0` to disable in-process replace behavior during diagnosis
