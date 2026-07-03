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

Status: complete (rebase onto `main` still pending PR #21 merge)

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

Status: complete

Results:

- 246 app-config variables inventoried in
  `docs/ARCHITECTURE_PHASE_2_ENV_INVENTORY.md`, generated from source and
  guarded by `tests/test_env_inventory.py` (regenerate with
  `PYTHONPATH=app python3 tests/test_env_inventory.py --write`).
- The settings-bus/child-process overlap is exactly one variable:
  `RELAYTV_DEVICE_NAME`, a dormant legacy fallback in `qt_shell_app.py` that
  prefers persisted settings. The guardrail test pins this contract, so M5 can
  remove runtime env writes outright once M4 migrates in-process readers.
- `tests/test_settings_env_sync.py` pins today's settings-apply and startup
  env sync behavior (normalization, clamping, flag encoding, the device-name
  trio, and the Jellyfin auth force-enable).

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

Status: complete

Results:

- `app/relaytv_app/config.py` now owns `env_bool`, `env_choice`, `env_int`,
  `env_float`, and `env_str`, covered by `tests/test_config.py`.
- All 13 duplicated `_env_*` helpers were replaced with aliased imports; the
  two route-side `_env_choice` copies accepted extra "enable(d)"/"disable(d)"
  spellings, preserved via `env_choice(name, extended=True)` wrappers in
  `routes/app_info.py` and `routes/__init__.py`.
- `resolver._env_bool` was defined but never called and was deleted.
- The duplicated `api_url` assignment in `resolve_streams_invidious()` noted
  in the review had already been fixed on the Phase 1 branch; nothing to do.
- Monkeypatch compatibility preserved: `player._env_bool` remains a module
  attribute (aliased import), which `tests/test_smoke.py` patches.

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

Status: complete

Results:

- `config.py` now owns `SETTINGS_BUS_VARS` (31 variables), an immutable
  `SettingsSnapshot` with typed accessors (`raw`/`text`/`flag`/`integer`/
  `number`) that mirror the env parse helpers, and a `RuntimeConfig` service
  with a module-level `runtime_config` instance.
- Instead of duplicating sync logic, `RuntimeConfig.set_env()` owns the
  dual-write: it mutates `os.environ` exactly as the legacy writers did and
  keeps the snapshot in lockstep. All 52 runtime env write sites across
  `routes/settings.py`, `main.py`, `routes/jellyfin.py`, and `player.py` were
  converted 1:1 to `set_env` calls; the M1 guardrail tests prove env behavior
  is unchanged.
- Startup captures operator-provided settings-bus env via
  `refresh_from_env()` before the persisted-settings sync runs.
- Lockstep is asserted after a full settings apply and after startup sync in
  `tests/test_settings_env_sync.py`; snapshot immutability, dual-write, and
  typed accessor behavior are covered in `tests/test_config.py`.
- The env inventory scanner now counts `runtime_config.set_env` as a runtime
  write, so the settings-bus classification is preserved.

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

Status: complete

Results:

- M4a: `ytdlp_format_policy` and `discovery_mdns` migrated; conftest lockstep
  fixture added.
- M4b: `resolver` migrated (`USE_INVIDIOUS`, `INVIDIOUS_BASE`,
  `RELAYTV_YTDLP_COOKIES`); `upload_store` had no settings-bus reads.
- M4c: reclassified — `state.py`'s settings-bus reads all live in the
  settings-defaults path and deliberately stay direct env reads (operator env
  is the correct default source; applied settings are always persisted before
  the env sync). Documented in the inventory doc with the accepted M5 nuance.
- M4d: `jellyfin_receiver` migrated (enabled, server URL, client/device name
  fallbacks, user id, api key, username/password, auth-enabled gate).
- M4e: `routes/__init__.py` (`RELAYTV_VIDEO_MODE`,
  `RELAYTV_JELLYFIN_PLAYBACK_MODE`) and `routes/settings.py` own reads
  migrated.
- M4f: `player.py` (idle flags, video mode, sub lang, DRM connector, audio
  device, CEC flag pair via `_env_any_flag`) and `x11_overlay.py` (idle
  flags) migrated. CEC migration is required for M5 correctness: with a live
  env read, removing the settings-apply env write would let operator env
  permanently override the UI setting.
- New guardrail: `test_settings_bus_env_reads_stay_in_allowed_modules` pins
  the allowed direct-reader set (`state.py`, child processes, entrypoint,
  `config.py`).
- Tests that mutate migrated vars mid-test now call
  `runtime_config.refresh_from_env()` after the mutation (13 insertions).

Scope refinement (2026-07-02, after M3): only reads of **settings bus**
variables migrate to snapshot reads. **Static env** variables are
operator-provided, never mutated at runtime, and their live reads through the
shared `config` helpers already have correct semantics — migrating them to
startup-captured snapshots would change behavior (frozen values, broken
monkeypatch-based tests) for no benefit. This shrinks M4 substantially: most
of `player.py`'s 104 env reads are static tuning knobs and stay as-is; the
migration targets are the settings-bus read sites per the inventory table.

Deliverables, ordered lowest risk first, one sub-milestone each:

- M4a: leaf modules with settings-bus reads — `ytdlp_format_policy.py`
  (`RELAYTV_QUALITY_MODE`, `RELAYTV_QUALITY_CAP`, `YTDLP_FORMAT`) and
  `discovery_mdns.py` (`RELAYTV_DEVICE_NAME`). `video_profile.py`,
  `thumb_cache.py`, and `debug.py` read only static env and need no changes.
- M4b: `resolver.py` (`INVIDIOUS_BASE`, `USE_INVIDIOUS`,
  `RELAYTV_YTDLP_COOKIES`); `upload_store.py` reads only static env.
- M4c: `state.py` settings-bus fallback reads.
- M4d: `integrations/jellyfin_receiver.py` Jellyfin settings-bus reads.
- M4e: `routes/` (`routes/__init__.py` `RELAYTV_VIDEO_MODE` and
  `RELAYTV_JELLYFIN_PLAYBACK_MODE`; `routes/settings.py` own reads).
- M4f: `player.py` settings-bus reads (`MPV_AUDIO_DEVICE`, `RELAYTV_CEC`,
  `RELAYTV_CEC_ENABLED`, `RELAYTV_DRM_CONNECTOR`, `RELAYTV_IDLE_*`,
  `RELAYTV_SUB_LANG`, `RELAYTV_VIDEO_MODE`) and `x11_overlay.py`
  (`RELAYTV_IDLE_*`).

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

Status: complete

Results:

- `RuntimeConfig.set_env` became `set_value`: it writes the snapshot and
  mirrors to `os.environ` only for `MIRRORED_TO_ENV`
  (`RELAYTV_DEVICE_NAME`, the pinned qt_shell fallback). All 52 runtime
  writers were renamed mechanically; no writer logic changed.
- The environment is now startup input (`refresh_from_env`) and
  child-process inheritance only — Finding 3's recommendation is complete.
- `test_settings_apply_does_not_write_env_beyond_mirror_contract` asserts a
  full settings apply leaves every non-mirrored settings-bus env var
  untouched; startup-sync tests assert the same for the startup path plus
  operator-env default precedence.

### M6: Settings Behavior Test Reshape

Status: complete (core); remaining smoke-test moves deferred as optional

Results:

- `tests/test_settings_env_sync.py` became
  `tests/test_settings_config_sync.py`: settings-apply and startup-sync
  guardrails now assert snapshot values, the env containment contract, and
  operator-env default precedence.
- `tests/test_settings_routes.py` and the CEC smoke assertions now assert
  snapshot state; the device-name mirror keeps a dedicated env assertion.
- The conftest lockstep fixture plus 13 in-test refreshes keep
  env-monkeypatching tests correct; new tests should prefer
  `runtime_config.set_value` over `monkeypatch.setenv` for settings-bus
  variables.
- Moving further settings-domain tests out of `tests/test_smoke.py` into
  focused files (review Finding 7) is deferred to Phase 3+ test hygiene.

Deliverables (original):

- Rework settings-apply tests to assert `RuntimeConfig` snapshot behavior
  rather than monkeypatched environment where possible.
- Keep a small set of legacy env-contract tests for the subprocess mirror.
- Move settings-domain tests concentrated in `tests/test_smoke.py` into
  focused files where practical (review Finding 7), without weakening
  coverage.

### M7: Browser Smoke (Optional Stretch)

Status: deferred (decision recorded 2026-07-02)

Playwright is not installed on the development host and would require
downloading a browser toolchain (~300MB) for an optional stretch goal.
Deferred without blocking the phase; the Phase 1 open question about
browser-automation smoke remains open for Phase 3+, where the playback
transition service will benefit more directly from UI-level regression
coverage. Rendered browser validation stays manual for now.

### M8: Phase 2 Final Validation

Status: complete (2026-07-03)

Progress:

- Automated gates pass on 2026-07-02: `ruff check app tests`, full
  `PYTHONPATH=app pytest -q` (268 passed), `git diff --check`, env inventory
  snapshot test green with the doc current.
- Ephemeral local-server smoke passed (uvicorn on 127.0.0.1:8899 with
  temporary state/upload/thumb/snapshot dirs and workers disabled): `/health`,
  `/status`, `/settings`, `/ui`, `/idle`, `/static/ui/app.css`,
  `/static/ui/app.js`, `/assets/banner.png`, `/pwa/brand/banner.png`,
  `/runtime/capabilities` all 200.
- End-to-end settings apply validated over HTTP on the ephemeral server:
  POST `/settings` (device name, quality mode/cap, sub lang, idle QR size)
  persisted, read back correctly, and flowed through the RuntimeConfig
  snapshot into `effective_ytdlp_format` in `/status` (1080 cap applied by
  the snapshot-reading format policy).
- Smoke note: `quality_cap` accepts numeric strings only ("1080"); "1080p"
  normalizes to "" — pre-existing behavior, verified unchanged from before
  Phase 2.
- Live validation completed 2026-07-03 on the running appliance: built
  `ghcr.io/mcgeezy/relaytv:codex-architecture-phase-2` from the branch,
  pointed `.env` `RELAYTV_IMAGE_REF` at it, and force-recreated the `relaytv`
  container. Startup clean (no errors; only the pre-existing host zeroconf
  multicast warning), Qt shell reattached, and `/status` returned the same
  state and `effective_ytdlp_format` as the pre-swap baseline.
- Live endpoint smoke: `/status`, `/settings`, `/ui`, `/idle`,
  `/static/ui/app.css`, `/static/ui/app.js`, `/assets/banner.png`,
  `/pwa/brand/banner.png`, `/runtime/capabilities` all 200;
  `/integrations/jellyfin/status` shows enabled/running/connected with a
  fresh heartbeat and successful registration; `/jellyfin/home` (5 rows) and
  `/jellyfin/movies` return real catalog items through the snapshot-migrated
  auth/config reads.
- Live settings apply, one variable per class, each applied via
  POST `/settings` (the same endpoint the settings UI posts to) and reverted:
  in-process — quality mode/cap `auto`/`720` changed
  `effective_ytdlp_format` to `height<=720` and reverted cleanly;
  subprocess-mirrored — `device_name` applied and read back (mDNS
  re-advertises only at startup by pre-existing design; the fresh container
  start advertised the persisted name through the migrated `_device_name()`
  path); Jellyfin — receiver healthy under snapshot reads (validated via
  status/browse above rather than toggling the live connection).
- Live playback smoke: `POST /play` of a YouTube URL resolved and played
  (position advancing), `POST /enqueue` accepted a second item which
  auto-advanced when the first ended, and `POST /close` returned the
  appliance to idle with `now_playing` cleared.

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
| 2026-07-02 | local | `codex/architecture-phase-2` | Completed M1: generated the machine-checked env inventory (246 variables), pinned the settings-bus/child-process contract (`RELAYTV_DEVICE_NAME` only, dormant fallback), and added settings-apply/startup env sync guardrail tests. | `ruff check app tests`; `PYTHONPATH=app pytest -q` (232 passed); `git diff --check` | Begin M2 shared env parsing module. |
| 2026-07-02 | local | `codex/architecture-phase-2` | Completed M2: added `config.py` typed env helpers, replaced all 13 duplicated `_env_*` copies with aliased imports (extended `_env_choice` spellings preserved via wrappers), and deleted the dead `resolver._env_bool`. | `ruff check app tests`; `PYTHONPATH=app pytest -q` (257 passed); `git diff --check`; `py_compile` on child-process modules | Begin M3 RuntimeConfig and SettingsSnapshot. |
| 2026-07-02 | local | `codex/architecture-phase-2` | Completed M3: added `RuntimeConfig`/`SettingsSnapshot` with `set_env` owning the env+snapshot dual-write, converted all 52 runtime env write sites 1:1, added startup `refresh_from_env`, and taught the inventory scanner about `set_env` writes. | `ruff check app tests`; `PYTHONPATH=app pytest -q` (262 passed); `git diff --check` | Begin M4 consumer migration (M4a leaf modules first). |
| 2026-07-02 | local | `codex/architecture-phase-2` | Completed M4a: refined M4 scope to settings-bus reads only, migrated `ytdlp_format_policy` (quality mode/cap, `YTDLP_FORMAT`) and `discovery_mdns` (device name) to snapshot reads, and added the conftest lockstep fixture that re-syncs the global RuntimeConfig from env around each test. | `ruff check app tests`; `PYTHONPATH=app pytest -q` (265 passed); `git diff --check` | Continue M4b resolver migration. |
| 2026-07-02 | local | `codex/architecture-phase-2` | Completed M4b-M4f: migrated resolver, jellyfin_receiver, routes, player, and x11_overlay settings-bus reads to snapshots; reclassified `state.py` defaults-path reads as intentionally direct; added the allowed-reader guardrail test and refreshed affected tests for lockstep. | `ruff check app tests`; `PYTHONPATH=app pytest -q` (266 passed); `git diff --check` | Begin M5 env write containment. |
| 2026-07-02 | local | `codex/architecture-phase-2` | Completed M5 and core M6: `set_value` contains env writes to the pinned `RELAYTV_DEVICE_NAME` mirror, and the settings sync/routes/CEC tests now assert snapshot state plus the env containment contract and operator-default precedence. | `ruff check app tests`; `PYTHONPATH=app pytest -q` (268 passed); `git diff --check` | Decide M7 browser smoke; then M8 final validation. |
| 2026-07-02 | local | `codex/architecture-phase-2` | Deferred M7 browser smoke (tooling cost, revisit in Phase 3+) and started M8: automated gates plus an ephemeral local-server HTTP smoke passed, including an end-to-end settings apply that flowed through the RuntimeConfig snapshot into `effective_ytdlp_format`. | `ruff check app tests`; `PYTHONPATH=app pytest -q` (268 passed); `git diff --check`; ephemeral `GET /health`, `/status`, `/settings`, `/ui`, `/idle`, static UI assets, brand assets, `/runtime/capabilities`; ephemeral `POST /settings` applies | Live container rebuild plus manual settings/playback smoke with the user, then the final Phase 2 to `main` PR (after PR #21 merges and a rebase onto `main`). |
| 2026-07-03 | local | `codex/architecture-phase-2` | Completed M8 live validation: rebuilt and force-recreated the live container on the phase-2 image, smoked all UI/asset/status endpoints, applied and reverted one settings variable per class over the live API (quality cap flowed through the snapshot into `effective_ytdlp_format`; device name applied and read back; Jellyfin receiver connected/registered and catalog browse returned items), and ran the playback smoke (play, enqueue with auto-advance, close-to-idle). | `ruff check app tests`; `PYTHONPATH=app pytest -q` (268 passed); `git diff --check`; live smoke per the M8 section | Merge PR #21, rebase this branch onto `main`, open the final Phase 2 PR. |

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
