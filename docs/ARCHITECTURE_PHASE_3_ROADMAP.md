# Phase 3 Architecture Roadmap

Status: **complete** — squash-merged to `main` as PR #23 on 2026-07-03.
This document is the historical milestone log for the phase.

Date started: 2026-07-03

Branch: `codex/architecture-phase-3`

Phase 3 goal: make close/play-now/queue/resume behavior deterministic by
introducing a playback transition service with explicit commands. The service
becomes the single writer for `NOW_PLAYING`, session state, queue advancement,
auto-next suppression, and close/resume semantics. `player.py` becomes the
process/control adapter (mpv lifecycle, Qt shell, CEC), not the owner of
product policy, and the temporary playback stack moves out of the routes
package.

Related review: `docs/ARCHITECTURE_REVIEW.md` (Finding 4 and the Phase 3
roadmap section)

Branch base note: this branch was cut from `codex/architecture-phase-2` while
the final Phase 2 PR (https://github.com/mcgeezy/relaytv/pull/22) was still
open. PR #22 merged on 2026-07-03 and this branch was rebased onto `main`
(dropping the now-squashed Phase 2 commits) the same day, with exact content
parity.

## Working Rules

- Keep Phase 3 work on `codex/architecture-phase-3` until the phase is
  complete.
- Merge small focused PRs into this branch instead of directly into `main`.
- Keep public endpoint paths, response shapes, playback semantics, and UI
  behavior stable.
- Update this file whenever a milestone starts, completes, changes scope, or
  uncovers follow-up work.
- Migrate one command path at a time with guardrail tests in place before each
  move, mirroring the Phase 1/2 discipline.
- Facade first, policy later: every command starts as a 1:1 delegation to the
  existing code path so route behavior is provably unchanged, then policy
  moves behind the facade in later milestones.
- Only open the final `codex/architecture-phase-3` to `main` PR after all
  Phase 3 validation gates pass.
- No release until the architecture roadmap completes (user decision
  2026-07-03); phases ship as `refactor:` PRs that do not trigger
  release-please.

## Scope

In scope:

- A `playback_service` module owning the explicit transition commands from the
  review: `play_now`, `queue_item`, `close_current`, `advance_queue`,
  `resume_session`, `natural_end`, `stop_all` (names may map onto existing
  richer variants; the review names are the contract vocabulary).
- Centralizing the ~20 scattered `state.AUTO_NEXT_SUPPRESS_UNTIL` writes
  (routes/playback.py, routes/jellyfin.py, routes/__init__.py, player.py)
  behind one service API.
- Moving the temporary playback stack (`_TEMP_PLAYBACK_STACK` /
  `_TEMP_PLAYBACK_LOCK` plus capture/complete/watchdog helpers) out of
  `routes/__init__.py` into the service.
- Centralizing close/resume semantics (`_can_preserve_closed_session`,
  `_resume_paused_current_session_in_place`,
  `_discard_interrupted_playback_state`, play-now preserve/rollback) in the
  service.
- A machine-checked playback-state writer inventory (mirroring the Phase 2 env
  inventory pattern) that pins who may write each playback global, tightening
  as milestones land.
- Focused transition tests for the five review scenarios: play-now
  interruption, close retaining queue, close not restarting interrupted
  media, idle dashboard enabled/disabled transitions, and app restart resume
  behavior.

Out of scope for Phase 3:

- Jellyfin product service extraction (Phase 4) — Jellyfin route handlers stay
  where they are; only their auto-next suppression writes migrate.
- Optional API token auth (Phase 5).
- Frontend framework or build pipeline changes.
- Endpoint removals or compatibility-breaking API changes.
- Rewriting mpv process management, the Qt shell supervisor, or CEC handling
  (`player.py` keeps process control; only product policy moves).
- Changing persisted session/queue/history file formats.
- Behavior changes to playback semantics — if a behavior bug is discovered,
  document it here unless the user asks for an immediate fix.

## Baseline (measured at phase start)

- Playback transition state and its owners today:
  - `state.py`: `QUEUE`/`QUEUE_LOCK`, `NOW_PLAYING`, `SESSION_STATE`,
    `SESSION_POSITION`, `AUTO_NEXT_SUPPRESS_UNTIL`, `ADVANCE_LOCK`, setters
    (`set_now_playing`, `set_session_state`, `set_session_position`) and
    persistence.
  - `player.py` (5584 lines): `play_item`, `advance_queue_playback`,
    `stop_mpv`, `stop_playback_keep_qt_shell`, auto-next suppression checks
    and transition flags, `restart_current`, natural-end handling in the mpv
    monitor, 4 direct `AUTO_NEXT_SUPPRESS_UNTIL` writes.
  - `routes/__init__.py` (6472 lines): `_TEMP_PLAYBACK_STACK`/`_LOCK`,
    `_capture_current_playback_state`, `_complete_temporary_playback`,
    `_temporary_watchdog`, `_discard_interrupted_playback_state`,
    `_can_preserve_closed_session`,
    `_resume_paused_current_session_in_place`, 2 suppression writes.
  - `routes/playback.py` (1037 lines): HTTP command endpoints with play-now
    preserve/rollback helpers and 12 suppression writes.
  - `routes/jellyfin.py`: 2 suppression writes.
- Existing guardrails: 19 route-level tests in `tests/test_playback_routes.py`
  covering play/play-now/close/stop/resume/temporary paths, plus queue/history
  route tests and playback tests inside `tests/test_smoke.py`.

## Milestones

### M0: Roadmap Foundation And Branch Setup

Status: complete (rebased onto `main` 2026-07-03 after PR #22's squash merge
via `git rebase --onto origin/main <phase-2 head>`; exact content parity
verified)

Deliverables:

- Create `codex/architecture-phase-3` and add this roadmap.
- Link this roadmap from `docs/README.md`.
- Add a Phase 3 discipline section to `AGENTS.md` and mark the Phase 2 section
  complete.
- Rebase onto `main` once PR #22 merges.

Validation:

- `ruff check app tests`
- `PYTHONPATH=app pytest -q`
- `git diff --check`

### M1: Transition Inventory And Guardrail Baseline

Status: complete (2026-07-03)

Findings:

- Measured baseline: 137 write sites across 7 modules.
  `AUTO_NEXT_SUPPRESS_UNTIL` has 20 writers in four modules; `QUEUE` has six
  writer modules including `upload_store.py` retention pruning and
  `routes/uploads.py`, so queue containment must account for non-playback
  writers.
- `routes/jellyfin.py` writes `NOW_PLAYING`/`SESSION_STATE` directly (not just
  suppression), so the M2 facade must cover the Jellyfin command paths too.
- Scenario coverage: four of the five review scenarios were already guarded by
  Phase 1 route tests and `test_smoke.py`; app-restart resume was untested and
  is now guarded by `tests/test_playback_transitions.py`
  (`test_app_restart_restores_resumable_closed_session`). The coverage map
  lives in the inventory doc.

Deliverables:

- `docs/ARCHITECTURE_PHASE_3_TRANSITION_INVENTORY.md`: machine-generated
  inventory of every module writing the playback transition globals
  (`NOW_PLAYING`, `SESSION_STATE`, `SESSION_POSITION`,
  `AUTO_NEXT_SUPPRESS_UNTIL`, `QUEUE` mutation, temp playback stack), guarded
  by `tests/test_transition_inventory.py` with a `--write` regeneration mode
  (same pattern as the Phase 2 env inventory).
- Behavior guardrail tests for the five review scenarios where not already
  covered: play-now interruption, close retaining queue, close not restarting
  interrupted media, idle dashboard enabled/disabled transitions, app restart
  resume behavior.

Validation: gates; inventory doc matches a fresh scan; new guardrails pass on
the unmodified code.

### M2: Playback Service Facade

Status: complete (2026-07-03)

Notes:

- Facade commands landed: `play_now`, `queue_item`, `advance_queue`,
  `stop_all`, `stop_keep_shell`, plus the M3-ready suppression API
  (`suppress_auto_next` / `clear_auto_next_suppression`, no callers yet).
- 17 route call sites migrated (9 `play_item` in playback.py, 2 in
  jellyfin.py, 2 in routes/__init__.py, 1 threadpool call in uploads.py, 2
  advance sites, plus the enqueue and smart queue-append cores through
  `queue_item`). No test changed: the facade delegates through player module
  attributes so existing monkeypatches keep intercepting.
- Optional-parameter contract: `play_now` forwards `start_pos` and `stop_all`
  forwards `restart_splash` only when specified, because existing test
  doubles for `player.play_item`/`player.stop_mpv` don't all accept them and
  omission is behavior-identical to the callee defaults.
- Deliberately left for later milestones: suppression writes at call sites
  (M3), `player.MPV_LOCK` usage around stop/load in routes (M5),
  `except player.QueueAdvanceEmptyError` type references (M6).

Deliverables:

- `app/relaytv_app/playback_service.py` exposing the review command
  vocabulary, each command delegating 1:1 to the existing implementation
  (player/state/routes helpers) with no behavior change.
- Route handlers in `routes/playback.py`, `routes/queue.py`,
  `routes/jellyfin.py`, and `routes/__init__.py` call the service instead of
  reaching into `player`/`state` for transition commands.
- Existing route guardrails prove responses unchanged.

Validation: gates; `tests/test_playback_routes.py` unchanged and green.

### M3: Auto-Next Suppression Ownership

Status: complete (2026-07-03)

Notes:

- All 20 scattered writes migrated: plain guards (2s), close/clear holds
  (24h), seek-transition holds and player handoff guards (extend-only max
  semantics) map onto `suppress_auto_next(seconds, extend_only=...)`;
  behavior byte-identical.
- `player.py` reaches the service through function-level imports to avoid a
  module-level import cycle (service imports player); the M6 policy move
  retires those call sites anyway.
- Inventory ratchet tightened: `AUTO_NEXT_SUPPRESS_UNTIL` writer set is now
  exactly `{playback_service.py}`.

Deliverables:

- One service API for suppression (e.g. `suppress_auto_next(seconds)` /
  `auto_next_suppressed()`); all ~20 scattered
  `state.AUTO_NEXT_SUPPRESS_UNTIL` writes migrate to it.
- Inventory test tightens: only `playback_service` (and `state.py` itself)
  may touch the suppression global.

Validation: gates; inventory containment test green.

### M4: Temporary Playback Stack Relocation

Status: complete (2026-07-03)

Notes:

- The stack, its lock, and all six helpers (discard, discard-interrupted,
  capture, restore, complete, watchdog) moved verbatim into
  `playback_service`; `routes/__init__.py` keeps one alias block pointing at
  the same objects so existing callers and tests that reference
  `routes._TEMP_PLAYBACK_STACK` keep observing the live stack.
- Two test monkeypatches of `routes._restore_playback_state` were repointed
  to `playback_service.restore_playback_state` — with the implementation in
  the service, patching the routes alias no longer intercepts the restore
  call (module-global dispatch happens inside the service now).
- Moving `restore_playback_state` makes the service a legitimate writer of
  `NOW_PLAYING`/`SESSION_STATE`; pins updated accordingly.

Deliverables:

- `_TEMP_PLAYBACK_STACK`, its lock, capture/complete/watchdog helpers move
  from `routes/__init__.py` into the service; routes keep thin wrappers only
  as long as tests monkeypatch them.
- Inventory test tightens for the stack.

Validation: gates; temporary-playback guardrails green.

### M5: Close And Resume Semantics

Status: complete (2026-07-03)

Notes:

- Service now owns the transition cores as real implementations:
  `can_preserve_closed_session`, `preserve_current_to_queue_front`,
  `rollback_play_now_preserve`, `resume_paused_in_place`, `close_current`,
  and `resume_session`.
- Altitude split established: HTTP guards, `_control_ack_payload` response
  shaping, UI queue events, notification-surface calls, and the Jellyfin
  stopped hint stay with the route handlers; `close_current` takes the
  visual-surface decisions (`idle_surface_enabled`, `keep_shell_allowed`) as
  parameters so surface policy stays out of the service.
- Ratchet: `routes/playback.py` no longer mutates `QUEUE` at all; the service
  is now a writer of `SESSION_POSITION`. No behavior-test edits were needed.

Deliverables:

- `close_current`, `resume_session`, preserve/rollback for play-now,
  `_can_preserve_closed_session`, `_discard_interrupted_playback_state`, and
  resume-in-place move behind the service as real implementations (not
  delegations).
- The service becomes the only writer of `NOW_PLAYING`/session state outside
  `state.py` setters for the close/resume paths.

Validation: gates; close/resume guardrails green; inventory tightens.

### M6: Queue Advancement And Natural End

Status: complete (2026-07-03), with a deliberate scope line

Notes:

- `natural_end()` landed as a service command: the player's autoplay monitor
  keeps detection (idle/ended confirmation, suppression window, transition
  guards) and calls the service for the decision — advance the queue,
  fall to idle, or hold during handoff gaps. The advance failure backoff and
  auto-next transition flagging move with it.
- The queue-advance outcome vocabulary (`QueueAdvanceEmptyError`,
  `QueueAdvanceSuppressedError`) is re-exported by the service and routes
  catch it there; product code now reaches queue advancement only through
  `advance_queue`/`natural_end`.
- Deliberate scope line: `advance_queue_playback`'s internals (playlist
  handoff attempts, dequeue with rollback, skip-unplayable loop) stay in
  `player.py` as the mpv handoff mechanics engine. They interleave mpv IPC
  state with queue pops under `ADVANCE_LOCK`; relocating them wholesale
  moves mechanics, not policy, and risks drift for no ownership gain — the
  service is the only caller, which is the boundary the review asked for.

