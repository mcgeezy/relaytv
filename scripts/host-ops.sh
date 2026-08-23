#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELAY_CONTAINER_DEFAULT="${RELAYTV_CONTAINER:-}"
JELLYFIN_CONTAINER_DEFAULT="${JELLYFIN_CONTAINER:-jellyfin}"

warn_deprecated() {
  echo "WARN: $*" >&2
}

script_name="$(basename "$0")"
if [[ "$script_name" == "host-shell-v2-ops.sh" ]]; then
  warn_deprecated "host-shell-v2-ops.sh is deprecated; use ./scripts/host-ops.sh"
fi

usage() {
  cat <<'EOF'
Usage:
  ./scripts/host-ops.sh build [--no-cache]
  ./scripts/host-ops.sh up [--build] [--no-cache] [--wayland-native|--x11-native|--headless] [--native-playback|--stable-playback]
  ./scripts/host-ops.sh rebuild [--no-cache] [--wayland-native|--x11-native|--headless] [--native-playback|--stable-playback]
  ./scripts/host-ops.sh logs [relaytv|jellyfin|both] [--since 5m] [--tail 260] [--grep REGEX]
  ./scripts/host-ops.sh status
  ./scripts/host-ops.sh native-ready [--wait SEC]
  ./scripts/host-ops.sh acceptance [--wait SEC] [--no-up] [--wayland-native|--x11-native|--headless] [--native-playback|--stable-playback] [--skip-youtube] [--youtube-url URL] [--check-seconds 10] [--connect-secs 20]
  ./scripts/host-ops.sh toast-burst [--count 10] [--duration 1.0] [--interval 1.0] [--text "Toast burst"] [--level info] [--position top-right]
  ./scripts/host-ops.sh smoke [--no-cache] [--wayland-native|--x11-native|--headless] [--native-playback|--stable-playback]   # native-first
  ./scripts/host-ops.sh soak [--preset short|30m|overnight] [--sec 180] [--poll 5] [--native-qt] [--wayland-native|--x11-native|--headless] [--native-playback|--stable-playback] [--capture-logs-on-pass] [--artifact-dir ./logs/relaytv-hostops-soak] [--report FILE] [--no-up]
EOF
}

read_env_value() {
  local key="$1"
  local env_file="$ROOT_DIR/.env"
  if [[ -f "$env_file" ]]; then
    awk -F= -v k="$key" '$1==k {print $2; exit}' "$env_file"
  fi
}

resolve_delegate_qpa() {
  local mode="$1"
  local configured=""
  case "$mode" in
    wayland-native)
      configured="${QT_QPA_PLATFORM:-}"
      if [[ -z "$configured" ]]; then
        configured="$(read_env_value QT_QPA_PLATFORM | tr -d '\r[:space:]' || true)"
      fi
      printf "%s" "${configured:-wayland}"
      ;;
    x11-native|x11-compat|headless)
      printf "xcb"
      ;;
  esac
}

configured_runtime_profile() {
  printf "native_qt"
}

native_qt_enable_hint() {
  local relay_mode
  relay_mode="$(read_env_value RELAYTV_MODE | tr -d '\r' | xargs || true)"
  case "$relay_mode" in
    x11)
      printf "./scripts/install.sh --mode x11 --native-qt && ./scripts/host-ops.sh up --x11-native --native-playback"
      ;;
    headless|drm)
      printf "native_qt is the only retained profile; reinstall on a wayland/x11 desktop session for visual runtime checks"
      ;;
    *)
      printf "./scripts/install.sh --mode wayland --native-qt && ./scripts/host-ops.sh up --wayland-native --stable-playback"
      ;;
  esac
}

compose_target_uid() {
  local uid="${PUID:-}"
  if [[ -z "$uid" && -f "$ROOT_DIR/.env" ]]; then
    uid="$(awk -F= '$1=="PUID"{print $2; exit}' "$ROOT_DIR/.env" | tr -d '[:space:]')"
  fi
  if [[ -z "$uid" ]]; then
    uid="$(id -u)"
  fi
  printf "%s" "$uid"
}

resolve_runtime_dir() {
  local uid="$1"
  if [[ -n "${XDG_RUNTIME_DIR:-}" && -d "${XDG_RUNTIME_DIR}" ]]; then
    printf "%s" "${XDG_RUNTIME_DIR}"
    return 0
  fi
  local runtime_dir="/run/user/${uid}"
  if [[ -d "$runtime_dir" ]]; then
    printf "%s" "$runtime_dir"
    return 0
  fi
  printf "/tmp"
}

resolve_wayland_display_name() {
  local runtime_dir="$1"
  local explicit="${WAYLAND_DISPLAY:-}"
  if [[ -n "$explicit" && -S "${runtime_dir}/${explicit}" ]]; then
    printf "%s" "$explicit"
    return 0
  fi
  local candidate=""
  candidate="$(ls "${runtime_dir}"/wayland-* 2>/dev/null | head -n 1 || true)"
  if [[ -n "$candidate" ]]; then
    printf "%s" "$(basename "$candidate")"
    return 0
  fi
  printf "wayland-0"
}

resolve_display_name() {
  local explicit="${DISPLAY:-}"
  if [[ -n "$explicit" ]]; then
    printf "%s" "$explicit"
    return 0
  fi
  local from_env=""
  from_env="$(read_env_value DISPLAY | tr -d '\r' | xargs || true)"
  if [[ -n "$from_env" ]]; then
    printf "%s" "$from_env"
    return 0
  fi
  printf ":0"
}

resolve_live_xauthority() {
  local uid runtime_dir cand
  uid="$(compose_target_uid)"
  runtime_dir="${XDG_RUNTIME_DIR:-/run/user/${uid}}"

  if [[ -d "$runtime_dir" ]]; then
    cand="$(ls -1t "${runtime_dir}"/.mutter-Xwaylandauth.* 2>/dev/null | head -n 1 || true)"
    if [[ -n "$cand" && -r "$cand" ]]; then
      printf "%s" "$cand"
      return 0
    fi
    if [[ -r "${runtime_dir}/gdm/Xauthority" ]]; then
      printf "%s" "${runtime_dir}/gdm/Xauthority"
      return 0
    fi
  fi

  if [[ -r "${HOME}/.Xauthority" ]]; then
    printf "%s" "${HOME}/.Xauthority"
    return 0
  fi
  return 1
}

resolve_service_container() {
  local service="$1"
  local explicit_name="$2"
  if [[ -n "$explicit_name" ]]; then
    printf "%s" "$explicit_name"
    return 0
  fi
  local cid=""
  cid="$(cd "$ROOT_DIR" && docker compose ps -q "$service" 2>/dev/null | head -n 1 || true)"
  if [[ -n "$cid" ]]; then
    printf "%s" "$cid"
    return 0
  fi
  printf "%s" "$service"
}

run_compose_build() {
  local no_cache="$1"
  local args=(compose build relaytv)
  if [[ "$no_cache" == "1" ]]; then
    args=(compose build --no-cache relaytv)
  fi
  (cd "$ROOT_DIR" && docker "${args[@]}")
}

