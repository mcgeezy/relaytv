#!/bin/sh
# RelayTV Flatpak launcher: maps the sandbox environment onto the same
# env-driven configuration surface the Docker runtime uses, then hands off
# to the shared container_entrypoint supervisor.
set -eu

# Optional user overrides (Flatpak analog of the Docker .env file):
# ~/.var/app/io.github.mcgeezy.relaytv/config/relaytv/env
ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/relaytv/env"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

# When started from SSH/TTY, Flatpak still exposes the desktop sockets but the
# caller may not provide display env. Fill only missing/TTY-derived values so
# explicit user config remains authoritative.
if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -n "${XDG_RUNTIME_DIR:-}" ]; then
  for relaytv_wayland_socket in "$XDG_RUNTIME_DIR"/wayland-*; do
    if [ -S "$relaytv_wayland_socket" ]; then
      export WAYLAND_DISPLAY="$(basename "$relaytv_wayland_socket")"
      break
    fi
  done
  unset relaytv_wayland_socket
fi
if [ -n "${WAYLAND_DISPLAY:-}" ]; then
  if [ -z "${XDG_SESSION_TYPE:-}" ] || [ "${XDG_SESSION_TYPE:-}" = "tty" ]; then
    export XDG_SESSION_TYPE=wayland
  fi
  if [ -z "${RELAYTV_HOST_SESSION_TYPE:-}" ] || [ "${RELAYTV_HOST_SESSION_TYPE:-}" = "tty" ]; then
    export RELAYTV_HOST_SESSION_TYPE=wayland
  fi
  export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland}"
  export RELAYTV_MODE="${RELAYTV_MODE:-wayland}"
  export RELAYTV_QT_RUNTIME_MODE="${RELAYTV_QT_RUNTIME_MODE:-auto}"
fi

# Persistent state lives in the app's private XDG data dir
# (~/.var/app/io.github.mcgeezy.relaytv/data on the host).
: "${RELAYTV_STATE_DIR:=${XDG_DATA_HOME:-$HOME/.local/share}}"
export RELAYTV_STATE_DIR
export RELAYTV_THUMB_DIR="${RELAYTV_THUMB_DIR:-$RELAYTV_STATE_DIR/thumbs}"
export RELAYTV_UPLOADS_DIR="${RELAYTV_UPLOADS_DIR:-$RELAYTV_STATE_DIR/uploads}"

# yt-dlp ships pinned in the bundle; the pip self-update path is off unless
# explicitly re-enabled in the env file.
export RELAYTV_YTDLP_AUTO_UPDATE="${RELAYTV_YTDLP_AUTO_UPDATE:-0}"

export RELAYTV_PORT="${RELAYTV_PORT:-8787}"
if [ "$RELAYTV_PORT" != "8787" ]; then
  export RELAYTV_QT_OVERLAY_URL="${RELAYTV_QT_OVERLAY_URL:-http://127.0.0.1:$RELAYTV_PORT/x11/overlay}"
  export RELAYTV_OVERLAY_URL="${RELAYTV_OVERLAY_URL:-http://127.0.0.1:$RELAYTV_PORT/x11/overlay}"
  export RELAYTV_IDLE_URL="${RELAYTV_IDLE_URL:-http://127.0.0.1:$RELAYTV_PORT/idle}"
fi

# Chromium's own sandbox cannot nest inside the Flatpak sandbox; the Flatpak
# provides the confinement (standard for pip-wheel QtWebEngine in Flatpak).
export QTWEBENGINE_CHROMIUM_FLAGS="${QTWEBENGINE_CHROMIUM_FLAGS:---no-sandbox}"

# Flatpak Wayland/libmpv stacks can freeze or black out video when a native Qt
# toast surface is raised over playback. Keep native toasts override-only and
# use the browser overlay notification path by default.
export RELAYTV_QT_NATIVE_TOASTS="${RELAYTV_QT_NATIVE_TOASTS:-0}"
export RELAYTV_QT_NATIVE_TOASTS_TOPLEVEL="${RELAYTV_QT_NATIVE_TOASTS_TOPLEVEL:-0}"

# App package plus pip-installed deps (path varies with the runtime Python).
SITE_PACKAGES="$(ls -d /app/lib/python3.*/site-packages 2>/dev/null | head -n 1 || true)"
export PYTHONPATH="/app/relaytv${SITE_PACKAGES:+:$SITE_PACKAGES}${PYTHONPATH:+:$PYTHONPATH}"

exec python3 -m relaytv_app.container_entrypoint "$@"