Deliverables:

- Queue advancement policy (`advance_queue_playback` product decisions:
  what advances, skip-unplayable, playlist handoff preference) moves behind
  the service; `player.py` keeps the mpv handoff mechanics and reports
  natural end to the service.
- Highest-risk milestone; may split into facade + policy-move sub-PRs. The
  advance path is lock-sensitive (`state.ADVANCE_LOCK`) — preserve lock
  ordering exactly.

Validation: gates; queue/auto-advance guardrails green; live playback smoke
before merging this milestone into the branch is encouraged.

### M7: Writer Containment And Test Reshape

Status: complete (2026-07-03)

Notes:

- Final route migrations: `stop_current` and `clear_session` transition cores
  moved into the service; pause/unpause/toggle session marks go through
  `mark_paused`; resume fallbacks through `mark_resumed_now_playing`.
- End state reached: `routes/playback.py` writes zero transition globals. The
  remaining non-service writers are pinned as documented exceptions (player
  process adapter, Jellyfin paths for Phase 4, status reconciliation, queue
  CRUD/retention, temp-stack compat aliases) — rationale in the inventory
  doc's Containment Contract section and in the pinned test.
- Test reshape assessment: route-level tests already exercise every service
  command through the public HTTP contract (they were written that way in
  Phase 1); dedicated service-level unit tests would duplicate them, so the
  reshape is limited to the M4 monkeypatch repoints. Revisit only if service
  commands gain callers outside the routes.

