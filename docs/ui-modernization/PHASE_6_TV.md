# Phase 6 TV presentation

Phase 6 aligns the browser TV surfaces (`/idle` and `/x11/overlay`) and the
native Qt fallback layers (`_NativeIdleLayer` and `_NativeToastOverlay` in
`qt_shell_app.py`) on a shared dark visual specification defined in
`app/relaytv_app/tv_theme.py` and matching `foundation.css`.

## Delivered behavior

- **`app/relaytv_app/tv_theme.py`**: Pure-Python TV presentation module (zero
  Qt/PySide6 import requirement) defining the dark semantic palette
  (`#0b1017` background, `#111824` secondary background, `#151d28` surface,
  `#1c2735` raised surface, `#f3f6fa` primary text, `#a7b4c5` secondary text,
  `#8999ad` tertiary text, `#60a5fa` accent, `#34d399` success, `#fbbf24`
  warning, `#fb7185` danger) and QSS helper functions for native Qt toast and
  idle widgets.
- **Native Qt parity (`app/relaytv_app/qt_shell_app.py`)**: Wired native toast
  (`qt-native-toast`, link, and image) and native idle (`qt-native-idle-rail`,
  clock, date, divider, weather panel, forecast strip, device footer, and QR
  card) stylesheet blocks to `tv_theme` helpers without altering any Qt
  lifecycle, window flag, libmpv, watchdog, IPC, or cursor behavior.
- **Browser `/idle` (`app/relaytv_app/static/ui/idle.html`)**: Adopted shared
  dark semantic tokens, room-distance tabular typography, safe-area inset
  margins, restrained surface dividers, and a high-contrast QR card while
  preserving all template substitutions, `escIdleHtml()`, 20s settings cache,
  DOM-write guards in `tick()`, weather caching, and session state handling.
- **Browser `/x11/overlay` (`app/relaytv_app/static/ui/x11-overlay.html`)**:
  Adopted shared dark semantic tokens and restrained toast chrome while
  preserving transparent background, `pointer-events: none` click-through,
  `X-RelayTV-Overlay-Token` authentication, `safeLinkUrl` and `safeImageUrl`
  protocol filtering, viewport-proportional toast scaling, and WebSocket/SSE
  `RelayTVRealtime` transport.

## Verification

Run the deterministic Phase 6 checks with:

```text
ruff check app tests
PYTHONPATH=app pytest -q tests/test_tv_theme.py tests/test_smoke.py -k "tv_theme or overlay or idle or browser_documents"
PYTHONPATH=app python3 -c "import re; from relaytv_app.routes import x11_overlay_page; print(re.findall(r'<script>(.*?)</script>', x11_overlay_page().body.decode(), re.S)[0])" | node --check -
NODE_PATH=/home/mark/.npm-global/lib/node_modules node scripts/ui-phase6-tv.js --base=http://127.0.0.1:8793
```

The Playwright E2E runner (`scripts/ui-phase6-tv.js`, report in
`docs/ui-modernization/phase-6-tv.json`) verifies `/idle` and `/x11/overlay` in
headless Chromium across `1280x720` (720p), `1920x1080` (1080p), and
`3840x2160` (4K), checking zero horizontal/vertical overflow, clock/weather/QR
visibility and non-overlap, overlay transparency and click-through, proportional
toast scaling (`0.75x`, `1.0x`, `2.0x`), and rejection of `javascript:` and
protocol-relative `//` toast links.