run_compose_up() {
  local with_build="$1"
  local no_cache="$2"
  local mode="$3"
  local native_playback="$4"
  local env_args=()
  local uid runtime_dir wayland_display_name display_name delegate_qpa
  uid="$(compose_target_uid)"
  runtime_dir="$(resolve_runtime_dir "$uid")"
  wayland_display_name="$(resolve_wayland_display_name "$runtime_dir")"
  display_name="$(resolve_display_name)"
  delegate_qpa="$(resolve_delegate_qpa "$mode")"
  if [[ "$mode" == "wayland-native" ]]; then
    env_args+=(
      DISPLAY="$display_name"
      XDG_RUNTIME_DIR="$runtime_dir"
      WAYLAND_DISPLAY="$wayland_display_name"
      XDG_SESSION_TYPE=wayland
      RELAYTV_MODE=wayland
      QT_QPA_PLATFORM="$delegate_qpa"
    )
  elif [[ "$mode" == "x11-native" || "$mode" == "x11-compat" ]]; then
    env_args+=(
      DISPLAY="$display_name"
      XDG_RUNTIME_DIR="$runtime_dir"
      WAYLAND_DISPLAY=
      XDG_SESSION_TYPE=x11
      RELAYTV_MODE=x11
      QT_QPA_PLATFORM=xcb
    )
  elif [[ "$mode" == "headless" ]]; then
    env_args+=(
      DISPLAY=:99
      XDG_RUNTIME_DIR="$runtime_dir"
      WAYLAND_DISPLAY=
      XDG_SESSION_TYPE=x11
      RELAYTV_MODE=headless
      QT_QPA_PLATFORM=xcb
      RELAYTV_XAUTHORITY_HOST_PATH=
      RELAYTV_HEADLESS_REMOTE_ENABLED=1
      RELAYTV_HEADLESS_VNC_ENABLED=1
    )
  fi
  if [[ "$mode" == "wayland-native" || "$mode" == "x11-native" || "$mode" == "x11-compat" ]]; then
    local xauth_override="${RELAYTV_XAUTHORITY_HOST_PATH:-}"
    if [[ -z "$xauth_override" ]]; then
      xauth_override="$(resolve_live_xauthority || true)"
    fi
    if [[ -n "$xauth_override" ]]; then
      env_args+=(RELAYTV_XAUTHORITY_HOST_PATH="$xauth_override")
      echo "host-shell-v2-ops: RELAYTV_XAUTHORITY_HOST_PATH=${xauth_override}"
    fi
  fi
  if [[ "$mode" == "wayland-native" ]]; then
    echo "host-shell-v2-ops: mode=wayland-native DISPLAY=${display_name} XDG_RUNTIME_DIR=${runtime_dir} WAYLAND_DISPLAY=${wayland_display_name} DELEGATE_QPA=${delegate_qpa:-<unset>}"
  elif [[ "$mode" == "x11-native" || "$mode" == "x11-compat" ]]; then
    echo "host-shell-v2-ops: mode=x11-native DISPLAY=${display_name} XDG_RUNTIME_DIR=${runtime_dir} DELEGATE_QPA=${delegate_qpa:-<unset>}"
  elif [[ "$mode" == "headless" ]]; then
    echo "host-shell-v2-ops: mode=headless DISPLAY=:99 XDG_RUNTIME_DIR=${runtime_dir} QPA=xcb VNC=${RELAYTV_HEADLESS_VNC_LISTEN:-127.0.0.1}:${RELAYTV_HEADLESS_VNC_PORT:-5900}"
  fi

  local cmd=(docker compose up -d relaytv)
  if [[ "$with_build" == "1" ]]; then
    if [[ "$no_cache" == "1" ]]; then
      if [[ "${#env_args[@]}" -gt 0 ]]; then
        (cd "$ROOT_DIR" && env "${env_args[@]}" docker compose build --no-cache relaytv && env "${env_args[@]}" "${cmd[@]}")
      else
        (cd "$ROOT_DIR" && docker compose build --no-cache relaytv && "${cmd[@]}")
      fi
      return
    fi
    cmd=(docker compose up -d --build relaytv)
  fi
  if [[ "${#env_args[@]}" -gt 0 ]]; then
    (cd "$ROOT_DIR" && env "${env_args[@]}" "${cmd[@]}")
  else
    (cd "$ROOT_DIR" && "${cmd[@]}")
  fi
}

resolve_pytest_bin() {
  if [[ -n "${PYTEST_BIN:-}" ]]; then
    printf "%s" "$PYTEST_BIN"
    return 0
  fi
  if [[ -x "/usr/bin/pytest" ]]; then
    printf "%s" "/usr/bin/pytest"
    return 0
  fi
  printf "%s" "pytest"
}

run_host_pytest() {
  local timeout_sec="$1"
  shift

  local pytest_bin
  pytest_bin="$(resolve_pytest_bin)"
  export PYTHONPATH="${PYTHONPATH:-app}"

  if [[ "$timeout_sec" =~ ^[0-9]+$ ]] && (( timeout_sec > 0 )) && command -v timeout >/dev/null 2>&1; then
    timeout "$timeout_sec" "$pytest_bin" -q "$@"
    return
  fi

  "$pytest_bin" -q "$@"
}

emit_soak_state_summary() {
  local status_src="$1"
  local runtime_src="$2"
  local prefix="${3:-soak state}"
  /usr/bin/python3 - "$status_src" "$runtime_src" "$prefix" <<'PY_STATE'
import json
import sys

status_path, runtime_path, prefix = sys.argv[1], sys.argv[2], sys.argv[3]
with open(status_path, "r", encoding="utf-8") as fh:
    status = json.load(fh)
with open(runtime_path, "r", encoding="utf-8") as fh:
    runtime = json.load(fh)

def pick(data, key):
    value = data.get(key)
    if value in (None, ""):
        return "-"
    return str(value)

parts = [
    f"playback_state={pick(status, 'playback_runtime_state')}",
    f"playback_reason={pick(status, 'playback_runtime_state_reason')}",
    f"playback_last_failure={pick(status, 'playback_runtime_last_failure_class')}",
    f"playback_last_recovery={pick(status, 'playback_runtime_last_recovery_action')}",
    f"overlay_state={pick(runtime, 'overlay_delivery_state')}",
    f"overlay_reason={pick(runtime, 'overlay_delivery_reason')}",
    f"overlay_last_failure={pick(runtime, 'overlay_delivery_last_failure_class')}",
    f"overlay_last_recovery={pick(runtime, 'overlay_delivery_last_recovery_action')}",
]
print(f"{prefix}: " + " ".join(parts))
PY_STATE
}

