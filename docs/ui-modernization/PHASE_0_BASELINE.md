# UI modernization phase 0 baseline

Status: in progress. First capture: September 13, 2026.

This is the working evidence record for phase 0 of the
[UI modernization plan](../UI_MODERNIZATION_PLAN.md). All UI planning and
implementation stays on `docs/ui-modernization-plan` and pull request #99 until
the complete refresh is ready. The intended Release Please target is `0.11.1`.

## Reference environments

The measurements below identify the exact runtime used. They are comparison
anchors, not compatibility claims for untested browsers or devices.

| Surface | Hardware and OS | RelayTV runtime | Browser/runtime | Phase 0 use |
| --- | --- | --- | --- | --- |
| Browser capture | `nuc.lan`, x86_64 Linux 7.0 host | released `v0.11.0` image at `2757075`; Debian 13; Python 3.13.13 | Playwright 1.62.0, Chromium 151.0.7922.34 | Current remote, dialogs, idle, asset and timing baseline |
| Native/embedded reference | `nuc.lan`, x86_64 | same released image | PySide6/Qt 6.11.2; QtWebEngine Chromium 140.0.7339.225 | Runtime recorded; compositor does not expose the screenshot protocol needed for a live-frame capture |
| Pi device reference | `raspi.lan`, aarch64 | released `v0.11.0` image at `2757075`; Python 3.13.13 | PySide6/Qt 6.11.2; QtWebEngine Chromium 140.0.7339.225 under labwc | Reviewed native 3840×2160 idle frame; active playback, notifications and input remain |
| Phone browser | Physical phone still required | current branch bind-mounted over a released image | Record browser and OS at validation time | Touch, safe area, virtual keyboard, PWA and background/resume |
| Safari/WebKit | Physical Apple device still required | current branch bind-mounted over a released image | Record browser and OS at validation time | CSS, dialog/focus, share target, installed PWA |
| Firefox | Playwright fixture on `nuc.lan` | released `v0.11.0` server | Playwright 1.62.0, Firefox 153.0 | Current phone/desktop layout, menu keyboard path, settings dialog, theme, provider and fallback transport smoke |
| WebKit engine | Playwright fixture on `nuc.lan` | released `v0.11.0` server | Playwright 1.62.0, WebKit 26.5 | Same deterministic smoke as Firefox; this is engine evidence, not physical Safari or installed-PWA evidence |

The implementation support rule is the current and previous major release of
Chrome/Chromium, Safari/WebKit, and Firefox at final validation, plus the exact
QtWebEngine version shipped in the release image. A feature may land only when
it works in those engines or has an explicit, tested fallback. This rule avoids
an evergreen-only design while allowing the matrix to age with the release.

## Reproducing the browser baseline

Run a released RelayTV server on port 8787, install Playwright outside the
production project, then run:

```sh
NODE_PATH=/path/to/global/node_modules \
  node scripts/ui-phase0-baseline.js \
  --base=http://127.0.0.1:8787
```

The script records exact server and browser versions, source/gzip asset sizes,
resource timing, paint timing, layout shift, long tasks, interactive target
sizes, a menu-response probe, and sanitized screenshots. It clears text/file
input values and hides the idle QR, device label, and weather location before
capturing. It makes no playback, queue, settings, or integration writes.

Use the same host, browser, server state, and dataset for before/after runs.
Loopback timings are diagnostic and must not be described as field performance.
The machine-readable first run is in
[`phase-0-baseline.json`](phase-0-baseline.json).

The state fixture runner records the remaining current surfaces without making
server writes or reading private provider data:

```sh
NODE_PATH=/path/to/global/node_modules \
  node scripts/ui-phase0-states.js \
  --base=http://127.0.0.1:8787

NODE_PATH=/path/to/global/node_modules \
  node scripts/ui-phase0-states.js \
  --base=http://127.0.0.1:8787 \
  --browser=firefox \
  --mode=compatibility \
  --report=docs/ui-modernization/phase-0-firefox.json
```

Use `--browser=webkit` for the WebKit run after installing Playwright's WebKit
runtime dependencies. The runner blocks service workers so their caches cannot
bypass the request fixtures. Its full mode intercepts status, playback,
provider, history, track and peer reads; command failures and authorization
challenges are intercepted too. The only persisted credential is a fixture
token inside a disposable browser context.

