# Phase 4 browse consistency

Phase 4 aligns the four media browse shells — Unified/Jellyfin (`jellyfin.css`),
Plex (`plex.css`), IPTV (`iptv.css`), and Seerr (`seerr.css`) — onto the Phase 1
semantic foundation tokens (`--background`, `--surface`, `--surface-raised`,
`--surface-hover`, `--text-primary`, `--text-secondary`, `--text-tertiary`,
`--border`, `--border-subtle`, `--border-strong`, `--accent`, `--accent-soft`,
`--accent-strong`, `--on-accent`, `--focus-ring`, `--status-success`,
`--status-warning`, `--status-danger`, and `--radius-control` / `--radius-card`
/ `--radius-lg` / `--radius-dialog`) while preserving each provider's subtle
identity accent and all existing JavaScript adapters.

## Delivered behavior

- **Unified dark and light themes across all four providers:** `plex.css` and
  `seerr.css` now use the shared semantic surface, text, border, and status
  tokens with explicit `@media (prefers-color-scheme: light)` rules (replacing
  their previous dark-only `#0b0b0c` and `#090815` palettes), so Auto, Dark, and
  Light theme switching works consistently across Unified/Jellyfin, Plex, IPTV,
  and Seerr.
- **Calm surfaces and consistent control geometry:** Header actions, Back
  buttons, section tabs, search inputs, filter selects, and detail actions meet
  the 44×44 CSS-pixel minimum touch target and display a 3px
  `var(--focus-ring)` outline on `:focus-visible`.
- **Desktop rail isolation while a provider shell is open:** Opening any of the
  four full-screen provider shells hides pointer interaction on `.primaryNav` so
  the desktop side rail cannot overlap or intercept the provider shell's top-left
  Back button at 1280px while keeping `#compactPlaybackBar` interactive above
  the browse view.
- **Preserved provider contracts and pinned CSS selectors:** All existing
  provider behaviors, responsive drawer/sheet layouts, iOS standalone safe-area
  header paddings, and smoke test assertions remain intact, with zero undefined
  CSS custom properties across all four provider stylesheets.

## Browser and gate verification

Run the deterministic Phase 4 Playwright E2E runner with:

```text
NODE_PATH=/home/mark/.npm-global/lib/node_modules \
  node scripts/ui-phase4-browse.js \
  --base=http://127.0.0.1:8792
```

The checked-in [Chromium report](phase-4-browse.json) verifies all four
providers (`jellyfin`, `plex`, `iptv`, `seerr`) at both `390×844` (phone) and
`1280×800` (desktop) in both `dark` and `light` themes:

- Shell background (`rgb(11, 16, 23)` dark vs. `rgb(244, 246, 249)` light),
  card/panel surface (`rgb(21, 29, 40)` dark vs. `rgb(255, 255, 255)` light),
  and primary text color (`rgb(243, 246, 250)` dark vs. `rgb(21, 32, 51)` light)
  update identically across all four provider shells.
- Zero horizontal document or shell overflow (`docOverflow: false`,
  `shellOverflow: false`) at 390px and 1280px.
- Zero nested interactive controls (`nestedInteractive: 0`) across all four
  shells.
- Primary header Back buttons and search inputs meet `>= 44px` height/width and
  render a `3px solid` focus ring (`focusOutlineWidth: 3`).
- Detail panels (`jellyfin`, `plex`, `seerr`) open at `layerNavigation` depth 2,
  close cleanly via `Escape` and browser `Back` to depth 1 without closing the
  parent provider shell, and return to `#browse` at depth 0 when exiting the
  shell.
- Unavailable/unconfigured providers render `.isUnavailable` in
  `#browseDestination`, route directly to `#settings` when activated, and return
  cleanly to `#browse` with `layerNavigation` depth 0 when Settings is closed.
