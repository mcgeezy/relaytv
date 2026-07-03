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

Status: planned

- Move `_jellyfin_integration_command_impl`, duplicate-command and duplicate
  play/UI-action suppression, and command-state reset into the service.
- Playback transitions inside the command impl go through existing
  `playback_service` commands; remaining direct writes become pinned
  exceptions with rationale.
- Tighten the Phase 3 transition-inventory pins: drop `routes/jellyfin.py`
  from all writer sets; `routes/__init__.py` keeps only the
  `_status_payload` reconciliation writes.

### M7: Service Tests, Writer Containment, And Docs

Status: planned

- `tests/test_jellyfin_service.py`: service-level tests with fake
  receiver/player adapters covering command normalization, stream selection
  policy (direct vs transcode), track preference resolution, and
  stopped/progress payload creation.
- Final Jellyfin surface inventory pin tightening to the end-state contract.
- Docs: `AGENTS.md` phase discipline update, `docs/README.md` module
  ownership note, API/ops doc touch-ups where behavior location moved.

### M8: Phase 4 Final Validation

Status: planned

- Full gates: `ruff check app tests`, `PYTHONPATH=app pytest -q`,
  CI-like fresh-venv run.
- Live validation on the appliance: build the branch image, deploy, and
  smoke Jellyfin end-to-end — integration status/heartbeat, catalog browse,
  item play (direct and forced-transcode), audio/subtitle runtime switch,
  pause/resume/seek/stop command ingress, stopped/progress hint emission,
  series play-all queueing.
- Open the final `codex/architecture-phase-4` to `main` PR.

## PR And Milestone Log

| Date | Item | Notes |
| --- | --- | --- |
| 2026-07-03 | Phase 4 started | Branch cut from `main` at `6fc7366` (Phase 3 squash merge); roadmap committed. |
