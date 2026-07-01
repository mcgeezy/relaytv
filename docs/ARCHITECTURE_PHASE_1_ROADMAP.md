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

Status: complete

Candidate domains:

- health/app info/status/capabilities
- static/PWA/assets/thumbs/snapshots
- devices/settings read endpoints if dependency paths are simple

Exit criteria:

- `main.py` still imports a single aggregated router.
- All public paths continue to register.
- Existing tests pass without broad assertion rewrites.

Progress:

- Converted `app/relaytv_app/routes.py` into a `routes` package while
  preserving `from relaytv_app.routes import router`.
- Extracted standalone `/health` into `app/relaytv_app/routes/health.py`.
- Extracted standalone `/devices` into `app/relaytv_app/routes/devices.py`.
- Extracted standalone `/discovery/status` and `/tv/status` into
  `app/relaytv_app/routes/status.py`.
- Extracted `/app/info` and its update-check helpers into
  `app/relaytv_app/routes/app_info.py`.
- Extracted brand/static/PWA endpoints and static asset helpers into
  `app/relaytv_app/routes/assets.py`, while keeping QR connect rendering in
  the aggregate router for now.
- Extracted `/thumbs/{filename}` into `app/relaytv_app/routes/assets.py`.
- Extracted `/snapshots/{filename}` and the GET/POST `/snapshot` aliases into
  `app/relaytv_app/routes/snapshots.py`.
- Seeded `app/relaytv_app/routes/ui.py` with the `/` to `/ui` redirect ahead
  of the larger `/ui` extraction.
- Extracted `GET /media/uploads/{upload_id}/{filename}` into
  `app/relaytv_app/routes/uploads.py`; ingest/upload playback endpoints remain
  in the aggregate router until queue/playback extraction.
- Deferred `/notifications/capabilities` and `/runtime/capabilities` to the
  overlay/playback extraction work because their helper dependencies are shared
  with active overlay and playback status behavior.

### M3: Extract Queue And History Router

Status: complete

Candidate domains:

- queue/history endpoints
- queue add aliases:
  - `POST /enqueue`
  - `POST /queue/add`
  - `POST /api/queue/add`
  - `POST /v1/queue/add`
- queue mutation endpoints:
  - `POST /queue/remove`
  - `POST /queue/move`
  - `POST /queue/dedupe`
  - `POST /clear`
- history endpoints:
  - `GET /history`
  - `POST /history/clear`
  - `POST /history/play`
- queue read endpoint:
  - `GET /queue`

Risks:

- These endpoints touch `state`, queue locks, queue persistence, history
  persistence, player prefetch/prime hooks, and queue UI events.
- Avoid behavior refactors here. Move code first; improve internals later.
- Keep playback actions such as `/play_now`, `/next`, `/previous`, `/smart`,
  `/share`, `/play`, upload ingest enqueue/play, `/now_playing/clear`, and
  close/resume/stop endpoints out of M3 unless a helper must move to preserve
  queue behavior.

Guardrails before moving:

- Keep `tests/test_route_inventory.py` authoritative for aliases.
- Added `tests/test_queue_history_routes.py` for:
  - alias registration
  - queue add path equivalence
  - queue remove/move/dedupe behavior
  - queue clear behavior
  - history read/clear/play behavior
  - queue retention behavior from the recent close/play-now fixes

Progress:

- Added focused queue/history route tests before moving M3 route code.
- Extracted queue add aliases, `/clear`, `/queue`, `/history`,
  `/history/clear`, `/history/play`, `/queue/remove`, `/queue/dedupe`, and
  `/queue/move` into `app/relaytv_app/routes/queue.py`.
- Deferred `/now_playing/clear` to M4 because it may stop playback, advance
  active media, and control idle/dashboard return behavior.

Exit criteria:

- Queue and history response shapes are unchanged.
- Queue aliases continue to resolve to the same endpoint names.
- Close/play-now behavior is not modified in this milestone.
- Existing queue retention tests still pass.

### M4: Extract Playback Router

Status: complete

Candidate domains:

