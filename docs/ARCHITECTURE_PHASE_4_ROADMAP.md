# Phase 4 Architecture Roadmap

Date started: 2026-07-03

Branch: `codex/architecture-phase-4` (cut from `main` at the Phase 3 squash
merge, PR #23 / `6fc7366`)

Phase 4 goal: make Jellyfin behavior testable without route context by
extracting the Jellyfin product logic — command interpretation, stream URL
construction, direct/transcode selection, playable item resolution,
track/subtitle preference handling, playback metadata merging, and
stopped/progress hint emission — into `integrations/jellyfin_service.py`.
`integrations/jellyfin_receiver.py` stays the transport/session/catalog
adapter, and the routes modules keep only HTTP guards, request models, ack
shaping, and UI events.

Related review: `docs/ARCHITECTURE_REVIEW.md` (Finding 5 and the Phase 4
roadmap section)

## Working Rules

- Keep Phase 4 work on `codex/architecture-phase-4` until the phase is
  complete.
- Merge small focused PRs into this branch instead of directly into `main`.
- Keep public endpoint paths, response shapes, playback semantics, and UI
  behavior stable.
- Update this file whenever a milestone starts, completes, changes scope, or
  uncovers follow-up work.
- Migrate one functional cluster at a time with guardrail tests in place
  before each move, mirroring the Phase 1/2/3 discipline.
- Facade first, policy later: implementations move wholesale into the service
  and the routes modules keep 1:1 module-level aliases, so route behavior is
  provably unchanged. Test monkeypatches are repointed mechanically to the
  service module in the same commit as each move.
- The service depends on `jellyfin_receiver` (transport), `playback_service`
  (transitions), `player` (mpv control), `state`, and `runtime_config` — never
  on the routes package. Route-facing side effects (UI event pushes, HTTP
  errors) stay in routes or are passed in as parameters.
- Playback transition writes inside migrated Jellyfin code route through
  `playback_service` commands where a command exists; any remaining direct
  write becomes a pinned, documented exception in the Phase 3 transition
  inventory.
- Only open the final `codex/architecture-phase-4` to `main` PR after all
  Phase 4 validation gates pass.
- No release until the architecture roadmap completes (user decision
  2026-07-03); phases ship as `refactor:` PRs that do not trigger
  release-please.

## Scope

In scope:

- An `integrations/jellyfin_service.py` module owning the product-level
  behavior named in the review: command normalization, playable item
  resolution, direct/transcode policy, queue/play actions, track preference
  handling, and stopped/progress payload creation.
- Migrating the ~2,700-line Jellyfin block in `routes/__init__.py`
  (63 functions, lines ~2307–4993 at phase start) into the service.
- Collapsing the delegation shims in `routes/jellyfin.py` (51 wrapper
  functions that currently call back into `routes/__init__.py`) onto the
  service.
- A machine-checked Jellyfin surface inventory (mirroring the Phase 2 env and
  Phase 3 transition inventory patterns) that pins which Jellyfin product
  functions may live in routes modules, tightening as milestones land.
- Tightening the Phase 3 transition-writer pins: `routes/jellyfin.py` stops
  writing transition globals; the Jellyfin command implementation leaves
  `routes/__init__.py`.
- Consolidating `player.py`'s duplicated stopped-hint path
  (`_emit_jellyfin_stopped_from_now`, `_jellyfin_complete_ratio`,
  `_RECENT_JELLYFIN_STOP` dedupe) onto the service if the seam allows a
  behavior-preserving move.
- Service-level tests with fake receiver/player adapters
  (`tests/test_jellyfin_service.py`).

Out of scope for Phase 4:

- Optional API token auth (Phase 5) and the operations test matrix (Phase 6).
- Rewriting `jellyfin_receiver.py` transport/cache/auth internals — it keeps
  HTTP, session, and catalog ownership.
- Changing Jellyfin endpoint paths, request models, or response shapes.
- The `_status_payload` session reconciliation in `routes/__init__.py`
  (status-side self-healing; still a candidate for a future
  `playback_service` reconcile command).
- Persisted file format changes.
- Behavior changes to Jellyfin semantics — if a behavior bug is discovered,
  document it here unless the user asks for an immediate fix.

## Baseline (measured at phase start)

- `routes/__init__.py` (6,353 lines): 63 Jellyfin function definitions
  spanning ~2,700 lines (extraction/normalization helpers, stream URL
  builders, transcode policy, playable item resolution, track selection and
  runtime switching, metadata enrichment, progress/stopped snapshot family,
  duplicate-command suppression, and the ~580-line
  `_jellyfin_integration_command_impl`), plus `_ui_event_push_jellyfin` and
  the `JellyfinCommandReq` model.
- `routes/jellyfin.py` (1,377 lines): all Jellyfin HTTP endpoints plus 51
  thin wrappers delegating back into `routes/__init__.py`; route handlers
  for audio/subtitle select contain in-place track-switch product logic and
  the module's 6 transition-global writes (4 `set_now_playing`,
  2 `set_session_state`).