## Initial measurements

Thirteen current UI JavaScript and CSS files total **549,241 source bytes** and
**126,414 gzip bytes**. Every provider controller and stylesheet is loaded by
the remote shell even when its launcher is unavailable. This is the comparison
point for the plan's 10% transfer-growth budget; it also makes route-level or
deferred loading worth evaluating during later phases.

| Probe | 390×844 dark phone | 1280×800 light desktop |
| --- | ---: | ---: |
| DOM content loaded | 145 ms | 119 ms |
| First contentful paint | 196 ms | 244 ms |
| Menu visual acknowledgement | 8.3 ms | 83.1 ms |
| Cumulative layout shift after initial updates | 0.115 | 0.080 |
| Long tasks | one, 71 ms | none |
| Visible interactive controls | 11 | 11 |
| Visible controls below the 44px product target | 5 | 5 |
| Document size | 390×844 | 1280×890 |

The phone's visible small targets are the provider launchers, Add URL, Menu,
and the 30px-high volume slider. On desktop the same control heights remain
below the product target. All visible controls in this initial remote state had
an accessible name, which is behavior to preserve.

These timings came from an idle, local released server with no network or CPU
throttling. The initial phone layout shift and observed long tasks deserve follow-up,
but the source of each must be isolated before setting a fix target. The menu
probe is already below the proposed 100ms acknowledgement budget in this run.

## Checked-in artifacts

- Current UI: [phone dark](../images/ui-phase-0/current-phone-dark.png),
  [desktop light](../images/ui-phase-0/current-desktop-light.png),
  [settings dark](../images/ui-phase-0/current-settings-dark.png),
  [Add media dark](../images/ui-phase-0/current-add-media-dark.png), and
  [TV idle dark](../images/ui-phase-0/current-tv-idle-dark.png).
- Proposed hierarchy: the standalone
  [HTML/CSS component specimen](phase-0-specimen.html) and its
  [rendered overview](../images/ui-phase-0/proposed-specimen.png).
- Raw measurements: [phase-0-baseline.json](phase-0-baseline.json).
- Deterministic state evidence: [state assertions](phase-0-states.json) and the
  [17 fixture screenshots](../images/ui-phase-0/states/), covering populated
  playback and queue, item actions, live media, history, tracks, peer transfer,
  provider shells and detail views, connection failures, reconnecting, and the
  TV overlay.
- Browser-engine evidence: [Firefox 153](phase-0-firefox.json) and
  [WebKit 26.5](phase-0-webkit.json).

The specimen is a design probe, not shipped UI. It deliberately uses the
proposed semantic token roles, 44px controls, compact transport, queue metadata,
phone bottom navigation, desktop rail, shared state components, API-token
recovery dialog, and TV safe area. Phase 1 may adjust its values when measured
contrast and real-device checks justify a change.

The proposed core text pairs pass an initial WCAG contrast calculation:

| Pair | Contrast ratio |
| --- | ---: |
| Dark primary text / background | 17.60:1 |
| Dark primary text / surface | 15.65:1 |
| Dark secondary text / surface | 8.06:1 |
| Dark on-accent text / accent | 7.45:1 |
| Focus ring / dark background | 10.58:1 |
| Light primary text / background | 15.08:1 |
| Light primary text / surface | 16.33:1 |
| Light secondary text / surface | 6.22:1 |
| Light on-accent text / accent | 6.70:1 |

These ratios validate the starting token pairs only. Each rendered component
still needs state-specific checks for borders, icons, focus, disabled controls,
artwork overlays, and browser forced-color behavior.

## Current layout findings

- At 1280×800 the document is 890px tall. The transport panel expands to use
  much of the desktop column, pushing its lower controls and queue content below
  the first viewport. Wide layouts should use width to expose content rather
  than scaling the transport tiles vertically.
- At 390×844 the complete idle transport fits, but only the top of the queue is
  visible. The proposed compact transport must preserve direct Play/Pause,
  Next, Mute, Close, seek, and volume access while giving queued metadata more
  of the first screen.
