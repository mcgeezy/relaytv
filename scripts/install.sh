#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say() { printf "%s\n" "$*"; }
warn_deprecated() { printf "WARN: %s\n" "$*" >&2; }
usage() {
  cat <<'EOF'
Usage:
  ./scripts/install.sh [--clean-autodetect] [--use-shell-env] [--mode auto|wayland|x11|drm|headless] [--runtime-profile native-qt|auto]
  ./scripts/install.sh [--native-qt]

Options:
  --clean-autodetect
      Ignore ambient shell display/session vars and discover from host session
      state (default behavior).
  --use-shell-env
      Prefer ambient shell vars (DISPLAY/XDG_SESSION_TYPE/etc.) before host
      session discovery.
  --mode auto|wayland|x11|drm|headless
      Override auto-detected runtime mode.
  --runtime-profile native-qt|auto
      Choose runtime policy profile:
      - native-qt: retained product profile
      - auto: alias for native-qt retained for compatibility
  --native-qt
      Shortcut for --runtime-profile native-qt

Environment:
  RELAYTV_INSTALL_CLEAN_AUTODETECT=1|0
      Controls default autodetect mode when no flag is provided.
  RELAYTV_INSTALL_MODE=auto|wayland|x11|drm|headless
      Default mode override when --mode is not provided.
  RELAYTV_INSTALL_RUNTIME_PROFILE=native-qt|auto
      Default runtime profile override when no profile flag is provided.
  RELAYTV_IMAGE_REF=ghcr.io/mcgeezy/relaytv:<tag>
      Optional published image reference written to .env for the
      `docker compose pull && docker compose up -d` flow.
  RELAYTV_CEC_ENABLED=auto|1|0
      Controls optional HDMI-CEC runtime control and device passthrough.
      Default auto detects CEC hardware and prompts when run interactively.
EOF
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    say "ERROR: Missing required command: $1" >&2
    exit 1
  }
}

need_cmd id
need_cmd date
need_cmd mktemp

CLEAN_AUTODETECT="${RELAYTV_INSTALL_CLEAN_AUTODETECT:-1}"
INSTALL_MODE="${RELAYTV_INSTALL_MODE:-auto}"
INSTALL_RUNTIME_PROFILE="${RELAYTV_INSTALL_RUNTIME_PROFILE:-native-qt}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean-autodetect)
      CLEAN_AUTODETECT="1"
      ;;
    --use-shell-env)
      CLEAN_AUTODETECT="0"
      ;;
    --mode)
      shift
      if [[ $# -eq 0 ]]; then
        say "ERROR: --mode requires a value." >&2
        usage >&2
        exit 2
      fi
      INSTALL_MODE="$1"
      ;;
    --runtime-profile)
      shift
      if [[ $# -eq 0 ]]; then
        say "ERROR: --runtime-profile requires a value." >&2
        usage >&2
        exit 2
      fi
      INSTALL_RUNTIME_PROFILE="$1"
      ;;
    --native-qt)
      INSTALL_RUNTIME_PROFILE="native-qt"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      say "ERROR: Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

INSTALL_MODE="$(printf "%s" "$INSTALL_MODE" | tr '[:upper:]' '[:lower:]')"
case "$INSTALL_MODE" in
  auto|wayland|x11|drm|headless) ;;
  *)
    say "ERROR: Invalid install mode: $INSTALL_MODE" >&2
    usage >&2
    exit 2
    ;;
esac

INSTALL_RUNTIME_PROFILE="$(printf "%s" "$INSTALL_RUNTIME_PROFILE" | tr '[:upper:]' '[:lower:]')"
case "$INSTALL_RUNTIME_PROFILE" in
  auto|native-qt) ;;
  *)
    say "ERROR: Invalid runtime profile: $INSTALL_RUNTIME_PROFILE" >&2
    usage >&2
    exit 2
    ;;
esac

# If run with sudo for one-time setup, prefer the invoking user.
TARGET_USER="${RELAYTV_TARGET_USER:-${SUDO_USER:-${USER:-$(id -un 2>/dev/null || true)}}}"
if [ -z "$TARGET_USER" ]; then
  say "ERROR: Could not determine target user." >&2
  exit 1
fi

PUID="$(id -u "$TARGET_USER")"
PGID="$(id -g "$TARGET_USER")"
# RELAYTV_TARGET_HOME and RELAYTV_HOST_PROFILE override autodetection for the
# test suite; they are not supported operator configuration.
TARGET_HOME="${RELAYTV_TARGET_HOME:-$(getent passwd "$TARGET_USER" | awk -F: '{print $6}')}"
HOST_ARCH="$(uname -m 2>/dev/null || printf '%s' unknown)"
HOST_MODEL=""
if [ -r /proc/device-tree/model ]; then
  HOST_MODEL="$(tr -d '\0' < /proc/device-tree/model | xargs || true)"
elif [ -r /proc/cpuinfo ]; then
  HOST_MODEL="$(awk -F: '/^Model[[:space:]]*:/{sub(/^[[:space:]]+/, "", $2); print $2; exit}' /proc/cpuinfo || true)"
fi

detect_host_profile() {
  local arch="$1"
  local model="$2"
  local model_lc
  model_lc="$(printf "%s" "$model" | tr '[:upper:]' '[:lower:]')"
  if [[ "$model_lc" == *raspberry*pi* ]]; then
    printf "raspi"
    return 0
  fi
  case "$arch" in
    x86_64|amd64)
      printf "amd64"
      ;;
    aarch64|arm64|armv8*|armv7*|armv6*)
      printf "arm"
      ;;
    *)
      printf "generic"
      ;;
  esac
}

HOST_PROFILE="${RELAYTV_HOST_PROFILE:-$(detect_host_profile "$HOST_ARCH" "$HOST_MODEL")}"

DISPLAY_VAL=""
XDG_SESSION_TYPE_VAL=""
WAYLAND_DISPLAY_VAL=""
QT_QPA_PLATFORM_VAL=""
QT_QPA_PLATFORM_FROM_ENV="0"
XDG_RUNTIME_DIR_VAL=""
if [ "$CLEAN_AUTODETECT" = "0" ]; then
  DISPLAY_VAL="${DISPLAY:-}"
  XDG_SESSION_TYPE_VAL="${XDG_SESSION_TYPE:-}"
  WAYLAND_DISPLAY_VAL="${WAYLAND_DISPLAY:-}"
  QT_QPA_PLATFORM_VAL="${QT_QPA_PLATFORM:-}"
  if [ -n "$QT_QPA_PLATFORM_VAL" ]; then
    QT_QPA_PLATFORM_FROM_ENV="1"
  fi
  XDG_RUNTIME_DIR_VAL="${XDG_RUNTIME_DIR:-}"
fi

PLAYER_BACKEND_VAL="${RELAYTV_PLAYER_BACKEND:-}"
QT_RUNTIME_MODE_VAL="${RELAYTV_QT_RUNTIME_MODE:-}"
QT_RUNTIME_MODE_FROM_ENV="0"
if [ "${RELAYTV_QT_RUNTIME_MODE+x}" = "x" ]; then
  QT_RUNTIME_MODE_FROM_ENV="1"
fi
QT_SHELL_MPV_ARGS_VAL="${RELAYTV_QT_SHELL_MPV_ARGS:-}"
QT_SHELL_MPV_ARGS_FROM_ENV="0"
if [ "${RELAYTV_QT_SHELL_MPV_ARGS+x}" = "x" ]; then
  QT_SHELL_MPV_ARGS_FROM_ENV="1"
