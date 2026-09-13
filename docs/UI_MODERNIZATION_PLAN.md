# UI modernization and implementation plan

Status: active; phase 0 baseline and design work is in progress. Created
September 13, 2026.
Planning branch: `docs/ui-modernization-plan`, based on `main` at `385649b`.
Unified pull request: #99. Intended release: `0.11.1`.

## Outcome and scope

Give RelayTV one clean, modern visual language across its phone/desktop remote,
media browsers, settings, dialogs, and TV display. Make everyday actions easier
to find while retaining existing playback, integration, and device behavior.
Modernize the JavaScript incrementally so future features reuse components and
interaction rules instead of adding another independent shell.

This plan is explicitly requested in `docs/`, an exception to the usual rule
that development roadmaps stay in PRs and git history. It describes proposed
work, not capabilities already shipped. Scope is every user-facing interface
implemented in this repository. Companion Android and Home Assistant interfaces
are outside this checkout; preserve their contracts and use their existing
clients for compatibility checks where available.

## Review findings

The review inspected source, existing tests, browser smoke scripts, and the
checked-in remote and TV product screenshots. Those screenshots are illustrative
and may predate the current source. A live browser audit and runtime inventory
are now recorded in the
[phase 0 baseline](ui-modernization/PHASE_0_BASELINE.md). Deterministic browser
surface and state captures are complete. Physical phone/PWA interaction, NUC
native visuals, and active Pi playback, notification, and input checks remain
open until their evidence rows are complete.

| Surface | Current implementation | Proposed improvement |
| --- | --- | --- |
| Remote, now playing, queue | `static/ui/app.js`, `app.css`; HTML in `routes/__init__.py` | Clear control hierarchy, more visible queue, persistent playback access while browsing |
| Link entry, uploads, share target, notifications | Same remote controller and generated HTML; share/PWA routes in `routes/assets.py` | Separate playback input from notification composition; retain share-to-prefilled-form behavior |
| API token entry and authorization recovery | Global fetch wrapper and browser `window.prompt` at the top of `static/ui/app.js` | First-party credential dialog with clear retry, cancel, replace, and forget behavior; keep the server token env-only |
| History, About, audio/subtitle selection | Dialogs in shared remote | Shared dialog, list, status, and focus behavior |
| Settings and integration setup | Large settings markup and handlers in the shared files | Dedicated settings view with grouped navigation, clear save state, and consistent setup flows |
| Jellyfin/Emby | `jellyfin.js`, `jellyfin.css` | Retain rich catalog and keyboard behavior; adopt shared navigation, cards, details, and themes |
| Plex | `plex.js`, `plex.css` | Match shared shell and detail patterns while preserving server/library and playback choices |
| IPTV | `iptv.js`, `iptv.css` | Consistent search, source/favorite navigation, channel rows, and playlist management |
| Seerr | `seerr.js`, `seerr.css` | Consistent discovery/details; retain request status, policy, and Quick Connect identity |
| Send/copy to device | `peers.js`, `peers.css` | Clear destination, selection, and send-versus-copy consequences |
| Browser idle dashboard and overlay | `/idle`, `/x11/overlay`; inline HTML/CSS/JS in `routes/__init__.py` | Related TV design with readable clock/weather/QR and restrained notifications |
| Native TV idle and notifications | Qt widget styling/painting and an embedded browser overlay in `qt_shell_app.py`; standalone browser host in `overlay_app.py` | Explicit native and browser implementations of the same visual specification |
| Brand, startup splash, and PWA assets | `static/brand/`, `routes/assets.py`, splash handling in `player.py`, and operator overrides under `/data/assets` or env paths | Cohesive loading/install presentation while preserving operator-provided assets and fallback order |
| PWA, loading and connection states | `routes/assets.py`, `realtime_transport.js`, `app.js` | Consistent theme metadata, update/reconnect feedback, and installed-app navigation |

