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
      Non-interactive defaults. Installs Docker when missing and disables
      optional CEC unless --enable-cec is also supplied.
  --install-docker
      Install Docker Engine from Docker's official repository when Docker is
      missing. This is implied by --yes.
  --no-install-docker
      Never install Docker automatically; fail with setup guidance instead.
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
DOCKER_INSTALL="${RELAYTV_INSTALL_DOCKER:-auto}"
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
    --install-docker)
      DOCKER_INSTALL="1"
      ;;
    --no-install-docker)
      DOCKER_INSTALL="0"
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
case "$DOCKER_INSTALL" in
  auto|0|1) ;;
  *) die "RELAYTV_INSTALL_DOCKER must be auto, 0, or 1" ;;
esac

validate_host_platform() {
  local kernel arch
  kernel="$(uname -s 2>/dev/null || true)"
  arch="$(uname -m 2>/dev/null || true)"

  if [ "$kernel" != "Linux" ]; then
    die "RelayTV requires a native Linux host connected to the display. Docker Desktop on macOS or Windows is not supported."
  fi
  if grep -qi microsoft /proc/version 2>/dev/null || [ -n "${WSL_INTEROP:-}" ]; then
    die "RelayTV's display, audio, and device passthrough require native Linux; WSL is not supported."
  fi
  if [[ "$IMAGE_REF" == ghcr.io/mcgeezy/relaytv:* ]]; then
    case "$arch" in
      x86_64|amd64|aarch64|arm64) ;;
      *)
        die "Published RelayTV images support only 64-bit amd64 and arm64 hosts (detected: ${arch:-unknown})."
        ;;
    esac
  fi
}

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
need_cmd uname

validate_host_platform

if command -v curl >/dev/null 2>&1; then
  FETCH=(curl -fsSL)
  FETCH_TOOL="curl"
elif command -v wget >/dev/null 2>&1; then
  FETCH=(wget -qO-)
  FETCH_TOOL="wget"
else
  die "Missing curl or wget"
fi

run_as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
    return
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo "$@"
    return
  fi
  die "Administrator access is required. Install sudo or run this installer as root."
}

confirm_docker_install() {
  if [ "$DOCKER_INSTALL" = "0" ]; then
    die "Docker is not installed. Install Docker Engine with the Compose plugin, then rerun this installer."
  fi
  if [ "$DOCKER_INSTALL" = "1" ] || [ "$ASSUME_YES" = "1" ]; then
    return 0
  fi
  if [ ! -r /dev/tty ]; then
    die "Docker is not installed. Re-run with --install-docker (or --yes), or install Docker Engine and the Compose plugin first."
  fi

  {
    say ""
    say "Docker Engine and Docker Compose are required but were not found."
    say "RelayTV can install them from Docker's official package repository."
    printf "Install Docker now? [y/N] "
  } > /dev/tty
  local answer=""
  read -r answer < /dev/tty || true
  case "${answer,,}" in
    y|yes) ;;
    *) die "Docker installation declined. Install Docker Engine and the Compose plugin, then rerun RelayTV." ;;
  esac
}

docker_install_supported() {
  local distro="" distro_like=""
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    distro="${ID:-}"
    distro_like="${ID_LIKE:-}"
  fi
  case " $distro $distro_like " in
    *" debian "*|*" ubuntu "*|*" raspbian "*|*" fedora "*|*" rhel "*|*" centos "*) return 0 ;;
    *)
      die "Automatic Docker installation supports Debian, Ubuntu, 64-bit Raspberry Pi OS, Fedora, RHEL, and CentOS. Install Docker Engine manually on '${distro:-this Linux distribution}', then rerun RelayTV."
      ;;
  esac
}

install_docker_engine() {
  local installer
  docker_install_supported
  confirm_docker_install
  installer="$(mktemp)"
  say "Installing Docker Engine and the Docker Compose plugin..."
  if ! "${FETCH[@]}" "https://get.docker.com" > "$installer"; then
    rm -f "$installer"
    die "Could not download Docker's official installation script."
  fi
  if ! run_as_root sh "$installer"; then
    rm -f "$installer"
    die "Docker installation failed. Review the package-manager output above."
  fi
  rm -f "$installer"
}