- play/pause/resume/seek/volume/close endpoints
- play-now, play-temporary, next/previous, smart/share aliases
- playback state endpoint
- upload ingest enqueue/play endpoints if still coupled to playback helpers
- `/notifications/capabilities` and `/runtime/capabilities` if helper
  dependencies fit better with overlay/playback movement

Risks:

- These endpoints touch `state`, `player`, queue locks, resume state, temporary
  playback stack, upload streaming, overlay toasts, and Jellyfin
  stopped/progress side effects.
- This is a route relocation milestone, not a playback behavior redesign.
- Preserve the recent fixes for interrupted playback, close behavior, queue
  retention, and idle/dashboard return semantics.

Manual smoke required before closing:

- play URL
- enqueue URL
- play now while queue exists
- close active play with preserved queue
- close returns idle/dashboard without replaying interrupted media
- upload play/enqueue still works
- playback state remains coherent with active media metadata

Exit criteria:

- Close/play-now/queue behavior is unchanged.
- Focused tests cover queue retention and close behavior paths already fixed in
  previous PRs.
- Upload ingest play/enqueue behavior is unchanged.

Progress:

- Added `tests/test_playback_routes.py` for HTTP-level route guardrails around
  `play_now`, `close`, `resume_session`, seek/volume/mute controls, and
  `/playback/state`.
- Extended playback route guardrails for `/now_playing/clear`,
  `/resume/clear`, and `/stop` before moving the close/resume/stop cluster.
- Extended playback route guardrails for `/play_temporary` and
  `/play_temporary/cancel` before moving temporary playback endpoints.
- Extended playback route guardrails for `/play`, `/next`, `/play_at`,
  `/previous`, `/share`, and `/smart` before moving the playback-action
  cluster.
- Added upload-route guardrails for `/ingest/media/enqueue` and
  `/ingest/media/play` before moving upload ingest playback into the uploads
  router.
- Added capability-route guardrails for `/notifications/capabilities` and
  `/runtime/capabilities` before moving those endpoints into a capability
  router.
- Extracted pause/resume/toggle-pause, seek, volume, mute, and
  `/playback/state` into `app/relaytv_app/routes/playback.py`.
- Extracted `/playback/play` and `/playback/toggle` into
  `app/relaytv_app/routes/playback.py`.
- Extracted `/play_now` and its interrupt-preservation helper into
  `app/relaytv_app/routes/playback.py`.
- Extracted `/now_playing/clear`, `/close`, `/resume/clear`,
  `/resume_session`, and `/stop` into `app/relaytv_app/routes/playback.py`.
- Extracted `/play_temporary` and `/play_temporary/cancel` into
  `app/relaytv_app/routes/playback.py`, while keeping the shared temporary
  playback stack helpers in the aggregate router until the remaining playback
  slices are moved.
- Extracted `/play`, `/next`, `/play_at`, `/previous`, `/share`, and `/smart`
  into `app/relaytv_app/routes/playback.py`.
- Extracted `/ingest/media`, `/ingest/media/enqueue`, and
  `/ingest/media/play` into `app/relaytv_app/routes/uploads.py`.
- Extracted `/notifications/capabilities` and `/runtime/capabilities` into
  `app/relaytv_app/routes/capabilities.py`; the shared capability helper
  functions remain in the aggregate router for now because status, overlay,
  and playback-state logic still use them.

### M5: Extract Settings Router

Status: complete

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

Progress:

- Added `tests/test_settings_routes.py` for HTTP-level guardrails around
  sanitized settings reads, YouTube cookies upload/clear, Invidious validation,
  and runtime environment side effects from settings updates.
- Extracted `GET /settings`, `POST /settings`,
  `POST /settings/youtube/cookies`, and
  `POST /settings/youtube/cookies/clear` into
  `app/relaytv_app/routes/settings.py`.

### M6: Extract Jellyfin Router

Status: complete

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

Progress:

- Started with the low-risk catalog/status slice, leaving playback actions,
  audio/subtitle mutation, command ingress, heartbeat, progress, and stopped
  endpoints for later M6 slices.
- Added `tests/test_jellyfin_routes.py` for HTTP-level guardrails around
  Jellyfin integration status, catalog cache clearing, home/search/movies,
  series/seasons/episodes, item detail, and adjacent episode reads.