Paths above are relative to `app/relaytv_app/` unless stated otherwise.

Concrete maintenance and UX opportunities:

- `app.js` is approximately 4,000 lines and combines auth, requests, remote
  controls, queue gestures, history, settings, theme, and initialization.
- API-token recovery currently patches `window.fetch`, opens a native browser
  prompt after the first same-origin Bearer challenge, stores the entered token
  in `localStorage`, and retries that request. Extraction needs an explicit UI
  and must retain the challenge-before-mutation safety boundary.
- `app.css` is approximately 2,600 lines; `jellyfin.css` approximately 1,800.
  Plex and Seerr also define their own colors and surfaces. A shared token layer
  can replace repeated gradients, translucent borders, and control treatments.
- Manual theme selection currently rewrites stylesheet media conditions through
  `_themeApplyToSheets()`. Replace this with explicit semantic theme tokens,
  retaining the existing `relaytv_theme` preference and Auto/Dark/Light choices.
- Four provider launchers compete for header space. A Browse destination gives
  them a stable home without crowding the remote.
- The checked-in phone screenshot gives large, similarly emphasized buttons
  most of the first screen. A compact transport makes room for the queue while
  keeping play/pause and mute easy to hit.
- Provider controllers already contain request cancellation, focus restoration,
  and keyboard behavior. Inventory and preserve these protections during reuse.
- Existing Node tests sometimes extract functions from source into VM contexts.
  Extraction must migrate those tests with the code rather than weaken them.
- Browser smoke exists for all four providers and peers; general remote,
  settings, and remaining dialogs need comparable end-to-end coverage.

## Design direction

Use calm neutral surfaces, crisp typography, modest rounding, and one blue/cyan
brand accent. Artwork supplies visual richness. Reserve prominent color for the
primary action and meaningful state; remove decorative glow and repeated glass
effects from ordinary controls. Keep the existing RelayTV logo, bundled provider
marks, unknown-provider fallback, and operator-provided brand assets.

These are proposed design values to validate in phase 0, not a new dependency:

| Foundation | Starting specification |
| --- | --- |
| Dark theme | Background `#0B1017`, surface `#151D28`, raised surface `#1C2735`, text `#F3F6FA`, secondary text `#A7B4C5` |
| Light theme | Background `#F4F6F9`, surface `#FFFFFF`, raised surface `#E9EEF5`, text `#152033`, secondary text `#526278` |
| Accent | Dark accent `#60A5FA`; light accent `#1D4ED8`; choose matching on-accent text and measure actual contrast |
| Type | Existing local/system font stack; 16px body, 14px secondary, 20–28px section/page headings; tabular playback times |
| Spacing | 4/8/12/16/24/32/48px scale; consistent page gutters and section rhythm |
| Shape | 8px controls, 12px cards, 16px dialogs; pills for compact status only |
| Interaction | At least 44×44 CSS-pixel product targets for touch controls; larger primary transport button |
| Motion | Short 120–180ms state transitions; respect reduced motion; avoid continuous shimmer/blur |
| Elevation | Flat content surfaces; limited shadow on floating menus, dialogs, and sheets |

Define semantic roles such as `--surface`, `--surface-raised`, `--text-primary`,
`--text-secondary`, `--border`, `--accent`, `--on-accent`, `--focus-ring`, and
status colors. Provider accents are scoped to identity, not separate themes.
Keep a documented layer scale for navigation, playback bar, popovers, and dialogs.

### Navigation and responsive layout

- Phone: a bottom navigation bar with **Remote**, **Browse**, and **Settings**.
  Keep Add media visible on Remote and the device name visible in the header.
  Keep the queue on Remote rather than duplicating it as a fourth destination.
- Tablet/desktop: the same destinations in a compact side rail; Remote uses
  now-playing/transport and queue columns when space permits. Browse gets the
  remaining width for its catalog.