- The header can show four provider launchers plus Add and Menu. Enabled
  providers compete with the device identity; compact mode already hides the
  text labels. The proposed Browse destination gives provider identity a stable
  location and leaves the app header predictable.
- The settings dialog contains the complete operator surface, but its length
  makes related connection state, credentials, and Apply/Test actions difficult
  to scan. A settings destination can improve grouping without changing field
  ownership or partial-save behavior.
- Remote, provider, and peer controllers define their own cards, state blocks,
  focus behavior, and colors. Shared components should begin as visual and
  interaction contracts around these proven controllers.
- The current API-token recovery is a native browser prompt. It has no product
  focus lifecycle, explanatory copy, replace/forget surface, or screenshotable
  state. The first-party dialog remains a required implementation scenario.

## Device and browser evidence

The current Pi native shell was reviewed from a live 3840×2160 frame on
September 13, 2026. The released `v0.11.0` container was running the Qt shell
under labwc with PySide6/Qt 6.11.2 and QtWebEngine Chromium 140.0.7339.225. The
idle clock, date, device identity and status are readable at TV distance, but
the composition leaves most of the canvas unused. Weather and QR were absent
under the device's current configuration, so those configured states still
need physical validation. The raw frame remains outside the repository because
it contains the operator's real device name.

The NUC runtime versions are recorded, but its compositor does not implement
the `wlr-screencopy` protocol used by `grim`; a live native frame could not be
captured there. NUC native visuals therefore remain open rather than being
inferred from the browser or Pi.

Firefox 153.0 and WebKit 26.5 each passed the deterministic 390×844 and
1280×800 compatibility smoke. Both runs confirmed no horizontal overflow, the
dark-theme media query, queue action labels, Escape dismissal, the settings
dialog role, and a populated Jellyfin shell. These results describe the current
released UI. Physical Safari, touch, safe areas, virtual-keyboard behavior,
PWA installation and resume from the background remain separate checks.

## Coverage inventory

Every current control group is assigned below. “Recorded” means source and live
browser evidence exist; it does not mean the surface has been redesigned.

