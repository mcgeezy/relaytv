# YouTube playlist queue implementation

Add every available video from a finite YouTube playlist as ordinary RelayTV
queue entries, preserving playlist order and intentional repeated videos.
Existing single-video enqueue aliases and playback resolution remain compatible.

## Milestones

1. **Import service and API** (`feat: add YouTube playlist queue imports`)
   - Validate YouTube playlist links and canonicalize away video/index context.
   - Enumerate once using flat yt-dlp metadata and existing cookie configuration;
     build clean video URLs without per-video network lookups.
   - Run bounded background jobs with status, cancellation, and request-ID
     idempotency. Reject oversized playlists explicitly; do not silently truncate.
   - Append through `playback_service.queue_items()` after extraction, preserving
     concurrent queue edits. Prevent cancelled/retired jobs from publishing.
   - Cover extraction, API/auth, retries, and real competing-request interleavings.
     Revert production guards to prove concurrency/lifecycle/boundary tests fail.
2. **Browser experience** (`feat: queue entire YouTube playlists from links`)
   - Offer an entire-playlist action for YouTube list links alongside individual
     video actions when a video is present.
   - Show import status and added/skipped counts, support cancel, and retain the
     request ID through ambiguous network failures and browser reloads.
   - Add browser logic tests and a browser smoke; document API and release impact.
3. **Host verification** (`docs: record YouTube playlist deployment verification`)
   - Run every AGENTS.md quality gate with project-compatible dependencies.
   - Preserve the device compose override, local healthcheck change, settings,
     queue, and session; deploy through the existing released-image bind mount.
   - Exercise the real playlist API, browser, persistence, and actual playback on
     this host; remove only test-created queue entries afterward.
   - Record concrete results, push the feature branch, and create a PR with user
     impact, operator impact, breaking changes, tests, and release-highlight review.

## Decisions

- Playlist imports append without interrupting playback or automatically starting
  an idle player. The existing queue/play controls remain in charge of playback.
- A playlist import is a snapshot of a finite playlist. Dynamic YouTube mixes
  require a finite playlist instead; private lists require configured cookies.
- Jobs and retry IDs are process-local, bounded, and expire after one hour.
  Restarted/expired jobs must be reported as unknown; clients must not silently
  resubmit an uncertain import with a new request ID.
- Flat metadata cannot guarantee playback availability. Skip known private/deleted
  placeholders and invalid entries, report their counts, and let existing playback
  error handling handle restrictions that are only discovered at play time.
- Cancellation/shutdown wins while extraction is in flight; once the atomic queue
  append begins, the import is committed and a cancel cannot undo it.

## Validation results

- Milestone 1: 37 focused playlist tests plus existing queue and route-inventory
  checks passed (58 tests total). Revert proofs removed the commit guard, hostname
  boundary, and playlist-size boundary independently; each produced the expected
  failures, then the production guards were restored.
- Milestone 1 full gates: 1,434 Python tests passed using an isolated editable
  `.[dev]` install (Python 3.13, FastAPI 0.143.3); Ruff, whitespace, both required
  JavaScript syntax checks, embedded overlay syntax, and 61 JavaScript tests passed.
- Milestone 2: 71 JavaScript tests passed. Desktop dark (1440px) and mobile light
  (390px) Chromium smoke passed against an isolated HTTP server, covering double
  taps, reload recovery, lost-response recovery, cancellation, video/list choices,
  dynamic mixes, lookalike hosts, and modal bounds. The smoke caught a missing
  static-asset allowlist entry, fixed before deployment and covered by the existing
  HTTP UI smoke. A focused final run passed 43 Python tests.
- Milestone 2 full Python gate passed all 1,434 tests; all required syntax,
  lint, and whitespace gates passed. Live extraction of the public JODA15
  playlist returned all four videos in order without per-video metadata calls.
- Final link review added pasted `www` and embedded `videoseries` cases. Browser
  and API revert proofs failed as expected when their guards were removed; the
  restored code passed 63 focused Python tests and 71 JavaScript tests.
- Milestone 3: deployed through the released image's existing read-only branch
  bind mount. Real API import added all four JODA15 videos in order, persisted
  them, and added zero on a same-ID retry. Two imported videos played with
  advancing Qt/mpv runtime telemetry. A real mobile browser import also added
  all four without interrupting current playback. Test entries were removed;
  original queue IDs and the playing session were restored.
- Complete results and reproduction commands:
  [YouTube playlist verification](../YOUTUBE_PLAYLIST_VERIFICATION.md).
- Final gates after the link-variant fix: all 1,438 Python tests and 71 JavaScript
  tests passed, as did Ruff, whitespace, required JavaScript/overlay syntax, and
  the additional playlist/smoke syntax checks. Final host API checks confirmed
  the embedded-playlist boundary through all four aliases; configuration files,
  original queue IDs, and the resumed playing session were preserved.