- Browse: a provider chooser with remembered selection. Configure unavailable
  providers through Settings; show a useful setup action when none is enabled.
  Keep provider-specific tabs and search within the selected provider. Cross-
  provider search is a later product feature, not a requirement of this refresh.
- While browsing/settings, show a compact now-playing bar with play/pause and a
  return-to-remote action. It reads the shared playback snapshot. It never
  starts a second connection or a separate playback state machine.
- Use browser history for destination, provider, and detail navigation with a
  small hash-state scheme under `/ui`; Back unwinds details before leaving a
  provider. Do not place credentials or private provider references in history.
  Missing/stale detail state returns to its provider with an explanation.
- Begin with compact below 768px, intermediate 768–1199px, and wide at 1200px.
  Verify at 320, 390, 768, 1280, and 1920 CSS pixels, with phone landscape,
  safe-area insets, the virtual keyboard, and 200% zoom. Adjust breakpoints when
  content requires it; avoid horizontal page overflow and covered controls.
- Phase 0 defines the supported browser/runtime matrix before adopting module,
  CSS, or accessibility features: phone and desktop browsers used by the product,
  installed PWA mode, and the Qt WebEngine versions present in supported images.
  Record actual versions instead of assuming current evergreen behavior.

### Interaction improvements by surface

**Remote and queue.** Make play/pause the dominant control; group skip/back/seek,
volume, and mute in compact rows. Keep stop/close playback separate from closing
a dialog and use labels that accurately describe the underlying action. Render
unavailable seek/track/CEC controls with a reason. Retain drag reorder, stable
queue IDs, remove/undo timing, and menu alternatives; provide keyboard move
up/down actions. Queue updates preserve focus, scroll, and an active drag.
An empty queue offers Add media and Browse, not an empty container.

**Add media and history.** Use Link and File modes with explicit Play now and
Add to queue actions, validation near the field, and useful upload progress.
Keep entered data after failures and prevent repeated submission while pending.
Preserve clipboard fallbacks and share intent confirmation. Put Send notification
in a separate dialog accessible from Remote's secondary actions. History keeps
replay/requeue and clear actions with understandable results.

**Media browsers.** Share a page header, search field, filter controls, media
card, pagination/loading state, and detail layout. Preserve source-specific
behavior: Jellyfin/Emby resume and tracks, Plex account/server/library handling,
IPTV sources/favorites/availability, and Seerr seasons/request policy/identity.
Use bounded catalogs with an explicit Load more fallback; preserve query,
filters, scroll, and focused item when returning from detail. Avoid nested card
buttons or a full-card click target that swallows action controls.

**Settings.** Replace the very long modal with a settings destination: Playback,
TV control, Appearance & idle display, Media sources, Uploads & storage, and
About/diagnostics. Preserve every existing setting and default. Keep browser
appearance separate from device-wide idle configuration. Put integration
credentials, link/test/disconnect controls, and connection status together.
Use dirty-field tracking and a visible Save/Cancel area for editable groups;
keep explicit immediate actions distinct. Save only intended fields and handle
partial failures without claiming all settings were saved. Warn before discarding
unsaved edits. Search/filtering of settings can follow once grouping works.

**Dialogs and device transfer.** One overlay manager controls focus, escape,
scroll locking, stacking, and restoration. Use compact sheets on phones and
dialogs on larger displays; large catalog views remain navigation destinations.
Device transfer shows the destination name/address, selected item count, and
plain-language Send/Copy effect before submission. Preserve peer selection during
realtime updates and show per-device failure/retry results without replaying a
successful transfer.

**TV screens.** Adapt the design for viewing across a room, with larger type and
safe margins. Keep time/weather as the idle focal point; device identity and a
scannable QR are secondary. Keep configurable positions/sizes and graceful
weather/QR failure states. Notifications need legible text, bounded wrapping,
image fallback, existing duration/position semantics, and unobtrusive animation.
Cover browser and native Qt rendering separately: browser CSS cannot style Qt
widgets. Preserve video transparency, click-through behavior, and idle/playback
visibility transitions. Do not add a full TV catalog or change the runtime engine.

