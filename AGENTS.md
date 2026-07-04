# Agent Instructions

## Release and Changelog Discipline

Use Conventional Commit titles for commits and pull requests:

- `feat: ...` for user-visible features
- `fix: ...` for bug fixes
- `docs: ...` for documentation-only changes
- `deps: ...` for dependency updates
- `chore: ...` for maintenance that should not trigger a release
- `refactor: ...`, `test: ...`, `build: ...`, or `ci: ...` when those scopes fit

For breaking changes, add `!`, for example:

```text
feat!: change install configuration format
```

When preparing a pull request, include release-note-quality context:

- user impact
- operator/deployment impact
- breaking changes, or `None`
- tests run

Do not manually create normal release tags.
Do not manually edit `CHANGELOG.md` for normal feature, fix, docs, or dependency
changes. Release Please owns version bumps, release pull requests, changelog
generation, Git tags, GitHub Releases, and immutable release image tags.

## Architecture Phase 5 Discipline

When working on the Phase 5 architecture effort (optional API token), keep
work on `codex/architecture-phase-5` unless the user explicitly directs
otherwise.

Keep `docs/ARCHITECTURE_PHASE_5_ROADMAP.md` current when milestones start,
complete, change scope, or uncover important follow-up work.

Phase 5 scope:

- optional bearer-token auth for write requests (`POST`/`PUT`/`PATCH`/
  `DELETE`) via app middleware; disabled by default, enabled with
  `RELAYTV_API_TOKEN`; reads, `/health`, `/ui`, and static assets stay open
- the token is env-only: plumbed through `runtime_config`
  (`SETTINGS_BUS_VARS`), never persisted to settings.json, never returned by
  `/settings`, never logged
- minimal web UI compatibility (one fetch wrapper attaching the bearer
  token from localStorage) — no frontend framework or redesign
- operator docs: trusted-LAN default posture, token setup, reverse-proxy
  exposure examples
- guardrail tests for the auth contract (`tests/test_api_auth.py`)

Do not start Phase 6+ work on this branch unless the user explicitly expands
the scope. Out of scope for Phase 5:

- auth for read endpoints, sessions/users, or credential stores beyond the
  single env token
- TLS termination inside RelayTV
- frontend framework or build pipeline
- endpoint removals or compatibility-breaking API changes
- with `RELAYTV_API_TOKEN` unset, any behavior change at all

Prefer small PRs into `codex/architecture-phase-5`, not directly into `main`.
No release until the architecture roadmap completes: phases ship as
`refactor:` PRs that intentionally do not trigger release-please.

## Architecture Phase 4 Discipline (complete)

Phase 4 (Jellyfin product service) completed on
`codex/architecture-phase-4`; final PR:
https://github.com/mcgeezy/relaytv/pull/24. The full record lives in
`docs/ARCHITECTURE_PHASE_4_ROADMAP.md`.

When working on the Phase 4 architecture effort (Jellyfin product service),
keep work on `codex/architecture-phase-4` unless the user explicitly directs
otherwise.

Keep `docs/ARCHITECTURE_PHASE_4_ROADMAP.md` current when milestones start,
complete, change scope, or uncover important follow-up work.

Phase 4 scope:

- extract Jellyfin product logic (command interpretation and ingress, stream
  URL construction, direct/transcode policy, playable item resolution, track
  preference handling, metadata enrichment, stopped/progress payloads) into
  `integrations/jellyfin_service.py`
- keep `integrations/jellyfin_receiver.py` as the transport/session/catalog
  adapter and the routes modules as HTTP guards, request models, ack shaping,
  and UI events; the service never imports the routes package
- route playback transitions inside migrated code through `playback_service`
  commands; document any remaining direct writes as pinned exceptions
- machine-checked Jellyfin route-surface inventory
  (`tests/test_jellyfin_inventory.py`) that tightens per milestone
- service-level tests with fake receiver/player adapters
  (`tests/test_jellyfin_service.py`)

Do not start Phase 5+ work on this branch unless the user explicitly expands
the scope. Out of scope for Phase 4:

- optional API token auth
- frontend framework or build pipeline
- endpoint removals or compatibility-breaking API changes
- rewriting `jellyfin_receiver.py` transport/cache/auth internals
- changing persisted file formats
- Jellyfin behavior changes — document discovered bugs in the roadmap instead

Prefer small PRs into `codex/architecture-phase-4`, not directly into `main`.
No release until the architecture roadmap completes: phases ship as
`refactor:` PRs that intentionally do not trigger release-please.

## Architecture Phase 3 Discipline (complete)

