# Phase 2 Architecture Roadmap

Date started: 2026-07-02

Branch: `codex/architecture-phase-2`

Phase 2 goal: stop using the process environment as the in-process
configuration bus. Introduce a typed runtime config service that is the single
source of truth for settings-driven behavior, keep environment mirroring only
where subprocesses require it, and preserve all public behavior. This phase
should make the Phase 3 playback-transition work easier by removing hidden
config coupling first.

Related review: `docs/ARCHITECTURE_REVIEW.md` (Finding 3, Finding 10, and the
Phase 2 roadmap section)

Branch base note: this branch was cut from `codex/architecture-phase-1` while
the final Phase 1 PR (https://github.com/mcgeezy/relaytv/pull/21) was still
open. After PR #21 merges, rebase this branch onto `main` before continuing.

## Working Rules

- Keep Phase 2 work on `codex/architecture-phase-2` until the phase is
  complete.
- Merge small focused PRs into this branch instead of directly into `main`.
- Keep public endpoint paths, response shapes, settings semantics, and UI
  behavior stable.
- Rebase or merge current `main` into this branch regularly.
- Update this file whenever a milestone starts, completes, changes scope, or
  uncovers follow-up work.
- Migrate consumers one domain at a time with guardrail tests in place before
  each move, mirroring the Phase 1 route-extraction discipline.
- Only open the final `codex/architecture-phase-2` to `main` PR after all
  Phase 2 validation gates pass.

## Scope

In scope:

- A shared typed env-parsing module that replaces the duplicated `_env_bool` /
  `_env_choice` / `_env_float` helpers (13 copies across 12 modules at phase
  start).
- A `RuntimeConfig` service with typed `SettingsSnapshot` views, constructed at
  startup from environment plus persisted settings, updated by settings writes.
- Migrating in-process readers (`player.py`, `routes/`, `jellyfin_receiver.py`,
  `state.py`, `resolver.py`, `upload_store.py`, `ytdlp_format_policy.py`,
  `video_profile.py`, `thumb_cache.py`, `discovery_mdns.py`, and others) from
  direct `os.getenv` / `os.environ` reads to config snapshots.
- Containing runtime `os.environ` writes (34 sites in `routes/settings.py`,
  15 in `main.py` at phase start) to an explicit subprocess-mirroring boundary.
- Reshaping settings-apply tests to target `RuntimeConfig` behavior instead of
  monkeypatched environment, while keeping compatibility guardrails.
- Optional: one browser-automation smoke for `/ui` (carried over from the
  Phase 1 open question).

Out of scope for Phase 2:

- Playback transition service/state machine (Phase 3).
- Jellyfin product service extraction (Phase 4).
- Optional API token auth (Phase 5).
- Frontend framework or build pipeline.
- Endpoint removals or compatibility-breaking API changes.
- Changing any `RELAYTV_*` variable names, defaults, or precedence semantics.
- Changing what child processes (mpv, yt-dlp, Qt shell, overlay) receive in
  their environment.

## Target Module Shape

- `app/relaytv_app/config.py`
  - typed env parsing helpers (`env_bool`, `env_choice`, `env_int`,
    `env_float`, `env_str`)
  - `SettingsSnapshot` (immutable typed view of settings-driven behavior)
  - `RuntimeConfig` (single source of truth: construct at startup, update on
    settings apply, expose snapshots, mirror the documented subprocess subset
    to `os.environ`)
- `docs/ARCHITECTURE_PHASE_2_ENV_INVENTORY.md`
  - the machine-checked inventory of `RELAYTV_*` reads/writes
- `tests/test_config.py`, `tests/test_env_inventory.py`
  - config service behavior and inventory guardrails

`main.py` startup and `routes/settings.py` apply paths should flow through
`RuntimeConfig`; direct `os.environ` mutation should survive only inside the
config module's subprocess-mirroring boundary and in true subprocess launch
sites.

## Milestones

### M0: Roadmap Foundation And Branch Setup

Status: in progress

Deliverables:

- Create `codex/architecture-phase-2` and add this roadmap.
- Link this roadmap from `docs/README.md` and refresh the module ownership
  snapshot if Phase 1's merge changed it.
- Add a Phase 2 discipline section to `AGENTS.md`.
- Rebase onto `main` once PR #21 merges.

Validation:

- `ruff check app tests`
- `PYTHONPATH=app pytest -q tests/test_smoke.py`
- `git diff --check`

### M1: Env Inventory And Test Baseline

Status: not started

Deliverables:

- Generate a complete inventory of `RELAYTV_*` (and other app-consumed)
  environment variables: where each is read, where it is written at runtime,
  its parse type, default, and whether subprocesses depend on it.
- Record the inventory in `docs/ARCHITECTURE_PHASE_2_ENV_INVENTORY.md` with a
  snapshot test (`tests/test_env_inventory.py`) so drift is caught, mirroring
  the Phase 1 route-inventory approach.
- Classify each variable: in-process only, subprocess-required, or both. The
  subprocess-required set defines the env-mirroring contract that must be
  preserved through the whole phase.
- Add settings-apply guardrail tests that capture today's settings-to-env sync
  behavior before anything moves.

Notes:

- This is a guardrail milestone. It should not move config code yet.

### M2: Shared Env Parsing Module

Status: not started

Deliverables:

- Add `app/relaytv_app/config.py` with typed env parsing helpers.
- Replace the duplicated per-module `_env_bool` / `_env_choice` / `_env_float`
  helpers with imports, module by module, with no behavior change (preserve
  each module's accepted truthy/falsy spellings if they differ; reconcile
  differences explicitly in the inventory doc first).
- Fix the duplicated `api_url` assignment in
  `resolver.resolve_streams_invidious()` noted in the review if still present.

Notes:

- Pure consolidation; every replacement should be covered by existing tests or
  a new focused test where behavior differences between copies exist.

### M3: RuntimeConfig And SettingsSnapshot

Status: not started

Deliverables:

- Add `RuntimeConfig`: constructed at startup from environment plus persisted
  settings (same precedence as today's `main.py` sync), updated by settings
  writes, exposing immutable typed `SettingsSnapshot` views.
- Keep full dual-write during this milestone: settings apply updates both
  `RuntimeConfig` and `os.environ` exactly as before, so consumers can migrate
  incrementally in M4.
- Add `tests/test_config.py` covering construction precedence, snapshot
  immutability, settings-apply updates, and subprocess mirroring.

Notes:

- No consumer migrates yet; this milestone only introduces the service and
  proves it tracks the legacy env bus.

### M4: Consumer Migration By Domain

Status: not started

Deliverables, ordered lowest risk first, one sub-milestone each:

- M4a: leaf policy modules — `ytdlp_format_policy.py`, `video_profile.py`,
  `thumb_cache.py`, `discovery_mdns.py`, `debug.py`.
- M4b: `resolver.py` and `upload_store.py`.
- M4c: `state.py` idle/session env reads.
- M4d: `integrations/jellyfin_receiver.py`.
- M4e: `routes/` (status payload construction, app info, remaining
  `routes/__init__.py` glue).
- M4f: `player.py` (largest reader, 104 sites at phase start) — split further
  by concern (backend selection, mpv args, resolver knobs) as needed.

Guardrails:

- Each sub-milestone lands with its domain's focused tests passing and the env
  inventory updated to reflect reads that moved behind `RuntimeConfig`.
- Import-time env reads that cache values in module globals must become
  snapshot reads or explicit provider calls, so runtime settings changes take
  effect identically to today.

Notes:

- `qt_shell_app.py` and `overlay_app.py` run as subprocesses and keep reading
  their environment; they are consumers of the mirroring contract, not
  migration targets.

### M5: Env Write Containment

Status: not started

Deliverables:

- Move the `routes/settings.py` settings-apply env writes and the `main.py`
  startup sync behind `RuntimeConfig`, mirroring only the M1-classified
  subprocess-required subset to `os.environ`.
- Remove now-dead env writes for in-process-only variables once all their M4
  readers use snapshots.
- Assert the mirroring contract in tests: the subprocess-required set is
  exactly what still lands in the environment after a settings apply.

Notes:

- This milestone completes Finding 3's recommendation; after it, the
  environment is an output boundary for children, not an in-process bus.

### M6: Settings Behavior Test Reshape

Status: not started

Deliverables:

- Rework settings-apply tests to assert `RuntimeConfig` snapshot behavior
  rather than monkeypatched environment where possible.
- Keep a small set of legacy env-contract tests for the subprocess mirror.
- Move settings-domain tests concentrated in `tests/test_smoke.py` into
  focused files where practical (review Finding 7), without weakening
  coverage.

### M7: Browser Smoke (Optional Stretch)

Status: not started

Deliverables:

- One Playwright (or equivalent) smoke path: `/ui` loads, settings modal opens
  and applies, queue shell renders, Jellyfin shell visibility.
- Wire it as an optional local/manual gate first; CI adoption is a separate
  decision.

Notes:

- Carried over from the Phase 1 open question. Skip without blocking the phase
  if tooling cost is too high; record the decision here.

### M8: Phase 2 Final Validation

Status: not started

Required before merging to `main`:

- `ruff check app tests`
- `PYTHONPATH=app pytest -q` (full suite)
- `git diff --check`
- Env inventory snapshot test green and inventory doc current.
- Live container rebuild and smoke: `/status`, `/settings`, `/ui`, `/idle`,
  static UI assets, brand assets, Jellyfin status/browse.
- Manual settings apply check in the live UI covering at least one variable in
  each class: in-process only, subprocess-required, and Jellyfin-related —
  settings changes are the primary regression risk of this phase.
- Playback smoke (play URL, enqueue, close-to-idle) to confirm player and
  resolver behavior is unchanged under snapshot-based config.
- Confirm this roadmap reflects reality.

## PR And Milestone Log

Add entries here as PRs land into `codex/architecture-phase-2`.

| Date | PR | Target | Summary | Validation | Follow-ups |
| --- | --- | --- | --- | --- | --- |
| 2026-07-02 | local | `codex/architecture-phase-2` | Created the Phase 2 branch and roadmap with env-inventory-first milestones. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_smoke.py`; `git diff --check` | Rebase onto `main` after PR #21 merges; begin M1 env inventory. |

## Open Questions

- Should `RuntimeConfig` be passed explicitly (dependency-style) into player
  and resolver call paths, or exposed through a module-level provider to keep
  Phase 2 diffs small? Default: module-level provider now, explicit injection
  where Phase 3 introduces the playback service.
- Do any `RELAYTV_*` variables have conflicting parse semantics between the
  duplicated helpers? M1 must answer this before M2 consolidates them.
- Playwright smoke: local-only gate or CI job? Decide during M7.

## Current Recommendation

Start with M1: the env inventory is cheap, catches surprises before any code
moves, and defines the subprocess-mirroring contract that every later
milestone depends on. Do not begin M4 consumer migration until M3's dual-write
`RuntimeConfig` has proven it tracks the legacy env bus under the existing
settings tests.
