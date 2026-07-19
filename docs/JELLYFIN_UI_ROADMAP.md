# Jellyfin UI Modernization Roadmap

Status: active  
Working branch: `feat/jellyfin-ui-modernization`  
Foundation: remote glass redesign PR
[#38](https://github.com/mcgeezy/relaytv/pull/38), merged 2026-07-19

This is a temporary working roadmap requested for the Jellyfin/Emby UI
modernization. Update it with milestone state, implementation decisions, and
validation evidence as work lands. At closeout, move durable product and
operator guidance into the primary docs, retain release history in the merged
PR, and remove this roadmap from the docs tree.

## Goal

Deliver a modern, fast, accessible Jellyfin/Emby browsing experience that
matches RelayTV's glass visual system while preserving the existing playback,
catalog, settings, route, and server-branding contracts.

The product should remain local-first and usable from both phones and desktop
browsers. The modernization must not rewrite the playback backend or move
Jellyfin product behavior back into route modules.

## Branch And Release Strategy

- Keep PR #38 limited to the remote glass redesign; do not add Jellyfin work to
  that branch or PR.
- This branch starts from the post-PR #38 `main` merge commit `d6eb8cf`.
- Use Conventional Commit titles and a dedicated feature PR:
  `feat: modernize the Jellyfin browsing UI`.
- The PR body must cover user impact, operator/deployment impact, breaking
  changes (`None` unless that changes), and tests run.
- Do not edit `CHANGELOG.md`, create a normal release tag, or change release
  versions manually; Release Please owns those outputs.

## Current Baseline

Reviewed 2026-07-19 on `feat/remote-glass-redesign` with the live RelayTV
instance at `http://10.55.55.2:8787/ui`. Playwright connected to the container
server at `ws://10.55.55.98:3000/`.

Healthy behavior:

- Dashboard, Movies, TV, search, detail, adjacent-episode navigation, and
  playback actions are already implemented.
- Jellyfin/Emby detection and user-facing branding work without separate route
  surfaces.
- Phone and desktop layouts had no viewport-level horizontal overflow.
- Dark and light themes rendered without browser errors.
- Keyboard navigation between catalog items worked.
- The browser run produced no console errors, page errors, failed requests, or
  HTTP error responses.

Performance and UX findings:

- Movies requests `limit=5000` and renders the full catalog instead of using
  the existing `start`, `limit`, and `next_start_index` API contract.
- The live 279-movie catalog produced 279 cards, 3,026 DOM elements, 240 image
  requests, about 12.6 MB of image transfer, and a 27,721-pixel nested scroll
  region on a 390x844 phone viewport.
- Non-dashboard catalog images are eager-loaded.
- Movie and series cards crop primary poster art into landscape thumbnails.
- Catalog cards use `role="button"` containers with nested action buttons;
  the live Movies view contained 558 nested interactive controls.
- Mobile catalog browsing uses an inner vertical scroller instead of one
  natural page scroll.
- Detail metadata can repeat values such as the production year, and four
  equally weighted playback actions obscure the most likely action.
- Keyboard instructions occupy phone space even on touch-only clients.
- The Jellyfin implementation is coupled to the large shared `app.js`,
  `app.css`, and Python-embedded `/ui` shell.

## Product Direction

### Shell And Navigation

- Use a compact app bar with Back, detected Jellyfin/Emby brand, connection
  state, and search.
- Use bottom navigation for Home, Movies, and TV on phones; retain a segmented
  navigation treatment on wider screens.
- Remove repeated provider headings and hide keyboard-only help on touch
  devices.
- Present loading skeletons, empty states, degraded status, reconnect actions,
  and action confirmation consistently.

### Catalog

- Home uses horizontal rails for Continue Watching, Next Up, Movies, Shows,
  and Recently Added.
- Continue Watching cards show resume progress.
- Movies and series use portrait posters; episodes use landscape artwork.
- Full catalogs load in bounded pages and append through an intersection
  sentinel.
- All off-screen images are lazy-loaded and stale requests are canceled.

### Detail And Actions

- Use a mobile bottom sheet and desktop side drawer with proper dialog and
  focus behavior.
- Prefer backdrop artwork for the detail hero, with poster and placeholder
  fallbacks.
- Make Resume the primary action when available; otherwise use Play.
- Keep Play Next visible and place less common queue actions in an overflow
  menu.
- Use one user-facing term for adding to the end of the queue.
- Preserve series, season, episode, Play All, and adjacent-episode workflows.

### Accessibility And Theme

- Do not nest buttons or other interactive controls inside a card acting as a
  button.
- Use semantic buttons, dialog relationships, focus containment/restoration,
  live regions, and background `inert` state where appropriate.
- Use at least 44px touch targets, visible focus rings, reduced-motion support,
  and contrast suitable for both themes.
- Build new Jellyfin components from semantic design tokens and an explicit
  effective-theme state instead of depending on runtime CSS media-rule
  rewriting.

## Milestones

### M0 - Dependency And Baseline

Status: complete

- [x] Review PR #38 history and current UI styling decisions.
- [x] Review architecture, Jellyfin inventory, operations, routes, and tests.
- [x] Capture live phone and desktop Playwright evidence.
- [x] Record performance and accessibility baselines.
- [x] Merge PR #38.
- [x] Fast-forward this branch to post-merge `main` (`d6eb8cf`).

Exit: the glass redesign is on `main`, this branch contains it exactly once,
and the worktree is ready for isolated Jellyfin commits.

### M1 - Frontend Containment

Status: complete

- [x] Mechanically extract Jellyfin JavaScript into
  `static/ui/jellyfin.js` without changing behavior.
- [x] Extract Jellyfin browse styles into `static/ui/jellyfin.css`.
- [x] Extend the static asset allowlist and asset-version stamp.
- [x] Keep current IDs and entrypoints stable until the modern renderer is
  covered by browser tests.
- [x] Route Jellyfin asset assertions to their dedicated files in the current
  smoke test; defer a separate browser-test module to M6.

Exit: the existing UI behaves identically, but Jellyfin work no longer expands
the shared frontend monolith.

### M2 - Catalog Performance Foundation

Status: complete

- [x] Replace the 5,000-item request with bounded pages (target 36-48 items).
- [x] Append using `next_start_index` and an `IntersectionObserver` sentinel.
- [x] Deduplicate by item ID and cancel obsolete search, sort, and tab requests.
- [x] Lazy-load every off-screen image.
- [x] Remove the phone's nested catalog scroll region.
- [x] Preserve selection and keyboard movement as pages append.

Exit: the first Movies render never fetches the full live library, initial DOM
and image work are bounded, and loading another page does not duplicate cards.

### M3 - Modern Shell

Status: complete

- [x] Add the responsive app bar and phone/desktop navigation treatments.
- [x] Integrate search, sort, connection state, loading, empty, and offline
  states.
- [x] Apply the glass design tokens in dark, light, and automatic modes.
- [x] Keep Jellyfin and Emby labels driven by detected server type.
- [x] Provide a query/local-storage experiment switch while the old shell is
  still needed for comparison.

Exit: users can navigate every top-level view in the modern shell without
losing existing features.

### M4 - Media Cards And Detail

Status: complete

- [x] Add additive poster/backdrop image roles to normalized catalog payloads.
- [x] Build media-type-aware Home, movie, series, and episode cards.
- [x] Add Continue Watching progress presentation.
- [x] Implement the bottom-sheet/side-drawer detail surface.
- [x] Normalize duplicated metadata and action terminology.
- [x] Preserve Play, Resume, Play Next, queue-last, Play All, and adjacent
  episode behavior.

Exit: catalog art uses the correct aspect ratio, detail prioritizes the likely
action, and no playback command semantics regress.

### M5 - TV, Search, And Resilience

Status: complete

- [x] Complete series to season to episode navigation.
- [x] Validate Play All and previous/next episode traversal.
- [x] Complete debounced, cancelable search across top-level sections.
- [x] Handle timeout, disconnected, empty, partial-image, and stale-response
  states.
- [x] Confirm Emby hides unsupported rows while preserving supported flows.

Exit: Jellyfin and Emby browse, search, and TV workflows are complete under
normal and degraded conditions.

### M6 - Browser, Accessibility, And Live Validation

Status: in progress

- [ ] Add a repeatable Playwright smoke path that accepts a WebSocket endpoint
  such as `ws://10.55.55.98:3000/`.
- [ ] Cover phone and desktop viewports in dark and light themes.
- [ ] Cover shell visibility, paginated Movies, TV hierarchy, search, detail,
  keyboard navigation, touch layout, and offline recovery.
- [ ] Assert no viewport overflow, nested interactive controls, console errors,
  failed requests, or unexpected HTTP responses.
- [ ] Run live Jellyfin and Emby verification from `JELLYFIN_OPERATIONS.md`.
- [ ] Update product screenshots only after the design is accepted.

Exit: automated browser coverage and live verification evidence are recorded,
with no known accessibility or functional regression.

### M7 - Release Handoff And Closeout

Status: pending

- [ ] Make the modern UI the default after acceptance.
- [ ] Remove the temporary comparison implementation and experiment switch.
- [ ] Update durable architecture and operator docs where behavior changed.
- [ ] Prepare the feature PR with release-note-quality context and tests.
- [ ] Remove this roadmap after its durable facts are captured elsewhere.

Exit: the feature PR is ready to merge and normal Release Please flow can
publish the change.

## Validation Gates

Run before finishing every implementation change:

```text
ruff check app tests
PYTHONPATH=app pytest -q
git diff --check
```

If public routes change, update `tests/test_route_inventory.py`. If Jellyfin
route functions move or change, regenerate `JELLYFIN_INVENTORY.md` with the
test's `--write` mode. Additive UI-only changes should not require route or
inventory churn.

Browser validation should capture:

- viewport and catalog scroll dimensions
- rendered item and DOM counts
- request failures and HTTP error responses
- console and page errors
- image request count and transfer size
- focus movement and dialog focus restoration
- screenshots at phone and desktop widths in both themes

## Decision Log

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-07-19 | Use a separate feature branch and PR instead of expanding PR #38. | PR #38 is already a large, review-ready visual-system change; separating Jellyfin keeps review, release notes, and rollback scope clear. |
| 2026-07-19 | Start the branch from `main` and rebase after PR #38 merges. | Avoids adding commits to the open branch and prevents stacked history from duplicating squash-merged commits. |
| 2026-07-19 | Keep the runtime frontend framework-free for this project. | Existing APIs and browser features are sufficient; modular static assets address the current coupling without adding a build/runtime toolchain. |
| 2026-07-19 | Fix pagination and rendering scale before visual restyling. | Live Playwright evidence shows the full-catalog render is the highest-impact functional risk. |
| 2026-07-19 | Reuse existing routes and add response fields only when required. | Preserves companion compatibility and the established route/service boundaries. |

## Change Log

| Date | Milestone | Commit / PR | Change | Validation |
| --- | --- | --- | --- | --- |
| 2026-07-19 | M0 | Planning | Reviewed history and docs; captured live Playwright baseline; created the modernization roadmap and branch strategy. | `ruff check app tests`; 401 tests; `git diff --check` passed on PR #38 before branching. |
| 2026-07-19 | M0 | `d6eb8cf` | Confirmed PR #38 merged and fast-forwarded the Jellyfin feature branch to its post-merge `main` commit. | Branch ancestry and clean merge state verified with Git and GitHub. |
| 2026-07-19 | M1 | `b70a392` | Extracted the Jellyfin browse controller and stylesheet into separately cache-busted static assets without changing IDs, APIs, or behavior. | Rebuilt the live container; `/health` and Jellyfin authentication/connection passed; Playwright phone-dark and desktop-light dashboard, Movies, detail, keyboard, asset-load, error, and overflow checks passed. Ruff and all 401 tests passed. |
| 2026-07-19 | M2 | Milestone commit | Replaced full-library catalog requests with 48-item pages, deduplicated sentinel-driven appends, cancellable browse requests, lazy images, and document-level catalog scrolling. | Rebuilt the live container; direct endpoint pagination passed; remote Playwright via `ws://10.55.55.98:3000/` verified 48-to-96 append, unique IDs, retained focus, no nested or horizontal overflow, bounded sort reloads, TV loading, and aborted obsolete sort/search requests. |
| 2026-07-19 | M3 | Milestone commit | Added the opt-out modern shell with a glass app bar, separate connection/loading state, desktop rail, phone bottom navigation, integrated search/sort toolbar, and persisted `jfui=modern|classic` switch. | Rebuilt the live container; remote Playwright and screenshot inspection covered phone dark, desktop light, and classic fallback modes with connected branding, fixed/sticky navigation, correct mode selection, zero overflow, and no browser errors. |
| 2026-07-19 | M4 | Milestone commit | Added poster/backdrop/progress payload roles, media-aware poster and landscape cards, Continue Watching progress/Resume affordances, normalized action labels, and viewport-anchored phone sheet/desktop drawer details. | Rebuilt the live container; service/route/inventory tests passed; live payload sampling verified image/progress roles; remote Playwright and screenshots verified 2:3 catalog art, 16:9 backdrops, progress controls, action parity, exact viewport anchoring, zero overflow, and no browser errors. |
| 2026-07-19 | M5 | Milestone commit | Fixed null season selection, completed TV hierarchy behavior, retained scoped cancelable search, and added deterministic partial-image fallback. | Rebuilt the live container; remote Playwright verified 11 series, 10 Season 1 episodes, adjacent traversal, Play All affordances, TV/movie search scoping, simulated offline-to-reconnect recovery, and 88/88 failed images replaced by the local fallback; Emby/Jellyfin service tests passed. |
