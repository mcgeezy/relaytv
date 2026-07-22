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
- whether the release warrants a highlight heading (see Release highlights
  below); add or update the file when it does

Do not manually create normal release tags.
Do not manually edit `CHANGELOG.md` for normal feature, fix, docs, or dependency
changes. Release Please owns version bumps, release pull requests, changelog
generation, Git tags, GitHub Releases, and immutable release image tags.

Do not push new commits to a branch after its pull request has merged: the
commits are silently orphaned (squash-merge already happened) and must be
cherry-picked into a fresh PR. Release Please re-reads merged PR bodies on
every run, so a PR body's `BEGIN_COMMIT_OVERRIDE` block must always describe
exactly what its squash contains.

### Release highlights

`.github/workflows/release-please.yml` post-processes each GitHub Release body:
below the hero image (and above the generated notes) it prepends
`docs/release-highlights/<version>.md` when that file exists. `<version>` is the
release-please version without the leading `v` (for example `0.8.0`). Use this
for a short heading that introduces a notable user-facing feature landing in
that release; it changes the GitHub Release notes only, not `CHANGELOG.md`, and
a release without a matching file is published unchanged.

Most PRs need no highlight. When a PR introduces a feature worth a lead-in
beyond the generated bullet list, review whether its release warrants one and
add or update `docs/release-highlights/<next-version>.md` in the same PR. Keep
it to a heading plus a couple of sentences; end it with a `---` rule.

## Quality Gates

Run before finishing any change:

```text
ruff check app tests
PYTHONPATH=app pytest -q
git diff --check
```

Several public surfaces are machine-checked against in-tree inventory docs.
When intentionally changing one of these surfaces, regenerate its doc with the
matching test's `--write` mode instead of hand-editing:

- routes/aliases: `tests/test_route_inventory.py` (in-code list, no companion doc)
- env variables: `tests/test_env_inventory.py` → `docs/ENV_INVENTORY.md`
- playback transition writers: `tests/test_transition_inventory.py` → `docs/TRANSITION_INVENTORY.md`
- Jellyfin route surface: `tests/test_jellyfin_inventory.py` → `docs/JELLYFIN_INVENTORY.md`
- runtime profile matrix: `tests/test_runtime_matrix.py` → `docs/OPERATIONS_TEST_MATRIX.md`

## Security Constraints

`RELAYTV_API_TOKEN` is an operator secret and stays env-only: plumbed through
the runtime config settings bus, never persisted to `settings.json`, never
returned by `/settings`, never logged. With the token unset, API behavior must
not change (local-first default).

## Architecture Roadmap (complete)

The 2026-06/07 architecture review and its six-phase refactor roadmap are
done — all phases squash-merged to `main` as `refactor:` PRs
[#21](https://github.com/mcgeezy/relaytv/pull/21) through
[#26](https://github.com/mcgeezy/relaytv/pull/26) (2026-07-03/04):

1. routes/static-UI split (#21)
2. runtime config service (#22)
3. playback transition service (#23)
4. Jellyfin product service (#24)
5. optional API token (#25)
6. operations test matrix (#26)

`docs/ARCHITECTURE.md` holds the current-state boundaries and the remaining
open follow-ups; the full review and per-phase milestone logs live in git
history (removed from the docs tree 2026-07-17). The former no-release hold
is lifted; normal release flow applies. The `codex/architecture-phase-*`
branch discipline no longer applies — work from `main` with normal
feature/fix branches.

Boundaries the roadmap established, to preserve in new work:

- `playback_service.py` is the writer of playback transition state
  (`NOW_PLAYING`, session state, queue advancement, auto-next suppression,
  close/resume); `player.py` is the process/control adapter.
- `config.py` owns env parsing and the settings bus; runtime `os.environ`
  writes stay behind its explicit subprocess-mirroring boundary.
- `integrations/jellyfin_service.py` owns Jellyfin product behavior;
  `jellyfin_receiver.py` stays transport/session/catalog and never imports
  the routes package.
- Route modules own public endpoint registration; do not remove endpoint
  aliases without a migration path for companion apps and Home Assistant.
