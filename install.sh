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

Re-running install.sh in an existing RelayTV directory (or passing --update)
updates RelayTV in place while preserving .env secrets, settings, and ./data.

Options:
  --dir PATH
      Install or update directory. Defaults to the current directory (or the
      detected RelayTV installation when updating).
  --update
      Update an existing RelayTV installation in place. Automatically locates
      an existing installation when --dir is omitted, and fails if none exists.
  --prune
      After a healthy update, remove the superseded RelayTV container image if
      no container or tag still references it.
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

compose_file_has_local_build() {
  local compose_file="$1"
  [ -f "$compose_file" ] && grep -Eq '^[[:space:]]*build:([[:space:]]|$|#)' "$compose_file" 2>/dev/null
}

is_deployed_relaytv_dir() {
  local dir="$1"
  [ -n "$dir" ] && [ -d "$dir" ] || return 1
  [ ! -e "$dir/.git" ] || return 1
  [ -f "$dir/docker-compose.yml" ] || return 1
  grep -Eq '^[[:space:]]*relaytv:[[:space:]]*$' "$dir/docker-compose.yml" 2>/dev/null || return 1
  ! compose_file_has_local_build "$dir/docker-compose.yml" || return 1
  [ -f "$dir/.env" ] || [ -f "$dir/scripts/install.sh" ]
}

is_relaytv_install_dir() {
  local dir="$1"
  [ -n "$dir" ] && [ -d "$dir" ] || return 1
  if [ "$FORCE_INSTALL" != "1" ]; then
    is_deployed_relaytv_dir "$dir"
    return $?
  fi
  [ -f "$dir/docker-compose.yml" ] || return 1
  grep -Eq '^[[:space:]]*relaytv:[[:space:]]*$' "$dir/docker-compose.yml" 2>/dev/null || return 1
  [ -f "$dir/.env" ] || [ -f "$dir/scripts/install.sh" ]
}

default_install_dir() {
  local cwd script_path="" script_dir=""
  cwd="$(pwd -P)"
  if is_deployed_relaytv_dir "$cwd"; then
    printf '%s\n' "$cwd"
    return 0
  fi
  script_path="${BASH_SOURCE[0]:-}"
  if [ -n "$script_path" ] && [ -f "$script_path" ]; then
    script_dir="$(cd "$(dirname "$script_path")" 2>/dev/null && pwd -P || true)"
    if [ -n "$script_dir" ] && is_deployed_relaytv_dir "$script_dir"; then
      printf '%s\n' "$script_dir"
      return 0
    fi
  fi
  printf '%s\n' "$cwd"
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
UPDATE_REQUESTED="${RELAYTV_INSTALL_UPDATE:-0}"
PRUNE_OLD_IMAGE="${RELAYTV_INSTALL_PRUNE:-0}"
DISCOVER_DOCKER="${RELAYTV_BOOTSTRAP_DISCOVER_DOCKER:-1}"
IS_UPDATE="0"
SAVED_IMAGE_PINNED="0"
CURRENT_DIR_CONFIRMED="0"
MODE_EXPLICIT="0"
if [ -n "${RELAYTV_INSTALL_MODE:-}" ]; then
  MODE_EXPLICIT="1"
fi
INSTALL_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)
      shift
      [[ $# -gt 0 ]] || die "--dir requires a value"
      INSTALL_DIR="$1"
      INSTALL_DIR_EXPLICIT="1"
      ;;
    --update)
      UPDATE_REQUESTED="1"
      ;;
    --prune)
      PRUNE_OLD_IMAGE="1"
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
      if [ "$key" = "--mode" ]; then
        MODE_EXPLICIT="1"
      fi
      INSTALL_ARGS+=("$key" "$1")
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      for passthrough_arg in "$@"; do
        case "$passthrough_arg" in
          --mode|--mode=*) MODE_EXPLICIT="1" ;;
        esac
      done
      INSTALL_ARGS+=("$@")
      break
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
  shift
done

case "$UPDATE_REQUESTED" in
  0|1) ;;
  *) die "RELAYTV_INSTALL_UPDATE must be 0 or 1" ;;
esac
case "$PRUNE_OLD_IMAGE" in
  0|1) ;;
  *) die "RELAYTV_INSTALL_PRUNE must be 0 or 1" ;;
esac
case "$DISCOVER_DOCKER" in
  0|1) ;;
  *) die "RELAYTV_BOOTSTRAP_DISCOVER_DOCKER must be 0 or 1" ;;