| Surface or state | Current controls and behavior that must survive | Baseline evidence | Later acceptance owner |
| --- | --- | --- | --- |
| Header and theme | Device identity; IPTV, Jellyfin/Emby, Plex, Seerr launchers; Add URL; History; About; Settings; Auto/Dark/Light | Recorded in source and phone/desktop captures | Phases 1 and 3 |
| Connection feedback | reconnect badge; WebSocket, SSE and polling fallback | Command failure and deterministic failed-refresh reconnect captures; existing transport tests | Phases 2 and 7 |
| Idle remote | now-playing empty state; Play/Pause, Next, Mute, Close, −10s, +30s, volume; empty queue | Recorded in dark phone and light desktop captures | Phases 1 and 3 |
| Active playback | artwork/provider metadata; state tag; progress/seek; audio/subtitle; up-next/skip | Deterministic playing and live/unseekable captures | Phase 3 |
| Queue | count; Send; Clear; item play, menu, remove, undo, drag and keyboard move | Populated capture includes URL, uploaded-media and unavailable items; action menu recorded; source and unit tests cover remaining behavior | Phase 3 |
| Add media | link, paste, Queue, Play, notification image selector and notification composer | Link and notification composer recorded; active overlay recorded | Phase 3 |
| History and tracks | replay/requeue, clear; audio and subtitle selection dialogs | Populated history, audio and subtitle captures | Phase 3 |
| Peer transfer | Send/Copy mode, selected items, add/test/save peer, all/none and submit | Deterministic peer list and transfer capture | Phase 5 |
| Settings: playback | device name, audio device, quality, subtitles, CEC and TV takeover/input/return behavior | Recorded in source and sanitized settings capture | Phase 5 |
| Settings: resolver | yt-dlp auto-update; Invidious toggle/base; cookie upload/clear | Recorded in source and sanitized settings capture | Phase 5 |
| Settings: idle | dashboard, notifications, QR, QR size, weather days/city/find | Recorded in source and sanitized settings capture | Phases 5 and 6 |
| Settings: storage | upload size and retention | Recorded in source and sanitized settings capture | Phase 5 |
| Settings: IPTV | enable and Apply | Recorded in source and sanitized settings capture | Phase 5 |
| Settings: Jellyfin/Emby | enable, server, client login, password clear, user ID, shared cast, API key clear, track languages, playback mode, Apply, clear cache | Recorded in source and sanitized settings capture | Phase 5 |
| Settings: Plex | enable, link/poll/cancel/unlink, server, playback mode, bitrate, Apply and Test | Recorded in source and sanitized settings capture | Phase 5 |
| Settings: Seerr | enable, server, API key clear, request mode/user, Apply and Test | Recorded in source and sanitized settings capture | Phase 5 |
| IPTV browser | My Channels search/favorite/play, Discover filters/refresh/load, source add/remove/directory | Deterministic current channel capture plus existing smoke and source | Phase 4 |
| Jellyfin/Emby browser | Dashboard/Movies/TV, search/sort, cards, series/episodes, details, resume/play/queue and track choices | Deterministic dashboard and detail captures plus existing smoke, README image and source | Phase 4 |
| Plex browser | Home/Libraries, search/load, server/library selection, details, resume/play/queue | Deterministic home and detail captures plus existing smoke, README image and source | Phase 4 |
| Seerr browser | Discover/Movies/TV/Requests, search/filter/load, Quick Connect, details/request/playback | Deterministic discover and detail captures plus existing smoke and source | Phase 4 |
| Browser idle | time/weather/device/QR, missing weather and QR behavior | Recorded in sanitized 1600×900 capture | Phase 6 |
| Browser overlay | notification text/image, duration and position; transparent idle state | Deterministic active-notification capture, source and syntax gate | Phase 6 |
| Native TV | splash, idle/time/weather/QR, notifications, transparency/click-through and playback transition | Pi native idle reviewed at 3840×2160; active Pi and NUC checks pending | Phase 6 |
| PWA/share/update | manifest/theme assets, install mode, share prefill, service worker, open-session update/rollback | Source inventory; physical installed-PWA evidence pending | Phases 1, 3 and 7 |
| API authorization | tokenless operation; Bearer challenge; retry/cancel/replace/forget; credential redaction | Deterministic challenged-write accept/retry and cancel paths recorded; proposed dialog remains | Phase 2 |

## Proposed wireframe and component evidence

[`phase-0-specimen.html`](phase-0-specimen.html) is a standalone, nonproduction
HTML/CSS specimen. It applies the proposed tokens to:

- phone navigation, compact now playing, transport and queue;
- desktop rail, two-column remote and persistent now-playing bar;
- shared buttons, status, inputs, queue rows, media card and dialog treatment;
- TV idle hierarchy and safe-margin treatment.

It contains no application JavaScript and does not claim finished interaction
or accessibility. Its purpose is to make Phase 1 token and component decisions
reviewable before production selectors or controllers change.

## Phase 0 exit checklist

- [x] Record the released server, host, browser, Qt and QtWebEngine versions.
- [x] Add a reproducible, read-only browser capture and measurement script.
- [x] Record initial JS/CSS source and gzip bytes.
- [x] Capture current phone/desktop remote, settings, Add media and browser idle.
- [x] Inventory current control groups and assign later acceptance ownership.
- [x] Produce phone, desktop and TV wireframes plus a shared component specimen.
- [x] Capture deterministic playing, live, unseekable, queue, uploaded-media,
  history and track-selection states.
- [x] Capture deterministic current provider and peer surfaces without private
  library, account, device, or network data.
- [x] Exercise current auth challenge, reconnect and active overlay states.
- [x] Record Firefox and Playwright WebKit engine evidence.
- [x] Record a live Pi native idle frame and exact embedded runtime.
- [ ] Record physical phone Safari/PWA evidence.
- [ ] Record NUC native visuals and Pi active playback, notification and input
  evidence.

Phase 1 must not retire current CSS or theme behavior while an unchecked item
could expose an unrecorded control or compatibility constraint. The unchecked
items may be completed alongside the fixture runner at the start of Phase 1,
but their acceptance rows remain blocking for removal of legacy behavior.
