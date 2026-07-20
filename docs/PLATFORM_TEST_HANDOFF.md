# Platform Test Handoff

This handoff covers branch `fix/pi-wayland-installer-qpa`.

## Purpose

Validate the installer fix for Raspberry Pi Wayland hosts and check that other
runtime profiles still start cleanly.

The bug found on the Pi was:

- native Qt Wayland started but painted black for both video and idle
- the xcb/Xwayland bridge worked only after the container could read the host
  Xauthority cookie

Expected installer behavior on Raspberry Pi Wayland:

- `RELAYTV_MODE=wayland`
- `QT_QPA_PLATFORM=xcb`
- `XAUTHORITY=/tmp/.Xauthority` when the target user's `.Xauthority` exists
- `docker-compose.override.yml` mounts the target user's `.Xauthority` at
  `/tmp/.Xauthority`

Do not print or collect `RELAYTV_API_TOKEN` during testing.

## Common Setup

```bash
git fetch origin
git switch fix/pi-wayland-installer-qpa
git status --short --branch
```

Use source-checkout deployment for this branch:

```bash
./scripts/install.sh
docker compose up -d --build --force-recreate relaytv
```

If testing from an interactive graphical shell and you want the installer to
inherit that shell's display variables:

```bash
./scripts/install.sh --use-shell-env
docker compose up -d --build --force-recreate relaytv
```

Basic service checks:

```bash
curl -fsS http://127.0.0.1:8787/health
curl -fsS http://127.0.0.1:8787/status | python3 -m json.tool
docker compose ps
docker logs --tail 160 relaytv
```

Useful status fields:

- `qt_shell_running`
- `backend_ready`
- `display_session_available`
- `host_session_type`
- `native_qt_telemetry_alive`
- `native_qt_overlay_load_ok`
- `native_qt_overlay_visible`
- `qt_shell_supervisor_last_action`
- `qt_shell_supervisor_last_reason`

## Raspberry Pi Wayland

Run:

```bash
DISPLAY=:0 \
XDG_SESSION_TYPE=wayland \
WAYLAND_DISPLAY=wayland-0 \
XDG_RUNTIME_DIR=/run/user/$(id -u) \
./scripts/install.sh --use-shell-env
docker compose up -d --build --force-recreate relaytv
```

Verify generated config without dumping secrets:

```bash
awk -F= '/^(RELAYTV_MODE|QT_QPA_PLATFORM|XAUTHORITY|WAYLAND_DISPLAY)=/ {print}' .env
rg -n 'Xauthority|/tmp/.X11-unix|/run/user' docker-compose.override.yml
```

Expected:

- `.env` contains `RELAYTV_MODE=wayland`
- `.env` contains `QT_QPA_PLATFORM=xcb`
- `.env` contains `XAUTHORITY=/tmp/.Xauthority`
- override mounts `/tmp/.X11-unix`
- override mounts `/run/user`
- override mounts the target user's `.Xauthority` to `/tmp/.Xauthority`

Functional checks:

- play a known-good 1080p H.264 item
- verify video and audio are both present
- close playback and verify the idle dashboard appears
- verify `/status` reports `backend_ready=True` and
  `native_qt_overlay_load_ok=True`

If a screenshot tool is available on the host, capture actual display pixels.
For Wayland:

```bash
XDG_RUNTIME_DIR=/run/user/$(id -u) WAYLAND_DISPLAY=wayland-0 grim /tmp/relaytv-screen.png
```

The screenshot must show video during playback and the RelayTV idle dashboard
after close. A black frame means the runtime is still broken even if telemetry
claims the overlay loaded.

Known recovery: if `~/.Xauthority` is removed after install, container
recreation fails with `bind source path does not exist`. Rerun the installer to
regenerate the override.

## Pi Native Wayland Opt-In

Native Qt Wayland remains an explicit operator opt-in for Pi hosts:

```bash
QT_QPA_PLATFORM=wayland ./scripts/install.sh --use-shell-env
docker compose up -d --build --force-recreate relaytv
```

This path is expected to be risky on Pi graphics stacks. If video or idle paints
black, return to the default installer output by rerunning:

```bash
./scripts/install.sh --use-shell-env
docker compose up -d --build --force-recreate relaytv
```

## AMD64 Wayland

Run from a Wayland desktop session:

```bash
./scripts/install.sh --use-shell-env
docker compose up -d --build --force-recreate relaytv
```

Expected:

- `RELAYTV_MODE=wayland`
- `QT_QPA_PLATFORM=wayland`
- no `.Xauthority` bind is required unless the operator explicitly selects xcb
- video playback is visible
- idle dashboard is visible after close

## X11 Desktop

Run from an X11 session:

```bash
./scripts/install.sh --use-shell-env
docker compose up -d --build --force-recreate relaytv
```

Expected:

- `RELAYTV_MODE=x11`
- `QT_QPA_PLATFORM=xcb`
- `XAUTHORITY=/tmp/.Xauthority` when the target user's `.Xauthority` exists
- video playback and idle dashboard are visible

## Headless

Run:

```bash
./scripts/install.sh --mode headless
docker compose up -d --build --force-recreate relaytv
```

Expected:

- container starts
- `/health` returns `{"ok":true}`
- `/ui` is reachable from another machine
- no physical display validation is expected

## Regression Commands

Before reporting results, run:

```bash
.venv/bin/ruff check app tests
PYTHONPATH=app .venv/bin/pytest -q
git diff --check
```

Record:

- host model and architecture
- session type
- generated runtime env keys listed above
- whether the `.Xauthority` bind was generated
- `/status` runtime fields
- playback result
- idle dashboard result
- screenshot result, if available