capture_soak_api_snapshots() {
  local artifact_dir="$1"
  local reason="$2"
  local status_src="$3"
  local runtime_src="$4"
  local diag_src="${5:-}"
  local ts
  ts="$(date -u +%Y%m%d-%H%M%S)"
  mkdir -p "$artifact_dir"
  local status_dst="${artifact_dir}/${ts}-${reason}-status.json"
  local runtime_dst="${artifact_dir}/${ts}-${reason}-runtime.json"
  local diag_dst=""
  cp "$status_src" "$status_dst" 2>/dev/null || true
  cp "$runtime_src" "$runtime_dst" 2>/dev/null || true
  local summary_dst="${artifact_dir}/${ts}-${reason}-state-summary.txt"
  emit_soak_state_summary "$status_src" "$runtime_src" "captured soak state" >"$summary_dst" || true
  if [[ -n "$diag_src" ]]; then
    diag_dst="${artifact_dir}/${ts}-${reason}-diagnostics.json"
    cp "$diag_src" "$diag_dst" 2>/dev/null || true
  fi
  echo "Captured soak API snapshots:"
  echo "  - $status_dst"
  echo "  - $runtime_dst"
  echo "  - $summary_dst"
  if [[ -n "$diag_dst" ]]; then
    echo "  - $diag_dst"
  fi
}

capture_soak_host_logs() {
  local artifact_dir="$1"
  local reason="$2"
  local relay_container="$3"
  local jellyfin_container="$4"
  local since_sec="${SOAK_LOG_SINCE_SEC:-300}"
  local ts
  ts="$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$artifact_dir"

  if ! command -v docker >/dev/null 2>&1; then
    echo "Host log capture skipped: docker not found."
    return 0
  fi

  local relaytv_log="${artifact_dir}/${ts}-${reason}-relaytv.log"
  local jellyfin_log="${artifact_dir}/${ts}-${reason}-jellyfin.log"

  if docker inspect -f '{{.State.Running}}' "$relay_container" 2>/dev/null | grep -qx 'true'; then
    docker logs --since="${since_sec}s" "$relay_container" >"$relaytv_log" 2>&1 || true
    echo "Captured host logs: $relaytv_log"
  else
    echo "Host log capture skipped: container '$relay_container' is not running."
  fi

  if [[ -n "$jellyfin_container" ]] && docker inspect -f '{{.State.Running}}' "$jellyfin_container" 2>/dev/null | grep -qx 'true'; then
    docker logs --since="${since_sec}s" "$jellyfin_container" >"$jellyfin_log" 2>&1 || true
    echo "Captured host logs: $jellyfin_log"
  fi
}

print_status() {
  local relay_container
  relay_container="$(resolve_service_container relaytv "$RELAY_CONTAINER_DEFAULT")"
  docker exec "$relay_container" /bin/sh -lc 'curl -fsS http://127.0.0.1:8787/status'
}

do_native_ready() {
  local wait_sec="${1:-0}"
  if ! [[ "$wait_sec" =~ ^[0-9]+$ ]]; then
    echo "native-ready: --wait must be a non-negative integer seconds value (got: ${wait_sec})" >&2
    return 2
  fi

  local relay_container
  relay_container="$(resolve_service_container relaytv "$RELAY_CONTAINER_DEFAULT")"

  local status_json runtime_json
  local deadline=$((SECONDS + wait_sec))
  while true; do
    if status_json="$(docker exec "$relay_container" /bin/sh -lc 'curl -fsS http://127.0.0.1:8787/status' 2>/dev/null)" \
      && runtime_json="$(docker exec "$relay_container" /bin/sh -lc 'curl -fsS http://127.0.0.1:8787/runtime/capabilities' 2>/dev/null)"; then
      break
    fi
    if (( SECONDS >= deadline )); then
      echo "native-ready: FAIL"
      echo "  - API endpoints not reachable after ${wait_sec}s (/status and /runtime/capabilities)"
      return 1
    fi
    sleep 1
  done

  local expected_profile
  expected_profile="$(configured_runtime_profile)"

  local qt_shell_running visual_runtime_mode notifications_deliverable qt_shell_module
  local configured_player_backend player_backend player_runtime_engine backend_runtime_mismatch headless_runtime host_session_type
  local relay_mode expected_visual_runtime
  qt_shell_running="$(jq -r '.qt_shell_running // false' <<<"$status_json")"
  visual_runtime_mode="$(jq -r '.visual_runtime_mode // ""' <<<"$status_json")"
  configured_player_backend="$(jq -r '.configured_player_backend // ""' <<<"$status_json")"
  player_backend="$(jq -r '.player_backend // ""' <<<"$status_json")"
  player_runtime_engine="$(jq -r '.player_runtime_engine // ""' <<<"$status_json")"
  backend_runtime_mismatch="$(jq -r '.backend_runtime_mismatch // false' <<<"$status_json")"
  notifications_deliverable="$(jq -r '.notifications_deliverable // false' <<<"$runtime_json")"
  headless_runtime="$(jq -r '.headless_runtime // false' <<<"$runtime_json")"
  host_session_type="$(jq -r '.host_session_type // ""' <<<"$runtime_json")"
  qt_shell_module="$(jq -r '.qt_shell_module // ""' <<<"$runtime_json")"
  relay_mode="$(read_env_value RELAYTV_MODE | tr -d '\r' | xargs || true)"
  case "$relay_mode" in
    headless|drm)
      expected_visual_runtime="headless"
      ;;
    *)
      expected_visual_runtime="qt_shell"
      ;;
  esac
  if [[ -z "$relay_mode" && "$host_session_type" == "headless" ]]; then
    expected_visual_runtime="headless"
  fi

  local -a failures=()
  if [[ "$configured_player_backend" != "qt" ]]; then
    failures+=("configured_player_backend=${configured_player_backend:-<unset>} (expected qt)")
  fi
  if [[ "$player_backend" != "$configured_player_backend" ]]; then
    failures+=("player_backend=${player_backend:-<unset>} (configured=${configured_player_backend:-<unset>})")
  fi
  if [[ "$backend_runtime_mismatch" == "true" ]]; then
    failures+=("backend_runtime_mismatch=true")
  fi
  if [[ "$visual_runtime_mode" != "$expected_visual_runtime" ]]; then
    failures+=("visual_runtime_mode=${visual_runtime_mode:-<unset>} (expected ${expected_visual_runtime})")
  fi

  if [[ "$expected_visual_runtime" == "qt_shell" ]]; then
    if [[ "$qt_shell_running" != "true" ]]; then
      failures+=("qt_shell_running=false")
    fi
    if [[ "$player_runtime_engine" != "qt_shell" ]]; then
      failures+=("player_runtime_engine=${player_runtime_engine:-<unset>} (expected qt_shell)")
    fi
    if [[ "$notifications_deliverable" != "true" ]]; then
      failures+=("notifications_deliverable=false")
    fi
    if [[ "$headless_runtime" == "true" ]]; then
      failures+=("headless_runtime=true (expected false)")
    fi
  else
    if [[ "$headless_runtime" != "true" ]]; then
      failures+=("headless_runtime=false (expected true)")
    fi
  fi

  if [[ "$qt_shell_module" != "relaytv_app.qt_shell_app" ]]; then
    failures+=("qt_shell_module=${qt_shell_module:-<unset>} (expected relaytv_app.qt_shell_app)")
  fi

  echo "native-ready: expected_profile=${expected_profile} expected_visual_runtime=${expected_visual_runtime} configured_player_backend=${configured_player_backend:-<unset>} player_backend=${player_backend:-<unset>} player_runtime_engine=${player_runtime_engine:-<unset>} backend_runtime_mismatch=${backend_runtime_mismatch:-<unset>} qt_shell_module=${qt_shell_module:-<unset>} visual_runtime_mode=${visual_runtime_mode:-<unset>} headless_runtime=${headless_runtime:-<unset>} notifications_deliverable=${notifications_deliverable:-<unset>}"

  if [[ "${#failures[@]}" -gt 0 ]]; then
    local fail
    echo "native-ready: FAIL"
    for fail in "${failures[@]}"; do
      echo "  - ${fail}"
    done
    echo "native-ready: fix hint -> ./scripts/install.sh --mode wayland --native-qt && ./scripts/host-ops.sh up --wayland-native --native-playback"
    return 1
  fi

  echo "native-ready: PASS"
  return 0
}