DOCKER_CMD=(docker)
ensure_docker_ready() {
  local direct_error=""
  if ! command -v docker >/dev/null 2>&1; then
    install_docker_engine
  fi

  if ! docker compose version >/dev/null 2>&1; then
    if [ "$(id -u)" -eq 0 ] || ! command -v sudo >/dev/null 2>&1 || ! sudo docker compose version >/dev/null 2>&1; then
      die "Docker Compose v2 is unavailable. Install the docker-compose-plugin package and rerun this installer."
    fi
  fi

  if docker info >/dev/null 2>&1; then
    DOCKER_CMD=(docker)
    return 0
  fi
  direct_error="$(docker info 2>&1 || true)"

  if command -v systemctl >/dev/null 2>&1 && ! systemctl is-active --quiet docker 2>/dev/null; then
    say "Starting Docker Engine..."
    run_as_root systemctl start docker || true
    if docker info >/dev/null 2>&1; then
      DOCKER_CMD=(docker)
      return 0
    fi
  fi

  if [ "$(id -u)" -eq 0 ]; then
    die "Docker Engine is installed but its daemon is unavailable. Start the docker service and rerun RelayTV."
  fi
  if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    DOCKER_CMD=(sudo docker)
    say "Docker requires administrator access; RelayTV will use sudo for this installation."
    say "No users were added to the docker group."
    return 0
  fi
  if [[ "${direct_error,,}" == *"permission denied"* ]]; then
    die "Docker is running, but this user cannot access its socket. Re-run with sudo or configure Docker access explicitly."
  fi
  die "Docker Engine is installed but unavailable. Start the docker service, verify 'docker info', and rerun RelayTV."
}

ensure_docker_ready

read_install_port() {
  local port="${RELAYTV_PORT:-}" line
  if [ -z "$port" ] && [ -f "$INSTALL_DIR/.env" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in
        RELAYTV_PORT=*) port="${line#RELAYTV_PORT=}" ;;
      esac
    done < "$INSTALL_DIR/.env"
  fi
  port="${port%\"}"
  port="${port#\"}"
  port="${port%\'}"
  port="${port#\'}"
  if [[ ! "$port" =~ ^[0-9]+$ ]] || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    port="8787"
  fi
  printf '%s' "$port"
}

wait_for_relaytv_health() {
  local timeout="${RELAYTV_INSTALL_HEALTH_TIMEOUT:-60}"
  local port health_url deadline
  if [[ ! "$timeout" =~ ^[0-9]+$ ]] || [ "$timeout" -lt 1 ]; then
    die "RELAYTV_INSTALL_HEALTH_TIMEOUT must be a positive number of seconds."
  fi
  port="$(read_install_port)"
  health_url="http://127.0.0.1:${port}/health"
  deadline=$((SECONDS + timeout))
  say "Waiting for RelayTV health at ${health_url}..."
  while [ "$SECONDS" -lt "$deadline" ]; do
    if { [ "$FETCH_TOOL" = "curl" ] && curl -fsS --max-time 2 "$health_url" >/dev/null 2>&1; } ||
      { [ "$FETCH_TOOL" = "wget" ] && wget -qO- --timeout=2 "$health_url" >/dev/null 2>&1; }; then
      say "RelayTV is healthy."
      return 0
    fi
    sleep 2
  done

  say "RelayTV did not become healthy within ${timeout} seconds." >&2
  (cd "$INSTALL_DIR" && "${DOCKER_CMD[@]}" compose ps) >&2 || true
  (cd "$INSTALL_DIR" && "${DOCKER_CMD[@]}" compose logs --tail=80 relaytv) >&2 || true
  die "Startup verification failed. Review the container status and logs above."
}

RAW_BASE="${RELAYTV_BOOTSTRAP_RAW_BASE:-https://raw.githubusercontent.com/${REPO}/${BRANCH}}"

fetch_to() {
  local url="$1" dest="$2"
  "${FETCH[@]}" "$url" > "$dest"
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

say ""
say "Generating RelayTV runtime configuration..."
(
  cd "$INSTALL_DIR"
  RELAYTV_IMAGE_REF="$IMAGE_REF" RELAYTV_CEC_ENABLED="$CEC_CHOICE" RELAYTV_INSTALL_YES="$ASSUME_YES" ./scripts/install.sh "${INSTALL_ARGS[@]}"
)

if [ "$DO_PULL" = "1" ]; then
  say ""
  say "Pulling RelayTV image..."
  (cd "$INSTALL_DIR" && "${DOCKER_CMD[@]}" compose pull)
fi

if [ "$DO_START" = "1" ]; then
  say ""
  say "Starting RelayTV..."
  (cd "$INSTALL_DIR" && "${DOCKER_CMD[@]}" compose up -d)
  wait_for_relaytv_health
fi

say ""
say "RelayTV install complete."
say "  Directory: $INSTALL_DIR"
say "  CEC mode: $CEC_CHOICE"
say ""
say "Useful commands:"
say "  cd $INSTALL_DIR"
if [ "${DOCKER_CMD[0]}" = "sudo" ]; then
  say "  sudo docker compose pull && sudo docker compose up -d"
else
  say "  docker compose pull && docker compose up -d"
fi
say "  ./scripts/doctor.sh"
