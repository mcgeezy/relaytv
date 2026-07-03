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

## Architecture Phase 2 Discipline

When working on the Phase 2 architecture effort (runtime config service), keep
work on `codex/architecture-phase-2` unless the user explicitly directs
otherwise.

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