Deliverables:

- Inventory test reaches its end state: `playback_service` and `state.py` are
  the only writers of playback transition globals; route/player modules are
  pinned to an explicit (ideally empty) exception list.
- Transition tests target service commands directly where route-level tests
  duplicated them; route tests remain as the public-contract layer.

Validation: gates; full suite green.

### M8: Phase 3 Final Validation

Status: complete (2026-07-03)

Live validation on the running appliance (image
`ghcr.io/mcgeezy/relaytv:codex-architecture-phase-3`, built from the branch
and force-recreated; `.env` `RELAYTV_IMAGE_REF` updated; startup clean, only
the pre-existing Chromium dbus warning):

- Play-now interruption: playing item was preserved to the queue front with
  `_relaytv_interrupt_preserved` and its resume position (11.2s) while the
  interrupting item took over — through `preserve_current_to_queue_front`.
- Close retaining queue: `close_current` returned closed/resumable with
  position 9.5s, kept the Qt shell (`kept_player_shell: true`), queue intact.
- Close not restarting interrupted media: session held closed for 8s with the
  queued interrupted item untouched (auto-next hold effective).
- App restart resume: container restart restored the persisted closed session
  (state closed, resume available, queue intact); `/resume_session` resumed
  at the persisted position (13.3s observed ≈ 9.5s + elapsed), not from zero.
