# Playback Transition Writer Inventory

This document is the machine-checked containment contract for playback
transition state (see `ARCHITECTURE.md`). It answers one question: outside
`state.py`, which modules write the playback transition globals?

`state.py` owns the globals, their setters, and their persistence, and is
excluded from the scan by definition. Writers are contained behind
`playback_service` commands, and `tests/test_transition_inventory.py` pins
the writer module sets — the generated table and the pinned sets must both
change in the same commit as any migration.

Write forms counted per global:

- direct assignment through any state alias (`state.X = ...`), excluding
  comparisons;
- calls to the `state.py` setters (`set_now_playing`, `set_session_state`,
  `set_session_position`) and composite `update_session` mutations;
- `QUEUE` mutations (`clear`/`append`/`insert`/`pop`/`remove`/`extend`,
  index/slice assignment, rebinding);
- for `_TEMP_PLAYBACK_STACK`, any reference at all — the stack is private
  transition state and the end state is that no routes module touches it.

Regenerate the table after intentional changes with:

    PYTHONPATH=app python3 tests/test_transition_inventory.py --write

## Key Finding

At phase start there are 137 write sites across 7 modules. Every route module
that hosts a playback command mutates session state directly, and the
temporary playback stack lives in the routes package. Two notable
concentrations:

- `AUTO_NEXT_SUPPRESS_UNTIL` has 20 scattered writers across four modules —
  the clearest first target for a single service API (roadmap M3).
- `QUEUE` has six writer modules including `upload_store.py` (retention
  pruning) and `routes/uploads.py`, so queue containment (roadmap M6/M7) must
  account for non-playback writers too.

## Containment Contract (Phase 3 end state, M7)

`playback_service` owns the transition commands and `state.py` owns the
globals. `routes/playback.py` — the module hosting every playback HTTP
command — no longer writes any transition global. Remaining non-service
writers are deliberate, pinned exceptions
(`tests/test_transition_inventory.py::EXPECTED_TRANSITION_WRITERS`):

- `player.py`: the process adapter updating session bookkeeping inside
  commands the service itself invoked (`play_item`, monitors, mpv handoff
  mechanics under `ADVANCE_LOCK`).
- `routes/__init__.py` `_status_payload`: status-side session reconciliation
  (self-healing on read); candidate for a future service reconcile command.
  This is the module's only remaining writer — the Jellyfin command
  implementation and `routes/jellyfin.py`'s track-switch writes were
  extracted in Phase 4 (M4/M6) and now go through `playback_service`
  commands (`update_now_playing`, `mark_paused`).
- `routes/queue.py`, `routes/uploads.py`, `upload_store.py`: queue CRUD and
  upload retention — queue content management, not playback transitions.
- `integrations/jellyfin_service.py`: queue URL retargeting after Jellyfin
  language preference changes (Phase 4 M4) and command-ingress playlist
  enqueues (Phase 4 M6) — queue content management; its playback transitions
  go through `playback_service` commands.
- `_TEMP_PLAYBACK_STACK` aliases in `routes/__init__.py` and thin wrappers in
  `routes/playback.py`: kept so existing tests observe the live stack through
  the routes module.

## Review Scenario Coverage Baseline

The five Phase 3 review scenarios and where they are guarded at phase start:

- Play-now interruption: `tests/test_playback_routes.py`
  (`test_play_now_route_preserves_current_and_uses_resolved_resume`,
  `test_play_now_route_rolls_back_preserved_current_when_start_fails`) and
  `tests/test_smoke.py` interrupt-queue tests.
- Close retaining queue: `tests/test_playback_routes.py`
  (`test_close_route_preserves_queue_and_returns_closed_session`) and
  `tests/test_smoke.py` (`test_close_preserves_interrupt_queue_items`).
- Close not restarting interrupted media: `tests/test_smoke.py`
  (`test_session_tracker_does_not_reopen_closed_session`,
  `test_closed_session_does_not_prime_mpv_up_next`,
  `test_restart_current_ignores_closed_resumable_session`).
- Idle dashboard enabled/disabled transitions: `tests/test_smoke.py`
  (`test_idle_settings_sync_starts_dashboard_when_enabled`, the
  `test_natural_queue_end_*` trio,
  `test_idle_qt_shell_is_not_reused_when_idle_dashboard_disabled`).
- App restart resume behavior: `tests/test_playback_transitions.py`
  (`test_app_restart_restores_resumable_closed_session`) — added in Phase 3
  M1; previously untested.

## Writer Inventory

<!-- BEGIN GENERATED TRANSITION TABLE (tests/test_transition_inventory.py) -->
| Transition global | Writers outside `state.py` (write sites) |
| --- | --- |
| `AUTO_NEXT_SUPPRESS_UNTIL` | `playback_service.py` (2) |
| `NOW_PLAYING` | `playback_service.py` (9)<br>`player.py` (10)<br>`routes/__init__.py` (1) |
| `QUEUE` | `integrations/jellyfin_service.py` (4)<br>`playback_service.py` (8)<br>`player.py` (5)<br>`routes/queue.py` (8)<br>`routes/uploads.py` (1)<br>`upload_store.py` (1) |
| `SESSION_POSITION` | `playback_service.py` (8)<br>`player.py` (9) |
| `SESSION_STATE` | `playback_service.py` (8)<br>`player.py` (12)<br>`routes/__init__.py` (4) |
| `_TEMP_PLAYBACK_STACK` | `playback_service.py` (8)<br>`routes/__init__.py` (2)<br>`routes/playback.py` (2) |
<!-- END GENERATED TRANSITION TABLE -->
