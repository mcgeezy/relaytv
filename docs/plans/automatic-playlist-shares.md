# Automatic playlist shares: server implementation

Extend the existing playlist importer to Android's existing `/smart` and
`/play_now` requests and the queue aliases. No Android source changes or app
rebuild are part of this server milestone.

- A finite YouTube `list=` link automatically imports the whole playlist from
  the beginning. `playlist_mode=video` preserves individual-video behavior;
  web individual-video actions send that override explicitly.
- Queue aliases append. Smart appends during playback/paused playback and starts
  the first video when idle. Play starts the first and puts the remainder before
  the preserved interrupted item and existing queue.
- Reuse bounded extraction, job status/cancellation, configured cookies, safe
  flat metadata, and playback service ownership. Reserve playback intent before
  extraction; never claim a fresh intent after a slow import superseded by Stop,
  Close, Clear, or another Play.
- Optional UUID request IDs bind URL, action, and options. Legacy URL-only clients
  coalesce identical in-flight requests; completed retries cannot be identified
  reliably without a client ID. Explicit re-shares remain allowed.
- Clear cancels pending imports and removes committed queue entries. Extraction
  failure changes nothing. A failed first-video start retains imported videos
  when it can do so without undoing newer queue edits, and reports actual counts.

## Milestones

1. Implement service commands, automatic endpoint dispatch, and focused tests.
2. Prove concurrency guards by reverting them; run all repository quality gates,
   regenerate the transition inventory, and update API/release documentation.
3. Deploy to this host using the existing branch bind mount, exercise phone-shaped
   requests through real extraction/playback, preserve the user's queue/session,
   commit verification, and update PR #158 while it remains open.