do_acceptance() {
  local wait_sec="$1"
  local mode="$2"
  local native_playback="$3"
  local skip_up="$4"
  local run_youtube="$5"
  local youtube_url="$6"
  local check_seconds="$7"
  local connect_secs="$8"

  if ! [[ "$wait_sec" =~ ^[0-9]+$ ]]; then
    echo "acceptance: --wait must be a non-negative integer seconds value (got: ${wait_sec})" >&2
    return 2
  fi
  if ! [[ "$check_seconds" =~ ^[0-9]+$ ]] || [[ "$check_seconds" -le 0 ]]; then
    echo "acceptance: --check-seconds must be a positive integer (got: ${check_seconds})" >&2
    return 2
  fi
  if ! [[ "$connect_secs" =~ ^[0-9]+$ ]] || [[ "$connect_secs" -le 0 ]]; then
    echo "acceptance: --connect-secs must be a positive integer (got: ${connect_secs})" >&2
    return 2
  fi

  if [[ "$skip_up" != "1" ]]; then
    run_compose_up 0 0 "$mode" "$native_playback"
  fi

  do_native_ready "$wait_sec"

  local configured_profile
  configured_profile="$(configured_runtime_profile)"
  if [[ "$configured_profile" != "native_qt" ]]; then
    echo "acceptance: note"
    echo "  - configured runtime profile is ${configured_profile}; continuing because native-ready validates the effective runtime"
    echo "acceptance: hint -> $(native_qt_enable_hint)"
  fi

  local relay_container
  relay_container="$(resolve_service_container relaytv "$RELAY_CONTAINER_DEFAULT")"

  local status_json runtime_json playback_json overlay_resp volume_up_resp volume_dn_resp
  status_json="$(docker exec "$relay_container" /bin/sh -lc 'curl -fsS http://127.0.0.1:8787/status')"
  runtime_json="$(docker exec "$relay_container" /bin/sh -lc 'curl -fsS http://127.0.0.1:8787/runtime/capabilities')"
  playback_json="$(docker exec "$relay_container" /bin/sh -lc 'curl -fsS http://127.0.0.1:8787/playback/state')"
  overlay_resp="$(docker exec "$relay_container" /bin/sh -lc "curl -fsS -X POST http://127.0.0.1:8787/overlay -H 'content-type: application/json' -d '{\"text\":\"acceptance-check\",\"duration\":1.0,\"position\":\"top-right\"}'")"
  volume_up_resp="$(docker exec "$relay_container" /bin/sh -lc "curl -fsS -X POST http://127.0.0.1:8787/volume -H 'content-type: application/json' -d '{\"delta\":1}'")"
  volume_dn_resp="$(docker exec "$relay_container" /bin/sh -lc "curl -fsS -X POST http://127.0.0.1:8787/volume -H 'content-type: application/json' -d '{\"delta\":-1}'")"

  local relay_mode expected_visual_runtime
  relay_mode="$(read_env_value RELAYTV_MODE | tr -d '\r' | xargs || true)"
  case "$relay_mode" in
    headless|drm)
      expected_visual_runtime="headless"
      ;;
    *)
      expected_visual_runtime="qt_shell"
      ;;
  esac
  if [[ -z "$relay_mode" ]]; then
    local host_session_type
    host_session_type="$(jq -r '.host_session_type // ""' <<<"$runtime_json")"
    if [[ "$host_session_type" == "headless" ]]; then
      expected_visual_runtime="headless"
    fi
  fi

  local -a failures=()
  local configured_backend player_backend runtime_engine runtime_mismatch visual_mode
  configured_backend="$(jq -r '.configured_player_backend // ""' <<<"$status_json")"
  player_backend="$(jq -r '.player_backend // ""' <<<"$status_json")"
  runtime_engine="$(jq -r '.player_runtime_engine // ""' <<<"$status_json")"
  runtime_mismatch="$(jq -r '.backend_runtime_mismatch // false' <<<"$status_json")"
  visual_mode="$(jq -r '.visual_runtime_mode // ""' <<<"$runtime_json")"
  if [[ "$configured_backend" != "qt" ]]; then
    failures+=("configured_player_backend=${configured_backend:-<unset>} (expected qt)")
  fi
  if [[ "$player_backend" != "qt" ]]; then
    failures+=("player_backend=${player_backend:-<unset>} (expected qt)")
  fi
  if [[ "$runtime_engine" != "qt_shell" ]]; then
    failures+=("player_runtime_engine=${runtime_engine:-<unset>} (expected qt_shell)")
  fi
  if [[ "$runtime_mismatch" == "true" ]]; then
    failures+=("backend_runtime_mismatch=true")
  fi
  if [[ "$visual_mode" != "$expected_visual_runtime" ]]; then
    failures+=("visual_runtime_mode=${visual_mode:-<unset>} (expected ${expected_visual_runtime})")
  fi

  local telemetry_selected telemetry_available telemetry_source fast_source
  telemetry_selected="$(jq -r '.native_qt_telemetry_selected // false' <<<"$runtime_json")"
  telemetry_available="$(jq -r '.native_qt_telemetry_available // false' <<<"$runtime_json")"
  telemetry_source="$(jq -r '.native_qt_telemetry_source // ""' <<<"$runtime_json")"
  fast_source="$(jq -r '.playback_telemetry_source // ""' <<<"$playback_json")"
  if [[ "$telemetry_selected" != "true" ]]; then
    failures+=("native_qt_telemetry_selected=false")
  fi
  if [[ "$telemetry_available" != "true" ]]; then
    failures+=("native_qt_telemetry_available=false")
  fi
  if [[ "$telemetry_source" != "qt_runtime" ]]; then
    failures+=("native_qt_telemetry_source=${telemetry_source:-<unset>} (expected qt_runtime)")
  fi
  if [[ "$fast_source" != "qt_runtime" && "$fast_source" != "qt_runtime_stale" ]]; then
    failures+=("playback_state.playback_telemetry_source=${fast_source:-<unset>} (expected qt_runtime*)")
  fi

  local overlay_ok overlay_deliverable
  overlay_ok="$(jq -r '.ok // false' <<<"$overlay_resp")"
  overlay_deliverable="$(jq -r '.notifications_deliverable // false' <<<"$overlay_resp")"
  if [[ "$overlay_ok" != "true" ]]; then
    failures+=("overlay.ok=false")
  fi
  if [[ "$expected_visual_runtime" == "qt_shell" && "$overlay_deliverable" != "true" ]]; then
    failures+=("overlay.notifications_deliverable=false")
  fi

  local volume_up_ok volume_up_ack volume_up_req
  local volume_dn_ok volume_dn_ack volume_dn_req
  local control_file sample_detail require_control_ack
  volume_up_ok="$(jq -r '.ok // false' <<<"$volume_up_resp")"
  volume_up_ack="$(jq -r '.ack_observed // false' <<<"$volume_up_resp")"
  volume_up_req="$(jq -r '.request_id // ""' <<<"$volume_up_resp")"
  volume_dn_ok="$(jq -r '.ok // false' <<<"$volume_dn_resp")"
  volume_dn_ack="$(jq -r '.ack_observed // false' <<<"$volume_dn_resp")"
  volume_dn_req="$(jq -r '.request_id // ""' <<<"$volume_dn_resp")"
  control_file="$(jq -r '.native_qt_telemetry_control_file // ""' <<<"$runtime_json")"
  sample_detail="$(jq -r '.native_qt_mpv_runtime_sample_detail // ""' <<<"$runtime_json" | tr '[:upper:]' '[:lower:]')"
  require_control_ack="1"
  if [[ -z "$control_file" || "$sample_detail" == subprocess_runtime* ]]; then
    require_control_ack="0"
  fi
  if [[ "$volume_up_ok" != "true" ]]; then
    failures+=("volume_up.ok=false")
  fi
  if [[ "$volume_dn_ok" != "true" ]]; then
    failures+=("volume_down.ok=false")
  fi
  if [[ "$require_control_ack" == "1" ]]; then
    if [[ "$volume_up_ack" != "true" ]]; then
      failures+=("volume_up.ack_observed=false")
    fi
    if [[ "$volume_dn_ack" != "true" ]]; then
      failures+=("volume_down.ack_observed=false")
    fi
    if [[ "$volume_up_req" != qtctl-* ]]; then
      failures+=("volume_up.request_id=${volume_up_req:-<unset>} (expected qtctl-*)")
    fi
    if [[ "$volume_dn_req" != qtctl-* ]]; then
      failures+=("volume_down.request_id=${volume_dn_req:-<unset>} (expected qtctl-*)")
    fi
  fi

  if [[ "${#failures[@]}" -gt 0 ]]; then
    local fail
    echo "acceptance: FAIL"
    for fail in "${failures[@]}"; do
      echo "  - ${fail}"
    done
    return 1
  fi

  echo "acceptance: core native contract PASS"
  echo "  backend=${player_backend} engine=${runtime_engine} visual=${visual_mode} telemetry=${telemetry_source} fast_source=${fast_source} overlay_deliverable=${overlay_deliverable}"
  if [[ "$require_control_ack" != "1" ]]; then
    echo "  note=volume control ack checks skipped (runtime control-file unavailable or subprocess_runtime telemetry)"
  fi

  if [[ "$run_youtube" == "1" ]]; then
    echo "acceptance: running YouTube pipeline check url=${youtube_url} check_seconds=${check_seconds} connect_secs=${connect_secs}"
    CHECK_SECONDS="$check_seconds" CHECK_CONNECT_SECS="$connect_secs" CHECK_PROVIDER=youtube CHECK_APP_PATH=1 \
      "$ROOT_DIR/scripts/pipeline-test.sh" "$youtube_url"

    echo "acceptance: verifying /play_now YouTube app-path mapping"
    local smart_payload smart_ok play_now_resp play_now_provider
    smart_payload="$(jq -cn --arg url "$youtube_url" '{url:$url}')"
    play_now_resp="$(curl -fsS -X POST "http://127.0.0.1:8787/play_now" -H "content-type: application/json" -d "$smart_payload")"
    play_now_provider="$(jq -r '.now_playing.provider // ""' <<<"$play_now_resp")"
    if [[ "$play_now_provider" != "youtube" ]]; then
      echo "acceptance: FAIL"
      echo "  - /play_now provider=${play_now_provider:-<unset>} (expected youtube)"
      return 1
    fi
    smart_ok=0
    local i app_status app_playing app_provider app_url app_stream
    for i in $(seq 1 20); do
      app_status="$(curl -fsS "http://127.0.0.1:8787/status")"
      app_playing="$(jq -r '.playing // false' <<<"$app_status")"
      app_provider="$(jq -r '.now_playing.provider // ""' <<<"$app_status")"
      app_url="$(jq -r '.now_playing.url // ""' <<<"$app_status")"
      app_stream="$(jq -r '.now_playing.stream // ""' <<<"$app_status")"
      if [[ "$app_playing" == "true" && ( "$app_provider" == "youtube" || "$app_url" == *"youtube.com"* || "$app_url" == *"youtu.be"* || "$app_stream" == *"googlevideo.com"* ) ]]; then
        smart_ok=1
        break
      fi
      sleep 1
    done
    if [[ "$smart_ok" != "1" ]]; then
      echo "acceptance: FAIL"
      echo "  - /play_now status check did not reach playing=true with YouTube identity within 20s"
      return 1
    fi
  else
    echo "acceptance: YouTube pipeline check skipped"
  fi

  echo "acceptance: PASS"
  return 0
}


