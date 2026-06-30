# Phase 1 Architecture Roadmap

Date started: 2026-06-30

Branch: `codex/architecture-phase-1`

Phase 1 goal: reduce `routes.py` and `/ui` coupling while preserving public
behavior. This phase should make future runtime config and playback-transition
work easier without changing product semantics.

Related review: `docs/ARCHITECTURE_REVIEW.md`

## Working Rules

- Keep Phase 1 work on `codex/architecture-phase-1` until the phase is complete.
- Merge small focused PRs into this branch instead of directly into `main`.
- Keep public endpoint paths, response shapes, and UI behavior stable.
- Rebase or merge current `main` into this branch regularly.
- Update this file whenever a milestone starts, completes, changes scope, or
  uncovers follow-up work.
- Only open the final `codex/architecture-phase-1` to `main` PR after all Phase
  1 validation gates pass.

## Scope

In scope:

- Split `routes.py` into domain routers.
- Extract `/ui` CSS and JavaScript from the Python string into static assets.
- Keep the existing server-rendered HTML approach unless plain static files
  become insufficient.
- Add or reshape tests so moved domains have focused coverage.
- Preserve release, install, and deployment behavior.

Out of scope for Phase 1:

- Runtime config service.
- Playback transition service/state machine.
- Jellyfin product service extraction.
- Optional API token auth.
- Frontend framework or build pipeline.
- Endpoint removals or compatibility-breaking API changes.

## Target Module Shape

Initial target:

- `app/relaytv_app/routes/__init__.py`
- `app/relaytv_app/routes/assets.py`
- `app/relaytv_app/routes/status.py`
- `app/relaytv_app/routes/settings.py`
- `app/relaytv_app/routes/queue.py`
- `app/relaytv_app/routes/playback.py`
- `app/relaytv_app/routes/uploads.py`
- `app/relaytv_app/routes/overlay.py`
- `app/relaytv_app/routes/jellyfin.py`
- `app/relaytv_app/routes/ui.py`

The top-level `router` import used by `main.py` should remain stable.

Initial UI asset target:

- `app/relaytv_app/static/ui/app.css`
- `app/relaytv_app/static/ui/app.js`

## Milestones

### M0: Review And Roadmap Foundation

Status: complete

Deliverables:

- Keep `docs/ARCHITECTURE_REVIEW.md` in-tree.
- Add this Phase 1 roadmap.
- Link both docs from `docs/README.md`.

Validation:

- `ruff check app tests`
- `PYTHONPATH=app pytest -q tests/test_smoke.py`
- `git diff --check`

### M1: Route Inventory And Test Baseline

Status: complete

Deliverables:

- Add a route inventory table or generated snapshot for current public paths.
- Identify endpoint aliases that must be preserved.
- Split the largest smoke assertions into domain-oriented tests where practical
  before moving code.

Notes:

- This is a guardrail milestone. It should not move route code yet.
- Completed with `docs/ARCHITECTURE_PHASE_1_ROUTE_INVENTORY.md` and
  `tests/test_route_inventory.py`.

### M2: Extract Low-Risk Routers

Status: pending

Candidate domains:

- health/app info/status/capabilities
- static/PWA/assets/thumbs/snapshots
- devices/settings read endpoints if dependency paths are simple

Exit criteria:

- `main.py` still imports a single aggregated router.
- All public paths continue to register.
- Existing tests pass without broad assertion rewrites.

### M3: Extract Queue And Playback Routers

Status: pending

Candidate domains:

- queue/history endpoints
- play/pause/resume/seek/volume/close endpoints
- smart/share/play-now aliases

Risks:

- These endpoints touch `state`, `player`, queue locks, resume state, and
  Jellyfin stopped/progress side effects.
- Avoid behavior refactors here. Move code first; improve internals later.

Exit criteria:

- Close/play-now/queue behavior is unchanged.
- Focused tests cover queue retention and close behavior paths already fixed in
  previous PRs.

### M4: Extract Settings Router

Status: pending

Candidate domains:

- `GET /settings`
- `POST /settings`
- YouTube cookies upload/clear
- settings-to-runtime apply helpers

Risks:

- This area mutates `os.environ` today.
- Runtime config cleanup belongs to Phase 2, so Phase 1 should only relocate the
  existing behavior.

Exit criteria:

- Settings UI still loads and applies.
- CEC, idle dashboard, idle notifications, uploads, YouTube, and Jellyfin
  settings retain behavior.

### M5: Extract Jellyfin Router

Status: pending

Candidate domains:

- Jellyfin catalog browse endpoints.
- Jellyfin action endpoints.
- Jellyfin command/heartbeat/progress/stopped endpoints.

Risks:

- Jellyfin route logic is currently interleaved with helper functions that may
  later belong in a service module.
- Phase 1 should prioritize route relocation and import clarity, not behavior
  redesign.

Exit criteria:

- Jellyfin browse, queue, play, subtitle/audio selection, and command ingress
  still work.
- `docs/JELLYFIN_OPERATIONS.md` is updated if the move exposes doc drift.

### M6: Extract UI Static Assets

Status: pending

Deliverables:

- Move CSS out of the `ui()` Python string.
- Move JavaScript out of the `ui()` Python string.
- Keep dynamic server values supplied through a small bootstrap payload or token
  replacement.

Risks:

- The UI contains many interconnected event handlers.
- Avoid redesigning UI while extracting assets.

Exit criteria:

- `/ui` renders the same primary controls.
- Settings modal opens and saves.
- Queue actions still update.
- Jellyfin shell still opens.
- PWA/static asset paths still work.

### M7: Phase 1 Final Validation

Status: pending

Required before merging to `main`:

- `ruff check app tests`
- `PYTHONPATH=app pytest -q tests/test_smoke.py`
- Manual `/ui` review in a running container.
- Manual settings modal apply check.
- Manual playback smoke:
  - play URL
  - enqueue URL
  - play now with queue present
  - close returns to idle/dashboard behavior
- Manual Jellyfin smoke when credentials are available:
  - status badge
  - browse home
  - play title
  - queue title
- Confirm `docs/ARCHITECTURE_PHASE_1_ROADMAP.md` status reflects reality.

## PR And Milestone Log

Add entries here as PRs land into `codex/architecture-phase-1`.

| Date | PR | Target | Summary | Validation | Follow-ups |
| --- | --- | --- | --- | --- | --- |
| 2026-06-30 | local | `codex/architecture-phase-1` | Created Phase 1 roadmap and kept architecture review docs in branch. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_smoke.py`; `git diff --check` | None. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Captured the public route inventory and added a route snapshot test before moving route code. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Begin M2 with low-risk router extraction. |

## Open Questions

- Should UI CSS extraction happen before or after router extraction?
- Should Phase 1 include a minimal Playwright dependency, or should browser
  validation stay manual until the UI assets are extracted?

## Current Recommendation

Begin M2 with low-risk router extraction. Avoid moving playback, settings, and
Jellyfin routes until the router aggregation pattern is proven.
