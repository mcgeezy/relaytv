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

has_controlling_tty() {
  ( exec </dev/tty >/dev/tty ) 2>/dev/null
}

default_install_dir() {
  pwd -P
}

parse_dotenv_scalar() {
  local raw="${1#*=}"
  raw="${raw%$'\r'}"
  raw="${raw#"${raw%%[![:space:]]*}"}"
  local dq_pattern='^"(([^"\\]|\\.)*)"[[:space:]]*(#.*)?$'
  if [[ "$raw" =~ $dq_pattern ]]; then
    local inner="${BASH_REMATCH[1]}" out="" i=0 len="${#BASH_REMATCH[1]}" ch next
    while (( i < len )); do
      ch="${inner:i:1}"
      if [[ "$ch" == "\\" ]] && (( i + 1 < len )); then
        next="${inner:i+1:1}"
        case "$next" in
          \"|\\|\$)
            out+="$next"
            (( i += 2 ))
            continue
            ;;
        esac
      fi
      out+="$ch"
      (( i += 1 ))
    done
    printf '%s' "$out"
  elif [[ "$raw" =~ ^\'([^\']*)\'[[:space:]]*(#.*)?$ ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
  else
    if [[ "$raw" =~ ^#.*$ ]]; then
      raw=""
    elif [[ "$raw" =~ ^(.*[^[:space:]])[[:space:]]+#.*$ ]]; then
      raw="${BASH_REMATCH[1]}"
    else
      raw="${raw%"${raw##*[![:space:]]}"}"
    fi
    printf '%s' "$raw"
  fi
}

read_dotenv_key() {
  local file="$1" wanted="$2" line found=""
  [ -f "$file" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)= ]] && [ "${BASH_REMATCH[2]}" = "$wanted" ]; then
      found="$line"
    fi
  done < "$file"
  [ -n "$found" ] || return 1
  parse_dotenv_scalar "$found"
}

INSTALL_DIR_EXPLICIT="0"
if [ -n "${RELAYTV_INSTALL_DIR:-}" ]; then
  INSTALL_DIR="$RELAYTV_INSTALL_DIR"
  INSTALL_DIR_EXPLICIT="1"
else
  INSTALL_DIR="$(default_install_dir)"
fi
REPO="${RELAYTV_BOOTSTRAP_REPO:-$DEFAULT_REPO}"
BRANCH_EXPLICIT="0"
if [ -n "${RELAYTV_BOOTSTRAP_BRANCH:-}" ]; then
  BRANCH="$RELAYTV_BOOTSTRAP_BRANCH"
  BRANCH_EXPLICIT="1"
else
  BRANCH="$DEFAULT_BRANCH"
fi
IMAGE_EXPLICIT="0"
if [ -n "${RELAYTV_IMAGE_REF:-}" ]; then
  IMAGE_REF="$RELAYTV_IMAGE_REF"
  IMAGE_EXPLICIT="1"
else
  IMAGE_REF="$DEFAULT_IMAGE"
fi
ASSUME_YES="${RELAYTV_INSTALL_YES:-0}"
DOCKER_INSTALL="${RELAYTV_INSTALL_DOCKER:-auto}"
CEC_EXPLICIT="0"
if [ -n "${RELAYTV_CEC_ENABLED:-}" ]; then
  CEC_CHOICE="$RELAYTV_CEC_ENABLED"
  CEC_EXPLICIT="1"
else
  CEC_CHOICE="auto"
fi
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
      BRANCH_EXPLICIT="1"
      ;;
    --image)
      shift
      [[ $# -gt 0 ]] || die "--image requires a value"
      IMAGE_REF="$1"
      IMAGE_EXPLICIT="1"
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
      CEC_EXPLICIT="1"
      ;;
    --no-cec)
      CEC_CHOICE="0"
      CEC_EXPLICIT="1"
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

if [ "$IMAGE_EXPLICIT" = "0" ]; then
  if [ "$BRANCH_EXPLICIT" = "1" ]; then
    if [[ "$BRANCH" =~ ^v?[0-9]+\.[0-9]+(\.[0-9]+)?([.-][A-Za-z0-9._-]+)?$ ]]; then
      IMAGE_REF="ghcr.io/mcgeezy/relaytv:${BRANCH}"
    else
      IMAGE_REF="$DEFAULT_IMAGE"
    fi
  elif saved_image_ref="$(read_dotenv_key "$INSTALL_DIR/.env" RELAYTV_IMAGE_REF)" && [ -n "$saved_image_ref" ]; then
    IMAGE_REF="$saved_image_ref"
  fi
fi
if [ "$CEC_EXPLICIT" = "0" ]; then
  if saved_cec_choice="$(read_dotenv_key "$INSTALL_DIR/.env" RELAYTV_CEC_ENABLED)" && [ -n "$saved_cec_choice" ]; then
    case "$saved_cec_choice" in
      0|1) CEC_CHOICE="$saved_cec_choice" ;;
    esac
  fi
fi

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
  local resolved_dir="$INSTALL_DIR" home_dir=""
  if [ -d "$INSTALL_DIR" ]; then
    resolved_dir="$(cd "$INSTALL_DIR" 2>/dev/null && pwd -P || printf '%s' "$INSTALL_DIR")"
  fi
  if [ "$resolved_dir" = "/" ]; then
    die "Refusing to install RelayTV directly into /."
  fi
  if [ -n "${HOME:-}" ]; then
    home_dir="$(cd "$HOME" 2>/dev/null && pwd -P || printf '%s' "$HOME")"
  fi
  if [ -n "$home_dir" ] && [ "$resolved_dir" = "$home_dir" ] && [ "$INSTALL_DIR_EXPLICIT" = "0" ] && [ "$FORCE_INSTALL" != "1" ] && [ "$ASSUME_YES" = "1" ]; then
    die "Refusing to install directly into your home directory ($INSTALL_DIR) with --yes. Create a subdirectory first (mkdir -p ~/relaytv && cd ~/relaytv), pass --dir PATH, or add --force."
  fi
  if [ "$INSTALL_DIR_EXPLICIT" = "1" ] || [ "$ASSUME_YES" = "1" ]; then
    return 0
  fi
  if ! has_controlling_tty; then
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
  if ! has_controlling_tty; then
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
  if command -v systemctl >/dev/null 2>&1; then
    run_as_root systemctl enable --now docker >/dev/null 2>&1 || true
  fi
}

DOCKER_CMD=(docker)
NVIDIA_TOOLKIT_HINT="0"
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
    run_as_root systemctl enable --now docker >/dev/null 2>&1 || run_as_root systemctl start docker || true
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
    if sudo docker info 2>/dev/null | grep -qi 'nvidia'; then
      NVIDIA_TOOLKIT_HINT="1"
    fi
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
  local port="${RELAYTV_PORT:-}"
  if [ -z "$port" ]; then
    port="$(read_dotenv_key "$INSTALL_DIR/.env" RELAYTV_PORT || true)"
  fi
  if [ -z "$port" ]; then
    port="$(read_dotenv_key "$INSTALL_DIR/.env" PORT || true)"
  fi
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
  local url="$1" dest="$2" rel_path="" fallback_base=""
  if "${FETCH[@]}" "$url" > "$dest"; then
    return 0
  fi
  if [[ "$BRANCH" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9._-]+)?$ ]] && [[ "$url" == "${RAW_BASE}/"* ]]; then
    rel_path="${url#"${RAW_BASE}/"}"
    if [[ "$RAW_BASE" == */"$BRANCH" ]]; then
      fallback_base="${RAW_BASE%/"$BRANCH"}/v${BRANCH}"
    else
      fallback_base="${RAW_BASE%/}/v${BRANCH}"
    fi
    "${FETCH[@]}" "${fallback_base}/${rel_path}" > "$dest"
    return $?
  fi
  return 1
}

