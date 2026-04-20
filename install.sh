#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail

DEFAULT_REPO="mcgeezy/relaytv"
DEFAULT_BRANCH="main"
DEFAULT_IMAGE="ghcr.io/mcgeezy/relaytv:latest"

say() { printf "%s\n" "$*"; }
die() {
  printf "ERROR: %s\n" "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  install.sh [options] [-- scripts/install.sh options]

Options:
  --dir PATH
      Install directory. Defaults to the current directory.
  --repo OWNER/REPO
      GitHub repository to download from. Default: mcgeezy/relaytv.
  --branch BRANCH
      GitHub branch/tag to download. Default: main.
  --image IMAGE
      RelayTV image reference to write to .env.
      Default: ghcr.io/mcgeezy/relaytv:latest.
  --yes
      Non-interactive defaults. Currently disables optional CEC unless
      --enable-cec is also supplied.
  --enable-cec
      Enable HDMI-CEC runtime control and device passthrough when a /dev/cec*
      device is present.
  --no-cec
      Do not enable HDMI-CEC passthrough.
  --no-pull
      Skip docker compose pull.
  --no-start
      Skip docker compose up -d.
  --force
      Allow installing into a directory that looks like a source checkout.
  -h, --help
      Show this help.

Common scripts/install.sh flags like --mode, --runtime-profile,
--clean-autodetect, --use-shell-env, and --native-qt can also be passed
directly.

Everything after "--" is forwarded to scripts/install.sh for future or
less-common options.
EOF
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

default_install_dir() {
  pwd -P
}

INSTALL_DIR_EXPLICIT="0"
if [ -n "${RELAYTV_INSTALL_DIR:-}" ]; then
  INSTALL_DIR="$RELAYTV_INSTALL_DIR"
  INSTALL_DIR_EXPLICIT="1"
else
  INSTALL_DIR="$(default_install_dir)"
fi
REPO="${RELAYTV_BOOTSTRAP_REPO:-$DEFAULT_REPO}"
BRANCH="${RELAYTV_BOOTSTRAP_BRANCH:-$DEFAULT_BRANCH}"
IMAGE_REF="${RELAYTV_IMAGE_REF:-$DEFAULT_IMAGE}"
ASSUME_YES="${RELAYTV_INSTALL_YES:-0}"
CEC_CHOICE="${RELAYTV_CEC_ENABLED:-auto}"
DO_PULL="${RELAYTV_INSTALL_PULL:-1}"
DO_START="${RELAYTV_INSTALL_START:-1}"
FORCE_INSTALL="${RELAYTV_INSTALL_FORCE:-0}"
INSTALL_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)
      shift
      [[ $# -gt 0 ]] || die "--dir requires a value"
      INSTALL_DIR="$1"
      INSTALL_DIR_EXPLICIT="1"
      ;;
    --repo)
      shift
      [[ $# -gt 0 ]] || die "--repo requires a value"
      REPO="$1"
      ;;
    --branch)
      shift
      [[ $# -gt 0 ]] || die "--branch requires a value"
      BRANCH="$1"
      ;;
    --image)
      shift
      [[ $# -gt 0 ]] || die "--image requires a value"
      IMAGE_REF="$1"
      ;;
    --yes)
      ASSUME_YES="1"
      ;;
    --enable-cec)
      CEC_CHOICE="1"
      ;;
    --no-cec)
      CEC_CHOICE="0"
      ;;
    --no-pull)
      DO_PULL="0"
      ;;
    --no-start)
      DO_START="0"
      ;;
    --force)
      FORCE_INSTALL="1"
      ;;
    --clean-autodetect|--use-shell-env|--native-qt)
      INSTALL_ARGS+=("$1")
      ;;
    --mode|--runtime-profile)
      key="$1"
      shift
      [[ $# -gt 0 ]] || die "${key} requires a value"
      INSTALL_ARGS+=("$key" "$1")
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      INSTALL_ARGS+=("$@")
      break
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
  shift
done

case "$CEC_CHOICE" in
  auto|0|1) ;;
  *) die "RELAYTV_CEC_ENABLED must be auto, 0, or 1" ;;
esac

confirm_current_directory_install() {
  if [ "$INSTALL_DIR_EXPLICIT" = "1" ] || [ "$ASSUME_YES" = "1" ]; then
    return 0
  fi
  if [ ! -r /dev/tty ]; then
    die "RelayTV installs into the current directory by default: $INSTALL_DIR. Re-run from the directory where you want the service installed, pass --dir PATH, or use --yes for non-interactive install."
  fi

  {
    say ""
    say "RelayTV will be installed in the current directory:"
    say "  $INSTALL_DIR"
    say ""
    say "Run this installer from the directory where you want the RelayTV service files."
    printf "Continue? [y/N] "
  } > /dev/tty

  local answer=""
  read -r answer < /dev/tty || true
  case "${answer,,}" in
    y|yes) ;;
    *)
      die "Installation cancelled. Run the installer from the directory where you want to install the RelayTV service."
      ;;
  esac
}

confirm_current_directory_install

need_cmd id
need_cmd mkdir
need_cmd chmod
need_cmd cp
need_cmd mktemp