build_soak_runtime_summary_json() {
  local relay_container="$1"
  docker exec "$relay_container" /bin/sh -lc '
python3 - <<"PY"
import json
import urllib.request

status = json.load(urllib.request.urlopen("http://127.0.0.1:8787/status", timeout=5))
runtime = json.load(urllib.request.urlopen("http://127.0.0.1:8787/runtime/capabilities", timeout=5))
out = {
    "playback_runtime_state": status.get("playback_runtime_state"),
    "playback_runtime_state_reason": status.get("playback_runtime_state_reason"),
    "playback_runtime_previous_state": status.get("playback_runtime_previous_state"),
    "playback_runtime_last_failure_class": status.get("playback_runtime_last_failure_class"),
    "playback_runtime_last_failure_unix": status.get("playback_runtime_last_failure_unix"),
    "playback_runtime_last_recovery_action": status.get("playback_runtime_last_recovery_action"),
    "playback_runtime_last_recovery_unix": status.get("playback_runtime_last_recovery_unix"),
    "overlay_delivery_state": runtime.get("overlay_delivery_state"),
    "overlay_delivery_reason": runtime.get("overlay_delivery_reason"),
    "overlay_delivery_previous_state": runtime.get("overlay_delivery_previous_state"),
    "overlay_delivery_last_failure_class": runtime.get("overlay_delivery_last_failure_class"),
    "overlay_delivery_last_failure_unix": runtime.get("overlay_delivery_last_failure_unix"),
    "overlay_delivery_last_recovery_action": runtime.get("overlay_delivery_last_recovery_action"),
    "overlay_delivery_last_recovery_unix": runtime.get("overlay_delivery_last_recovery_unix"),
}
print(json.dumps(out, sort_keys=True))
PY
'
}