- Extracted Jellyfin catalog/status routes into
  `app/relaytv_app/routes/jellyfin.py`:
  - `GET /integrations/jellyfin/status`
  - `POST /integrations/jellyfin/catalog/cache_clear`
  - `GET /jellyfin/home`
  - `GET /jellyfin/search`
  - `GET /jellyfin/movies`
  - `GET /jellyfin/tv/series`
  - `GET /jellyfin/tv/series/{series_id}/seasons`
  - `GET /jellyfin/tv/series/{series_id}/episodes`
  - `GET /jellyfin/item/{item_id}`
  - `GET /jellyfin/item/{item_id}/adjacent`
- Added Jellyfin audio/subtitle route guardrails for runtime audio selection,
  in-place audio switching, and unavailable subtitle index validation.
- Extracted Jellyfin audio/subtitle option and selection routes into
  `app/relaytv_app/routes/jellyfin.py`:
  - `GET /jellyfin/audio/options`
  - `POST /jellyfin/audio/select`
  - `GET /jellyfin/subtitle/options`
  - `POST /jellyfin/subtitle/select`
- Added Jellyfin action route guardrails for series play-all command payloads,
  empty series handling, play-next mapping, resume position resolution, and
  duplicate UI action suppression.
- Extracted Jellyfin item action and series play-all routes into
  `app/relaytv_app/routes/jellyfin.py`:
  - `POST /jellyfin/action`
  - `POST /jellyfin/tv/series/{series_id}/play_all`
- Added Jellyfin lifecycle route guardrails for settings-derived device names,
  optional register-on-connect, command-state resets, UI refresh events, and
  pending register handshakes.
- Extracted Jellyfin lifecycle routes into
  `app/relaytv_app/routes/jellyfin.py`:
  - `POST /integrations/jellyfin/connect`
  - `POST /integrations/jellyfin/disconnect`
  - `POST /integrations/jellyfin/register`
- Added Jellyfin command/progress/stopped route guardrails for deprecated push
  ingress, command dispatch, disabled receiver handling, heartbeat pending
  responses, progress snapshots, and stopped snapshots.
- Extracted remaining Jellyfin command/progress/stopped routes into
  `app/relaytv_app/routes/jellyfin.py`:
  - `POST /integrations/jellyfin/push`
  - `POST /integrations/jellyfin/command`
  - `POST /integrations/jellyfin/heartbeat`
  - `GET /integrations/jellyfin/progress_snapshot`
  - `POST /integrations/jellyfin/stopped`
  - `GET /integrations/jellyfin/stopped_snapshot`
- M6 route extraction is complete. Command implementation helpers remain in the
  aggregate module for now and should be considered Phase 2/service extraction
  follow-up, not unfinished Phase 1 route work.

### M7: Extract UI Static Assets

Status: complete

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

Progress:

- Added narrow static UI asset serving for `GET /static/ui/{asset_name}`.
- Extracted the main `/ui` stylesheet into
  `app/relaytv_app/static/ui/app.css`.
- Updated `/ui` to load `/static/ui/app.css` with a stylesheet link.
- Updated smoke coverage so HTML structure remains checked through `/ui` while
  CSS-specific assertions read the stylesheet asset.
- Extracted the main `/ui` JavaScript block into
  `app/relaytv_app/static/ui/app.js`.
- Kept the dynamic idle panel catalog as a small inline bootstrap payload before
  loading `/static/ui/app.js`.
- Updated smoke coverage so JavaScript-specific assertions read the static
  script asset.
- M7 static UI asset extraction is complete.

### M8: Phase 1 Final Validation

Status: in progress

Required before merging to `main`:

- `ruff check app tests`
- `PYTHONPATH=app pytest -q tests/test_smoke.py`
- `PYTHONPATH=app pytest -q tests/test_route_inventory.py`
- Manual `/ui` review in a running container.
- Manual settings modal apply check.
- Manual playback smoke:
  - play URL
  - enqueue URL
  - play now with queue present
  - close returns to idle/dashboard behavior
  - close does not replay interrupted media
  - upload play/enqueue
- Manual Jellyfin smoke when credentials are available:
  - status badge
  - browse home
  - play title
  - queue title