fi
QT_SHELL_MODULE_VAL="${RELAYTV_QT_SHELL_MODULE:-}"
VIDEO_MODE_VAL="${RELAYTV_VIDEO_MODE:-}"
DRM_CONNECTOR_VAL="${RELAYTV_DRM_CONNECTOR:-}"
INSTALL_QT_BUNDLE_VAL="${RELAYTV_INSTALL_QT:-1}"
INSTALL_X11_OVERLAY_BUNDLE_VAL="${RELAYTV_INSTALL_X11_OVERLAY:-}"
INSTALL_HEADLESS_BUNDLE_VAL="${RELAYTV_INSTALL_HEADLESS:-}"
INSTALL_NODE_BUNDLE_VAL="${RELAYTV_INSTALL_NODE:-1}"
INSTALL_IDLE_BROWSER_BUNDLE_VAL="${RELAYTV_INSTALL_IDLE_BROWSER:-}"
INSTALL_OPS_TOOLS_BUNDLE_VAL="${RELAYTV_INSTALL_OPS_TOOLS:-}"
IMAGE_REF_VAL="${RELAYTV_IMAGE_REF:-}"
HEADLESS_REMOTE_ENABLED_VAL="${RELAYTV_HEADLESS_REMOTE_ENABLED:-}"
HEADLESS_REMOTE_DISPLAY_VAL="${RELAYTV_HEADLESS_REMOTE_DISPLAY:-}"
HEADLESS_REMOTE_RESOLUTION_VAL="${RELAYTV_HEADLESS_REMOTE_RESOLUTION:-}"
HEADLESS_REMOTE_SOFTWARE_VAL="${RELAYTV_HEADLESS_REMOTE_SOFTWARE:-}"
HEADLESS_VNC_ENABLED_VAL="${RELAYTV_HEADLESS_VNC_ENABLED:-}"
HEADLESS_VNC_LISTEN_VAL="${RELAYTV_HEADLESS_VNC_LISTEN:-}"
HEADLESS_VNC_PORT_VAL="${RELAYTV_HEADLESS_VNC_PORT:-}"
HEADLESS_VNC_PASSWORD_FILE_VAL="${RELAYTV_HEADLESS_VNC_PASSWORD_FILE:-}"
YTDLP_AUTO_UPDATE_VAL="${RELAYTV_YTDLP_AUTO_UPDATE:-}"
YTDLP_AUTO_UPDATE_INTERVAL_HOURS_VAL="${RELAYTV_YTDLP_AUTO_UPDATE_INTERVAL_HOURS:-}"
YTDLP_AUTO_UPDATE_TIMEOUT_SEC_VAL="${RELAYTV_YTDLP_AUTO_UPDATE_TIMEOUT_SEC:-}"
YTDLP_AUTO_UPDATE_STATE_FILE_VAL="${RELAYTV_YTDLP_AUTO_UPDATE_STATE_FILE:-}"
PI_VIDEO_DEVICES_ENABLED_VAL="${RELAYTV_PI_VIDEO_DEVICES_ENABLED:-}"
CEC_ENABLED_VAL="${RELAYTV_CEC_ENABLED:-auto}"
CEC_RUNTIME_VAL="${RELAYTV_CEC:-}"
CEC_MONITOR_VAL="${RELAYTV_CEC_MONITOR:-}"

# bcm2835-codec V4L2 decode/encode nodes. Present on Pi 4 and earlier; the
# Pi 5 dropped that codec block, so these nodes do not exist there.
PI_VIDEO_DECODE_NODES="/dev/video10 /dev/video11 /dev/video12 /dev/video13"

detect_pi_video_default() {
  local node
  if [ "$HOST_PROFILE" = "raspi" ]; then
    for node in $PI_VIDEO_DECODE_NODES; do
      if [ -e "$node" ]; then
        printf "1"
        return 0
      fi
    done
  fi
  printf "0"
}

if [ -z "$PI_VIDEO_DEVICES_ENABLED_VAL" ]; then
  PI_VIDEO_DEVICES_ENABLED_VAL="$(detect_pi_video_default)"
fi
case "$PI_VIDEO_DEVICES_ENABLED_VAL" in
  0|1) ;;
  *)
    say "WARN: RELAYTV_PI_VIDEO_DEVICES_ENABLED must be 0 or 1. Falling back to auto."
    PI_VIDEO_DEVICES_ENABLED_VAL="$(detect_pi_video_default)"
    ;;
esac
case "$CEC_ENABLED_VAL" in
  auto|0|1) ;;
  *)
    say "WARN: RELAYTV_CEC_ENABLED must be auto, 0, or 1. Falling back to auto."
    CEC_ENABLED_VAL="auto"
    ;;
esac

detect_from_session_leader() {
  local leader="$1"
  [ -n "$leader" ] || return 0
  [ -r "/proc/${leader}/environ" ] || return 0

  local disp=""
  local wayland=""
  local xdg_runtime=""
  disp="$(tr '\0' '\n' < "/proc/${leader}/environ" | awk -F= '$1=="DISPLAY"{print $2; exit}')"
  wayland="$(tr '\0' '\n' < "/proc/${leader}/environ" | awk -F= '$1=="WAYLAND_DISPLAY"{print $2; exit}')"
  xdg_runtime="$(tr '\0' '\n' < "/proc/${leader}/environ" | awk -F= '$1=="XDG_RUNTIME_DIR"{print $2; exit}')"

  if [ "$CLEAN_AUTODETECT" = "1" ]; then
    DISPLAY_VAL="$disp"
  elif [ -z "$DISPLAY_VAL" ] && [ -n "$disp" ]; then
    DISPLAY_VAL="$disp"
  fi
  if [ "$CLEAN_AUTODETECT" = "1" ]; then
    WAYLAND_DISPLAY_VAL="$wayland"
  elif [ -z "$WAYLAND_DISPLAY_VAL" ] && [ -n "$wayland" ]; then
    WAYLAND_DISPLAY_VAL="$wayland"
  fi
  if [ "$CLEAN_AUTODETECT" = "1" ]; then
    XDG_RUNTIME_DIR_VAL="$xdg_runtime"
  elif [ -z "$XDG_RUNTIME_DIR_VAL" ] && [ -n "$xdg_runtime" ]; then
    XDG_RUNTIME_DIR_VAL="$xdg_runtime"
  fi
}

detect_via_loginctl() {
  command -v loginctl >/dev/null 2>&1 || return 0

  # Prefer active seat0 session for target user.
  local sid=""
  sid="$(
    loginctl list-sessions --no-legend 2>/dev/null \
      | awk -v u="$TARGET_USER" '$3 == u && $4 == "seat0" {print $1; exit}'
  )" || true

  # SSH install fallback: if target user seat0 session is not visible, try any
  # graphical seat0 session and extract its display/auth details.
  if [ -z "$sid" ]; then
    local cand typ
    while read -r cand _uid _user seat _rest; do
      [ "${seat:-}" = "seat0" ] || continue
      typ="$(loginctl show-session "$cand" -p Type --value 2>/dev/null || true)"
      case "$typ" in
        x11|wayland)
          sid="$cand"
          break
          ;;
      esac
    done < <(loginctl list-sessions --no-legend 2>/dev/null || true)
  fi

  [ -n "$sid" ] || return 0

  local typ disp leader
  typ="$(loginctl show-session "$sid" -p Type --value 2>/dev/null || true)"
  disp="$(loginctl show-session "$sid" -p Display --value 2>/dev/null || true)"
  leader="$(loginctl show-session "$sid" -p Leader --value 2>/dev/null || true)"

  if [ "$typ" = "x11" ] || [ "$typ" = "wayland" ]; then
    XDG_SESSION_TYPE_VAL="$typ"
  fi
  if [ "$CLEAN_AUTODETECT" = "1" ]; then
    DISPLAY_VAL="$disp"
  elif [ -z "$DISPLAY_VAL" ] && [ -n "$disp" ]; then
    DISPLAY_VAL="$disp"
  fi

  detect_from_session_leader "$leader"
}

# First attempt: infer from active graphical session (works from SSH/sudo context).
detect_via_loginctl

# Additional fallback for shell-env mode only: infer DISPLAY from X11 sockets.
# Clean autodetect intentionally avoids this to prevent stale/container sockets.
if [ "$CLEAN_AUTODETECT" = "0" ] && [ -z "$DISPLAY_VAL" ] && [ "${XDG_SESSION_TYPE_VAL:-}" != "wayland" ] && [ -d /tmp/.X11-unix ]; then
  x_sock="$(ls /tmp/.X11-unix/X* 2>/dev/null | head -n 1 || true)"
  if [ -n "$x_sock" ]; then
    x_num="${x_sock##*/X}"
    if [[ "$x_num" =~ ^[0-9]+$ ]]; then
      DISPLAY_VAL=":${x_num}"
    fi
  fi
fi

# If still missing and this looks like an X11 host, default to :0.
if [ -z "$DISPLAY_VAL" ] && [ "${XDG_SESSION_TYPE_VAL:-}" = "x11" ]; then
  DISPLAY_VAL=":0"
fi

if [ -z "$XDG_SESSION_TYPE_VAL" ]; then
  if [ -n "$DISPLAY_VAL" ]; then
    XDG_SESSION_TYPE_VAL="x11"
  else
    XDG_SESSION_TYPE_VAL="tty"
  fi
elif [ "$XDG_SESSION_TYPE_VAL" = "tty" ] && [ -n "$DISPLAY_VAL" ]; then
  XDG_SESSION_TYPE_VAL="x11"
fi

# XDG runtime dir should match target uid.
if [ -z "$XDG_RUNTIME_DIR_VAL" ]; then
  XDG_RUNTIME_DIR_VAL="/run/user/${PUID}"
