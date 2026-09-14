# Phase 2 controller seams and packaged markup

Phase 2 separates the browser application's core boundaries and large feature
controllers while preserving the released interface and classic-script loading
model. `app.js` now wires dependencies and starts the application; feature code
can move independently without introducing a framework or production build.

## Extracted ownership

| Asset | Responsibility |
| --- | --- |
| `api.js` | Same-origin API-token compatibility, request timeout, normalized command results, and safe retry rules |
| `store.js` | Server status snapshot subscriptions and separate transient UI state |
| `navigation.js` | Browser-history layer depth and popstate listener lifecycle |
| `overlays.js` | Dialog initial focus, focus trap, Escape/backdrop requests, return focus, and cleanup |
| `remote.js` | Remote, queue, realtime, history, track, About, and media-input behavior |
| `settings.js` | Settings loading, integration setup, validation, and save behavior |
| `app.js` | Core adapter wiring, compatibility names, and DOM bootstrap |

The core modules publish factories under `window.RelayTV`. The active status
store and layer navigation instance are available under `RelayTV.runtime`.
Existing global feature functions remain as temporary bridges for current
provider controllers and event handlers.

`api.js` still installs the existing global fetch compatibility wrapper because
provider callers have not all migrated to an explicit request client. Remote
commands now use the explicit API adapter. The browser token remains in the
existing `relaytv_api_token` local-storage key, is attached only to same-origin
requests, and is never placed in settings, URLs, logs, screenshots, or the
status store. A challenged write retries only after explicit token entry;
cancel, non-Bearer rejection, and rejected or ambiguous non-idempotent writes
never trigger an automatic replay.

## Packaged documents

The complete `/ui`, `/idle`, and `/x11/overlay` documents now live in:

- `static/ui/index.html`;
- `static/ui/idle.html`;
- `static/ui/x11-overlay.html`.

Their route handlers retain endpoint ownership, cache headers, and all runtime
substitutions. The documents and every new controller participate in the UI
asset version stamp. Template documents are read from the same packaged static
tree copied into the published container and are not exposed through the
allowlisted static-asset endpoint.

## Regression evidence

Run the deterministic seam check with:

```text
NODE_PATH=/home/mark/.npm-global/lib/node_modules \
  node scripts/ui-phase2-seams.js \
  --base=http://127.0.0.1:8790
```

The checked-in [Chromium report](phase-2-seams.json) verifies:

- every core namespace and extracted controller asset loads in dependency order;
- the active status store receives the deterministic playback snapshot;
- About focuses its close button, traps focus, and restores focus to the visible
  Menu button;
- Escape and two repeated open/close cycles return navigation depth to zero;
- the extracted settings controller is available after startup;
- no page or console errors occur.

The same seam runner passed in Firefox 153 and Playwright WebKit 26.5. The full
Phase 0 runner passed all 17 Chromium scenarios after extraction, and the Phase
1 runner passed all nine Auto/Dark/Light scenarios. These cover command/auth
outcomes, queue stability, provider rendering and details, connection recovery,
settings markup, responsive dialog behavior, and the TV overlay.

Node coverage now exercises API-token origin boundaries, challenged retry and
cancel paths, normalized command failures, store unsubscribe behavior,
transient-state separation, navigation listener cleanup, dialog focus and
listener cleanup, and the existing settings/provider/queue/realtime behaviors.
CI syntax-checks every production UI JavaScript asset so a new extracted file
cannot bypass the JavaScript gate.

The dialog lifecycle test was revert-proved: removing the production focus-wrap
operation made the boundary-tab assertion fail, and restoring the operation
returned the test to green.

## Transfer budget

The 21 production UI CSS and JavaScript assets total **565,542 source bytes**
and **131,708 gzip bytes**. This is **16,301 source bytes (2.97%)** and **5,294
gzip bytes (4.19%)** above the Phase 0 baseline, within the plan's 10% initial
transfer budget. Compared with Phase 1, the extraction adds 3,907 gzip bytes;
most application code moved between files unchanged.

## Exit status

- [x] API, status-store, navigation, and overlay helpers have explicit owners
  and tests.
- [x] Remote and settings responsibilities are separate from application
  bootstrap.
- [x] UI, idle, and overlay markup load from packaged documents with runtime
  substitutions and stable endpoints.
- [x] Request outcome, challenged-write, focus, history-depth, unsubscribe, and
  listener-cleanup regressions are covered.
- [x] Extracted asset order, serving, versioning, and production JavaScript
  syntax are checked.
- [x] Phase 0 and Phase 1 behavior remains equivalent in browser regression
  runs.

Phase 3 can replace the temporary global feature bridges as it introduces the
responsive application shell, remote layout, compact playback bar, and explicit
navigation destinations.
