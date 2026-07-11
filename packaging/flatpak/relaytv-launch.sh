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

# App package plus pip-installed deps (path varies with the runtime Python).
SITE_PACKAGES="$(ls -d /app/lib/python3.*/site-packages 2>/dev/null | head -n 1 || true)"
export PYTHONPATH="/app/relaytv${SITE_PACKAGES:+:$SITE_PACKAGES}${PYTHONPATH:+:$PYTHONPATH}"

exec python3 -m relaytv_app.container_entrypoint "$@"