fi
if [ ! -d "$XDG_RUNTIME_DIR_VAL" ]; then
  XDG_RUNTIME_DIR_VAL="/tmp"
fi

if [ -z "$WAYLAND_DISPLAY_VAL" ] && [ "$XDG_SESSION_TYPE_VAL" = "wayland" ] && [ -d "$XDG_RUNTIME_DIR_VAL" ]; then
  wayland_sock="$(ls "$XDG_RUNTIME_DIR_VAL"/wayland-* 2>/dev/null | head -n 1 || true)"
  if [ -n "$wayland_sock" ]; then
    WAYLAND_DISPLAY_VAL="$(basename "$wayland_sock")"
  fi
fi

detect_connected_drm_connector() {
  local status_file status name connector
  for status_file in /sys/class/drm/card*-*/status; do
    [ -r "$status_file" ] || continue
    name="$(basename "$(dirname "$status_file")")"
    case "${name,,}" in
      *virtual*|*writeback*) continue ;;
    esac
    status="$(tr -d '[:space:]' < "$status_file" | tr '[:upper:]' '[:lower:]')"
    if [ "$status" = "connected" ]; then
      connector="${name#*-}"
      printf "%s" "$connector"
      return 0
    fi
  done
  printf ""
}

DRM_CONNECTOR_DETECTED="$(detect_connected_drm_connector)"
DRM_CONNECTED="0"
if [ -n "$DRM_CONNECTOR_DETECTED" ]; then
  DRM_CONNECTED="1"
fi

MODE=""
if [ "$INSTALL_MODE" != "auto" ]; then
  MODE="$INSTALL_MODE"
elif [ "$XDG_SESSION_TYPE_VAL" = "wayland" ] || [ -n "$WAYLAND_DISPLAY_VAL" ]; then
  MODE="wayland"
elif [ "$XDG_SESSION_TYPE_VAL" = "x11" ] || [ -n "$DISPLAY_VAL" ]; then
  MODE="x11"
elif [ "$DRM_CONNECTED" = "1" ]; then
  MODE="drm"
fi

if [ -z "$MODE" ]; then
  say "ERROR: No active display session or connected DRM/KMS output detected." >&2
  say "Connect an HDMI/DP display and re-run ./scripts/install.sh." >&2
  say "If you intentionally want remote-only runtime, run with --mode headless." >&2
  exit 3
fi

if [ "$MODE" = "drm" ] && [ "$DRM_CONNECTED" != "1" ]; then
  say "ERROR: DRM/KMS mode requested, but no connected DRM output was detected." >&2
  say "Connect an HDMI/DP display and re-run ./scripts/install.sh." >&2
  exit 3
fi

# Wayland sessions often expose Xwayland for xcb delegate runtime; infer DISPLAY
# when missing so overlay notifications can stay visible on this host profile.
if [ "$MODE" = "wayland" ] && [ -z "$DISPLAY_VAL" ] && [ -d /tmp/.X11-unix ]; then
  x_sock="$(ls /tmp/.X11-unix/X* 2>/dev/null | head -n 1 || true)"
  if [ -n "$x_sock" ]; then
    x_num="${x_sock##*/X}"
    if [[ "$x_num" =~ ^[0-9]+$ ]]; then
      DISPLAY_VAL=":${x_num}"
    fi
  fi
fi

if [ -z "$QT_QPA_PLATFORM_VAL" ]; then
  case "$MODE" in
    wayland) QT_QPA_PLATFORM_VAL="wayland" ;;
    x11) QT_QPA_PLATFORM_VAL="xcb" ;;
    drm) QT_QPA_PLATFORM_VAL="offscreen" ;;
    *) QT_QPA_PLATFORM_VAL="offscreen" ;;
  esac
fi

if [ -z "$HEADLESS_REMOTE_ENABLED_VAL" ]; then
  if [ "$MODE" = "headless" ]; then
    HEADLESS_REMOTE_ENABLED_VAL="1"
  else
    HEADLESS_REMOTE_ENABLED_VAL="0"
  fi
fi
if [ -z "$HEADLESS_REMOTE_DISPLAY_VAL" ]; then
  HEADLESS_REMOTE_DISPLAY_VAL=":99"
fi
if [ -z "$HEADLESS_REMOTE_RESOLUTION_VAL" ]; then
  HEADLESS_REMOTE_RESOLUTION_VAL="1920x1080x24"
fi
if [ -z "$HEADLESS_REMOTE_SOFTWARE_VAL" ]; then
  HEADLESS_REMOTE_SOFTWARE_VAL="1"
fi
if [ -z "$HEADLESS_VNC_ENABLED_VAL" ]; then
  HEADLESS_VNC_ENABLED_VAL="$HEADLESS_REMOTE_ENABLED_VAL"
fi
if [ -z "$HEADLESS_VNC_LISTEN_VAL" ]; then
  HEADLESS_VNC_LISTEN_VAL="127.0.0.1"
fi
if [ -z "$HEADLESS_VNC_PORT_VAL" ]; then
  HEADLESS_VNC_PORT_VAL="5900"
fi
if [ -z "$YTDLP_AUTO_UPDATE_VAL" ]; then
  YTDLP_AUTO_UPDATE_VAL="0"
fi
if [ -z "$YTDLP_AUTO_UPDATE_INTERVAL_HOURS_VAL" ]; then
  YTDLP_AUTO_UPDATE_INTERVAL_HOURS_VAL="24"
fi
if [ -z "$YTDLP_AUTO_UPDATE_TIMEOUT_SEC_VAL" ]; then
  YTDLP_AUTO_UPDATE_TIMEOUT_SEC_VAL="180"
fi
if [ -z "$YTDLP_AUTO_UPDATE_STATE_FILE_VAL" ]; then
  YTDLP_AUTO_UPDATE_STATE_FILE_VAL="/data/.relaytv-ytdlp-update.json"
fi
if [ "$HEADLESS_REMOTE_ENABLED_VAL" = "1" ] && [ "$MODE" = "headless" ] && [ -z "${QT_QPA_PLATFORM:-}" ]; then
  QT_QPA_PLATFORM_VAL="xcb"
fi

RUNTIME_PROFILE_EFFECTIVE="$INSTALL_RUNTIME_PROFILE"
if [ "$RUNTIME_PROFILE_EFFECTIVE" = "auto" ]; then
  RUNTIME_PROFILE_EFFECTIVE="native-qt"
fi

if [ -z "$PLAYER_BACKEND_VAL" ]; then
  case "$MODE" in
    drm) PLAYER_BACKEND_VAL="mpv" ;;
    *) PLAYER_BACKEND_VAL="qt" ;;
  esac
fi

if [ -z "$QT_SHELL_MODULE_VAL" ]; then
  QT_SHELL_MODULE_VAL="relaytv_app.qt_shell_app"
fi

if [ "$HOST_PROFILE" = "raspi" ] && [ "$MODE" = "wayland" ] && [ "$QT_SHELL_MODULE_VAL" = "relaytv_app.qt_shell_app" ]; then
  # Raspberry Pi Wayland/libmpv is not reliably visible with native Wayland Qt.
  # Prefer Xwayland unless the operator explicitly selected a Qt platform.
  if [ "$QT_QPA_PLATFORM_FROM_ENV" != "1" ]; then
    QT_QPA_PLATFORM_VAL="xcb"
  fi
fi

needs_stable_xauthority_bind() {
  # X11 and explicit Xwayland bridge mode need a stable cookie file visible
  # inside the container. Mutter session-scoped runtime cookies are handled
  # through the mounted /run/user parent instead.
  { [ "$MODE" = "x11" ] || [[ "${QT_QPA_PLATFORM_VAL}" == xcb* ]] || [ "${QT_QPA_PLATFORM_VAL}" = "x11" ]; } \
    && [ -n "${TARGET_HOME:-}" ] && [ -f "${TARGET_HOME}/.Xauthority" ]
}

if [ -z "$QT_RUNTIME_MODE_VAL" ]; then
  case "$MODE" in
    wayland|x11) QT_RUNTIME_MODE_VAL="embed" ;;
    *) QT_RUNTIME_MODE_VAL="auto" ;;
  esac
fi

X11_OVERLAY_VAL="0"
if [ "$MODE" = "x11" ]; then
  X11_OVERLAY_VAL="1"
fi

if [ -z "$INSTALL_X11_OVERLAY_BUNDLE_VAL" ]; then
  if [ "$X11_OVERLAY_VAL" = "1" ]; then
    INSTALL_X11_OVERLAY_BUNDLE_VAL="1"
  else
    INSTALL_X11_OVERLAY_BUNDLE_VAL="0"
  fi
