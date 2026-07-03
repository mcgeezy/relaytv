# Phase 4 Jellyfin Route-Surface Inventory

This document is the measured baseline for the Phase 4 Jellyfin product
service work (`docs/ARCHITECTURE_PHASE_4_ROADMAP.md`). It answers one
question: which Jellyfin functions are defined in the routes package today?

`integrations/jellyfin_receiver.py` owns transport/session/catalog and is not
part of this scan. Everything listed below either stays as HTTP surface
(endpoint handlers, request guards, UI-event glue) or migrates into
`integrations/jellyfin_service.py` during Phase 4.
`tests/test_jellyfin_inventory.py` pins the allowed function set per routes
module so each milestone tightens the contract explicitly — the generated
listing and the pinned sets must both change in the same commit as the
migration itself.

Scan rule: any `def` in `app/relaytv_app/routes/` whose name mentions
jellyfin (case-insensitive). Helpers without jellyfin in the name (for
example `_first_nonempty_str`, `_normalize_lang_pref`) ride along with the
cluster that uses them and are handled per milestone.

Regenerate the listing after intentional changes with:

    PYTHONPATH=app python3 tests/test_jellyfin_inventory.py --write

## Key Finding

At phase start there are 117 Jellyfin function definitions across four routes
modules. The product core (~2,700 lines, 63 functions) lives in
`routes/__init__.py`; `routes/jellyfin.py` holds every endpoint handler plus
25 thin shims that delegate straight back into `routes/__init__.py`, so the
HTTP module and the product logic are mutually entangled. Notable
concentrations:

- `_jellyfin_integration_command_impl` (~580 lines) is the single largest
  product function and owns command ingress end-to-end.
- The stopped/progress hint family is duplicated: `routes/__init__.py` owns
  one copy and `player.py` carries a parallel
  `_emit_jellyfin_stopped_from_now` path with its own dedupe state.

## Containment Contract (Phase 4 end state)

To be finalized in M7. Intended end state:

- `integrations/jellyfin_service.py` owns command normalization, playable
  item resolution, direct/transcode policy, track preference handling,
  metadata enrichment, and stopped/progress payload creation.
- `routes/jellyfin.py` keeps endpoint handlers, request models, HTTP guards
  (`_require_jellyfin_catalog_ready*`), and UI-event pushes.
- `routes/__init__.py` keeps only compatibility aliases that existing tests
  observe, each a direct assignment to the service function.
- `routes/assets.py` keeps its two static-asset endpoints.

## Review Scenario Coverage Baseline

Jellyfin behaviors guarded at phase start (route-level, via
`tests/test_jellyfin_routes.py` unless noted):

- Command ingress: pause dispatch, disabled-receiver guard, duplicate UI
  action suppression, resume preserving queue, play-next mapping.
- Catalog browse: home/search/movies/series/episodes pagination and status.
- Track selection: audio options runtime selection, in-place audio switch,
  subtitle reject-unavailable, subtitle off-in-place and options-off-row
  (`tests/test_smoke.py`).
- Stopped/progress: progress snapshot payloads, stopped route snapshot
  emission.
- Connect lifecycle: connect/register/disconnect/heartbeat/push-deprecated.

Service-level tests with fake receiver/player adapters are added in M7
(`tests/test_jellyfin_service.py`).

## Jellyfin Function Listing

<!-- BEGIN GENERATED JELLYFIN ROUTE LISTING (tests/test_jellyfin_inventory.py) -->
### `routes/__init__.py` (31)

- `_effective_jellyfin_playback_mode`
- `_first_playable_jellyfin_episode`
- `_jellyfin_auto_prefers_transcode`
- `_jellyfin_complete_ratio`
- `_jellyfin_complete_remaining_sec`
- `_jellyfin_emit_progress_hint`
- `_jellyfin_emit_stopped_hint`
- `_jellyfin_emit_stopped_payload`
- `_jellyfin_enrich_now_stream_metadata`
- `_jellyfin_integration_command_impl`
- `_jellyfin_is_duplicate_command`
- `_jellyfin_played_percentage`
- `_jellyfin_progress_snapshot`
- `_jellyfin_runtime_selected_audio_stream`
- `_jellyfin_runtime_selected_subtitle_stream`
- `_jellyfin_should_suppress_duplicate_play`
- `_jellyfin_should_suppress_duplicate_ui_action`
- `_jellyfin_snap_position_ticks`
- `_jellyfin_stopped_snapshot`
- `_jellyfin_stopped_snapshot_from_now`
- `_jellyfin_target_max_streaming_bitrate`
- `_jellyfin_try_set_mpv_audio_track`
- `_jellyfin_try_set_mpv_subtitle_track`
- `_merge_jellyfin_playback_metadata`
- `_native_jellyfin_auto_transcode_guard_active`
- `_preferred_jellyfin_stream_indices`
- `_reset_jellyfin_command_state`
- `_resolve_jellyfin_playable_item`
- `_retarget_jellyfin_queue_stream_preferences`
- `_select_jellyfin_playback_url`
- `_ui_event_push_jellyfin`

### `routes/assets.py` (2)

- `_jellyfin_svg`
- `pwa_jellyfin_svg`

### `routes/jellyfin.py` (51)

- `_apply_jellyfin_stream_params`
- `_build_jellyfin_item_stream_url`
- `_extract_jellyfin_audio_stream_index_from_url`
- `_extract_jellyfin_item_id_from_url_raw`
- `_extract_jellyfin_media_source_id_from_url`
- `_extract_jellyfin_subtitle_stream_index_from_url`
- `_jellyfin_access_token`
- `_jellyfin_command_req`
- `_jellyfin_emit_progress_hint`
- `_jellyfin_enrich_now_stream_metadata`
- `_jellyfin_integration_command`
- `_jellyfin_integration_command_impl`
- `_jellyfin_progress_snapshot`
- `_jellyfin_runtime_selected_audio_stream`
- `_jellyfin_runtime_selected_subtitle_stream`
- `_jellyfin_should_suppress_duplicate_ui_action`
- `_jellyfin_stopped_snapshot`
- `_jellyfin_try_set_mpv_audio_track`
- `_jellyfin_try_set_mpv_subtitle_track`
- `_normalize_jellyfin_source_url`
- `_require_jellyfin_catalog_ready`
- `_require_jellyfin_catalog_ready_for_playback`
- `_reset_jellyfin_command_state`
- `_retarget_jellyfin_queue_stream_preferences`
- `_select_jellyfin_playback_url`
- `_ui_event_push_jellyfin`
- `jellyfin_audio_options`
- `jellyfin_audio_select`
- `jellyfin_catalog_cache_clear`
- `jellyfin_home`
- `jellyfin_integration_command`
- `jellyfin_integration_connect`
- `jellyfin_integration_disconnect`
- `jellyfin_integration_heartbeat`
- `jellyfin_integration_progress_snapshot`
- `jellyfin_integration_push`
- `jellyfin_integration_register`
- `jellyfin_integration_status`
- `jellyfin_integration_stopped`
- `jellyfin_integration_stopped_snapshot`
- `jellyfin_item_action`
- `jellyfin_item_adjacent`
- `jellyfin_item_detail`
- `jellyfin_movies`
- `jellyfin_search`
- `jellyfin_subtitle_options`
- `jellyfin_subtitle_select`
- `jellyfin_tv_series`
- `jellyfin_tv_series_episodes`
- `jellyfin_tv_series_play_all`
- `jellyfin_tv_series_seasons`

### `routes/playback.py` (1)

- `_jellyfin_emit_stopped_hint`
<!-- END GENERATED JELLYFIN ROUTE LISTING -->
