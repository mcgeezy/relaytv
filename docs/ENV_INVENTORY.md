# Environment Variable Inventory

This is the machine-checked inventory of app-config environment variables
(see `ARCHITECTURE.md` for the runtime config service boundary it guards).
The table below is generated from the source tree and guarded by
`tests/test_env_inventory.py`; regenerate it after intentional changes with:

```text
PYTHONPATH=app python3 tests/test_env_inventory.py --write
```

## Scope

In scope: `RELAYTV_*`, `YTDLP_*`, `USE_INVIDIOUS`, `INVIDIOUS_BASE`,
and the `MPV_*` player knobs — the app configuration surface
managed behind `RuntimeConfig`.

Out of scope: platform and toolkit variables (`DISPLAY`, `WAYLAND_DISPLAY`,
`XDG_*`, `QT_*`, `HOME`, `PATH`, ...). Those describe the host session, are not
part of the settings bus, and keep flowing to child processes through normal
environment inheritance.

## Classification

- **settings bus**: written at runtime by the app itself through
  `RuntimeConfig.set_value` (startup sync in `main.py`, settings apply in
  `routes/settings.py`, `routes/jellyfin.py`, or `player.py`). Since M5,
  these writes land in the RuntimeConfig snapshot only; `os.environ` receives
  just the pinned `MIRRORED_TO_ENV` subset (`RELAYTV_DEVICE_NAME`).
- **child process input**: referenced by `qt_shell_app.py` or
  `overlay_app.py`, which run as child processes spawned at runtime and
  inherit the server's environment. These define the subprocess mirroring
  contract.
- **pre-app entrypoint**: referenced only by `container_entrypoint.py`, which
  runs before the server process and configures its environment. Not part of
  the in-process bus.
- **entrypoint input**: referenced by `container_entrypoint.py` and by
  runtime modules.
- **static env**: read-only operator configuration; no runtime writes.

## Key Finding: The Runtime Env Bus Is Almost Entirely In-Process

As of phase start, exactly one **settings bus** variable is referenced by a
runtime child process module: `RELAYTV_DEVICE_NAME`, which `qt_shell_app.py`
reads only as a legacy fallback after preferring persisted settings (settings
are written before the env sync runs, so the fallback is effectively dormant).
No other settings-bus variable has a child process reader; children otherwise
consume operator-provided variables that are set before the server starts and
never mutated at runtime. `tests/test_env_inventory.py` pins this contract.

M5 status: containment is complete. `RuntimeConfig.set_value` writes the
snapshot and mirrors only `MIRRORED_TO_ENV` (`RELAYTV_DEVICE_NAME`, kept so
the qt_shell fallback sees the latest applied name even if the persisted
settings file is unreadable in the child). All other runtime writes no longer
touch `os.environ`; the environment is startup input (`refresh_from_env`) and
child-process inheritance only. The guardrail tests fail loudly if a
settings-bus variable gains a child process reader or an in-process consumer
reads the bus from env without updating this document.

Nuances the migration must preserve:

- `player.py` builds child-process command lines (mpv, splash, idle browser)
  from env-derived values in-process; those become snapshot reads, not env
  mirroring.
- `state.py` reads many settings-bus variables in its settings-defaults path
  (`_default_settings`, `_default_ytdlp_format`). These deliberately stay
  direct env reads: operator env is the intended default source for keys
  missing from the persisted settings file, and applied settings are always
  persisted before the env sync runs, so defaults never need the runtime bus.
  Accepted M5 nuance: if an operator hand-deletes a key from the settings
  file after changing it in the UI, the default will come from operator env
  rather than the last applied value.

M4 status: all other in-process settings-bus env reads have been migrated to
`RuntimeConfig` snapshot reads; `tests/test_env_inventory.py` pins the allowed
direct-reader set (`state.py` defaults, child processes, entrypoint,
`config.py` itself).

## Inventory

