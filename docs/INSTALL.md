# Install

RelayTV supports a native Qt desktop runtime by default. Rollback during Phase 5 decommission is now a tagged-baseline redeploy, not a parallel compat runtime.

Supported default product profiles:

- Wayland desktop: native Qt
- X11 desktop: native Qt
- DRM/KMS or headless: explicit non-default path

Idle default note:

- Raspberry Pi class hosts now default to the same embedded `libmpv` + browser-overlay Qt runtime used on x86/NUC desktop hosts.
- Raspberry Pi class hosts now default to the browser-backed idle screen through the Qt web overlay.
- The older native Qt idle layer is deprecated and retained only as an explicit override via `RELAYTV_QT_NATIVE_IDLE=1`.
- Native Qt toast delivery is also deprecated as a compatibility override via `RELAYTV_QT_NATIVE_TOASTS=1`.

## Quick Start

Published-image one-line install:

```bash
mkdir -p ~/relaytv && cd ~/relaytv && curl -fsSL https://raw.githubusercontent.com/mcgeezy/relaytv/main/install.sh | bash
```

The bootstrap installer downloads the release compose file and installer scripts,
confirms that RelayTV will be installed in the current directory, generates
`.env`, optionally enables HDMI-CEC passthrough when detected and approved, then
runs:

```bash
docker compose pull
docker compose up -d
```

Common bootstrap options:

```bash
# Install into a specific directory
curl -fsSL https://raw.githubusercontent.com/mcgeezy/relaytv/main/install.sh | bash -s -- --dir /opt/relaytv

# Non-interactive install; skips optional CEC unless explicitly enabled
mkdir -p ~/relaytv && cd ~/relaytv && curl -fsSL https://raw.githubusercontent.com/mcgeezy/relaytv/main/install.sh | bash -s -- --yes

# Force optional CEC passthrough when /dev/cec* exists
mkdir -p ~/relaytv && cd ~/relaytv && curl -fsSL https://raw.githubusercontent.com/mcgeezy/relaytv/main/install.sh | bash -s -- --enable-cec

# Override runtime detection
mkdir -p ~/relaytv && cd ~/relaytv && curl -fsSL https://raw.githubusercontent.com/mcgeezy/relaytv/main/install.sh | bash -s -- --mode x11

# Refuse to overwrite source checkouts by default; force only when intentional
curl -fsSL https://raw.githubusercontent.com/mcgeezy/relaytv/main/install.sh | bash -s -- --dir /opt/relaytv --force
```

Source checkout install:

```bash
cd /path/to/relaytv
chmod +x scripts/install.sh scripts/doctor.sh
./scripts/install.sh
docker compose up -d --build
./scripts/doctor.sh
```

For local source iteration instead of pulled images:

```bash
cd /path/to/relaytv
./scripts/install.sh
docker compose up -d --build
./scripts/doctor.sh
```

## What The Installer Does

`scripts/install.sh`:

- detects the active runtime (`wayland`, `x11`, or `drm`)
- writes only the `.env` keys needed for the detected runtime plus non-default overrides
- emits Docker build bundle flags only when an optional runtime path needs them
- generates host device overrides for Raspberry Pi V4L2 devices and optional HDMI-CEC passthrough
- records host identity (`PUID`, `PGID`, render group gid)
- selects a runtime profile (`native-qt` by default)
- leaves `MPV_AUDIO_DEVICE` blank so runtime auto-detect chooses audio output

Root `install.sh`:

- bootstraps published-image installs from GitHub raw files
- defaults to the current directory and prompts before writing service files
- writes a release-only `docker-compose.yml` with no source build dependency
- downloads `scripts/install.sh`, `scripts/doctor.sh`, and `scripts/host-ops.sh`
- runs `scripts/install.sh` to generate `.env`
- optionally prompts for HDMI-CEC support when CEC hardware is detected
- pulls and starts the configured published image by default

Published-image defaults:

- the bootstrap installer writes a release-only `docker-compose.yml` that uses the published image
- source checkouts use the repo `docker-compose.yml` and should start with `docker compose up -d --build`
- default image ref is `ghcr.io/mcgeezy/relaytv:latest`
- operators can use `docker compose pull && docker compose up -d`

Published image:

- `ghcr.io/mcgeezy/relaytv:latest` is the only public image tag offered by the installer.
- The deleted `dev` branch is not an install target and no `dev` image tag is offered.
- Use `main` for published-image installs; use a source checkout plus `docker compose up -d --build` for local development or testing.
- Local developers can still use build-time bundle flags with `docker compose up -d --build` when testing optional runtime paths.

## Docker Build Bundles

The default container build is now lean and native-Qt-first. Optional feature bundles are build-time opt-ins exposed through `docker-compose.yml` args and `.env`:

- `RELAYTV_INSTALL_X11_OVERLAY=1`: include GTK/WebKit packages for the legacy X11 overlay fallback
- `RELAYTV_INSTALL_HEADLESS=1`: include `Xvfb` and `x11vnc` for headless remote mode
- `RELAYTV_INSTALL_NODE=1`: include the yt-dlp JavaScript challenge runtime bundle
  - default is now `1`
  - RelayTV prefers `deno` when available, otherwise uses explicit `--js-runtimes node`
- `RELAYTV_INSTALL_IDLE_BROWSER=1`: include Chromium for the optional browser-backed idle dashboard
- `RELAYTV_INSTALL_OPS_TOOLS=1`: include extra debug/ops tools (`mesa-utils`, `procps`, `socat`)

