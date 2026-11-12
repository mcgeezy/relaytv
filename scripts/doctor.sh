#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

say() { printf "\n\033[1m%s\033[0m\n" "$*"; }
kv() { printf "  %-24s %s\n" "$1" "$2"; }

say "RelayTV doctor"
kv "Date (UTC):" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
kv "Kernel:" "$(uname -srmo 2>/dev/null || true)"
kv "Arch:" "$(uname -m)"
kv "User:" "$(id -un) (uid=$(id -u), gid=$(id -g))"
kv "Session type:" "${XDG_SESSION_TYPE:-unknown}"
kv "DISPLAY:" "${DISPLAY:-<unset>}"

# Show GDM Wayland/X11 config hints when present (Ubuntu/Debian GNOME common case)
if command -v dpkg-query >/dev/null 2>&1; then
  if dpkg-query -W -f='${Status}' gdm3 2>/dev/null | grep -q "install ok installed"; then
    if [[ -f /etc/gdm3/custom.conf ]]; then
      wl_line="$(grep -E '^[[:space:]]*WaylandEnable=' /etc/gdm3/custom.conf 2>/dev/null | tail -n 1 || true)"
      say "GDM (X11/Wayland)"
      if [[ -n "$wl_line" ]]; then
        echo "  /etc/gdm3/custom.conf: $wl_line"
      else
        echo "  /etc/gdm3/custom.conf: WaylandEnable not set (default behavior applies)"
      fi
    fi
  fi
fi


SESSION="${XDG_SESSION_TYPE:-unknown}"
if [[ "$SESSION" == "wayland" ]]; then
  say "Overlay status"
  echo "  Wayland session detected — the host X11 overlay fallback is disabled under Wayland."
  echo "  Native Qt overlay/toast remains the primary path."
  echo "  Ubuntu/Debian GNOME: choose \"*on Xorg\" at login (gear icon), or disable Wayland in GDM."
fi


is_secret_env_key() {
  local key_uc
  key_uc="$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')"
  case "$key_uc" in
    *_FILE|*_PATH|*_DIR|*_ENABLED|*_MODE) return 1 ;;
  esac
  [[ "$key_uc" =~ (^|_)(TOKEN|KEY|PASSWORD|PASSWD|SECRET)($|_) ]]
}

print_redacted_env() {
  local file="$1" line prefix key rhs
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    if [[ "$line" =~ ^([[:space:]]*(export[[:space:]]+)?)([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      prefix="${BASH_REMATCH[1]}"
      key="${BASH_REMATCH[3]}"
      rhs="${BASH_REMATCH[4]}"
      if is_secret_env_key "$key" && [[ ! "$rhs" =~ ^[[:space:]]*(\"\"|\'\')?[[:space:]]*(#.*)?$ ]]; then
        printf "  %s%s=<redacted>\n" "$prefix" "$key"
        continue
      fi
    fi
    printf "  %s\n" "$line"
  done < "$file"
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

read_configured_port() {
  local port="${RELAYTV_PORT:-}" line matched_key raw
  if [[ -z "$port" && -f .env ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%$'\r'}"
      if [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?(RELAYTV_PORT|PORT)=(.*)$ ]]; then
        matched_key="${BASH_REMATCH[2]}"
        raw="$(parse_dotenv_scalar "$line")"
        if [[ "$matched_key" == "RELAYTV_PORT" || -z "$port" ]]; then
          port="$raw"
        fi
      fi
    done < .env
  fi
  if [[ ! "$port" =~ ^[0-9]+$ ]] || (( 10#$port < 1 || 10#$port > 65535 )); then
    port="8787"
  fi
  printf '%d\n' "$((10#$port))"
}

if [[ -f .env ]]; then
  say ".env"
  if command -v stat >/dev/null 2>&1; then
    env_mode="$(stat -c '%a' .env 2>/dev/null || true)"
    if [[ -n "$env_mode" && "$env_mode" != "600" ]]; then
      echo "  WARN: .env mode is ${env_mode} (expected 600)"
    fi
  fi
  print_redacted_env .env
else
  say ".env"
  echo "  (missing) — run ./scripts/install.sh"
fi

say "Host devices"
for p in /dev/dri /dev/dri/renderD128 /dev/snd /dev/cec0 /sys/class/drm /dev/tty0; do
  if [[ -e "$p" ]]; then
    kv "$p" "✓ present"
  else
    kv "$p" "- missing"
  fi
done

say "Docker"
if command -v docker >/dev/null 2>&1; then
  kv "docker:" "$(docker --version 2>/dev/null || true)"
else
  kv "docker:" "NOT FOUND"
fi
if command -v docker >/dev/null 2>&1; then
  if docker compose version >/dev/null 2>&1; then
    kv "docker compose:" "$(docker compose version 2>/dev/null | head -n 1)"
  else
    kv "docker compose:" "NOT FOUND (use docker-compose v1 or install plugin)"
  fi
fi

say "Compose config sanity"
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  if docker compose config >/dev/null 2>&1; then
    echo "  ✓ docker compose config OK"
  else
    echo "  ✗ docker compose config FAILED"
    docker compose config >/dev/null || true
  fi
else
  echo "  (skipped)"
fi

say "Container status"
if command -v docker >/dev/null 2>&1; then
  docker ps --filter "name=relaytv" --format "  {{.Names}}\t{{.Status}}\t{{.Ports}}" || true
else
  echo "  (docker not installed)"
fi

say "HTTP health checks"
if command -v curl >/dev/null 2>&1; then
  port="$(read_configured_port)"
  base="${RELAYTV_BASE:-http://127.0.0.1:${port}}"
  for ep in /health /status /ui /x11/overlay; do
    code="$(curl -s -o /dev/null -w "%{http_code}" "$base$ep" || true)"
    kv "$base$ep" "$code"
  done
else
  echo "  curl not installed; skipping HTTP checks"
fi

say "Overlay runtime hints"
if [[ "${XDG_SESSION_TYPE:-}" == "x11" ]]; then
  echo "  X11 session detected."
  echo "  Host X11 overlay fallback is available if explicitly enabled (RELAYTV_X11_OVERLAY=1):"
  echo "    - Host overlay app: python3 app/relaytv_app/overlay_app.py --url http://127.0.0.1:8787/x11/overlay"
  echo "    - In-container overlay requires X11 socket + Xauthority passthrough."
else
  echo "  Not an X11 session. Host X11 overlay fallback will not be active."
fi

echo
echo "Done."
