# Phase 3 remote and application navigation

Phase 3 turns `/ui` into a responsive application shell while retaining the
existing playback, queue, history, track, settings, and provider controllers.
The phone layout uses a bottom navigation bar; layouts at 768px and wider use a
compact side rail. Remote remains the initial destination and receives an
explicit `#remote` hash. Browse and Settings use `#browse` and `#settings`, so
browser Back returns through public destinations without placing credentials or
private media references in the URL.

## Delivered behavior

- Remote, Browse, and Settings are stable primary destinations. The device name
  and Add media action remain visible in the header at every width.
- Browse gives IPTV, Jellyfin/Emby, Plex, and Seerr a consistent home. It keeps
  unavailable services visible with a path to Settings and identifies the last
  service used without persisting a media or credential reference.
- The existing provider launch buttons remain as hidden compatibility hooks.
  Browse proxies them rather than duplicating provider requests or state.
- A compact playback bar subscribes to the Phase 2 status store. Browse and
  Settings therefore show the same title and play/pause state without opening a
  second realtime connection. Return unwinds provider and destination history
  back to Remote.
- Now playing and the six direct transport commands use less height. At phone
  sizes the queue begins in the first viewport, while desktop uses a two-column
  Remote layout. Queue rows retain artwork, provider marks, metadata, and a
  single action menu.
- Reordering remains available by drag and Arrow keys on wider layouts. Phones
  expose Move up and Move down in the item menu, alongside Play now, Send to
  device, and Remove. Remove retains its undo window.
- Add media now accepts supported local video and audio files for immediate
  playback or queueing through the existing ingest endpoints. Link playback,
  link queueing, image-backed notifications, paste/share prefill, and retryable
  upload errors stay in the same composer.
- History replay/requeue and audio/subtitle track selection keep their existing
  endpoints and dialogs. The Phase 3 interaction run found and fixed a missing
  subtitle-dialog popstate branch that had prevented its close button from
  dismissing the dialog.

## Browser evidence

Run the deterministic Phase 3 matrix with:

```text
NODE_PATH=/home/mark/.npm-global/lib/node_modules \
  node scripts/ui-phase3-remote.js \
  --base=http://127.0.0.1:8790
```

The checked-in [Chromium report](phase-3-remote.json) records Playwright 1.62.0
with Chromium 151. It verifies 320×720, 390×844, 768×1024, 1280×800,
1920×1080, and 844×390 phone-landscape layouts. All direct visible controls in
the measured shell are at least 44px in both dimensions, all three fixture queue
rows remain reachable, and no measured layout has horizontal overflow.

The action matrix drives real browser interactions through deterministic HTTP
fixtures for:

- play/pause, link queue, and link play;
- local video upload failure/retry to the queue and local audio upload for
  immediate playback;
- notification text and image URL submission;
- queue move, remove, undo, and committed remove;
- history requeue and replay;
- audio and subtitle selection;
- Browse, remembered Jellyfin selection, provider open, compact play/pause,
  return to Remote, and Settings navigation;
- PWA share-target prefill and query removal without automatic playback.

The checked-in screenshots show the
[390px Remote](../images/ui-phase-3/phase3-remote-390x844.png),
[1280px Remote](../images/ui-phase-3/phase3-remote-1280x800.png), and
[390px Browse destination](../images/ui-phase-3/phase3-browse-phone.png).
Firefox 153 passed the six-layout and share-target subset. The installed WebKit
runner could not launch because its host dependency `libavif.so.16` is absent;
this is an environment limitation, so current WebKit Phase 3 evidence remains
pending. Phase 0 WebKit evidence still covers the pre-shell behavior.

The subtitle popstate regression was revert-proved: removing only the new
dispatcher branch left `#subLangBackdrop` visible after its Close action, and
restoring the branch made the same browser interaction pass.
IPTV destination history was also revert-proved: removing its layer push made
browser Back skip Browse and return directly to Remote; restoring it made Back
close IPTV while retaining the public `#browse` destination.

The full Phase 0 Chromium runner still passes all 17 recorded states after the
navigation change. The Phase 1 Chromium runner still passes its nine theme,
contrast, focus, target-size, reduced-motion, provider, and dialog scenarios.

## Transfer budget

The 23 production UI CSS and JavaScript assets total **590,993 source bytes**
and **138,661 gzip bytes**. This is **41,752 source bytes (7.60%)** and **12,247
gzip bytes (9.69%)** above the Phase 0 baseline, within the plan's 10% initial
transfer budget. Phase 3 adds one versioned stylesheet and one versioned script;
neither introduces a frontend framework, build tool, font, or network image
dependency.

## Exit status

- [x] Responsive phone navigation and desktop/tablet rail are implemented.
- [x] Remote transport and queue controls remain reachable at planned widths.
- [x] Browse and Settings participate in public hash navigation and browser Back.
- [x] Compact playback uses the shared status snapshot and command adapter.
- [x] Link, upload, notification, history, track, queue, provider, and share
  interactions are browser-tested.
- [x] Existing Phase 0 and Phase 1 Chromium behavior remains covered.
- [ ] Physical phone safe-area, virtual-keyboard, installed-PWA, and 200% zoom
  checks remain for Phase 7 device sign-off.
- [ ] WebKit Phase 3 rerun remains pending on a host with its runtime libraries.

Phase 4 can now migrate each provider surface onto the shared shell components
without changing provider adapters or primary navigation.