**Feedback and accessibility.** Each surface defines initial loading, ready,
empty, pending, rejected, disconnected, and retry states. A failed or timed-out
write is never displayed as confirmed success. Reconnection feedback preserves
the view and does not flood notifications. Use semantic buttons/labels, visible
focus, text alongside status color, and polite announcements for command results
without announcing every playback tick. Modal focus stays inside, Escape closes
the top dialog, and closing restores a logical focus target, following the
[WAI dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/).
The 44px touch target above is a product goal above the WCAG 2.2 AA
[24px target-size minimum and exceptions](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html).
Measure contrast and test keyboard/screen-reader use before making accessibility
conformance claims.

**Authorization recovery.** Replace the native API-token prompt with a labeled,
focus-managed RelayTV dialog that explains why control actions are locked. Keep
the browser credential separate from device settings and offer explicit retry,
cancel, replace, and forget actions. Retry a write only after the server returned
its Bearer challenge, because middleware rejects that request before route code
can mutate state. Canceling or dismissing the dialog leaves the command rejected
and never replays it later.

## JavaScript and asset architecture

Retain FastAPI and plain static assets. There is no demonstrated need for a
framework rewrite or a production Node build. Start by extracting tested
controllers behind an explicit `RelayTV` namespace; move toward browser ES
modules only after script ordering, test loading, and asset versioning work in
the supported browsers/embedded hosts. Module loading has different scope and
serving requirements; use the [MDN module guide](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Modules)
when validating that transition. A module conversion is not a prerequisite for
shipping the design.

Proposed ownership within `static/ui/`:

| Files | Responsibility |
| --- | --- |
| `tokens.css`, `base.css`, `components.css` | Theme roles, typography, shared controls, states, dialogs, and layers |
| `app.css` and provider CSS | Layout and remaining feature-specific presentation |
| `core/api.js` | Explicit same-origin auth, request timeout/cancellation, normalized outcomes |
| `core/theme.js` | Auto/Dark/Light, initial theme bootstrap, preference compatibility |
| `core/navigation.js`, `core/overlays.js` | Destination/back behavior; dialog stack and focus lifecycle |
| `core/store.js` | Read-only server snapshots plus separate transient UI state/subscriptions |
| `components/` | Reusable rendering and interaction primitives with explicit inputs/callbacks |
| `remote.js`, `queue.js`, `settings.js`, `history.js`, `media-input.js` | Extracted feature controllers with mount/unmount cleanup |
| Existing provider and peer controllers | Provider-specific interaction and public API adaptation |
| `app.js` | Bootstrap, dependency wiring, and temporary compatibility bridges |
| `realtime_transport.js` | Existing capability negotiation and notification transport |

Implementation rules:

1. Move one feature at a time, preserving selectors/global adapters while callers
   and tests migrate. Remove a bridge only after its last consumer is migrated.
   Keep provider product decisions in their existing Python services.
2. Replace the global fetch wrapper only after all browser callers use the
   explicit request adapter. Preserve same-origin bearer handling and tokenless
   operation. The existing browser-entered token is distinct from server config:
   server `RELAYTV_API_TOKEN` remains env-only and never enters `/settings`,
   logs, query strings, or the general UI store. Redact credentials from errors
   and screenshots; preserve provider and peer credential boundaries. The auth
   controller owns the one challenged request awaiting a user decision and does
   not turn general network recovery into a write retry.
3. Distinguish rejected writes from transport failures with unknown server
   outcome. Do not automatically replay non-idempotent writes on reconnection.
   Preserve the current safe unauthorized-request handling during extraction.
4. Keep WebSocket/SSE/polling fallback and read-only socket semantics. Use one
   shared subscription path; unsubscribe views and cancel obsolete reads on
   close/navigation. Generation checks prevent late responses from repainting a
   new view. Client abort is not a guarantee that a server write was canceled.
