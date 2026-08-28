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

Run before finishing any change. The JavaScript gates are not optional — CI
runs them, and a `.js` change that only passes the Python gates will fail
there:

```text
ruff check app tests
PYTHONPATH=app pytest -q
git diff --check
node --check app/relaytv_app/static/ui/app.js
node --check app/relaytv_app/static/ui/realtime_transport.js
PYTHONPATH=app python3 -c "import re; from relaytv_app.routes import x11_overlay_page; print(re.findall(r'<script>(.*?)</script>', x11_overlay_page().body.decode(), re.S)[0])" | node --check -
node --test tests/js/*.test.js
```

CI installs with `pip install -e '.[dev]'`, which honors the pins in
`pyproject.toml`. A system Python may resolve different versions, so a green
local run is not proof of a green CI run — and the difference is not always
subtle. FastAPI is the one that has bitten: versions before 0.129 flatten
included routers into `app.routes`, later ones keep them as lazy entries, so a
test that walks routes can find everything locally and nothing on CI. When a
test inspects framework internals, make it assert that it *found* something,
or it will pass by finding nothing.

Several public surfaces are machine-checked against in-tree inventory docs.
When intentionally changing one of these surfaces, regenerate its doc with the
matching test's `--write` mode instead of hand-editing:

- routes/aliases: `tests/test_route_inventory.py` (in-code list, no companion doc)
- env variables: `tests/test_env_inventory.py` → `docs/ENV_INVENTORY.md`
- playback transition writers: `tests/test_transition_inventory.py` → `docs/TRANSITION_INVENTORY.md`
- Jellyfin route surface: `tests/test_jellyfin_inventory.py` → `docs/JELLYFIN_INVENTORY.md`
- runtime profile matrix: `tests/test_runtime_matrix.py` → `docs/OPERATIONS_TEST_MATRIX.md`

## Test Discipline

Two rules, both learned by shipping code that broke without a test noticing:

- **Revert proof.** For every concurrency, lifecycle, or boundary fix, revert
  the production guard alone and confirm the new test *fails*. A test that
  passes against the audited behavior has pinned nothing.
- **A test that cannot fail is not coverage.** Before trusting a new test, ask
  what it would take for it to fail. A responsiveness test that blocks an
  `asyncio.Event` and asserts the loop still ticks proves only that asyncio
  yields; it passes against the bug it was written for. Reach for the real
  path — a real request through an ASGI transport, a real thread blocked on a
  real `time.sleep` — when the cheap version would pass either way.

Race and lifecycle tests should drive the actual interleaving rather than
simulate it: block the slow half on an event, wait until it is genuinely in
flight, issue the competing operation, then release.

## Device Testing

The published image copies in only `app/relaytv_app` and installs every
dependency system-wide; the project itself is never installed as a package. So
a branch runs inside the *released* image by bind-mounting the checkout over
`/app/relaytv_app` — seconds, instead of the ten minutes a rebuild takes:

```yaml
# docker-compose.branchtest.yml — local only, keep it untracked
services:
  relaytv:
    volumes:
      - type: bind
        source: /opt/dev/relaytv/app/relaytv_app
        target: /app/relaytv_app
        read_only: true
```

```text
docker rm -f relaytv   # the running container is usually not owned by this
                       # compose project, so `compose down` finds nothing and
                       # `up` then fails on the container-name conflict
docker compose -f docker-compose.yml -f docker-compose.override.yml \
               -f docker-compose.branchtest.yml up -d
```

Four things to know before touching a device:

- **`docker-compose.override.yml` is per-device.** `scripts/install.sh`
  generates it from the hardware that host actually exposes, so they differ
  between devices — a Pi's carries `/dev/video10-13`, `/dev/cec0`, and
  `.Xauthority` an x86_64 host does not. Never copy one device's override to
  another. When rsyncing a branch between devices, exclude
  `docker-compose.override.yml`, `.env`, `data/`, and `bin/`, and back up the
  first three first.
- **`RELAYTV_MODE=headless` implies Xvfb**, which the published image is not
  built with (`RELAYTV_INSTALL_HEADLESS=0`). Leave the mode alone unless the
  image was built for it.
- **`docker exec … python3 -c` starts a fresh process.** It reads empty module
  state, not the running server's, so discovery, cache, and session state read
  as zero. Read runtime state through the HTTP API; `docker exec` is only good
  for checking which *code* is loaded.
- **The suite does not cover the process lifecycle.** `start_mpv` calls
  `stop_mpv` to clear the previous process, and only a *seamless replace*
  skips that path — so a defect in cold start can sit behind a fully green
  suite. Play something on a device before claiming a playback change works.

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
  `jellyfin_receiver.py` and `jellyfin_ws.py` stay transport and never import
  the routes package — the control socket reaches playback through the command
  sink the routes package registers, not by importing it.
- Route modules own public endpoint registration; do not remove endpoint
  aliases without a migration path for companion apps and Home Assistant.