<!-- BEGIN GENERATED ENV TABLE (tests/test_env_inventory.py) -->
| Variable | Referenced in | Runtime writers | Classification |
| --- | --- | --- | --- |
| `INVIDIOUS_BASE` | `config.py`<br>`main.py`<br>`resolver.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `MPV_ARGS` | `player.py`<br>`qt_shell_app.py` | - | child process input |
| `MPV_AUDIO_DEVICE` | `config.py`<br>`player.py`<br>`routes/settings.py` | `routes/settings.py` | settings bus |
| `MPV_DEBUG` | `player.py`<br>`qt_shell_app.py` | - | child process input |
| `MPV_EXTRA_ARGS` | `player.py` | - | static env |
| `MPV_IPC_PATH` | `player.py`<br>`qt_shell_app.py`<br>`routes/__init__.py` | - | child process input |
| `MPV_LOG_FILE` | `player.py`<br>`qt_shell_app.py` | - | child process input |
| `RELAYTV_ACCESS_LOG` | `debug.py` | - | static env |
| `RELAYTV_ACCESS_LOG_HOT_PATHS` | `debug.py` | - | static env |
| `RELAYTV_ACCESS_LOG_LEVEL` | `debug.py` | - | static env |
| `RELAYTV_API_TOKEN` | `api_auth.py`<br>`config.py` | - | static env |
| `RELAYTV_ARM_DEFAULT_QUALITY` | `state.py`<br>`ytdlp_format_policy.py` | - | static env |
| `RELAYTV_ARM_ENFORCE_SAFE_YTDL_FORMAT` | `ytdlp_format_policy.py` | - | static env |
| `RELAYTV_ARM_FAST_PROFILE` | `player.py`<br>`qt_shell_app.py` | - | child process input |
| `RELAYTV_AUTO_STREAM_PROFILE` | `state.py`<br>`ytdlp_format_policy.py` | - | static env |
| `RELAYTV_BANNER_PATH` | `qt_shell_app.py`<br>`routes/assets.py` | - | child process input |
| `RELAYTV_CEC` | `config.py`<br>`player.py`<br>`routes/settings.py`<br>`state.py` | `routes/settings.py` | settings bus |
| `RELAYTV_CEC_ALLOW_REQUEST_OVERRIDE` | `player.py` | - | static env |
| `RELAYTV_CEC_AUTO_ON_SWITCH` | `player.py` | - | static env |
| `RELAYTV_CEC_ENABLED` | `config.py`<br>`player.py`<br>`routes/settings.py`<br>`state.py` | `routes/settings.py` | settings bus |
| `RELAYTV_CEC_MONITOR` | `player.py` | - | static env |
| `RELAYTV_CEC_PHYS_ADDR` | `player.py` | - | static env |
| `RELAYTV_CURSOR_MODE` | `qt_shell_app.py` | - | child process input |
| `RELAYTV_DEBUG` | `debug.py`<br>`player.py`<br>`qt_shell_app.py` | - | child process input |
| `RELAYTV_DEFAULT_VOLUME` | `state.py` | - | static env |
| `RELAYTV_DEVICE_ID` | `device_identity.py` | - | static env |
| `RELAYTV_DEVICE_NAME` | `config.py`<br>`device_identity.py`<br>`integrations/jellyfin_receiver.py`<br>`qt_shell_app.py`<br>`routes/settings.py`<br>`state.py` | `routes/settings.py` | settings bus, child process input |
| `RELAYTV_DISABLE_WORKERS` | `main.py` | - | static env |
| `RELAYTV_DISPLAY_CAP_HEIGHT` | `video_profile.py`<br>`ytdlp_format_policy.py` | - | static env |
| `RELAYTV_DRM_CONNECTOR` | `config.py`<br>`player.py`<br>`routes/settings.py`<br>`state.py` | `routes/settings.py` | settings bus |
| `RELAYTV_DRM_VIDEO_FALLBACK_TO_X11` | `player.py` | - | static env |
| `RELAYTV_FORCE_RESOLVE_PROVIDERS` | `player.py` | - | static env |
| `RELAYTV_HEADLESS_REMOTE_DISPLAY` | `container_entrypoint.py` | - | pre-app entrypoint |
| `RELAYTV_HEADLESS_REMOTE_ENABLED` | `container_entrypoint.py` | `container_entrypoint.py` | pre-app entrypoint |
| `RELAYTV_HEADLESS_REMOTE_RESOLUTION` | `container_entrypoint.py` | - | pre-app entrypoint |
| `RELAYTV_HEADLESS_REMOTE_SOFTWARE` | `container_entrypoint.py` | - | pre-app entrypoint |
| `RELAYTV_HEADLESS_VNC_ENABLED` | `container_entrypoint.py` | - | pre-app entrypoint |
| `RELAYTV_HEADLESS_VNC_LISTEN` | `container_entrypoint.py` | - | pre-app entrypoint |
| `RELAYTV_HEADLESS_VNC_PASSWORD` | `container_entrypoint.py` | - | pre-app entrypoint |
| `RELAYTV_HEADLESS_VNC_PASSWORD_FILE` | `container_entrypoint.py` | - | pre-app entrypoint |
| `RELAYTV_HEADLESS_VNC_PORT` | `container_entrypoint.py` | - | pre-app entrypoint |
| `RELAYTV_HISTORY_FILE` | `state.py` | - | static env |
| `RELAYTV_HISTORY_LIMIT` | `state.py` | - | static env |
| `RELAYTV_HOST_PROFILE` | `container_entrypoint.py` | - | pre-app entrypoint |
| `RELAYTV_HOST_SESSION_TYPE` | `player.py`<br>`qt_shell_app.py`<br>`routes/__init__.py` | - | child process input |
| `RELAYTV_IDLE_BROWSER` | `player.py` | - | static env |
| `RELAYTV_IDLE_DASHBOARD_ENABLED` | `config.py`<br>`player.py`<br>`routes/settings.py`<br>`state.py`<br>`x11_overlay.py` | `routes/settings.py` | settings bus |
| `RELAYTV_IDLE_NOTIFICATIONS_ENABLED` | `config.py`<br>`player.py`<br>`routes/settings.py`<br>`state.py`<br>`x11_overlay.py` | `routes/settings.py` | settings bus |
| `RELAYTV_IDLE_QR_ENABLED` | `config.py`<br>`routes/settings.py`<br>`state.py` | `routes/settings.py` | settings bus |
| `RELAYTV_IDLE_QR_SIZE` | `config.py`<br>`routes/settings.py`<br>`state.py` | `routes/settings.py` | settings bus |
| `RELAYTV_IDLE_URL` | `player.py` | - | static env |
| `RELAYTV_IMAGE_CREATED` | `routes/app_info.py` | - | static env |
| `RELAYTV_IMAGE_REVISION` | `routes/app_info.py` | - | static env |
| `RELAYTV_IMAGE_SOURCE` | `routes/app_info.py` | - | static env |
| `RELAYTV_IMAGE_VERSION` | `device_identity.py`<br>`routes/app_info.py` | - | static env |
| `RELAYTV_IPTV_CHECK_BATCH` | `integrations/iptv_service.py` | - | static env |
| `RELAYTV_IPTV_CHECK_INTERVAL_SEC` | `integrations/iptv_service.py` | - | static env |
| `RELAYTV_IPTV_DB_PATH` | `integrations/iptv_service.py` | - | static env |
| `RELAYTV_IPTV_ENABLED` | `config.py`<br>`integrations/iptv_service.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_IPTV_FETCH_TIMEOUT_SEC` | `integrations/iptv_service.py` | - | static env |
| `RELAYTV_IPTV_MAX_CHANNELS` | `integrations/iptv_service.py` | - | static env |
| `RELAYTV_IPTV_MAX_PLAYLIST_BYTES` | `integrations/iptv_service.py` | - | static env |
| `RELAYTV_IPTV_PROBE_TIMEOUT_SEC` | `integrations/iptv_service.py` | - | static env |
| `RELAYTV_JELLYFIN_ADJACENT_SEASON_PROBE_MAX` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_API_KEY` | `config.py`<br>`integrations/jellyfin_receiver.py`<br>`main.py`<br>`state.py` | `main.py` | settings bus |
| `RELAYTV_JELLYFIN_AUDIO_LANG` | `config.py`<br>`integrations/jellyfin_service.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py` | `integrations/jellyfin_service.py`<br>`main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_JELLYFIN_AUTH_ENABLED` | `config.py`<br>`integrations/jellyfin_receiver.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_JELLYFIN_AUTH_TIMEOUT_SEC` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_AUTO_REGISTER` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_CATALOG_MAX_ENTRIES` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_CATALOG_TTL_SEC` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_CLIENT_NAME` | `config.py`<br>`integrations/jellyfin_receiver.py`<br>`routes/settings.py` | `routes/settings.py` | settings bus |
| `RELAYTV_JELLYFIN_CLIENT_VERSION` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_COMMAND_ID_TTL_SEC` | `integrations/jellyfin_service.py` | - | static env |
| `RELAYTV_JELLYFIN_COMPLETE_RATIO` | `integrations/jellyfin_receiver.py`<br>`integrations/jellyfin_service.py`<br>`player.py` | - | static env |
| `RELAYTV_JELLYFIN_COMPLETE_REMAINING_SEC` | `integrations/jellyfin_receiver.py`<br>`integrations/jellyfin_service.py`<br>`player.py` | - | static env |
| `RELAYTV_JELLYFIN_DETAIL_TTL_SEC` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_DEVICE_ID` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_DEVICE_NAME` | `config.py`<br>`integrations/jellyfin_receiver.py`<br>`routes/settings.py` | `routes/settings.py` | settings bus |
| `RELAYTV_JELLYFIN_ENABLED` | `config.py`<br>`integrations/jellyfin_receiver.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_JELLYFIN_HEARTBEAT_SEC` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_ITEM_TIMEOUT_SEC` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_MAX_STREAMING_BITRATE` | `integrations/jellyfin_service.py` | - | static env |
| `RELAYTV_JELLYFIN_METADATA_TTL_SEC` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_NATIVE_AUTO_TRANSCODE` | `integrations/jellyfin_service.py` | - | static env |
| `RELAYTV_JELLYFIN_PASSWORD` | `config.py`<br>`integrations/jellyfin_receiver.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_JELLYFIN_PLAYBACK_MODE` | `config.py`<br>`integrations/jellyfin_service.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_JELLYFIN_PLAYING_PATH` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_PLAY_DEBOUNCE_SEC` | `integrations/jellyfin_service.py` | - | static env |
| `RELAYTV_JELLYFIN_PROGRESS_PATH` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_PROGRESS_TIMEOUT_SEC` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_RECENT_STOP_SUPPRESS_SEC` | `player.py` | - | static env |
| `RELAYTV_JELLYFIN_REGISTER_RETRY` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_REGISTER_RETRY_BASE_SEC` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_REGISTER_RETRY_MAX_SEC` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_REGISTER_TIMEOUT_SEC` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_SEARCH_TTL_SEC` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_SERVER_TYPE` | `config.py`<br>`integrations/jellyfin_receiver.py`<br>`routes/settings.py`<br>`state.py` | `integrations/jellyfin_receiver.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_JELLYFIN_SERVER_URL` | `config.py`<br>`integrations/jellyfin_receiver.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_JELLYFIN_STOPPED_DEDUPE_SEC` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_STOPPED_PATH` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_STOPPED_TIMEOUT_SEC` | `integrations/jellyfin_receiver.py` | - | static env |
| `RELAYTV_JELLYFIN_SUB_LANG` | `config.py`<br>`integrations/jellyfin_service.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py` | `integrations/jellyfin_service.py`<br>`main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_JELLYFIN_UI_ACTION_DEDUPE_SEC` | `integrations/jellyfin_service.py` | - | static env |
| `RELAYTV_JELLYFIN_USERNAME` | `config.py`<br>`integrations/jellyfin_receiver.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_JELLYFIN_USER_ID` | `config.py`<br>`integrations/jellyfin_receiver.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_JELLYFIN_WS_CONNECT_TIMEOUT_SEC` | `integrations/jellyfin_ws.py` | - | static env |
| `RELAYTV_JELLYFIN_WS_ENABLED` | `integrations/jellyfin_ws.py` | - | static env |
| `RELAYTV_JELLYFIN_WS_RETRY_BASE_SEC` | `integrations/jellyfin_ws.py` | - | static env |
| `RELAYTV_JELLYFIN_WS_RETRY_MAX_SEC` | `integrations/jellyfin_ws.py` | - | static env |
| `RELAYTV_LOGO_IMAGE` | `qt_shell_app.py` | - | child process input |
| `RELAYTV_LOGO_PATH` | `routes/__init__.py`<br>`routes/assets.py` | - | static env |
| `RELAYTV_LOG_LEVEL` | `debug.py` | - | static env |
| `RELAYTV_MDNS_BROWSE_ENABLED` | `discovery_mdns.py` | - | static env |
| `RELAYTV_MDNS_BROWSE_REFRESH_SEC` | `discovery_mdns.py` | - | static env |
| `RELAYTV_MDNS_BROWSE_TTL_SEC` | `discovery_mdns.py` | - | static env |
| `RELAYTV_MDNS_ENABLED` | `discovery_mdns.py` | - | static env |
| `RELAYTV_MDNS_HOST` | `device_identity.py` | - | static env |
| `RELAYTV_MDNS_INSTANCE_SUFFIX` | `discovery_mdns.py` | - | static env |
| `RELAYTV_MDNS_SERVER` | `discovery_mdns.py` | - | static env |
| `RELAYTV_MDNS_SERVICE_TYPE` | `discovery_mdns.py` | - | static env |
| `RELAYTV_MODE` | `container_entrypoint.py`<br>`player.py`<br>`routes/__init__.py` | `container_entrypoint.py` | entrypoint input |
| `RELAYTV_MPV_AUTO_TUNE` | `player.py` | - | static env |
| `RELAYTV_MPV_IPC_RETRIES` | `player.py` | - | static env |
| `RELAYTV_MPV_IPC_RETRY_BACKOFF_SEC` | `player.py` | - | static env |
| `RELAYTV_MPV_POLL_CACHE_SEC` | `player.py` | - | static env |
| `RELAYTV_MPV_POLL_CACHE_STALE_SEC` | `player.py` | - | static env |
| `RELAYTV_MPV_POLL_IPC_TIMEOUT_SEC` | `player.py` | - | static env |
| `RELAYTV_MPV_SEAMLESS_REPLACE` | `player.py` | - | static env |
| `RELAYTV_MPV_STARTUP_TIMEOUT` | `player.py` | - | static env |
| `RELAYTV_MPV_UPNEXT_CONSUME_MAX_TIMEPOS_SEC` | `player.py` | - | static env |
| `RELAYTV_MPV_UPNEXT_MIN_POSITION_DROP_SEC` | `player.py` | - | static env |
| `RELAYTV_MPV_YTDL` | `player.py` | - | static env |
| `RELAYTV_MPV_YTDL_PATH` | `player.py` | - | static env |
| `RELAYTV_MPV_YTDL_RAW_OPTIONS` | `player.py` | - | static env |
| `RELAYTV_NATURAL_IDLE_ENSURE_DELAY_SEC` | `player.py` | - | static env |
| `RELAYTV_NATURAL_IDLE_SETTLE_SEC` | `player.py` | - | static env |
| `RELAYTV_OVERLAY_CLICKTHROUGH` | `overlay_app.py`<br>`x11_overlay.py` | - | child process input |
| `RELAYTV_OVERLAY_DEBUG_BG` | `routes/__init__.py` | - | static env |
| `RELAYTV_OVERLAY_LOG` | `x11_overlay.py` | - | static env |
| `RELAYTV_OVERLAY_OSD_DEBUG` | `player.py`<br>`routes/__init__.py` | - | static env |
| `RELAYTV_OVERLAY_TOAST_IMAGES` | `routes/__init__.py` | - | static env |
| `RELAYTV_OVERLAY_URL` | `overlay_app.py` | - | child process input |
| `RELAYTV_PEERS_FILE` | `peers.py` | - | static env |
| `RELAYTV_PLAYBACK_END_MARGIN_SEC` | `player.py` | - | static env |
| `RELAYTV_PLAYBACK_IDLE_CONFIRM_SEC` | `player.py` | - | static env |
| `RELAYTV_PLAYBACK_NOTIFY_DISPLAY_SEC` | `routes/__init__.py` | - | static env |
| `RELAYTV_PLAYBACK_NOTIFY_FADE_MS` | `routes/__init__.py` | - | static env |
| `RELAYTV_PLAYBACK_RUNTIME_GAP_CONFIRM_SEC` | `player.py` | - | static env |
| `RELAYTV_PLAYBACK_START_TIMEOUT_SEC` | `player.py` | - | static env |
| `RELAYTV_PLAYBACK_TRANSITION_SEC` | `player.py` | - | static env |
| `RELAYTV_PLAYER_BACKEND` | `player.py` | - | static env |
| `RELAYTV_PORT` | `config.py` | - | static env |
| `RELAYTV_POSTLIVE_RELAY` | `postlive_relay.py` | - | static env |
| `RELAYTV_QT_AUDIO_RECOVERY_COOLDOWN` | `player.py` | - | static env |
| `RELAYTV_QT_AUDIO_WATCHDOG_INTERVAL` | `player.py` | - | static env |
| `RELAYTV_QT_CURSOR_AUTOHIDE` | `qt_shell_app.py` | - | child process input |
| `RELAYTV_QT_CURSOR_AUTOHIDE_MS` | `qt_shell_app.py` | - | child process input |
| `RELAYTV_QT_CURSOR_AUTOHIDE_SEC` | `qt_shell_app.py` | - | child process input |
| `RELAYTV_QT_CURSOR_DEBUG` | `qt_shell_app.py` | - | child process input |
| `RELAYTV_QT_CURSOR_HIDE_MS` | `qt_shell_app.py` | - | child process input |
| `RELAYTV_QT_CURSOR_MODE` | `qt_shell_app.py` | - | child process input |
| `RELAYTV_QT_CURSOR_REFRESH_MS` | `qt_shell_app.py` | - | child process input |
| `RELAYTV_QT_EXTERNAL_HEALTH_GRACE_SEC` | `player.py` | - | static env |
| `RELAYTV_QT_EXTERNAL_HEALTH_TIMEOUT_SEC` | `player.py` | - | static env |
| `RELAYTV_QT_EXTERNAL_MPV_ARGS` | `player.py` | - | static env |
| `RELAYTV_QT_EXTERNAL_RENDERER_ORDER` | `player.py` | - | static env |
| `RELAYTV_QT_EXTERNAL_WAYLAND_PROFILE` | `player.py` | - | static env |
| `RELAYTV_QT_LIBMPV` | `qt_shell_app.py` | - | child process input |
| `RELAYTV_QT_LIBMPV_FRAME_MS` | `qt_shell_app.py` | - | child process input |
| `RELAYTV_QT_LIBMPV_TOPLEVEL_OVERLAY` | `qt_shell_app.py` | - | child process input |
| `RELAYTV_QT_NATIVE_IDLE` | `qt_shell_app.py`<br>`routes/__init__.py` | - | child process input |
| `RELAYTV_QT_NATIVE_IDLE_TOPLEVEL` | `qt_shell_app.py` | - | child process input |
| `RELAYTV_QT_NATIVE_TOASTS` | `qt_shell_app.py`<br>`routes/__init__.py` | - | child process input |
| `RELAYTV_QT_NATIVE_TOASTS_TOPLEVEL` | `qt_shell_app.py` | - | child process input |
| `RELAYTV_QT_OVERLAY_ENABLED` | `qt_shell_app.py`<br>`routes/__init__.py` | - | child process input |
| `RELAYTV_QT_OVERLAY_HEADLESS` | `qt_shell_app.py` | - | child process input |
| `RELAYTV_QT_OVERLAY_SOFTWARE` | `overlay_app.py`<br>`qt_shell_app.py`<br>`routes/__init__.py` | - | child process input |
| `RELAYTV_QT_OVERLAY_TOPLEVEL` | `qt_shell_app.py` | - | child process input |
| `RELAYTV_QT_OVERLAY_URL` | `player.py`<br>`qt_shell_app.py`<br>`routes/__init__.py` | - | child process input |
| `RELAYTV_QT_RESOLVE_PRESTOP` | `player.py` | - | static env |
| `RELAYTV_QT_RUNTIME_CONTROL_FILE` | `player.py`<br>`qt_shell_app.py` | - | child process input |
| `RELAYTV_QT_RUNTIME_CONTROL_WAIT_SEC` | `player.py` | - | static env |
| `RELAYTV_QT_RUNTIME_MODE` | `container_entrypoint.py`<br>`player.py` | `container_entrypoint.py` | entrypoint input |
| `RELAYTV_QT_RUNTIME_PAUSE_WAIT_SEC` | `player.py` | - | static env |
| `RELAYTV_QT_RUNTIME_PLAYING_GRACE_SEC` | `player.py` | - | static env |
| `RELAYTV_QT_RUNTIME_SEEK_WAIT_SEC` | `player.py` | - | static env |
| `RELAYTV_QT_RUNTIME_STATUS_FILE` | `player.py`<br>`qt_shell_app.py` | - | child process input |
| `RELAYTV_QT_SHELL_BOOT_GRACE_SEC` | `player.py` | - | static env |
| `RELAYTV_QT_SHELL_DISPLAY_SETTLE_SEC` | `player.py` | - | static env |
| `RELAYTV_QT_SHELL_FD_CRITICAL_THRESHOLD` | `player.py` | - | static env |
| `RELAYTV_QT_SHELL_FD_LIMIT` | `player.py` | - | static env |
| `RELAYTV_QT_SHELL_FD_WARN_THRESHOLD` | `player.py` | - | static env |
| `RELAYTV_QT_SHELL_MODULE` | `container_entrypoint.py`<br>`player.py`<br>`routes/__init__.py` | - | entrypoint input |
| `RELAYTV_QT_SHELL_MPV_ARGS` | `container_entrypoint.py`<br>`player.py`<br>`qt_shell_app.py` | - | child process input, entrypoint input |
| `RELAYTV_QT_SHELL_SUPERVISOR` | `player.py` | - | static env |
| `RELAYTV_QT_SHELL_SUPERVISOR_COOLDOWN_SEC` | `player.py` | - | static env |
| `RELAYTV_QT_SHELL_SUPERVISOR_INTERVAL` | `player.py` | - | static env |
| `RELAYTV_QT_SHELL_VIDEO_GRACE_SEC` | `player.py` | - | static env |
| `RELAYTV_QT_TRACKLIST_REFRESH_SEC` | `qt_shell_app.py` | - | child process input |
| `RELAYTV_QUALITY_CAP` | `config.py`<br>`routes/settings.py`<br>`state.py`<br>`ytdlp_format_policy.py` | `routes/settings.py` | settings bus |
| `RELAYTV_QUALITY_MODE` | `config.py`<br>`routes/settings.py`<br>`state.py`<br>`ytdlp_format_policy.py` | `routes/settings.py` | settings bus |
| `RELAYTV_QUEUE_FILE` | `state.py` | - | static env |
| `RELAYTV_QUEUE_HANDOFF_CONFIRM_POLLS` | `player.py` | - | static env |
| `RELAYTV_QUEUE_HANDOFF_CONFIRM_POLL_INTERVAL_SEC` | `player.py` | - | static env |
| `RELAYTV_QUEUE_HANDOFF_SUPPRESS_SEC` | `player.py` | - | static env |
| `RELAYTV_QUEUE_PREFETCH_PROVIDERS` | `player.py` | - | static env |
| `RELAYTV_QUEUE_PREFETCH_TTL_SEC` | `player.py` | - | static env |
| `RELAYTV_QUEUE_TOAST_LIGHTWEIGHT_WAIT_SEC` | `routes/__init__.py` | - | static env |
| `RELAYTV_QUEUE_TOAST_METADATA_WAIT_SEC` | `routes/__init__.py` | - | static env |
| `RELAYTV_RESOLVER_LOG_LEVEL` | `debug.py` | - | static env |
| `RELAYTV_RESOLVE_PLAYBACK_TRANSITION_SEC` | `player.py` | - | static env |
| `RELAYTV_SEAMLESS_REPLACE_RETRIES` | `player.py` | - | static env |
| `RELAYTV_SEAMLESS_REPLACE_RETRY_DELAY_SEC` | `player.py` | - | static env |
| `RELAYTV_SEEK_TRANSITION_HOLD_SEC` | `routes/__init__.py` | - | static env |
| `RELAYTV_SEERR_API_KEY` | `config.py`<br>`integrations/seerr_client.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_SEERR_ENABLED` | `config.py`<br>`integrations/seerr_client.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_SEERR_REQUEST_USER_ID` | `config.py`<br>`integrations/seerr_client.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_SEERR_SERVER_URL` | `config.py`<br>`integrations/seerr_client.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_SEERR_SHARED_REQUESTS_ENABLED` | `config.py`<br>`integrations/seerr_client.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_SESSION_FILE` | `state.py` | - | static env |
| `RELAYTV_SETTINGS_FILE` | `state.py` | - | static env |
| `RELAYTV_SHELL_V2_NATIVE_PLAYING_GRACE_SEC` | `player.py` | - | static env |
| `RELAYTV_SLOW_REQUEST_MS` | `debug.py` | - | static env |
| `RELAYTV_SNAPSHOT_DIR` | `routes/snapshots.py` | - | static env |
| `RELAYTV_SPLASH` | `player.py` | - | static env |
| `RELAYTV_SPLASH_ARGS` | `player.py` | - | static env |
| `RELAYTV_SPLASH_IMAGE` | `player.py` | - | static env |
| `RELAYTV_SPLASH_X11` | `player.py` | - | static env |
| `RELAYTV_STATE_DIR` | `state.py` | - | static env |
| `RELAYTV_STATIC_DIR` | `routes/assets.py` | - | static env |
| `RELAYTV_STATUS_INCLUDE_MPV_LOG` | `routes/__init__.py` | - | static env |
| `RELAYTV_SUB_LANG` | `config.py`<br>`player.py`<br>`routes/settings.py`<br>`state.py` | `routes/settings.py` | settings bus |
| `RELAYTV_THUMB_DIR` | `qt_shell_app.py`<br>`thumb_cache.py` | - | child process input |
| `RELAYTV_THUMB_JPEG_Q` | `thumb_cache.py` | - | static env |
| `RELAYTV_THUMB_MAX_BYTES` | `thumb_cache.py` | - | static env |
| `RELAYTV_THUMB_MAX_FILES` | `thumb_cache.py` | - | static env |
| `RELAYTV_THUMB_MAX_TOTAL_BYTES` | `thumb_cache.py` | - | static env |
| `RELAYTV_THUMB_MAX_TOTAL_MB` | `thumb_cache.py` | - | static env |
| `RELAYTV_THUMB_PRUNE_INTERVAL_SEC` | `thumb_cache.py` | - | static env |
| `RELAYTV_THUMB_RETENTION_SEC` | `thumb_cache.py` | - | static env |
| `RELAYTV_THUMB_SRC_MAP_MAX` | `thumb_cache.py` | - | static env |
| `RELAYTV_THUMB_WIDTH` | `thumb_cache.py` | - | static env |
| `RELAYTV_UPDATE_CHECK_DISABLED` | `routes/app_info.py` | - | static env |
| `RELAYTV_UPDATE_CHECK_TTL_SEC` | `routes/app_info.py` | - | static env |
| `RELAYTV_UPLOADS_DIR` | `upload_store.py` | - | static env |
| `RELAYTV_UPLOAD_MAX_SIZE_GB` | `config.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_UPLOAD_PROGRESSIVE_MAX_STALL_SEC` | `upload_store.py` | - | static env |
| `RELAYTV_UPLOAD_PROGRESSIVE_MIN_THROUGHPUT_KBPS` | `upload_store.py` | - | static env |
| `RELAYTV_UPLOAD_PROGRESSIVE_MP4_READY_MB` | `upload_store.py` | - | static env |
| `RELAYTV_UPLOAD_PROGRESSIVE_WEBM_READY_MB` | `upload_store.py` | - | static env |
| `RELAYTV_UPLOAD_RETENTION_HOURS` | `config.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_VIDEO_MODE` | `config.py`<br>`player.py`<br>`routes/__init__.py`<br>`routes/settings.py`<br>`state.py` | `player.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_VIDEO_PROFILE_ALLOW_AV1` | `video_profile.py` | - | static env |
| `RELAYTV_VIDEO_PROFILE_TTL_SEC` | `video_profile.py` | - | static env |
| `RELAYTV_X11_OVERLAY` | `player.py`<br>`routes/__init__.py`<br>`x11_overlay.py` | - | static env |
| `RELAYTV_YOUTUBE_PROGRESSIVE_FIRST` | `ytdlp_format_policy.py` | - | static env |
| `RELAYTV_YTDLP_AUTO_UPDATE` | `container_entrypoint.py`<br>`main.py`<br>`routes/settings.py`<br>`state.py`<br>`ytdlp_update.py` | `main.py`<br>`routes/settings.py` | settings bus, entrypoint input |
| `RELAYTV_YTDLP_AUTO_UPDATE_INTERVAL_HOURS` | `container_entrypoint.py` | - | pre-app entrypoint |
| `RELAYTV_YTDLP_AUTO_UPDATE_POLL_SEC` | `ytdlp_update.py` | - | static env |
| `RELAYTV_YTDLP_AUTO_UPDATE_STATE_FILE` | `container_entrypoint.py` | - | pre-app entrypoint |
| `RELAYTV_YTDLP_AUTO_UPDATE_TIMEOUT_SEC` | `container_entrypoint.py` | - | pre-app entrypoint |
| `RELAYTV_YTDLP_COOKIES` | `config.py`<br>`main.py`<br>`resolver.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `RELAYTV_YTDLP_COOKIES_FROM_BROWSER` | `resolver.py` | - | static env |
| `RELAYTV_YTDLP_COOKIES_UPLOAD_PATH` | `routes/settings.py` | - | static env |
| `RELAYTV_YTDLP_JS_RUNTIME` | `resolver.py` | - | static env |
| `RELAYTV_YTDLP_UPDATE_CHANNEL` | `container_entrypoint.py` | - | pre-app entrypoint |
| `RELAYTV_YTDLP_UPDATE_DIR` | `container_entrypoint.py` | - | pre-app entrypoint |
| `RELAYTV_YTDLP_USE_NODE` | `resolver.py` | - | static env |
| `USE_INVIDIOUS` | `config.py`<br>`main.py`<br>`resolver.py`<br>`routes/settings.py`<br>`state.py` | `main.py`<br>`routes/settings.py` | settings bus |
| `YTDLP_ARGS` | `resolver.py` | - | static env |
| `YTDLP_COOKIES` | `resolver.py`<br>`routes/settings.py`<br>`state.py` | - | static env |
| `YTDLP_COOKIES_FROM_BROWSER` | `resolver.py` | - | static env |
| `YTDLP_FORMAT` | `config.py`<br>`routes/settings.py`<br>`state.py`<br>`ytdlp_format_policy.py` | `routes/settings.py` | settings bus |
| `YTDLP_FORMAT_BITCHUTE` | `ytdlp_format_policy.py` | - | static env |
| `YTDLP_FORMAT_RUMBLE` | `ytdlp_format_policy.py` | - | static env |
| `YTDLP_FORMAT_TIKTOK` | `ytdlp_format_policy.py` | - | static env |
| `YTDLP_FORMAT_TWITCH` | `ytdlp_format_policy.py` | - | static env |
| `YTDLP_FORMAT_YOUTUBE` | `ytdlp_format_policy.py` | - | static env |
| `YTDLP_INFO_TTL_SEC` | `resolver.py` | - | static env |
| `YTDLP_JS_RUNTIME` | `resolver.py` | - | static env |
<!-- END GENERATED ENV TABLE -->