5. Use stable IDs and targeted DOM updates. Do not rebuild a slider, selected
   queue, focused card, or form on each status tick. Escape untrusted text and
   retain safe artwork/reference handling.
6. Replace CSS media rewriting with root theme attributes and semantic tokens;
   migrate provider hardcoded colors before retiring the old helper. Preserve
   stored preference, system changes in Auto, initial paint, and PWA theme color.
7. Extract generated UI/idle/overlay markup into packaged template/static files
   in a separate mechanical change. Keep endpoint ownership/aliases stable and
   preserve all substitutions. Verify assets are copied into the released image
   under `app/relaytv_app`; version every new asset through `_ui_asset_version()`
   or an equivalent coherent scheme, including module imports if adopted.
8. The current service worker has no offline asset cache. Preserve that behavior
   unless offline support is separately designed. Do not imply controls work
   offline or introduce stale HTML/JS mixtures during deployment.
9. Native Qt uses a small presentation constants/helper module matching the
   browser design specification. Keep lifecycle/process logic in place; do not
   make Qt depend on browser CSS or frontend tooling at runtime.
10. Preserve bundled provider SVG selection, self-hosted provider aliases, and
    the local unknown-provider fallback. Do not restore third-party favicon
    requests or allow lookalike hosts to claim a known service mark. Preserve
    `RELAYTV_LOGO_PATH`, `RELAYTV_BANNER_PATH`, `RELAYTV_LOGO_IMAGE`, and
    `/data/assets` precedence when reorganizing brand and PWA assets.

## Delivery sequence

Each row is a separately reviewable milestone within the same branch and pull
request. Keep checkpoints independently testable and update the pull request
description as its final squash grows. Effort is a relative planning estimate,
not a schedule commitment.

| Phase | Work and principal files | Dependency / effort | Exit evidence |
| --- | --- | --- | --- |
| 0 — Baseline and design | Inventory every control/setting, auth recovery, provider/brand asset path, and supported browser/Qt runtime; capture live surfaces and states; create phone/desktop/TV wireframes and a small HTML/CSS component specimen | First / M | Coverage checklist, browser/runtime matrix, current screenshots, proposed layouts, baseline JS/CSS bytes and browser performance; no missing features |
| 1 — Shared visual foundations | Add tokens/base/components; extract theme handling; adapt shared buttons/forms/status and one representative dialog | 0 / M | Auto/Dark/Light across sample remote/provider/dialog; contrast, focus, target-size, reduced-motion checks; no legacy theme regressions |
| 2 — Controller seams and markup | Extract API/overlay/state/navigation helpers, remote/settings responsibilities, and packaged markup in separate changes; update asset loading and tests | 1 / L | Behavior stays equivalent during extraction; all callers use the intended adapters; packaged assets load; request/focus/cleanup regressions covered |
| 3 — Remote and app navigation | New responsive shell, transport, queue, Add media, history, tracks, notification composer, compact playback bar | 2 / L | Play/add/reorder/remove/undo/history/upload/share work through real browser interactions; controls remain reachable on phones |
| 4 — Browse consistency | Migrate Jellyfin/Emby first, then Plex, IPTV, Seerr onto shared header/cards/details/status; preserve each adapter | 3 / L, split by provider | Each existing provider smoke passes plus shared theme/back/focus/error cases; disabled and unconfigured provider cases pass |
| 5 — Settings and devices | Settings destination, complete field mapping, save/discard behavior, integration setup, peer sheet | 3; final styling after 4 / L | Every prior setting accounted for; save/link/test/disconnect/Quick Connect and send/copy failure handling verified |
| 6 — TV presentation | Browser idle/overlay assets, startup splash/brand fallbacks, and native Qt visual constants/layout; loading/weather/QR/toast parity | 1–2; integrate with 5 idle settings / L | Browser captures plus actual native device evidence; cold-start splash/playback, operator branding, idle return, overlay transparency and QR readability verified |
| 7 — Hardening and release | Accessibility/responsive/performance matrix, stale-asset update checks, remove obsolete styles/bridges, refresh screenshots/docs | 3–6 / M | Full gates pass, all inventory entries covered, device sign-off evidence and release context recorded |

