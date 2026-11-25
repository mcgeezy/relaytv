# Shared RTSP Link Playback Plan

Enable playing shared `rtsp://` and `rtsps://` links (IP cameras, NVRs,
`go2rtc` / Frigate / Scrypted / UniFi Protect restreams, and Home Assistant
camera feeds) directly on RelayTV through `/share`, `/smart`, `/play`,
`/play_now`, `/play_temporary`, `/enqueue`, and `/ui`.

Status: proposed, not implemented. Per `docs/README.md` (Documentation rule),
engineering plans stay out of the public release docs tree — this file is
branch/PR-scoped and should be folded into implementation PR bodies (with
durable user/API behavior documented in `docs/API.md`) rather than merged to
`main` as a permanent plan doc.

---

## 1. Grounding Facts & Current Blockers

RelayTV's runtime image already ships everything needed to decode RTSP/RTSPS
natively without server-side transcoding or a local `ffmpeg` relay:

- `app/Dockerfile` installs `mpv`, `libmpv2`, and `ffmpeg` (`libavformat`
  includes RTSP and RTSPS demuxers for H.264, HEVC, MJPEG, AAC, Opus, and
  G.711 PCMU/PCMA).
- `docker-compose.yml` runs with `network_mode: host`, so both TCP-interleaved
  and UDP RTP transports work out of the box on LAN deployments.
- `public_media.sanitize_public_url()` already uses `url_boundary.parse_url()`
  and rebuilds `ParsedUrl.netloc` without `username` or `password`, and
  `/queue/play`, `/history/play`, and `/history/requeue` already replay from
  the server's unredacted stored item by ID/index rather than trusting
  display-safe client URLs.

Today, shared RTSP links fail across five concrete boundaries:

| Surface | File | Current Blocker |
| --- | --- | --- |
| **1. URL extraction & input validation** | `app/relaytv_app/resolver.py` (`extract_first_url`, `validate_user_url`) | `extract_first_url()` matches `https?://\S+` only (case-sensitive), and `validate_user_url()` rejects schemes outside `("http", "https")`. |
| **2. Web UI & PWA share target** | `app/relaytv_app/static/ui/app.js` (`looksLikeUrl`, `normalizeUrl`, `consumeShareParam`) | `looksLikeUrl()` accepts only `^https?:\/\/` or `^www\.`, blocking both the `/ui` Add Link dialog and `GET /share?url=rtsp://…` redirects. |
| **3. `yt-dlp` subprocess & hook stalls** | `app/relaytv_app/resolver.py` (`make_item`, `enrich_item_metadata`), `app/relaytv_app/player.py` (`play_item`, `_build_mpv_args`) | Non-YouTube items invoke `ytdlp_info()` + `resolve_title()` (up to two 20s subprocess timeouts before playback starts) and leave mpv's `ytdl_hook` enabled. |
| **4. Credential redaction gaps** | `app/relaytv_app/public_media.py`, `app/relaytv_app/player.py`, `app/relaytv_app/routes/playback.py` | Camera URLs frequently embed `rtsp://user:pass@host/…` or `?user=…&password=…`. Non-URL string redaction in `public_media_item()`, `_redacted_mpv_args()`, debug logs, and fallback toast labels only recognize `http(s)://`, and `_SENSITIVE_QUERY_KEYS` lacks `user`/`password` parameter names. |
| **5. Android share sheet** *(companion)* | `relaytv-android` `ShareActivity.kt` (`extractUrl`) | Matches `https?://\S+` only, silently dropping `rtsp://` / `rtsps://` text shares before calling `/smart` or `/play_now`. |

---

## 2. Core Design Decisions

1. **Direct `mpv` / `libmpv` playback (`provider = "rtsp"`), zero `yt-dlp`:**
   Classify `rtsp://` and `rtsps://` as `"rtsp"` in `resolver.provider_from_url()`.
   Skip `ytdlp_info()`, `resolve_title()`, queue metadata/stream prefetch,
   `resolve_streams()`, and mpv's `ytdl_hook` (`ytdl=no`). Item construction
   completes in `<1ms` with zero child processes.
