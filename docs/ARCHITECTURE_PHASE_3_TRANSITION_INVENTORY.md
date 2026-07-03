# Phase 3 Playback Transition Writer Inventory

This document is the measured baseline for the Phase 3 playback transition
service work (`docs/ARCHITECTURE_PHASE_3_ROADMAP.md`). It answers one
question: outside `state.py`, which modules write the playback transition
globals today?

`state.py` owns the globals, their setters, and their persistence, and is
excluded from the scan by definition. Everything listed below is a writer the
phase migrates behind `playback_service` commands, and
`tests/test_transition_inventory.py` pins the writer module sets so each
milestone tightens the contract explicitly — the generated table and the
pinned sets must both change in the same commit as the migration itself.

Write forms counted per global:

- direct assignment through any state alias (`state.X = ...`), excluding
  comparisons;
- calls to the `state.py` setters (`set_now_playing`, `set_session_state`,
  `set_session_position`);
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
| `NOW_PLAYING` | `playback_service.py` (1)<br>`player.py` (9)<br>`routes/__init__.py` (3)<br>`routes/jellyfin.py` (4)<br>`routes/playback.py` (10) |
| `QUEUE` | `playback_service.py` (2)<br>`player.py` (5)<br>`routes/__init__.py` (4)<br>`routes/playback.py` (3)<br>`routes/queue.py` (5)<br>`routes/uploads.py` (1)<br>`upload_store.py` (1) |
| `SESSION_POSITION` | `player.py` (8)<br>`routes/playback.py` (9) |
| `SESSION_STATE` | `playback_service.py` (2)<br>`player.py` (11)<br>`routes/__init__.py` (5)<br>`routes/jellyfin.py` (2)<br>`routes/playback.py` (13) |
| `_TEMP_PLAYBACK_STACK` | `playback_service.py` (8)<br>`routes/__init__.py` (2)<br>`routes/playback.py` (2) |
<!-- END GENERATED TRANSITION TABLE -->