esac

resolve_saved_install_defaults() {
  local saved_image_ref="" saved_cec_choice="" pinned_tag_re=""
  SAVED_IMAGE_PINNED="0"
  if [ "$BRANCH_EXPLICIT" = "0" ]; then
    BRANCH="$DEFAULT_BRANCH"
  fi
  if [ "$IMAGE_EXPLICIT" = "0" ]; then
    if [ "$BRANCH_EXPLICIT" = "1" ]; then
      if [[ "$BRANCH" =~ ^v?[0-9]+\.[0-9]+(\.[0-9]+)?([.-][A-Za-z0-9._-]+)?$ ]]; then
        IMAGE_REF="ghcr.io/mcgeezy/relaytv:${BRANCH}"
      else
        IMAGE_REF="$DEFAULT_IMAGE"
      fi
    elif saved_image_ref="$(read_dotenv_key "$INSTALL_DIR/.env" RELAYTV_IMAGE_REF)" && [ -n "$saved_image_ref" ]; then
      IMAGE_REF="$saved_image_ref"
      if [ "$saved_image_ref" != "$DEFAULT_IMAGE" ]; then
        SAVED_IMAGE_PINNED="1"
        pinned_tag_re='^ghcr\.io/mcgeezy/relaytv:(v?[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9._-]+)?)$'
        if [ "$BRANCH_EXPLICIT" = "0" ] && [ "$REPO" = "$DEFAULT_REPO" ] && [[ "$saved_image_ref" =~ $pinned_tag_re ]]; then
          BRANCH="${BASH_REMATCH[1]}"
        fi
      fi
    else
      IMAGE_REF="$DEFAULT_IMAGE"
    fi
  fi
  if [ "$CEC_EXPLICIT" = "0" ]; then
    CEC_CHOICE="auto"
    if saved_cec_choice="$(read_dotenv_key "$INSTALL_DIR/.env" RELAYTV_CEC_ENABLED)" && [ -n "$saved_cec_choice" ]; then
      case "$saved_cec_choice" in
        0|1) CEC_CHOICE="$saved_cec_choice" ;;
      esac
    fi
  fi
}

resolve_saved_install_defaults

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

probe_docker_compose_working_dir() {
  [ "$DISCOVER_DOCKER" = "1" ] || return 1
  command -v docker >/dev/null 2>&1 || return 1
  local fmt='{{ index .Config.Labels "com.docker.compose.project.working_dir" }}'
  local working_dir=""
  working_dir="$(docker inspect relaytv --format "$fmt" 2>/dev/null || true)"
  if [ -z "$working_dir" ] && [ "$(id -u 2>/dev/null || printf '1')" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
    working_dir="$(sudo -n docker inspect relaytv --format "$fmt" 2>/dev/null || true)"
  fi
  if [ -n "$working_dir" ] && is_deployed_relaytv_dir "$working_dir"; then
    (cd "$working_dir" 2>/dev/null && pwd -P) || printf '%s\n' "$working_dir"
    return 0
  fi
  return 1
}

discover_existing_install_dir() {
  local cand="" target_user="" target_home="" home_cand=""
  if cand="$(probe_docker_compose_working_dir)" && [ -n "$cand" ]; then
    printf '%s\n' "$cand"
    return 0
  fi
  if [ "$(id -u 2>/dev/null || printf '1')" -eq 0 ]; then
    target_user="${RELAYTV_TARGET_USER:-${SUDO_USER:-}}"
    if [ -n "$target_user" ] && [ "$target_user" != "root" ] && command -v getent >/dev/null 2>&1 && command -v awk >/dev/null 2>&1; then
      target_home="$(getent passwd "$target_user" 2>/dev/null | awk -F: '{print $6}' || true)"
    fi
  fi
  if [ -n "${HOME:-}" ] && [ "${HOME%/}" != "/root" ]; then
    home_cand="${HOME%/}/relaytv"
    if is_deployed_relaytv_dir "$home_cand"; then
      (cd "$home_cand" 2>/dev/null && pwd -P) || printf '%s\n' "$home_cand"
      return 0
    fi
  fi
  if [ -n "$target_home" ]; then
    home_cand="${target_home%/}/relaytv"
    if is_deployed_relaytv_dir "$home_cand"; then
      (cd "$home_cand" 2>/dev/null && pwd -P) || printf '%s\n' "$home_cand"
      return 0
    fi
  fi
  if [ -n "${HOME:-}" ]; then
    home_cand="${HOME%/}/relaytv"
    if is_deployed_relaytv_dir "$home_cand"; then
      (cd "$home_cand" 2>/dev/null && pwd -P) || printf '%s\n' "$home_cand"
      return 0
    fi
  fi
  return 1
}

prompt_or_adopt_discovered_install() {
  local discovered="$1" answer=""
  [ -n "$discovered" ] || return 1
  if [ "$UPDATE_REQUESTED" = "1" ]; then
    INSTALL_DIR="$discovered"
    IS_UPDATE="1"
    CURRENT_DIR_CONFIRMED="0"
    resolve_saved_install_defaults
    return 0
  fi
  if [ "$ASSUME_YES" != "1" ] && has_controlling_tty; then
    {
      say ""
      say "Existing RelayTV installation detected in:"
      say "  $discovered"
      say ""
      printf "Update this installation instead of installing into %s? [Y/n] " "$INSTALL_DIR"
    } > /dev/tty
    read -r answer < /dev/tty || true
    case "${answer,,}" in
      ""|y|yes)
        INSTALL_DIR="$discovered"
        IS_UPDATE="1"
        CURRENT_DIR_CONFIRMED="0"
        resolve_saved_install_defaults
        return 0
        ;;
      n|no)
        return 1
        ;;
      *)
        die "Cancelled. Re-run from $discovered (or pass --update) to update RelayTV."
        ;;
    esac
  fi
  return 1
}

