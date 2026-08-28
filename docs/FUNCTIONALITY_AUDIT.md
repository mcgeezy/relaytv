# Functionality Audit and Remediation Roadmap

Working engineering document for the application-wide functionality review.
This is not an operator runbook. Keep it current while remediation is active,
then remove it after completed work and any genuinely open follow-ups have been
folded into `ARCHITECTURE.md`.

## Audit Baseline

- Audit branch: `audit/functionality-review`
- Reviewed base: `1c4b3d0` (`origin/main`, RelayTV `v0.10.0`)
- Review date: 2026-08-27
- Scope: Python application and browser UI functions/helpers, public route
  behavior, state persistence, playback ordering, background-worker lifecycle,
  integration boundaries, and realtime delivery
- Inventory: 38,612 Python lines, 1,587 functions including nested helpers,
  and 79 classes
- Baseline verification: `ruff check app tests`, `PYTHONPATH=app pytest -q`,
  `git diff --check`, JavaScript syntax checks, and the JavaScript test suite
- Baseline result: 743 Python tests and 11 JavaScript tests passed

The passing baseline is important but does not close the findings below. Most
are concurrency, invalid-input, failure-publication, or lifecycle windows that
the current suite does not exercise.

## Required Design Principles

Remediation must preserve the architecture boundaries in `ARCHITECTURE.md` and
the repository instructions:

- `playback_service.py` owns playback transition policy; `player.py` remains
  the process/control adapter.
- `state.py` owns persisted queue, history, session, and settings state.
- Transport layers do not publish product state without proving that they still
  own the live generation.
- `RELAYTV_API_TOKEN` remains env-only, secret, and behavior-neutral when
  unset.
- Compatibility routes remain available until companion applications have a
  documented migration path.
- Slow network and disk work must not hold the application event loop or shared
  state locks.
- A regression test must fail against the reverted production guard for every
  concurrency or lifecycle fix.

## Findings

