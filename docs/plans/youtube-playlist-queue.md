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
3. **Host verification** (`test: verify YouTube playlist queue deployment`)
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

Pending implementation and deployment.