2. **Expand only the user-media URL boundary, never outbound HTTP or extractor boundaries:**
   Permit `("http", "https", "rtsp", "rtsps")` in `resolver.validate_user_url()`
   while keeping `url_boundary.is_allowed_outbound_http_url()`,
   `url_boundary.safe_urlopen()`, `thumb_cache`, `peers` HTTP probes, and
   `resolver.validate_resolved_stream_url()` restricted to `("http", "https")`.
   A hostile web extractor response or HTTP 302 redirect can never pivot a
   public web link into opening a local RTSP socket.
3. **Default `--rtsp-transport=tcp`:**
   FFmpeg's RTSP demuxer defaults to UDP first and waits 5–10 seconds before
   falling back to TCP when UDP RTP packets are blocked by VLAN rules, Wi-Fi
   client isolation, host firewalls, or Tailscale/WireGuard tunnels. Defaulting
   `rtsp-transport=tcp` (configurable via `RELAYTV_RTSP_TRANSPORT` or
   `MPV_ARGS`) yields fast ~200–400ms startup and affects only `lavf` RTSP
   streams.
4. **Continuous live semantics (matching `provider == "iptv"`):**
   RTSP feeds are unbounded 24/7 streams, not finite YouTube/Twitch broadcasts:
   - Mark `is_live: True` so `/ui` renders the `LIVE` badge and enables cached
     DVR rewind (`--demuxer-seekable-cache=yes`, `extract_dvr_status()`, and
     `POST /playback/live`).
   - Return `False` in `_item_is_completable_live_stream()` so brief camera
     Wi-Fi drops or keyframe gaps that trip `core-idle` do not fire
     `_live_stream_end_stalled()` and prematurely advance the queue.
   - Ignore `start_pos` / `resume_pos` on `"rtsp"` playback starts and restarts
     so mpv never sends `PLAY … Range: npt=<sec>-` (which causes many IP
     cameras to reject playback with RTSP `456`/`457`).
   - Exclude `"rtsp"` from `_mpv_up_next_eligible_item()` so queued cameras are
     not pre-opened in mpv's internal playlist (many cameras allow only 1–2
     concurrent RTSP sessions).
5. **Strict credential hygiene across API, logs, toasts, and peers:**
   Store unredacted RTSP URLs only in server-owned state (`NOW_PLAYING`,
   `queue.json`, `history.json`, `session.json`). Strip `username`/`password`
   and camera auth query keys in every public payload, fallback title, TV
   overlay toast, and mpv debug log. Refuse peer transfer (`peers.wire_item()`)
   when redaction altered an RTSP URL so credentials never cross devices and
   peers never receive a broken `401` URL while the sender deletes its copy.

---

## 3. Server Implementation Specification

### 3.1 Input Validation & Provider Classification (`app/relaytv_app/resolver.py`)

- Define explicit scheme constants:
  ```python
  USER_MEDIA_URL_SCHEMES = ("http", "https", "rtsp", "rtsps")
  RESOLVED_HTTP_STREAM_SCHEMES = ("http", "https")
  ```
- `extract_first_url(text)`:
  - Match `re.search(r"(?:https?|rtsps?)://\S+", text, re.IGNORECASE)`.
- `validate_user_url(raw)`:
  - Accept `parsed.scheme in USER_MEDIA_URL_SCHEMES`.
  - Update the `400` detail to `"Unsupported URL scheme (http, https, rtsp, or rtsps only)"`
    (check existing test assertions on the exact message before changing or
    extend tests accordingly).
  - Keep `parsed.hostname` presence, `url_boundary.is_forbidden_ip_literal()`,
    and `url_boundary.has_forbidden_resolved_address()` checks unchanged
    (RFC1918 LAN addresses remain allowed; link-local `169.254.0.0/16`,
    unspecified `0.0.0.0`/`::`, multicast literals, and non-canonical numeric
    IPs remain blocked).