All phases remain on `docs/ui-modernization-plan` and pull request #99 by user
request. Keep provider behavior changes in clearly identified commits and avoid
combining unrelated product changes with a visual migration. Before merging,
refresh the branch from current `main`, resolve overlapping fixes deliberately,
and rerun the entire gate and device matrix against the resulting squash.

## Validation and acceptance

Phase 0 produces a checklist with one row per surface/state, screenshot, test,
and any hardware requirement. Capture dark/light remote, every provider's browse
and detail views, settings groups, dialogs, peer selection, browser idle/overlay,
and native idle/notifications. Use deterministic fixture content for repeatable
browser captures, with long titles, missing artwork, empty lists, and slow or
failed responses. Keep separate real-device integration checks.

Extend the existing `scripts/{jellyfin,plex,iptv,seerr,peers}-ui-smoke.js` scripts;
add remote/settings/dialog browser scenarios and a reproducible fixture runner.
Record the Playwright/browser version and setup command so CI can reproduce
them. Prefer role/name selectors for user actions; update existing selectors
deliberately, never delete assertions just to make a redesign pass.

Required behavioral scenarios:

- Remote idle/playing/paused/live/unseekable; failed play/queue/seek/track writes;
  remove/undo/reorder while snapshots arrive; upload failure and retry.
- Navigation and dialogs using touch, keyboard, and browser Back; nested detail
  dismissal; focus restoration after the originating item disappears; unsaved
  settings and virtual keyboard without hidden buttons.
- Provider search A completing after search B, closing during a request, repeated
  open/close, pagination bounds, unavailable integrations, auth expiry, and
  Seerr request policy. Late results cannot overwrite the active view.
- WebSocket operation, SSE fallback, legacy capability `404`, polling fallback,
  disconnect/reconnect, and phone background/resume. No duplicate listeners,
  subscriptions, timers, or automatically replayed writes.
- Protected and tokenless API control; canceled token entry; credential-safe
  errors; challenged writes retry at most once after explicit token entry, and
  replace/forget behavior is verified. Peer send/copy retains correct identity,
  item selection, and outcome.
- Bundled provider marks cover known and self-hosted aliases without network
  favicon requests; lookalike and unknown hosts keep the local fallback. Default
  and operator-provided logo/banner/splash assets retain their precedence.
- Light/dark/Auto including system changes and retained browser preference;
  reduced motion, visible focus, labeled icons, screen-reader announcements,
  200% zoom, narrow screens, touch/keyboard equivalents for drag gestures.
- Fresh and already-open PWA/browser sessions across an update and rollback;
  share target still prepopulates without unexpectedly starting playback.
- Browser and native TV idle/weather/QR/notification states at 720p, 1080p, and
  4K where supported; real scan and couch-distance reading checks.

Record initial transfer bytes, number of requests, layout shifts, long tasks
during queue/catalog updates, and memory after repeated navigation on the same
device and dataset. Phase 0 names that reference device, OS, browser/runtime,
and dataset. Proposed budget: no more than 10% initial JS/CSS transfer
growth without a documented reason; UI processing should acknowledge input
within 100ms on the reference device, independently of network completion.
Do not invent baseline numbers. Keep catalogs bounded and load artwork lazily
with reserved dimensions. Test low-power hardware before adding costly effects.

Run the repository's required gates for every change:

```sh
ruff check app tests
PYTHONPATH=app pytest -q
git diff --check
node --check app/relaytv_app/static/ui/app.js
node --check app/relaytv_app/static/ui/realtime_transport.js
PYTHONPATH=app python3 -c "import re; from relaytv_app.routes import x11_overlay_page; print(re.findall(r'<script>(.*?)</script>', x11_overlay_page().body.decode(), re.S)[0])" | node --check -
node --test tests/js/*.test.js
```