- Confirm `docs/ARCHITECTURE_PHASE_1_ROADMAP.md` status reflects reality.

Progress:

- Automated validation passed:
  - `ruff check app tests`
  - `PYTHONPATH=app pytest -q tests/test_smoke.py tests/test_route_inventory.py`
  - `PYTHONPATH=app pytest -q`
  - `git diff --check`
- Live HTTP review passed on a local server with workers disabled and temporary
  state/upload/thumb directories:
  - `GET /ui`
  - `GET /static/ui/app.css`
  - `GET /static/ui/app.js`
  - `GET /assets/banner.png`
  - `GET /pwa/brand/banner.png`
- Confirmed the updated banner image is served by the banner endpoints.
- Browser-rendered views were reviewed in the live container environment and
  confirmed good.
- Rebuilt and force-recreated the live `relaytv` Compose container from the
  local branch image with existing `.env` credentials and data mounts.
- Live credentialed HTTP smoke passed against the rebuilt container:
  - `GET /status`
  - `GET /settings`
  - `GET /ui`
  - `GET /idle`
  - `GET /static/ui/app.css`
  - `GET /static/ui/app.js`
  - `GET /assets/banner.png`
  - `GET /pwa/brand/banner.png`
  - `GET /integrations/jellyfin/status`
  - `GET /jellyfin/home?limit=6&refresh=1`
  - `GET /jellyfin/movies?limit=6&refresh=1`
  - `GET /jellyfin/search?q=the&limit=3`
- Live status confirmed the Qt shell runtime is selected and Jellyfin is
  enabled, authenticated, connected, and running.
- Raspberry Pi live-container review found that stale queue state could keep
  `/status` and `/playback/state` in an endless queue-handoff/buffering state
  after the Qt playback runtime exited. Tightened handoff detection so queued
  items alone do not count as an active transition; explicit playback or
  auto-next transition markers are now required.
- Rebuilt the Raspberry Pi live container from the updated branch and confirmed
  that after the startup transition window expires, `/status` and
  `/playback/state` return idle with `transition_in_progress=false`,
  `qt_shell_running=true`, and backend telemetry available.
- Follow-up Raspberry Pi testing found that when `/play_now` playback crashed
  after interrupting a Jellyfin title, the autoplay worker consumed the
  interrupt-preserved Jellyfin queue entry as `auto_next`. Auto-next is now
  suppressed for `_relaytv_interrupt_preserved` queue heads so interrupted
  media remains queued for explicit user action instead of replaying
  automatically.
- Rebuild-resume review found that startup restore depended on the autoplay
  worker and shutdown depended on the periodic session tracker. Startup now
  attempts persisted-session restore synchronously before showing the idle Qt
  shell, and shutdown writes one final session snapshot before teardown.
- Follow-up Raspberry Pi testing found that active mpv playback can outlive
  RelayTV's in-memory `NOW_PLAYING` state during rebuilds. Shutdown snapshots
  now recover a resumable Jellyfin session from the live mpv path and matching
  history entry before persisting session state, including rotated/volatile
  Jellyfin URL tokens.
- Controlled Raspberry Pi replay found that slower Qt/libmpv startup can leave
  a now-playing item in a brief runtime telemetry gap after `/history/play`.
  `/status` no longer demotes a queue-empty playing session to idle during that
  gap; it reports a transition state and lets the playback worker handle true
  natural ends.
- Manual settings apply plus playback/Jellyfin play and queue actions still
  need explicit confirmation before opening the final Phase 1 to `main` PR.

## PR And Milestone Log

Add entries here as PRs land into `codex/architecture-phase-1`.

