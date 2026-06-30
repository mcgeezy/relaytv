# Phase 1 Route Inventory

Date captured: 2026-06-30

Branch: `codex/architecture-phase-1`

Purpose: preserve the current public FastAPI route surface while Phase 1 splits
`routes.py` into domain routers. This inventory is not a replacement for
`docs/API.md`; it is a refactor guardrail.

The matching automated guardrail is `tests/test_route_inventory.py`.

## Summary

- Current registered public API routes: 108 method/path registrations.
- Public endpoint paths and aliases must remain stable during Phase 1.
- Endpoint function names are included because they help detect accidental route
  shadowing or alias loss while moving code.

## Compatibility Alias Groups

These aliases are intentionally preserved:

| Group | Routes |
| --- | --- |
| Queue add | `POST /enqueue`, `POST /queue/add`, `POST /api/queue/add`, `POST /v1/queue/add` |
| Snapshot | `GET /snapshot`, `POST /snapshot` |
| Toast/overlay | `POST /overlay`, `POST /toast`, `POST /notify` |

## Target Domains

The `Target domain` column is the intended Phase 1 extraction destination. It
may change as code movement exposes dependencies; update this file and
`docs/ARCHITECTURE_PHASE_1_ROADMAP.md` when that happens.

| Target domain | Method | Path | Current endpoint |
| --- | --- | --- | --- |
| assets/pwa | `GET` | `/assets/banner.png` | `relaytv_banner_png_asset` |
| assets/pwa | `GET` | `/assets/banner.svg` | `relaytv_banner_svg_asset` |
| assets/pwa | `GET` | `/assets/logo.svg` | `relaytv_logo_svg_asset` |
| assets/pwa | `GET` | `/favicon.ico` | `favicon_ico` |
| assets/pwa | `GET` | `/manifest.json` | `pwa_manifest` |
| assets/pwa | `GET` | `/pwa/brand/banner.png` | `pwa_brand_banner_png_asset` |
| assets/pwa | `GET` | `/pwa/brand/banner.svg` | `pwa_brand_banner_svg_asset` |
| assets/pwa | `GET` | `/pwa/brand/logo.svg` | `pwa_brand_logo_svg_asset` |
| assets/pwa | `GET` | `/pwa/icon.svg` | `pwa_icon_svg` |
| assets/pwa | `GET` | `/pwa/jellyfin.svg` | `pwa_jellyfin_svg` |
| assets/pwa | `GET` | `/pwa/splash.svg` | `pwa_splash_svg` |
| assets/pwa | `GET` | `/pwa/weather/{asset_name}` | `pwa_weather_asset` |
| assets/pwa | `GET` | `/pwa/{asset_path:path}` | `pwa_static_asset` |
| assets/pwa | `GET` | `/qr/connect.svg` | `qr_connect_svg` |
| assets/pwa | `GET` | `/snapshot` | `snapshot` |
| assets/pwa | `GET` | `/snapshots/{filename}` | `get_snapshot` |
| assets/pwa | `GET` | `/sw.js` | `pwa_sw` |
| assets/pwa | `GET` | `/thumbs/{filename}` | `thumbs` |
| assets/pwa | `POST` | `/snapshot` | `snapshot` |
| jellyfin | `GET` | `/integrations/jellyfin/progress_snapshot` | `jellyfin_integration_progress_snapshot` |
| jellyfin | `GET` | `/integrations/jellyfin/status` | `jellyfin_integration_status` |
| jellyfin | `GET` | `/integrations/jellyfin/stopped_snapshot` | `jellyfin_integration_stopped_snapshot` |
| jellyfin | `GET` | `/jellyfin/audio/options` | `jellyfin_audio_options` |
| jellyfin | `GET` | `/jellyfin/home` | `jellyfin_home` |
| jellyfin | `GET` | `/jellyfin/item/{item_id}` | `jellyfin_item_detail` |
| jellyfin | `GET` | `/jellyfin/item/{item_id}/adjacent` | `jellyfin_item_adjacent` |
| jellyfin | `GET` | `/jellyfin/movies` | `jellyfin_movies` |
| jellyfin | `GET` | `/jellyfin/search` | `jellyfin_search` |
| jellyfin | `GET` | `/jellyfin/subtitle/options` | `jellyfin_subtitle_options` |
| jellyfin | `GET` | `/jellyfin/tv/series` | `jellyfin_tv_series` |
| jellyfin | `GET` | `/jellyfin/tv/series/{series_id}/episodes` | `jellyfin_tv_series_episodes` |
| jellyfin | `GET` | `/jellyfin/tv/series/{series_id}/seasons` | `jellyfin_tv_series_seasons` |
| jellyfin | `POST` | `/integrations/jellyfin/catalog/cache_clear` | `jellyfin_catalog_cache_clear` |
| jellyfin | `POST` | `/integrations/jellyfin/command` | `jellyfin_integration_command` |
| jellyfin | `POST` | `/integrations/jellyfin/connect` | `jellyfin_integration_connect` |
| jellyfin | `POST` | `/integrations/jellyfin/disconnect` | `jellyfin_integration_disconnect` |
| jellyfin | `POST` | `/integrations/jellyfin/heartbeat` | `jellyfin_integration_heartbeat` |
| jellyfin | `POST` | `/integrations/jellyfin/push` | `jellyfin_integration_push` |
| jellyfin | `POST` | `/integrations/jellyfin/register` | `jellyfin_integration_register` |
| jellyfin | `POST` | `/integrations/jellyfin/stopped` | `jellyfin_integration_stopped` |
| jellyfin | `POST` | `/jellyfin/action` | `jellyfin_item_action` |
| jellyfin | `POST` | `/jellyfin/audio/select` | `jellyfin_audio_select` |
| jellyfin | `POST` | `/jellyfin/subtitle/select` | `jellyfin_subtitle_select` |
| jellyfin | `POST` | `/jellyfin/tv/series/{series_id}/play_all` | `jellyfin_tv_series_play_all` |
| overlay/idle | `GET` | `/idle` | `idle_page` |
| overlay/idle | `GET` | `/idle/weather` | `get_idle_weather` |
| overlay/idle | `GET` | `/x11/host_urls` | `x11_host_urls` |
| overlay/idle | `GET` | `/x11/overlay` | `x11_overlay_page` |
| overlay/idle | `GET` | `/x11/overlay/events` | `x11_overlay_events` |
| overlay/idle | `POST` | `/notify` | `notify` |
| overlay/idle | `POST` | `/overlay` | `overlay` |
| overlay/idle | `POST` | `/toast` | `toast` |
| overlay/idle | `POST` | `/x11/overlay/client_state` | `x11_overlay_client_state` |
| playback/ui | `GET` | `/` | `root` |
| playback/ui | `GET` | `/playback/state` | `playback_state` |
| playback/ui | `GET` | `/share` | `share` |
| playback/ui | `GET` | `/ui` | `ui` |
| playback/ui | `GET` | `/ui/events` | `ui_events` |
| playback/ui | `POST` | `/close` | `close` |
| playback/ui | `POST` | `/mute` | `mute` |
| playback/ui | `POST` | `/next` | `next_track` |
| playback/ui | `POST` | `/now_playing/clear` | `clear_now_playing` |
| playback/ui | `POST` | `/pause` | `pause` |
| playback/ui | `POST` | `/play` | `play` |
| playback/ui | `POST` | `/play_at` | `play_at` |
| playback/ui | `POST` | `/play_now` | `play_now` |
| playback/ui | `POST` | `/play_temporary` | `play_temporary` |
| playback/ui | `POST` | `/play_temporary/cancel` | `play_temporary_cancel` |
| playback/ui | `POST` | `/playback/play` | `playback_play` |
| playback/ui | `POST` | `/playback/toggle` | `playback_toggle` |
| playback/ui | `POST` | `/previous` | `previous` |
| playback/ui | `POST` | `/resume` | `resume` |
| playback/ui | `POST` | `/resume/clear` | `clear_resumable_session` |
| playback/ui | `POST` | `/resume_session` | `resume_session` |
| playback/ui | `POST` | `/seek` | `seek` |
| playback/ui | `POST` | `/seek_abs` | `seek_abs` |
| playback/ui | `POST` | `/smart` | `smart` |
| playback/ui | `POST` | `/stop` | `stop` |
| playback/ui | `POST` | `/toggle_pause` | `toggle_pause` |
| playback/ui | `POST` | `/volume` | `volume` |
| queue/history | `GET` | `/history` | `history` |
| queue/history | `GET` | `/queue` | `queue` |
| queue/history | `POST` | `/api/queue/add` | `enqueue` |
| queue/history | `POST` | `/clear` | `clear` |
| queue/history | `POST` | `/enqueue` | `enqueue` |
| queue/history | `POST` | `/history/clear` | `history_clear` |
| queue/history | `POST` | `/history/play` | `history_play` |
| queue/history | `POST` | `/queue/add` | `enqueue` |
| queue/history | `POST` | `/queue/dedupe` | `queue_dedupe` |
| queue/history | `POST` | `/queue/move` | `queue_move` |
| queue/history | `POST` | `/queue/remove` | `queue_remove` |
| queue/history | `POST` | `/v1/queue/add` | `enqueue` |
| settings | `GET` | `/settings` | `get_settings` |
| settings | `POST` | `/settings` | `update_settings` |
| settings | `POST` | `/settings/youtube/cookies` | `upload_youtube_cookies` |
| settings | `POST` | `/settings/youtube/cookies/clear` | `clear_youtube_cookies` |
| status/devices | `GET` | `/app/info` | `app_info` |
| status/devices | `GET` | `/devices` | `get_devices` |
| status/devices | `GET` | `/discovery/status` | `discovery_status` |
| status/devices | `GET` | `/health` | `health` |
| status/devices | `GET` | `/notifications/capabilities` | `notifications_capabilities` |
| status/devices | `GET` | `/runtime/capabilities` | `runtime_capabilities` |
| status/devices | `GET` | `/status` | `status` |
| status/devices | `GET` | `/tv/status` | `tv_status` |
| uploads | `GET` | `/media/uploads/{upload_id}/{filename}` | `get_uploaded_media` |
| uploads | `POST` | `/ingest/media` | `ingest_media` |
| uploads | `POST` | `/ingest/media/enqueue` | `ingest_media_enqueue` |
| uploads | `POST` | `/ingest/media/play` | `ingest_media_play` |
