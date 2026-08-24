# Install

RelayTV supports a native Qt desktop runtime by default. Rollback for a broken
runtime rollout is a tagged-baseline redeploy, not a parallel compatibility
runtime.

Supported default product profiles:

- Wayland desktop: native Qt
- X11 desktop: native Qt
- DRM/KMS or headless: explicit non-default path

Idle default note:

- Raspberry Pi class hosts now default to the same embedded `libmpv` + browser-overlay Qt runtime used on x86/NUC desktop hosts.
- Raspberry Pi class hosts now default to the browser-backed idle screen through the Qt web overlay.
- The older native Qt idle layer is deprecated and retained only as an explicit override via `RELAYTV_QT_NATIVE_IDLE=1`.
- Native Qt toast delivery is also deprecated as a compatibility override via `RELAYTV_QT_NATIVE_TOASTS=1`.

## Supported Hosts and Prerequisites

Published RelayTV images support native 64-bit Linux on `amd64`/`x86_64` and
`arm64`/`aarch64`. This includes 64-bit Raspberry Pi OS, Debian, Ubuntu,
Fedora, RHEL, and CentOS hosts. The television runtime depends on native host
networking plus direct display, audio, and device access, so WSL and Docker
Desktop on macOS or Windows are not supported install targets.

RelayTV requires [Docker Engine](https://docs.docker.com/engine/install/) and
the [Docker Compose v2 plugin](https://docs.docker.com/compose/install/linux/).
When Docker is absent, the interactive bootstrap offers to install it from
Docker's official package repository on the distributions above. The
installer downloads Docker's official installation script to a temporary file
before running it with administrator access; it does not pipe the script
directly into a shell.

Use `--install-docker` to approve dependency installation up front,
`--no-install-docker` to require an existing Docker installation, or `--yes`
for a fully non-interactive install. RelayTV does not add users to the
`docker` group because [that group grants root-level
privileges](https://docs.docker.com/engine/install/linux-postinstall/). If the
current account cannot access Docker, bootstrap commands use `sudo` for that
installation and print matching operator commands at completion.

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

# Non-interactive install; installs Docker if needed and skips optional CEC
mkdir -p ~/relaytv && cd ~/relaytv && curl -fsSL https://raw.githubusercontent.com/mcgeezy/relaytv/main/install.sh | bash -s -- --yes

# Approve Docker installation if it is missing
mkdir -p ~/relaytv && cd ~/relaytv && curl -fsSL https://raw.githubusercontent.com/mcgeezy/relaytv/main/install.sh | bash -s -- --install-docker

# Require Docker Engine and Compose v2 to be installed already
mkdir -p ~/relaytv && cd ~/relaytv && curl -fsSL https://raw.githubusercontent.com/mcgeezy/relaytv/main/install.sh | bash -s -- --no-install-docker

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
- atomically refreshes installer-owned `.env` keys while preserving
  operator-owned values such as `RELAYTV_API_TOKEN` and `RELAYTV_PORT`
- restricts `.env` permissions to the target user (`0600`)
- emits Docker build bundle flags only when an optional runtime path needs them
- generates host integration overrides only for existing system paths and
  devices, including desktop sockets, Raspberry Pi V4L2 devices, and optional
  HDMI-CEC passthrough
- refuses to overwrite an unmarked, user-managed `docker-compose.override.yml`
- disables container SELinux labeling in the generated override on enforcing
  SELinux hosts, avoiding relabeling of shared system paths
- records host identity (`PUID`, `PGID`, render group gid)
- selects a runtime profile (`native-qt` by default)
- leaves `MPV_AUDIO_DEVICE` blank so runtime auto-detect chooses audio output

Root `install.sh`:

- bootstraps published-image installs from GitHub raw files
- validates native Linux and the published image architecture before download
- offers to install Docker Engine and Compose v2 when missing, then validates
  daemon and socket access
- defaults to the current directory and prompts before writing service files
- writes a release-only `docker-compose.yml` with no source build dependency
- downloads `scripts/install.sh`, `scripts/doctor.sh`, and `scripts/host-ops.sh`
- runs `scripts/install.sh` to generate `.env`
- delegates HDMI-CEC detection and the optional prompt to `scripts/install.sh`
- pulls and starts the configured published image by default
- waits for `/health` and reports container status and recent logs if startup
  does not become healthy within the configured timeout

Published-image defaults:

- the bootstrap installer writes a release-only `docker-compose.yml` that uses the published image
- source checkouts use the repo `docker-compose.yml` and should start with `docker compose up -d --build`
- default image ref is `ghcr.io/mcgeezy/relaytv:latest`
- operators can use `docker compose pull && docker compose up -d`

Published image:

- `ghcr.io/mcgeezy/relaytv:latest` is the default public image tag offered by the installer.
- Immutable release tags are also published by the release workflow; see
  [RELEASE.md](RELEASE.md) for tag shapes and image traceability.
- The deleted `dev` branch is not an install target and no `dev` image tag is offered.
- Use `main` for published-image installs; use a source checkout plus `docker compose up -d --build` for local development or testing.
- Local developers can still use build-time bundle flags with `docker compose up -d --build` when testing optional runtime paths.

## Network Trust Model

RelayTV is designed for a trusted local network. The HTTP API can start
playback, mutate the queue, upload media, change settings, send TV
notifications, and interact with Jellyfin. Do not expose the service directly
to the public internet.

If remote access is needed, put RelayTV behind a VPN, trusted reverse proxy, or
Home Assistant access layer that provides authentication and transport
security.

Optionally, set `RELAYTV_API_TOKEN` in the `.env` in your RelayTV directory to require
`Authorization: Bearer <token>` on all write requests (playback control,
queue/settings changes, uploads, Jellyfin commands). Reads — `/health`,
`/status`, the web UI, and static assets — stay open, and the web UI prompts
for the token on first use. Leave it unset (the default) for the fully open
trusted-LAN behavior. See `API.md` ("Optional API token") for details and
reverse-proxy examples.

### Reverse proxies and realtime updates

The browser UI and companion clients prefer RelayTV's read-only WebSocket
state channel and automatically fall back to Server-Sent Events and then HTTP
polling when the network path cannot sustain it. A reverse proxy should forward
the public `Host` and `X-Forwarded-Proto`, permit HTTP/1.1 WebSocket Upgrade and
Connection headers, disable buffering for the SSE fallback, and use a long
read timeout. The forwarded public scheme is required for same-origin browser
validation when TLS terminates at the proxy.

Use the complete nginx and Caddy examples in [API.md](API.md#network-trust-model).
RelayTV's stock Uvicorn process trusts forwarded headers from loopback. If a
proxy connects from another address, scope Uvicorn's
`--forwarded-allow-ips` setting to that proxy rather than trusting arbitrary
clients.

## Docker Build Bundles

The default container build is now lean and native-Qt-first. Optional feature bundles are build-time opt-ins exposed through `docker-compose.yml` args and `.env`:

- `RELAYTV_INSTALL_X11_OVERLAY=1`: include GTK/WebKit packages for the legacy X11 overlay fallback
- `RELAYTV_INSTALL_HEADLESS=1`: include `Xvfb` and `x11vnc` for headless remote mode
- `RELAYTV_INSTALL_DENO=1`: include deno, yt-dlp's default-enabled JavaScript
  challenge runtime (pinned static binary, sha256-verified; amd64/arm64 only)
  - default is `1`; deno sandboxes the remote challenge code and needs no
    per-invocation flags, so it also covers mpv's own ytdl hook
- `RELAYTV_INSTALL_NODE=1`: include the nodejs fallback runtime for yt-dlp
  - default is now `1`
  - RelayTV prefers `deno` when available, otherwise uses explicit `--js-runtimes node`
    (the only option on 32-bit ARM, which has no deno build)
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

Raspberry Pi Wayland installs use an Xwayland/xcb Qt bridge by default because
native Wayland Qt can produce black video on Pi graphics stacks. When the
target user's `.Xauthority` file exists, the installer mounts it into the
container and writes `XAUTHORITY=/tmp/.Xauthority` so the bridge can connect to
the host display.

If a host specifically works better with native Qt Wayland, opt into it
explicitly:

```bash
QT_QPA_PLATFORM=wayland ./scripts/install.sh --use-shell-env
```

If `~/.Xauthority` is deleted after install, container recreation fails with
`bind source path does not exist`. Rerun the installer to regenerate the
override without the stale mount.

## Verify Generated Runtime Env

```bash
grep -nE 'RELAYTV_IMAGE_REF|RELAYTV_MODE|RELAYTV_VIDEO_MODE|RELAYTV_DRM_CONNECTOR|RELAYTV_PLAYER_BACKEND|XDG_SESSION_TYPE|QT_QPA_PLATFORM|WAYLAND_DISPLAY|RELAYTV_QT_RUNTIME_MODE|RELAYTV_QT_SHELL_MPV_ARGS|RELAYTV_QT_SHELL_MODULE|RELAYTV_HEADLESS_REMOTE_|RELAYTV_HEADLESS_VNC_' .env
```

## Pulled Image Runtime Contract

Published images still require the same Linux media-host integration as local builds:

- `/dev/dri` passthrough for GPU acceleration and `/dev/snd` passthrough for
  audio. Both live in the generated `docker-compose.override.yml`, written by
  `scripts/install.sh` from the devices the host actually exposes — the base
  compose files map no devices, so hosts without KMS or audio hardware can
  still start the container. Re-run `scripts/install.sh` after hardware or
  kernel changes to refresh the override.
- NVIDIA decode acceleration requires host NVIDIA drivers plus Docker NVIDIA
  Container Toolkit. When both an NVIDIA device and toolkit are detected,
  `scripts/install.sh` writes a generated compose override with `gpus: all` and
  NVIDIA driver capabilities for decode/playback.
- host display/session env such as `DISPLAY`, `XDG_SESSION_TYPE`, `WAYLAND_DISPLAY`, and `XDG_RUNTIME_DIR`
- host networking
- the stable `/run/user` parent and X11 socket directory for desktop session
  access; RelayTV selects the current session credential inside the container

All optional host bind mounts use Compose long syntax with
`create_host_path: false`. Missing host paths are omitted, so Compose cannot
create a directory in place of an absent system file. In particular,
`/etc/timezone` is not mounted; `/etc/localtime` is included only when it
exists. This follows Docker's [bind-mount
behavior](https://docs.docker.com/engine/storage/bind-mounts/) while keeping
the generated host contract explicit.

GNOME/Mutter Xwayland credentials use session-scoped names such as
`.mutter-Xwaylandauth.ABC123`. The installer never writes one of those names
as a Compose bind source. It mounts the stable runtime parent instead, and the
RelayTV app resolves the newest readable credential whenever it launches a
display process. Rerunning the installer migrates older generated overrides
and removes the former `RELAYTV_XAUTHORITY_HOST_PATH` entry from `.env`.

Pulled images are an operator convenience, not a generic desktop-container portability layer.

## Common Notes

### Audio Device

`MPV_AUDIO_DEVICE` is intentionally left blank by default so RelayTV chooses the active sink at runtime.

If you need to force a specific sink, set `MPV_AUDIO_DEVICE` manually in `.env`.

### yt-dlp Auto-Update

Toggleable at runtime in the web UI (Settings -> YouTube -> "Keep yt-dlp up to
date"): enabling it runs an immediate update check and then a daily background
check, no container restart needed. The env values seed the toggle's default
and tune the schedule:

```bash
RELAYTV_YTDLP_AUTO_UPDATE=1
RELAYTV_YTDLP_AUTO_UPDATE_INTERVAL_HOURS=6
RELAYTV_YTDLP_AUTO_UPDATE_TIMEOUT_SEC=180
RELAYTV_YTDLP_AUTO_UPDATE_STATE_FILE=/data/.relaytv-ytdlp-update.json
RELAYTV_YTDLP_UPDATE_DIR=/data/ytdlp
RELAYTV_YTDLP_UPDATE_CHANNEL=nightly
```

Updates install to `RELAYTV_YTDLP_UPDATE_DIR` on the data volume, so they
survive a container recreate. `$HOME` is `/tmp` and `/tmp` is tmpfs, so the
older user-site location was discarded on every deploy while the update state
file (on `/data`) still read "checked recently" — leaving the device on the
image's yt-dlp until the interval elapsed again.

`RELAYTV_YTDLP_UPDATE_CHANNEL` defaults to `nightly` because yt-dlp ships
extractor fixes there first; stable can go weeks between releases while a site
change breaks playback. A failed nightly install falls back to `stable`
automatically, and `stable` pins the old behaviour. A persisted copy is
discarded if it will not run or if a rebuilt image ships something newer.

Official release images disable `yt-dlp` auto-update by default for source and
object traceability. Enabling it is a user opt-in that may improve resolver
freshness, but audited/reproducible build claims only cover the image as built.
See [RELEASE.md](RELEASE.md) for release input details.

### Playback starts then stops after a few seconds

Almost always a stale yt-dlp. The resolve succeeds, so `POST /play_now` returns
200 and the container log looks clean, but the stream URL it produced is one the
site now rejects and mpv dies on the first fetch. mpv runs with `--no-terminal`,
so its reason is not on stdout — check instead:

```bash
curl -s http://<host>:8787/status | jq .last_playback_error
cat /data/.relaytv-ytdlp-update.json
```

`last_playback_error` carries mpv's own message (for example
`stream: Failed to open <url>` after an HTTP 403). If yt-dlp is behind, force a
check by toggling auto-update off and on in Settings, or restart the container.

### CEC

`scripts/install.sh` detects likely HDMI-CEC hardware and prompts to enable it
when run interactively. If enabled, it writes the detected CEC device nodes into
`docker-compose.override.yml`. Linux CEC adapters exposed as `/dev/cec*` are
included directly. USB libCEC adapters are included only when `cec-client -l`
reports an allowlisted device path such as `/dev/ttyACM*`.

Manual override:

```bash
RELAYTV_CEC_ENABLED=1 ./scripts/install.sh
```

Equivalent compose passthrough:

```yaml
devices:
  - /dev/cec0:/dev/cec0
  - /dev/ttyACM0:/dev/ttyACM0
```

### X11 Overlay

The standalone host X11 overlay is a fallback/diagnostic path, not the primary product runtime.

If you still need it, run:

```bash
python3 app/relaytv_app/overlay_app.py --url http://127.0.0.1:8787/x11/overlay
```
