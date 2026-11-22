# Phase 7: Hardening, Verification, and Release Readiness — Checkpoint

## Delivered Verification & Hardening

- **Phase 7 Hardening Runner (`scripts/ui-phase7-hardening.js` -> `docs/ui-modernization/phase-7-hardening.json`)**:
  - **Responsive & Accessibility Stress Matrix**: Verified zero horizontal overflow and zero page errors across Remote, Browse, and Settings at:
    - Narrow phone (`320×568`, dark)
    - 200% zoom (`640×400`, light)
    - Phone landscape (`667×375`, dark)
    - Reduced motion (`1280×800`, `prefers-reduced-motion: reduce`, dark)
  - **Keyboard-Only Navigation & Focus Traps**: Verified Enter/Escape/Tab flows on Add media (`#addUrlBtn` -> `#addBackdrop` -> Escape restores `#addUrlBtn`) and Peers (`#queueSendBtn` -> `#peersBackdrop` focuses `#peersCloseBtn`, Tab remains trapped inside dialog, Escape restores `#queueSendBtn`).
  - **Share-Target Safety**: Verified `/ui?share=<url>` pre-fills `#addUrlInput`, strips `?share=` from the URL bar, and triggers zero playback writes (`/play_now`, `/enqueue`).
  - **Navigation Stability**: Cycled `remote -> browse -> settings -> remote` 10 consecutive times (30 destination switches) with `nodeDelta: 0` (`1197 -> 1197` DOM nodes), `layerNavigation.getDepth() === 0`, and zero page errors.

## Initial JS/CSS Transfer Budget

| Reference | Files | Uncompressed | Gzipped (level 9) | Delta vs. `v0.12.3` |
| --- | --- | --- | --- | --- |
| Phase 0 baseline (`v0.11.0`) | 13 | 549,241 B | 126,414 B | — |
| `main` (`v0.12.3` @ `a8bc5dc`) | 14 | 604,936 B | 137,939 B | Baseline |
| Current `HEAD` (`feat/pr99-resume-v013`) | 24 | 672,421 B | 155,405 B | **+17,466 B (+12.66%)** |

**Documented reason for +12.66% gzip growth vs. `v0.12.3` (+2.66% above the 10% guideline):**
- Shared semantic token/component foundation & theme controller (`foundation.css` +2.05 KB gz, `theme.js` +1.35 KB gz)
- Extracted browser controller seams (`api.js` +2.23 KB gz, `overlays.js` +1.01 KB gz, `navigation.js` +0.61 KB gz, `store.js` +0.52 KB gz)
- Responsive app shell, bottom/rail navigation, and persistent compact playback bar (`shell.css` +3.67 KB gz, `shell.js` +3.36 KB gz)
- First-party API token dialog (`#authTokenBackdrop`) and Settings destination + dirty leave guard
- Full light-theme parity for previously dark-only `plex.css` (+0.27 KB gz) and `seerr.css` (+0.36 KB gz); `peers.css` shrank by -0.98 KB gz.

- **Independent Security & Regression Audit (Carbon, full `main..HEAD` diff)**:
  - Audited `RELAYTV_API_TOKEN` isolation, XSS/CSS escaping, overlay click-through/token headers, `tv_theme.py` / `qt_shell_app.py` styling boundaries, and Phase 4–6 modal/navigation interactions.
  - Resolved all 8 concrete findings before Phase 7 sign-off:
    1. `[High]` `api.js` `isSameOrigin()` now handles `URL` instances (`.href`) and rejects empty raw URLs so cross-origin `fetch(new URL(...))` never leaks `Authorization: Bearer`.
    2. `[High]` `remote.js` `_uiCloseTopLayerFromNav()` now skips `#settingsBackdrop.isDestination` and prioritizes higher modals (`#aboutBackdrop`, `#peersBackdrop`, `#histBackdrop`, `#notifyBackdrop`, `#addBackdrop`); `settings.js` Escape handler now respects `e.defaultPrevented` and higher open modals/menus.
    3. `[Medium]` `api.js` now coalesces concurrent 401 Bearer challenges via `(promptedThisLoad && !activePromptPromise)`, resets `promptedThisLoad` on `storeToken()`, and `settings.js` settles any prior pending auth-dialog Promise on re-entrant open.
    4. `[Medium]` `shell.css` hides `.compactPlaybackBar` while any `aria-modal="true"` provider detail/connect drawer is open (`#jfGrid.detailOpen`, `#jellyfinShell.jfSeasonChooserOpen`, `#plexDetail`, `#seerrDetail`, `#seerrConnectBackdrop`).
    5. `[Medium]` `remote.js` updates `relaytvPeers.syncPlayback()` before `_uiRefreshInteractionLockActive()` and `peers.js:close()` / `settings.js:closeSettings()` flush `renderStatus(__lastStatus)` on close.
    6. `[Low]` `overlays.js` `createDialogController().open(openOpts)` honors per-open `opener`/`initialFocus` overrides and skips focus restoration to hidden elements; `#authTokenBackdrop` no longer hardcodes `opener: hdrMenuBtn`.
    7. `[Low]` `seerr.js` `_seerrRenderDetail` strips `[\r\n\f"'()\\]` from `backdrop_url` before setting `backgroundImage`.
    8. `[Low]` Trimmed trailing EOF blank lines in `remote.js` and `settings.js` so `git diff --check main..HEAD` is completely clean.

## Full Gate & Browser Matrix Summary

| Gate / Suite | Result |
| --- | --- |
| `ruff check app tests` | Passed |
| `PYTHONPATH=app pytest -q` | **1,741 passed** |
| `node --test tests/js/*.test.js` | **109 passed** |
| `node --check app/relaytv_app/static/ui/*.js` | All 17 JS files passed |
| Generated X11 overlay inline script (`node --check -`) | Passed |
| Undefined CSS custom properties check across `static/ui/*.css` | **0 undefined** |
| `git diff --check` | Clean |
| Playwright Phase 0 (17 states + first-party auth dialog) | Passed |
| Playwright Phase 1 (9 Auto/Dark/Light + a11y checks) | Passed |
| Playwright Phase 2 (controller seams) | Passed |
| Playwright Phase 3 (remote + 6 viewports + interactions) | Passed |
| Playwright Phase 4 (all 4 browse providers × dark/light × phone/desktop) | Passed |
| Playwright Phase 5 (Settings destination, dirty guard, auth dialog, Peers) | Passed |
| Playwright Phase 6 (`/idle` & `/x11/overlay` at 720p/1080p/4K) | Passed |
| Playwright Phase 7 (320px/200% zoom/landscape/reduced-motion/keyboard/share/stability) | Passed |
| Playwright `playlist-ui-smoke.js` | Passed |

## Remaining Before PR Merge

- Physical device sign-off on NUC/Pi (bind-mounted `/app/relaytv_app` cold-start playback, play/pause, seek, next, close, idle return, and native Qt idle/toast rendering).
- Prune temporary development docs (`docs/UI_MODERNIZATION_PLAN.md`, `docs/ui-modernization/*`, `docs/images/ui-phase-*`) after device sign-off, keeping `docs/release-highlights/0.13.0.md` for Release Please `v0.13.0`.
