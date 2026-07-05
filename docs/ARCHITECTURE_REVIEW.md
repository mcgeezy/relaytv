# RelayTV Architecture Review

Date: 2026-06-30

Status, 2026-07-04: **the recommended roadmap below is complete.** All six
phases merged to `main` as PRs
[#21](https://github.com/mcgeezy/relaytv/pull/21)–[#26](https://github.com/mcgeezy/relaytv/pull/26)
(2026-07-03/04). This document is now the historical findings record; the
per-phase milestone logs live in the `ARCHITECTURE_PHASE_*_ROADMAP.md` docs,
and the machine-checked inventories (routes, env, transitions, Jellyfin,
runtime matrix) remain active guardrails enforced by the test suite.

Finding outcomes:

| Finding | Addressed by | Status |
| --- | --- | --- |
| 1. `routes.py` owns too many boundaries | Phase 1 (PR #21) | Done — domain route modules; `routes/__init__.py` still holds shared helpers/glue (follow-up below) |
| 2. UI embedded as a Python string | Phase 1 (PR #21) | Done — CSS/JS extracted to `static/ui/`; browser smoke test still open |
| 3. `os.environ` as config bus | Phase 2 (PR #22) | Done — `config.py` runtime config service + machine-checked env inventory |
| 4. Playback state spread across modules | Phase 3 (PR #23) | Done — `playback_service.py` owns transition commands; writer inventory enforced |
| 5. Partial Jellyfin boundary | Phase 4 (PR #24) | Done — `integrations/jellyfin_service.py`; receiver stays transport-only |
| 6. State persistence schema ownership | — | **Open** — no versioned models yet |
| 7. Tests too concentrated | Phases 1–6 | Largely done — focused route/service/inventory test files; `test_smoke.py` still large |
| 8. API trust boundary | Phase 5 (PR #25) | Done — optional `RELAYTV_API_TOKEN` bearer auth; trusted-LAN docs |
| 9. Runtime/installer policy tracing | Phase 6 (PR #26) | Done — machine-checked runtime matrix + operator checklists |
| 10. Duplication and drift | Phase 2 (partial) | Partial — env parsing centralized; provider/URL helper consolidation still open |

Scope reviewed:

- FastAPI app setup, routes, settings, status, playback, queue, upload, overlay, Jellyfin, and PWA endpoints
- Runtime modules under `app/relaytv_app`
- Docker, release, CI, install, and operations scripts
- Existing tests and public documentation

This review is intentionally architecture-focused. It does not propose a rewrite.
RelayTV is already useful and has strong local-first product direction; the main
risk is that several feature families now share the same large modules and
runtime globals, making small behavior changes expensive to reason about.

## Executive Summary

RelayTV has grown into a capable single-process appliance: FastAPI serves API
routes, UI HTML, status streams, upload ingest, native Qt playback coordination,
Jellyfin browsing/control, and installer/runtime diagnostics. The product
surface is coherent, but the code boundaries are no longer carrying their
weight.

The highest-value next step is to introduce clearer ownership boundaries while
preserving behavior:

1. Split `routes.py` by domain.
2. Move inline UI HTML/CSS/JS out of Python strings.
3. Replace runtime `os.environ` mutation with a runtime config object.
4. Centralize playback and queue transitions behind a small service/state
   machine.
5. Expand tests from smoke/string assertions into behavior-focused service and
   API contract tests.

## Current Shape

Original measured hotspots:

- `app/relaytv_app/routes.py`: 16,619 lines, 282 functions.
- `routes.py::ui`: 7,393 lines of server-rendered HTML/CSS/JS.
- `app/relaytv_app/player.py`: 5,169 lines, 223 functions.
- `app/relaytv_app/qt_shell_app.py`: 3,082 lines, with a 1,823-line `main()`.
- `app/relaytv_app/integrations/jellyfin_receiver.py`: 2,619 lines.
- `tests/test_smoke.py`: 2,960 lines.

Shape after the roadmap completed (2026-07-04):

- `app/relaytv_app/routes/` is a package with domain modules for app info,
  assets, capabilities, devices, health, Jellyfin, playback, queue, settings,
  snapshots, status, UI, and uploads.
- `app/relaytv_app/routes/__init__.py` remains the aggregate compatibility
  module and still owns shared helpers plus cross-domain glue (~3,900 lines).
- Main UI CSS and JavaScript live in `app/relaytv_app/static/ui/app.css` and
  `app/relaytv_app/static/ui/app.js`.
- `app/relaytv_app/config.py` is the runtime config service (typed env
  parsing, settings bus, subprocess mirroring boundary).
- `app/relaytv_app/playback_service.py` owns playback transition commands;
  `player.py` is the process/control adapter.
- `app/relaytv_app/integrations/jellyfin_service.py` owns Jellyfin product
  behavior; `jellyfin_receiver.py` stays transport/session/catalog.
- Focused test files cover capabilities, Jellyfin routes/service, playback
  routes/transitions, queue/history, settings, uploads, API auth, the runtime
  matrix, and the machine-checked route/env/transition/Jellyfin inventories.
- `tests/test_smoke.py` is still large (~4,200 lines) and remains the
  catch-all for player/resolver behavior tests.

Positive foundations:

- `ruff check app tests` currently passes.
- Release and image workflows are structured and traceable.
- State persistence uses atomic writes in several places.
- Upload paths and thumbnails have useful validation and cleanup behavior.
- Status/runtime endpoints expose a lot of operational detail.
- The product is consistently local-first and automation-friendly.
- Phase 1 has already reduced route registration and UI asset coupling without
  changing public endpoint paths.

## Findings

### 1. `routes.py` Owns Too Many Boundaries

The original monolithic `routes.py` owned:

- HTTP route registration
- API request models
- queue/history operations
- temporary playback stack
- overlay/toast event streaming
- Jellyfin catalog API wrappers
- Jellyfin command parsing and playback URL normalization
- settings apply and runtime env sync
- status payload construction
- PWA/static asset helpers
- the full browser UI document

Status after Phase 1 (PR #21):

- Public route registration has been split into domain modules.
- The aggregate `routes/__init__.py` still owns a large helper surface,
  temporary playback stack, overlay/idle behavior, status payload construction,
  and cross-domain compatibility functions.

Remaining impact:

- Simple UI or playback changes can accidentally affect API behavior.
- Reviewers have to hold multiple subsystems in memory.
- Tests tend toward brittle full-page smoke assertions because seams are hard to
  isolate.

Recommendation:

Continue splitting helper ownership by domain without changing public endpoints:

- `routes/playback.py`
- `routes/queue.py`
- `routes/settings.py`
- `routes/uploads.py`
- `routes/overlay.py`
- `routes/jellyfin.py`
- `routes/status.py`
- `routes/ui.py`
- `routes/assets.py`

Keep the top-level `router` aggregator so imports remain simple.

### 2. The Web UI Is Embedded As One Large Python String

The main UI originally lived inside `ui()` with inline CSS, markup, and
JavaScript. CSS and JavaScript have now been extracted to static assets, but the
HTML shell and dynamic bootstrap data are still generated server-side.

Remaining impact:

- UI regressions are easier to review than before, but template structure,
  bootstrap data, and browser behavior are still mostly covered by Python smoke
  assertions and manual review.
- Tests assert raw strings rather than behavior.
- Browser-only issues are not naturally covered by the Python test suite.

Recommendation:

Finish the current low-build-tooling path:

1. Keep the HTML template simple, using lightweight token replacement where
   needed.
2. Add one Playwright smoke path for `/ui`, settings modal, queue actions, and
   Jellyfin shell visibility.

Do not introduce a frontend build pipeline unless the UI needs bundling. Plain
static files are enough for the current app.

### 3. Runtime Configuration Uses `os.environ` As A Mutable State Bus

Settings are persisted in `state.py`, then mirrored into environment variables
from `main.py` and `routes.py`. Runtime modules also read environment variables
directly.

Examples:

- `main.py` syncs persisted settings into `os.environ` during app startup.
- `routes.update_settings()` writes many `RELAYTV_*` variables at runtime.
- `player.py`, `resolver.py`, `qt_shell_app.py`, `upload_store.py`, and
  `jellyfin_receiver.py` read runtime behavior from environment variables.

Impact:

- Configuration source of truth is ambiguous.
- Tests must monkeypatch environment and module globals.
- Runtime setting changes can leave stale module-level values where variables
  were read at import time.
- Secret values are harder to reason about because they move through process
  environment.

Recommendation:

Introduce a `RuntimeConfig` service:

- Constructed from env plus persisted settings at startup.
- Updated through settings writes.
- Exposed as typed snapshots to playback, resolver, upload, Jellyfin, and UI.
- Optionally mirrors selected values to environment only for subprocesses that
  require env-based configuration.

### 4. Playback State Is Spread Across `state.py`, `player.py`, And Routes

Playback transitions involve:

- `state.NOW_PLAYING`
- `state.SESSION_STATE`
- `state.SESSION_POSITION`
- `state.AUTO_NEXT_SUPPRESS_UNTIL`
- player process globals
- mpv property cache
- queue locks
- temporary playback stack in `routes.py`
- Jellyfin stopped/progress side effects

Impact:

- Close/play-now/queue/resume behavior is hard to reason about.
- Recent bugs around interrupted playback, close behavior, idle dashboard, and
  resume position are symptoms of unclear transition ownership.
- Locking is present, but ownership is distributed across modules.

Recommendation:

Add a playback transition service with explicit commands:

- `play_now(item, preserve_current=False)`
- `queue_item(item)`
- `close_current(retain_queue=True)`
- `advance_queue(reason)`
- `resume_session()`
- `natural_end()`
- `stop_all(reason)`

The service should be the only writer for `NOW_PLAYING`, queue advancement,
session state, and close/resume semantics. `player.py` should become the
process/control adapter, not the owner of product policy.

### 5. Jellyfin Has A Partial Integration Boundary

`integrations/jellyfin_receiver.py` owns Jellyfin status, auth, catalog cache,
and API calls. `routes.py` still owns large amounts of Jellyfin product logic:

- command interpretation
- stream URL construction
- direct/transcode selection
- queueing play-all actions
- track/subtitle selection
- playback metadata merging
- stopped/progress hint emission

Impact:

- Jellyfin changes often require editing unrelated route sections.
- It is difficult to test Jellyfin behavior without FastAPI route context.
- The public operations doc has been updated to make username/password auth the
  preferred path while keeping API key as an optional fallback.

Recommendation:

Create `integrations/jellyfin_service.py` for product-level behavior:

- command normalization
- playable item resolution
- direct/transcode policy
- queue/play actions
- track preference handling
- stopped/progress payload creation

Then keep `jellyfin_receiver.py` focused on Jellyfin HTTP/session/catalog
transport.

### 6. State Persistence Needs Schema Ownership

Persistent queue/history/session/settings are stored as JSON files. The current
code does useful validation and atomic writes, but schema ownership is implicit.

Impact:

- Queue and session item shapes have grown organically.
- Compatibility behavior is encoded in normalization helpers spread across
  state, resolver, player, and routes.
- Future migrations will be difficult if file formats are not versioned.

Recommendation:

Add schema versions and centralized models:

- `QueueItem`
- `HistoryItem`
- `SessionSnapshot`
- `Settings`
- `JellyfinPlaybackRef`

Use Pydantic or dataclasses with explicit `from_legacy()` migration helpers.
Start by validating on load and preserving unknown fields only where needed for
backward compatibility.

### 7. Tests Are Useful But Too Concentrated

The test suite has good regression coverage for recent issues, but it is
concentrated in one large `tests/test_smoke.py` file. Many UI tests assert raw
HTML/JS strings.

Impact:

- Tests catch accidental deletion but not always behavior.
- It is hard to find the right test location for a new feature.
- Large monkeypatch-heavy tests reinforce current module coupling.

Recommendation:

Split tests by behavior:

- `tests/test_settings.py`
- `tests/test_queue.py`
- `tests/test_playback_transitions.py`
- `tests/test_jellyfin_service.py`
- `tests/test_uploads.py`
- `tests/test_runtime_capabilities.py`
- `tests/test_ui_contract.py`

Add contract tests for JSON endpoints and service-level tests for close,
play-now, queue retention, resume, and Jellyfin stopped/progress semantics.

### 8. API Trust Boundary Should Be Explicit

RelayTV is local-first and uses host networking. Many write endpoints are
unauthenticated:

- playback controls
- queue mutation
- settings updates
- upload ingest
- overlay/toast
- Jellyfin command ingress

Impact:

- This is acceptable for a trusted LAN appliance only if it is documented
  clearly.
- Users may expose the service through Home Assistant, reverse proxies, or
  remote access without realizing the control surface is broad.

Recommendation:

Short term:

- Add a clear "trusted LAN only" section to install/API docs.

Medium term:

- Add optional token auth for write endpoints:
  - disabled by default for backward compatibility
  - enabled with `RELAYTV_API_TOKEN`
  - supports `Authorization: Bearer <token>`
  - exempts health and static assets

### 9. Runtime And Installer Policy Are Hard To Trace End-To-End

Runtime policy is split across:

- `scripts/install.sh`
- `docker-compose.yml`
- `docker-compose.release.yml`
- `container_entrypoint.py`
- `player.py`
- `qt_shell_app.py`
- environment defaults

Impact:

- Display/runtime bugs are hard to localize.
- Installer changes need careful string tests because generated compose/env
  output is not modeled.

Recommendation:

Keep the shell installer, but extract a machine-readable runtime profile table
into docs and tests:

- host profile
- display session
- selected player backend
- Qt runtime mode
- required mounts/devices
- expected fallback behavior

Then test profile decisions in Python where possible and keep shell tests for
rendered output.

### 10. Small Duplication And Drift Are Accumulating

Examples:

- Several `_env_bool` / `_env_choice` helpers exist across modules.
- Provider classification and URL handling are duplicated in resolver/player
  paths.
- Jellyfin operations docs have been updated to describe username/password auth
  as preferred and API key as optional/fallback.
- `resolver.resolve_streams_invidious()` contains a duplicated `api_url`
  assignment.

Impact:

- Individually small, but they increase the cost of future changes.

Recommendation:

Create small shared modules:

- `config.py` for env parsing and typed settings snapshots
- `providers.py` for provider detection and URL helpers
- `models.py` for queue/history/session payloads

## Recommended Roadmap

### Phase 0: Guardrails And Documentation (complete)

Goal: improve reviewability without changing runtime behavior.

Status:

- Architecture review and Phase 1 roadmap are in-tree.
- Jellyfin docs now make username/password auth the preferred path and API key
  optional/fallback.
- Trusted-LAN security assumptions are documented in install/API docs.
- `docs/README.md` includes a concise module ownership snapshot; revisit it
  after Phase 1 merges if ownership changes again.

Suggested PR size: small.

### Phase 1: Extract Routes And Static UI Assets (complete — PR #21)

Goal: reduce `routes.py` risk.

- Create route modules and aggregate them into the existing FastAPI router.
- Move `/ui` CSS and JS into static files.
- Keep generated constants/token replacement for server data.
- Preserve all public paths.
- Add one browser smoke test for settings/open/close and basic queue rendering.

Status:

- Public routes have been split into domain modules under
  `app/relaytv_app/routes/`.
- Main `/ui` CSS and JavaScript have been extracted into static files.
- Final validation and optional browser automation remain before merging Phase 1
  to `main`.

### Phase 2: Runtime Config Service (complete — PR #22)

Goal: stop using process environment as the in-process config bus.

- Add `RuntimeConfig` and typed `SettingsSnapshot`.
- Move env parsing into one module.
- Change player/resolver/upload/Jellyfin reads to accept config snapshots or use
  a config provider.
- Retain env mirroring only for subprocesses.

Suggested PR size: medium.

### Phase 3: Playback Transition Service (complete — PR #23)

Goal: make close/play-now/queue/resume behavior deterministic.

- Introduce a playback service with explicit commands.
- Move temporary playback stack out of `routes.py`.
- Centralize queue advancement and close semantics.
- Add focused tests for:
  - play-now interruption
  - close retaining queue
  - close not restarting interrupted media
  - idle dashboard enabled/disabled transitions
  - app restart resume behavior

Suggested PR size: medium to large; split by command path.

### Phase 4: Jellyfin Product Service (complete — PR #24)

Goal: make Jellyfin behavior testable without route context.

- Move command parsing, stream selection, queue actions, and track preference
  handling to a Jellyfin service module.
- Keep receiver transport/cache/auth separate.
- Add service tests with fake receiver/player adapters.
- Update API docs after behavior is isolated.

Suggested PR size: medium.

### Phase 5: Optional API Token (complete — PR #25)

Goal: preserve local-first defaults while giving operators a safe exposure path.

- Add optional token auth for write endpoints.
- Document reverse-proxy examples.
- Keep `GET /health`, static assets, and possibly `/ui` open unless configured
  otherwise.

Suggested PR size: small to medium.

### Phase 6: Operations Test Matrix (complete — PR #26)

Goal: catch runtime regressions before users do.

- Add a documented validation matrix:
  - x11
  - wayland
  - headless
  - native Qt embedded
  - Qt external mpv
  - Raspberry Pi profile
  - amd64 mini PC profile
- Keep host validation scripts, but make expected capabilities explicit in
  fixtures/docs.

Suggested PR size: ongoing.

## Open Follow-Ups

Work the roadmap intentionally did not cover, in rough value order:

1. `test(ui): add one browser (Playwright) smoke path for /ui` — settings
   modal, queue actions, Jellyfin shell visibility (Finding 2's remaining
   item; still string-asserted today).
2. `refactor(state): add versioned models for queue/history/session/settings`
   — Finding 6 was not addressed; migrations remain implicit.
3. `refactor(routes): continue shrinking routes/__init__.py` — the aggregate
   still owns ~3,900 lines of shared helpers, overlay/idle behavior, and
   status payload construction.
4. `refactor(providers): consolidate provider/URL classification` — still
   duplicated between resolver and player paths (Finding 10 remainder).
5. `test: keep splitting test_smoke.py by behavior` — still ~4,200 lines.

## Non-Goals

- Do not replace FastAPI.
- Do not add a frontend framework unless plain static assets become limiting.
- Do not remove existing endpoint aliases until companion apps and Home
  Assistant integrations have migration paths.
- Do not rewrite playback backends before transition ownership is clarified.

## Definition Of Success

The architecture work is succeeding when:

- Most feature changes touch one domain module and one test file.
- Settings changes do not require direct `os.environ` mutation in route handlers.
- Close/play-now/resume behavior is covered by service tests, not only manual
  validation.
- Jellyfin command behavior can be tested without spinning up the full UI route.
- `/ui` changes can be reviewed as HTML/CSS/JS rather than as a Python string
  diff.