resolve_install_or_update_target() {
  local discovered=""
  if is_relaytv_install_dir "$INSTALL_DIR"; then
    INSTALL_DIR="$(cd "$INSTALL_DIR" 2>/dev/null && pwd -P || printf '%s' "$INSTALL_DIR")"
    IS_UPDATE="1"
    resolve_saved_install_defaults
    return 0
  fi

  if [ "$INSTALL_DIR_EXPLICIT" = "0" ]; then
    discovered="$(discover_existing_install_dir || true)"
    if [ -n "$discovered" ]; then
      prompt_or_adopt_discovered_install "$discovered" || true
      return 0
    fi
  fi

  return 0
}

can_defer_discovery_until_sudo_docker() {
  [ "$IS_UPDATE" != "1" ] || return 1
  [ "$INSTALL_DIR_EXPLICIT" = "0" ] || return 1
  [ "$DISCOVER_DOCKER" = "1" ] || return 1
  [ "$(id -u 2>/dev/null || printf '1')" -ne 0 ] || return 1
  command -v docker >/dev/null 2>&1 || return 1
  command -v sudo >/dev/null 2>&1 || return 1
  ! docker info >/dev/null 2>&1 || return 1
  if [ "$UPDATE_REQUESTED" = "1" ]; then
    return 0
  fi
  [ "$ASSUME_YES" != "1" ] && has_controlling_tty
}

confirm_current_directory_install() {
  if [ "$IS_UPDATE" = "1" ] || [ "$CURRENT_DIR_CONFIRMED" = "1" ]; then
    return 0
  fi
  if [ "$UPDATE_REQUESTED" = "1" ]; then
    die "No existing RelayTV installation found in $INSTALL_DIR. Omit --update for a fresh install, or pass --dir PATH."
  fi

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
    die "Refusing to install directly into your home directory ($INSTALL_DIR) with --yes. Create a subdirectory first (mkdir -p ~/relaytv && cd ~/relaytv), pass --dir PATH, pass --update for an existing install, or add --force."
  fi
  if [ "$INSTALL_DIR_EXPLICIT" = "1" ] || [ "$ASSUME_YES" = "1" ]; then
    CURRENT_DIR_CONFIRMED="1"
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
    y|yes)
      CURRENT_DIR_CONFIRMED="1"
      ;;
    *)
      die "Installation cancelled. Run the installer from the directory where you want to install the RelayTV service."
      ;;
  esac
}

need_cmd id
need_cmd mkdir
need_cmd chmod
need_cmd cp
need_cmd mktemp
need_cmd uname

resolve_install_or_update_target
validate_host_platform
if ! can_defer_discovery_until_sudo_docker; then
  confirm_current_directory_install
fi

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
NVIDIA_TOOLKIT_HINT="${RELAYTV_NVIDIA_DOCKER_TOOLKIT:-}"
case "$NVIDIA_TOOLKIT_HINT" in
  ""|0|1) ;;
  *)
    printf "WARN: RELAYTV_NVIDIA_DOCKER_TOOLKIT must be 0 or 1. Falling back to auto-detection.\n" >&2
    NVIDIA_TOOLKIT_HINT=""
    ;;