do_soak() {
  local soak_sec="$1"
  local soak_poll="$2"
  local mode="$3"
  local native_playback="$4"
  local run_full="$5"
  local run_jellyfin_queue="$6"
  local capture_logs_on_pass="$7"
  local skip_up="$8"
  local artifact_dir="$9"
  local report_file="${10}"
  local soak_profile_requested="${11:-auto}"

  local started_utc ended_utc stamp output_log summary_file run_rc
  started_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  output_log=""
  summary_file=""
  if [[ -n "$artifact_dir" ]]; then
    mkdir -p "$artifact_dir"
    output_log="${artifact_dir}/${stamp}-soak.log"
    if [[ -n "$report_file" ]]; then
      summary_file="$report_file"
    else
      summary_file="${artifact_dir}/${stamp}-summary.json"
    fi
  elif [[ -n "$report_file" ]]; then
    summary_file="$report_file"
  fi

  local configured_profile soak_profile
  configured_profile="$(configured_runtime_profile)"
  soak_profile="${soak_profile_requested}"
  if [[ "$soak_profile" == "auto" || -z "$soak_profile" ]]; then
    soak_profile="$configured_profile"
  fi
  if [[ "$soak_profile" != "native_qt" ]]; then
    echo "Invalid soak profile: $soak_profile (expected auto|native_qt)" >&2
    exit 2
  fi
  if [[ "$soak_profile" == "native_qt" && "$configured_profile" != "native_qt" ]]; then
    echo "soak: note"
    echo "  - configured runtime profile is ${configured_profile}; continuing because native-ready validates the effective runtime"
    echo "soak: hint -> $(native_qt_enable_hint)"
  fi
  if [[ "$run_full" == "1" || "$run_jellyfin_queue" == "1" ]]; then
    echo "soak: note"
    echo "  - --full and --jellyfin-queue are compat-only checks and are ignored for native_qt soak runs"
    run_full=0
    run_jellyfin_queue=0
  fi

  # Native Qt profile should inherit the installer/.env turn-up policy and not
  # force legacy compatibility mode toggles.
  if [[ "$soak_profile" == "native_qt" ]]; then
    mode=""
    native_playback=""
  fi

  if [[ "$skip_up" != "1" ]]; then
    run_compose_up 0 0 "$mode" "$native_playback"
  fi
  if [[ "$skip_up" == "1" ]]; then
    do_native_ready 5
  else
    do_native_ready 25
  fi

  local soak_base_url="${SOAK_BASE_URL:-http://127.0.0.1:8787}"
  local soak_http_timeout_sec="${SOAK_HTTP_TIMEOUT_SEC:-8}"
  local soak_fail_diag_levels="${SOAK_FAIL_DIAG_LEVELS:-error}"
  local relay_container jellyfin_container
  relay_container="$(resolve_service_container relaytv "$RELAY_CONTAINER_DEFAULT")"
  jellyfin_container="$(resolve_service_container jellyfin "$JELLYFIN_CONTAINER_DEFAULT")"

  echo "soak: native-qt profile enabled; rollback-only compat preflight/diagnostic gates are retired"
  soak_fail_diag_levels=off

  echo "soak: sec=${soak_sec} poll=${soak_poll} profile=${soak_profile} mode=${mode:-inherit} native_playback=${native_playback:-inherit} full=${run_full} jellyfin_queue=${run_jellyfin_queue} capture_logs_on_pass=${capture_logs_on_pass} skip_up=${skip_up}"
  if [[ -n "$output_log" ]]; then
    echo "soak: output_log=${output_log}"
  fi

  do_soak_run() {
    local soak_iter soak_deadline status_tmp runtime_tmp diag_snapshot_arg

    echo "Running host-ops soak gate..."
    echo " - profile=${soak_profile}"
    echo " - base_url=${soak_base_url}"
    echo " - duration=${soak_sec}s poll=${soak_poll}s"
    echo " - fail_diag_levels=${soak_fail_diag_levels}"
    mkdir -p "$artifact_dir"

    soak_deadline=$((SECONDS + soak_sec))
    soak_iter=0
    while (( SECONDS < soak_deadline )); do
      soak_iter=$((soak_iter + 1))
      status_tmp="$(mktemp)"
      runtime_tmp="$(mktemp)"
      diag_snapshot_arg=""

      set +e
      curl -fsS --max-time "$soak_http_timeout_sec" "${soak_base_url}/status" >"$status_tmp"
      status_rc=$?
      curl -fsS --max-time "$soak_http_timeout_sec" "${soak_base_url}/runtime/capabilities" >"$runtime_tmp"
      runtime_rc=$?
      set -e

      if [[ "$status_rc" -ne 0 || "$runtime_rc" -ne 0 ]]; then
        echo "Soak gate failed: API polling error (status=$status_rc runtime=$runtime_rc)"
        capture_soak_host_logs "$artifact_dir" "soak-api-failed-${soak_iter}" "$relay_container" "$jellyfin_container"
        rm -f "$status_tmp" "$runtime_tmp"
        return 1
      fi

      if ! /usr/bin/python3 - "$status_tmp" "$runtime_tmp" "$soak_iter" <<'PY'
import json
import sys

status_path, runtime_path, it = sys.argv[1], sys.argv[2], sys.argv[3]
with open(status_path, "r", encoding="utf-8") as fh:
    status = json.load(fh)
with open(runtime_path, "r", encoding="utf-8") as fh:
    runtime = json.load(fh)

errors = []

if str(runtime.get("visual_runtime_mode") or "") not in {"qt_shell", "x11_display", "wayland_display", "headless"}:
    errors.append(f"bad visual_runtime_mode: {runtime.get('visual_runtime_mode')!r}")

backend = str(status.get("configured_player_backend") or "").strip().lower()
if backend != "qt":
    errors.append(f"configured_player_backend not qt: {backend!r}")
qt_module = str(status.get("qt_shell_module") or "").strip()
native_qt_modules = {"relaytv_app.qt_shell_app"}
if qt_module and qt_module not in native_qt_modules:
    errors.append(f"qt_shell_module mismatch: {qt_module!r}")
playing = bool(status.get("playing"))
transitioning = bool(status.get("transitioning_between_items"))
runtime_engine = str(status.get("player_runtime_engine") or "").strip().lower()
if playing and not transitioning:
    if runtime_engine != "qt_shell":
        errors.append(f"runtime engine not qt_shell while playing: {runtime_engine!r}")
if not playing:
    if runtime_engine not in {"qt_shell", "none"}:
        errors.append(f"unexpected runtime engine while idle: {runtime_engine!r}")
if bool(status.get("notifications_available")) is not True:
    errors.append("notifications_available is false")

if errors:
    raise SystemExit(" | ".join(errors))

print(
    f"soak iter={it} profile=native_qt ok "
    f"playing={bool(status.get('playing'))} engine={str(status.get('player_runtime_engine') or 'unknown')} "
    f"playback_state={str(status.get('playback_runtime_state') or '-')} "
    f"playback_last_failure={str(status.get('playback_runtime_last_failure_class') or '-')} "
    f"playback_last_recovery={str(status.get('playback_runtime_last_recovery_action') or '-')} "
    f"overlay_state={str(runtime.get('overlay_delivery_state') or '-')} "
    f"overlay_last_failure={str(runtime.get('overlay_delivery_last_failure_class') or '-')} "
    f"overlay_last_recovery={str(runtime.get('overlay_delivery_last_recovery_action') or '-')}"
)
PY
      then
        echo "Soak gate failed: semantic checks failed at iteration ${soak_iter}"
        emit_soak_state_summary "$status_tmp" "$runtime_tmp" "soak failure state"
        capture_soak_api_snapshots "$artifact_dir" "soak-semantic-failed-${soak_iter}" "$status_tmp" "$runtime_tmp" "$diag_snapshot_arg"
        capture_soak_host_logs "$artifact_dir" "soak-semantic-failed-${soak_iter}" "$relay_container" "$jellyfin_container"
        rm -f "$status_tmp" "$runtime_tmp"
        return 1
      fi

      if (( SECONDS + soak_poll >= soak_deadline )); then
        break
      fi
      rm -f "$status_tmp" "$runtime_tmp"
      status_tmp=""
      runtime_tmp=""
      diag_snapshot_arg=""
      sleep "$soak_poll"
    done
    echo "Soak gate passed."
    if [[ -n "${status_tmp:-}" && -n "${runtime_tmp:-}" && -f "$status_tmp" && -f "$runtime_tmp" ]]; then
      emit_soak_state_summary "$status_tmp" "$runtime_tmp" "soak pass state"
      capture_soak_api_snapshots "$artifact_dir" "soak-pass" "$status_tmp" "$runtime_tmp" "$diag_snapshot_arg"
    fi
    if [[ "$capture_logs_on_pass" == "1" ]]; then
      capture_soak_host_logs "$artifact_dir" "soak-pass" "$relay_container" "$jellyfin_container"
    fi
  }

  set +e
  if [[ -n "$output_log" ]]; then
    do_soak_run 2>&1 | tee "$output_log"
    run_rc=${PIPESTATUS[0]}
  else
    do_soak_run
    run_rc=$?
  fi
  set -e
  unset -f do_soak_run

  ended_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local soak_runtime_summary='{}'
  if [[ -n "$relay_container" ]]; then
    set +e
    soak_runtime_summary="$(build_soak_runtime_summary_json "$relay_container" 2>/dev/null)"
    local summary_rc=$?
    set -e
    if [[ "$summary_rc" -ne 0 || -z "$soak_runtime_summary" ]]; then
      soak_runtime_summary='{}'
    fi
  fi
  if [[ -n "$summary_file" ]]; then
    local pass_json
    if [[ "$run_rc" -eq 0 ]]; then
      pass_json="true"
    else
      pass_json="false"
    fi
    mkdir -p "$(dirname "$summary_file")"
    cat >"$summary_file" <<EOF
{
  "started_utc": "${started_utc}",
  "ended_utc": "${ended_utc}",
  "mode": "${mode}",
  "native_playback": "${native_playback}",
  "soak_profile": "${soak_profile}",
  "soak_sec": ${soak_sec},
  "soak_poll_sec": ${soak_poll},
  "full": ${run_full},
  "jellyfin_queue": ${run_jellyfin_queue},
  "capture_logs_on_pass": ${capture_logs_on_pass},
  "skip_up": ${skip_up},
  "output_log": "${output_log}",
  "rc": ${run_rc},
  "pass": ${pass_json},
  "runtime_state_summary": ${soak_runtime_summary}
}
EOF
    echo "soak: summary_file=${summary_file}"
  fi
  return "$run_rc"
}