`scripts/install.sh` auto-emits the X11 overlay or headless bundle flags when those modes are explicitly selected.

## Runtime Profiles

Default desktop product profile:

- `native-qt`

Selection options:

- `--runtime-profile native-qt|auto`
- shortcut: `--native-qt`

Examples:

```bash
# default product install path
./scripts/install.sh

# force native Qt explicitly
./scripts/install.sh --mode wayland --native-qt

# redeploy a tagged native baseline explicitly
git checkout native-qt-baseline
./scripts/install.sh --mode wayland --native-qt
```

Decommission note:

- active product direction is native-only
- retired compat installer flags have been removed
- host/runtime operations now live in `docs/NATIVE_RUNTIME_OPERATIONS.md`

You can also set the default profile with env:

```bash
RELAYTV_INSTALL_RUNTIME_PROFILE=native-qt ./scripts/install.sh --mode wayland
```

## Mode Defaults

### Wayland

- preferred on modern Linux desktops
- installer writes Wayland runtime values automatically
- native Qt is the default product path

### X11

- supported native Qt desktop path
- use when the host session is X11 or when that environment is operationally simpler

### DRM/KMS

- selected only when no active Wayland or X11 session exists but a connected DRM output is detected
- installer writes:
  - `RELAYTV_MODE=drm`
  - `RELAYTV_VIDEO_MODE=drm`
  - `RELAYTV_DRM_CONNECTOR=<connected-connector>`
  - `RELAYTV_PLAYER_BACKEND=mpv`

### Headless

Headless is explicit only. Auto mode does not silently choose it.

Enable it with:

```bash
./scripts/install.sh --mode headless
```

or:

```bash
RELAYTV_INSTALL_MODE=headless ./scripts/install.sh
```

Headless mode uses an in-container virtual X server plus VNC.

## Runtime Detection

Default behavior is clean autodetect and ignores ambient shell vars such as `DISPLAY` or `XDG_SESSION_TYPE`.

If you explicitly want to inherit the current shell environment:

```bash
./scripts/install.sh --use-shell-env
```

## Raspberry Pi Notes

On Raspberry Pi hosts, the installer also generates `docker-compose.override.yml` for the standard V4L2 devices (`/dev/video10-13`) so the normal startup command stays the same:

```bash
docker compose pull && docker compose up -d
```

Overrides:

- `RELAYTV_PI_VIDEO_DEVICES_ENABLED=1` force on
- `RELAYTV_PI_VIDEO_DEVICES_ENABLED=0` force off

## Verify Generated Runtime Env

```bash
grep -nE 'RELAYTV_IMAGE_REF|RELAYTV_MODE|RELAYTV_VIDEO_MODE|RELAYTV_DRM_CONNECTOR|RELAYTV_PLAYER_BACKEND|XDG_SESSION_TYPE|QT_QPA_PLATFORM|WAYLAND_DISPLAY|RELAYTV_QT_RUNTIME_MODE|RELAYTV_QT_SHELL_MPV_ARGS|RELAYTV_QT_SHELL_MODULE|RELAYTV_HEADLESS_REMOTE_|RELAYTV_HEADLESS_VNC_' .env
```

## Pulled Image Runtime Contract

Published images still require the same Linux media-host integration as local builds:

- `/dev/dri` passthrough for GPU acceleration
- `/dev/snd` passthrough for audio
- host display/session env such as `DISPLAY`, `XDG_SESSION_TYPE`, `WAYLAND_DISPLAY`, and `XDG_RUNTIME_DIR`
- host networking
- `/run/user/<uid>` and X11 socket mounts for desktop session access

Pulled images are an operator convenience, not a generic desktop-container portability layer.

## Common Notes

### Audio Device

`MPV_AUDIO_DEVICE` is intentionally left blank by default so RelayTV chooses the active sink at runtime.

If you need to force a specific sink, set `MPV_AUDIO_DEVICE` manually in `.env`.

### yt-dlp Auto-Update

Optional container-start behavior:

```bash
RELAYTV_YTDLP_AUTO_UPDATE=1
RELAYTV_YTDLP_AUTO_UPDATE_INTERVAL_HOURS=24
RELAYTV_YTDLP_AUTO_UPDATE_TIMEOUT_SEC=180
RELAYTV_YTDLP_AUTO_UPDATE_STATE_FILE=/data/.relaytv-ytdlp-update.json
```

Official release images disable `yt-dlp` auto-update by default for source and
object traceability. Enabling it is a user opt-in that may improve resolver
freshness, but audited/reproducible build claims only cover the image as built.
See [RELEASE.md](RELEASE.md) for release input details.

### CEC

The bootstrap installer prompts to enable HDMI-CEC when likely CEC hardware is
detected. If enabled, `scripts/install.sh` writes the detected `/dev/cec*`
devices into `docker-compose.override.yml`.

Manual override:

```bash
RELAYTV_CEC_ENABLED=1 ./scripts/install.sh
```

Equivalent compose passthrough:

```yaml
devices:
  - /dev/cec0:/dev/cec0
```

### X11 Overlay

The standalone host X11 overlay is a fallback/diagnostic path, not the primary product runtime.

If you still need it, run:

```bash
python3 app/relaytv_app/overlay_app.py --url http://127.0.0.1:8787/x11/overlay
```
