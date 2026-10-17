# YouTube playlist queue verification

Initial web import milestone verified on 2026-10-17 on the development host, branch
`feat/youtube-playlist-queue`. Application milestones: `f2e1d74`, `03a378c`,
and `73a29ad`.

## Automated checks

- Project-compatible environment created with `uv venv .venv-ci` and
  `uv pip install --python .venv-ci/bin/python -e '.[dev]'` (Python 3.13,
  FastAPI 0.143.3).
- All 1,438 Python tests and 71 JavaScript tests passed.
- Ruff, `git diff --check`, the required app/realtime/embedded-overlay syntax
  checks, and syntax checks for the new playlist script and browser smoke passed.
- Revert proofs independently removed the extraction commit guard, YouTube host
  boundary, playlist-size boundary, browser embedded-playlist boundary, and API
  embedded-playlist boundary. The relevant tests failed in every case; restoring
  the guards restored passing results.
- Concurrency tests block the actual extraction subprocess boundary in a real
  worker thread, issue competing HTTP requests, then release it. They cover
  immediate `202`, unrelated HTTP responsiveness, queue edits during extraction,
  duplicate retries, cancellation, retired generations, and retained worker slots.

## Browser checks

Run with Playwright available on the Node module path:

```sh
node scripts/playlist-ui-smoke.js --base=http://127.0.0.1:8787
```

Passed in Chromium at desktop dark (1440 × 1000) and mobile light (390 × 844),
both on an isolated HTTP server and on the deployed host. The checked-in smoke
controls playlist API responses so it can safely check duplicate taps, reload
recovery, lost-response recovery, cancellation, playlist/video choices, pasted
`www` links, embedded playlists, dynamic mixes, lookalike domains, and modal
bounds without modifying a live queue.

A separate real browser-to-server run on the host used the entire-playlist
button with the public JODA15 playlist. It added all four videos, reported
completion, preserved the current playing URL, and produced no page errors.
Only the test-created queue entries were removed afterward.

## Device checks

The released `ghcr.io/mcgeezy/relaytv:latest` image was recreated using the
existing local compose override and read-only branch bind mount at
`/app/relaytv_app`; no image rebuild or device override replacement was needed.
Queue/session/config backups were saved locally before deployment. The existing
local compose healthcheck change remains outside the feature commits.

The real API accepted a playlist import in **0.003 seconds**, returned `202`,
and added all four videos from
`https://www.youtube.com/playlist?list=PL6IaIsEjSbf96XFRuNccS_RuEXwNdsoEu`:

| Position | Video ID | Title |
| --- | --- | --- |
| 1 | `yN-Q-tFFElc` | JOD |
| 2 | `aVjGozRKnS0` | JOD2 |
| 3 | `BdvQkoF6Jq4` | JOD3 |
| 4 | `DpNKHkMbULM` | JOD4 |

Disk persistence matched that order. Retrying the same request ID with a
video/list/index URL returned the completed job and added zero further entries.
Original queue IDs remained unchanged.

Queue Play loaded the first two imported videos on the actual device, including
loading from a closed/idle player. Qt/mpv runtime telemetry confirmed advancing
playback rather than only successful URL resolution:

| Video | Position before | Position after | Telemetry |
| --- | --- | --- | --- |
| JOD | 0.44 s | 1.96 s | `qt_runtime` |
| JOD2 | 0.08 s | 1.64 s | `qt_runtime` |

The original playing session was resumed at its saved position; the original
queue and stable IDs were preserved, and test entries were removed. After the
final link-variant fix, the container was recreated again, embedded playlist
rejection was checked through all four single-video enqueue aliases, and the
original queue/session were restored again. The deployed container is healthy.

## Automatic server share extension

Implemented in `9ef3f1a` (with review hardening in `f129055` and `ae2d6fe`),
also verified on the development host on 2026-10-17. This extension replaces the
initial milestone's playlist-only rejection on the enqueue aliases with automatic
background import. `/smart` and `/play_now` also recognize playlist-only and
video/list links sent by existing phone builds.

- **1,507 Python tests and 72 JavaScript tests passed**, with all required Ruff,
  whitespace, JavaScript, and embedded-overlay syntax gates. The final focused
  HTTP UI/import run passed 110 tests (`test_youtube_playlist.py`).
- Additional revert proofs covered reserved playback intent, ownership at queue
  commit, conservative failure rollback, shutdown during first-video resolution,
  pending-import Clear, the player's load boundary, fresh completion counts,
  superseded `preserve_current` capture, `RD…` watch-URL fallback in `auto` mode,
  and malformed-URL boundary rejection (`400` instead of `500`). Each relevant
  test failed when its production guard was removed.
- Desktop dark and mobile light Chromium smoke passed on the updated host. Web
  individual-video buttons send an explicit override to retain their behavior.
- An existing-phone-shaped `POST /smart` with only a mobile playlist URL returned
  `202` in **0.016 seconds**, appended all four videos in order, and preserved
  current playback. An identical in-flight request returned the same job ID.
- `POST /play_now` with a video/list/index/timestamp link started playlist video 1,
  queued the remaining three in order, and preserved the interrupted video with
  its resume position after the playlist. A same-UUID retry added zero entries.
  Actual Qt runtime playback advanced from **0.04 to 1.28 seconds**.
- Smart on a closed/idle player started the first video and queued/persisted the
  remaining three before existing queue items. Actual Qt runtime playback
  advanced from **0.44 to 1.32 seconds**.
- Test-created queue entries were removed; the original queue IDs and playing
  session were restored. The original provider's page reported unavailable on
  re-resolution, so restoration used its matching cached stream from the private
  local session backup. Subsequent HTTP runtime checks confirmed advancing
  original playback. Device config and the user's compose healthcheck edit were
  preserved. No Android files or published images were changed.

## Product limits

Imports are finite snapshots: at most 1,000 entries, a 120-second extraction
deadline, two extraction workers, and one-hour process-local job/idempotency
records. Oversized playlists fail explicitly. Dynamic mixes require a saved
playlist. Flat metadata identifies known unavailable placeholders but cannot
guarantee that every listed video will play; normal playback handling applies
to restrictions discovered later.