fi
if [ -z "$INSTALL_HEADLESS_BUNDLE_VAL" ]; then
  if [ "$HEADLESS_REMOTE_ENABLED_VAL" = "1" ] || [ "$MODE" = "headless" ]; then
    INSTALL_HEADLESS_BUNDLE_VAL="1"
  else
    INSTALL_HEADLESS_BUNDLE_VAL="0"
  fi
fi
if [ -z "$INSTALL_NODE_BUNDLE_VAL" ]; then
  INSTALL_NODE_BUNDLE_VAL="0"
fi
if [ -z "$INSTALL_IDLE_BROWSER_BUNDLE_VAL" ]; then
  INSTALL_IDLE_BROWSER_BUNDLE_VAL="0"
fi
if [ -z "$INSTALL_OPS_TOOLS_BUNDLE_VAL" ]; then
  INSTALL_OPS_TOOLS_BUNDLE_VAL="0"
fi
for bundle_key in \
  INSTALL_QT_BUNDLE_VAL \
  INSTALL_X11_OVERLAY_BUNDLE_VAL \
  INSTALL_HEADLESS_BUNDLE_VAL \
  INSTALL_NODE_BUNDLE_VAL \
  INSTALL_IDLE_BROWSER_BUNDLE_VAL \
  INSTALL_OPS_TOOLS_BUNDLE_VAL; do
  case "${!bundle_key}" in
    0|1) ;;
    *)
      say "WARN: ${bundle_key} must be 0 or 1. Falling back to default."
      case "$bundle_key" in
        INSTALL_QT_BUNDLE_VAL) INSTALL_QT_BUNDLE_VAL="1" ;;
        INSTALL_X11_OVERLAY_BUNDLE_VAL) INSTALL_X11_OVERLAY_BUNDLE_VAL="0" ;;
        INSTALL_HEADLESS_BUNDLE_VAL) INSTALL_HEADLESS_BUNDLE_VAL="0" ;;
        INSTALL_NODE_BUNDLE_VAL) INSTALL_NODE_BUNDLE_VAL="0" ;;
        INSTALL_IDLE_BROWSER_BUNDLE_VAL) INSTALL_IDLE_BROWSER_BUNDLE_VAL="0" ;;
        INSTALL_OPS_TOOLS_BUNDLE_VAL) INSTALL_OPS_TOOLS_BUNDLE_VAL="0" ;;
      esac
      ;;
  esac
done

if [ -z "$VIDEO_MODE_VAL" ]; then
  case "$MODE" in
    drm) VIDEO_MODE_VAL="drm" ;;
    x11) VIDEO_MODE_VAL="x11" ;;
    *) VIDEO_MODE_VAL="auto" ;;
  esac
fi

if [ -z "$DRM_CONNECTOR_VAL" ] && [ "$MODE" = "drm" ]; then
  DRM_CONNECTOR_VAL="$DRM_CONNECTOR_DETECTED"
fi

detect_render_gid() {
  # Allow explicit override when needed.
  if [ -n "${RELAYTV_RENDER_GID:-}" ]; then
    printf "%s" "${RELAYTV_RENDER_GID}"
    return 0
  fi

  # Best source: actual render node gid from host.
  if [ -e /dev/dri/renderD128 ] && command -v stat >/dev/null 2>&1; then
    local gid=""
    gid="$(stat -c '%g' /dev/dri/renderD128 2>/dev/null || true)"
    if [[ "$gid" =~ ^[0-9]+$ ]]; then
      printf "%s" "$gid"
      return 0
    fi
  fi

  # Fallback: system render group gid, if present.
  if command -v getent >/dev/null 2>&1; then
    local gid=""
    gid="$(getent group render 2>/dev/null | awk -F: '{print $3}' | head -n1 || true)"
    if [[ "$gid" =~ ^[0-9]+$ ]]; then
      printf "%s" "$gid"
      return 0
    fi
  fi

  # Conservative default (common on amd64).
  printf "992"
}

RENDER_GID_DETECTED="$(detect_render_gid)"
AUDIO_DEVICE_VAL="${MPV_AUDIO_DEVICE:-}"

docker_tag_sanitize() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9._-]+/-/g; s/^-+//; s/-+$//; s/\.\.+/./g'
}

detect_default_image_ref() {
  local image_repo="ghcr.io/mcgeezy/relaytv"
  local exact_tag=""
  local branch_name=""

  exact_tag="$(git describe --exact-match --tags 2>/dev/null || true)"
  if [ -n "$exact_tag" ]; then
    printf '%s:%s' "$image_repo" "$(docker_tag_sanitize "$exact_tag")"
    return 0
  fi

  branch_name="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  case "$branch_name" in
    ""|HEAD|main|master)
      printf '%s:latest' "$image_repo"
      ;;
    *)
      printf '%s:%s' "$image_repo" "$(docker_tag_sanitize "$branch_name")"
      ;;
  esac
}

if [ -z "$IMAGE_REF_VAL" ]; then
  IMAGE_REF_VAL="$(detect_default_image_ref)"
fi

detect_cec_device_nodes() {
  local node
  for node in /dev/cec*; do
    [ -e "$node" ] || continue
    printf "%s\n" "$node"
  done
  if command -v cec-client >/dev/null 2>&1; then
    cec-client -l 2>/dev/null \
      | grep -Eo '/dev/(cec[0-9]+|ttyACM[0-9]+)' \
      || true
  fi
}

detect_cec_device_group_ids() {
  local node
  while IFS= read -r node; do
    [ -n "$node" ] || continue
    [ -e "$node" ] || continue
    stat -c '%g' "$node" 2>/dev/null || true
  done < <(detect_cec_device_nodes | sort -u)
}

resolve_cec_enabled() {
  local requested="$1"
  local summary="$2"
  if [ "$requested" = "0" ]; then
    printf "0"
    return 0
  fi
  if [ "$requested" = "1" ]; then
    printf "1"
    return 0
  fi
  if [ -z "$summary" ]; then
    printf "0"
    return 0
  fi
  if [ "${RELAYTV_INSTALL_YES:-0}" = "1" ] || [ ! -r /dev/tty ]; then
    printf "0"
    return 0
  fi

  {
    say ""
    say "HDMI-CEC hardware was detected: $summary"
    say "Enable optional TV controls?"
    say "This lets RelayTV turn the TV on, switch inputs, and monitor standby/source changes."
    say "Choose no if this device shares HDMI with equipment you do not want RelayTV to control."
    printf "Enable HDMI-CEC passthrough? [y/N] "
  } > /dev/tty
  local answer=""
  read -r answer < /dev/tty || true
  case "${answer,,}" in
    y|yes) printf "1" ;;
    *) printf "0" ;;
  esac
}

detect_nvidia_device() {
  if [ -e /dev/nvidiactl ] || [ -e /proc/driver/nvidia/version ]; then
    printf "1"
    return 0
  fi
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    printf "1"
    return 0
  fi
  printf "0"
}

detect_nvidia_docker_toolkit() {
  if command -v docker >/dev/null 2>&1 && docker info 2>/dev/null | grep -qi 'nvidia'; then
    printf "1"
    return 0
  fi
  if command -v nvidia-container-cli >/dev/null 2>&1 && nvidia-container-cli info >/dev/null 2>&1; then
    printf "1"
    return 0
  fi
  printf "0"
}

# Devices every display/audio-capable host should pass through. The base
# compose files carry no device mappings (a mapped-but-missing device makes
# compose refuse to create the container), so the generated override is the
# only device passthrough source and probes existence for each node.
CORE_DEVICE_NODES="/dev/snd /dev/dri"

yaml_double_quote() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '"%s"' "$value"
}

selinux_is_enforcing() {
  if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce 2>/dev/null || true)" = "Enforcing" ]; then
    return 0
  fi
  [ -r /sys/fs/selinux/enforce ] && [ "$(cat /sys/fs/selinux/enforce 2>/dev/null || true)" = "1" ]
}