Phase 3 (playback transition service) completed on
`codex/architecture-phase-3`; final PR:
https://github.com/mcgeezy/relaytv/pull/23. The full record lives in
`docs/ARCHITECTURE_PHASE_3_ROADMAP.md`.

Keep `docs/ARCHITECTURE_PHASE_3_ROADMAP.md` current when milestones start,
complete, change scope, or uncover important follow-up work.

Phase 3 scope:

- introduce a `playback_service` module owning explicit transition commands
  (play-now, queue, close, advance, resume, natural end, stop-all), facade
  first with 1:1 delegation, policy moves later
- centralize auto-next suppression writes behind one service API
- move the temporary playback stack out of the routes package
- centralize close/resume semantics; the service becomes the only writer of
  playback transition globals outside `state.py`
- machine-checked transition writer inventory that tightens per milestone

Do not start Phase 4+ work on this branch unless the user explicitly expands
the scope. Out of scope for Phase 3:

- Jellyfin product service extraction
- optional API token auth
- frontend framework or build pipeline
- endpoint removals or compatibility-breaking API changes
- rewriting mpv process management, the Qt shell supervisor, or CEC handling
- changing persisted session/queue/history file formats
- playback behavior changes — document discovered bugs in the roadmap instead

Prefer small PRs into `codex/architecture-phase-3`, not directly into `main`.
No release until the architecture roadmap completes: phases ship as
`refactor:` PRs that intentionally do not trigger release-please.

## Architecture Phase 2 Discipline (complete)

Phase 2 (runtime config service) completed on `codex/architecture-phase-2`;
final PR: https://github.com/mcgeezy/relaytv/pull/22. The full record lives in
`docs/ARCHITECTURE_PHASE_2_ROADMAP.md`.

Keep `docs/ARCHITECTURE_PHASE_2_ROADMAP.md` current when milestones start,
complete, change scope, or uncover important follow-up work.

Phase 2 scope:

- introduce a shared typed env-parsing module and a `RuntimeConfig` service
  with typed settings snapshots
- migrate in-process `os.getenv`/`os.environ` readers to config snapshots,
  one domain at a time behind guardrail tests
- contain runtime `os.environ` writes to an explicit subprocess-mirroring
  boundary
- reshape settings tests toward config behavior instead of monkeypatched env

Do not start Phase 3+ work on this branch unless the user explicitly expands
the scope. Out of scope for Phase 2:

- playback transition/state-machine rewrite
- Jellyfin product service extraction
- optional API token auth
- frontend framework or build pipeline
- endpoint removals or compatibility-breaking API changes
- changing `RELAYTV_*` variable names, defaults, or precedence semantics
- changing what child processes (mpv, yt-dlp, Qt shell, overlay) receive in
  their environment

Prefer small PRs into `codex/architecture-phase-2`, not directly into `main`.
Preserve public behavior while moving configuration plumbing. If a behavior bug
is discovered, document it in the Phase 2 roadmap unless the user asks for an
immediate fix.

## Architecture Phase 1 Discipline (complete)

Phase 1 is complete; the final PR to `main` is
https://github.com/mcgeezy/relaytv/pull/21. The rules below remain for
historical context and for any follow-up commits that must land on the Phase 1
branch before merge.

When working on the Phase 1 architecture effort, keep work on
`codex/architecture-phase-1` unless the user explicitly directs otherwise.

Keep `docs/ARCHITECTURE_PHASE_1_ROADMAP.md` current when milestones start,
complete, change scope, or uncover important follow-up work. Preserve
`docs/ARCHITECTURE_REVIEW.md` as the higher-level findings document.

Phase 1 scope:

- split `routes.py` into domain routers while preserving public endpoint paths,
  aliases, request models, response shapes, and runtime behavior
- extract `/ui` CSS and JavaScript into static assets without redesigning the UI
- add or reshape tests only as needed to protect moved domains

Do not start Phase 2+ work on this branch unless the user explicitly expands the
scope. Out of scope for Phase 1:

- runtime config service
- playback transition/state-machine rewrite
- Jellyfin product service extraction
- optional API token auth
- frontend framework or build pipeline
- endpoint removals or compatibility-breaking API changes

Prefer small PRs into `codex/architecture-phase-1`, not directly into `main`.
Avoid behavior refactors while moving structure. If a behavior bug is discovered,
document it in the Phase 1 roadmap unless the user asks for an immediate fix.

Before finishing Phase 1 work, run:

```text
ruff check app tests
PYTHONPATH=app pytest -q tests/test_smoke.py tests/test_route_inventory.py
git diff --check
```