- `validate_resolved_stream_url(raw)`:
  - Keep `parsed.scheme in RESOLVED_HTTP_STREAM_SCHEMES` unchanged.
- `provider_from_url(u)` & `_provider_display_name(provider)`:
  - Parse via `url_boundary.parse_url(u)`; if `parsed is not None and parsed.scheme in ("rtsp", "rtsps")`,
    return `"rtsp"`.
  - Map `"rtsp": "RTSP"` in `_provider_display_name()`.
- `_fallback_item_title(url, provider)` & `make_item(input_text, *, lightweight=False)`:
  - Sanitize `url` via `public_media.sanitize_public_url(u)` before deriving any
    fallback title so userinfo or auth query params can never land in `title`.
  - For `prov == "rtsp"`, derive a clean label (for example last non-generic
    path segment + host, or `f"RTSP · {parsed.hostname}"` when path is empty or
    generic like `/live` / `/stream1`), set `"is_live": True`, and return
    immediately without calling `ytdlp_info()`, `resolve_title()`, or
    `attach_local_thumbnail()` (no HTTP thumbnail exists).
- `enrich_item_metadata(item)`:
  - Early-return `False` when `prov == "rtsp"`.

### 3.2 Public Redaction (`app/relaytv_app/public_media.py`)

- Extend `_SENSITIVE_QUERY_KEYS` with common IP-camera query auth keys:
  - `"pass"`, `"passwd"`, `"password"`, `"pwd"`, `"user"`, `"username"`, `"usr"`.
- In `public_media_item(item)`:
  - Expand the fallback string-value prefix check from
    `("http://", "https://", "/plex/stream", "/browse/stream", "/postlive")` to
    include `"rtsp://"` and `"rtsps://"` so an accidental raw URL in any string
    field is sanitized before leaving `/status`, `/queue`, `/history`, or
    realtime snapshots.

### 3.3 Runtime Config (`app/relaytv_app/config.py` & `docs/ENV_INVENTORY.md`)

- Optional operator override `RELAYTV_RTSP_TRANSPORT`:
  - Read through `runtime_config` (default `"tcp"`, allowed values
    `"tcp"`, `"udp"`, `"udp_multicast"`, `"http"`, or `""` to defer to mpv's
    built-in default).
  - Regenerate `docs/ENV_INVENTORY.md` via
    `PYTHONPATH=app pytest tests/test_env_inventory.py --write` (or verify
    `tests/test_env_inventory.py` passes).

### 3.4 Player Lifecycle, Transport & Log Hygiene (`app/relaytv_app/player.py` & `qt_shell_app.py`)

- **Transport option (`rtsp-transport`):**
  - `player._build_mpv_args()`: append `--rtsp-transport=<mode>` (default
    `tcp`) unless `--rtsp-transport` is already present in `mpv_args + extra`.
  - `qt_shell_app._build_mpv_args()`: append `--rtsp-transport=<mode>` unless
    already present in `args + extra`.
  - `qt_shell_app.LibmpvWidget`: call `self._set_opt_best_effort("rtsp-transport", mode)`
    before `extra` overrides so `MPV_ARGS` / `RELAYTV_QT_SHELL_MPV_ARGS` still
    win when explicitly set.
- **Disable `yt-dlp` hook on RTSP URLs:**
  - `player._should_force_ytdl_off(stream_url, provider_hint)`: return `True`
    when `prov == "rtsp"` or `stream_url.strip().lower().startswith(("rtsp://", "rtsps://"))`.
  - On seamless reuse (`player._load_stream_in_existing_mpv()` and
    `qt_shell_app.py` `LibmpvWidget.load_stream` / `load_stream` IPC branch):
    when `stream_url` starts with `rtsp://` or `rtsps://`, include per-file
    `ytdl=no` in `loadfile` options so a reused `--ytdl=yes` instance never
    invokes `ytdl_hook`.
