# Phase 5: Settings, Auth, and Peers — Checkpoint

## Delivered Behavior

- **Responsive Settings Destination (`settings.js`, `shell.js`, `shell.css`, `index.html`)**:
  - Settings opens in-flow as a primary destination (`#settingsBackdrop.isDestination`) inside the app shell layout (beside the desktop navigation rail at `>= 768px`, in normal scroll flow with bottom clearance on phones), keeping the compact playback bar active while playback is running.
  - Preserves `#settingsBackdrop` modal fallback behavior, all existing Settings tab/form IDs, `media_servers` CRUD, Plex/Jellyfin/Seerr/IPTV sections, and the realtime-refresh overwrite protection (`__settingsDirty` / `_isSettingsControlFocused`).
  - Adds a visible unsaved-changes badge (`#settingsDirtyBadge`) and an inline discard confirmation bar (`#settingsDiscardBar` with Keep editing / Discard changes) when attempting to leave or close Settings with unsaved edits.
  - Routes header menu `#settingsBtn` through `shell.navigation.navigate('settings')` and closes cleanly via `layerNavigation.back()` / `closeSettings({force:true})` on Save & Close.
- **First-Party API Token Dialog (`api.js`, `settings.js`, `index.html`, `shell.css`)**:
  - Replaces blocking `window.prompt(...)` with `#authTokenBackdrop`, an accessible modal dialog managed by `overlays.createDialogController` (`initialFocus`, Tab focus trap, Escape/backdrop cancel, focus restoration).
  - Coalesces concurrent 401 Bearer challenges via `requestReplacementToken()`, never replays a canceled write, supports Forget / Replace from the header menu (`#authTokenManageBtn`) via `RelayTV.api.forgetToken()`, and preserves all `RELAYTV_API_TOKEN` security boundaries (env-only on server, never returned by `/settings`, attached only to same-origin requests).
- **Peers Overlay Alignment (`peers.js`, `peers.css`)**:
  - Wires `peers.js` through `overlays.createDialogController` and `layerNavigation.back()` for consistent focus management, Escape/Back handling, and focus restoration.
  - Aligns `peers.css` surfaces, borders, text hierarchy, focus rings, and status dots/badges to `foundation.css` semantic tokens across dark and light modes.

## Verification Evidence

- Unit tests: `node --test tests/js/*.test.js` (including new async dialog, cancel, and `forgetToken()` tests in `tests/js/api.test.js` and existing `peer_selection.test.js` / `ui_review_fixes.test.js` coverage).
- Browser E2E runner: `scripts/ui-phase5-settings.js` -> `docs/ui-modernization/phase-5-settings.json` and screenshots under `docs/images/ui-phase-5/` (desktop dark, phone dark/light destination layouts, dirty leave guard, first-party API token accept/cancel/forget flow, and Peers overlay focus/Escape flow).
- Regression runners: `scripts/ui-phase0-states.js` (updated `exerciseAuthorization()` to drive `#authTokenBackdrop` and assert zero native `window.prompt` dialogs) and `scripts/ui-phase3-remote.js`.