| Date | PR | Target | Summary | Validation | Follow-ups |
| --- | --- | --- | --- | --- | --- |
| 2026-06-30 | local | `codex/architecture-phase-1` | Created Phase 1 roadmap and kept architecture review docs in branch. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_smoke.py`; `git diff --check` | None. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Captured the public route inventory and added a route snapshot test before moving route code. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Begin M2 with low-risk router extraction. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Started M2 by converting `routes.py` to a package and extracting standalone `/health`. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Continue M2 with status/assets routes. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Continued M2 by extracting standalone `/devices`. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Continue M2 with status/assets routes. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Continued M2 by extracting `/discovery/status` and `/tv/status`. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Continue M2 with assets or app info. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Continued M2 by extracting `/app/info` and release update-check helpers. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Continue M2 with static/PWA/assets routes or simple root redirect. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Continued M2 by extracting brand/static/PWA asset routes and shared static asset helpers. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Continue M2 with thumbnails/snapshots/uploads or simple root redirect. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Continued M2 by moving thumbnail serving into the asset router. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Continue M2 with snapshots/uploads or simple root redirect. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Continued M2 by extracting snapshot serving and capture aliases. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Continue M2 with uploads or simple root redirect. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Continued M2 by seeding the UI router with the root redirect. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Continue M2 with uploads or capability endpoints. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Continued M2 by extracting static uploaded-media serving while leaving ingest/playback upload routes in place. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Continue M2 with capability endpoints or begin planning M3 queue/playback moves. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Closed M2 after extracting the low-risk standalone routers and documenting deferred capability endpoints. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Begin M3 with queue/history route extraction before playback controls. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Split queue/history and playback into separate milestones, added M3 guardrails, and renumbered later milestones. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Begin M3 with queue/history tests and route extraction. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Added focused M3 queue/history route guardrail tests before moving route code. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_queue_history_routes.py tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Extract queue/history routes. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Extracted queue/history routes into `app/relaytv_app/routes/queue.py` and deferred `/now_playing/clear` to playback routing. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_queue_history_routes.py tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Close M3 after final route inventory review, then begin M4 playback router planning. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Closed M3 after confirming queue/history endpoints moved and `/now_playing/clear` remains in the playback/UI inventory group. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_queue_history_routes.py tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Begin M4 playback router planning. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Started M4 by adding HTTP-level playback route guardrails before moving playback routes. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_playback_routes.py tests/test_queue_history_routes.py tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Extract a narrow playback control slice first. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Extracted low-risk playback controls and `/playback/state` into `app/relaytv_app/routes/playback.py`. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_playback_routes.py tests/test_queue_history_routes.py tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Continue M4 with `/playback/play` and `/playback/toggle` or close/play-now planning. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Extracted `/playback/play` and `/playback/toggle` into the playback router. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_playback_routes.py tests/test_queue_history_routes.py tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Continue M4 with close/play-now planning. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Extracted `/play_now` and interrupt-preservation behavior into the playback router. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_playback_routes.py tests/test_queue_history_routes.py tests/test_route_inventory.py tests/test_smoke.py`; `git diff --check` | Continue M4 with close/resume/stop planning. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Added focused Jellyfin audio/subtitle route guardrails before moving audio/subtitle routes. | `PYTHONPATH=app pytest -q tests/test_jellyfin_routes.py` | Extract audio/subtitle routes into the Jellyfin router. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Extracted Jellyfin audio/subtitle options and selection into `app/relaytv_app/routes/jellyfin.py`. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_jellyfin_routes.py tests/test_smoke.py` | Continue M6 with item actions or connect/disconnect/register. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Added focused Jellyfin action and series play-all route guardrails before moving command-construction routes. | `PYTHONPATH=app pytest -q tests/test_jellyfin_routes.py` | Extract item action and series play-all routes into the Jellyfin router. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Extracted Jellyfin item action and series play-all routes into `app/relaytv_app/routes/jellyfin.py`. | `PYTHONPATH=app pytest -q tests/test_jellyfin_routes.py tests/test_route_inventory.py tests/test_smoke.py` | Continue M6 with connect/disconnect/register. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Added focused Jellyfin lifecycle route guardrails before moving connect/disconnect/register. | `PYTHONPATH=app pytest -q tests/test_jellyfin_routes.py` | Extract lifecycle routes into the Jellyfin router. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Extracted Jellyfin connect/disconnect/register routes into `app/relaytv_app/routes/jellyfin.py`. | `PYTHONPATH=app pytest -q tests/test_jellyfin_routes.py tests/test_route_inventory.py tests/test_smoke.py` | Continue M6 with command ingress plus heartbeat/progress/stopped. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Added focused Jellyfin command/progress/stopped route guardrails before moving the remaining Jellyfin routes. | `PYTHONPATH=app pytest -q tests/test_jellyfin_routes.py` | Extract remaining Jellyfin routes into the Jellyfin router. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Completed M6 by extracting command ingress, push, heartbeat, progress snapshot, stopped, and stopped snapshot routes into `app/relaytv_app/routes/jellyfin.py`. | `PYTHONPATH=app pytest -q tests/test_jellyfin_routes.py tests/test_route_inventory.py tests/test_smoke.py` | Begin M7 UI static asset extraction. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Started M7 by extracting the main `/ui` stylesheet into `app/relaytv_app/static/ui/app.css` and adding narrow static UI asset serving. | `PYTHONPATH=app pytest -q tests/test_smoke.py tests/test_route_inventory.py` | Continue M7 with JavaScript extraction. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Completed M7 by extracting the main `/ui` JavaScript into `app/relaytv_app/static/ui/app.js` with an inline bootstrap for dynamic catalog data. | `PYTHONPATH=app pytest -q tests/test_smoke.py tests/test_route_inventory.py` | Begin M8 final validation and manual UI review. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Started M8 final validation, included the updated banner image, passed automated gates and live HTTP asset checks. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_smoke.py tests/test_route_inventory.py`; `PYTHONPATH=app pytest -q`; `git diff --check`; live `GET /ui`, `/static/ui/app.css`, `/static/ui/app.js`, `/assets/banner.png`, `/pwa/brand/banner.png` | Complete rendered browser review and credentialed Jellyfin smoke when environment is available. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Rebuilt and force-recreated the live Compose container, recorded user-confirmed browser view review, and ran credentialed live HTTP/Jellyfin smoke. | `docker compose up -d --build --force-recreate relaytv`; live `GET /status`, `/settings`, `/ui`, `/idle`, `/static/ui/app.css`, `/static/ui/app.js`, `/assets/banner.png`, `/pwa/brand/banner.png`, `/integrations/jellyfin/status`, `/jellyfin/home?limit=6&refresh=1`, `/jellyfin/movies?limit=6&refresh=1`, `/jellyfin/search?q=the&limit=3`; `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_smoke.py`; `git diff --check` | Complete explicit settings apply plus playback/Jellyfin play and queue smoke before final Phase 1 to `main` PR. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Fixed stale queue-handoff detection found on Raspberry Pi after interrupt/advance left Qt playback runtime unavailable with preserved queue state. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_smoke.py tests/test_route_inventory.py`; `PYTHONPATH=app pytest -q`; `git diff --check`; Raspberry Pi `docker compose up -d --build --force-recreate relaytv`; live `/status` and `/playback/state` after transition expiry | Continue manual playback/Jellyfin action smoke for final M8 sign-off. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Suppressed auto-next for interrupt-preserved queue entries so failed `/play_now` attempts do not automatically replay the interrupted Jellyfin item. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_smoke.py tests/test_route_inventory.py`; `PYTHONPATH=app pytest -q`; `git diff --check` | Rebuild Raspberry Pi live container and verify failed play-now leaves the interrupted item queued instead of auto-playing it. |
| 2026-06-30 | local | `codex/architecture-phase-1` | Made rebuild resume more deterministic by persisting a final shutdown session snapshot and restoring persisted playback before idle shell startup. | `ruff check app tests`; `PYTHONPATH=app pytest -q tests/test_smoke.py tests/test_route_inventory.py`; `PYTHONPATH=app pytest -q`; `git diff --check` | Rebuild Raspberry Pi live container and retest active Jellyfin playback resume across recreate. |

## Open Questions

- Should UI CSS extraction happen before or after router extraction?
- Should Phase 1 include a minimal Playwright dependency, or should browser
  validation stay manual until the UI assets are extracted?

## Current Recommendation

Complete the remaining M8 manual settings apply and playback action checks in
the live environment. The automated gates, rendered browser view review, and
credentialed live HTTP/Jellyfin browse smoke have passed; do not merge Phase 1
to `main` until settings apply plus playback/Jellyfin play and queue actions
are explicitly confirmed.
