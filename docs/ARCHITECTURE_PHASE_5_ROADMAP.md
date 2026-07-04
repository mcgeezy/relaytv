# Phase 5 Architecture Roadmap

Date started: 2026-07-03

Branch: `codex/architecture-phase-5` (cut from `main` at the Phase 4 squash
merge, PR #24 / `5ebebb3`)

Phase 5 goal: make the API trust boundary explicit while preserving
local-first defaults. Add optional bearer-token auth for write endpoints —
disabled by default, enabled with `RELAYTV_API_TOKEN` — and document the
trusted-LAN assumption plus safe exposure patterns (reverse proxy examples).

Related review: `docs/ARCHITECTURE_REVIEW.md` (Finding 8 and the Phase 5
roadmap section)

## Working Rules

- Keep Phase 5 work on `codex/architecture-phase-5` until the phase is
  complete.
- Merge small focused PRs into this branch instead of directly into `main`.
- Backward compatibility is the hard constraint: with `RELAYTV_API_TOKEN`
  unset (the default), every endpoint behaves exactly as today.
- Update this file whenever a milestone starts, completes, changes scope, or
  uncovers follow-up work.
- The token is an env-only secret: read through `runtime_config` snapshots,
  never persisted to settings.json, never returned by `/settings`, never
  logged.
- Only open the final `codex/architecture-phase-5` to `main` PR after all
  Phase 5 validation gates pass.
- No release until the architecture roadmap completes (user decision
  2026-07-03); phases ship as `refactor:` PRs that do not trigger
  release-please.

## Scope

In scope:

- Optional bearer-token guard for write requests (`POST`/`PUT`/`PATCH`/
  `DELETE`), enforced by app middleware so current and future write routes
  are covered by default:
  - disabled when `RELAYTV_API_TOKEN` is unset or blank (default)
  - `Authorization: Bearer <token>` with constant-time comparison
  - `401` + `WWW-Authenticate: Bearer` on missing/incorrect token
  - reads stay open: `GET`/`HEAD`/`OPTIONS` (health, status, `/ui`, static
    assets) are never guarded
- `RELAYTV_API_TOKEN` plumbed through the Phase 2 runtime config service
  (`SETTINGS_BUS_VARS`) so in-process readers use snapshots.
- Minimal web UI compatibility: a fetch wrapper that attaches the bearer
  token from browser localStorage and prompts once when a write is rejected,
  so the served UI keeps working when an operator enables the token.
- Operator docs: explicit "trusted LAN only" default, token setup, curl
  examples, and reverse-proxy exposure examples.
- Guardrail tests for the auth contract (`tests/test_api_auth.py`).

Out of scope (per review and user direction):

- Auth for read endpoints, sessions/users, or any credential store beyond
  the single env token.
- TLS termination inside RelayTV (documented as the reverse proxy's job).
- Frontend framework or build pipeline changes; the UI change is a small
  plain-JS wrapper.
- Endpoint removals or compatibility-breaking API changes.
- Changing Jellyfin receiver transport/auth internals.

## Baseline (measured at phase start)

- Every write endpoint is unauthenticated; the app relies on trusted-LAN
  host networking (`network_mode: host`, port 8787).
- All state-mutating endpoints use `POST` (routes packages register no
  `PUT`/`PATCH`/`DELETE` handlers); guarding by method covers the full
  write surface including uploads and Jellyfin command ingress.
- Internal runtime components (`qt_shell_app`, `overlay_app`, idle
  dashboard) only issue `GET` requests against the local API, and the ops
  scripts (`doctor.sh`, `host-ops.sh`) do not POST — enabling the token
  cannot break the appliance runtime itself.
- The Jellyfin integration is outbound-polling (receiver → server); nothing
  external pushes writes into RelayTV as part of normal operation.
- The docker healthcheck and install verification use `GET /health`.
- `docker-compose.yml` loads `.env` via `env_file`, so `RELAYTV_API_TOKEN`
  set in `/opt/relaytv/.env` reaches the app with no compose changes.
- The web UI issues writes as plain `fetch(..., {method: 'POST'})` calls
  scattered across `static/ui/app.js` (~25 call sites) — token support
  belongs in one global wrapper, not per call site.

## Milestones

### M0: Branch, Roadmap, And Discipline Docs

Status: complete

- Branch cut from `main` at `5ebebb3` (Phase 4 squash merge).
- This roadmap; `AGENTS.md` Phase 5 discipline section (Phase 4 marked
  complete); `docs/README.md` link.

### M1: Token Guard Middleware, Config Plumbing, And Tests

Status: complete

- `RELAYTV_API_TOKEN` added to `SETTINGS_BUS_VARS` (snapshot-readable,
  env-only; excluded from `/settings` responses and settings persistence).
- New `app/relaytv_app/api_auth.py`: token lookup from the runtime config
  snapshot, bearer extraction, constant-time verification
  (`hmac.compare_digest`), and the middleware guard callable.
- Guard registered in `create_app` before the slow-request logger is
  registered, so rejected writes still show up in request logging.
- `tests/test_api_auth.py`: default-off passthrough, missing token 401,
  wrong token 401, correct token accepted, reads and `/health`/`/ui`/static
  always open, `WWW-Authenticate` header shape, `/settings` never exposes
  the token.
- Landed as designed; `docs/ARCHITECTURE_PHASE_2_ENV_INVENTORY.md`
  regenerated for the new settings-bus variable. 299 tests green.

### M2: Web UI Token Support

Status: complete

- One global fetch wrapper in `static/ui/app.js` that attaches
  `Authorization: Bearer <token>` from `localStorage['relaytv_api_token']`
  when present, and on a `401` write response prompts once for the token,
  stores it, and retries.
- No visual redesign; behavior with no token configured is unchanged.
- Landed as a self-contained IIFE wrapping `window.fetch` at the top of
  `app.js`: bearer header attached only to same-origin requests, prompt
  fires at most once per page load and only when the `401` carries
  `WWW-Authenticate: Bearer`, entered token stored and the request
  retried once.

### M3: Operator Docs

Status: planned

- `INSTALL.md`/`API.md`: explicit "trusted LAN only" default posture,
  `RELAYTV_API_TOKEN` setup, curl examples with the bearer header, what
  stays open (reads, health, UI, static assets), UI token entry, and
  reverse-proxy exposure examples (TLS + auth at the proxy).

### M4: Phase 5 Final Validation

Status: planned

- Full gates: `ruff check app tests`, `PYTHONPATH=app pytest -q`, CI-like
  fresh-venv run.
- Live validation on the appliance: token unset → behavior identical
  (playback, queue, settings, Jellyfin smoke); token set in `.env` →
  writes rejected without the header and accepted with it, `GET`
  endpoints/UI/healthcheck unaffected, Jellyfin receiver operation
  unaffected, web UI works after storing the token; then restore the
  token-off default.
- Open the final `codex/architecture-phase-5` to `main` PR.

## PR And Milestone Log

| Date | Item | Notes |
| --- | --- | --- |
| 2026-07-03 | Phase 5 started | Branch cut from `main` at `5ebebb3` (Phase 4 squash merge); roadmap committed. |