do_toast_burst() {
  local count="$1"
  local duration="$2"
  local interval="$3"
  local text="$4"
  local level="$5"
  local position="$6"

  local relay_container
  relay_container="$(resolve_service_container relaytv "$RELAY_CONTAINER_DEFAULT")"
  local i payload
  for ((i=1; i<=count; i++)); do
    payload=$(printf '{"text":"%s %d/%d","duration":%s,"level":"%s","position":"%s"}' "$text" "$i" "$count" "$duration" "$level" "$position")
    docker exec "$relay_container" /bin/sh -lc \
      "curl -fsS -X POST http://127.0.0.1:8787/toast -H 'content-type: application/json' -d '$payload' >/dev/null"
    sleep "$interval"
  done
}

do_logs() {
  local target="$1"
  local since="$2"
  local tail="$3"
  local grep_re="$4"

  local relay_container jellyfin_container
  relay_container="$(resolve_service_container relaytv "$RELAY_CONTAINER_DEFAULT")"
  jellyfin_container="$(resolve_service_container jellyfin "$JELLYFIN_CONTAINER_DEFAULT")"

  local cmd_relay=(docker logs "--since=${since}" "$relay_container")
  local cmd_jelly=(docker logs "--since=${since}" "$jellyfin_container")
  if [[ -n "$tail" ]]; then
    cmd_relay=(docker logs "--tail=${tail}" "$relay_container")
    cmd_jelly=(docker logs "--tail=${tail}" "$jellyfin_container")
  fi

  run_log_cmd() {
    local label="$1"
    shift
    echo "===== ${label} ====="
    if [[ -n "$grep_re" ]]; then
      "$@" 2>&1 | grep -Ei "$grep_re" || true
    else
      "$@" 2>&1
    fi
  }

  case "$target" in
    relaytv) run_log_cmd "relaytv" "${cmd_relay[@]}" ;;
    jellyfin) run_log_cmd "jellyfin" "${cmd_jelly[@]}" ;;
    both)
      run_log_cmd "relaytv" "${cmd_relay[@]}"
      run_log_cmd "jellyfin" "${cmd_jelly[@]}"
      ;;
    *)
      echo "Unknown logs target: $target" >&2
      exit 2
      ;;
  esac
}