Use the CI dependency installation (`pip install -e '.[dev]'`) in an isolated
environment when needed; record the environment and versions with results.
Also syntax-check every new JS file and any other generated inline scripts.
When overlay JS moves out of HTML, update its generated-script gate so it
verifies the actual served code rather than an empty extraction. Lifecycle,
concurrency, and boundary fixes require a regression test that fails when the
production guard alone is reverted, as required by `AGENTS.md`.

On-device validation uses the released image with this checkout bind-mounted
over `/app/relaytv_app`. Follow `AGENTS.md` device precautions: retain each
device's generated override, `.env`, data and binaries; retain its runtime mode;
read live state through HTTP. Exercise an actual cold start, play/pause, seek,
next, close, and return to idle before claiming playback compatibility. A browser
screenshot or a green unit suite alone does not verify native Qt behavior.

Completion means every surface in the inventory has migrated, existing actions
and settings remain available, all required checks pass, and residual hardware
or accessibility limitations are explicitly recorded rather than silently waived.

## Rollout, risks, and release context

Use small, reviewable commits that leave a working application at each
checkpoint. Record before/after evidence and the milestone checklist in the
unified PR. Baseline tests land before behavioral extraction; provider migration
proceeds one provider at a time. If a milestone is unstable, revert its commits
and directly dependent changes; retain compatibility adapters until dependent
migrations are complete. No new persistent setting or API migration is expected,
making rollback simpler.

| Risk | Mitigation |
| --- | --- |
| Shared CSS breaks an unmigrated provider | Scope tokens/components, migrate incrementally, compare all provider themes at each foundation change |
| New script layout breaks globals or cached assets | Explicit dependency order, temporary bridges, image asset checks, browser update/rollback scenarios |
| Refactor drops concurrency protections | Inventory existing guards, drive real interleavings in tests, perform required revert proof |
| Smaller remote loses usability | Validate 44px targets and direct access to common controls using actual phones |
| Settings redesign changes partial-save behavior | Map all fields and immediate actions; assert submitted payloads and failure results |
| Browser and native TV diverge | Maintain one visual specification with separate renderer acceptance evidence |
| Scope grows into a product rewrite | Defer unified search, new integrations, offline control, backend rewrites, and companion redesigns |

Phase 0 has no runtime user or operator impact and no breaking changes. Its
checked-in capture script, sanitized screenshots, runtime matrix, measurements,
coverage inventory, and component specimen establish the comparison point for
later implementation.

Planning-change verification (September 13, 2026): `ruff check app tests`,
`git diff --check`, both required static JS syntax checks, and the generated
X11 overlay script syntax check passed. The Python suite passed **1,134 tests**;
the Node suite passed **54 tests**. Plan structure, referenced existing assets
and smoke scripts, the docs index link, and whitespace were checked. This run
used local FastAPI **0.118.0** and pytest **9.0.2**, not a fresh CI dependency
installation; it does not establish compatibility with every version allowed
by `pyproject.toml`. No UI implementation, browser smoke, or device playback
validation was performed for that documentation-only checkpoint. The linked
phase 0 evidence supersedes that limitation where it records exact runtime and
browser measurements.

The unified implementation PR uses a Conventional Commit title and includes user
impact, operator/deployment impact, breaking changes or **None**, and full test
and device evidence. The requested patch target is `0.11.1`; its release lead-in
lives in `docs/release-highlights/0.11.1.md`. Keep the final PR title and squash
metadata release-producing, and add `Release-As: 0.11.1` to the final squash
only if other merged work would make Release Please choose a different version.
Leave version files, tags, `CHANGELOG.md`, and the release PR to Release Please.
Regenerate machine-checked inventories only if an intentional public, config, or
runtime surface change requires it.