if command -v curl >/dev/null 2>&1; then
  FETCH=(curl -fsSL)
elif command -v wget >/dev/null 2>&1; then
  FETCH=(wget -qO-)
else
  die "Missing curl or wget"
fi

if ! command -v docker >/dev/null 2>&1; then
  die "Docker is not installed. Install Docker Engine and rerun this installer."
fi
if ! docker compose version >/dev/null 2>&1; then
  die "Docker Compose v2 is not available. Install the Docker compose plugin and rerun this installer."
fi

RAW_BASE="${RELAYTV_BOOTSTRAP_RAW_BASE:-https://raw.githubusercontent.com/${REPO}/${BRANCH}}"

fetch_to() {
  local url="$1" dest="$2"
  "${FETCH[@]}" "$url" > "$dest"
}

detect_cec_devices() {
  local dev
  for dev in /dev/cec*; do
    [ -e "$dev" ] || continue
    printf "%s\n" "$dev"
  done
}

cec_detected_summary() {
  local nodes
  nodes="$(detect_cec_devices | paste -sd, - 2>/dev/null || true)"
  if [ -n "$nodes" ]; then
    printf "%s" "$nodes"
    return 0
  fi
  if command -v cec-client >/dev/null 2>&1 && cec-client -l 2>/dev/null | grep -qi 'adapter'; then
    printf "cec-client adapter"
    return 0
  fi
  if command -v lsusb >/dev/null 2>&1 && lsusb 2>/dev/null | grep -qi 'pulse.*eight\|cec'; then
    printf "USB CEC adapter"
    return 0
  fi
  printf ""
}

prompt_enable_cec() {
  local summary="$1"
  if [ "$CEC_CHOICE" = "0" ] || [ -z "$summary" ]; then
    printf "0"
    return 0
  fi
  if [ "$CEC_CHOICE" = "1" ]; then
    printf "1"
    return 0
  fi
  if [ "$ASSUME_YES" = "1" ] || [ ! -r /dev/tty ]; then
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

if [ -d "$INSTALL_DIR/.git" ] && [ "$FORCE_INSTALL" != "1" ]; then
  die "$INSTALL_DIR looks like a source checkout. Use scripts/install.sh from that checkout, choose --dir, or rerun with --force."
fi

mkdir -p "$INSTALL_DIR/scripts" "$INSTALL_DIR/data" "$INSTALL_DIR/bin" \
  || die "Could not create $INSTALL_DIR. Re-run with sudo or choose --dir ~/relaytv."

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

say "RelayTV bootstrap installer"
say "  Repository: ${REPO}"
say "  Branch/tag: ${BRANCH}"
say "  Install dir: ${INSTALL_DIR}"
say "  Image: ${IMAGE_REF}"

fetch_to "$RAW_BASE/docker-compose.release.yml" "$tmp_dir/docker-compose.yml"
fetch_to "$RAW_BASE/install.sh" "$tmp_dir/bootstrap-install.sh"
fetch_to "$RAW_BASE/scripts/install.sh" "$tmp_dir/install.sh"
fetch_to "$RAW_BASE/scripts/doctor.sh" "$tmp_dir/doctor.sh"
fetch_to "$RAW_BASE/scripts/host-ops.sh" "$tmp_dir/host-ops.sh"

cp "$tmp_dir/docker-compose.yml" "$INSTALL_DIR/docker-compose.yml"
cp "$tmp_dir/bootstrap-install.sh" "$INSTALL_DIR/install.sh"
cp "$tmp_dir/install.sh" "$INSTALL_DIR/scripts/install.sh"
cp "$tmp_dir/doctor.sh" "$INSTALL_DIR/scripts/doctor.sh"
cp "$tmp_dir/host-ops.sh" "$INSTALL_DIR/scripts/host-ops.sh"
chmod +x "$INSTALL_DIR/install.sh" "$INSTALL_DIR/scripts/install.sh" "$INSTALL_DIR/scripts/doctor.sh" "$INSTALL_DIR/scripts/host-ops.sh"

cec_summary="$(cec_detected_summary)"
cec_enabled="$(prompt_enable_cec "$cec_summary")"

say ""
say "Generating RelayTV runtime configuration..."
(
  cd "$INSTALL_DIR"
  RELAYTV_IMAGE_REF="$IMAGE_REF" RELAYTV_CEC_ENABLED="$cec_enabled" ./scripts/install.sh "${INSTALL_ARGS[@]}"
)

if [ "$DO_PULL" = "1" ]; then
  say ""
  say "Pulling RelayTV image..."
  (cd "$INSTALL_DIR" && docker compose pull)
fi

if [ "$DO_START" = "1" ]; then
  say ""
  say "Starting RelayTV..."
  (cd "$INSTALL_DIR" && docker compose up -d)
fi

say ""
say "RelayTV install complete."
say "  Directory: $INSTALL_DIR"
say "  CEC enabled: $cec_enabled"
say ""
say "Useful commands:"
say "  cd $INSTALL_DIR"
say "  docker compose pull && docker compose up -d"
say "  ./scripts/doctor.sh"