main() {
  if [[ $# -lt 1 ]]; then
    usage
    exit 2
  fi
  local cmd="$1"
  shift

  case "$cmd" in
    build)
      local no_cache=0
      if [[ "${1:-}" == "--no-cache" ]]; then
        no_cache=1
      fi
      run_compose_build "$no_cache"
      ;;
    up)
      local with_build=0 no_cache=0 mode="" native_playback=""
      while [[ $# -gt 0 ]]; do
        case "$1" in
          --build) with_build=1 ;;
          --no-cache) no_cache=1 ;;
          --wayland-native) mode="wayland-native" ;;
          --x11-native) mode="x11-native" ;;
          --x11-compat) warn_deprecated "--x11-compat is deprecated; use --x11-native"; mode="x11-native" ;;
          --headless) mode="headless" ;;
          --native-playback) native_playback="1" ;;
          --stable-playback) native_playback="0" ;;
          *) echo "Unknown arg for up: $1" >&2; exit 2 ;;
        esac
        shift
      done
      run_compose_up "$with_build" "$no_cache" "$mode" "$native_playback"
      ;;
    rebuild)
      local no_cache=0 mode="" native_playback=""
      while [[ $# -gt 0 ]]; do
        case "$1" in
          --no-cache) no_cache=1 ;;
          --wayland-native) mode="wayland-native" ;;
          --x11-native) mode="x11-native" ;;
          --x11-compat) warn_deprecated "--x11-compat is deprecated; use --x11-native"; mode="x11-native" ;;
          --headless) mode="headless" ;;
          --native-playback) native_playback="1" ;;
          --stable-playback) native_playback="0" ;;
          *) echo "Unknown arg for rebuild: $1" >&2; exit 2 ;;
        esac
        shift
      done
      run_compose_build "$no_cache"
      run_compose_up 0 0 "$mode" "$native_playback"
      ;;
    logs)
      local target="${1:-relaytv}"
      shift || true
      local since="5m" tail="" grep_re=""
      while [[ $# -gt 0 ]]; do
        case "$1" in
          --since) since="${2:-5m}"; shift 2 ;;
          --tail) tail="${2:-260}"; shift 2 ;;
          --grep) grep_re="${2:-}"; shift 2 ;;
          *) echo "Unknown arg for logs: $1" >&2; exit 2 ;;
        esac
      done
      do_logs "$target" "$since" "$tail" "$grep_re"
      ;;
    status)
      print_status
      ;;
    native-ready)
      local wait_sec=0
      while [[ $# -gt 0 ]]; do
        case "$1" in
          --wait) wait_sec="${2:-0}"; shift 2 ;;
          *) echo "Unknown arg for native-ready: $1" >&2; exit 2 ;;
        esac
      done
      do_native_ready "$wait_sec"
      ;;
    acceptance)
      local wait_sec=25 mode="" native_playback="" skip_up=0
      local run_youtube=1 youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"
      local check_seconds=10 connect_secs=20
      while [[ $# -gt 0 ]]; do
        case "$1" in
          --wait) wait_sec="${2:-25}"; shift 2 ;;
          --no-up) skip_up=1; shift ;;
          --wayland-native) mode="wayland-native"; shift ;;
          --x11-native) mode="x11-native"; shift ;;
          --x11-compat) warn_deprecated "--x11-compat is deprecated; use --x11-native"; mode="x11-native"; shift ;;
          --headless) mode="headless"; shift ;;
          --native-playback) native_playback="1"; shift ;;
          --stable-playback) native_playback="0"; shift ;;
          --skip-youtube) run_youtube=0; shift ;;
          --youtube-url) youtube_url="${2:-}"; shift 2 ;;
          --check-seconds) check_seconds="${2:-10}"; shift 2 ;;
          --connect-secs) connect_secs="${2:-20}"; shift 2 ;;
          *) echo "Unknown arg for acceptance: $1" >&2; exit 2 ;;
        esac
      done
      if [[ -z "$youtube_url" ]]; then
        echo "acceptance: --youtube-url requires a non-empty URL" >&2
        exit 2
      fi
      if [[ "$skip_up" == "1" && "$wait_sec" == "25" ]]; then
        wait_sec=5
      fi
      do_acceptance "$wait_sec" "$mode" "$native_playback" "$skip_up" "$run_youtube" "$youtube_url" "$check_seconds" "$connect_secs"
      ;;
    toast-burst)
      local count=10 duration=1.0 interval=1.0 text="Toast burst" level="info" position="top-right"
      while [[ $# -gt 0 ]]; do
        case "$1" in
          --count) count="${2:-10}"; shift 2 ;;
          --duration) duration="${2:-1.0}"; shift 2 ;;
          --interval) interval="${2:-1.0}"; shift 2 ;;
          --text) text="${2:-Toast burst}"; shift 2 ;;
          --level) level="${2:-info}"; shift 2 ;;
          --position) position="${2:-top-right}"; shift 2 ;;
          *) echo "Unknown arg for toast-burst: $1" >&2; exit 2 ;;
        esac
      done
      do_toast_burst "$count" "$duration" "$interval" "$text" "$level" "$position"
      ;;
    smoke)
      local no_cache=0 mode="" native_playback=""
      while [[ $# -gt 0 ]]; do
        case "$1" in
          --no-cache) no_cache=1 ;;
          --wayland-native) mode="wayland-native" ;;
          --x11-native) mode="x11-native" ;;
          --x11-compat) warn_deprecated "--x11-compat is deprecated; use --x11-native"; mode="x11-native" ;;
          --headless) mode="headless" ;;
          --native-playback) native_playback="1" ;;
          --stable-playback) native_playback="0" ;;
          *) echo "Unknown arg for smoke: $1" >&2; exit 2 ;;
        esac
        shift
      done
      run_compose_build "$no_cache"
      run_compose_up 0 0 "$mode" "$native_playback"
      do_native_ready 25
      print_status
      ;;
    soak)
      local soak_sec=180 soak_poll=5 mode="" native_playback="" run_full=0 run_jellyfin_queue=0 capture_logs_on_pass=0 skip_up=0
      local soak_profile="auto"
      local preset="short" sec_explicit=0 poll_explicit=0 capture_logs_explicit=0 artifact_dir="$ROOT_DIR/logs/relaytv-hostops-soak" report_file=""
      while [[ $# -gt 0 ]]; do
        case "$1" in
          --preset) preset="${2:-short}"; shift 2 ;;
          --sec) soak_sec="${2:-180}"; sec_explicit=1; shift 2 ;;
          --poll) soak_poll="${2:-5}"; poll_explicit=1; shift 2 ;;
          --native-qt) soak_profile="native_qt"; shift ;;
          --compat) echo "soak: --compat has been retired; use --native-qt" >&2; exit 2 ;;
          --wayland-native) mode="wayland-native"; shift ;;
          --x11-native) mode="x11-native"; shift ;;
          --x11-compat) mode="x11-native"; shift ;;
          --headless) mode="headless"; shift ;;
          --native-playback) native_playback="1"; shift ;;
          --stable-playback) native_playback="0"; shift ;;
          --full) run_full=1; shift ;;
          --jellyfin-queue) run_jellyfin_queue=1; shift ;;
          --capture-logs-on-pass) capture_logs_on_pass=1; capture_logs_explicit=1; shift ;;
          --artifact-dir) artifact_dir="${2:-$ROOT_DIR/logs/relaytv-hostops-soak}"; shift 2 ;;
          --report) report_file="${2:-}"; shift 2 ;;
          --no-up) skip_up=1; shift ;;
          *) echo "Unknown arg for soak: $1" >&2; exit 2 ;;
        esac
      done
      case "$preset" in
        short|"") ;;
        30m|half-hour|halfhour)
          if [[ "$sec_explicit" != "1" ]]; then soak_sec=1800; fi
          if [[ "$poll_explicit" != "1" ]]; then soak_poll=5; fi
          if [[ "$capture_logs_explicit" != "1" ]]; then capture_logs_on_pass=1; fi
          ;;
        overnight|8h)
          if [[ "$sec_explicit" != "1" ]]; then soak_sec=28800; fi
          if [[ "$poll_explicit" != "1" ]]; then soak_poll=15; fi
          if [[ "$capture_logs_explicit" != "1" ]]; then capture_logs_on_pass=1; fi
          ;;
        *)
          echo "Unknown soak preset: $preset (expected short|30m|overnight)" >&2
          exit 2
          ;;
      esac
      do_soak "$soak_sec" "$soak_poll" "$mode" "$native_playback" "$run_full" "$run_jellyfin_queue" "$capture_logs_on_pass" "$skip_up" "$artifact_dir" "$report_file" "$soak_profile"
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      echo "Unknown command: $cmd" >&2
      usage
      exit 2
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