| ID | Priority | Area | Finding | User/operator impact | Status |
| --- | --- | --- | --- | --- | --- |
| F01 | High | Playback | A resolver started by an older Play can finish after Stop or a newer Play and still load media and publish session state. | Playback can restart after Stop or select the wrong item. | In review (#74) |
| F02 | High | API auth | `GET /share` starts playback and clears the queue, and `GET /snapshot` commands mpv, while the token middleware exempts all GET requests. | Token-enabled installations retain unauthenticated control paths. | In review (#68) |
| F03 | High | URL/public state | Input validation accepts malformed host/port forms that later make public URL serialization raise. | One poisoned queue item can repeatedly break queue/status/realtime snapshots. | In review (#70) |
| F04 | High | Persistence | Settings, queue, and history snapshots can be written out of order; session fields do not share one mutation/publish lock. | A successful newer mutation can disappear after restart, and persisted session fields can be incoherent. | In review (#73) |
| F05 | High | mDNS | Advertisement startup can self-deadlock, late registration can publish after Stop, failed startup can leak Zeroconf (the `except` closes the global, still `None`, not the local it built — found while fixing, not in the original review), and browse generations reuse a stop event. | Discovery can hang, leak resources, or revive retired workers. | In review (#72) |
| F06 | Medium-high | IPTV | The refresh worker reuses a module stop event and Stop neither joins nor retires the active generation. | A refresh can survive shutdown and run concurrently with its replacement. | In review (#72) |
| F07 | Medium-high | Browser UI | The shared control helper treats any completed HTTP response as success without checking `response.ok`. | Rejected Play/Pause, Next, Close, seek, volume, and queue commands appear to succeed. | In review (#69) |
| F08 | Medium-high | Uploads | Async upload endpoints perform synchronous writes, cleanup scans, JSON persistence, and repeated `fsync()` calls. | Large uploads can stall HTTP controls and realtime connections. | In review (#75) |
| F09 | Product gap | Jellyfin | Shared/API-key cast-target settings are absent from the release, and the first implementation implicitly combines API-key control with login-session catalog access. | Operators cannot configure the intended all-user target or tell which stored identity is active. | In review (#77) |
| F10 | Medium | Thumbnail/cache | Thumbnail jobs are unbounded and not deduplicated; failed images can be queued repeatedly. The yt-dlp metadata cache is also unbounded and unlocked. | Long-running processes can accumulate memory/work and repeatedly hit failing providers. | In review (#76) |
| F11 | Medium | Snapshot | Snapshot creation ignores the mpv result and returns before the image is known to exist. | Clients receive `ok: true` followed by an immediate 404 or a permanently missing image. | In review (#69) |
| F12 | Medium | Post-live relay | Concurrent session creation can publish more than one supposedly single-player relay pipeline. | Duplicate yt-dlp/ffmpeg pipelines consume CPU, disk, and network until reaped. | In review (#72 / #74) |
| F13 | Medium | Upload boundary | Upload IDs are joined to the upload root without canonical format or containment validation after URL decoding. | Crafted legacy/public paths can address metadata/session paths outside the intended upload root when matching files exist. | In review (#71) |
| F14 | Medium | Provider matching | Provider classification uses netloc substring checks rather than hostname label boundaries. | Lookalike domains can select the wrong resolver strategy. | In review (#70) |

The realtime WebSocket/SSE hub and current Jellyfin socket-generation ownership
were also reviewed. No new blocking defect was found in those implementations.

## Acceptance Criteria by Finding

### F01 — Playback intent ownership

- Reserve a monotonically increasing playback-intent generation before any
  resolver, IPTV lookup, post-live preparation, or availability wait.
- A newer Play, Stop, Close, resume-clear, or other terminal transition retires
  older unresolved intents.
- Check ownership before every irreversible effect: mpv load/start, post-live
  relay publication, history insertion, `NOW_PLAYING`, session state, and
  watchdog arming.
- Retired work cleans up any private relay/process resources it prepared.
- Do not serialize the complete resolver call behind `MPV_LOCK`; a newer intent
  must be able to supersede a slow older one.
- Regression tests block a resolver, issue Stop or a second Play, release the
  resolver, and prove that only the newest intent can load or publish.

### F02 — Mutating GET authentication

- Keep local-first behavior unchanged when `RELAYTV_API_TOKEN` is unset.
- When configured, protect the compatibility `GET /share` and `GET /snapshot`
  paths with the same constant-time bearer comparison as other writes.
- Prefer POST in browser, Android, and Home Assistant callers while retaining
  GET only as an authenticated compatibility alias.
- Extend the route/auth inventory so a new mutating GET cannot be introduced
  without an explicit classification and test.

### F03 and F14 — Canonical URL boundary

- Create one hostname/port parser used by input validation, public
  serialization, and provider classification.
- Reject missing/invalid hostnames, credentials where not supported, invalid
  ports, and ambiguous malformed netlocs at ingestion.
- Match providers by exact hostname or a dot-delimited subdomain boundary.
- Public serialization must return a safe omission/fallback for malformed
  legacy persisted values and must never raise.
- Test poisoned queue, history, now-playing, status, and realtime snapshots as
  well as lookalike provider domains.

### F04 — Ordered durable state publication

- Give settings, queue, history, and session independent persistence
  coordinators so an older snapshot cannot replace a newer one.
- Use a single session lock and mutation function for composite session fields;
  avoid a sequence of setters that persists intermediate combinations.
- Do not hold state locks across filesystem I/O. Use versions or a serialized
  publisher that rechecks the latest version before committing.
- Make persistence failure observable to user-facing writes and runtime health;
  the atomic writer must not silently turn disk failure into API success.
- Tests deterministically reverse write completion order and verify both memory
  and disk contain the latest complete state.

### F05, F06, and F12 — Generation-owned lifecycle

- Each mDNS advertisement, mDNS browser, IPTV worker, and post-live reservation
  owns its own stop event and generation identity.
- Start/stop decision and publication are serialized, while blocking joins and
  network cleanup remain bounded.
- A retired generation cannot publish globals or consume globals belonging to
  its replacement.
- Close locally constructed resources on partial startup failure.
- Tests hold startup/refresh/session creation across Stop and replacement Start,
  then prove only one live generation or relay pipeline remains.

### F07 and F11 — Honest browser/control acknowledgements

- The shared browser request helper checks HTTP status, parses a safe error
  detail, returns/throws a useful result, and distinguishes command rejection
  from connectivity loss.
- Controls expose failure without applying a false success state.
- Snapshot creation checks mpv's command response and waits for a non-empty
  output file with a short bounded timeout before returning success.
- Add JavaScript tests for 401, 409, 500, timeout, and successful retry, plus
  endpoint tests for command failure and delayed screenshot creation.

### F08 — Non-blocking upload pipeline

- Move blocking file writes, `fsync`, metadata/session writes, and cleanup scans
  off the event loop with bounded buffering/backpressure.
- Avoid `fsync` and session-file replacement for every 1 MiB chunk; batch
  durable progress while retaining safe restart behavior.
- Preserve size limits, progressive playback thresholds, cleanup, cancellation,
  and partial-file removal.
- A responsiveness test blocks the disk worker while proving `/health` and a
  realtime subscriber continue to make progress.

### F09 — Jellyfin shared cast target

- Recover the existing implementation onto a fresh branch based on current
  `main`; do not merge it mechanically if interfaces have moved.
- Present an explicit settings choice between shared/admin API-key identity and
  caller-specific login identity.
- Keep secrets redacted and expose only configured-state booleans.
- Verify registration, catalog, metadata, rename-stable device identity,
  disconnect/reconnect, and multi-user visibility.
- Deliver this independently so lifecycle and persistence fixes do not become a
  prerequisite for the product feature.

### F10 and F13 — Bounded resource/input helpers

- Bound thumbnail work, deduplicate queued/in-flight IDs, and apply negative
  backoff after failures.
- Add lock-protected size/TTL eviction and in-flight coordination to yt-dlp
  metadata caching.
- Enforce the generated upload-ID format and common-path containment after URL
  decoding at the store boundary.
- Test queue saturation, duplicate/failing thumbnails, cache eviction, encoded
  separators, traversal, and valid legacy media references.

## Finding Verification

Every finding above was re-verified against the branch working tree before
planning. The baseline reproduces: `ruff check app tests` clean, 743 Python
tests passing.

| ID | Verified by | Evidence |
| --- | --- | --- |
| F01 | Code read | `player.play_item` resolves outside `MPV_LOCK` (`player.py:5019-5100`) and takes the lock only at `player.py:5119`. No intent identity exists anywhere in the module. |
| F02 | Code read | `api_auth.write_request_allowed` returns `True` for any non-write method; `GET /share` (`routes/playback.py:509`) clears the queue and plays, `GET /snapshot` (`routes/snapshots.py:26`) commands mpv. |
| F03 | Executed | `validate_user_url("http://host:99999/a")` is accepted; `public_media.sanitize_public_url` on the same value raises `ValueError: Port out of range 0-65535`. Also `http://host:abc/a`. |
| F04 | Code read | `_atomic_write_json` (`state.py:174`) converts every write failure into a logged warning; `set_session_state`/`set_pause_reason`/`set_session_position`/`set_now_playing` each mutate an unguarded global and persist the whole composite payload. |
| F05 | Code read | `start()` has no generation identity, so an in-flight `start_async` publishes after `stop()`. The `except` branch closes the **global** `_ZEROCONF` (still `None` at that point) rather than the local `zc`, so a failed `register_service` leaks an open Zeroconf. `_BROWSE_STOP` is module-level and shared across generations. |
| F06 | Code read | `_WORKER_STOP` is module-level; `stop_worker` sets it without joining or retiring, and `start_worker` clears the same event. |
| F07 | Code read | `post()` (`static/ui/app.js:69-95`) sets `ok = true` whenever the fetch settles, never reading `response.ok` or `status`. |
| F08 | Code read | `ingest_media_play` runs `os.fsync` plus a `write_session` JSON write per 1 MiB chunk inside `async def`, on the event loop (`routes/uploads.py:262-312`). |
| F09 | Branch inspection | A complete 7-commit implementation exists on the unmerged local branch `fix/jellyfin-shared-cast-target` (15 files, +931/-64), including settings, UI, docs, and tests. |
| F10 | Code read | `_Q = queue.Queue()` is unbounded with no in-flight de-duplication, and a failed download `continue`s with no negative caching. |
| F11 | Code read | `snapshot()` discards `player.mpv_command(...)` and returns `ok: True` with an `image_url` before any file exists. |
| F12 | Code read | `create_session` spawns the pipeline, *then* reads `_SESSIONS` to supersede. Two concurrent callers both observe an empty map and both publish. |
| F13 | Executed | `upload_ref_from_url("http://h/media/uploads/..%2F..%2Fdata/x.mp4")` returns `('../../data', 'x.mp4')`; `metadata_path` on that id yields `/data/uploads/../../data/meta.json`. `unquote` is applied after the path split, and only `filename` is `basename`d. |
| F14 | Executed | `provider_from_url("https://evil-youtu.com/x") == "youtube"`, `provider_from_url("https://rumble.com.evil.net/x") == "rumble"`, and `is_youtube_url("https://youtube.com.phish.co/v") is True`. |

### One correction to F02's stated impact

F02 is recorded as affecting only token-enabled installations. It is worse than
that. There is no CORS middleware and no `Origin`, `Referer`, or `Sec-Fetch-Site`
check anywhere in the application, so on a **default, tokenless** install any web
page the operator visits can issue `<img src="http://relaytv.local:8787/share?url=...">`
and take over the television. The browser blocks the *response*, not the request,
and the side effect has already happened. Adding a token does not close this;
only removing the side effect from the GET does.

That changes the fix. Authenticating `GET /share` would leave the default install
exposed and would simultaneously break the PWA share target, which is declared
`{"action": "/share", "method": "GET"}` in the web app manifest
(`routes/assets.py:365`) and is a browser navigation that cannot carry an
`Authorization` header. The plan below makes `GET /share` non-mutating instead.

## Implementation Roadmap

### Sequencing rationale

This sequence differs from a straight reading of the findings table in four
deliberate ways:

1. **F02 ships first and alone.** It is the only finding that is live on a
   default install, and the fix is small and self-contained. Bundling it with the
   URL-parser consolidation would delay a security fix behind a refactor.
2. **F04 precedes F01.** F01's acceptance criteria require an ownership check
   before every session publish. F04 consolidates those scattered setters into a
   single guarded mutation. Doing F04 first means F01 adds one check at one
   chokepoint instead of three that F04 then rewrites.
3. **F09 runs in parallel from day one.** The implementation already exists on a
   branch; it should not queue behind seven unrelated fixes.
4. **There is no standalone test milestone.** The audit's own M0 note concedes
   tests should travel with their fix. The requirement is restated as a gate
   below rather than scheduled as a release.

Every PR below branches from fresh `main`, uses a Conventional Commit title, and
updates the Milestone Log.

### Gates applied to every PR

Non-negotiable, in addition to the per-PR steps:

```
ruff check app tests
PYTHONPATH=app pytest -q
git diff --check
node --check app/relaytv_app/static/ui/app.js
node --test tests/js/*.test.js
```

Plus the two standing repository rules:

- **Revert proof.** For every concurrency, lifecycle, or boundary fix, revert the
  production guard alone and confirm the new test *fails*. A test that passes
  against the audited behavior has not pinned anything.
- **Inventories are generated, never hand-edited.** Where a PR adds routes or env
  vars, regenerate with `--write` (`tests/test_route_inventory.py`,
  `tests/test_env_inventory.py`, `tests/test_transition_inventory.py`,
  `tests/test_jellyfin_inventory.py`, `tests/test_runtime_matrix.py`).

### PR 1 — `fix: remove unauthenticated control side effects from GET`

Findings F02. **Ship first.**

- Redirect `GET /share` (302) to `/ui?share=<encoded>`; the UI performs the
  authenticated `POST /share`. The GET keeps working as a PWA share target with
  or without a token, and stops being a control path.
- Add an explicit mutating-GET classification to `api_auth.py` and consult it in
  the middleware. `GET /snapshot` stays a working compatibility alias for
  `relaytv-ha` but is classified as a write, so it is guarded when a token is set
  and unchanged when one is not.
- Extend `test_route_inventory.py` so every route carries a read/write
  classification and a new unclassified mutating GET fails the build.

Test steps:

1. `GET /snapshot` returns 401 with a token configured and no header, 200 with
   the correct bearer, and 200 with no token configured (local-first unchanged).
2. `GET /share?url=…` returns 302 and starts no playback; the queue is untouched.
3. Constant-time comparison is still used; wrong-length tokens do not short-circuit.
4. Route inventory rejects an added mutating GET that is not classified.
5. **Revert proof:** drop the classification entry, confirm the auth test fails.
6. **Device:** on Living Room, install `/ui` as a PWA and share a YouTube link
   to it, confirming the redirect reaches the confirm step and plays. Confirm the
   Home Assistant snapshot action still returns an image, and that the native
   Android app's share sheet still works (it POSTs to `/smart`, so it exercises a
   different path and should be unaffected).

### PR 2 — `fix: report control and snapshot failures honestly`

Findings F07, F11.

- `post()` checks `response.ok`, parses a safe `detail`, and distinguishes a
  rejected command (HTTP status) from a lost connection (network error). Only the
  latter keeps the existing idempotent-retry path.
- `snapshot()` checks the mpv command result and waits for a non-empty file with
  a short bounded timeout before returning success.

Test steps:

1. JS: 401, 409, 500, network timeout, and successful retry — each asserts the
   surfaced state, and that a rejected command does **not** apply optimistic UI.
2. Endpoint: mpv command failure returns non-200; a delayed file returns success
   only once the image exists; a never-appearing file times out rather than
   returning `ok: true`.
3. **Revert proof:** restore the unconditional `ok = true`, confirm the JS tests fail.
4. **Device:** set a token, clear it in the browser, and confirm Play/Pause, Next,
   seek, and volume all show a visible failure rather than silently reverting on
   the next poll.

### PR 3 — `fix: canonicalize URL host parsing`

Findings F03, F14.

- One `parse_host_port()` helper used by `validate_user_url`,
  `sanitize_public_url`, `provider_from_url`, and `is_youtube_url`.
- Reject invalid ports and empty/malformed hosts at ingestion; match providers on
  an exact hostname or a dot-delimited suffix boundary.
- `sanitize_public_url` must return a safe fallback for already-persisted
  malformed values and must never raise.

Test steps:

1. Ingestion rejects `http://host:99999/a`, `http://host:abc/a`, `http://:80/a`,
   and `http://user@:9/a`.
2. A queue, history, and now-playing entry each pre-poisoned with a malformed URL
   still serializes — `/queue`, `/status`, `/history`, and a realtime snapshot all
   return 200. This is the migration case: bad values are already on disk.
3. `evil-youtu.com`, `rumble.com.evil.net`, `nottwitch.tv.attacker.io`, and
   `youtube.com.phish.co` all classify as `other`, while `youtu.be`,
   `www.youtube.com`, `m.youtube.com`, and real provider hosts are unchanged.
4. **Revert proof:** restore the substring match, confirm the lookalike test fails.
5. **Device:** play one item per provider (YouTube, Rumble, Twitch, Odysee, an
   IPTV channel, and an upload) and confirm resolution is unchanged. This is the
   PR most likely to regress a working provider.

### PR 4 — `fix: contain upload identifiers`

Findings F13.

- Enforce the generated `u_[0-9a-f]{20}` upload-ID format at the store boundary,
  and verify `os.path.commonpath` containment after decoding.

Test steps:

1. `..%2F..%2Fdata`, `../../etc`, and an encoded separator inside an ID are all
   rejected before any filesystem access.
2. A valid legacy media reference still resolves — check a real `meta.json` from
   a device before assuming the format matches.
3. **Revert proof:** remove the containment check, confirm the traversal test fails.
4. **Device:** upload a file, play it, and confirm an existing pre-upgrade upload
   still plays from history.

### PR 5 — `fix: own background service generations`

Findings F05, F06, F12.

Grouped because all three take the identical shape, and `jellyfin_ws.py` already
carries a reviewed implementation of that pattern to copy: a per-generation stop
event, a lifecycle lock, and a publish that re-checks ownership before writing a
global.

- mDNS advertisement, mDNS browse, and the IPTV refresh worker each own a stop
  event and generation identity. Fix the `except` branch that closes the global
  instead of the local `zc`.
- `postlive_relay.create_session` reserves the single-player slot before spawning,
  preserving the deliberate spawn-before-supersede ordering that restart-in-place
  depends on — the comment at `postlive_relay.py:276` explains why, and it must
  survive the change.

Test steps:

1. Hold startup across a Stop and a replacement Start; assert the retired
   generation publishes nothing and exactly one live generation remains.
2. A `register_service` failure closes the Zeroconf it constructed (assert on a
   fake with a `closed` flag).
3. `stop_worker` then `start_worker` while a refresh is mid-flight leaves one
   worker, and the retired one does not resume.
4. Two concurrent `create_session` calls yield exactly one live relay; the loser's
   processes are reaped.
5. **Revert proof:** restore the module-level stop event, confirm the
   restart-during-refresh test fails.
6. **Device:** restart the container on both devices and confirm each still
   appears in the other's device list within 30s. Then play a post-live YouTube
   item and confirm exactly one ffmpeg process (`docker exec relaytv ps`).

### PR 6 — `fix: order durable state publication`

Findings F04.

- Independent persistence coordinators for settings, queue, history, and session,
  each version-checked so an older snapshot cannot replace a newer one.
- One session lock and one composite mutation function; no sequence of setters
  that persists intermediate combinations.
- No state lock held across filesystem I/O.
- Write failure becomes observable rather than a logged warning behind a 200.

Test steps:

1. Deterministically reverse write completion order (two mutations, release the
   older writer last) and assert both memory and disk hold the newer state.
2. A composite session change persists once, not once per field, and no
   intermediate combination is ever written.
3. A failing `os.replace` surfaces to the caller and to runtime health rather
   than returning success.
4. Lock-held-across-IO assertion: the persistence path does not hold `QUEUE_LOCK`
   or the session lock while writing.
5. **Revert proof:** restore the unversioned write, confirm the reordering test fails.
6. **Device:** change a setting, immediately restart the container, and confirm
   the change survived. Repeat with a queue mutation.

### PR 7 — `fix: enforce latest playback intent`

Findings F01, and the playback half of F12.

The highest-risk change in the audit; it lands after PR 6 so the ownership check
attaches to one consolidated publish point.

- Reserve a monotonic intent generation before any resolver, IPTV lookup,
  post-live preparation, or availability wait.
- Any terminal transition (newer Play, Stop, Close, resume-clear) retires older
  unresolved intents.
- Check ownership before every irreversible effect: mpv load, relay publication,
  history insertion, `NOW_PLAYING`, session state, watchdog arming.
- Retired work releases the relay and process resources it prepared.
- Do **not** widen `MPV_LOCK` to cover the resolver; a newer intent must be able
  to supersede a slow older one.

Test steps:

1. Block a resolver, issue Stop, release it: nothing loads, nothing publishes.
2. Block resolver A, issue Play B, release A: only B loads and publishes.
3. A retired intent that had prepared a relay session closes it.
4. Assert `MPV_LOCK` is not held across `resolve_streams` — the fix must not
   serialize playback behind slow resolution.
5. **Revert proof:** remove the ownership check, confirm the Stop-during-resolve
   test fails.
6. **Device soak, both devices:** rapid Play/Stop/Play on a slow-resolving item;
   confirm no resurrection after Stop and no wrong-item selection. Then leave a
   queue of ten items playing to completion and confirm ordering.

### PR 8 — `perf: keep upload ingestion off the event loop`

Findings F08.

- Move file writes, `fsync`, metadata/session writes, and cleanup scans to a
  worker with bounded buffering.
- Batch durable progress rather than syncing every 1 MiB, while keeping safe
  restart behavior.
- Preserve size limits, progressive thresholds, cleanup, cancellation, and
  partial-file removal.

Test steps:

1. Block the disk worker and assert `/health` and a realtime subscriber still
   make progress.
2. Backpressure: a producer faster than the disk does not grow memory without bound.
3. Size limit, cancellation mid-upload, and partial-file removal all still hold.
4. Progressive playback still starts at the same threshold.
5. **Device:** upload a large file (~2 GB) to Living Room and, during the upload,
   confirm the UI stays responsive and playback controls still work — the exact
   symptom this fixes.

### PR 9 — `fix: bound thumbnail and metadata caches`

Findings F10, plus the store integration for F13.

- Bound the thumbnail queue, de-duplicate queued and in-flight IDs, and apply
  negative backoff after failures.
- Lock-protected size/TTL eviction and in-flight coordination for the yt-dlp
  metadata cache.

Test steps:

1. Queue saturation drops or blocks per the chosen policy rather than growing.
2. The same ID queued twice while in flight is fetched once.
3. A permanently failing thumbnail is not retried on every status poll.
4. Cache eviction respects both size and TTL under concurrent access.
5. **Device:** load a 200-item history and confirm memory is stable across an
   hour and that failing thumbnails stop being re-requested.

### PR 10 — `feat: configure shared Jellyfin cast identity`

Findings F09. **Independent — start in parallel with PR 1.**

The rebased implementation is in #77 on `feat/jellyfin-shared-cast`. Its
Settings modal and receiver now share one persisted, explicit mode; legacy
API-key settings infer shared mode so upgrades do not silently lose the target.

- Explicit settings choice between shared/admin API-key identity and one
  operator-configured user-login identity.
- Control and catalog both use the selected identity; inactive stored
  credentials are retained for reversible switching but never used as fallback.
- In shared mode, carry Jellyfin's `ControllingUserId` through item detail,
  playback planning, queue metadata, and delayed resume hydration so an
  API-key target can resolve the initiating user's visible metadata without
  becoming a user-login target.
- Secrets stay redacted; expose only configured-state booleans.
- Treat public status assembly as the final media-URL trust boundary: signed
  URLs are scrubbed even if a failed lookup put one in a display field or
  native runtime telemetry rather than a normal URL field.
- Initiating-caller attribution for a shared cast remains a separate follow-up
  in `ARCHITECTURE.md`; user-login mode is not presented as per-caller identity.

Test steps:

1. The existing branch tests pass after rebase — re-run rather than assume.
2. `/settings` never returns the API key; only a `*_configured` boolean.
3. Confirm the machine-checked Jellyfin route and environment inventories are
   unchanged; the new mode is persisted settings state, not a new route or env
   surface.
4. **Device:** register against the real Jellyfin server, confirm RelayTV appears
   as a cast target for a **second** Jellyfin user, cast an item and verify title,
   artwork, pause, resume, stop, and progress reporting, rename the device and
   confirm identity is stable, then disconnect and reconnect.
5. Scan public status against the configured API key and verify neither display
   metadata nor native runtime telemetry contains the credential.

### PR 11 — `docs: close functionality audit`

- Fold any genuinely open follow-up into `ARCHITECTURE.md`, update operator docs
  only where behavior changed, and delete this file.
- The `ARCHITECTURE.md` follow-up list entry added on this branch is removed in
  the same PR.

Gate: full quality gates, a companion compatibility review, and a deployment soak
on both devices with no open finding lacking an `ARCHITECTURE.md` follow-up.

### Findings-to-PR map

| PR | Findings | Device verification required |
| --- | --- | --- |
| 1 | F02 | Yes — PWA share, HA snapshot |
| 2 | F07, F11 | Yes — control rejection is a UI behavior |
| 3 | F03, F14 | Yes — provider regression risk |
| 4 | F13 | Yes — legacy upload compatibility |
| 5 | F05, F06, F12 | Yes — discovery and relay are network-dependent |
| 6 | F04 | Yes — restart survival |
| 7 | F01, F12 (playback) | Yes — soak on both devices |
| 8 | F08 | Yes — large upload responsiveness |
| 9 | F10 | Yes — long-running memory |
| 10 | F09 | Yes — multi-user Jellyfin |
| 11 | — | Final soak |

## Testing a Branch on a Device

Not obvious, and it cost time to work out, so it is recorded here rather than
rediscovered.

The image copies in only `app/relaytv_app` and installs every dependency
system-wide; the project itself is never installed as a package. So a branch
can be run inside the *released* image by bind-mounting the checkout over
`/app/relaytv_app` — no rebuild, a few seconds instead of the ten minutes the
`docker-image` job takes:

```yaml
# docker-compose.branchtest.yml (gitignored, local only)
services:
  relaytv:
    volumes:
      - type: bind
        source: /opt/dev/relaytv/app/relaytv_app
        target: /app/relaytv_app
        read_only: true
```

```
docker rm -f relaytv   # the running container is not owned by this compose
                       # project, so `compose down` finds nothing and `up`
                       # then fails on the container-name conflict
docker compose -f docker-compose.yml -f docker-compose.override.yml \
               -f docker-compose.branchtest.yml up -d
```

Three things to know:

- **`docker-compose.override.yml` is per-device.** It is generated by
  `scripts/install.sh` from the hardware that host actually exposes — the Pi's
  carries `/dev/video10-13`, `/dev/cec0`, and `.Xauthority` that the NUC does
  not. Never copy one device's override to another, and exclude it (with
  `.env`, `data/`, and `bin/`) when rsyncing a branch between devices.
- **`RELAYTV_MODE=headless` implies Xvfb**, which the published image is not
  built with (`RELAYTV_INSTALL_HEADLESS=0`). Leave the mode alone.
- **`docker exec … python3 -c` starts a fresh process**, so it reads empty
  module state, not the running server's. Discovery, cache, and session state
  must be read through the HTTP API; `docker exec` is only good for checking
  which *code* is loaded.

## Cross-Repository Compatibility

Verified directly against the companion checkouts on 2026-08-28
(`/opt/dev/relaytv-android`, `/opt/dev/relaytv-ha`) rather than assumed.
**No companion change is required for any PR in this roadmap.**

### Mutating GET surface is exactly two routes

Enumerated from the live app: of 71 registered GET routes, only `GET /share`
and `GET /snapshot` reach a mutating helper. The PR 1 classification set is
therefore complete, and the inventory test added there is what keeps it that way.

### `relaytv-android` — unaffected

- Endpoint literals in `app/src/main`: `/health`, `/next`, `/pause`, `/play_now`,
  `/previous`, `/realtime/capabilities`, `/resume`, `/seek_abs`, `/smart`,
  `/stop`, `/ui`, `/ui/events`, `/ui/ws`, `/volume`. Neither `/share` nor
  `/snapshot` appears anywhere in the app.
- Android share intents are handled natively: `ShareActivity`/`ShareWorker` POST
  to `/smart` (default) or `/play_now` via `Net.postJson(...)`
  (`ShareWorker.kt:63`), never through the web share target.
- Every request carries `applyBearerToken(apiToken)` (`Net.kt:35-49`), so the
  app already satisfies a token-enabled server on all write paths.
- The app also injects `relaytv_api_token` into WebView `localStorage`
  (`MainActivity.kt:342`), so the embedded `/ui` uses the same authenticated
  POST path the browser UI does.

The PWA `share_target` that PR 1 changes is the *browser* install path, not the
native app. The redirect preserves it; the native app never touches it.

### `relaytv-ha` — unaffected

- `snapshot()` (`relaytv_api.py:294-298`) issues `POST /snapshot` first and falls
  back to `GET /snapshot` **only** on `RelayTVEndpointNotFound` (404/405). A 401
  raises `RelayTVAuthError` and does not fall back, so classifying the GET as a
  write cannot silently degrade the integration.
- `auth_headers` (`relaytv_api.py:108-112`) attaches the bearer token to every
  request including the GET fallback, so even the fallback path stays authorized.
- The integration's only other RelayTV path literals are `/ui/events` and
  `/ui/ws`; all control verbs are already POST.

Because HA leads with POST and authorizes the fallback, `GET /snapshot` could in
principle be retired. It is kept as an authenticated alias anyway, per the
repository rule against removing aliases without a documented migration path.

### Still worth re-checking at merge time

- Browser UI: keep the optional bearer-token prompt/retry behavior and test
  incorrect, cleared, and replaced tokens (PR 2 changes this code).
- Re-run this enumeration if a companion is updated between now and PR 1, since
  the conclusion depends on the companion source as it stands today.

No companion change is required for internal persistence, lifecycle, cache, or
playback-intent generation unless its public response contract changes.

## Milestone Log

| Date | Milestone | Commit/PR | Result |
| --- | --- | --- | --- |
| 2026-08-27 | Audit baseline | `audit/functionality-review` at `1c4b3d0` | Findings recorded; implementation not started. |
| 2026-08-27 | Findings verified, roadmap sequenced | `audit/functionality-review` | All 14 findings reproduced against the working tree; F02 impact widened to tokenless installs; PR sequence set. |
| 2026-08-28 | Companion compatibility confirmed | `relaytv-android`, `relaytv-ha` working copies | Neither companion calls `GET /share` or `GET /snapshot`; both already POST with a bearer token. PR 1 needs no companion change. |
| 2026-08-28 | PRs 1-10 implemented and opened | #68, #69, #70, #71, #72, #73, #74, #75, #76, #77 | Every finding has a fix in review, each with a revert proof confirmed to fail against the reverted guard. CI green on all. PR 11 remains blocked on these merging. |
| 2026-08-28 | Fleet verification on a combined branch | `test/audit-combined` on Living Room (x86_64) and Mark's Room (aarch64) | Found and fixed a regression in #74 that the 752-test suite missed: retiring intents inside `player.stop_mpv` made every cold start supersede itself, because `start_mpv` calls `stop_mpv` to clear the previous process. Only a seamless replace survived. Retirement moved to the `playback_service` terminal transitions; two tests added, both confirmed to fail against the reverted fix. Cross-device discovery, mDNS restart cycles, and the Jellyfin socket all verified. Per-PR results and remaining checks are recorded in each PR. |
| 2026-08-28 | Review findings remediated and integration stack rebuilt | #68, #72, #73, #74, #76, #77; `test/audit-combined` at `bb6ecbb` | Closed the cross-site snapshot, lifecycle deadlock/leak, stale publication, late playback side-effect, cache publication, and implicit Jellyfin identity gaps with revert-proven tests. The pushed combined branch includes open runtime PRs #66 and #68-#77 plus the #78 test instructions; 948 Python and 24 JavaScript tests pass with all syntax, lint, inventory, and diff gates clean. |
| 2026-08-28 | Shared Jellyfin second-user soak completed | #77 at `43f0e1a`; `test/audit-combined` at `f5dab00` | A real cast from Gavin to the userless shared target found two preview defects: metadata requests lacked caller context, and a failed lookup could expose a signed stream URL through public title/runtime telemetry. Four revert-proven regressions now cover command-user propagation, user-scoped metadata lookup, delayed hydration, and final status redaction. Live title/artwork, pause, resume, stop, 36 successful progress reports, and one successful stopped report passed with zero failures or dropped commands; Jellyfin logged no new empty-Guid exception. The combined stack passes 951 Python and 24 JavaScript tests. |

Update this log only at completed milestones. Detailed investigation and soak
logs belong in PR descriptions and git history rather than growing this file
indefinitely.