- `integrations/jellyfin_receiver.py` (2,614 lines): transport, auth/session,
  catalog cache, item metadata/detail, `resolve_playback_url` — stays the
  transport adapter.
- `player.py`: duplicated stopped-hint emission
  (`_emit_jellyfin_stopped_from_now`, own `_jellyfin_complete_ratio`,
  `_RECENT_JELLYFIN_STOP` dedupe) called from stop/advance paths.
- Transition-writer pins at phase start
  (`tests/test_transition_inventory.py`): `routes/jellyfin.py` writes
  `NOW_PLAYING` and `SESSION_STATE`; the Jellyfin command impl contributes to
  `routes/__init__.py`'s pinned writes.
- Existing guardrails: `tests/test_jellyfin_routes.py` (706 lines; patches 33
  `routes.*` and 33 `routes.jellyfin_receiver.*` attributes), Jellyfin cases
  in `tests/test_smoke.py`, and the route inventory test.

## Milestones

### M0: Roadmap Foundation And Branch Setup

Status: complete

- Branch `codex/architecture-phase-4` cut from `main` after the Phase 3
  squash merge (PR #23, `6fc7366`).
- This roadmap document, with measured baseline.
- `docs/README.md` link.

### M1: Jellyfin Surface Inventory And Guardrail Baseline

Status: complete

- `tests/test_jellyfin_inventory.py`: scans the routes package for Jellyfin
  product function definitions and pins the allowed set per module; each
  migration milestone must tighten the pins in the same commit.
- `docs/ARCHITECTURE_PHASE_4_JELLYFIN_INVENTORY.md`: generated table plus the
  containment contract, regenerated via `--write`.
- Confirm scenario coverage baseline: which Jellyfin behaviors are guarded by
  route tests today (command ingress actions, stream selection, track
  select, stopped/progress emission).

### M2: Service Skeleton And Pure Helper Migration

Status: complete

- Create `integrations/jellyfin_service.py` with module logger and section
  layout.
- Move the pure helpers (no state writes, no player calls): item id/media
  source/url/playlist/play-mode extraction, action normalization, ticks and
  seek/start/volume extraction, command id extraction, source URL
  normalization, stream/transcode URL builders, stream/media-source param
  appliers, url-origin and media-url classification.
- Routes modules keep 1:1 aliases; monkeypatched names repointed
  mechanically.

### M3: Stream Selection, Transcode Policy, And Playable Resolution

Status: complete

- Note: `resolve_playable_item` keeps raising `HTTPException` (404) inside
  the service so callers' behavior is unchanged; a service-level error type
  is a candidate follow-up once the command ingress (M6) is the only caller.

- Move `_select_jellyfin_playback_url`, `_jellyfin_auto_prefers_transcode`,
  `_jellyfin_target_max_streaming_bitrate`,
  `_native_jellyfin_auto_transcode_guard_active`, playback-mode
  normalize/effective helpers, `_resolve_jellyfin_playable_item`, and
  `_first_playable_jellyfin_episode`.

### M4: Track Preference And Runtime Track Switching

Status: complete

- Landed in two commits: the helper family move, then the audio/subtitle
  select handler cores as `switch_audio_track`/`switch_subtitle_track`
  service commands.
- `playback_service` gained `update_now_playing(now)` (in-place metadata
  refresh) and an optional `reason` on `mark_paused`, so the switch commands
  write no transition globals directly; `routes/jellyfin.py` left the
  NOW_PLAYING/SESSION_STATE writer pins entirely (originally promised for
  M6).
- `emit_progress_hint` moved early (M5 family) because the switch commands
  call it service-internally.
- The queue retarget write moved with its function; the transition QUEUE pin
  gained `integrations/jellyfin_service.py` as a documented
  content-management exception.

- Move `_preferred_jellyfin_stream_indices`,
  `_retarget_jellyfin_queue_stream_preferences`,
  `_jellyfin_try_set_mpv_audio_track` / `_jellyfin_try_set_mpv_subtitle_track`,
  runtime selected-stream pickers, `_merge_jellyfin_playback_metadata`, and
  `_jellyfin_enrich_now_stream_metadata`.
- Slim the audio/subtitle select route handlers; route their
  `set_now_playing`/`set_session_state` writes through `playback_service`
  commands where a suitable command exists.

### M5: Progress And Stopped Hint Family

Status: complete

- `emit_progress_hint` had already moved in M4 (service-internal caller);
  this milestone moved the remaining nine functions and the
  `register_progress_provider` registration (now at service import time —
  the routes package imports the service, so ordering is unchanged).
- Assessed and deferred: consolidating `player.py`'s parallel stopped-hint
  path (`_emit_jellyfin_stopped_from_now`, `_jellyfin_should_snap_complete`,
  `_canonical_jellyfin_url_key`, `_RECENT_JELLYFIN_STOP` dedupe). The player
  copies differ subtly from the service versions (URL-key canonicalization
  lowercases media-source ids and resolves item ids through the receiver;
  the snap helper returns a bool rather than snapped ticks), so unifying
  them is a behavior change, not a move. Left as review Finding 10 drift
  with the player pinned as a transition-writer exception.

- Move `_jellyfin_emit_progress_hint`, `_jellyfin_progress_snapshot`,
  `_jellyfin_complete_ratio`/`_jellyfin_complete_remaining_sec`,
  `_jellyfin_snap_position_ticks`, `_jellyfin_played_percentage`,
  `_jellyfin_stopped_snapshot_from_now`, `_jellyfin_stopped_snapshot`,
  `_jellyfin_emit_stopped_payload`, `_jellyfin_emit_stopped_hint`.
- Assess consolidating `player.py`'s `_emit_jellyfin_stopped_from_now` onto
  the service via function-level imports (the Phase 3 player →
  playback_service pattern); keep it a documented deferral if the seam is
  not behavior-preserving.

### M6: Command Ingress Migration

Status: complete

- `handle_command(req, *, controls, ui)` owns the command ingress in the
  service. Route-facing side effects are injected seams: `controls` maps the
  playback control actions to route callables (late-binding lambdas in the
  `routes/__init__.py` wrapper, so existing monkeypatches keep
  intercepting), and `ui` provides toast/queue-event/jellyfin-event
  callbacks. The wrapper `_jellyfin_integration_command_impl` stays in
  routes as the adapter builder.
- The dedupe state (play debounce, command-id TTL, UI-action dedupe) and its
  four functions moved to the service; `smart_item_from_url` and the api-key
  URL extractor moved with the play path.
- The play path's `set_now_playing` goes through
  `playback_service.update_now_playing`; playlist enqueues stay direct QUEUE
  content writes under the pinned service exception.
- Transition pins after M6: `routes/__init__.py` is out of the QUEUE writer
  set entirely and keeps only `_status_payload` reconciliation writes for
  NOW_PLAYING/SESSION_STATE.

### M7: Service Tests, Writer Containment, And Docs

Status: complete

- `tests/test_jellyfin_service.py` added: 15 service-level tests with fake
  receiver/player adapters — command normalization and ticks conversion,
  direct/transcode selection policy (healthy-direct, AV1 guard, no-detail
  fallback, forced mode), language-preference indices, mpv track scoring,
  stopped/progress payload construction, and `handle_command` ingress with
  fake control/UI adapters.
- Pruned the 12 dead delegation shims that the migrations orphaned in
  `routes/jellyfin.py` (819 lines now, from 1,377) and tightened the
  inventory pins to the end-state contract documented in
  `docs/ARCHITECTURE_PHASE_4_JELLYFIN_INVENTORY.md`.
- Docs: inventory containment contract finalized; `AGENTS.md` Phase 4
  discipline added (Phase 3 marked complete); `docs/README.md` module
  ownership snapshot refreshed with `playback_service` and
  `jellyfin_service`.

### M8: Phase 4 Final Validation

Status: complete

- Full gates: `ruff check app tests`, `PYTHONPATH=app pytest -q`,
  CI-like fresh-venv run (FastAPI 0.139) — all green.
- Live validation on the appliance (branch image deployed via
  `RELAYTV_IMAGE_REF`, container recreated, `/health` ok). All passing:
  - Integration lifecycle: connect, register, heartbeat, `sync_health: ok`;
    the integration also recovered cleanly (reconnect + re-auth) after the
    Jellyfin server itself went down mid-validation and came back.
  - Catalog browse: home, search, movies, series listings.
  - Direct play: movie item played `direct`/`direct_ok`, metadata enriched
    in place (2 audio + 3 subtitle streams), pre-existing queue preserved.
  - Forced transcode: `jellyfin_playback_mode: transcode` via settings →
    same item selected `master.m3u8` with reason `forced_transcode_mode`
    and played (position advancing); mode restored to `auto` afterward.
  - Command ingress: resume (position advances), seek by ticks
    (3000000000 → pos 301.593), pause, stop — all dispatched through
    `handle_command` with the injected control seams.
  - Runtime track switching: audio index 2 in-place (`mpv_runtime_aid`),
    subtitle off (`-1`) then index 4 (`mpv_runtime_sid`), playback
    uninterrupted; language settings preserved (`audio: eng, sub: eng`).
  - Stopped/progress hints: `progress_snapshot` payload correct (ItemId,
    IsPaused, PositionTicks, played pct); every stop reported
    `last_stopped_ok: true` and `last_progress_ok: true`.
  - Series play-all: `POST /jellyfin/tv/series/{id}/play_all` on a
    10-episode series — episode 1 started `direct`, episodes 2–10 queued
    in exact episode order; generic queue-title fallback verified
    byte-identical to pre-phase behavior on `main`.
- Appliance returned to its pre-validation state (session closed, queue
  empty, playback mode `auto`).
- Open the final `codex/architecture-phase-4` to `main` PR.

## PR And Milestone Log

| Date | Item | Notes |
| --- | --- | --- |
| 2026-07-03 | Phase 4 started | Branch cut from `main` at `6fc7366` (Phase 3 squash merge); roadmap committed. |
| 2026-07-03 | M0–M7 landed | `cf6b521` M0 docs, `bde89dc` M1 inventory ratchet, `c703c6d` M2 helpers, `038e13a` M3 stream policy, `39bdf4f`/`c636b9d` M4 track handling, `2bd2a9e` M5 hints, `bbfd6cb` M6 command ingress, `557ce6b` M7 service tests + docs. |
| 2026-07-03 | M8 complete | Live appliance validation passed end-to-end (direct + forced-transcode play, track switching, command ingress, hints, series play-all); final PR opened. |