- Natural end (bonus, exercised organically): when the resumed item ended,
  `natural_end` auto-advanced to the interrupted queue item and resumed it
  from its preserved position (28s observed ≈ 11.2s + elapsed).
- Idle dashboard enabled/disabled transitions: setting toggled on and off via
  the live API, reflected in `/status` both ways.
- Cleanup: `stop_current` returned a resumable stop, `/resume/clear`
  (`clear_session`) returned the appliance to a clean idle matching the
  pre-smoke baseline.

Required before merging to `main`:

- `ruff check app tests`
- `PYTHONPATH=app pytest -q` (full suite)
- `git diff --check`
- Transition inventory test green and inventory doc current.
- Live container rebuild and smoke on the appliance.
- Live transition smoke covering the five review scenarios: play-now
  interruption, close retaining queue, close not restarting interrupted
  media, idle dashboard enabled/disabled transitions, app restart resume.
- Confirm this roadmap reflects reality.

## PR And Milestone Log

Add entries here as PRs land into `codex/architecture-phase-3`.

| Date | PR | Base | Summary | Validation | Next step |
| --- | --- | --- | --- | --- | --- |
| 2026-07-03 | local | `codex/architecture-phase-3` | Completed M1: machine-checked transition writer inventory (137 write sites, 7 modules) with pinned per-global writer sets, plus the previously missing app-restart resume guardrail. | `ruff check app tests`; `PYTHONPATH=app pytest -q` (271 passed); `git diff --check` | M2 playback service facade. |
| 2026-07-03 | local | `codex/architecture-phase-3` | Completed M2: `playback_service` facade with the review command vocabulary; 17 route transition call sites migrated with zero test changes. | `ruff check app tests`; `PYTHONPATH=app pytest -q` (271 passed, no test edits); `git diff --check` | M3 auto-next suppression ownership. |
| 2026-07-03 | local | `codex/architecture-phase-3` | Completed M3: all 20 auto-next suppression writes migrated to the service API; writer set for `AUTO_NEXT_SUPPRESS_UNTIL` tightened to `{playback_service.py}`. | `ruff check app tests`; `PYTHONPATH=app pytest -q` (271 passed, no test edits); `git diff --check` | M4 temporary playback stack relocation. |
| 2026-07-03 | local | `codex/architecture-phase-3` | Completed M4: temporary playback stack and helpers moved into `playback_service` with routes-side compat aliases; two restore monkeypatches repointed to the service. | `ruff check app tests`; `PYTHONPATH=app pytest -q` (271 passed); `git diff --check` | M5 close and resume semantics. |
| 2026-07-03 | local | `codex/architecture-phase-3` | Completed M5: close/resume transition cores moved into the service (close_current, resume_session, resume-in-place, play-now preserve/rollback, can-preserve predicate); routes keep guards, response shaping, and UI/Jellyfin side effects. | `ruff check app tests`; `PYTHONPATH=app pytest -q` (271 passed, no behavior-test edits); `git diff --check` | M6 queue advancement and natural end. |
| 2026-07-03 | local | `codex/architecture-phase-3` | Completed M6: `natural_end` service command owns the end-of-playback decision; the autoplay monitor keeps detection only; advance outcome exceptions re-exported via the service. Advance mechanics stay in player.py by design (see M6 notes). | `ruff check app tests`; `PYTHONPATH=app pytest -q` (271 passed, no test edits); `git diff --check` | M7 writer containment and test reshape. |
| 2026-07-03 | local | `codex/architecture-phase-3` | Completed M7: stop/clear-session cores and pause/resume marks moved into the service; `routes/playback.py` now writes zero transition globals; remaining writers pinned as documented exceptions. | `ruff check app tests`; `PYTHONPATH=app pytest -q` (271 passed, no test edits); `git diff --check` | M8 final validation (live smoke with the user). |
| 2026-07-03 | local | `codex/architecture-phase-3` | Completed M8: live container rebuilt on the phase-3 image and all five review scenarios validated on the appliance (play-now interruption, close retaining queue, closed-session hold, app-restart resume at position, idle dashboard toggles), plus organic natural-end auto-advance of the interrupted item. | `ruff check app tests`; `PYTHONPATH=app pytest -q` (271 passed); `git diff --check`; live smoke per the M8 section | Merge PR #22 (Phase 2), rebase this branch onto `main`, open the final Phase 3 PR. |
| 2026-07-03 | [#23](https://github.com/mcgeezy/relaytv/pull/23) | `main` | PR #22 merged; rebased this branch onto `main` past the squash merge (exact content parity) and opened the final Phase 3 PR. | `ruff check app tests`; `PYTHONPATH=app pytest -q` (271 passed, post-rebase); `git diff --check` | Merge PR #23 to complete Phase 3. |