- **Direct play & resume suppression in `play_item` (`_play_item_owned`):**
  - Set `should_resolve = False` when `provider == "rtsp"`.
  - Force `resume_start_pos = None` (and `player_start_pos = None`) when
    `provider == "rtsp"` so `/history/play`, `restart_current()`,
    `preserve_current_to_queue_front()`, and `play_temporary` restoration always
    join the live edge rather than issuing an RTSP `Range:` seek.
- **Queue prefetch, up-next, and live stall guards:**
  - `_item_looks_like_live_stream(item)`: return `True` when `provider == "rtsp"`.
  - `_item_is_completable_live_stream(now)`: return `False` when
    `provider in {"iptv", "rtsp"}`.
  - `_item_supports_metadata_prefetch(item)`: return `False` when
    `provider in {"jellyfin", "plex", "iptv", "upload", "rtsp"}`.
  - `_mpv_up_next_eligible_item(item)`: return `False` when
    `provider in {"iptv", "plex", "rtsp"}`.
- **Log & toast redaction:**
  - `player._redacted_mpv_args()`: redact args starting with
    `("http://", "https://", "rtsp://", "rtsps://")` and `--audio-file=` with
    any of those schemes.
  - `player._play_item_owned()`: sanitize `raw` and `stream` via
    `public_media.sanitize_public_url()` before passing to `debug_log()` when
    `provider == "rtsp"` (or generally for any URL carrying credentials).
  - `routes/playback.py`: ensure fallback toast strings (`title or title_hint or request_url`
    in `_play_now_item()`, `play()`, `play_temporary()`, and `share()`) pass raw
    URL fallbacks through `public_media.sanitize_public_url()`.

### 3.5 Peer Transfer (`app/relaytv_app/peers.py`)

- Determine whether an item carries non-portable inline credentials:
  - For `provider == "rtsp"`, compare `public_media.sanitize_public_url(raw_url)`
    against normalized `raw_url` (or check `parsed.username` / `parsed.password`
    / sensitive query keys).
  - If credentials were present:
    - `wire_item(item)` returns `None` so a stripped, unplayable `401` URL is
      never sent to a peer and never removed from the sender on Move/Handoff.
    - `wire_entries()` reports `reason = "rtsp_credentials_stay_on_this_device"`.
    - `playback_service.handoff_snapshot()` returns `None` for credentialed RTSP
      items (matching IPTV), while allowing unauthenticated LAN relays
      (for example `rtsp://go2rtc.lan:8554/front_door`) to hand off cleanly.

---

## 4. Web UI & PWA Share Target (`app/relaytv_app/static/ui/app.js`)

- `looksLikeUrl(s)` & `normalizeUrl(s)`:
  - Accept `/^(?:https?|rtsps?):\/\//i` alongside `/^www\./i`.
  - Update the invalid-URL alert/helper copy in `submitAddUrl()` to mention
    `http(s)://` or `rtsp(s)://`.
- `_isNowPlayingLive(np)`:
  - Return `True` when `provider === 'iptv' || provider === 'rtsp'`.
- `consumeShareParam()`:
  - Automatically benefits from `looksLikeUrl()` + `normalizeUrl()`, allowing
    `GET /share?url=rtsp%3A%2F%2F…` to prefill and open the Add Link modal.
- Provider badge:
  - RelayTV's local-first letter-tile generator (`app.js`) already renders a
    clean SVG badge for `"rtsp"` with zero external requests.

---

## 5. Companion Integrations & Scope Boundaries

### Companion Repositories (Separate PRs)

1. **`relaytv-android` (`/opt/dev/relaytv-android`):**
   - Widen `ShareActivity.extractUrl()` regex from `https?://\S+` to
     `(?:https?|rtsps?)://\S+` (and adjust the minimum-length check from
     `"https://".length` to `"rtsp://".length`).
   - Optionally add `ACTION_VIEW` `<intent-filter>` entries with
     `<data android:scheme="rtsp"/>` and `<data android:scheme="rtsps"/>` on
     `SharePlayNowAlias` / `ShareQueueAlias` so tapping an `rtsp://` link on
     Android offers **RelayTV · Play** and **Queue** directly (`extractSharedHttpUrl`
     already inspects `shareIntent.dataString`).