ensure_host_device_override() {
  local pi_enabled="$1"
  local cec_enabled="$2"
  local nvidia_enabled="$3"
  local override_file="$ROOT/docker-compose.override.yml"
  local override_tmp=""
  local marker="# Generated by scripts/install.sh (host-device-overrides)"
  local devices_block=""
  local group_block=""
  local nvidia_block=""
  local volumes_block=""
  local security_block=""
  local node
  local gid

  if [ -f "$override_file" ] &&
    ! grep -qF "$marker" "$override_file" &&
    ! grep -qF "# Generated by scripts/install.sh (pi-video-devices)" "$override_file"; then
    say "ERROR: $override_file is user-managed; refusing to overwrite it." >&2
    say "Move or merge that file before rerunning scripts/install.sh." >&2
    return 1
  fi

  append_bind_mount() {
    local source="$1" target="$2" read_only="$3"
    [ -e "$source" ] || return 0
    if [[ "$source$target" == *$'\n'* || "$source$target" == *$'\r'* || "$source$target" == *$'\t'* ]]; then
      return 0
    fi
    volumes_block+="      - type: bind"$'\n'
    volumes_block+="        source: $(yaml_double_quote "$source")"$'\n'
    volumes_block+="        target: $(yaml_double_quote "$target")"$'\n'
    volumes_block+="        read_only: ${read_only}"$'\n'
    volumes_block+="        bind:"$'\n'
    volumes_block+="          create_host_path: false"$'\n'
  }

  append_bind_mount "/etc/localtime" "/etc/localtime" "true"
  append_bind_mount "/sys" "/sys" "true"
  append_bind_mount "/run/udev" "/run/udev" "true"
  if [ -n "$DISPLAY_VAL" ] || [ "$MODE" = "x11" ]; then
    append_bind_mount "/tmp/.X11-unix" "/tmp/.X11-unix" "false"
  fi
  # Mutter rotates its .mutter-Xwaylandauth.* filename with each graphical
  # session. Mount the stable runtime parent and let RelayTV resolve the live
  # credential when it launches a display process.
  if [ -n "$XDG_RUNTIME_DIR_VAL" ] && { [ "$MODE" = "wayland" ] || [ "$MODE" = "x11" ]; }; then
    if [[ "$XDG_RUNTIME_DIR_VAL" == /run/user/* ]] && [ -d /run/user ]; then
      append_bind_mount "/run/user" "/run/user" "false"
    elif [ "$XDG_RUNTIME_DIR_VAL" != "/tmp" ]; then
      append_bind_mount "$XDG_RUNTIME_DIR_VAL" "$XDG_RUNTIME_DIR_VAL" "false"
    fi
  fi
  if needs_stable_xauthority_bind; then
    append_bind_mount "${TARGET_HOME}/.Xauthority" "/tmp/.Xauthority" "true"
  fi
  if selinux_is_enforcing; then
    security_block=$(cat <<'EOF'
    security_opt:
      - label=disable
EOF
)
  fi

  for node in $CORE_DEVICE_NODES; do
    [ -e "$node" ] || continue
    devices_block+="      - ${node}:${node}"$'\n'
  done
  if [ "$pi_enabled" = "1" ]; then
    # Map only nodes the host actually exposes: docker compose refuses to
    # create a container whose device mapping points at a missing path, so an
    # unconditional list breaks hosts without bcm2835-codec (e.g. Pi 5).
    for node in $PI_VIDEO_DECODE_NODES; do
      [ -e "$node" ] || continue
      devices_block+="      - ${node}:${node}"$'\n'
    done
  fi
  if [ "$cec_enabled" = "1" ]; then
    while IFS= read -r node; do
      [ -n "$node" ] || continue
      devices_block+="      - ${node}:${node}"$'\n'
    done < <(detect_cec_device_nodes | sort -u)
    while IFS= read -r gid; do
      [ -n "$gid" ] || continue
      group_block+="      - \"${gid}\""$'\n'
    done < <(detect_cec_device_group_ids | sort -n -u)
  fi
  if [ "$nvidia_enabled" = "1" ]; then
    nvidia_block=$(cat <<'EOF'
    gpus: all
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
      - NVIDIA_DRIVER_CAPABILITIES=compute,video,graphics,utility
EOF
)
  fi

  if [ -n "$volumes_block" ] || [ -n "$devices_block" ] || [ -n "$group_block" ] || [ -n "$nvidia_block" ] || [ -n "$security_block" ]; then
    override_tmp="$(mktemp "$ROOT/.docker-compose.override.yml.tmp.XXXXXX")"
    cat > "$override_tmp" <<EOF
$marker
services:
  relaytv:
EOF
    if [ -n "$volumes_block" ]; then
      cat >> "$override_tmp" <<EOF
    volumes:
${volumes_block%$'\n'}
EOF
    fi
    if [ -n "$devices_block" ]; then
      cat >> "$override_tmp" <<EOF
    devices:
${devices_block%$'\n'}
EOF
    fi
    if [ -n "$group_block" ]; then
      cat >> "$override_tmp" <<EOF
    group_add:
${group_block%$'\n'}
EOF
    fi
    if [ -n "$nvidia_block" ]; then
      cat >> "$override_tmp" <<EOF
${nvidia_block}
EOF
    fi
    if [ -n "$security_block" ]; then
      cat >> "$override_tmp" <<EOF
${security_block}
EOF
    fi
    mv -f "$override_tmp" "$override_file"
    if [ "$(id -u)" -eq 0 ]; then
      chown "${PUID}:${PGID}" "$override_file" 2>/dev/null || true
    fi
  else
    if [ -f "$override_file" ] && grep -qF "$marker" "$override_file"; then
      rm -f "$override_file"
    elif [ -f "$override_file" ] && grep -qF "# Generated by scripts/install.sh (pi-video-devices)" "$override_file"; then
      rm -f "$override_file"
    fi
  fi
}

check_pi_video_nodes() {
  local missing=""
  local node
  for node in $PI_VIDEO_DECODE_NODES; do
    if [ ! -e "$node" ]; then
      if [ -z "$missing" ]; then
        missing="$node"
      else
        missing="$missing $node"
      fi
    fi
  done
  printf "%s" "$missing"
}

NVIDIA_DEVICE_DETECTED="$(detect_nvidia_device)"
NVIDIA_DOCKER_TOOLKIT_AVAILABLE="$(detect_nvidia_docker_toolkit)"
NVIDIA_PASSTHROUGH_ENABLED="0"
if [ "$NVIDIA_DEVICE_DETECTED" = "1" ] && [ "$NVIDIA_DOCKER_TOOLKIT_AVAILABLE" = "1" ]; then
  NVIDIA_PASSTHROUGH_ENABLED="1"
fi

CEC_NODES_DETECTED="$(detect_cec_device_nodes | sort -u | paste -sd, - 2>/dev/null || true)"
CEC_ENABLED_VAL="$(resolve_cec_enabled "$CEC_ENABLED_VAL" "$CEC_NODES_DETECTED")"
if [ -z "$CEC_RUNTIME_VAL" ]; then
  CEC_RUNTIME_VAL="$CEC_ENABLED_VAL"
fi
if [ -z "$CEC_MONITOR_VAL" ]; then
  CEC_MONITOR_VAL="$CEC_ENABLED_VAL"
fi
case "$CEC_RUNTIME_VAL" in
  0|1) ;;
  *)
    say "WARN: RELAYTV_CEC must be 0 or 1. Falling back to RELAYTV_CEC_ENABLED=${CEC_ENABLED_VAL}."
    CEC_RUNTIME_VAL="$CEC_ENABLED_VAL"
    ;;
esac
case "$CEC_MONITOR_VAL" in
  0|1) ;;
  *)
    say "WARN: RELAYTV_CEC_MONITOR must be 0 or 1. Falling back to RELAYTV_CEC=${CEC_RUNTIME_VAL}."
    CEC_MONITOR_VAL="$CEC_RUNTIME_VAL"
    ;;
esac

ensure_host_device_override "$PI_VIDEO_DEVICES_ENABLED_VAL" "$CEC_ENABLED_VAL" "$NVIDIA_PASSTHROUGH_ENABLED"
PI_VIDEO_NODES_MISSING=""
if [ "$PI_VIDEO_DEVICES_ENABLED_VAL" = "1" ]; then
  PI_VIDEO_NODES_MISSING="$(check_pi_video_nodes)"
fi
DRI_NODE_MISSING="0"
if [ ! -e /dev/dri ]; then
  DRI_NODE_MISSING="1"
fi
SND_NODE_MISSING="0"
if [ ! -e /dev/snd ]; then
  SND_NODE_MISSING="1"
fi
CEC_NODES_MISSING="0"
if [ "$CEC_ENABLED_VAL" = "1" ] && [ -z "$CEC_NODES_DETECTED" ]; then
  CEC_NODES_MISSING="1"
fi

ensure_data_dir_ownership() {
  local data_dir="$ROOT/data"
  mkdir -p "$data_dir"

  # Installer commonly runs via sudo for first turn-up; normalize ownership
  # so compose bind-mount does not leave runtime state unwritable.
  if [ "$(id -u)" -eq 0 ]; then
    chown -R "${PUID}:${PGID}" "$data_dir" 2>/dev/null || true
    return 0
  fi

  local cur_uid="" cur_gid=""
  if command -v stat >/dev/null 2>&1; then
    cur_uid="$(stat -c '%u' "$data_dir" 2>/dev/null || true)"
    cur_gid="$(stat -c '%g' "$data_dir" 2>/dev/null || true)"
  fi
  if [ -n "$cur_uid" ] && [ -n "$cur_gid" ] && { [ "$cur_uid" != "$PUID" ] || [ "$cur_gid" != "$PGID" ]; }; then
    say "WARN: $data_dir ownership is ${cur_uid}:${cur_gid}; expected ${PUID}:${PGID}. Re-run install with sudo to repair."
  fi
}

ensure_data_dir_ownership

emit_env_line() {
  printf '%s=%s\n' "$1" "$2"
}

emit_section() {
  local header="$1" body="$2"
  if [ -z "$body" ]; then
    return 0
  fi
  printf '\n# %s\n' "$header"
  printf '%s' "$body"
}

installer_owned_env_key() {
  case "$1" in
    PUID|PGID|DISPLAY|XAUTHORITY|XDG_SESSION_TYPE|XDG_RUNTIME_DIR|WAYLAND_DISPLAY|QT_QPA_PLATFORM|MPV_AUDIO_DEVICE|\
    RELAYTV_CEC|RELAYTV_CEC_ENABLED|RELAYTV_CEC_MONITOR|RELAYTV_DRM_CONNECTOR|\
    RELAYTV_HEADLESS_REMOTE_DISPLAY|RELAYTV_HEADLESS_REMOTE_ENABLED|RELAYTV_HEADLESS_REMOTE_RESOLUTION|\
    RELAYTV_HEADLESS_REMOTE_SOFTWARE|RELAYTV_HEADLESS_VNC_ENABLED|RELAYTV_HEADLESS_VNC_LISTEN|\
    RELAYTV_HEADLESS_VNC_PASSWORD_FILE|RELAYTV_HEADLESS_VNC_PORT|RELAYTV_HOST_PROFILE|\
    RELAYTV_HOST_SESSION_TYPE|RELAYTV_IMAGE_REF|RELAYTV_INSTALL_HEADLESS|RELAYTV_INSTALL_IDLE_BROWSER|\
    RELAYTV_INSTALL_NODE|RELAYTV_INSTALL_OPS_TOOLS|RELAYTV_INSTALL_QT|RELAYTV_INSTALL_X11_OVERLAY|\
    RELAYTV_MODE|RELAYTV_PLAYER_BACKEND|RELAYTV_QT_RUNTIME_MODE|RELAYTV_QT_SHELL_MODULE|\
    RELAYTV_QT_SHELL_MPV_ARGS|RELAYTV_RENDER_GID|RELAYTV_VIDEO_MODE|RELAYTV_X11_OVERLAY|\
    RELAYTV_XAUTHORITY_HOST_PATH|RELAYTV_YTDLP_AUTO_UPDATE|RELAYTV_YTDLP_AUTO_UPDATE_INTERVAL_HOURS|\
    RELAYTV_YTDLP_AUTO_UPDATE_STATE_FILE|RELAYTV_YTDLP_AUTO_UPDATE_TIMEOUT_SEC)
      return 0
      ;;
    *) return 1 ;;
  esac
}

DISPLAY_ENV_BLOCK=""
RUNTIME_ENV_BLOCK=""
BUILD_ENV_BLOCK=""
HEADLESS_ENV_BLOCK=""
YTDLP_ENV_BLOCK=""
RENDER_ENV_BLOCK=""
AUDIO_ENV_BLOCK=""
IMAGE_ENV_BLOCK=""
HOST_ENV_BLOCK=""

if [ -n "${DISPLAY_VAL}" ]; then
  DISPLAY_ENV_BLOCK+=$(emit_env_line "DISPLAY" "${DISPLAY_VAL}")
  DISPLAY_ENV_BLOCK+=$'\n'
fi
if [ -n "${XDG_SESSION_TYPE_VAL}" ]; then
  DISPLAY_ENV_BLOCK+=$(emit_env_line "XDG_SESSION_TYPE" "${XDG_SESSION_TYPE_VAL}")
  DISPLAY_ENV_BLOCK+=$'\n'
  DISPLAY_ENV_BLOCK+=$(emit_env_line "RELAYTV_HOST_SESSION_TYPE" "${XDG_SESSION_TYPE_VAL}")
  DISPLAY_ENV_BLOCK+=$'\n'
fi
if [ "${XDG_RUNTIME_DIR_VAL}" != "/run/user/${PUID}" ]; then
  DISPLAY_ENV_BLOCK+=$(emit_env_line "XDG_RUNTIME_DIR" "${XDG_RUNTIME_DIR_VAL}")
  DISPLAY_ENV_BLOCK+=$'\n'
fi
if [ -n "${WAYLAND_DISPLAY_VAL}" ]; then
  DISPLAY_ENV_BLOCK+=$(emit_env_line "WAYLAND_DISPLAY" "${WAYLAND_DISPLAY_VAL}")
  DISPLAY_ENV_BLOCK+=$'\n'
fi
if [ -n "${QT_QPA_PLATFORM_VAL}" ]; then
  DISPLAY_ENV_BLOCK+=$(emit_env_line "QT_QPA_PLATFORM" "${QT_QPA_PLATFORM_VAL}")
  DISPLAY_ENV_BLOCK+=$'\n'
fi
if needs_stable_xauthority_bind; then
  DISPLAY_ENV_BLOCK+=$(emit_env_line "XAUTHORITY" "/tmp/.Xauthority")
  DISPLAY_ENV_BLOCK+=$'\n'
fi
RUNTIME_ENV_BLOCK+=$(emit_env_line "RELAYTV_MODE" "${MODE}")
RUNTIME_ENV_BLOCK+=$'\n'
if [ "${X11_OVERLAY_VAL}" != "0" ]; then
  RUNTIME_ENV_BLOCK+=$(emit_env_line "RELAYTV_X11_OVERLAY" "${X11_OVERLAY_VAL}")
  RUNTIME_ENV_BLOCK+=$'\n'
fi
if [ "${VIDEO_MODE_VAL}" != "auto" ]; then
  RUNTIME_ENV_BLOCK+=$(emit_env_line "RELAYTV_VIDEO_MODE" "${VIDEO_MODE_VAL}")
  RUNTIME_ENV_BLOCK+=$'\n'
fi
if [ -n "${DRM_CONNECTOR_VAL}" ]; then
  RUNTIME_ENV_BLOCK+=$(emit_env_line "RELAYTV_DRM_CONNECTOR" "${DRM_CONNECTOR_VAL}")
  RUNTIME_ENV_BLOCK+=$'\n'
fi
if [ "${PLAYER_BACKEND_VAL}" != "qt" ]; then
  RUNTIME_ENV_BLOCK+=$(emit_env_line "RELAYTV_PLAYER_BACKEND" "${PLAYER_BACKEND_VAL}")
  RUNTIME_ENV_BLOCK+=$'\n'
fi
if [ "${QT_RUNTIME_MODE_FROM_ENV}" = "1" ]; then
  RUNTIME_ENV_BLOCK+=$(emit_env_line "RELAYTV_QT_RUNTIME_MODE" "${QT_RUNTIME_MODE_VAL}")
  RUNTIME_ENV_BLOCK+=$'\n'
fi
if [ "${QT_SHELL_MPV_ARGS_FROM_ENV}" = "1" ]; then
  RUNTIME_ENV_BLOCK+=$(emit_env_line "RELAYTV_QT_SHELL_MPV_ARGS" "${QT_SHELL_MPV_ARGS_VAL}")
  RUNTIME_ENV_BLOCK+=$'\n'
fi
if [ "${QT_SHELL_MODULE_VAL}" != "relaytv_app.qt_shell_app" ]; then
  RUNTIME_ENV_BLOCK+=$(emit_env_line "RELAYTV_QT_SHELL_MODULE" "${QT_SHELL_MODULE_VAL}")
  RUNTIME_ENV_BLOCK+=$'\n'
fi

if [ "${INSTALL_QT_BUNDLE_VAL}" != "1" ]; then
  BUILD_ENV_BLOCK+=$(emit_env_line "RELAYTV_INSTALL_QT" "${INSTALL_QT_BUNDLE_VAL}")
  BUILD_ENV_BLOCK+=$'\n'
fi
if [ "${INSTALL_X11_OVERLAY_BUNDLE_VAL}" != "0" ]; then
  BUILD_ENV_BLOCK+=$(emit_env_line "RELAYTV_INSTALL_X11_OVERLAY" "${INSTALL_X11_OVERLAY_BUNDLE_VAL}")
  BUILD_ENV_BLOCK+=$'\n'
fi
if [ "${INSTALL_HEADLESS_BUNDLE_VAL}" != "0" ]; then
  BUILD_ENV_BLOCK+=$(emit_env_line "RELAYTV_INSTALL_HEADLESS" "${INSTALL_HEADLESS_BUNDLE_VAL}")
  BUILD_ENV_BLOCK+=$'\n'
fi
if [ "${INSTALL_NODE_BUNDLE_VAL}" != "1" ]; then
  BUILD_ENV_BLOCK+=$(emit_env_line "RELAYTV_INSTALL_NODE" "${INSTALL_NODE_BUNDLE_VAL}")
  BUILD_ENV_BLOCK+=$'\n'
fi
if [ "${INSTALL_IDLE_BROWSER_BUNDLE_VAL}" != "0" ]; then
  BUILD_ENV_BLOCK+=$(emit_env_line "RELAYTV_INSTALL_IDLE_BROWSER" "${INSTALL_IDLE_BROWSER_BUNDLE_VAL}")
  BUILD_ENV_BLOCK+=$'\n'
fi
if [ "${INSTALL_OPS_TOOLS_BUNDLE_VAL}" != "0" ]; then
  BUILD_ENV_BLOCK+=$(emit_env_line "RELAYTV_INSTALL_OPS_TOOLS" "${INSTALL_OPS_TOOLS_BUNDLE_VAL}")
  BUILD_ENV_BLOCK+=$'\n'
fi

if [ "${IMAGE_REF_VAL}" != "ghcr.io/mcgeezy/relaytv:latest" ]; then
  IMAGE_ENV_BLOCK+=$(emit_env_line "RELAYTV_IMAGE_REF" "${IMAGE_REF_VAL}")
  IMAGE_ENV_BLOCK+=$'\n'
fi

if [ -n "${HOST_PROFILE}" ] && [ "${HOST_PROFILE}" != "generic" ]; then
  HOST_ENV_BLOCK+=$(emit_env_line "RELAYTV_HOST_PROFILE" "${HOST_PROFILE}")
  HOST_ENV_BLOCK+=$'\n'
fi

if [ "${HEADLESS_REMOTE_ENABLED_VAL}" = "1" ] || [ "${MODE}" = "headless" ]; then
  if [ "${HEADLESS_REMOTE_ENABLED_VAL}" != "0" ]; then
    HEADLESS_ENV_BLOCK+=$(emit_env_line "RELAYTV_HEADLESS_REMOTE_ENABLED" "${HEADLESS_REMOTE_ENABLED_VAL}")
    HEADLESS_ENV_BLOCK+=$'\n'
  fi
  if [ "${HEADLESS_REMOTE_DISPLAY_VAL}" != ":99" ]; then
    HEADLESS_ENV_BLOCK+=$(emit_env_line "RELAYTV_HEADLESS_REMOTE_DISPLAY" "${HEADLESS_REMOTE_DISPLAY_VAL}")
    HEADLESS_ENV_BLOCK+=$'\n'
  fi
  if [ "${HEADLESS_REMOTE_RESOLUTION_VAL}" != "1920x1080x24" ]; then
    HEADLESS_ENV_BLOCK+=$(emit_env_line "RELAYTV_HEADLESS_REMOTE_RESOLUTION" "${HEADLESS_REMOTE_RESOLUTION_VAL}")
    HEADLESS_ENV_BLOCK+=$'\n'
  fi
  if [ "${HEADLESS_REMOTE_SOFTWARE_VAL}" != "1" ]; then
    HEADLESS_ENV_BLOCK+=$(emit_env_line "RELAYTV_HEADLESS_REMOTE_SOFTWARE" "${HEADLESS_REMOTE_SOFTWARE_VAL}")
    HEADLESS_ENV_BLOCK+=$'\n'
  fi
  if [ "${HEADLESS_VNC_ENABLED_VAL}" != "1" ]; then
    HEADLESS_ENV_BLOCK+=$(emit_env_line "RELAYTV_HEADLESS_VNC_ENABLED" "${HEADLESS_VNC_ENABLED_VAL}")
    HEADLESS_ENV_BLOCK+=$'\n'
  fi
  if [ "${HEADLESS_VNC_LISTEN_VAL}" != "127.0.0.1" ]; then
    HEADLESS_ENV_BLOCK+=$(emit_env_line "RELAYTV_HEADLESS_VNC_LISTEN" "${HEADLESS_VNC_LISTEN_VAL}")
    HEADLESS_ENV_BLOCK+=$'\n'
  fi
  if [ "${HEADLESS_VNC_PORT_VAL}" != "5900" ]; then
    HEADLESS_ENV_BLOCK+=$(emit_env_line "RELAYTV_HEADLESS_VNC_PORT" "${HEADLESS_VNC_PORT_VAL}")
    HEADLESS_ENV_BLOCK+=$'\n'
  fi
  if [ -n "${HEADLESS_VNC_PASSWORD_FILE_VAL}" ]; then
    HEADLESS_ENV_BLOCK+=$(emit_env_line "RELAYTV_HEADLESS_VNC_PASSWORD_FILE" "${HEADLESS_VNC_PASSWORD_FILE_VAL}")
    HEADLESS_ENV_BLOCK+=$'\n'
  fi
fi

if [ "${YTDLP_AUTO_UPDATE_VAL}" != "0" ]; then
  YTDLP_ENV_BLOCK+=$(emit_env_line "RELAYTV_YTDLP_AUTO_UPDATE" "${YTDLP_AUTO_UPDATE_VAL}")
  YTDLP_ENV_BLOCK+=$'\n'
fi
if [ "${YTDLP_AUTO_UPDATE_INTERVAL_HOURS_VAL}" != "24" ]; then
  YTDLP_ENV_BLOCK+=$(emit_env_line "RELAYTV_YTDLP_AUTO_UPDATE_INTERVAL_HOURS" "${YTDLP_AUTO_UPDATE_INTERVAL_HOURS_VAL}")
  YTDLP_ENV_BLOCK+=$'\n'
fi
if [ "${YTDLP_AUTO_UPDATE_TIMEOUT_SEC_VAL}" != "180" ]; then
  YTDLP_ENV_BLOCK+=$(emit_env_line "RELAYTV_YTDLP_AUTO_UPDATE_TIMEOUT_SEC" "${YTDLP_AUTO_UPDATE_TIMEOUT_SEC_VAL}")
  YTDLP_ENV_BLOCK+=$'\n'
fi
if [ "${YTDLP_AUTO_UPDATE_STATE_FILE_VAL}" != "/data/.relaytv-ytdlp-update.json" ]; then
  YTDLP_ENV_BLOCK+=$(emit_env_line "RELAYTV_YTDLP_AUTO_UPDATE_STATE_FILE" "${YTDLP_AUTO_UPDATE_STATE_FILE_VAL}")
  YTDLP_ENV_BLOCK+=$'\n'
fi

CEC_ENV_BLOCK=""
if [ "${CEC_ENABLED_VAL}" = "1" ] || [ "${CEC_RUNTIME_VAL}" = "1" ] || [ "${CEC_MONITOR_VAL}" = "1" ]; then
  CEC_ENV_BLOCK+=$(emit_env_line "RELAYTV_CEC" "${CEC_RUNTIME_VAL}")
  CEC_ENV_BLOCK+=$'\n'
  CEC_ENV_BLOCK+=$(emit_env_line "RELAYTV_CEC_ENABLED" "${CEC_ENABLED_VAL}")
  CEC_ENV_BLOCK+=$'\n'
  CEC_ENV_BLOCK+=$(emit_env_line "RELAYTV_CEC_MONITOR" "${CEC_MONITOR_VAL}")
  CEC_ENV_BLOCK+=$'\n'
fi

if [ "${RENDER_GID_DETECTED}" != "992" ]; then
  RENDER_ENV_BLOCK+=$(emit_env_line "RELAYTV_RENDER_GID" "${RENDER_GID_DETECTED}")
  RENDER_ENV_BLOCK+=$'\n'
fi

if [ -n "${AUDIO_DEVICE_VAL}" ]; then
  AUDIO_ENV_BLOCK+=$(emit_env_line "MPV_AUDIO_DEVICE" "${AUDIO_DEVICE_VAL}")
  AUDIO_ENV_BLOCK+=$'\n'
fi

write_runtime_env() {
  local env_file="$ROOT/.env"
  local env_tmp preserved_tmp line key
  env_tmp="$(mktemp "$ROOT/.env.tmp.XXXXXX")"
  preserved_tmp="$(mktemp "$ROOT/.env.preserved.tmp.XXXXXX")"

  if [ -f "$env_file" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      if [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)= ]]; then
        key="${BASH_REMATCH[2]}"
        if ! installer_owned_env_key "$key"; then
          printf '%s\n' "$line" >> "$preserved_tmp"
        fi
      fi
    done < "$env_file"
  fi

  {
    printf '# Generated by scripts/install.sh on %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    emit_env_line "PUID" "${PUID}"
    emit_env_line "PGID" "${PGID}"
    emit_section "Optional display/session passthrough" "${DISPLAY_ENV_BLOCK}"
    emit_section "Runtime mode hints" "${RUNTIME_ENV_BLOCK}"
    emit_section "Detected host profile" "${HOST_ENV_BLOCK}"
    emit_section "Published image selection" "${IMAGE_ENV_BLOCK}"
    emit_section "Optional Docker build feature bundles" "${BUILD_ENV_BLOCK}"
    emit_section "Headless remote display" "${HEADLESS_ENV_BLOCK}"
    emit_section "yt-dlp runtime maintenance" "${YTDLP_ENV_BLOCK}"
    emit_section "Optional HDMI-CEC control" "${CEC_ENV_BLOCK}"
    emit_section "Host render group id for container group_add mapping" "${RENDER_ENV_BLOCK}"
    emit_section "Audio device override (blank means runtime auto-detect)" "${AUDIO_ENV_BLOCK}"
    if [ -s "$preserved_tmp" ]; then
      printf '\n# Operator-managed settings preserved across installer runs\n'
      cat "$preserved_tmp"
    fi
  } > "$env_tmp"

  chmod 600 "$env_tmp"
  if [ "$(id -u)" -eq 0 ]; then
    chown "${PUID}:${PGID}" "$env_tmp" 2>/dev/null || true
  fi
  mv -f "$env_tmp" "$env_file"
  rm -f "$preserved_tmp"
}

write_runtime_env

# Keep .env editable by the target user when installer is run via sudo/root.
if [ "$(id -u)" -eq 0 ]; then
  chown "${PUID}:${PGID}" .env 2>/dev/null || true
fi

say "RelayTV installer (minimal)"
say "  Autodetect mode: $( [ "$CLEAN_AUTODETECT" = "1" ] && printf '%s' clean || printf '%s' shell-env )"
say "  Install mode request: ${INSTALL_MODE}"
PUBLIC_REQUEST_PROFILE="${INSTALL_RUNTIME_PROFILE}"
PUBLIC_EFFECTIVE_PROFILE="${RUNTIME_PROFILE_EFFECTIVE}"
say "  Runtime profile request/effective: ${PUBLIC_REQUEST_PROFILE} / ${PUBLIC_EFFECTIVE_PROFILE}"
say "  Target user: ${TARGET_USER}"
say "  Wrote: $ROOT/.env"
say "  Host arch/profile: ${HOST_ARCH} / ${HOST_PROFILE}"
if [ -n "$HOST_MODEL" ]; then
  say "  Host model: ${HOST_MODEL}"
fi
say "  PUID/PGID: ${PUID}/${PGID}"
say "  DISPLAY: ${DISPLAY_VAL:-<unset>}"
say "  XDG_SESSION_TYPE: ${XDG_SESSION_TYPE_VAL:-<unset>}"
say "  WAYLAND_DISPLAY: ${WAYLAND_DISPLAY_VAL:-<unset>}"
say "  QT_QPA_PLATFORM: ${QT_QPA_PLATFORM_VAL:-<unset>}"
say "  XAUTHORITY: resolved from the mounted session directory at runtime"
say "  RELAYTV_MODE: ${MODE}"
say "  RELAYTV_X11_OVERLAY: ${X11_OVERLAY_VAL}"
say "  RELAYTV_VIDEO_MODE: ${VIDEO_MODE_VAL}"
say "  RELAYTV_DRM_CONNECTOR: ${DRM_CONNECTOR_VAL:-<unset>} (detected=${DRM_CONNECTOR_DETECTED:-<none>})"
say "  RELAYTV_PLAYER_BACKEND: ${PLAYER_BACKEND_VAL}"
say "  RELAYTV_QT_RUNTIME_MODE: ${QT_RUNTIME_MODE_VAL}"
say "  RELAYTV_QT_SHELL_MPV_ARGS: ${QT_SHELL_MPV_ARGS_VAL:-<unset>}"
say "  RELAYTV_QT_SHELL_MODULE: ${QT_SHELL_MODULE_VAL}"
say "  RELAYTV_IMAGE_REF: ${IMAGE_REF_VAL}"
say "  Build bundles (qt/x11-overlay/headless/node/idle-browser/ops): ${INSTALL_QT_BUNDLE_VAL}/${INSTALL_X11_OVERLAY_BUNDLE_VAL}/${INSTALL_HEADLESS_BUNDLE_VAL}/${INSTALL_NODE_BUNDLE_VAL}/${INSTALL_IDLE_BROWSER_BUNDLE_VAL}/${INSTALL_OPS_TOOLS_BUNDLE_VAL}"
say "  RELAYTV_HEADLESS_REMOTE_ENABLED: ${HEADLESS_REMOTE_ENABLED_VAL}"
say "  RELAYTV_HEADLESS_REMOTE_DISPLAY: ${HEADLESS_REMOTE_DISPLAY_VAL}"
say "  RELAYTV_HEADLESS_VNC_ENABLED: ${HEADLESS_VNC_ENABLED_VAL}"
say "  RELAYTV_HEADLESS_VNC_LISTEN: ${HEADLESS_VNC_LISTEN_VAL}"
say "  RELAYTV_HEADLESS_VNC_PORT: ${HEADLESS_VNC_PORT_VAL}"
say "  RELAYTV_YTDLP_AUTO_UPDATE: ${YTDLP_AUTO_UPDATE_VAL}"
say "  RELAYTV_YTDLP_AUTO_UPDATE_INTERVAL_HOURS: ${YTDLP_AUTO_UPDATE_INTERVAL_HOURS_VAL}"
say "  RELAYTV_RENDER_GID: ${RENDER_GID_DETECTED:-<unset>}"
say "  RELAYTV_HOST_ARCH: ${HOST_ARCH}"
say "  RELAYTV_HOST_PROFILE: ${HOST_PROFILE}"
say "  RELAYTV_PI_VIDEO_DEVICES_ENABLED: ${PI_VIDEO_DEVICES_ENABLED_VAL}"
say "  NVIDIA device detected: ${NVIDIA_DEVICE_DETECTED}"
say "  NVIDIA Docker toolkit available: ${NVIDIA_DOCKER_TOOLKIT_AVAILABLE}"
say "  NVIDIA passthrough enabled: ${NVIDIA_PASSTHROUGH_ENABLED}"
say "  RELAYTV_CEC_ENABLED: ${CEC_ENABLED_VAL}"
say "  RELAYTV_CEC: ${CEC_RUNTIME_VAL}"
say "  RELAYTV_CEC_MONITOR: ${CEC_MONITOR_VAL}"
say "  CEC devices detected: ${CEC_NODES_DETECTED:-<none>}"
if [ -f "$ROOT/docker-compose.override.yml" ] && grep -qF "# Generated by scripts/install.sh (host-device-overrides)" "$ROOT/docker-compose.override.yml"; then
  say "  compose override: $ROOT/docker-compose.override.yml (existing host integrations only)"
fi
if [ "$PI_VIDEO_DEVICES_ENABLED_VAL" = "1" ]; then
  if [ -n "$PI_VIDEO_NODES_MISSING" ]; then
    say "  WARN: Missing Pi V4L2 nodes: ${PI_VIDEO_NODES_MISSING}"
  fi
fi
if [ "$CEC_NODES_MISSING" = "1" ]; then
  say "  WARN: RELAYTV_CEC_ENABLED=1 but no CEC device node was found."
fi
if [ "$DRI_NODE_MISSING" = "1" ] && [ "$MODE" != "headless" ]; then
  say "  WARN: /dev/dri (GPU/KMS) not found; playback will use software rendering."
  say "        On Raspberry Pi enable KMS (dtoverlay=vc4-kms-v3d in config.txt), or re-run with --mode headless."
fi
if [ "$SND_NODE_MISSING" = "1" ]; then
  say "  WARN: /dev/snd not found; container audio output will be unavailable."
fi
if [ "$NVIDIA_PASSTHROUGH_ENABLED" != "1" ]; then
  say "  WARN: NVIDIA decoder passthrough disabled; NVIDIA playback acceleration will not be used unless both an NVIDIA device and Docker NVIDIA toolkit are detected."
fi
say "  MPV_AUDIO_DEVICE: ${AUDIO_DEVICE_VAL:-<auto>}"