if [ -d "$INSTALL_DIR/.git" ] && [ "$FORCE_INSTALL" != "1" ]; then
  die "$INSTALL_DIR looks like a source checkout. Use scripts/install.sh from that checkout, choose --dir, or rerun with --force."
fi
if [ -f "$INSTALL_DIR/docker-compose.yml" ] && [ "$FORCE_INSTALL" != "1" ] && ! grep -q 'relaytv:' "$INSTALL_DIR/docker-compose.yml" 2>/dev/null; then
  die "$INSTALL_DIR/docker-compose.yml already exists and does not look like a RelayTV compose file. Choose --dir or rerun with --force."
fi

need_cmd mv
need_cmd rm

mkdir -p "$INSTALL_DIR/scripts" "$INSTALL_DIR/data" "$INSTALL_DIR/bin" \
  || die "Could not create $INSTALL_DIR. Re-run with sudo or choose --dir ~/relaytv."

tmp_dir="$(mktemp -d)"
STAGE_FILES=()
cleanup() {
  local f
  for f in "${STAGE_FILES[@]}"; do
    if [ -n "$f" ]; then
      rm -f "$f"
    fi
  done
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

install_file_atomic() {
  local src="$1" dest="$2" mode="$3" stage
  stage="$(mktemp "${dest}.tmp.XXXXXX")"
  STAGE_FILES+=("$stage")
  cp "$src" "$stage"
  chmod "$mode" "$stage"
  mv -f "$stage" "$dest"
}

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

install_file_atomic "$tmp_dir/docker-compose.yml" "$INSTALL_DIR/docker-compose.yml" 0644
install_file_atomic "$tmp_dir/bootstrap-install.sh" "$INSTALL_DIR/install.sh" 0755
install_file_atomic "$tmp_dir/install.sh" "$INSTALL_DIR/scripts/install.sh" 0755
install_file_atomic "$tmp_dir/doctor.sh" "$INSTALL_DIR/scripts/doctor.sh" 0755
install_file_atomic "$tmp_dir/host-ops.sh" "$INSTALL_DIR/scripts/host-ops.sh" 0755

if [ "$(id -u)" -eq 0 ]; then
  target_user="${RELAYTV_TARGET_USER:-${SUDO_USER:-}}"
  if [ -n "$target_user" ] && [ "$target_user" != "root" ] && id -u "$target_user" >/dev/null 2>&1; then
    target_uid="$(id -u "$target_user")"
    target_gid="$(id -g "$target_user")"
    chown "${target_uid}:${target_gid}" \
      "$INSTALL_DIR" \
      "$INSTALL_DIR/scripts" \
      "$INSTALL_DIR/data" \
      "$INSTALL_DIR/bin" \
      "$INSTALL_DIR/docker-compose.yml" \
      "$INSTALL_DIR/install.sh" \
      "$INSTALL_DIR/scripts/install.sh" \
      "$INSTALL_DIR/scripts/doctor.sh" \
      "$INSTALL_DIR/scripts/host-ops.sh" 2>/dev/null || true
  fi
fi

say ""
say "Generating RelayTV runtime configuration..."
(
  cd "$INSTALL_DIR"
  # Do not turn a saved CEC choice into an explicit override of runtime flags.
  if [ "$CEC_EXPLICIT" = "1" ]; then
    export RELAYTV_CEC_ENABLED="$CEC_CHOICE"
  else
    unset RELAYTV_CEC_ENABLED
  fi
  RELAYTV_NVIDIA_DOCKER_TOOLKIT="$NVIDIA_TOOLKIT_HINT" RELAYTV_IMAGE_REF="$IMAGE_REF" RELAYTV_INSTALL_YES="$ASSUME_YES" ./scripts/install.sh "${INSTALL_ARGS[@]}"
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

RESOLVED_CEC_MODE="$(read_dotenv_key "$INSTALL_DIR/.env" RELAYTV_CEC_ENABLED || true)"
if [ -z "$RESOLVED_CEC_MODE" ]; then
  RESOLVED_CEC_MODE="0"
fi

say ""
say "RelayTV install complete."
say "  Directory: $INSTALL_DIR"
say "  CEC mode: $RESOLVED_CEC_MODE"
say ""
say "Useful commands:"
say "  cd $INSTALL_DIR"
if [ "${DOCKER_CMD[0]}" = "sudo" ]; then
  say "  sudo docker compose pull && sudo docker compose up -d"
else
  say "  docker compose pull && docker compose up -d"
fi
say "  ./scripts/doctor.sh"