2. **`relaytv-ha` (`/opt/dev/relaytv-ha`):**
   - Sync `_SENSITIVE_QUERY_KEYS` in `custom_components/relaytv/url_utils.py`
     with `public_media.py` (`pass`, `passwd`, `password`, `pwd`, `user`,
     `username`, `usr`).
   - `media_player.play_media` and `relaytv.play_temporary` require no transport
     changes and immediately support doorbell/camera interruptions (`url:
     rtsp://…`, `timeout_sec: 15`, `resume: true`).

### Explicit Non-Goals (Phase 1)

- **IPTV M3U catalog (`integrations/iptv_service.py`):** Keep `parse_m3u()`
  scoped to `http`/`https` in Phase 1. Supporting `rtsp://` entries inside
  uploaded M3U camera playlists is a clean Phase 2 option if desired (requires
  skipping `urllib` HTTP probes in `iptv_service.check_channel()` for RTSP
  rows).
- **WebRTC / browser preview:** RelayTV plays on the TV output via `mpv` /
  `libmpv`, not inside the mobile remote browser; no server-side RTSP-to-WebRTC
  or HLS transcoding is needed.

---

## 6. Phasing & PR Plan

| Phase | Repo | Scope | Suggested Commit / PR Title |
| --- | --- | --- | --- |
| **1** | `relaytv` | Server RTSP validation, `"rtsp"` provider fast path, `--rtsp-transport=tcp`, live/resume/prefetch guards, credential redaction, peer guard, `/ui` + `/share` URL support, docs (`docs/API.md`, `docs/ENV_INVENTORY.md`), tests | `feat: support playing shared RTSP and RTSPS stream links` |
| **2** | `relaytv-android` | `ShareActivity.extractUrl()` `rtsp(s)://` support + optional `ACTION_VIEW` scheme intent filters + unit tests | `feat: accept rtsp and rtsps links in Android share targets` |
| **3** | `relaytv-ha` | Sync `_SENSITIVE_QUERY_KEYS` in `url_utils.py` + document camera `play_temporary` example | `fix: redact camera credential query params in media URLs` |

Release highlight assessment (`AGENTS.md`): Phase 1 introduces a notable
user-facing capability (direct IP camera / NVR / doorbell RTSP playback); review
whether `<next-version>.md` under `docs/release-highlights/` warrants a short
2-sentence lead-in when preparing the Phase 1 PR.

---

## 7. Quality Gates, Guardrails & Test Plan

### Automated Tests (`relaytv`)

1. **Boundary & provider unit tests (`tests/test_url_boundary.py` / `tests/test_rtsp_playback.py`):**
   - `extract_first_url()` extracts `rtsp://` and `RTSPS://` embedded in shared
     text.
   - `validate_user_url()` accepts LAN (`rtsp://192.168.1.50:554/stream1`) and
     hostname RTSP/RTSPS URLs, while still rejecting `file://`, `edl://`,
     `av://`, `rtmp://`, `169.254.169.254`, `0.0.0.0`, multicast literals, and
     malformed ports (`rtsp://cam:99999/live`).
   - `validate_resolved_stream_url()` and `is_allowed_outbound_http_url()`
     continue to **reject** `rtsp://` and `rtsps://` (revert-proof boundary
     check).
   - `make_item("rtsp://admin:secret@192.168.1.50:554/front_door")` never
     spawns `yt-dlp` (assert subprocess mock call count == `0`), sets
     `provider == "rtsp"` and `is_live is True`, and produces a credential-free
     `title`.
2. **Redaction & peer safety tests (`tests/test_public_media.py` / `tests/test_peers.py`):**
   - `public_media_item()` strips `admin:secret@` and `?user=admin&password=secret`
     from `url`, `input`, and accidental string fields (`title`).
   - `_redacted_mpv_args()` redacts `rtsp://admin:secret@…` to `<media-url>`.
   - `peers.wire_entries()` skips credentialed RTSP URLs with
     `rtsp_credentials_stay_on_this_device` while allowing credential-free
     `rtsp://go2rtc.lan:8554/front_door`.
