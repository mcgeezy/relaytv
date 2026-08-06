# Jellyfin Route-Surface Inventory

This document is the machine-checked containment contract for the Jellyfin
route surface (see `ARCHITECTURE.md`). It answers one question: which
Jellyfin functions are defined in the routes package?

`integrations/jellyfin_receiver.py` owns transport/session/catalog and is not
part of this scan. Everything listed below is HTTP surface (endpoint
handlers, request guards, UI-event glue); product behavior belongs in
`integrations/jellyfin_service.py`. `tests/test_jellyfin_inventory.py` pins
the allowed function set per routes module — the generated listing and the
pinned sets must both change in the same commit as any migration.

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

## Containment Contract (Phase 4 end state, M7)

`integrations/jellyfin_service.py` owns command normalization and ingress
(`handle_command`), playable item resolution, direct/transcode policy
(`select_playback_url`), track preference handling and runtime track
switching (`switch_audio_track`/`switch_subtitle_track`), metadata
enrichment, and stopped/progress payload creation. Its playback transitions
go through `playback_service` commands. What remains in the routes package
(`tests/test_jellyfin_inventory.py::EXPECTED_JELLYFIN_ROUTE_FUNCTIONS`):

- `routes/jellyfin.py`: endpoint handlers, request models, HTTP guards
  (`_require_jellyfin_catalog_ready*`), UI-event pushes, and the shims still
  called by endpoint handlers (options pickers, snapshots, dedupe reset).
  Dead delegation shims left behind by the migrations were pruned in M7.
- `routes/__init__.py`: assignment aliases to the service functions (kept so
  existing tests and cross-module late imports keep resolving) plus one thin
  def — `_jellyfin_integration_command_impl`, the adapter wrapper that
  injects route-side control dispatch and UI-event seams into
  `handle_command`.
- `routes/assets.py`: two static-asset endpoints.
- `player.py` (outside this scan) keeps its parallel stopped-hint dedupe
  path — see the Phase 4 roadmap M5 deferral note.

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

Service-level tests with fake receiver/player adapters were added in M7
(`tests/test_jellyfin_service.py`): command normalization and ticks
conversion, direct/transcode selection policy (healthy-direct, AV1 guard,
no-detail fallback, forced mode), language-preference stream indices, mpv
track selection scoring, stopped/progress payload construction, and
`handle_command` ingress with fake control/UI adapters (pause dispatch,
duplicate command-id suppression, play through `playback_service`).

## Jellyfin Function Listing

<!-- BEGIN GENERATED JELLYFIN ROUTE LISTING (tests/test_jellyfin_inventory.py) -->
### `routes/__init__.py` (2)

- `_jellyfin_integration_command_impl`
- `_ui_event_push_jellyfin`

### `routes/assets.py` (2)

- `_jellyfin_svg`
- `pwa_jellyfin_svg`

### `routes/jellyfin.py` (41)

- `_extract_jellyfin_audio_stream_index_from_url`
- `_extract_jellyfin_item_id_from_url_raw`
- `_extract_jellyfin_subtitle_stream_index_from_url`
- `_jellyfin_command_req`
- `_jellyfin_integration_command`
- `_jellyfin_integration_command_impl`
- `_jellyfin_progress_snapshot`
- `_jellyfin_runtime_selected_audio_stream`
- `_jellyfin_runtime_selected_subtitle_stream`
- `_jellyfin_should_suppress_duplicate_ui_action`
- `_jellyfin_socket_command`
- `_jellyfin_stopped_snapshot`
- `_require_jellyfin_catalog_ready`
- `_require_jellyfin_catalog_ready_for_playback`
- `_reset_jellyfin_command_state`
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
