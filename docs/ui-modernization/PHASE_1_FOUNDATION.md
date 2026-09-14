# Phase 1 shared visual foundation

Phase 1 introduces RelayTV's production design foundation without removing the
provider styles or interaction paths recorded in phase 0. It establishes the
shared visual rules that later phases can adopt one surface at a time.

## Shipped foundation

- `foundation.css` defines semantic dark and light colors, the 4–48px spacing
  scale, control/card/dialog radii, motion durations, elevation, and layer
  values. Compatibility aliases connect those roles to the existing CSS custom
  properties while provider migration remains in progress.
- Shared button, field, status, panel, and dialog classes provide 44px minimum
  controls, visible focus rings, semantic states, and reduced-motion behavior.
- The Add media fields and actions, notification composer, and About dialog use
  the shared classes. On phones, About is a bottom sheet with a labelled dialog
  relationship and a 44×44 close target.
- `theme.js` owns the existing Auto/Dark/Light preference, system-theme
  observation, theme metadata, and the temporary media-query compatibility
  bridge. Its script order and asset version are explicit, and its core logic
  runs in the Node test harness.

The existing `relaytv_theme` storage key and Auto/Dark/Light values remain
compatible. No settings, API, playback, provider, or deployment contract
changes in this phase.

## Browser evidence

Run the deterministic evidence capture against a local testing server:

```text
NODE_PATH=/home/mark/.npm-global/lib/node_modules \
  node scripts/ui-phase1-foundation.js \
  --base=http://127.0.0.1:8790
```

The checked-in [Chromium report](phase-1-foundation.json) and
[screenshots](../images/ui-phase-1/) cover each Auto, Dark, and Light mode on:

- the remote at 390×844;
- the Jellyfin provider shell at 1280×800;
- the About dialog at 390×844.

The same nine-scenario runner passed in Firefox 153 and Playwright WebKit 26.5.
The phase 0 regression runner also passed all 17 Chromium scenarios and its
Firefox/WebKit compatibility scenario against the Phase 1 source. Those checks
preserved queue menu removal, provider fixture rendering, authorization retry
and cancel behavior, reconnect feedback, overlay notification rendering, and
phone/desktop overflow assertions.

Measured semantic pairs meet the 4.5:1 text threshold used by this checkpoint:

| Pair | Dark / Auto | Light |
| --- | ---: | ---: |
| Primary text / background | 17.60:1 | 15.08:1 |
| Primary text / surface | 15.65:1 | 16.33:1 |
| Secondary text / surface | 8.06:1 | 6.22:1 |
| On-accent text / accent | 7.45:1 | 6.70:1 |
| Focus ring / background | 11.44:1 | 6.99:1 |

The evidence runner additionally verifies the rendered body palette after its
theme transition, no phone horizontal overflow, three deterministic provider
items, a visible 3px focus outline, 44px minimum dialog targets, reduced-motion
durations, phone-sheet placement, and dialog labelling. It waits for computed
theme styles before measuring so a transition frame cannot produce a false
result.

## Transfer budget

The 15 production UI CSS and JavaScript assets now total **557,375 source
bytes** and **127,801 gzip bytes**. Compared with the phase 0 baseline, this is
an increase of **8,134 source bytes (1.48%)** and **1,387 gzip bytes (1.10%)**.
Theme extraction moved code out of `app.js`; the net increase is the reusable
foundation and its tests rather than a runtime framework or build dependency.

## Exit status

- [x] Semantic tokens, shared base rules, and representative components load as
  versioned production assets.
- [x] Theme handling is extracted and its persisted, manual, Auto, system-change,
  and unreadable-stylesheet paths are tested.
- [x] Auto, Dark, and Light remote/provider/dialog specimens are captured.
- [x] Contrast, focus, target-size, reduced-motion, dialog, and overflow checks
  pass.
- [x] The phase 0 Chromium and Firefox/WebKit compatibility scenarios pass.

The physical phone/PWA and remaining native-device rows in the
[phase 0 baseline](PHASE_0_BASELINE.md) still block later removal of legacy
theme and surface styles. Phase 2 can build controller and markup seams on this
foundation while those hardware rows are completed.