3. **Player & transition tests (`tests/test_rtsp_playback.py`):**
   - `play_item()` with `provider == "rtsp"` bypasses `resolve_streams()`,
     passes `--rtsp-transport=tcp` and `--ytdl=no` on cold start, passes
     `ytdl=no` on seamless `loadfile` replace, and drops non-zero `start_pos` /
     `resume_pos`.
   - `_item_looks_like_live_stream()` is `True` while
     `_item_is_completable_live_stream()` is `False` (confirming a simulated
     `core-idle` stall does **not** trigger `_live_stream_end_stalled()` —
     revert-proof per `AGENTS.md`).
4. **JavaScript UI tests (`tests/js/`):**
   - `looksLikeUrl()` and `normalizeUrl()` accept `rtsp://` and `rtsps://`
     (case-insensitive) and `_isNowPlayingLive({provider: 'rtsp'}) === true`.

### Mandatory Quality Gates (`AGENTS.md`)

```bash
ruff check app tests
PYTHONPATH=app pytest -q
git diff --check
node --check app/relaytv_app/static/ui/app.js
node --check app/relaytv_app/static/ui/realtime_transport.js
PYTHONPATH=app python3 -c "import re; from relaytv_app.routes import x11_overlay_page; print(re.findall(r'<script>(.*?)</script>', x11_overlay_page().body.decode(), re.S)[0])" | node --check -
node --test tests/js/*.test.js
```

### On-Device Verification (`AGENTS.md` Device Testing)

Because unit tests mock process spawning, verify both **cold start** and
**seamless replace** on a real RelayTV container bind-mount (`docker-compose.branchtest.yml`)
against a live RTSP publisher (for example a LAN camera/`go2rtc` stream or a
local `mediamtx` + `ffmpeg -re -f lavfi -i testsrc=size=1280x720:rate=30 -c:v libx264 -f rtsp`
feed):

1. Cold start from idle via `POST /share` and `/ui` Add Link (`Play now`).
2. Seamless replace from an active YouTube video (`--ytdl=yes`) to an `rtsp://`
   stream and back to YouTube.
3. `POST /play_temporary` with `rtsp://…` (`timeout_sec: 10`, `resume: true`)
   interrupting an active VOD and cleanly resuming at the saved position.
4. Confirm `GET /status`, `GET /queue`, `GET /history`, and container logs
   contain zero plaintext RTSP credentials.

---

## 8. Risks & Edge Cases

- **Audio-less cameras & initial probe delay:** Some IP cameras advertise no
  audio track or emit sparse IDR keyframes (every 2–5s), causing `libavformat`
  to wait during stream probing before rendering frame 1. Avoid aggressive
  global `--untimed` or `--cache=no` flags (which break DVR rewind and A/V sync
  on audio-bearing cameras); only consider scoped per-file `demuxer-lavf-o`
  tuning if on-device testing shows >2s startup on real cameras.
- **Audio/video codec quirks (`PCMU`/`PCMA`, `MJPEG`, `HEVC`):** Standard
  `ffmpeg`/`libmpv` decodes G.711 mulaw/alaw and H.264 in software effortlessly;
  4K HEVC camera main-streams on Raspberry Pi may require hardware decode or
  sharing the camera's H.264 sub-stream.
- **Self-signed TLS on `rtsps://`:** NVR appliances (for example UniFi Protect
  `rtsps://…:7441/…`) often use self-signed certificates; verify how FFmpeg's
  `tls_verify` defaults behave in Debian's `libmpv2` build and document
  `?enable_srtp` / port `554` plain LAN RTSP or `demuxer-lavf-o=tls_verify=0`
  opt-in only if needed (never disable TLS verification silently by default).
- **Non-host Docker networking:** Default installs use `network_mode: host`,
  and `--rtsp-transport=tcp` also works through NATted Docker bridge networks
  for outbound camera connections without UDP RTP port forwarding.