esac
NVIDIA_CDI_HINT="${RELAYTV_NVIDIA_CDI:-}"
case "$NVIDIA_CDI_HINT" in
  ""|0|1) ;;
  *)
    printf "WARN: RELAYTV_NVIDIA_CDI must be 0 or 1. Falling back to auto-detection.\n" >&2
    NVIDIA_CDI_HINT=""
    ;;
esac
ensure_docker_ready() {
  local direct_error="" sudo_docker_info=""
  if ! command -v docker >/dev/null 2>&1; then
    if [ "$UPDATE_REQUESTED" = "1" ]; then
      die "Docker is not installed; no existing RelayTV container installation was found."
    fi
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
  if command -v sudo >/dev/null 2>&1 && sudo_docker_info="$(sudo docker info 2>/dev/null)"; then
    DOCKER_CMD=(sudo docker)
    if [ -z "$NVIDIA_TOOLKIT_HINT" ] && [ -n "$sudo_docker_info" ]; then
      if printf '%s\n' "$sudo_docker_info" | grep -qiE '^[[:space:]]*Runtimes:.*(^|[[:space:]])nvidia([[:space:]]|$)'; then
        NVIDIA_TOOLKIT_HINT="1"
      else
        NVIDIA_TOOLKIT_HINT="0"
      fi
    fi
    if [ -z "$NVIDIA_CDI_HINT" ] && [ -n "$sudo_docker_info" ]; then
      if [[ "${sudo_docker_info,,}" == *"nvidia.com/gpu"* ]]; then
        NVIDIA_CDI_HINT="1"
      else
        NVIDIA_CDI_HINT="0"
      fi
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

# Retry Docker label discovery when Docker required sudo credentials before
# `docker inspect` could read container labels.
if [ "$IS_UPDATE" != "1" ] && [ "$INSTALL_DIR_EXPLICIT" = "0" ] && [ "$DISCOVER_DOCKER" = "1" ] && [ "$CURRENT_DIR_CONFIRMED" != "1" ]; then
  late_discovered_dir="$("${DOCKER_CMD[@]}" inspect relaytv --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' 2>/dev/null || true)"
  if [ -n "$late_discovered_dir" ] && is_deployed_relaytv_dir "$late_discovered_dir"; then
    late_discovered_dir="$(cd "$late_discovered_dir" 2>/dev/null && pwd -P || printf '%s' "$late_discovered_dir")"
    if prompt_or_adopt_discovered_install "$late_discovered_dir"; then
      validate_host_platform
    fi
  fi
fi
confirm_current_directory_install

read_install_port() {
  local port="${RELAYTV_PORT:-}"
  if [ -z "$port" ]; then
    port="$(read_dotenv_key "$INSTALL_DIR/.env" RELAYTV_PORT || true)"
  fi
  if [ -z "$port" ]; then
    port="$(read_dotenv_key "$INSTALL_DIR/.env" PORT || true)"
  fi
  if [[ ! "$port" =~ ^[0-9]+$ ]] || (( 10#$port < 1 || 10#$port > 65535 )); then
    port="8787"
  fi
  printf '%d' "$((10#$port))"
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
  return 1
}

inspect_container_running() {
  local state=""
  state="$("${DOCKER_CMD[@]}" inspect -f '{{.State.Running}}' relaytv 2>/dev/null || true)"
  [ "$state" = "true" ]
}

inspect_container_image_id() {
  "${DOCKER_CMD[@]}" inspect -f '{{.Image}}' relaytv 2>/dev/null || true
}

inspect_image_id() {
  local ref="$1"
  [ -n "$ref" ] || return 0
  "${DOCKER_CMD[@]}" image inspect -f '{{.Id}}' "$ref" 2>/dev/null || true
}

inspect_version_summary() {
  local target="$1" ver="" rev=""
  [ -n "$target" ] || return 0
  ver="$("${DOCKER_CMD[@]}" inspect -f '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$target" 2>/dev/null || true)"
  rev="$("${DOCKER_CMD[@]}" inspect -f '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$target" 2>/dev/null || true)"
  if [ -n "$rev" ] && [ "${#rev}" -gt 12 ]; then
    rev="${rev:0:12}"
  fi
  if [ -n "$ver" ] && [ -n "$rev" ] && [ "$ver" != "$rev" ]; then
    printf '%s (%s)' "$ver" "$rev"
  elif [ -n "$ver" ]; then
    printf '%s' "$ver"
  elif [ -n "$rev" ]; then
    printf '%s' "$rev"
  elif [[ "$target" == sha256:* ]]; then
    printf '%s' "${target:7:12}"
  fi
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

is_valid_updater_bootstrap() {
  local candidate="$1"
  [ -s "$candidate" ] \
    && grep -q -- '--update' "$candidate" 2>/dev/null \
    && grep -q -- 'Useful commands:' "$candidate" 2>/dev/null \
    && bash -n "$candidate" >/dev/null 2>&1
}

fetch_bootstrap_installer() {
  local dest="$1" default_raw_base="" alt_dest="" primary_ok="0"
  alt_dest="${dest}.main"
  if fetch_to "$RAW_BASE/install.sh" "$dest" 2>/dev/null && [ -s "$dest" ] && bash -n "$dest" >/dev/null 2>&1; then
    primary_ok="1"
    if is_valid_updater_bootstrap "$dest"; then
      return 0
    fi
  else
    rm -f "$dest"
  fi
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ] && is_valid_updater_bootstrap "${BASH_SOURCE[0]}"; then
    cp "${BASH_SOURCE[0]}" "$dest"
    return 0
  fi
  if [ -n "${RELAYTV_BOOTSTRAP_RAW_BASE:-}" ]; then
    default_raw_base="$RELAYTV_BOOTSTRAP_RAW_BASE"
  else
    default_raw_base="https://raw.githubusercontent.com/${REPO}/${DEFAULT_BRANCH}"
  fi
  if "${FETCH[@]}" "${default_raw_base}/install.sh" > "$alt_dest" 2>/dev/null && is_valid_updater_bootstrap "$alt_dest"; then
    mv -f "$alt_dest" "$dest"
    return 0
  fi
  rm -f "$alt_dest"
  if [ "$primary_ok" = "1" ] && [ -s "$dest" ]; then
    return 0
  fi
  if fetch_to "$RAW_BASE/install.sh" "$dest" && [ -s "$dest" ] && bash -n "$dest" >/dev/null 2>&1; then
    return 0
  fi
  rm -f "$dest"
  return 1
}

if [ -e "$INSTALL_DIR/.git" ] && [ "$FORCE_INSTALL" != "1" ]; then
  die "$INSTALL_DIR looks like a source checkout. Use scripts/install.sh from that checkout, choose --dir, or rerun with --force."
fi
if [ -f "$INSTALL_DIR/docker-compose.yml" ] && [ "$FORCE_INSTALL" != "1" ] && ! grep -q 'relaytv:' "$INSTALL_DIR/docker-compose.yml" 2>/dev/null; then
  die "$INSTALL_DIR/docker-compose.yml already exists and does not look like a RelayTV compose file. Choose --dir or rerun with --force."
fi
if [ -f "$INSTALL_DIR/docker-compose.yml" ] && [ "$FORCE_INSTALL" != "1" ] && compose_file_has_local_build "$INSTALL_DIR/docker-compose.yml"; then
  die "$INSTALL_DIR/docker-compose.yml looks like a source-build compose file. Use scripts/install.sh from that checkout, choose --dir, or rerun with --force."
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

restore_target_ownership() {
  [ "$(id -u)" -eq 0 ] || return 0
  local target_user="${RELAYTV_TARGET_USER:-${SUDO_USER:-}}" target_uid target_gid p
  if [ -n "$target_user" ] && [ "$target_user" != "root" ] && id -u "$target_user" >/dev/null 2>&1; then
    target_uid="$(id -u "$target_user")"
    target_gid="$(id -g "$target_user")"
    for p in \
      "$INSTALL_DIR" \
      "$INSTALL_DIR/scripts" \
      "$INSTALL_DIR/data" \
      "$INSTALL_DIR/bin" \
      "$INSTALL_DIR/docker-compose.yml" \
      "$INSTALL_DIR/docker-compose.override.yml" \
      "$INSTALL_DIR/.env" \
      "$INSTALL_DIR/install.sh" \
      "$INSTALL_DIR/scripts/install.sh" \
      "$INSTALL_DIR/scripts/doctor.sh" \
      "$INSTALL_DIR/scripts/host-ops.sh"; do
      if [ -e "$p" ]; then
        chown "${target_uid}:${target_gid}" "$p" 2>/dev/null || true
      fi
    done
  fi
}

MANAGED_BACKUP_PATHS=(
  "docker-compose.yml"
  "docker-compose.override.yml"
  ".env"
  "install.sh"
  "scripts/install.sh"
  "scripts/doctor.sh"
  "scripts/host-ops.sh"
)
WAS_RUNNING="0"
PREV_CONTAINER_IMAGE_ID=""
PREV_VERSION_SUMMARY=""

capture_pre_update_state() {
  local rel prev_image_ref=""
  mkdir -p "$tmp_dir/backup/scripts"
  for rel in "${MANAGED_BACKUP_PATHS[@]}"; do
    if [ -f "$INSTALL_DIR/$rel" ]; then
      cp -p "$INSTALL_DIR/$rel" "$tmp_dir/backup/$rel"
    fi
  done
  if inspect_container_running; then
    WAS_RUNNING="1"
  fi
  prev_image_ref="$(read_dotenv_key "$INSTALL_DIR/.env" RELAYTV_IMAGE_REF || true)"
  if [ -z "$prev_image_ref" ]; then
    prev_image_ref="$DEFAULT_IMAGE"
  fi
  PREV_CONTAINER_IMAGE_ID="$(inspect_container_image_id)"
  if [ -z "$PREV_CONTAINER_IMAGE_ID" ]; then
    PREV_CONTAINER_IMAGE_ID="$(inspect_image_id "$prev_image_ref")"
  fi
  PREV_VERSION_SUMMARY="$(inspect_version_summary "${PREV_CONTAINER_IMAGE_ID:-relaytv}")"
}

compose_rollback_up() {
  (
    cd "$INSTALL_DIR"
    "${DOCKER_CMD[@]}" compose up -d --pull never --force-recreate 2>/dev/null \
      || "${DOCKER_CMD[@]}" compose up -d --force-recreate
  )
}

rollback_failed_update() {
  local failed_stage="$1" rel mode backed_mode="" rollback_image_ref="" current_img_id=""
  say "" >&2
  say "Update failed during ${failed_stage}; rolling back RelayTV in ${INSTALL_DIR}..." >&2

  for rel in "${MANAGED_BACKUP_PATHS[@]}"; do
    if [ -f "$tmp_dir/backup/$rel" ]; then
      mode="0644"
      case "$rel" in
        install.sh|scripts/*) mode="0755" ;;
        .env) mode="0600" ;;
      esac
      if command -v stat >/dev/null 2>&1; then
        backed_mode="$(stat -c '%a' "$tmp_dir/backup/$rel" 2>/dev/null || true)"
        if [[ "$backed_mode" =~ ^[0-7]{3,4}$ ]]; then
          mode="$backed_mode"
        fi
      fi
      install_file_atomic "$tmp_dir/backup/$rel" "$INSTALL_DIR/$rel" "$mode" || true
    elif [ -f "$INSTALL_DIR/$rel" ]; then
      rm -f "$INSTALL_DIR/$rel" || true
    fi
  done
  restore_target_ownership

  rollback_image_ref="$(read_dotenv_key "$INSTALL_DIR/.env" RELAYTV_IMAGE_REF || true)"
  if [ -z "$rollback_image_ref" ]; then
    rollback_image_ref="$DEFAULT_IMAGE"
  fi
  if [ -n "$PREV_CONTAINER_IMAGE_ID" ]; then
    current_img_id="$(inspect_image_id "$rollback_image_ref")"
    if [ "$current_img_id" != "$PREV_CONTAINER_IMAGE_ID" ] && [[ "$rollback_image_ref" != *@sha256:* ]]; then
      say "Restoring previous image ${PREV_VERSION_SUMMARY:-$PREV_CONTAINER_IMAGE_ID} -> ${rollback_image_ref}..." >&2
      "${DOCKER_CMD[@]}" image tag "$PREV_CONTAINER_IMAGE_ID" "$rollback_image_ref" >/dev/null 2>&1 || true
    fi
  fi

  if [ "$failed_stage" = "container startup" ] || [ "$failed_stage" = "health verification" ]; then
    if [ "$WAS_RUNNING" = "1" ]; then
      say "Restarting previous RelayTV container..." >&2
      if compose_rollback_up >&2 && wait_for_relaytv_health >&2; then
        say "Rollback succeeded: previous RelayTV (${PREV_VERSION_SUMMARY:-previous image}) is running and healthy." >&2
      else
        say "ERROR: Rollback restart did not pass health verification. Inspect '${DOCKER_CMD[*]} compose logs relaytv'." >&2
      fi
    else
      (cd "$INSTALL_DIR" && "${DOCKER_CMD[@]}" compose down) >/dev/null 2>&1 || true
      say "Stopped failed RelayTV container and restored previous configuration." >&2
    fi
  else
    say "Configuration files restored; existing RelayTV container was left untouched." >&2
  fi
}

stage_release_files() {
  install_file_atomic "$tmp_dir/docker-compose.yml" "$INSTALL_DIR/docker-compose.yml" 0644
  install_file_atomic "$tmp_dir/bootstrap-install.sh" "$INSTALL_DIR/install.sh" 0755
  install_file_atomic "$tmp_dir/install.sh" "$INSTALL_DIR/scripts/install.sh" 0755
  install_file_atomic "$tmp_dir/doctor.sh" "$INSTALL_DIR/scripts/doctor.sh" 0755
  install_file_atomic "$tmp_dir/host-ops.sh" "$INSTALL_DIR/scripts/host-ops.sh" 0755
  restore_target_ownership
}

if [ "$IS_UPDATE" = "1" ]; then
  capture_pre_update_state
  say "RelayTV updater"
  say "  Repository: ${REPO}"
  say "  Branch/tag: ${BRANCH}"
  say "  Install dir: ${INSTALL_DIR}"
  say "  Image: ${IMAGE_REF}"
  if [ -n "$PREV_VERSION_SUMMARY" ]; then
    say "  Current version: ${PREV_VERSION_SUMMARY}"
  fi
  if [ "$SAVED_IMAGE_PINNED" = "1" ]; then
    say "  NOTE: RELAYTV_IMAGE_REF is pinned to ${IMAGE_REF} in .env; keeping pinned image."
    say "        Pass --branch main (or --image ${DEFAULT_IMAGE}) to switch to latest, or --branch vX.Y.Z to update the pin."
  fi
else
  say "RelayTV bootstrap installer"
  say "  Repository: ${REPO}"
  say "  Branch/tag: ${BRANCH}"
  say "  Install dir: ${INSTALL_DIR}"
  say "  Image: ${IMAGE_REF}"
fi

fetch_to "$RAW_BASE/docker-compose.release.yml" "$tmp_dir/docker-compose.yml"
fetch_bootstrap_installer "$tmp_dir/bootstrap-install.sh"
fetch_to "$RAW_BASE/scripts/install.sh" "$tmp_dir/install.sh"
fetch_to "$RAW_BASE/scripts/doctor.sh" "$tmp_dir/doctor.sh"
fetch_to "$RAW_BASE/scripts/host-ops.sh" "$tmp_dir/host-ops.sh"

if ! stage_release_files; then
  if [ "$IS_UPDATE" = "1" ]; then
    rollback_failed_update "file staging"
  fi
  die "Failed to stage RelayTV release files in ${INSTALL_DIR}."
fi

say ""
say "Generating RelayTV runtime configuration..."
if ! (
  cd "$INSTALL_DIR"
  # Do not turn a saved CEC choice into an explicit override of runtime flags.
  if [ "$CEC_EXPLICIT" = "1" ]; then
    export RELAYTV_CEC_ENABLED="$CEC_CHOICE"
  else
    unset RELAYTV_CEC_ENABLED
  fi
  # Pre-0.13.2 tags carry a scripts/install.sh without SAVED_RELAYTV_MODE support;
  # forward the saved mode on flagless rollbacks so headless/offline hosts do not exit 3.
  if [ "$IS_UPDATE" = "1" ] && [ "$MODE_EXPLICIT" = "0" ] && ! grep -q 'SAVED_RELAYTV_MODE' ./scripts/install.sh 2>/dev/null; then
    saved_legacy_mode="$(read_dotenv_key "$INSTALL_DIR/.env" RELAYTV_MODE || true)"
    case "$saved_legacy_mode" in
      headless|wayland|x11|drm)
        export RELAYTV_INSTALL_MODE="$saved_legacy_mode"
        ;;
    esac
  fi
  RELAYTV_NVIDIA_DOCKER_TOOLKIT="$NVIDIA_TOOLKIT_HINT" RELAYTV_NVIDIA_CDI="$NVIDIA_CDI_HINT" RELAYTV_IMAGE_REF="$IMAGE_REF" RELAYTV_INSTALL_YES="$ASSUME_YES" ./scripts/install.sh "${INSTALL_ARGS[@]}"
); then
  if [ "$IS_UPDATE" = "1" ]; then
    rollback_failed_update "runtime configuration"
  fi
  die "Runtime configuration failed."
fi

if [ "$DO_PULL" = "1" ]; then
  say ""
  say "Pulling RelayTV image..."
  if ! (cd "$INSTALL_DIR" && "${DOCKER_CMD[@]}" compose pull); then
    if [ "$IS_UPDATE" = "1" ]; then
      rollback_failed_update "image pull"
    fi
    die "Image pull failed."
  fi
fi

if [ "$DO_START" = "1" ]; then
  say ""
  say "Starting RelayTV..."
  if ! (cd "$INSTALL_DIR" && "${DOCKER_CMD[@]}" compose up -d); then
    if [ "$IS_UPDATE" = "1" ]; then
      rollback_failed_update "container startup"
    fi
    die "Container startup failed."
  fi
  if ! wait_for_relaytv_health; then
    if [ "$IS_UPDATE" = "1" ]; then
      rollback_failed_update "health verification"
    fi
    die "Startup verification failed. Review the container status and logs above."
  fi
fi

RESOLVED_CEC_MODE="$(read_dotenv_key "$INSTALL_DIR/.env" RELAYTV_CEC_ENABLED || true)"
if [ -z "$RESOLVED_CEC_MODE" ]; then
  RESOLVED_CEC_MODE="0"
fi

if [ "$IS_UPDATE" = "1" ]; then
  NEW_CONTAINER_IMAGE_ID=""
  if [ "$DO_START" = "1" ]; then
    NEW_CONTAINER_IMAGE_ID="$(inspect_container_image_id)"
  fi
  if [ -z "$NEW_CONTAINER_IMAGE_ID" ]; then
    NEW_CONTAINER_IMAGE_ID="$(inspect_image_id "$IMAGE_REF")"
  fi
  NEW_VERSION_SUMMARY="$(inspect_version_summary "${NEW_CONTAINER_IMAGE_ID:-$IMAGE_REF}")"

  if [ "$PRUNE_OLD_IMAGE" = "1" ] && [ -n "$PREV_CONTAINER_IMAGE_ID" ] && [ -n "$NEW_CONTAINER_IMAGE_ID" ] && [ "$PREV_CONTAINER_IMAGE_ID" != "$NEW_CONTAINER_IMAGE_ID" ]; then
    if [ "$DO_START" = "1" ]; then
      say ""
      say "Removing superseded RelayTV image (${PREV_VERSION_SUMMARY:-$PREV_CONTAINER_IMAGE_ID})..."
      if ! "${DOCKER_CMD[@]}" image rm "$PREV_CONTAINER_IMAGE_ID" >/dev/null 2>&1; then
        say "NOTE: Kept previous image ${PREV_CONTAINER_IMAGE_ID} (still referenced by another tag or container)."
      fi
    else
      say ""
      say "NOTE: Skipping --prune because --no-start was specified."
    fi
  fi

  say ""
  say "RelayTV update complete."
  say "  Directory: $INSTALL_DIR"
  if [ -n "$PREV_CONTAINER_IMAGE_ID" ] && [ -n "$NEW_CONTAINER_IMAGE_ID" ] && [ "$PREV_CONTAINER_IMAGE_ID" = "$NEW_CONTAINER_IMAGE_ID" ]; then
    say "  Version: ${NEW_VERSION_SUMMARY:-$IMAGE_REF} (already up to date)"
  elif [ -n "$PREV_VERSION_SUMMARY" ] || [ -n "$NEW_VERSION_SUMMARY" ]; then
    if [ "$DO_START" = "1" ]; then
      say "  Version: ${PREV_VERSION_SUMMARY:-previous} -> ${NEW_VERSION_SUMMARY:-$IMAGE_REF}"
    else
      say "  Version: ${PREV_VERSION_SUMMARY:-previous} -> ${NEW_VERSION_SUMMARY:-$IMAGE_REF} (staged; --no-start)"
    fi
  else
    say "  Image: $IMAGE_REF"
  fi
  say "  CEC mode: $RESOLVED_CEC_MODE"
else
  say ""
  say "RelayTV install complete."
  say "  Directory: $INSTALL_DIR"
  say "  CEC mode: $RESOLVED_CEC_MODE"
fi
say ""
say "Useful commands:"
say "  cd $INSTALL_DIR"
say "  ./install.sh --update"
if [ "${DOCKER_CMD[0]}" = "sudo" ]; then
  say "  sudo docker compose pull && sudo docker compose up -d"
else
  say "  docker compose pull && docker compose up -d"
fi
say "  ./scripts/doctor.sh"
