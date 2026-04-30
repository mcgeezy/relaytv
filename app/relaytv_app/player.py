# SPDX-License-Identifier: GPL-3.0-only
import os
import importlib.util
import shlex
import subprocess
import json
import socket
import signal
import threading
import tempfile
import time
import re
import platform
import shutil
import sys
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

from typing import Any, Callable
from fastapi import HTTPException

from . import state, devices, ytdlp_format_policy, video_profile
from .integrations import jellyfin_receiver
from .debug import debug_log, get_logger
from .resolver import enrich_item_metadata, is_youtube_url, make_item, provider_from_url, resolve_streams, validate_user_url, ytdlp_info


logger = get_logger("player")

_NATURAL_IDLE_RESET_UNTIL = 0.0
_NATURAL_IDLE_ENSURE_TIMER: threading.Timer | None = None


def _idle_dashboard_enabled() -> bool:
    try:
        settings = state.get_settings() if hasattr(state, "get_settings") else {}
    except Exception:
        settings = {}
    if isinstance(settings, dict) and settings.get("idle_dashboard_enabled") is False:
        return False
    raw = (os.getenv("RELAYTV_IDLE_DASHBOARD_ENABLED") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def _native_sidecar_health_snapshot(require_qt_shell: bool = False):
    """Legacy compatibility shim retained for dev-branch tests.

    The sidecar runtime has been retired from the live app. Dev-only tests may
    still monkeypatch this symbol while exercising older helper branches.
    """
    return None

# =========================
# CEC (optional)
# =========================
#
# Goals (Phase 1+2):
#  1) When playback starts, optionally power on the TV and switch to this HDMI input.
#  2) When the TV goes to standby, pause playback and persist a resume position.
#
# CEC is notoriously inconsistent across TVs, so everything here is best-effort
# and fully optional. Enable by setting RELAYTV_CEC=1 (recommended) or by passing
# cec=true in the /play request payload.

CEC_TV_ADDR = os.getenv("CEC_TV_ADDR", "0")  # logical address for TV is usually 0
CEC_ENABLED_ENV = os.getenv("RELAYTV_CEC", "0").strip()
CEC_MONITOR_ENV = os.getenv("RELAYTV_CEC_MONITOR", "").strip()  # default to RELAYTV_CEC
CEC_AUTO_ON_SWITCH_ENV = os.getenv("RELAYTV_CEC_AUTO_ON_SWITCH", "").strip()  # default to RELAYTV_CEC

_CEC_MONITOR_THREAD: threading.Thread | None = None
_CEC_MONITOR_STOP = threading.Event()
_CEC_LAST_STANDBY = 0.0
_CEC_CONTROLLER_PROC: subprocess.Popen | None = None
_CEC_CONTROLLER_LOCK = threading.Lock()
_CEC_CONTROLLER_WRITE_LOCK = threading.Lock()
_CEC_CONTROLLER_STATUS: dict[str, Any] = {
    "running": False,
    "pid": None,
    "last_error": "",
    "last_command": "",
    "last_command_ok": None,
    "last_command_ts": 0.0,
    "last_event": "",
    "last_event_ts": 0.0,
}

CEC_OPCODE_STANDBY = "36"
CEC_OPCODE_ROUTING_CHANGE = "80"
CEC_OPCODE_ROUTING_INFORMATION = "81"
CEC_OPCODE_ACTIVE_SOURCE = "82"
CEC_OPCODE_REPORT_POWER_STATUS = "90"


def _setting_enabled(name: str, default: bool) -> bool:
    settings = getattr(state, "get_settings", lambda: {})()
    v = str(settings.get(name, "")).strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _our_phys_addr() -> str | None:
    val = (os.getenv("RELAYTV_CEC_PHYS_ADDR") or "").strip().lower()
    return val or None


def _parse_cec_traffic(line: str) -> tuple[str, list[str]] | None:
    low = (line or "").strip().lower()
    if ">>" not in low:
        return None
    m = re.search(r">>\s*([0-9a-f]{2}(?::[0-9a-f]{2})+)", low)
    if not m:
        return None
    parts = [p for p in m.group(1).split(":") if p]
    if len(parts) < 2:
        return None
    return parts[1], parts[2:]


def _normalize_phys_addr(a: str, b: str) -> str:
    return f"{a}{b}00"



def cec_enabled(request_flag: bool | None = None) -> bool:
    if request_flag:
        return True
    return CEC_ENABLED_ENV in ("1", "true", "yes", "on")


def cec_auto_on_switch(request_flag: bool | None = None) -> bool:
    # If caller explicitly asked for cec, treat that as "auto on + switch".
    if request_flag:
        return True
    if CEC_AUTO_ON_SWITCH_ENV:
        return CEC_AUTO_ON_SWITCH_ENV.lower() in ("1", "true", "yes", "on")
    return cec_enabled(False)


def cec_monitor_enabled() -> bool:
    if CEC_MONITOR_ENV:
        return CEC_MONITOR_ENV.lower() in ("1", "true", "yes", "on")
    return cec_enabled(False)


def cec_available() -> bool:
    """Best-effort: return True if cec-client is runnable."""
    try:
        p = subprocess.run(["cec-client", "-l"], text=True, capture_output=True, timeout=3)
        if p.returncode != 0:
            return False
        out = (p.stdout or "") + "\n" + (p.stderr or "")
        # Output usually includes 'device:' and/or 'adapter:' lines
        return ("device:" in out.lower()) or ("adapter:" in out.lower())
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _update_cec_controller_status(**patch) -> None:
    with _CEC_CONTROLLER_LOCK:
        _CEC_CONTROLLER_STATUS.update(patch)


def cec_controller_status() -> dict[str, Any]:
    with _CEC_CONTROLLER_LOCK:
        status = dict(_CEC_CONTROLLER_STATUS)
        proc = _CEC_CONTROLLER_PROC
    if proc is not None:
        status["running"] = proc.poll() is None
        status["pid"] = proc.pid
    return status


def _cec_controller_running() -> bool:
    with _CEC_CONTROLLER_LOCK:
        proc = _CEC_CONTROLLER_PROC
    return proc is not None and proc.poll() is None and proc.stdin is not None


def _wait_for_cec_controller(timeout_sec: float = 2.0) -> bool:
    deadline = time.time() + max(0.0, float(timeout_sec))
    while time.time() < deadline:
        if _cec_controller_running():
            return True
        time.sleep(0.05)
    return _cec_controller_running()


def _cec_send_via_controller(cmds: str) -> bool:
    with _CEC_CONTROLLER_LOCK:
        proc = _CEC_CONTROLLER_PROC
    if proc is None or proc.poll() is not None or proc.stdin is None:
        return False

    normalized = cmds if cmds.endswith("\n") else f"{cmds}\n"
    with _CEC_CONTROLLER_WRITE_LOCK:
        try:
            proc.stdin.write(normalized)
            proc.stdin.flush()
            _update_cec_controller_status(
                last_command=cmds.strip(),
                last_command_ok=True,
                last_command_ts=time.time(),
                last_error="",
            )
            return True
        except Exception as exc:
            _update_cec_controller_status(
                last_command=cmds.strip(),
                last_command_ok=False,
                last_command_ts=time.time(),
                last_error=str(exc),
            )
            logger.warning("cec_controller_send_failed error=%s", exc)
            return False


def _cec_send_one_shot(cmds: str) -> None:
    try:
        p = subprocess.run(
            ["cec-client", "-s", "-d", "1"],
            input=cmds,
            text=True,
            capture_output=True,
            timeout=6,
        )
        _update_cec_controller_status(
            last_command=cmds.strip(),
            last_command_ok=(p.returncode == 0),
            last_command_ts=time.time(),
        )
        # Some adapters return non-zero even if it worked; be tolerant but log.
        if p.returncode != 0:
            logger.warning("cec_send_nonzero rc=%s", p.returncode)
            if (p.stderr or "").strip():
                logger.warning("cec_send_stderr %s", (p.stderr or "").strip())
            if (p.stdout or "").strip():
                logger.warning("cec_send_stdout %s", (p.stdout or "").strip())
    except FileNotFoundError:
        logger.info("cec_unavailable cec-client_not_installed")
        _update_cec_controller_status(last_command=cmds.strip(), last_command_ok=False, last_command_ts=time.time(), last_error="cec-client_not_installed")
    except Exception as e:
        logger.warning("cec_send_failed error=%s", e)
        _update_cec_controller_status(last_command=cmds.strip(), last_command_ok=False, last_command_ts=time.time(), last_error=str(e))


def cec_send(cmds: str) -> None:
    """Send commands to cec-client stdin. Example cmds: "on 0\nas\n"""

    if cec_monitor_enabled():
        if not _cec_controller_running():
            start_cec_monitor()
            _wait_for_cec_controller()
        if _cec_send_via_controller(cmds):
            return
    _cec_send_one_shot(cmds)


def tv_on_and_switch() -> None:
    """Power on the TV (best-effort) and switch to this source."""
    cec_send(f"on {CEC_TV_ADDR}\nas\n")


def _pause_for_tv_standby() -> None:
    """Pause mpv and persist resume position when TV goes to standby."""
    global _CEC_LAST_STANDBY
    # Debounce repeated standby broadcasts
    now = time.time()
    if now - _CEC_LAST_STANDBY < 2.0:
        return
    _CEC_LAST_STANDBY = now

    try:
        if not is_playing():
            return

        with MPV_LOCK:
            try:
                cur_paused = bool(mpv_get("pause"))
            except Exception:
                cur_paused = False

            # Capture position before pausing
            pos = None
            try:
                pos = mpv_get("time-pos")
            except Exception:
                pos = None

            try:
                mpv_set("pause", True)
            except Exception as e:
                logger.warning("cec_standby_pause_failed error=%s", e)
                return

        # Persist session info outside lock
        try:
            if pos is not None:
                state.set_session_position(float(pos))
        except Exception:
            pass

        try:
            # Store on NOW_PLAYING so resume can use it later.
            if isinstance(state.NOW_PLAYING, dict) and pos is not None:
                np = dict(state.NOW_PLAYING)
                np["resume_pos"] = float(pos)
                state.set_now_playing(np)
        except Exception:
            pass

        state.set_session_state("paused")
        state.set_pause_reason("tv_standby")
        state.update_tv_state(tv_power_status="standby", last_event="standby")
        if not cur_paused:
            logger.info("cec_tv_standby_paused position=%s", pos)
    except Exception as e:
        logger.warning("cec_standby_handler_error error=%s", e)


def _cec_monitor_loop() -> None:
    """Own cec-client for both event monitoring and command writes."""
    cmd = ["cec-client", "-d", "1"]
    logger.info("cec_controller_start cmd=%s", " ".join(cmd))
    try:
        with subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1) as proc:
            global _CEC_CONTROLLER_PROC
            with _CEC_CONTROLLER_LOCK:
                _CEC_CONTROLLER_PROC = proc
                _CEC_CONTROLLER_STATUS.update({"running": True, "pid": proc.pid, "last_error": ""})
            while not _CEC_MONITOR_STOP.is_set():
                line = proc.stdout.readline() if proc.stdout else ""
                if not line:
                    # process ended or no output
                    if proc.poll() is not None:
                        break
                    time.sleep(0.1)
                    continue

                parsed = _parse_cec_traffic(line)
                if parsed:
                    opcode, operands = parsed
                    _update_cec_controller_status(last_event=opcode, last_event_ts=time.time())
                    if opcode == CEC_OPCODE_STANDBY:
                        _pause_for_tv_standby()
                        continue

                    if opcode == CEC_OPCODE_ACTIVE_SOURCE and len(operands) >= 2:
                        phys = _normalize_phys_addr(operands[0], operands[1])
                        state.update_tv_state(active_source_phys_addr=phys, last_event="active_source")
                        ours = _our_phys_addr()
                        if _setting_enabled("tv_pause_on_input_change", True) and ours and phys != ours and is_playing():
                            with MPV_LOCK:
                                try:
                                    pos = mpv_get("time-pos")
                                except Exception:
                                    pos = None
                                try:
                                    mpv_set("pause", True)
                                    state.set_session_state("paused")
                                    state.set_pause_reason("input_changed")
                                    if pos is not None:
                                        state.set_session_position(float(pos))
                                except Exception:
                                    pass
                        if (
                            _setting_enabled("tv_auto_resume_on_return", False)
                            and ours
                            and phys == ours
                            and state.get_pause_reason() == "input_changed"
                            and is_playing()
                        ):
                            with MPV_LOCK:
                                try:
                                    mpv_set("pause", False)
                                    state.set_session_state("playing")
                                    state.set_pause_reason(None)
                                except Exception:
                                    pass
                        continue

                    if opcode in (CEC_OPCODE_ROUTING_CHANGE, CEC_OPCODE_ROUTING_INFORMATION):
                        state.update_tv_state(last_event="routing_change")
                        continue

                    if opcode == CEC_OPCODE_REPORT_POWER_STATUS and operands:
                        status_map = {"00": "on", "01": "standby", "02": "in_transition_standby_to_on", "03": "in_transition_on_to_standby"}
                        state.update_tv_state(tv_power_status=status_map.get(operands[0], "unknown"), last_event="power_status")
                        continue

                low = line.strip().lower()
                if "standby" in low and ("broadcast" in low or "received" in low or "traffic" in low):
                    _update_cec_controller_status(last_event="standby_text", last_event_ts=time.time())
                    _pause_for_tv_standby()

            try:
                proc.terminate()
            except Exception:
                pass
    except FileNotFoundError:
        logger.info("cec_controller_unavailable cec-client_not_installed")
        _update_cec_controller_status(running=False, pid=None, last_error="cec-client_not_installed")
    except Exception as e:
        logger.warning("cec_controller_crashed error=%s", e)
        _update_cec_controller_status(running=False, pid=None, last_error=str(e))
    finally:
        with _CEC_CONTROLLER_LOCK:
            if _CEC_CONTROLLER_PROC is not None and _CEC_CONTROLLER_PROC.poll() is not None:
                _CEC_CONTROLLER_PROC = None
            _CEC_CONTROLLER_STATUS.update({"running": False, "pid": None})
    logger.info("cec_controller_stopped")


def start_cec_monitor() -> None:
    """Start background CEC monitor thread if enabled and available."""
    global _CEC_MONITOR_THREAD
    if _CEC_MONITOR_THREAD and _CEC_MONITOR_THREAD.is_alive():
        return
    if not cec_monitor_enabled():
        return
    if not cec_available():
        logger.info("cec_monitor_enabled_but_client_unavailable")
        return

    _CEC_MONITOR_STOP.clear()
    t = threading.Thread(target=_cec_monitor_loop, name="cec-monitor", daemon=True)
    _CEC_MONITOR_THREAD = t
    t.start()


# =========================
# MPV + IPC
# =========================

MPV_PROC: subprocess.Popen | None = None
MPV_LOCK = threading.Lock()
IPC_PATH = os.getenv("MPV_IPC_PATH", "/tmp/mpv.sock")
SPLASH_PROC: subprocess.Popen | None = None
SPLASH_LOCK = threading.Lock()
IDLE_BROWSER_PROC: subprocess.Popen | None = None
QT_SHELL_PROC: subprocess.Popen | None = None
QT_SHELL_LOCK = threading.Lock()
_AUTO_AUDIO_DEVICE_CACHE: str | None = None
_MPV_UPNEXT_LOCK = threading.Lock()
_MPV_UPNEXT_ARMED_ID = ""
_MPV_UPNEXT_ARMED_URL = ""
_MPV_UPNEXT_ARMED_AT = 0.0
_QUEUE_PREFETCH_LOCK = threading.Lock()
_QUEUE_PREFETCH_INFLIGHT: set[str] = set()
_QUEUE_METADATA_PREFETCH_LOCK = threading.Lock()
_QUEUE_METADATA_PREFETCH_INFLIGHT: set[str] = set()
_MPV_PROP_CACHE_LOCK = threading.Lock()
_MPV_PROP_CACHE_TS = 0.0
_MPV_PROP_CACHE: dict[str, Any] = {}
_QT_EXTERNAL_RUNTIME_LOCK = threading.Lock()
_QT_EXTERNAL_RUNTIME_STATE: dict[str, object] = {
    "last_launch_ts": 0.0,
    "fallback_to_x11": False,
    "fallback_reason": "",
    "mode_args": [],
    "video_health_last_ok": None,
    "video_health_last_ts": 0.0,
    "video_health_fail_count": 0,
}


def _is_arm_arch() -> bool:
    m = (platform.machine() or "").strip().lower()
    return m in ("aarch64", "arm64", "armv7l", "armv8l")


def _mpv_ipc_retry_count() -> int:
    raw = (os.getenv("RELAYTV_MPV_IPC_RETRIES") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except Exception:
            pass
    return 2 if _is_arm_arch() else 1


def _mpv_ipc_retry_backoff_sec() -> float:
    raw = (os.getenv("RELAYTV_MPV_IPC_RETRY_BACKOFF_SEC") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except Exception:
            pass
    return 0.06 if _is_arm_arch() else 0.03


def _mpv_poll_cache_ttl_sec() -> float:
    raw = (os.getenv("RELAYTV_MPV_POLL_CACHE_SEC") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except Exception:
            pass
    return 1.0 if _is_arm_arch() else 0.08


def _mpv_poll_cache_stale_sec() -> float:
    raw = (os.getenv("RELAYTV_MPV_POLL_CACHE_STALE_SEC") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except Exception:
            pass
    return 3.0 if _is_arm_arch() else 1.2


def _mpv_poll_ipc_timeout_sec() -> float:
    raw = (os.getenv("RELAYTV_MPV_POLL_IPC_TIMEOUT_SEC") or "").strip()
    if raw:
        try:
            return max(0.1, float(raw))
        except Exception:
            pass
    return 0.35 if _is_arm_arch() else 0.6


def _mpv_cache_update(values: dict[str, Any]) -> None:
    global _MPV_PROP_CACHE_TS, _MPV_PROP_CACHE
    if not isinstance(values, dict) or not values:
        return
    now = time.time()
    with _MPV_PROP_CACHE_LOCK:
        merged = dict(_MPV_PROP_CACHE)
        for k, v in values.items():
            key = str(k or "").strip()
            if not key:
                continue
            merged[key] = v
        _MPV_PROP_CACHE = merged
        _MPV_PROP_CACHE_TS = now


def _mpv_cache_get_many(props: list[str], max_age_sec: float, project_playback: bool = False) -> dict[str, Any]:
    if not props or max_age_sec <= 0:
        return {}
    now = time.time()
    with _MPV_PROP_CACHE_LOCK:
        age = now - float(_MPV_PROP_CACHE_TS or 0.0)
        if age > max_age_sec:
            return {}
        snap = dict(_MPV_PROP_CACHE)
    out: dict[str, Any] = {}
    for p in props:
        k = str(p or "").strip()
        if not k or k not in snap:
            continue
        out[k] = snap.get(k)
    if project_playback and ("time-pos" in out):
        pos = out.get("time-pos")
        paused = snap.get("pause")
        if isinstance(pos, (int, float)) and (paused is False):
            est = float(pos) + max(0.0, float(age))
            dur = snap.get("duration")
            if isinstance(dur, (int, float)):
                est = min(est, max(0.0, float(dur)))
            out["time-pos"] = est
    return out


def _mpv_cache_get(prop: str, max_age_sec: float):
    key = str(prop or "").strip()
    if not key:
        return None
    got = _mpv_cache_get_many([key], max_age_sec=max_age_sec, project_playback=(key == "time-pos"))
    if key in got:
        return got.get(key)
    return None


def splash_enabled() -> bool:
    return (os.getenv("RELAYTV_SPLASH", "1").strip().lower() in ("1", "true", "yes", "on"))


def splash_image_path() -> str:
    explicit = (os.getenv("RELAYTV_SPLASH_IMAGE") or "").strip()
    if explicit:
        return explicit

    module_brand = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "static", "brand", "splash.png")
    )
    candidates = (
        module_brand,
        "/app/relaytv_app/static/brand/splash.png",
        "/data/assets/splash.png",
    )
    for path in candidates:
        if path and os.path.exists(path):
            return path

    # Keep stable fallback path for diagnostics when no candidate exists.
    return module_brand


def _splash_process_running() -> bool:
    global SPLASH_PROC
    return SPLASH_PROC is not None and SPLASH_PROC.poll() is None


def _splash_video_mode() -> str:
    settings = getattr(state, "get_settings", lambda: {})()
    return ((settings.get("video_mode")) or os.getenv("RELAYTV_VIDEO_MODE", "auto") or "auto").strip().lower()


def _idle_browser_process_running() -> bool:
    global IDLE_BROWSER_PROC
    return IDLE_BROWSER_PROC is not None and IDLE_BROWSER_PROC.poll() is None


def _qt_shell_backend_enabled() -> bool:
    configured = (os.getenv("RELAYTV_PLAYER_BACKEND") or "").strip().lower()
    if configured in ("qt", "qt-shell", "qtshell"):
        return True
    if configured in ("mpv", "classic"):
        return False
    # Default policy: use Qt when a display session is available.
    return _has_x11_display() or _has_wayland_display()


def qt_shell_backend_enabled() -> bool:
    return _qt_shell_backend_enabled()


def _host_session_type() -> str:
    return (
        os.getenv("RELAYTV_HOST_SESSION_TYPE")
        or os.getenv("XDG_SESSION_TYPE")
        or ""
    ).strip().lower()


def qt_runtime_mode_configured() -> str:
    """Configured Qt playback mode: auto | embed | external_mpv."""
    raw = (os.getenv("RELAYTV_QT_RUNTIME_MODE") or "auto").strip().lower()
    if raw in ("external", "external_mpv", "external-mpv", "qt_external_mpv", "qt-wayland-external"):
        return "external_mpv"
    if raw in ("embed", "embedded", "qt_shell", "qt-shell"):
        return "embed"
    return "auto"


def qt_runtime_mode_effective() -> str:
    """Effective Qt playback mode used by start_mpv."""
    configured = qt_runtime_mode_configured()
    if configured != "auto":
        return configured
    if _host_session_type() == "wayland":
        return "external_mpv"
    return "embed"


def _qt_runtime_uses_external_mpv() -> bool:
    return _qt_shell_backend_enabled() and qt_runtime_mode_effective() == "external_mpv"


def _qt_shell_running() -> bool:
    global QT_SHELL_PROC
    return QT_SHELL_PROC is not None and QT_SHELL_PROC.poll() is None


def _qt_external_mpv_running() -> bool:
    global MPV_PROC
    if MPV_PROC is None:
        return False
    try:
        if MPV_PROC.poll() is not None:
            return False
    except Exception:
        return False
    return True


def _cleanup_ipc_socket() -> None:
    """Best-effort cleanup for stale mpv IPC socket path."""
    try:
        if os.path.exists(IPC_PATH):
            os.remove(IPC_PATH)
    except Exception:
        pass


def _stop_qt_shell() -> None:
    global QT_SHELL_PROC

    def _iter_qt_runtime_pids() -> list[int]:
        out: list[int] = []
        ipc_path = (os.getenv("MPV_IPC_PATH") or IPC_PATH or "").strip()
        ipc_token = f"--input-ipc-server={ipc_path}".lower() if ipc_path else "--input-ipc-server="
        try:
            for name in os.listdir("/proc"):
                if not name.isdigit():
                    continue
                pid = int(name)
                if pid <= 1 or pid == os.getpid():
                    continue
                cmdline_path = f"/proc/{pid}/cmdline"
                try:
                    with open(cmdline_path, "rb") as fh:
                        raw = fh.read()
                except Exception:
                    continue
                if not raw:
                    continue
                text = raw.decode("utf-8", "ignore").replace("\x00", " ").strip().lower()
                is_qt_shell = "relaytv_app.qt_shell_app" in text
                is_mpv_ipc = (" mpv " in f" {text} ") and (ipc_token in text)
                if is_qt_shell or is_mpv_ipc:
                    out.append(pid)
        except Exception:
            return []
        return sorted(set(out))

    def _signal_pids(pids: list[int], sig: int) -> None:
        for pid in pids:
            try:
                os.kill(int(pid), sig)
            except Exception:
                pass

    def _alive_pids(pids: list[int]) -> list[int]:
        live: list[int] = []
        for pid in pids:
            try:
                os.kill(int(pid), 0)
                live.append(int(pid))
            except Exception:
                pass
        return live

    with QT_SHELL_LOCK:
        if _qt_shell_running():
            try:
                QT_SHELL_PROC.terminate()
                QT_SHELL_PROC.wait(timeout=3)
            except subprocess.TimeoutExpired:
                QT_SHELL_PROC.kill()
            except Exception:
                pass
        orphan_pids = _iter_qt_runtime_pids()
        if orphan_pids:
            _signal_pids(orphan_pids, signal.SIGTERM)
            deadline = time.monotonic() + 1.2
            while time.monotonic() < deadline:
                live = _alive_pids(orphan_pids)
                if not live:
                    break
                time.sleep(0.05)
            live = _alive_pids(orphan_pids)
            if live:
                _signal_pids(live, signal.SIGKILL)
        QT_SHELL_PROC = None


def _start_qt_shell(stream_url: str | None = None, audio_url: str | None = None) -> bool:
    global QT_SHELL_PROC
    settings = getattr(state, "get_settings", lambda: {})()
    shell_module = (os.getenv("RELAYTV_QT_SHELL_MODULE") or "relaytv_app.qt_shell_app").strip()
    if not shell_module:
        shell_module = "relaytv_app.qt_shell_app"
    elif importlib.util.find_spec(shell_module) is None:
        shell_module = "relaytv_app.qt_shell_app"
    overlay_url = (os.getenv("RELAYTV_QT_OVERLAY_URL") or "http://127.0.0.1:8787/x11/overlay").strip()
    try:
        parts = urlsplit(overlay_url)
        q = dict(parse_qsl(parts.query, keep_blank_values=True))
        q["ts"] = str(int(time.time() * 1000))
        overlay_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))
    except Exception:
        pass
    ipc_path = (os.getenv("MPV_IPC_PATH") or IPC_PATH).strip() or IPC_PATH
    audio_dev = _effective_audio_device(settings)
    sub_lang = (settings.get("sub_lang") or os.getenv("RELAYTV_SUB_LANG") or "").strip()
    startup_volume = _configured_start_volume()
    ytdl_enabled = _env_bool("RELAYTV_MPV_YTDL", True)
    provider_hint = _provider_hint_for_stream(stream_url or "", fallback_now_playing=True)
    if ytdl_enabled and stream_url and _should_force_ytdl_off(stream_url, provider_hint):
        ytdl_enabled = False
    ytdl_path = (os.getenv("RELAYTV_MPV_YTDL_PATH") or "yt-dlp").strip()
    ytdl_format = _effective_ytdl_format(settings, provider=provider_hint)
    ytdl_raw_options = (os.getenv("RELAYTV_MPV_YTDL_RAW_OPTIONS") or "").strip()
    args = [
        sys.executable,
        "-m",
        shell_module,
        "--overlay-url",
        overlay_url,
        "--ipc-path",
        ipc_path,
        "--volume",
        f"{startup_volume:g}",
        "--ytdl-enabled",
        ("1" if ytdl_enabled else "0"),
        "--ytdl-path",
        ytdl_path,
    ]
    stream = (stream_url or "").strip()
    if stream:
        args += ["--stream", stream]
    if ytdl_format:
        args += ["--ytdl-format", ytdl_format]
    if ytdl_raw_options:
        args += ["--ytdl-raw-options", ytdl_raw_options]
    if audio_dev:
        args += ["--audio-device", audio_dev]
    if sub_lang:
        args += ["--sub-lang", sub_lang]
    if audio_url:
        args += ["--audio", audio_url]
    if _env_bool("RELAYTV_DEBUG"):
        debug_log(
            "player",
            "qt-shell launch "
            f"module={shell_module} "
            f"stream_arg={'--stream' in args} "
            f"ipc={ipc_path} "
            f"overlay={overlay_url}",
        )

    _stop_qt_shell()
    with QT_SHELL_LOCK:
        QT_SHELL_PROC = subprocess.Popen(args)
    return False


def _qt_shell_resolve_prestop_enabled() -> bool:
    """Opt-in escape hatch for legacy x86 idle-shell pre-stop behavior."""
    raw = (os.getenv("RELAYTV_QT_RESOLVE_PRESTOP") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _qt_external_mpv_mode_args(*, fallback_to_x11: bool = False) -> list[str]:
    if fallback_to_x11:
        return ["--gpu-api=opengl", "--gpu-context=x11egl"]
    explicit = _split_env_args("RELAYTV_QT_EXTERNAL_MPV_ARGS")
    if explicit:
        return explicit
    order_raw = (os.getenv("RELAYTV_QT_EXTERNAL_RENDERER_ORDER") or "").strip().lower()
    if order_raw:
        order = [x.strip() for x in order_raw.split(",") if x.strip()]
    elif _host_session_type() == "wayland":
        order = ["wayland", "x11egl"]
    else:
        order = ["x11egl", "wayland"]
    for mode in order:
        # Prefer explicit/detected wayland display name when available; in
        # CI/test environments socket probing may be restricted.
        if mode == "wayland" and (_has_wayland_display() or bool(_wayland_display_name())):
            return _qt_external_wayland_mode_args()
        if mode in ("x11egl", "x11") and _has_x11_display():
            return ["--gpu-api=opengl", "--gpu-context=x11egl"]
    # Final fallback when display probes are unavailable in restricted envs.
    if _has_wayland_display() or _host_session_type() == "wayland":
        return _qt_external_wayland_mode_args()
    return ["--gpu-api=opengl", "--gpu-context=x11egl"]


def _qt_external_wayland_mode_args() -> list[str]:
    """Default wayland renderer profile for external mpv runtime."""
    profile = (os.getenv("RELAYTV_QT_EXTERNAL_WAYLAND_PROFILE") or "conservative").strip().lower()
    if profile in ("baseline", "simple", "legacy"):
        return ["--vo=gpu", "--gpu-context=wayland"]
    # Conservative profile is default; validated on NUC Wayland stack.
    return [
        "--vo=gpu",
        "--gpu-context=wayland",
        "--gpu-api=opengl",
        "--opengl-es=yes",
        "--fbo-format=rgba8",
    ]


def _qt_external_launch_env(mode_args: list[str]) -> dict[str, str] | None:
    if not isinstance(mode_args, list):
        return None
    low = [str(a or "").strip().lower() for a in mode_args]
    wants_wayland = any(a == "--gpu-context=wayland" for a in low)
    if not wants_wayland:
        return None
    if (os.getenv("WAYLAND_DISPLAY") or "").strip():
        return None
    display_name = _wayland_display_name()
    if not display_name:
        return None
    env = os.environ.copy()
    env["WAYLAND_DISPLAY"] = display_name
    return env


def _set_qt_external_runtime_state(
    *,
    fallback_to_x11: bool,
    mode_args: list[str],
    fallback_reason: str = "",
) -> None:
    with _QT_EXTERNAL_RUNTIME_LOCK:
        _QT_EXTERNAL_RUNTIME_STATE["last_launch_ts"] = float(time.time())
        _QT_EXTERNAL_RUNTIME_STATE["fallback_to_x11"] = bool(fallback_to_x11)
        _QT_EXTERNAL_RUNTIME_STATE["fallback_reason"] = str(fallback_reason or "").strip()
        _QT_EXTERNAL_RUNTIME_STATE["mode_args"] = list(mode_args or [])


def _set_qt_external_runtime_reason(reason: str) -> None:
    with _QT_EXTERNAL_RUNTIME_LOCK:
        _QT_EXTERNAL_RUNTIME_STATE["fallback_reason"] = str(reason or "").strip()


def _record_qt_external_video_health(ok: bool) -> None:
    with _QT_EXTERNAL_RUNTIME_LOCK:
        _QT_EXTERNAL_RUNTIME_STATE["video_health_last_ok"] = bool(ok)
        _QT_EXTERNAL_RUNTIME_STATE["video_health_last_ts"] = float(time.time())
        if not ok:
            _QT_EXTERNAL_RUNTIME_STATE["video_health_fail_count"] = int(
                _QT_EXTERNAL_RUNTIME_STATE.get("video_health_fail_count") or 0
            ) + 1


def _qt_external_video_healthy_with_grace() -> bool:
    try:
        timeout = float(os.getenv("RELAYTV_QT_EXTERNAL_HEALTH_TIMEOUT_SEC", "2.0"))
    except Exception:
        timeout = 2.0
    timeout = max(0.1, timeout)
    try:
        grace = float(os.getenv("RELAYTV_QT_EXTERNAL_HEALTH_GRACE_SEC", "1.5"))
    except Exception:
        grace = 1.5
    grace = max(0.0, grace)

    if _video_output_healthy(timeout=timeout):
        return True
    if grace <= 0:
        return False

    deadline = time.monotonic() + grace
    probe_timeout = min(0.5, max(0.1, timeout / 4.0))
    while time.monotonic() < deadline:
        if _video_output_healthy(timeout=probe_timeout):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.15, remaining))
    return False


def qt_external_runtime_state() -> dict[str, object]:
    with _QT_EXTERNAL_RUNTIME_LOCK:
        return {
            "last_launch_ts": float(_QT_EXTERNAL_RUNTIME_STATE.get("last_launch_ts") or 0.0),
            "fallback_to_x11": bool(_QT_EXTERNAL_RUNTIME_STATE.get("fallback_to_x11")),
            "fallback_reason": str(_QT_EXTERNAL_RUNTIME_STATE.get("fallback_reason") or ""),
            "mode_args": list(_QT_EXTERNAL_RUNTIME_STATE.get("mode_args") or []),
            "video_health_last_ok": _QT_EXTERNAL_RUNTIME_STATE.get("video_health_last_ok"),
            "video_health_last_ts": float(_QT_EXTERNAL_RUNTIME_STATE.get("video_health_last_ts") or 0.0),
            "video_health_fail_count": int(_QT_EXTERNAL_RUNTIME_STATE.get("video_health_fail_count") or 0),
        }


def _build_qt_external_mpv_args(
    stream_url: str,
    audio_url: str | None,
    *,
    fallback_to_x11: bool = False,
) -> list[str]:
    # Start from the standard app-managed args, then append external runtime
    # renderer hints. Keep first-wins semantics for singleton options.
    args = _build_mpv_args(stream_url, audio_url, "x11")
    base = _strip_mpv_renderer_args(args[:-1])
    extra = _qt_external_mpv_mode_args(fallback_to_x11=fallback_to_x11)
    out = base + extra + [stream_url]
    return _first_wins_dedupe(out)


def _strip_mpv_renderer_args(args: list[str]) -> list[str]:
    """Remove renderer flags so qt external mode can inject the effective backend."""
    out: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        low = str(arg or "").strip().lower()
        if low in ("--vo", "--gpu-context", "--gpu-api"):
            skip_next = True
            continue
        if low.startswith("--vo=") or low.startswith("--gpu-context=") or low.startswith("--gpu-api="):
            continue
        out.append(arg)
    return out


def _start_qt_external_mpv(
    stream_url: str,
    audio_url: str | None,
    *,
    fallback_to_x11: bool = False,
    fallback_reason: str = "",
) -> subprocess.Popen:
    _stop_qt_shell()
    args = _build_qt_external_mpv_args(stream_url, audio_url, fallback_to_x11=fallback_to_x11)
    mode_args = _qt_external_mpv_mode_args(fallback_to_x11=fallback_to_x11)
    _set_qt_external_runtime_state(
        fallback_to_x11=fallback_to_x11,
        fallback_reason=fallback_reason,
        mode_args=mode_args,
    )
    launch_env = _qt_external_launch_env(mode_args)
    if launch_env is not None:
        debug_log(
            "player",
            f"Qt external mpv using auto-detected WAYLAND_DISPLAY={launch_env.get('WAYLAND_DISPLAY')}",
        )
    debug = _env_bool("MPV_DEBUG") or _env_bool("RELAYTV_DEBUG")
    if debug:
        logger.info("starting_external_mpv args=%s", " ".join(shlex.quote(a) for a in args))
    if launch_env is not None:
        return subprocess.Popen(args, env=launch_env)
    return subprocess.Popen(args)


def ensure_qt_shell_idle(*, force: bool = False) -> None:
    """Ensure Qt shell is running in idle-overlay mode for qt backend."""
    if not _qt_shell_backend_enabled():
        return
    if not _idle_dashboard_enabled():
        return
    qpa_platform = (os.getenv("QT_QPA_PLATFORM") or "").strip().lower()
    headless_qpa = qpa_platform in ("offscreen", "vnc", "minimal")
    if not (_has_x11_display() or _has_wayland_display() or headless_qpa):
        return
    # Never relaunch idle shell during a play transition; that can race and
    # tear down an in-flight stream launch before mpv IPC is ready.
    if (not force) and playback_transitioning():
        return
    # In external-mpv runtime mode, never start an idle Qt shell while media is
    # actively playing or while a play transition is in progress.
    if _qt_runtime_uses_external_mpv():
        if _qt_external_mpv_running():
            return
    if _qt_shell_running():
        return
    _start_qt_shell(None, audio_url=None)


def _idle_browser_command() -> str | None:
    configured = (os.getenv("RELAYTV_IDLE_BROWSER") or "").strip()
    if configured:
        return configured
    for cmd in ("chromium-browser", "chromium", "google-chrome", "google-chrome-stable"):
        if shutil.which(cmd):
            return cmd
    return None


def _start_idle_browser() -> bool:
    """Start browser-backed idle dashboard when X11 is available.

    Returns True when an idle browser window is active.
    """
    global IDLE_BROWSER_PROC
    if not _has_x11_display():
        return False

    cmd = _idle_browser_command()
    if not cmd:
        return False

    url = (os.getenv("RELAYTV_IDLE_URL") or "http://127.0.0.1:8787/idle").strip()
    args = [
        cmd,
        "--no-first-run",
        "--disable-session-crashed-bubble",
        "--disable-infobars",
        "--kiosk",
        "--app=" + url,
    ]

    with SPLASH_LOCK:
        if _idle_browser_process_running():
            return True
        try:
            IDLE_BROWSER_PROC = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print("Failed to launch idle browser:", e)
            IDLE_BROWSER_PROC = None
            return False

        try:
            IDLE_BROWSER_PROC.wait(timeout=0.8)
            IDLE_BROWSER_PROC = None
            return False
        except subprocess.TimeoutExpired:
            return True


def _stop_idle_browser() -> None:
    global IDLE_BROWSER_PROC
    with SPLASH_LOCK:
        if not _idle_browser_process_running():
            IDLE_BROWSER_PROC = None
            return
        try:
            IDLE_BROWSER_PROC.terminate()
            IDLE_BROWSER_PROC.wait(timeout=3)
        except subprocess.TimeoutExpired:
            IDLE_BROWSER_PROC.kill()
        except Exception:
            pass
        IDLE_BROWSER_PROC = None


def _build_splash_args(image_path: str, mode: str) -> list[str]:
    settings = getattr(state, "get_settings", lambda: {})()
    debug = _env_bool("MPV_DEBUG") or _env_bool("RELAYTV_DEBUG")
    extra = _split_env_args("RELAYTV_SPLASH_ARGS")
    args = [
        "mpv",
        "--fs",
        "--no-audio",
        "--loop-file=inf",
        "--image-display-duration=inf",
        "--keep-open=always",
        "--force-window=yes",
        "--osc=no",
        "--input-default-bindings=no",
        "--title=RelayTV Splash",
        "--idle=yes",
        "--no-input-terminal",
        "--no-terminal",
    ]
    if debug and not _has_opt(args + extra, "--log-file"):
        args.append("--log-file=/tmp/mpv-splash.log")
    if mode == "drm":
        args += ["--vo=gpu", "--gpu-context=drm"]
        conn = (settings.get("drm_connector") or os.getenv("RELAYTV_DRM_CONNECTOR") or "").strip()
        if conn:
            args.append(f"--drm-connector={conn}")
    args += extra
    args.append(image_path)
    return _first_wins_dedupe(args)


def start_splash_screen() -> None:
    """Start idle background.

    Idle screen prefers browser dashboard when possible, with mpv image fallback.
    Playback startup explicitly stops idle first
    to avoid DRM master contention.
    """
    global SPLASH_PROC

    if not splash_enabled():
        return
    if not _idle_dashboard_enabled():
        return

    # Prefer NO splash in desktop X11 sessions by default.
    # Desktop users can set their own wallpaper/background and the overlay (if enabled)
    # provides branded notifications without forcing a fullscreen splash.
    mode = _splash_video_mode()
    x11_available = _has_x11_display()
    if x11_available and mode != "drm":
        if os.getenv("RELAYTV_SPLASH_X11", "0").strip().lower() not in ("1","true","yes","on"):
            return

    # If the X11 overlay is enabled, do not start the mpv splash (avoid extra window/process).
    try:
        from .x11_overlay import overlay_enabled as _x11_overlay_enabled  # local import to avoid cycles
        if x11_available and _x11_overlay_enabled():
            return
    except Exception:
        pass

    # Browser idle dashboard is preferred for desktop/X11 sessions.
    if mode != "drm" and _start_idle_browser():
        return

    image_path = splash_image_path()
    if not image_path or not os.path.exists(image_path):
        logger.warning("splash_image_not_found path=%s", image_path)
        return


    with SPLASH_LOCK:
        if _splash_process_running():
            return

        try_modes: list[str]
        if mode in ("x11", "drm"):
            try_modes = [mode]
        else:
            try_modes = ["drm"]
            if x11_available:
                try_modes.append("x11")

        for m in try_modes:
            try:
                SPLASH_PROC = subprocess.Popen(_build_splash_args(image_path, m))
            except Exception as e:
                logger.warning("splash_start_failed mode=%s error=%s", m, e)
                SPLASH_PROC = None
                continue

            try:
                SPLASH_PROC.wait(timeout=0.6)
                # Exited quickly -> try next mode
                SPLASH_PROC = None
                continue
            except subprocess.TimeoutExpired:
                return


def stop_splash_screen() -> None:
    global SPLASH_PROC
    _stop_idle_browser()
    with SPLASH_LOCK:
        if not _splash_process_running():
            SPLASH_PROC = None
            return

        try:
            SPLASH_PROC.terminate()
            SPLASH_PROC.wait(timeout=3)
        except subprocess.TimeoutExpired:
            SPLASH_PROC.kill()
        except Exception:
            pass
        SPLASH_PROC = None

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _split_env_args(name: str) -> list[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw)
    except ValueError as e:
        logger.warning("env_arg_parse_error name=%s error=%s raw=%r", name, e, raw)
        return []

def _has_opt(args: list[str], opt: str) -> bool:
    return any(a == opt or a.startswith(opt + "=") for a in args)


def _first_wins_dedupe(args: list[str]) -> list[str]:
    """Drop duplicate singleton mpv options; keep the first value.

    This prevents stale/conflicting MPV_ARGS from overriding app-selected safe
    defaults (e.g. switching hwdec from v4l2m2m-copy to unsupported v4l2m2m).
    """
    singletons = (
        "--vo",
        "--ao",
        "--gpu-context",
        "--drm-device",
        "--drm-connector",
        "--hwdec",
        "--hwdec-codecs",
        "--video-sync",
        "--framedrop",
        "--cache",
        "--cache-secs",
        "--demuxer-readahead-secs",
        "--msg-level",
        "--osd-level",
        "--osd-playing-msg",
        "--term-playing-msg",
        "--ytdl-format",
    )
    seen: set[str] = set()
    out: list[str] = []
    for a in args:
        key = None
        for p in singletons:
            if a == p or a.startswith(p + "="):
                key = p
                break
        if key is None:
            out.append(a)
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def _effective_ytdl_format(settings: dict | None = None, provider: str | None = None) -> str:
    """Resolve effective ytdl format from settings + runtime video profile."""
    s = settings if isinstance(settings, dict) else getattr(state, "get_settings", lambda: {})()
    prov = str(provider or "").strip().lower()
    if not prov:
        now = state.get_now_playing() if hasattr(state, "get_now_playing") else {}
        prov = str((now or {}).get("provider") or "other").strip().lower() or "other"
    try:
        profile = dict(video_profile.get_profile() or {})
    except Exception:
        profile = {}
    return ytdlp_format_policy.effective_ytdlp_format(s, provider=prov, profile=profile)


def _provider_hint_for_stream(stream_url: str, fallback_now_playing: bool = True) -> str:
    prov = provider_from_url(stream_url or "")
    if prov == "other":
        try:
            host = (urlparse(stream_url or "").netloc or "").lower()
            if any(x in host for x in ("tiktokcdn", "byteoversea", "byteimg", "tiktokv", "muscdn")):
                prov = "tiktok"
        except Exception:
            pass
    if prov and prov != "other":
        return prov
    if fallback_now_playing:
        now = state.get_now_playing() if hasattr(state, "get_now_playing") else {}
        now_prov = str((now or {}).get("provider") or "").strip().lower()
        if now_prov:
            return now_prov
    return prov or "other"


def _signed_direct_url(url: str) -> bool:
    try:
        p = urlparse(url or "")
        host = (p.netloc or "").lower()
        path = (p.path or "").lower()
        q = parse_qs(p.query or "")
    except Exception:
        return False
    if not host:
        return False
    if not (path.endswith(".mp4") or path.endswith(".m3u8") or "/stream" in path or "/video" in path):
        return False
    # Common signed URL query markers used by CDN direct links.
    signed_markers = (
        "sig",
        "signature",
        "x-signature",
        "expires",
        "expire",
        "x-expires",
        "token",
        "auth",
    )
    keys = {k.lower() for k in q.keys()}
    return any(k in keys for k in signed_markers)


def _should_force_ytdl_off(stream_url: str, provider_hint: str | None = None) -> bool:
    prov = str(provider_hint or "").strip().lower() or _provider_hint_for_stream(stream_url, fallback_now_playing=False)
    if prov != "tiktok":
        return False
    return _signed_direct_url(stream_url)


_QT_RUNTIME_TELEMETRY_CONTRACT_VERSION = "v1"


def _qt_shell_runtime_status_file() -> str:
    return (os.getenv("RELAYTV_QT_RUNTIME_STATUS_FILE") or "/tmp/relaytv-qt-runtime.json").strip()


def _qt_shell_runtime_control_file() -> str:
    return (os.getenv("RELAYTV_QT_RUNTIME_CONTROL_FILE") or "/tmp/relaytv-qt-runtime-control.json").strip()


def _qt_shell_runtime_read() -> tuple[dict[str, Any] | None, float | None, str]:
    shell_module = (os.getenv("RELAYTV_QT_SHELL_MODULE") or "relaytv_app.qt_shell_app").strip()
    if shell_module != "relaytv_app.qt_shell_app":
        return None, None, "disabled"
    path = _qt_shell_runtime_status_file()
    if not path:
        return None, None, "missing"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None, None, "missing"
    except Exception:
        return None, None, "invalid"
    if not isinstance(data, dict):
        return None, None, "invalid"
    ts = data.get("ts")
    try:
        age = max(0.0, time.time() - float(ts))
    except Exception:
        age = None
    return data, age, "ok"


def _qt_shell_runtime_snapshot(*, max_age_sec: float = 3.0) -> dict[str, Any] | None:
    data, age, state = _qt_shell_runtime_read()
    if state != "ok" or data is None:
        return None
    if age is None or age > max(0.2, float(max_age_sec)):
        return None
    if (data.get("alive") is False) and age > 0.5:
        return None
    return data


def _qt_shell_fd_diagnostics(pid: object) -> dict[str, Any]:
    try:
        pid_val = int(pid or 0)
    except Exception:
        pid_val = 0
    limit_raw = (os.getenv("RELAYTV_QT_SHELL_FD_LIMIT") or "8192").strip()
    warn_raw = (os.getenv("RELAYTV_QT_SHELL_FD_WARN_THRESHOLD") or "4096").strip()
    critical_raw = (os.getenv("RELAYTV_QT_SHELL_FD_CRITICAL_THRESHOLD") or "7168").strip()
    try:
        fd_limit = max(1, int(float(limit_raw)))
    except Exception:
        fd_limit = 8192
    try:
        warn_threshold = max(1, int(float(warn_raw)))
    except Exception:
        warn_threshold = min(fd_limit, 4096)
    try:
        critical_threshold = max(warn_threshold, int(float(critical_raw)))
    except Exception:
        critical_threshold = min(fd_limit, 7168)
    out = {
        "qt_shell_fd_count": None,
        "qt_shell_fd_limit": fd_limit,
        "qt_shell_fd_warn_threshold": warn_threshold,
        "qt_shell_fd_critical_threshold": critical_threshold,
        "qt_shell_fd_headroom": None,
        "qt_shell_fd_pressure_pct": None,
        "qt_shell_fd_warning": False,
        "qt_shell_fd_warning_level": "unknown",
    }
    if pid_val <= 1:
        return out
    try:
        fd_count = len(os.listdir(f"/proc/{pid_val}/fd"))
    except Exception:
        return out
    out["qt_shell_fd_count"] = fd_count
    out["qt_shell_fd_headroom"] = max(0, fd_limit - fd_count)
    try:
        out["qt_shell_fd_pressure_pct"] = round((float(fd_count) / float(fd_limit)) * 100.0, 1)
    except Exception:
        out["qt_shell_fd_pressure_pct"] = None
    if fd_count >= critical_threshold:
        out["qt_shell_fd_warning"] = True
        out["qt_shell_fd_warning_level"] = "critical"
    elif fd_count >= warn_threshold:
        out["qt_shell_fd_warning"] = True
        out["qt_shell_fd_warning_level"] = "warn"
    else:
        out["qt_shell_fd_warning_level"] = "ok"
    return out


def qt_shell_runtime_telemetry(*, max_age_sec: float = 3.0) -> dict[str, Any]:
    data, age, state = _qt_shell_runtime_read()
    freshness = state
    available = False
    if state == "ok" and data is not None:
        freshness = "fresh"
        if age is None or age > max(0.2, float(max_age_sec)):
            freshness = "stale"
        elif (data.get("alive") is False) and (age or 0.0) > 0.5:
            freshness = "stale"
        available = freshness == "fresh"
    fd_diag = _qt_shell_fd_diagnostics((data or {}).get("qt_shell_pid"))
    return {
        "contract_version": _QT_RUNTIME_TELEMETRY_CONTRACT_VERSION,
        "path": _qt_shell_runtime_status_file(),
        "source": "qt_runtime" if available else "none",
        "selected": bool(_qt_shell_runtime_preferred()) if state != "disabled" else False,
        "available": available,
        "freshness": freshness,
        "age_sec": age,
        "runtime": str((data or {}).get("runtime") or "qt_shell_native"),
        "alive": bool((data or {}).get("alive")) if data is not None else False,
        "qt_shell_pid": (data or {}).get("qt_shell_pid"),
        "control_file": str((data or {}).get("control_file") or ""),
        "last_control_action": str((data or {}).get("last_control_action") or ""),
        "last_control_request_id": str((data or {}).get("last_control_request_id") or ""),
        "last_control_handled": (data or {}).get("last_control_handled"),
        "last_control_ok": (data or {}).get("last_control_ok"),
        "last_control_error": str((data or {}).get("last_control_error") or ""),
        "mpv_runtime_initialized": (data or {}).get("mpv_runtime_initialized"),
        "mpv_runtime_playback_active": (data or {}).get("mpv_runtime_playback_active"),
        "mpv_runtime_stream_loaded": (data or {}).get("mpv_runtime_stream_loaded"),
        "mpv_runtime_playback_started": (data or {}).get("mpv_runtime_playback_started"),
        "mpv_runtime_paused": (data or {}).get("mpv_runtime_paused"),
        "mpv_runtime_time_pos": (data or {}).get("mpv_runtime_time_pos"),
        "mpv_runtime_duration": (data or {}).get("mpv_runtime_duration"),
        "mpv_runtime_volume": (data or {}).get("mpv_runtime_volume"),
        "mpv_runtime_mute": (data or {}).get("mpv_runtime_mute"),
        "mpv_runtime_path": str((data or {}).get("mpv_runtime_path") or ""),
        "mpv_runtime_current_vo": str((data or {}).get("mpv_runtime_current_vo") or ""),
        "mpv_runtime_current_ao": str((data or {}).get("mpv_runtime_current_ao") or ""),
        "mpv_runtime_aid": (data or {}).get("mpv_runtime_aid"),
        "mpv_runtime_playlist_pos": (data or {}).get("mpv_runtime_playlist_pos"),
        "mpv_runtime_playlist_count": (data or {}).get("mpv_runtime_playlist_count"),
        "mpv_runtime_track_list": (data or {}).get("mpv_runtime_track_list"),
        "mpv_runtime_sample_detail": str((data or {}).get("mpv_runtime_sample_detail") or ""),
        **fd_diag,
    }


def _qt_shell_runtime_mpv_property(prop: str):
    snap = _qt_shell_runtime_snapshot()
    if snap is None:
        return None
    key = str(prop or "").strip().lower()
    mapping = {
        "pause": "mpv_runtime_paused",
        "time-pos": "mpv_runtime_time_pos",
        "duration": "mpv_runtime_duration",
        "path": "mpv_runtime_path",
        "volume": "mpv_runtime_volume",
        "mute": "mpv_runtime_mute",
        "core-idle": "mpv_runtime_core_idle",
        "eof-reached": "mpv_runtime_eof_reached",
        "playlist-pos": "mpv_runtime_playlist_pos",
        "playlist-count": "mpv_runtime_playlist_count",
        "track-list": "mpv_runtime_track_list",
    }
    field = mapping.get(key)
    if not field:
        return None
    return snap.get(field)


def _qt_shell_runtime_supports_mpv_property(prop: str) -> bool:
    key = str(prop or "").strip().lower()
    return key in {
        "pause",
        "time-pos",
        "duration",
        "path",
        "volume",
        "mute",
        "core-idle",
        "eof-reached",
        "playlist-pos",
        "playlist-count",
        "track-list",
    }


def _qt_shell_runtime_startup_ready(*, max_age_sec: float = 1.0) -> bool:
    if not _qt_shell_backend_enabled() or _qt_runtime_uses_external_mpv():
        return False
    if not _qt_shell_running():
        return False
    telemetry = qt_shell_runtime_telemetry(max_age_sec=max_age_sec)
    if telemetry.get("source") != "qt_runtime":
        return False
    if telemetry.get("alive") is not True:
        return False
    control_file = str(telemetry.get("control_file") or "").strip()
    if control_file:
        return True
    if telemetry.get("mpv_runtime_initialized") is True:
        return True
    if any(
        telemetry.get(key) is True
        for key in (
            "mpv_runtime_playback_active",
            "mpv_runtime_stream_loaded",
            "mpv_runtime_playback_started",
        )
    ):
        return True
    return bool(str(telemetry.get("mpv_runtime_path") or "").strip())


def _qt_shell_runtime_output_state(*, max_age_sec: float = 1.0) -> dict[str, Any] | None:
    if not _qt_shell_runtime_preferred() or _qt_runtime_uses_external_mpv():
        return None
    snap = _qt_shell_runtime_snapshot(max_age_sec=max_age_sec)
    if not isinstance(snap, dict):
        return None
    return {
        "path": str(snap.get("mpv_runtime_path") or "").strip(),
        "current_vo": str(snap.get("mpv_runtime_current_vo") or "").strip(),
        "current_ao": str(snap.get("mpv_runtime_current_ao") or "").strip(),
        "aid": snap.get("mpv_runtime_aid"),
        "playback_active": snap.get("mpv_runtime_playback_active") is True,
        "stream_loaded": snap.get("mpv_runtime_stream_loaded") is True,
        "playback_started": snap.get("mpv_runtime_playback_started") is True,
        "core_idle": snap.get("mpv_runtime_core_idle"),
        "eof_reached": snap.get("mpv_runtime_eof_reached"),
        "sample_detail": str(snap.get("mpv_runtime_sample_detail") or "").strip().lower(),
    }


def _host_runtime_mpv_property(prop: str):
    return _qt_shell_runtime_mpv_property(prop)


def _host_runtime_mpv_properties(props: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for prop in list(props or []):
        key = str(prop or "").strip()
        if not key:
            continue
        out[key] = _host_runtime_mpv_property(key)
    return out



def _qt_shell_runtime_preferred() -> bool:
    return _qt_shell_runtime_snapshot() is not None


def _qt_shell_runtime_requires_live_mpv_ipc(props: list[str] | None = None) -> bool:
    snap = _qt_shell_runtime_snapshot(max_age_sec=10.0)
    if not isinstance(snap, dict):
        return False
    sample_detail = str(snap.get("mpv_runtime_sample_detail") or "").strip().lower()
    if not sample_detail.startswith("subprocess_runtime"):
        return False
    if not props:
        return True
    live_props = {
        "pause",
        "time-pos",
        "duration",
        "path",
        "volume",
        "mute",
        "core-idle",
        "eof-reached",
        "playlist-pos",
        "playlist-count",
        "track-list",
    }
    for prop in list(props or []):
        key = str(prop or "").strip().lower()
        if key in live_props:
            return True
    return False


def _qt_shell_runtime_accepts_mpv_commands() -> bool:
    snap = _qt_shell_runtime_snapshot(max_age_sec=10.0)
    if isinstance(snap, dict):
        control_file = str(snap.get("control_file") or "").strip()
        if control_file:
            return True
    # A freshly started idle shell can accept runtime control before the first
    # heartbeat publishes a fresh snapshot. Prefer that control path over a
    # full shell restart when the embedded Qt shell is already alive.
    if _qt_shell_backend_enabled() and (not _qt_runtime_uses_external_mpv()) and _qt_shell_running():
        return bool(str(_qt_shell_runtime_control_file() or "").strip())
    return False


def _qt_shell_runtime_write_control(payload: dict[str, Any]) -> dict[str, Any]:
    path = ""
    snap = _qt_shell_runtime_snapshot(max_age_sec=10.0)
    if isinstance(snap, dict):
        path = str(snap.get("control_file") or "").strip()
    if not path:
        path = _qt_shell_runtime_control_file()
    if not path:
        raise HTTPException(status_code=409, detail="qt shell runtime control unavailable")
    control_payload = dict(payload or {})
    request_id = str(control_payload.get("request_id") or "").strip()
    if not request_id:
        request_id = f"qtctl-{time.time_ns()}"
        control_payload["request_id"] = request_id
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".tmp.", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(control_payload, fh, ensure_ascii=True, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass
    return {"error": "success", "request_id": request_id}


def _qt_shell_runtime_load_stream(stream_url: str, audio_url: str | None = None) -> dict[str, Any]:
    stream = str(stream_url or "").strip()
    if not stream:
        return {"error": "invalid_command"}
    payload: dict[str, Any] = {
        "action": "load_stream",
        "stream": stream,
        "ts": time.time(),
    }
    audio = str(audio_url or "").strip() if isinstance(audio_url, str) else ""
    if audio:
        payload["audio"] = audio
    return _qt_shell_runtime_finalize_control_result(
        _qt_shell_runtime_write_control(payload),
        timeout_sec=max(1.5, _qt_shell_runtime_control_wait_sec()),
    )


def qt_shell_runtime_overlay_toast(
    *,
    text: str,
    duration: float = 4.0,
    level: str = "info",
    icon: str | None = None,
    image_url: str | None = None,
    link_url: str | None = None,
    link_text: str | None = None,
    position: str = "top-left",
    style: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "overlay_toast",
        "text": str(text or ""),
        "duration": max(0.8, float(duration or 4.0)),
        "level": str(level or "info"),
        "icon": icon,
        "position": str(position or "top-left"),
        "ts": time.time(),
    }
    if image_url:
        payload["image_url"] = str(image_url)
    if link_url:
        payload["link_url"] = str(link_url)
    if link_text:
        payload["link_text"] = str(link_text)
    if isinstance(style, dict) and style:
        payload["style"] = dict(style)
    return _qt_shell_runtime_finalize_control_result(
        _qt_shell_runtime_write_control(payload),
        timeout_sec=max(1.0, _qt_shell_runtime_control_wait_sec()),
    )


def _qt_shell_runtime_command(cmd_list: list):
    cmd = list(cmd_list or [])
    if not cmd:
        return {"error": "invalid_command"}
    command_name = str(cmd[0] or "").strip().lower()
    if command_name == "get_property" and len(cmd) >= 2:
        return {"error": "success", "data": _qt_shell_runtime_mpv_property(str(cmd[1] or ""))}
    if command_name == "set_property" and len(cmd) >= 3:
        return _qt_shell_runtime_write_control(
            {
                "action": "set_property",
                "name": str(cmd[1] or ""),
                "value": cmd[2],
                "ts": time.time(),
            }
        )
    if command_name == "loadfile" and len(cmd) >= 4 and str(cmd[2] or "").strip().lower() == "replace":
        stream = str(cmd[1] or "").strip()
        option_arg = ""
        if len(cmd) >= 5 and str(cmd[3] or "").strip() == "-1":
            option_arg = str(cmd[4] or "").strip()
        else:
            option_arg = str(cmd[3] or "").strip()
        audio = ""
        if stream and option_arg.startswith("audio-file="):
            audio = option_arg.split("=", 1)[1]
        elif stream and option_arg.startswith("audio-files-append="):
            audio = option_arg.split("=", 1)[1]
        if stream and audio:
            return _qt_shell_runtime_write_control(
                {
                    "action": "load_stream",
                    "stream": stream,
                    "audio": audio,
                    "ts": time.time(),
                }
            )
    return _qt_shell_runtime_write_control(
        {
            "action": "command",
            "command": cmd,
            "ts": time.time(),
        }
    )


def _qt_shell_runtime_control_wait_sec() -> float:
    raw = (os.getenv("RELAYTV_QT_RUNTIME_CONTROL_WAIT_SEC") or "").strip()
    if raw:
        try:
            return max(0.0, min(float(raw), 5.0))
        except Exception:
            pass
    return 1.2


def _qt_shell_runtime_seek_wait_sec() -> float:
    raw = (os.getenv("RELAYTV_QT_RUNTIME_SEEK_WAIT_SEC") or "").strip()
    if raw:
        try:
            return max(_qt_shell_runtime_control_wait_sec(), min(float(raw), 8.0))
        except Exception:
            pass
    return max(3.5, _qt_shell_runtime_control_wait_sec())


def _qt_shell_runtime_pause_wait_sec() -> float:
    raw = (os.getenv("RELAYTV_QT_RUNTIME_PAUSE_WAIT_SEC") or "").strip()
    if raw:
        try:
            return max(0.0, min(float(raw), 2.0))
        except Exception:
            pass
    return min(0.3, _qt_shell_runtime_control_wait_sec())


def _qt_shell_runtime_pause_timeout_tolerable(result: dict[str, Any]) -> bool:
    if not isinstance(result, dict):
        return False
    if str(result.get("error") or "").strip().lower() != "timeout_or_unavailable":
        return False
    try:
        telemetry = qt_shell_runtime_telemetry(max_age_sec=3.0)
    except Exception:
        telemetry = {}
    if isinstance(telemetry, dict) and bool(telemetry.get("alive")):
        return True
    return _qt_shell_running()

def _qt_shell_runtime_wait_for_ack(request_id: str, *, timeout_sec: float | None = None) -> dict[str, Any]:
    rid = str(request_id or "").strip()
    if not rid:
        return {"ok": False, "observed": False, "reason": "missing_request_id", "request_id": "", "telemetry": {}}
    wait_sec = _qt_shell_runtime_control_wait_sec() if timeout_sec is None else max(0.0, float(timeout_sec))
    deadline = time.time() + wait_sec
    telemetry: dict[str, Any] = {}
    while True:
        telemetry = qt_shell_runtime_telemetry(max_age_sec=max(wait_sec + 1.0, 3.0))
        if str(telemetry.get("last_control_request_id") or "") == rid and telemetry.get("last_control_handled") is not None:
            ok = telemetry.get("last_control_ok")
            if ok is False:
                return {"ok": False, "observed": True, "reason": "control_failed", "request_id": rid, "telemetry": telemetry}
            return {"ok": True, "observed": True, "reason": "control_acknowledged", "request_id": rid, "telemetry": telemetry}
        if time.time() >= deadline:
            return {"ok": False, "observed": False, "reason": "timeout_or_unavailable", "request_id": rid, "telemetry": telemetry}
        time.sleep(0.05)

def _qt_shell_runtime_finalize_control_result(result: dict[str, Any], *, timeout_sec: float | None = None) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("error") != "success":
        return result
    request_id = str(result.get("request_id") or "").strip()
    if not request_id:
        return result
    ack = _qt_shell_runtime_wait_for_ack(request_id, timeout_sec=timeout_sec)
    if ack.get("ok") is True:
        out = dict(result)
        out.update(
            {
                "ack_observed": True,
                "ack_reason": str(ack.get("reason") or "control_acknowledged"),
                "request_id": request_id,
            }
        )
        return out
    telemetry = dict(ack.get("telemetry") or {})
    return {
        "error": str(ack.get("reason") or "control_failed"),
        "request_id": request_id,
        "ack_observed": bool(ack.get("observed")),
        "ack_reason": str(ack.get("reason") or "control_failed"),
        "detail": str(telemetry.get("last_control_error") or "qt runtime control ack unavailable"),
    }


def _qt_runtime_active(*, require_active_session: bool = True) -> bool:
    """Best-effort native Qt runtime activity check when mpv IPC is unavailable."""
    if not _qt_shell_running():
        return False
    snap = _qt_shell_runtime_snapshot(max_age_sec=3.0)
    if not isinstance(snap, dict):
        return False

    path = str(snap.get("mpv_runtime_path") or "").strip()
    playback_active = snap.get("mpv_runtime_playback_active")
    stream_load_attempted = snap.get("mpv_runtime_stream_load_attempted")
    stream_loaded = snap.get("mpv_runtime_stream_loaded")
    playback_started = snap.get("mpv_runtime_playback_started")
    runtime_paused = snap.get("mpv_runtime_paused")
    core_idle = snap.get("mpv_runtime_core_idle")
    eof_reached = snap.get("mpv_runtime_eof_reached")
    mpv_runtime_error = str(snap.get("mpv_runtime_error") or "").strip().lower()
    sample_detail = str(snap.get("mpv_runtime_sample_detail") or "").strip().lower()
    native_visible = bool(path)
    paused_loaded = (
        runtime_paused is True
        and (stream_loaded is True or bool(path))
        and eof_reached is not True
    )
    if isinstance(playback_active, bool):
        native_visible = playback_active
        if (
            (not native_visible)
            and (stream_loaded is True or playback_started is True)
            and (core_idle is not True)
        ):
            # Keep visual runtime considered active while stream is loaded and
            # not explicitly idle, even if heartbeat lags transiently.
            native_visible = True
    if (not native_visible) and paused_loaded:
        native_visible = True
    pending_start = (
        (stream_load_attempted is True)
        and (eof_reached is not True)
        and (core_idle is not True)
        and not mpv_runtime_error.startswith("loadfile_failed")
        and not sample_detail.startswith("loadfile_failed")
        and not sample_detail.startswith("stream_load_skipped")
    )
    if (not native_visible) and pending_start:
        native_visible = True
    playback_ended = (eof_reached is True) and (core_idle is True)
    telemetry_active = bool(
        (
            playback_started is True
            or paused_loaded
            or pending_start
            or (stream_loaded is True and core_idle is not True)
            or (native_visible and core_idle is not True)
        )
        and (not playback_ended)
    )

    global _QT_RUNTIME_ACTIVE_LAST_TS
    now_ts = time.time()
    if native_visible:
        _QT_RUNTIME_ACTIVE_LAST_TS = now_ts

    if not require_active_session:
        return native_visible or telemetry_active

    sess = str(getattr(state, "SESSION_STATE", "") or "").strip().lower()
    has_now = isinstance(getattr(state, "NOW_PLAYING", None), dict)
    if not (sess in ("playing", "paused") or has_now or playback_transitioning()):
        # Self-heal after transient state clears: if native telemetry says media is
        # active, keep the runtime marked playing and let /status repair session.
        return telemetry_active
    if native_visible or telemetry_active:
        return True

    # Native health can momentarily flap delegate/native during launch; keep
    # session active briefly when an item is loaded to avoid false idle clears.
    try:
        grace = float(
            os.getenv("RELAYTV_QT_RUNTIME_PLAYING_GRACE_SEC")
            or os.getenv("RELAYTV_SHELL_V2_NATIVE_PLAYING_GRACE_SEC")
            or "3.0"
        )
    except Exception:
        grace = 3.0
    grace = max(0.0, min(30.0, grace))
    if (
        has_now
        and (not playback_ended)
        and (now_ts - float(_QT_RUNTIME_ACTIVE_LAST_TS or 0.0)) <= grace
    ):
        return True
    return False


def _is_playing() -> bool:
    """Best-effort check whether the active playback backend is running."""
    if _qt_shell_backend_enabled():
        if _qt_runtime_uses_external_mpv():
            return _qt_external_mpv_running()
        # Prevent startup races from collapsing active playback back to idle.
        if _qt_shell_running() and playback_transitioning():
            return True
        # In Qt backend, an idle shell may be running without active media.
        # Prefer core-idle/eof probes over raw socket existence to avoid
        # misclassifying a black idle surface as active playback.
        if _qt_shell_running() and os.path.exists(IPC_PATH):
            try:
                props = mpv_get_many(["core-idle", "eof-reached", "path"])
                core_idle = props.get("core-idle")
                if core_idle is True:
                    return _qt_runtime_active(require_active_session=True)
            except Exception:
                pass
            return True
        return _qt_runtime_active(require_active_session=True)

    global MPV_PROC
    if MPV_PROC is None:
        return False
    try:
        if MPV_PROC.poll() is not None:
            return False
    except Exception:
        return False
    return True


def is_playing() -> bool:
    """Public wrapper used by routes/UI."""
    return _is_playing()


def _persist_runtime_volume_before_stop() -> None:
    """Best-effort persist of the live runtime volume before shell teardown."""
    try:
        volume = mpv_get("volume")
    except Exception:
        return
    try:
        if volume is None:
            return
        value = max(0.0, min(200.0, float(volume)))
    except Exception:
        return
    try:
        state.update_settings({"volume": value})
    except Exception:
        pass


def stop_mpv(*, restart_splash: bool = True):
    global MPV_PROC
    _persist_runtime_volume_before_stop()
    _stop_qt_shell()
    _reset_mpv_up_next_state()
    if MPV_PROC and MPV_PROC.poll() is None:
        MPV_PROC.terminate()
        try:
            MPV_PROC.wait(timeout=3)
        except subprocess.TimeoutExpired:
            MPV_PROC.kill()
    MPV_PROC = None
    _cleanup_ipc_socket()

    if restart_splash and _qt_shell_backend_enabled():
        ensure_qt_shell_idle(force=True)
    elif restart_splash:
        start_splash_screen()


def _has_x11_display() -> bool:
    """Best-effort check for an X11 display inside the container."""
    disp = (os.getenv("DISPLAY") or "").strip()
    if not disp:
        return False

    # Local X11 display endpoints should have a matching unix socket.
    # For TCP/SSH forwarded displays keep permissive detection.
    raw = disp
    if raw.startswith("unix/"):
        raw = raw[len("unix/") :]
    if raw.startswith(":"):
        num = raw[1:].split(".", 1)[0].strip()
        if num.isdigit():
            return os.path.exists(f"/tmp/.X11-unix/X{num}")
    return True


def _wayland_display_name() -> str:
    explicit = (os.getenv("WAYLAND_DISPLAY") or "").strip()
    if explicit:
        return explicit
    runtime_dir = (os.getenv("XDG_RUNTIME_DIR") or "").strip()
    if not runtime_dir:
        return ""
    try:
        names = sorted(os.listdir(runtime_dir))
    except Exception:
        return ""
    for name in names:
        if not str(name).startswith("wayland-"):
            continue
        try:
            path = os.path.join(runtime_dir, str(name))
            if os.path.exists(path):
                return str(name)
        except Exception:
            continue
    return ""


def _has_wayland_display() -> bool:
    name = _wayland_display_name()
    if not name:
        return False
    if os.path.isabs(name):
        return os.path.exists(name)
    runtime_dir = (os.getenv("XDG_RUNTIME_DIR") or "").strip()
    if runtime_dir and os.path.exists(os.path.join(runtime_dir, name)):
        return True
    # Keep permissive fallback for CI/tests/container sessions where env is
    # explicitly set but runtime socket probing is restricted.
    if (os.getenv("WAYLAND_DISPLAY") or "").strip():
        return True
    return _host_session_type() == "wayland"


def _overlay_osd_debug_enabled() -> bool:
    v = (os.getenv("RELAYTV_OVERLAY_OSD_DEBUG") or os.getenv("OVERLAY_OSD_DEBUG") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _x11_overlay_enabled() -> bool:
    return (os.getenv("RELAYTV_X11_OVERLAY") or "0").strip().lower() in ("1", "true", "yes", "on")


def _x11_mode_active(selected_mode: str | None = None) -> bool:
    """Return True when RelayTV should behave as X11 for notifications/OSD."""
    mode = (selected_mode or "").strip().lower()
    if not mode:
        mode = (
            (getattr(state, "get_settings", lambda: {})().get("video_mode"))
            or os.getenv("RELAYTV_VIDEO_MODE", "")
            or os.getenv("RELAYTV_MODE", "")
            or ""
        ).strip().lower()
    if mode == "x11":
        return True
    if mode == "drm":
        return False

    # auto/unknown: infer from display/session. In containers XDG_SESSION_TYPE may
    # be empty even when X11 forwarding is active, so DISPLAY is the primary signal.
    xdg = os.getenv("XDG_SESSION_TYPE", "").strip().lower()
    if xdg == "wayland":
        return False
    return _has_x11_display()


def _build_mpv_args(stream_url: str, audio_url: str | None, mode: str) -> list[str]:
    """Build mpv command line with minimal app-managed options.

    Keep mpv mostly default-driven and only set options required for RelayTV
    control/session behavior.
    """
    settings = getattr(state, "get_settings", lambda: {})()
    debug = _env_bool("MPV_DEBUG") or _env_bool("RELAYTV_DEBUG")
    extra = _split_env_args("MPV_ARGS")
    # Keep auto-tune opt-in so default playback args do not force decode
    # profile/hwdec choices unless explicitly requested by environment policy.
    tune_enabled = _env_bool("RELAYTV_MPV_AUTO_TUNE", False)
    try:
        runtime_profile = dict(video_profile.get_profile() or {}) if tune_enabled else {}
    except Exception:
        runtime_profile = {}
    decode_profile = str(runtime_profile.get("decode_profile") or "").strip().lower()

    log_file = (os.getenv("MPV_LOG_FILE") or "").strip()
    if not log_file and debug:
        log_file = "/tmp/mpv.log"
    mpv_args: list[str] = [
        "mpv",
        "--fs",
        "--keep-open=no",
        f"--input-ipc-server={IPC_PATH}",
    ]

    # Enable file logging if requested.
    if log_file and not _has_opt(mpv_args + extra, "--log-file"):
        mpv_args.append(f"--log-file={log_file}")

    if debug and not _has_opt(mpv_args + extra, "--msg-level"):
        mpv_args.append("--msg-level=all=debug")

    # Conservative decode defaults by runtime profile.
    # MPV_ARGS retains override priority by short-circuiting when explicit opts
    # are already present.
    if tune_enabled and decode_profile.startswith("intel_amd64_") and not _has_opt(mpv_args + extra, "--hwdec"):
        mpv_args.append("--hwdec=auto-safe")

    # Keep defaults mostly mpv-driven, but allow a lightweight ARM safety net
    # for sync stability when users do not provide an explicit profile.
    arm_fast_default = _env_bool("RELAYTV_ARM_FAST_PROFILE", True)
    arm_machine = (platform.machine() or "").lower() in ("aarch64", "arm64")
    if arm_fast_default and (arm_machine or decode_profile == "arm_safe") and not _has_opt(mpv_args + extra, "--profile"):
        mpv_args.append("--profile=fast")

    if audio_url:
        mpv_args.append(f"--audio-file={audio_url}")

    # In X11 sessions, route notifications through RelayTV's overlay and
    # suppress mpv playback banners/messages that can appear on every file start.
    suppress_mpv_osd = _x11_mode_active(mode) or _x11_mode_active(None) or _x11_overlay_enabled()
    if suppress_mpv_osd:
        mpv_args += [
            "--osd-level=0",
            "--osd-playing-msg=",
            "--term-playing-msg=",
            "--osc=no",
        ]
    if _overlay_osd_debug_enabled():
        debug_log(
            "osd",
            f"build_mpv_args mode={mode!r} x11_selected={_x11_mode_active(mode)} x11_env={_x11_mode_active(None)} x11_overlay={_x11_overlay_enabled()} suppress_mpv_osd={suppress_mpv_osd}",
        )

    # Keep mpv defaults, but honor explicit operator/UI audio-device selection
    # when provided so deployments can target the correct HDMI/ALSA sink.
    audio_dev = _effective_audio_device(settings)
    if audio_dev:
        mpv_args.append(f"--audio-device={audio_dev}")

    # Persisted user volume should survive track changes and app restarts.
    volume = _configured_start_volume()
    mpv_args.append(f"--volume={volume:g}")

    provider_hint = _provider_hint_for_stream(stream_url, fallback_now_playing=True)
    ytdl_allowed = _env_bool("RELAYTV_MPV_YTDL", True) and (not _should_force_ytdl_off(stream_url, provider_hint))
    if ytdl_allowed:
        mpv_args.append("--ytdl=yes")
        ytdl_path = (os.getenv("RELAYTV_MPV_YTDL_PATH") or "yt-dlp").strip()
        mpv_args.append(f"--script-opts=ytdl_hook-ytdl_path={ytdl_path}")
        ytdl_format = _effective_ytdl_format(settings, provider=provider_hint)

        if ytdl_format:
            mpv_args.append(f"--ytdl-format={ytdl_format}")
        ytdl_raw = (os.getenv("RELAYTV_MPV_YTDL_RAW_OPTIONS") or "").strip()
        if ytdl_raw:
            mpv_args.append(f"--ytdl-raw-options={ytdl_raw}")
    else:
        mpv_args.append("--ytdl=no")

    # Video output mode
    mode = (mode or "auto").strip().lower()
    if mode == "drm":
        # DRM/KMS requires explicit VO/context selection.
        mpv_args += [
            "--vo=gpu",
            "--gpu-context=drm",
        ]
        conn = (settings.get("drm_connector") or os.getenv("RELAYTV_DRM_CONNECTOR") or "").strip()
        if conn:
            mpv_args.append(f"--drm-connector={conn}")

    sub_lang = (settings.get("sub_lang") or os.getenv("RELAYTV_SUB_LANG") or "").strip()
    if sub_lang:
        mpv_args.append("--sub-auto=fuzzy")
        mpv_args.append(f"--slang={sub_lang}")
    # Append user args, then sanitize duplicate singleton options.
    mpv_args += extra
    mpv_args.append(stream_url)
    mpv_args = _first_wins_dedupe(mpv_args)
    return mpv_args


def _video_output_healthy(timeout: float = 2.0) -> bool:
    """Best-effort check that mpv has an active video output path."""
    deadline = time.time() + max(0.1, timeout)
    native_age = max(0.5, min(timeout, 1.5))
    while time.time() < deadline:
        if not _is_playing():
            return False
        native_state = _qt_shell_runtime_output_state(max_age_sec=native_age)
        if native_state is not None:
            has_video = bool(native_state.get("current_vo"))
            has_audio = bool(native_state.get("current_ao")) or isinstance(native_state.get("aid"), int)
            degraded = native_state.get("sample_detail") == "property_read_degraded"
            playback_active = bool(native_state.get("playback_active"))
            if has_video or (degraded and playback_active):
                return True
            if has_audio:
                return False
            time.sleep(0.1)
            continue
        try:
            props = mpv_get_many(["current-vo", "video-params", "audio-params"])
        except Exception:
            props = {}

        cur_vo = props.get("current-vo")
        vparams = props.get("video-params")
        aparams = props.get("audio-params")

        has_video = bool(cur_vo) or isinstance(vparams, dict)
        has_audio = isinstance(aparams, dict)
        if has_video:
            return True

        # If we only have audio after startup settles, treat as video init failure.
        if has_audio:
            return False

        time.sleep(0.1)
    return False


def _drm_no_video_fallback_allowed() -> bool:
    return _env_bool("RELAYTV_DRM_VIDEO_FALLBACK_TO_X11", True)


def _configured_start_volume() -> float:
    """Return persisted/default startup volume clamped to mpv's 0..200 range."""
    settings = getattr(state, "get_settings", lambda: {})()
    configured_volume = settings.get("volume")
    try:
        return max(0.0, min(200.0, float(configured_volume)))
    except Exception:
        return 100.0


def _effective_audio_device(settings: dict[str, Any] | None = None) -> str:
    """Resolve audio device preference with auto-detect fallback.

    Priority:
      1) Persisted settings `audio_device`
      2) Explicit env `MPV_AUDIO_DEVICE`
      3) Best-effort hardware detect from `devices.detect_audio_device()`
    """
    s = settings if isinstance(settings, dict) else getattr(state, "get_settings", lambda: {})()
    explicit = (s.get("audio_device") or os.getenv("MPV_AUDIO_DEVICE") or "").strip()
    if explicit:
        return explicit

    connector = (s.get("drm_connector") or os.getenv("RELAYTV_DRM_CONNECTOR") or "").strip()
    try:
        detected = (devices.detect_audio_device(connector) or "").strip()
    except Exception:
        detected = ""
    return detected


def _audio_device_explicitly_configured(settings: dict[str, Any] | None = None) -> bool:
    s = settings if isinstance(settings, dict) else getattr(state, "get_settings", lambda: {})()
    return bool((s.get("audio_device") or os.getenv("MPV_AUDIO_DEVICE") or "").strip())


def _audio_output_ready() -> bool:
    native_state = _qt_shell_runtime_output_state(max_age_sec=1.5)
    if native_state is not None:
        ao = str(native_state.get("current_ao") or "").strip()
        aid = native_state.get("aid")
        if ao:
            return True
        if isinstance(aid, int) and aid > 0:
            return True
        if native_state.get("sample_detail") == "property_read_degraded" and native_state.get("playback_active"):
            return True
        return False
    try:
        props = mpv_get_many(["current-ao", "audio-params", "current-tracks/audio/id"])
    except Exception:
        return False
    if isinstance(props.get("audio-params"), dict):
        return True
    ao = props.get("current-ao")
    if isinstance(ao, str) and ao.strip():
        return True
    aid = props.get("current-tracks/audio/id")
    return isinstance(aid, (int, float)) and int(aid) > 0


def _recover_audio_output_if_needed(settings: dict[str, Any] | None = None) -> None:
    """Best-effort audio recovery after startup.

    If no explicit audio device is configured and AO didn't initialize, fall back
    to mpv auto sink/device selection and reselect audio track.
    """
    try:
        mpv_set("aid", "auto")
    except Exception:
        pass
    # Give mpv a brief moment to settle after initial startup.
    time.sleep(0.08)
    if _audio_output_ready():
        return
    if _audio_device_explicitly_configured(settings):
        return
    fallback_dev = (_effective_audio_device(settings) or "").strip()
    try:
        mpv_set("audio-device", fallback_dev or "auto")
    except Exception:
        pass
    try:
        mpv_set("aid", "auto")
    except Exception:
        pass


def _apply_startup_mpv_runtime_settings() -> None:
    """Best-effort runtime settings used by both classic and Qt backends."""
    try:
        mpv_set("volume", _configured_start_volume())
    except Exception:
        pass


def _load_stream_in_existing_mpv(stream_url: str, audio_url: str | None = None) -> bool:
    """Try seamless in-process stream replacement on an already-running mpv."""
    if not _env_bool("RELAYTV_MPV_SEAMLESS_REPLACE", True):
        return False
    if (
        _qt_shell_backend_enabled()
        and (not _qt_runtime_uses_external_mpv())
        and (not _idle_dashboard_enabled())
    ):
        sess = str(getattr(state, "SESSION_STATE", "") or "").strip().lower()
        has_now = isinstance(getattr(state, "NOW_PLAYING", None), dict)
        if not (has_now or sess in ("playing", "paused")):
            return False
    # Qt startup transitions can temporarily report "playing" while only the
    # idle shell is alive. Gate seamless replace on a real media runtime.
    runtime_alive = False
    if _qt_shell_backend_enabled():
        if _qt_runtime_uses_external_mpv():
            runtime_alive = _qt_external_mpv_running()
        else:
            snap = _qt_shell_runtime_snapshot(max_age_sec=3.0)
            playback_active = False
            idle_shell_reusable = False
            qt_runtime_alive = False
            if isinstance(snap, dict):
                path = str(snap.get("mpv_runtime_path") or "").strip()
                control_file = str(snap.get("control_file") or "").strip()
                qt_runtime_alive = snap.get("alive") is True
                stream_loaded = snap.get("mpv_runtime_stream_loaded")
                playback_started = snap.get("mpv_runtime_playback_started")
                core_idle = snap.get("mpv_runtime_core_idle")
                eof_reached = snap.get("mpv_runtime_eof_reached")
                runtime_error = str(snap.get("mpv_runtime_error") or "").strip().lower()
                sample_detail = str(snap.get("mpv_runtime_sample_detail") or "").strip().lower()
                playback_active = bool(
                    snap.get("mpv_runtime_playback_active") is True
                    or (path and core_idle is not True)
                    or ((stream_loaded is True or playback_started is True) and core_idle is not True)
                    or (eof_reached is True and (path or stream_loaded is True or playback_started is True))
                )
                idle_shell_reusable = bool(
                    (_qt_shell_running() or qt_runtime_alive)
                    and control_file
                    and not runtime_error.startswith("loadfile_failed")
                    and not sample_detail.startswith("loadfile_failed")
                )
            if (not idle_shell_reusable) and _qt_shell_running():
                idle_shell_reusable = bool(str(_qt_shell_runtime_control_file() or "").strip())
            runtime_alive = playback_active or idle_shell_reusable or qt_runtime_alive or _qt_runtime_active(require_active_session=False)
    else:
        proc = MPV_PROC
        try:
            runtime_alive = bool(proc and proc.poll() is None)
        except Exception:
            runtime_alive = False
    if not runtime_alive:
        return False
    control_available = os.path.exists(IPC_PATH)
    if not control_available:
        snap = _qt_shell_runtime_snapshot(max_age_sec=10.0)
        control_available = isinstance(snap, dict) and bool(str(snap.get("control_file") or "").strip())
    if (
        (not control_available)
        and _qt_shell_backend_enabled()
        and (not _qt_runtime_uses_external_mpv())
        and _qt_shell_running()
    ):
        control_available = bool(str(_qt_shell_runtime_control_file() or "").strip())
    if not control_available:
        return False

    if _qt_shell_runtime_accepts_mpv_commands():
        try:
            resp = _qt_shell_runtime_load_stream(stream_url, audio_url)
        except Exception:
            return False
    else:
        cmd: list[object] = ["loadfile", str(stream_url), "replace"]
        if audio_url:
            cmd.extend(["-1", f"audio-files-append={str(audio_url)}"])
        try:
            resp = mpv_command(cmd)
        except Exception:
            return False
    if not isinstance(resp, dict) or resp.get("error") != "success":
        return False

    # Existing "up-next armed" state points at the old timeline.
    _reset_mpv_up_next_state()
    return True


def start_mpv(stream_url: str, audio_url: str | None = None):
    """Start mpv fullscreen with an IPC socket so we can control it.

    Video mode:
      - RELAYTV_VIDEO_MODE=auto (default): try DRM first; if it fails quickly, fall back to X11.
      - RELAYTV_VIDEO_MODE=x11: force X11 mode.
      - RELAYTV_VIDEO_MODE=drm: force DRM/KMS mode.
    """
    global MPV_PROC
    # Resolve can take longer than the initial transition window. Refresh it
    # here so watchdogs do not relaunch the idle shell while playback startup
    # is tearing down the idle runtime and spawning the media runtime.
    _mark_playback_transition()
    stop_splash_screen()
    stop_mpv(restart_splash=False)
    _reset_mpv_up_next_state()

    # Remove stale socket
    _cleanup_ipc_socket()

    settings = getattr(state, "get_settings", lambda: {})()
    mode = ((settings.get("video_mode")) or os.getenv("RELAYTV_VIDEO_MODE", "auto") or "auto").strip().lower()
    debug = _env_bool("MPV_DEBUG") or _env_bool("RELAYTV_DEBUG")
    if _overlay_osd_debug_enabled():
        debug_log("osd", f"start_mpv requested_mode={mode!r} display={os.getenv('DISPLAY')!r} xdg_session_type={os.getenv('XDG_SESSION_TYPE')!r}")

    startup_timeout = float(os.getenv("RELAYTV_MPV_STARTUP_TIMEOUT", "5"))

    if _qt_shell_backend_enabled():
        if _qt_runtime_uses_external_mpv():
            MPV_PROC = _start_qt_external_mpv(
                stream_url,
                audio_url=audio_url,
                fallback_to_x11=False,
                fallback_reason="",
            )
            if not wait_for_ipc_ready(timeout=startup_timeout):
                raise HTTPException(status_code=500, detail="qt external mpv started but IPC not ready")
            healthy = _qt_external_video_healthy_with_grace()
            _record_qt_external_video_health(healthy)
            if not healthy:
                debug_log("player", "Qt external mpv came up without video output")
                if _has_x11_display():
                    debug_log("player", "Retrying Qt external mpv with X11 EGL fallback")
                    stop_mpv(restart_splash=False)
                    MPV_PROC = _start_qt_external_mpv(
                        stream_url,
                        audio_url=audio_url,
                        fallback_to_x11=True,
                        fallback_reason="video_unhealthy",
                    )
                    if not wait_for_ipc_ready(timeout=startup_timeout):
                        raise HTTPException(status_code=500, detail="qt external mpv x11 fallback started but IPC not ready")
                else:
                    _set_qt_external_runtime_reason("video_unhealthy_no_x11")
            _apply_startup_mpv_runtime_settings()
            _recover_audio_output_if_needed(settings)
            return

        _start_qt_shell(stream_url, audio_url=audio_url)
        if not wait_for_ipc_ready(timeout=startup_timeout):
            raise HTTPException(status_code=500, detail="qt shell started but mpv IPC not ready")
        _apply_startup_mpv_runtime_settings()
        _recover_audio_output_if_needed(settings)
        return

    def _spawn(mode_to_use: str) -> subprocess.Popen:
        args = _build_mpv_args(stream_url, audio_url, mode_to_use)
        if debug:
            logger.info("starting_mpv mode=%s args=%s", mode_to_use, " ".join(shlex.quote(a) for a in args))
        return subprocess.Popen(args)

    if mode == "x11":
        MPV_PROC = _spawn("x11")
        if not wait_for_ipc_ready(timeout=startup_timeout):
            raise HTTPException(status_code=500, detail="mpv started but IPC not ready")
        return

    if mode == "drm":
        MPV_PROC = _spawn("drm")
        if not wait_for_ipc_ready(timeout=startup_timeout):
            raise HTTPException(status_code=500, detail="mpv started but IPC not ready")
        if not _video_output_healthy(timeout=2.0):
            debug_log("player", "DRM mode came up without video output")
            if _drm_no_video_fallback_allowed() and _has_x11_display():
                debug_log("player", "Falling back from DRM to X11 due to missing video output")
                stop_mpv(restart_splash=False)
                MPV_PROC = _spawn("x11")
                if not wait_for_ipc_ready(timeout=startup_timeout):
                    raise HTTPException(status_code=500, detail="mpv x11 fallback started but IPC not ready")
        return

    # auto: prefer DRM when possible, but fall back to X11 if DRM fails (e.g., desktop holds DRM master)
    MPV_PROC = _spawn("drm")
    try:
        MPV_PROC.wait(timeout=0.6)
        # Exited quickly -> fallback if X11 is available
        if _has_x11_display():
            MPV_PROC = _spawn("x11")
    except subprocess.TimeoutExpired:
        # Still running => assume OK
        pass

    if not wait_for_ipc_ready(timeout=startup_timeout):
        raise HTTPException(status_code=500, detail="mpv started but IPC not ready")

    if not _video_output_healthy(timeout=2.0):
        debug_log("player", "Auto mode detected no video output after startup")
        if _drm_no_video_fallback_allowed() and _has_x11_display():
            debug_log("player", "Retrying playback in X11 due to missing video output")
            stop_mpv(restart_splash=False)
            MPV_PROC = _spawn("x11")
            if not wait_for_ipc_ready(timeout=startup_timeout):
                raise HTTPException(status_code=500, detail="mpv x11 fallback started but IPC not ready")


def wait_for_ipc_ready(timeout: float = 5.0) -> bool:
    """Wait for playback control to become reachable.

    Legacy/external mpv still requires IPC readiness. Native Qt embed mode may
    become ready via its runtime heartbeat before IPC is reachable, so accept
    that runtime contract as the startup gate for native Qt playback.
    """
    timeout_sec = max(0.1, timeout)
    deadline = time.time() + timeout_sec
    heartbeat_age = max(0.5, min(timeout_sec, 1.5))
    while time.time() < deadline:
        if _qt_shell_runtime_startup_ready(max_age_sec=heartbeat_age):
            return True
        if not _is_playing():
            time.sleep(0.05)
            continue
        if os.path.exists(IPC_PATH):
            try:
                r = _mpv_ipc_request({"command": ["get_property", "pause"]}, timeout=0.5)
                if isinstance(r, dict) and (r.get("error") in ("success", None)):
                    return True
            except Exception:
                pass
        time.sleep(0.05)
    return False


def get_mpv_log_tail(lines: int = 80) -> list[str]:
    """Return last N lines from mpv log file when configured."""
    log_file = (os.getenv("MPV_LOG_FILE") or "").strip()
    if not log_file or not os.path.exists(log_file):
        return []
    max_lines = max(1, int(lines))
    try:
        # Tail efficiently from EOF to avoid full-file reads on frequent /status
        # polling (important on low-power hosts when mpv.log grows large).
        with open(log_file, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            if file_size <= 0:
                return []
            chunk_size = 8192
            remaining = file_size
            blocks: list[bytes] = []
            newline_count = 0
            while remaining > 0 and newline_count <= (max_lines + 1):
                step = min(chunk_size, remaining)
                remaining -= step
                f.seek(remaining, os.SEEK_SET)
                data = f.read(step)
                if not data:
                    break
                blocks.append(data)
                newline_count += data.count(b"\n")
            merged = b"".join(reversed(blocks))
        tail_lines = merged.decode("utf-8", errors="replace").splitlines()[-max_lines:]
        return [ln.rstrip("\n") for ln in tail_lines]
    except Exception:
        return []


def _mpv_ipc_request(payload: dict, timeout: float = 1.0) -> dict:
    """Send one JSON IPC command to mpv and return parsed JSON response.

    We translate missing socket / connection failures into a 409 so UI polling and
    remote commands can handle "mpv not ready" gracefully.
    """
    if not os.path.exists(IPC_PATH):
        raise HTTPException(status_code=409, detail="mpv IPC socket not available (is it playing?)")

    data = (json.dumps(payload) + "\n").encode("utf-8")
    attempts = _mpv_ipc_retry_count()
    backoff = _mpv_ipc_retry_backoff_sec()
    last_exc: Exception | None = None
    resp = b""
    for attempt in range(max(1, attempts)):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(IPC_PATH)
                s.sendall(data)
                # mpv responses are single-line JSON
                resp = b""
                while not resp.endswith(b"\n"):
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
            last_exc = None
            break
        except (OSError, ConnectionError, TimeoutError, socket.timeout) as e:
            last_exc = e
            if attempt + 1 >= max(1, attempts):
                break
            time.sleep(backoff)
    if last_exc is not None:
        raise HTTPException(status_code=409, detail=f"mpv IPC not available: {last_exc}")

    try:
        return json.loads(resp.decode("utf-8", "replace").strip() or "{}")
    except Exception:
        return {"raw": resp.decode("utf-8", "replace")}

def _mpv_ipc_request_many(payloads: list[dict], timeout: float = 1.5) -> list[dict]:
    """Send multiple IPC requests over a single UNIX socket connection.

    mpv's IPC is line-delimited JSON. Opening a new connection per property can overwhelm
    mpv under frequent polling; this batches requests to reduce connection churn.
    """
    if not payloads:
        return []
    if not os.path.exists(IPC_PATH):
        raise HTTPException(status_code=409, detail="mpv IPC socket not available (is it playing?)")

    attempts = _mpv_ipc_retry_count()
    backoff = _mpv_ipc_retry_backoff_sec()
    last_exc: Exception | None = None
    for attempt in range(max(1, attempts)):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(IPC_PATH)
            for p in payloads:
                s.sendall((json.dumps(p) + "\n").encode("utf-8"))

            buf = b""
            out: list[dict] = []
            while len(out) < len(payloads):
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf and len(out) < len(payloads):
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line.decode("utf-8", "replace")))
                    except Exception:
                        out.append({"error": "parse_error"})
            return out
        except (OSError, ConnectionError, TimeoutError, socket.timeout) as exc:
            last_exc = exc
            if attempt + 1 >= max(1, attempts):
                break
            time.sleep(backoff)
        finally:
            try:
                s.close()
            except Exception:
                pass
    if last_exc is not None:
        raise HTTPException(status_code=409, detail=f"mpv IPC not available: {last_exc}")
    return []

def mpv_command(cmd_list: list):
    command_name = str((list(cmd_list or [None])[0]) or "").strip().lower()
    runtime_cmd_allowed = False
    runtime_timeout: float | None = None
    if command_name != "get_property":
        runtime_cmd_allowed = _qt_shell_runtime_accepts_mpv_commands()
        if not runtime_cmd_allowed and _qt_shell_runtime_preferred():
            try:
                runtime_cmd_allowed = not _qt_shell_runtime_requires_live_mpv_ipc()
            except Exception:
                runtime_cmd_allowed = False
    if command_name == "seek":
        runtime_timeout = _qt_shell_runtime_seek_wait_sec()
    elif (
        command_name == "set_property"
        and len(cmd_list or []) >= 3
        and str((cmd_list or [None, None])[1] or "").strip().lower() == "pause"
    ):
        runtime_timeout = _qt_shell_runtime_pause_wait_sec()
    if runtime_cmd_allowed:
        return _qt_shell_runtime_finalize_control_result(_qt_shell_runtime_command(cmd_list), timeout_sec=runtime_timeout)
    try:
        result = _mpv_ipc_request({"command": cmd_list})
    except Exception:
        if runtime_cmd_allowed:
            return _qt_shell_runtime_finalize_control_result(_qt_shell_runtime_command(cmd_list), timeout_sec=runtime_timeout)
        raise
    if isinstance(result, dict) and result.get("error") == "success":
        return result
    if runtime_cmd_allowed:
        return _qt_shell_runtime_finalize_control_result(_qt_shell_runtime_command(cmd_list), timeout_sec=runtime_timeout)
    return result


def mpv_seek_absolute_with_retry(sec: float, tries: int = 25, delay: float = 0.12, tolerance: float = 1.0) -> bool:
    """Seek to absolute time with retries, only succeeding when mpv reports success."""
    if sec is None:
        return False
    try:
        target = float(sec)
    except Exception:
        return False
    if target <= 0.25:
        return False

    for _ in range(max(1, int(tries))):
        try:
            resp = mpv_command(["seek", target, "absolute"])
            # mpv IPC returns {"error": "success"} on success
            if isinstance(resp, dict) and resp.get("error") == "success":
                # Best-effort verify time-pos if available
                try:
                    cur = mpv_get("time-pos")
                    if cur is None:
                        return True
                    curf = float(cur)
                    if abs(curf - target) <= float(tolerance) or curf >= (target - float(tolerance)):
                        return True
                except Exception:
                    return True
        except Exception:
            pass
        time.sleep(float(delay))

    return False



def mpv_get(prop: str):
    key = str(prop or "").strip()
    prefer_host = _qt_shell_runtime_preferred()
    live_ipc_required = _qt_shell_runtime_requires_live_mpv_ipc([key])
    if not prefer_host and not live_ipc_required:
        cache_ttl = _mpv_poll_cache_ttl_sec()
        cached = _mpv_cache_get(prop, max_age_sec=cache_ttl)
        if cached is not None:
            return cached
    host_val = _host_runtime_mpv_property(prop) if prefer_host else None
    if prefer_host and (not live_ipc_required) and host_val is not None:
        if key:
            _mpv_cache_update({key: host_val})
        return host_val
    if prefer_host and (not live_ipc_required) and _qt_shell_runtime_supports_mpv_property(key):
        cached = _mpv_cache_get(prop, max_age_sec=_mpv_poll_cache_stale_sec())
        if cached is not None:
            return cached
    # Status/UI polling should never crash the whole API if mpv is not ready.
    try:
        r = mpv_command(["get_property", prop])
    except Exception:
        host_val = _host_runtime_mpv_property(prop)
        if host_val is not None:
            if key:
                _mpv_cache_update({key: host_val})
            return host_val
        cached = _mpv_cache_get(prop, max_age_sec=_mpv_poll_cache_stale_sec())
        if cached is not None:
            return cached
        return _host_runtime_mpv_property(prop)
    if r.get("error") != "success":
        host_val = _host_runtime_mpv_property(prop)
        if host_val is not None:
            if key:
                _mpv_cache_update({key: host_val})
            return host_val
        cached = _mpv_cache_get(prop, max_age_sec=_mpv_poll_cache_stale_sec())
        if cached is not None:
            return cached
        return _host_runtime_mpv_property(prop)
    if "data" in r:
        val = r.get("data")
        if key:
            _mpv_cache_update({key: val})
        return val
    host_val = _host_runtime_mpv_property(prop)
    if host_val is not None:
        if key:
            _mpv_cache_update({key: host_val})
        return host_val
    cached = _mpv_cache_get(prop, max_age_sec=_mpv_poll_cache_stale_sec())
    if cached is not None:
        return cached
    return _host_runtime_mpv_property(prop)



def mpv_get_many(props: list[str]) -> dict[str, Any]:
    """Get multiple mpv properties in one IPC connection."""
    normalized = [str(p or "").strip() for p in list(props or []) if str(p or "").strip()]
    if not normalized:
        return {}
    prefer_host = _qt_shell_runtime_preferred()
    live_ipc_required = _qt_shell_runtime_requires_live_mpv_ipc(normalized)
    host = _host_runtime_mpv_properties(normalized) if prefer_host else {}
    if prefer_host and (not live_ipc_required) and all(host.get(p) is not None for p in normalized):
        _mpv_cache_update(host)
        return host
    if prefer_host and (not live_ipc_required) and all(_qt_shell_runtime_supports_mpv_property(p) for p in normalized):
        stale = _mpv_cache_get_many(
            normalized,
            max_age_sec=_mpv_poll_cache_stale_sec(),
            project_playback=True,
        )
        out: dict[str, Any] = dict(host)
        for p in normalized:
            if out.get(p) is None and stale.get(p) is not None:
                out[p] = stale.get(p)
        if all(out.get(p) is not None for p in normalized):
            _mpv_cache_update(out)
            return out
    if not prefer_host and (not live_ipc_required):
        cache_ttl = _mpv_poll_cache_ttl_sec()
        if cache_ttl > 0:
            cached = _mpv_cache_get_many(normalized, max_age_sec=cache_ttl, project_playback=True)
            if len(cached) == len(normalized):
                return cached
    payloads = [{"command": ["get_property", p]} for p in normalized]
    try:
        resps = _mpv_ipc_request_many(payloads, timeout=_mpv_poll_ipc_timeout_sec())
    except Exception:
        fallback_host = host if prefer_host else _host_runtime_mpv_properties(normalized)
        if any(v is not None for v in fallback_host.values()):
            _mpv_cache_update(fallback_host)
            return fallback_host
        stale = _mpv_cache_get_many(
            normalized,
            max_age_sec=_mpv_poll_cache_stale_sec(),
            project_playback=True,
        )
        return stale if stale else {}
    out: dict[str, Any] = {}
    success_count = 0
    for p, r in zip(normalized, resps):
        if isinstance(r, dict) and r.get("error") == "success":
            out[p] = r.get("data")
            success_count += 1
        else:
            out[p] = None
    for p in normalized:
        if p not in out:
            out[p] = None
    if prefer_host:
        for p in normalized:
            if host.get(p) is not None:
                out[p] = host.get(p)
        _mpv_cache_update(out)
        return out
    host = _host_runtime_mpv_properties(normalized)
    if success_count <= 0:
        if any(v is not None for v in host.values()):
            _mpv_cache_update(host)
            return host
    else:
        for p in normalized:
            if out.get(p) is None and host.get(p) is not None:
                out[p] = host.get(p)
    _mpv_cache_update(out)
    return out

def mpv_set_result(prop: str, value) -> dict[str, Any]:
    key = str(prop or "").strip()
    r = mpv_command(["set_property", prop, value])
    if key == "pause" and _qt_shell_runtime_pause_timeout_tolerable(r):
        r = {
            "error": "success",
            "request_id": str(r.get("request_id") or ""),
            "ack_observed": False,
            "ack_reason": "control_pending",
        }
    if r.get("error") != "success":
        raise HTTPException(status_code=500, detail=f"mpv set_property failed: {r}")
    if key:
        _mpv_cache_update({key: value})
    return dict(r)


def mpv_set(prop: str, value):
    mpv_set_result(prop, value)
    return True


def native_qt_runtime_active() -> bool:
    return _qt_runtime_active(require_active_session=False)


def native_qt_playback_explicitly_ended() -> bool:
    snap = _qt_shell_runtime_snapshot(max_age_sec=3.0)
    if not isinstance(snap, dict):
        return False
    eof_reached = snap.get("mpv_runtime_eof_reached")
    core_idle = snap.get("mpv_runtime_core_idle")
    stream_loaded = snap.get("mpv_runtime_stream_loaded")
    playback_started = snap.get("mpv_runtime_playback_started")
    if eof_reached is True and (core_idle is True or stream_loaded is not True):
        return True
    if core_idle is True and (stream_loaded is not True) and (playback_started is not True):
        return True
    return False


def queue_handoff_suppress_sec() -> float:
    try:
        base = float(os.getenv("RELAYTV_QUEUE_HANDOFF_SUPPRESS_SEC", "2.0"))
    except Exception:
        base = 2.0
    base = max(0.5, min(20.0, base))
    return base


def _queue_item_identity(item: object) -> str:
    if not isinstance(item, dict):
        return str(item or "").strip()
    return "|".join(
        [
            str(item.get("jellyfin_item_id") or "").strip(),
            str(item.get("jellyfin_media_source_id") or "").strip(),
            str(item.get("url") or "").strip(),
        ]
    )


def _queue_item_play_url(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("url") or "").strip()
    return str(item or "").strip()


def _queue_prefetch_ttl_sec() -> float:
    raw = (os.getenv("RELAYTV_QUEUE_PREFETCH_TTL_SEC") or "900").strip()
    try:
        return max(30.0, min(float(raw), 3600.0))
    except Exception:
        return 900.0


def _queue_prefetch_providers() -> set[str]:
    raw = (os.getenv("RELAYTV_QUEUE_PREFETCH_PROVIDERS") or "youtube,rumble,twitch,tiktok,bitchute,odysee,vimeo").strip().lower()
    return {p.strip() for p in raw.split(",") if p.strip()}


def _value_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) != 0.0
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "live")


def _url_looks_like_live_stream(url: str, provider: str) -> bool:
    try:
        parsed = urlparse(str(url or "").strip())
        path = (parsed.path or "").strip().lower()
        host = (parsed.netloc or "").strip().lower()
    except Exception:
        return False
    if not path:
        return False

    if provider == "youtube":
        if "/live" in path:
            return True
        try:
            q = parse_qs(parsed.query or "")
            live_q = str((q.get("live") or [""])[0] or "").strip().lower()
            if live_q in ("1", "true", "yes", "on"):
                return True
        except Exception:
            pass
        return False

    if provider == "twitch":
        if host.startswith("clips.twitch.tv"):
            return False
        if path.startswith("/videos/") or "/videos/" in path or "/clip/" in path or path.startswith("/clips/"):
            return False
        segs = [s for s in path.split("/") if s]
        if not segs:
            return False
        if segs[0] in {"directory", "downloads", "jobs", "p", "settings", "subscriptions", "wallet"}:
            return False
        return True

    return False


def _item_looks_like_live_stream(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    for key in (
        "is_live",
        "live",
        "live_stream",
        "is_live_stream",
        "livestream",
        "stream_live",
    ):
        if _value_truthy(item.get(key)):
            return True
    for key in ("jellyfin_stream_mode", "jellyfin_stream_reason", "stream_mode", "stream_reason"):
        val = str(item.get(key) or "").strip().lower()
        if "live" in val:
            return True

    url = _queue_item_play_url(item)
    if not url:
        return False
    provider = str(item.get("provider") or provider_from_url(url) or "").strip().lower()
    return _url_looks_like_live_stream(url, provider)


def _provider_item_is_live_stream(item: object, url: str, provider: str) -> bool:
    if _item_looks_like_live_stream(item):
        return True
    if provider != "rumble" or not url:
        return False
    try:
        info = ytdlp_info(url)
    except Exception:
        info = None
    if not isinstance(info, dict):
        return False
    live_status = str(info.get("live_status") or "").strip()
    is_live = bool(info.get("is_live")) or live_status.strip().lower() in ("is_live", "live")
    if is_live and isinstance(item, dict):
        item["is_live"] = True
        if live_status:
            item["live_status"] = live_status
    return is_live


def _item_should_prefetch_stream(item: object) -> bool:
    url = _queue_item_play_url(item)
    if not url:
        return False
    if isinstance(item, dict):
        provider = str(item.get("provider") or "").strip().lower()
    else:
        provider = ""
    if not provider:
        provider = str(provider_from_url(url) or "").strip().lower()
    if _provider_item_is_live_stream(item, url, provider):
        return False
    prefer_mpv_ytdl = _env_bool("RELAYTV_MPV_YTDL", True)
    force_resolve_provider = provider in _providers_forced_to_resolve()
    prefetch_provider = provider in _queue_prefetch_providers()
    return bool((not prefer_mpv_ytdl) or force_resolve_provider or prefetch_provider or is_youtube_url(url))


def _item_needs_metadata_prefetch(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    if bool(item.get("_metadata_lightweight")):
        return True
    title = str(item.get("title") or "").strip()
    url = str(item.get("url") or "").strip()
    thumb = str(item.get("thumbnail") or item.get("thumbnail_local") or "").strip()
    return (not title) or title == url or (not thumb)


def _fresh_prefetched_stream(item: object) -> tuple[str, str | None] | None:
    if not isinstance(item, dict):
        return None
    url = str(item.get("url") or "").strip()
    if not url:
        return None
    try:
        ts = float(item.get("_resolved_at") or 0.0)
    except Exception:
        ts = 0.0
    if ts <= 0.0 or (time.time() - ts) > _queue_prefetch_ttl_sec():
        return None
    if str(item.get("_resolved_source_url") or "").strip() != url:
        return None
    stream = str(item.get("_resolved_stream") or "").strip()
    if not stream:
        return None
    audio = str(item.get("_resolved_audio") or "").strip() or None
    return stream, audio


def _store_prefetched_stream(item: dict[str, Any], url: str, stream: str, audio: str | None) -> None:
    item["_resolved_source_url"] = str(url or "").strip()
    item["_resolved_stream"] = str(stream or "").strip()
    item["_resolved_audio"] = str(audio or "").strip() if audio else ""
    item["_resolved_at"] = float(time.time())


def _prefetch_queue_item_worker(item: dict[str, Any], url: str) -> None:
    try:
        stream, audio = resolve_streams(url)
        _store_prefetched_stream(item, url, stream, audio)
        debug_log("player", f"queue prefetch resolved provider={item.get('provider') or provider_from_url(url)} url={url!r}")
    except Exception as exc:
        debug_log("player", f"queue prefetch failed url={url!r} err={exc}")
    finally:
        with _QUEUE_PREFETCH_LOCK:
            _QUEUE_PREFETCH_INFLIGHT.discard(url)


def _prefetch_queue_item_metadata_worker(item: dict[str, Any], url: str, persist_queue: bool) -> None:
    try:
        changed = enrich_item_metadata(item)
        if changed and persist_queue:
            try:
                state.persist_queue()
            except Exception:
                pass
        debug_log("player", f"queue metadata prefetch {'updated' if changed else 'skipped'} url={url!r}")
    except Exception as exc:
        debug_log("player", f"queue metadata prefetch failed url={url!r} err={exc}")
    finally:
        with _QUEUE_METADATA_PREFETCH_LOCK:
            _QUEUE_METADATA_PREFETCH_INFLIGHT.discard(url)


def prefetch_queue_item_stream(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    url = str(item.get("url") or "").strip()
    if not url:
        return False

    started = False
    metadata_thread = None
    if _item_needs_metadata_prefetch(item):
        with _QUEUE_METADATA_PREFETCH_LOCK:
            if url not in _QUEUE_METADATA_PREFETCH_INFLIGHT:
                _QUEUE_METADATA_PREFETCH_INFLIGHT.add(url)
                metadata_thread = threading.Thread(
                    target=_prefetch_queue_item_metadata_worker,
                    args=(item, url, True),
                    daemon=True,
                    name="relaytv-queue-metadata-prefetch",
                )
                started = True
    if metadata_thread is not None:
        metadata_thread.start()

    if not _item_should_prefetch_stream(item):
        return started
    if _fresh_prefetched_stream(item) is not None:
        return True
    stream_thread = None
    with _QUEUE_PREFETCH_LOCK:
        if url in _QUEUE_PREFETCH_INFLIGHT:
            return True
        _QUEUE_PREFETCH_INFLIGHT.add(url)
        stream_thread = threading.Thread(
            target=_prefetch_queue_item_worker,
            args=(item, url),
            daemon=True,
            name="relaytv-queue-prefetch",
        )
    if stream_thread is not None:
        stream_thread.start()
    return True


def _mpv_up_next_eligible_item(item: object) -> bool:
    """Return True when queue head is safe for mpv seamless up-next handoff."""
    url = _queue_item_play_url(item)
    if not url:
        return False
    if isinstance(item, dict):
        provider = str(item.get("provider") or "").strip().lower()
    else:
        provider = ""
    if not provider:
        provider = str(provider_from_url(url) or "").strip().lower()
    prefer_mpv_ytdl = _env_bool("RELAYTV_MPV_YTDL", True)
    force_resolve_provider = provider in _providers_forced_to_resolve()
    # Keep resolver-only providers out of mpv's internal queue to avoid false
    # handoff consumption when extractor/auth fails before playback starts.
    if (not prefer_mpv_ytdl) or force_resolve_provider:
        return False
    if _item_should_prefetch_stream(item):
        return False
    return True


def _reset_mpv_up_next_state() -> None:
    global _MPV_UPNEXT_ARMED_ID, _MPV_UPNEXT_ARMED_URL, _MPV_UPNEXT_ARMED_AT
    with _MPV_UPNEXT_LOCK:
        _MPV_UPNEXT_ARMED_ID = ""
        _MPV_UPNEXT_ARMED_URL = ""
        _MPV_UPNEXT_ARMED_AT = 0.0


def _mpv_up_next_load_target(item: object) -> tuple[list[object], str] | None:
    """Return the mpv loadfile command and armed media URL for queue head."""
    head_url = _queue_item_play_url(item)
    if not head_url:
        return None
    if _mpv_up_next_eligible_item(item):
        return (["loadfile", head_url, "append-play"], head_url)
    prefetched = _fresh_prefetched_stream(item)
    if prefetched is None:
        return None
    stream_url, audio_url = prefetched
    if not stream_url:
        return None
    cmd: list[object] = ["loadfile", stream_url, "append-play"]
    if audio_url:
        cmd.extend(["-1", f"audio-files-append={str(audio_url)}"])
    return cmd, stream_url


def _clear_mpv_playlist_after_current() -> None:
    props = mpv_get_many(["playlist-pos", "playlist-count"])
    try:
        pos = int(props.get("playlist-pos") or 0)
    except Exception:
        pos = 0
    try:
        count = int(props.get("playlist-count") or 0)
    except Exception:
        count = 0
    if count <= (pos + 1):
        return
    for idx in range(count - 1, pos, -1):
        try:
            mpv_command(["playlist-remove", int(idx)])
        except Exception:
            break


def _clear_mpv_playlist_before_current() -> None:
    props = mpv_get_many(["playlist-pos"])
    try:
        pos = int(props.get("playlist-pos") or 0)
    except Exception:
        pos = 0
    if pos <= 0:
        return
    for _ in range(pos):
        try:
            mpv_command(["playlist-remove", 0])
        except Exception:
            break


def _prime_mpv_up_next_from_queue(*, force: bool = False) -> bool:
    """Keep exactly one up-next item queued in mpv's internal playlist."""
    global _MPV_UPNEXT_ARMED_ID, _MPV_UPNEXT_ARMED_URL, _MPV_UPNEXT_ARMED_AT
    if not _is_playing():
        _reset_mpv_up_next_state()
        return False
    control_available = os.path.exists(IPC_PATH)
    if not control_available:
        snap = _qt_shell_runtime_snapshot(max_age_sec=10.0)
        control_available = isinstance(snap, dict) and bool(str(snap.get("control_file") or "").strip())
    if not control_available:
        _reset_mpv_up_next_state()
        return False

    with state.QUEUE_LOCK:
        head = state.QUEUE[0] if state.QUEUE else None

    if head is None:
        with _MPV_UPNEXT_LOCK:
            had_armed = bool(_MPV_UPNEXT_ARMED_ID)
        if had_armed:
            try:
                _clear_mpv_playlist_before_current()
                _clear_mpv_playlist_after_current()
            except Exception:
                pass
        _reset_mpv_up_next_state()
        return False

    head_id = _queue_item_identity(head)
    head_url = _queue_item_play_url(head)
    if not head_url:
        return False
    load_target = _mpv_up_next_load_target(head)
    if load_target is None:
        try:
            prefetch_queue_item_stream(head)
        except Exception:
            pass
        with _MPV_UPNEXT_LOCK:
            had_armed = bool(_MPV_UPNEXT_ARMED_ID)
        if had_armed:
            try:
                _clear_mpv_playlist_after_current()
            except Exception:
                pass
        _reset_mpv_up_next_state()
        return False
    load_cmd, armed_url = load_target

    with _MPV_UPNEXT_LOCK:
        already_armed = (head_id == _MPV_UPNEXT_ARMED_ID) and (armed_url == _MPV_UPNEXT_ARMED_URL)
    if already_armed and not force:
        return True

    try:
        _clear_mpv_playlist_before_current()
        _clear_mpv_playlist_after_current()
        r = mpv_command(load_cmd)
        if not isinstance(r, dict) or r.get("error") != "success":
            return False
    except Exception:
        return False

    with _MPV_UPNEXT_LOCK:
        _MPV_UPNEXT_ARMED_ID = head_id
        _MPV_UPNEXT_ARMED_URL = armed_url
        _MPV_UPNEXT_ARMED_AT = time.time()
    return True


def prime_mpv_up_next_from_queue(*, force: bool = False) -> bool:
    """Public wrapper for routes/tests."""
    return _prime_mpv_up_next_from_queue(force=force)


class QueueAdvanceEmptyError(RuntimeError):
    """Raised when queue advance is requested but no queued item is available."""


class QueueAdvanceSuppressedError(RuntimeError):
    """Raised when auto-next queue advance is suppressed by explicit user close/stop."""


def _auto_next_suppressed() -> bool:
    if str(getattr(state, "SESSION_STATE", "idle") or "idle").strip().lower() == "closed":
        return True
    return False


def _attempt_playlist_next_handoff(*, poll_sleep: Callable[[float], None] | None = None) -> str | None:
    with state.QUEUE_LOCK:
        if not state.QUEUE:
            raise QueueAdvanceEmptyError("Queue is empty")
        head = state.QUEUE[0]
    # mpv playlist-next cannot apply app-level resume seeks for queued items.
    # If queue head carries a resume position, force dequeue/play_item handoff.
    if isinstance(head, dict):
        try:
            head_resume = float(head.get("resume_pos")) if head.get("resume_pos") is not None else None
        except Exception:
            head_resume = None
        if head_resume is not None and head_resume > 0.0:
            return None
    else:
        head_resume = None

    if not head:
        raise QueueAdvanceEmptyError("Queue is empty")

    try:
        seamless_ready = bool(prime_mpv_up_next_from_queue(force=False))
    except Exception:
        seamless_ready = False
    if not seamless_ready:
        return None

    try:
        result = mpv_command(["playlist-next", "force"])
    except Exception:
        result = {"error": "exception"}
    if not isinstance(result, dict) or result.get("error") != "success":
        return None

    try:
        confirm_polls = int(os.getenv("RELAYTV_QUEUE_HANDOFF_CONFIRM_POLLS", "20"))
    except Exception:
        confirm_polls = 20
    confirm_polls = max(1, min(confirm_polls, 60))
    try:
        confirm_interval = float(os.getenv("RELAYTV_QUEUE_HANDOFF_CONFIRM_POLL_INTERVAL_SEC", "0.05"))
    except Exception:
        confirm_interval = 0.05
    confirm_interval = max(0.01, min(confirm_interval, 0.25))

    for i in range(confirm_polls):
        try:
            props = mpv_get_many(["playlist-pos", "playlist-count", "time-pos", "path", "pause"])
            if _consume_mpv_queued_next_if_started(props, force_playlist_advance=True):
                return "mpv_playlist_next"
        except Exception:
            pass
        if i < (confirm_polls - 1):
            if callable(poll_sleep):
                poll_sleep(confirm_interval)
            else:
                time.sleep(confirm_interval)
    return "mpv_playlist_next_pending"


def advance_queue_playback(
    *,
    mode: str,
    prefer_playlist_next: bool = False,
    poll_sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Advance playback to the next queue item using one shared handoff path."""
    handoff_guard = queue_handoff_suppress_sec()
    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + handoff_guard
    allow_skip_unplayable = mode in {"next", "play_next"}
    skipped_unplayable = 0

    with state.ADVANCE_LOCK:
        if mode == "auto_next" and _auto_next_suppressed():
            raise QueueAdvanceSuppressedError("auto-next suppressed while session is closed")
        if prefer_playlist_next and is_playing():
            method = _attempt_playlist_next_handoff(poll_sleep=poll_sleep)
            if method:
                state.AUTO_NEXT_SUPPRESS_UNTIL = max(
                    float(state.AUTO_NEXT_SUPPRESS_UNTIL or 0.0),
                    time.time() + handoff_guard,
                )
                return {
                    "status": "playing_next",
                    "now_playing": state.NOW_PLAYING,
                    "method": method,
                }
        while True:
            prev_now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
            with state.QUEUE_LOCK:
                if not state.QUEUE:
                    if skipped_unplayable > 0:
                        raise QueueAdvanceEmptyError("No playable items remain in queue")
                    raise QueueAdvanceEmptyError("Queue is empty")
                next_item = state.QUEUE.pop(0)
                snapshot = {"queue": list(state.QUEUE), "saved_at": int(time.time())}

            try:
                state.persist_queue_payload(snapshot)
            except Exception as exc:
                logger.warning("queue_persist_failed source=advance_queue_playback error=%s", exc)

            if mode == "auto_next" and _auto_next_suppressed():
                with state.QUEUE_LOCK:
                    state.QUEUE.insert(0, next_item)
                    rollback = {"queue": list(state.QUEUE), "saved_at": int(time.time())}
                try:
                    state.persist_queue_payload(rollback)
                except Exception as exc:
                    logger.warning("queue_rollback_persist_failed source=auto_next_suppressed error=%s", exc)
                raise QueueAdvanceSuppressedError("auto-next suppressed after dequeue")

            _emit_jellyfin_stopped_from_now(prev_now)
            start_pos = next_item.get("resume_pos") if isinstance(next_item, dict) else None
            try:
                now = play_item(
                    next_item,
                    use_resolver=True,
                    cec=False,
                    clear_queue=False,
                    mode=mode,
                    start_pos=(float(start_pos) if start_pos is not None else None),
                )
            except Exception as exc:
                skip_unplayable = (
                    allow_skip_unplayable
                    and isinstance(exc, HTTPException)
                    and int(getattr(exc, "status_code", 0) or 0) == 400
                )
                if skip_unplayable:
                    skipped_unplayable += 1
                    logger.warning(
                        "queue_skip_unplayable mode=%s skipped=%s title=%s error=%s",
                        mode,
                        skipped_unplayable,
                        str(next_item.get("title") or next_item.get("url") or "") if isinstance(next_item, dict) else str(next_item or ""),
                        exc,
                    )
                    continue
                with state.QUEUE_LOCK:
                    state.QUEUE.insert(0, next_item)
                    rollback = {"queue": list(state.QUEUE), "saved_at": int(time.time())}
                try:
                    state.persist_queue_payload(rollback)
                except Exception as persist_exc:
                    logger.warning("queue_rollback_persist_failed source=advance_queue_exception error=%s", persist_exc)
                raise

            state.AUTO_NEXT_SUPPRESS_UNTIL = max(
                float(state.AUTO_NEXT_SUPPRESS_UNTIL or 0.0),
                time.time() + handoff_guard,
            )
            result = {
                "status": "playing_next",
                "now_playing": now,
                "method": "dequeue_play_item",
            }
            if skipped_unplayable > 0:
                result["skipped_unplayable"] = skipped_unplayable
            return result


def _consume_mpv_queued_next_if_started(
    props: dict[str, Any] | None = None,
    *,
    force_playlist_advance: bool = False,
) -> bool:
    """When mpv auto-advances to queued item, consume queue head into app state."""
    def _same_media_ref(left: object, right: object) -> bool:
        volatile_query_keys = {
            "api_key",
            "auth",
            "expires",
            "exp",
            "signature",
            "sig",
            "token",
            "x-emby-token",
            "x-jellyfin-token",
            "jwt",
        }

        def _normalized_url_parts(raw: str) -> tuple[str, str, str, tuple[tuple[str, str], ...]] | None:
            try:
                parsed = urlsplit(raw)
            except Exception:
                return None
            if not (parsed.scheme and parsed.netloc):
                return None
            stable_query: list[tuple[str, str]] = []
            try:
                for k, v in parse_qsl(parsed.query, keep_blank_values=True):
                    key = str(k or "").strip().lower()
                    if not key:
                        continue
                    if key in volatile_query_keys:
                        continue
                    stable_query.append((key, str(v or "").strip()))
                stable_query.sort()
            except Exception:
                stable_query = []
            return (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path,
                tuple(stable_query),
            )

        left_norm = str(left or "").strip()
        right_norm = str(right or "").strip()
        if not left_norm or not right_norm:
            return False
        if left_norm == right_norm:
            return True
        ln = _normalized_url_parts(left_norm)
        rn = _normalized_url_parts(right_norm)
        if ln and rn:
            # Primary match keeps media-identifying query params (for example,
            # YouTube v=...), while ignoring volatile auth/token params.
            if ln == rn:
                return True
            # If no stable query params remain after filtering, path-only match
            # is still acceptable for tokenized stream endpoints.
            if (not ln[3]) and (not rn[3]) and ln[:3] == rn[:3]:
                return True
        return False

    if not _is_playing():
        return False

    with _MPV_UPNEXT_LOCK:
        armed_id = str(_MPV_UPNEXT_ARMED_ID or "")
        armed_url = str(_MPV_UPNEXT_ARMED_URL or "")
    if not armed_id:
        return False

    if not isinstance(props, dict):
        props = mpv_get_many(["playlist-pos", "time-pos", "path"])
    try:
        playlist_pos = int(props.get("playlist-pos") or 0)
    except Exception:
        playlist_pos = 0
    if playlist_pos < 1:
        return False

    prev_now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    prev_url = _queue_item_play_url(prev_now) if isinstance(prev_now, dict) else ""

    # For explicit user-initiated /next we can trust playlist-pos advancement
    # from mpv even if path/time-pos telemetry lags briefly behind.
    path_confirms_advanced = bool(force_playlist_advance)
    current_path = str(props.get("path") or "").strip()
    if current_path and not force_playlist_advance:
        if armed_url and _same_media_ref(current_path, armed_url):
            path_confirms_advanced = True
        elif prev_url and _same_media_ref(current_path, prev_url):
            return False

    # Guard against false queue consumption when mpv playlist metadata drifts:
    # only consume when current playback position appears to have reset relative
    # to the previous now-playing resume position.
    time_pos: float | None
    try:
        raw_time_pos = props.get("time-pos")
        time_pos = float(raw_time_pos) if raw_time_pos is not None else None
    except Exception:
        time_pos = None
    try:
        min_drop = float(os.getenv("RELAYTV_MPV_UPNEXT_MIN_POSITION_DROP_SEC", "5.0"))
    except Exception:
        min_drop = 5.0
    try:
        max_when_unknown_prev = float(os.getenv("RELAYTV_MPV_UPNEXT_CONSUME_MAX_TIMEPOS_SEC", "20.0"))
    except Exception:
        max_when_unknown_prev = 20.0
    prev_resume: float | None = None
    if isinstance(prev_now, dict):
        try:
            raw_prev_resume = prev_now.get("resume_pos")
            prev_resume = float(raw_prev_resume) if raw_prev_resume is not None else None
        except Exception:
            prev_resume = None
    if not path_confirms_advanced:
        if (prev_resume is not None) and (time_pos is not None):
            if time_pos >= max(0.0, prev_resume - min_drop):
                return False
        elif (prev_resume is None) and (time_pos is not None) and (time_pos > max_when_unknown_prev):
            return False

    consumed = None
    snapshot = None
    with state.QUEUE_LOCK:
        if state.QUEUE:
            idx = next((i for i, q in enumerate(state.QUEUE) if _queue_item_identity(q) == armed_id), None)
            if idx is None and armed_url:
                idx = next(
                    (
                        i
                        for i, q in enumerate(state.QUEUE)
                        if _same_media_ref(_queue_item_play_url(q), armed_url)
                    ),
                    None,
                )
            if idx is not None:
                consumed = state.QUEUE.pop(idx)
                snapshot = {"queue": list(state.QUEUE), "saved_at": int(time.time())}

    if snapshot is not None:
        try:
            state.persist_queue_payload(snapshot)
        except Exception as e:
            logger.warning("queue_persist_failed source=consume_up_next error=%s", e)

    if consumed is None:
        _reset_mpv_up_next_state()
        try:
            _prime_mpv_up_next_from_queue(force=True)
        except Exception:
            pass
        return False

    _emit_jellyfin_stopped_from_now(prev_now)

    url = _queue_item_play_url(consumed)
    title = url
    provider = provider_from_url(url)
    now_item: dict[str, Any] = {
        "input": url,
        "url": url,
        "title": title,
        "provider": provider,
        "stream": url,
        "audio": None,
        "started": int(time.time()),
        "mode": "auto_next_queue",
    }
    if isinstance(consumed, dict):
        now_item["title"] = consumed.get("title") or title
        now_item["provider"] = consumed.get("provider") or provider
        now_item["thumbnail"] = consumed.get("thumbnail")
        now_item["thumbnail_local"] = consumed.get("thumbnail_local")
        now_item["channel"] = consumed.get("channel")
        prefetched = _fresh_prefetched_stream(consumed)
        if prefetched is not None:
            prefetched_stream, prefetched_audio = prefetched
            now_item["stream"] = prefetched_stream
            now_item["audio"] = prefetched_audio
        if consumed.get("jellyfin_item_id"):
            now_item["jellyfin_item_id"] = consumed.get("jellyfin_item_id")
        if consumed.get("jellyfin_media_source_id"):
            now_item["jellyfin_media_source_id"] = consumed.get("jellyfin_media_source_id")

    try:
        pos = props.get("time-pos")
        if pos is not None:
            now_item["resume_pos"] = float(pos)
            state.set_session_position(float(pos))
    except Exception:
        pass

    state.set_now_playing(now_item)
    state.set_session_state("playing")
    state.set_pause_reason(None)
    _reset_mpv_up_next_state()
    _prime_mpv_up_next_from_queue(force=True)
    return True


def _providers_forced_to_resolve() -> set[str]:
    """Providers that should bypass mpv's yt-dlp hook and use server-side resolving."""
    raw = (os.getenv("RELAYTV_FORCE_RESOLVE_PROVIDERS") or "rumble,bitchute").strip().lower()
    return {p.strip() for p in raw.split(",") if p.strip()}


def play_item(item_or_text, use_resolver: bool, cec: bool, clear_queue: bool, mode: str, start_pos: float | None = None):
    """Play a queue item dict or a raw shared URL/text."""
    _mark_playback_transition()
    if isinstance(item_or_text, dict):
        item = item_or_text
    else:
        item = make_item(str(item_or_text), lightweight=False)

    raw = validate_user_url(item["url"])
    item["url"] = raw
    title = item.get("title") or raw
    provider = item.get("provider") or provider_from_url(raw)
    play_t0 = time.monotonic()
    debug_log("player", f"play_item start mode={mode} provider={provider} use_resolver={use_resolver}")
    debug_log("player", f"raw_url={raw!r}")
    # CEC auto on/switch with active-source awareness.
    tv_state = state.get_tv_state() if hasattr(state, "get_tv_state") else {}
    active_src = str(tv_state.get("active_source_phys_addr") or "")
    ours = _our_phys_addr() or ""
    should_take_over = _setting_enabled("tv_takeover_enabled", True) and (not ours or active_src != ours)
    if cec_auto_on_switch(cec) and should_take_over and cec_available():
        tv_on_and_switch()

    if clear_queue:
        with state.QUEUE_LOCK:
            state.QUEUE.clear()
        state.persist_queue()

    stream, audio = (raw, None)
    prefer_mpv_ytdl = _env_bool("RELAYTV_MPV_YTDL", True)
    provider = str(provider or "").strip().lower()
    trusted_local_stream = ""
    if provider == "upload" and isinstance(item, dict):
        local_candidate = str(item.get("_local_stream_path") or "").strip()
        if local_candidate and os.path.exists(local_candidate):
            trusted_local_stream = local_candidate
    item_is_live = _provider_item_is_live_stream(item, raw, provider)
    force_resolve_provider = provider in _providers_forced_to_resolve() and (not item_is_live)
    prefetched = _fresh_prefetched_stream(item)
    should_resolve = use_resolver and (not trusted_local_stream) and (not prefer_mpv_ytdl or is_youtube_url(raw) or force_resolve_provider or prefetched is not None)
    if force_resolve_provider:
        debug_log("player", f"forcing resolver for provider={provider}")
    if trusted_local_stream:
        stream = trusted_local_stream
    elif should_resolve:
        _mark_playback_transition(window_sec=_resolver_playback_transition_window_sec())
        # A previous x86_64 workaround stopped the idle Qt shell before long
        # YouTube resolves. That avoided one historical NUC crash, but it also
        # caused visible black-screen/desktop bounce from idle on /smart and
        # /play_now because the display left idle before playback was ready.
        # Keep this behavior as an explicit opt-in only.
        if (
            _qt_shell_resolve_prestop_enabled()
            and prefetched is None
            and provider == "youtube"
            and _qt_shell_backend_enabled()
            and (not _qt_runtime_uses_external_mpv())
            and (os.getenv("RELAYTV_QT_SHELL_MODULE") or "relaytv_app.qt_shell_app").strip() == "relaytv_app.qt_shell_app"
            and _qt_shell_running()
        ):
            try:
                runtime_profile = dict(video_profile.get_profile() or {})
            except Exception:
                runtime_profile = {}
            decode_profile = str(runtime_profile.get("decode_profile") or "").strip().lower()
            host_arch = (platform.machine() or "").strip().lower()
            sess = str(getattr(state, "SESSION_STATE", "") or "").strip().lower()
            has_now = isinstance(getattr(state, "NOW_PLAYING", None), dict)
            if (
                host_arch in ("x86_64", "amd64")
                and decode_profile != "arm_safe"
                and (not has_now)
                and sess in ("", "idle", "closed")
            ):
                _stop_qt_shell()
        if prefetched is not None:
            stream, audio = prefetched
            debug_log("player", "using prefetched resolved stream")
        else:
            t_resolve = time.monotonic()
            stream, audio = resolve_streams(raw)
            debug_log("player", f"resolve_streams finished in {int((time.monotonic() - t_resolve) * 1000)}ms")
            if isinstance(item, dict):
                _store_prefetched_stream(item, raw, stream, audio)

    debug_log("player", f"resolved_stream={stream!r} audio={audio!r}")

    with MPV_LOCK:
        t_mpv = time.monotonic()
        # Resolver work can outlast the initial transition window. Refresh it
        # immediately before the playback handoff so background idle workers do
        # not collapse the session while the reused runtime is loading media.
        _mark_playback_transition()
        reused_runtime = _load_stream_in_existing_mpv(stream, audio_url=audio)
        if (not reused_runtime) and _qt_shell_backend_enabled():
            # ARM hosts can expose a brief control-gap at EOF; retry a couple
            # of times before escalating to full player restart.
            try:
                retries = int(os.getenv("RELAYTV_SEAMLESS_REPLACE_RETRIES", "2"))
            except Exception:
                retries = 2
            retries = max(0, min(6, retries))
            try:
                delay_sec = float(os.getenv("RELAYTV_SEAMLESS_REPLACE_RETRY_DELAY_SEC", "0.12"))
            except Exception:
                delay_sec = 0.12
            delay_sec = max(0.01, min(1.0, delay_sec))
            for _ in range(retries):
                time.sleep(delay_sec)
                reused_runtime = _load_stream_in_existing_mpv(stream, audio_url=audio)
                if reused_runtime:
                    break
        if reused_runtime:
            # Keep auto-idle/orphan watchdogs suppressed while the reused shell
            # transitions from idle surface to the newly loaded stream.
            _mark_playback_transition()
        if not reused_runtime:
            start_mpv(stream, audio_url=audio)
        debug_log(
            "player",
            f"{'seamless_replace' if reused_runtime else 'start_mpv'} finished in "
            f"{int((time.monotonic() - t_mpv) * 1000)}ms",
        )

    # If resuming, seek after mpv starts (IPC can take a moment).
    if start_pos is not None:
        mpv_seek_absolute_with_retry(start_pos)
        try:
            mpv_set("pause", False)
        except Exception:
            pass

    now = {
        "input": item_or_text if not isinstance(item_or_text, dict) else raw,
        "url": raw,
        "title": title,
        "provider": provider,
        "stream": stream,
        "audio": audio,
        "started": int(time.time()),
        "mode": mode,
        "thumbnail": item.get("thumbnail"),
        "thumbnail_local": item.get("thumbnail_local"),
        "channel": item.get("channel"),
        **({"jellyfin_item_id": item.get("jellyfin_item_id")} if item.get("jellyfin_item_id") else {}),
        **({"jellyfin_media_source_id": item.get("jellyfin_media_source_id")} if item.get("jellyfin_media_source_id") else {}),
        **({"jellyfin_stream_mode": item.get("jellyfin_stream_mode")} if item.get("jellyfin_stream_mode") else {}),
        **({"jellyfin_stream_reason": item.get("jellyfin_stream_reason")} if item.get("jellyfin_stream_reason") else {}),
    }
    # Keep last resume position on the now_playing dict for close/resume UX.
    if start_pos is not None:
        try:
            now["resume_pos"] = float(start_pos)
        except Exception:
            pass


    state.set_now_playing(now)
    state.set_session_state("playing")
    state.set_pause_reason(None)
    state.set_session_position(float(start_pos) if start_pos is not None else 0.0)


    # Record play history
    state.history_add({
        "ts": int(time.time()),
        "mode": mode,
        "url": raw,
        "title": title,
        "provider": provider,
        **({"channel": item.get("channel")} if item.get("channel") else {}),
        **({"jellyfin_item_id": item.get("jellyfin_item_id")} if item.get("jellyfin_item_id") else {}),
        **({"jellyfin_media_source_id": item.get("jellyfin_media_source_id")} if item.get("jellyfin_media_source_id") else {}),
        **({
            "thumbnail": item.get("thumbnail"),
            "thumbnail_local": item.get("thumbnail_local"),
        } if item.get("thumbnail") else {}),
    })

    # Keep exactly one "up next" item primed in mpv so queue handoff avoids
    # stop/start transitions between plays.
    try:
        _prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass

    debug_log("player", f"play_item complete in {int((time.monotonic() - play_t0) * 1000)}ms title={title!r}")

    return now



def _handle_playback_idle_no_queue() -> None:
    """Transition app/UI state when playback has ended and queue is empty."""
    global _NATURAL_IDLE_RESET_UNTIL, _NATURAL_IDLE_ENSURE_TIMER
    now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    _emit_jellyfin_stopped_from_now(now)
    # Only clear now-playing when not in a user-closed resumable session.
    if getattr(state, "SESSION_STATE", "idle") != "closed":
        state.set_now_playing(None)
        state.set_session_state("idle")
        state.set_session_position(None)

    # Keep a short hold so status/orphan repair treats the idle Qt runtime as
    # intentional while the overlay dashboard reclaims the screen.
    try:
        settle_sec = float(os.getenv("RELAYTV_NATURAL_IDLE_SETTLE_SEC", "10.0"))
    except Exception:
        settle_sec = 10.0
    settle_sec = max(1.0, min(30.0, settle_sec))
    _NATURAL_IDLE_RESET_UNTIL = time.time() + settle_sec
    if _qt_shell_backend_enabled():
        if not _idle_dashboard_enabled():
            try:
                stop_mpv(restart_splash=False)
            except Exception:
                pass
            return
        if _NATURAL_IDLE_ENSURE_TIMER is not None:
            try:
                _NATURAL_IDLE_ENSURE_TIMER.cancel()
            except Exception:
                pass
        try:
            delay_sec = float(os.getenv("RELAYTV_NATURAL_IDLE_ENSURE_DELAY_SEC", "1.0"))
        except Exception:
            delay_sec = 1.0
        delay_sec = max(0.2, min(5.0, delay_sec))

        def _ensure_idle_after_natural_end() -> None:
            try:
                ensure_qt_shell_idle()
            except Exception:
                pass

        # Do not tear down the Qt shell on natural EOF. Leaving the idle player
        # surface alive avoids exposing the desktop before the overlay paints.
        _NATURAL_IDLE_ENSURE_TIMER = threading.Timer(delay_sec, _ensure_idle_after_natural_end)
        _NATURAL_IDLE_ENSURE_TIMER.daemon = True
        _NATURAL_IDLE_ENSURE_TIMER.start()
    else:
        start_splash_screen()


def _session_already_idle_without_queue() -> bool:
    """Return True once the app has already settled into queue-empty idle."""
    return (
        getattr(state, "SESSION_STATE", "idle") == "idle"
        and getattr(state, "NOW_PLAYING", None) is None
    )


def _playback_runtime_idle_or_ended() -> bool:
    """Return True when playback should be treated as ended/idle for auto-next."""
    global _PLAYBACK_IDLE_CANDIDATE_SINCE

    def _clear_idle_candidate() -> None:
        global _PLAYBACK_IDLE_CANDIDATE_SINCE
        _PLAYBACK_IDLE_CANDIDATE_SINCE = 0.0

    sess = str(getattr(state, "SESSION_STATE", "idle") or "idle").strip().lower()
    has_now = isinstance(getattr(state, "NOW_PLAYING", None), dict)
    if sess == "paused":
        _clear_idle_candidate()
        return False
    if playback_transitioning() or auto_next_transitioning():
        _clear_idle_candidate()
        return False
    if not _is_playing():
        if has_now and sess in ("playing", "paused"):
            _clear_idle_candidate()
            return False
        _clear_idle_candidate()
        return True
    if not _qt_shell_backend_enabled():
        _clear_idle_candidate()
        return False
    if native_qt_playback_explicitly_ended():
        _clear_idle_candidate()
        return True
    try:
        props = mpv_get_many(["core-idle", "eof-reached", "pause", "path", "time-pos", "duration"])
    except Exception:
        _clear_idle_candidate()
        return False
    core_idle = props.get("core-idle")
    eof_reached = props.get("eof-reached")
    paused = props.get("pause")
    path = str(props.get("path") or "").strip()
    if core_idle is not True or paused is True:
        _clear_idle_candidate()
        return False

    near_end = False
    try:
        pos = float(props.get("time-pos"))
    except Exception:
        pos = -1.0
    try:
        dur = float(props.get("duration"))
    except Exception:
        dur = -1.0
    if dur > 0.0 and pos >= 0.0:
        try:
            margin = float(os.getenv("RELAYTV_PLAYBACK_END_MARGIN_SEC", "1.5"))
        except Exception:
            margin = 1.5
        margin = max(0.2, min(10.0, margin))
        near_end = pos >= max(0.0, dur - margin)

    candidate = bool(eof_reached is True or not path or near_end)
    if not candidate:
        _clear_idle_candidate()
        return False

    now_ts = time.time()
    if _PLAYBACK_IDLE_CANDIDATE_SINCE <= 0.0:
        _PLAYBACK_IDLE_CANDIDATE_SINCE = now_ts
        return False
    try:
        confirm = float(os.getenv("RELAYTV_PLAYBACK_IDLE_CONFIRM_SEC", "1.0"))
    except Exception:
        confirm = 1.0
    confirm = max(0.0, min(10.0, confirm))
    if (now_ts - _PLAYBACK_IDLE_CANDIDATE_SINCE) < confirm:
        return False
    _clear_idle_candidate()
    return True

def _autoplay_next_worker():
    """
    Background thread: when mpv ends and queue has items, play next.
    """
    global _SESSION_RESTORE_ATTEMPTED
    while True:
        time.sleep(0.25)
        if not _SESSION_RESTORE_ATTEMPTED:
            _SESSION_RESTORE_ATTEMPTED = True
            if _restore_session_on_startup_if_needed():
                continue
        if time.time() < state.AUTO_NEXT_SUPPRESS_UNTIL:
            continue
        # If user intentionally closed the session, do not auto-advance.
        if getattr(state, "SESSION_STATE", "idle") == "closed":
            continue
        if not _playback_runtime_idle_or_ended():
            continue
        if playback_transitioning():
            continue
        with state.QUEUE_LOCK:
            has_queue = bool(state.QUEUE)
        if not has_queue:
            # Avoid flashing idle above video during native handoff gaps.
            if playback_transitioning() or auto_next_transitioning():
                continue
            if _session_already_idle_without_queue():
                continue
            _handle_playback_idle_no_queue()
            continue

        _set_auto_next_transition(True)
        try:
            advance_queue_playback(mode="auto_next", prefer_playlist_next=False)
        except QueueAdvanceEmptyError:
            _handle_playback_idle_no_queue()
        except QueueAdvanceSuppressedError:
            continue
        except Exception as exc:
            logger.warning("auto_next_failed error=%s", exc)
            state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 3.0
        finally:
            _set_auto_next_transition(False)




_AUTOPLAY_THREAD_STARTED = False
_AUTOPLAY_THREAD_LOCK = threading.Lock()
_SESSION_TRACKER_THREAD_STARTED = False
_SESSION_TRACKER_THREAD_LOCK = threading.Lock()
_QT_AUDIO_WATCHDOG_THREAD_STARTED = False
_QT_AUDIO_WATCHDOG_THREAD_LOCK = threading.Lock()
_QT_AUDIO_RECOVERY_LAST_TS = 0.0
_SESSION_RESTORE_ATTEMPTED = False
_AUTO_NEXT_TRANSITION_LOCK = threading.Lock()
_AUTO_NEXT_TRANSITION = False
_PLAYBACK_TRANSITION_LOCK = threading.Lock()
_PLAYBACK_TRANSITION_UNTIL = 0.0
_QT_RUNTIME_ACTIVE_LAST_TS = 0.0
_PLAYBACK_IDLE_CANDIDATE_SINCE = 0.0
_RECENT_JELLYFIN_STOP_LOCK = threading.Lock()
_RECENT_JELLYFIN_STOP: dict[str, object] = {
    "ts": 0.0,
    "item_id": "",
    "url_key": "",
    "media_source_id": "",
}


def _set_auto_next_transition(active: bool) -> None:
    global _AUTO_NEXT_TRANSITION
    with _AUTO_NEXT_TRANSITION_LOCK:
        _AUTO_NEXT_TRANSITION = bool(active)


def auto_next_transitioning() -> bool:
    with _AUTO_NEXT_TRANSITION_LOCK:
        return bool(_AUTO_NEXT_TRANSITION)


def _mark_playback_transition(window_sec: float | None = None) -> None:
    """Mark a short transition window so UI can avoid idle flashes during startup."""
    try:
        win = float(window_sec if window_sec is not None else os.getenv("RELAYTV_PLAYBACK_TRANSITION_SEC", "5.0"))
    except Exception:
        win = 5.0
    win = min(60.0, max(0.0, win))
    if win <= 0:
        return
    until = time.time() + win
    global _PLAYBACK_TRANSITION_UNTIL
    with _PLAYBACK_TRANSITION_LOCK:
        if until > float(_PLAYBACK_TRANSITION_UNTIL or 0.0):
            _PLAYBACK_TRANSITION_UNTIL = until


def _resolver_playback_transition_window_sec() -> float:
    """Resolver-backed starts need a longer idle-suppression window."""
    try:
        win = float(os.getenv("RELAYTV_RESOLVE_PLAYBACK_TRANSITION_SEC", "20.0"))
    except Exception:
        win = 20.0
    return min(60.0, max(5.0, win))


def playback_transitioning() -> bool:
    now_ts = time.time()
    with _PLAYBACK_TRANSITION_LOCK:
        return now_ts < float(_PLAYBACK_TRANSITION_UNTIL or 0.0)


def _jellyfin_complete_ratio() -> float:
    try:
        ratio = float(os.getenv("RELAYTV_JELLYFIN_COMPLETE_RATIO", "0.98"))
    except Exception:
        ratio = 0.98
    return min(0.999, max(0.0, ratio))


def _jellyfin_complete_remaining_sec() -> float:
    try:
        sec = float(os.getenv("RELAYTV_JELLYFIN_COMPLETE_REMAINING_SEC", "0"))
    except Exception:
        sec = 0.0
    return max(0.0, sec)


def _jellyfin_should_snap_complete(pos_ticks: int, run_ticks: int) -> bool:
    if run_ticks <= 0:
        return False
    pos = max(0, int(pos_ticks or 0))
    run = int(run_ticks)
    if pos >= run:
        return True
    if pos >= int(run * _jellyfin_complete_ratio()):
        return True
    remain_sec = _jellyfin_complete_remaining_sec()
    if remain_sec > 0.0:
        remain_ticks = int(remain_sec * 10_000_000)
        if (run - pos) <= max(0, remain_ticks):
            return True
    return False


def _jellyfin_played_percentage(pos_ticks: int, run_ticks: int | None = None) -> float | None:
    try:
        run = int(run_ticks) if run_ticks is not None else None
    except Exception:
        run = None
    if run is None or run <= 0:
        return None
    try:
        pct = (float(max(0, int(pos_ticks or 0))) / float(run)) * 100.0
    except Exception:
        return None
    if pct < 0.0:
        pct = 0.0
    if pct > 100.0:
        pct = 100.0
    return round(pct, 3)


def _canonical_jellyfin_item_id(raw: object) -> str:
    return str(raw or "").strip().lower().replace("-", "")


def _extract_jellyfin_media_source_id_from_url(raw_url: str | None) -> str:
    try:
        q = dict(parse_qsl(urlsplit(str(raw_url or "").strip()).query, keep_blank_values=True))
    except Exception:
        return ""
    return str(q.get("mediaSourceId") or q.get("MediaSourceId") or "").strip().lower()


def _canonical_jellyfin_url_key(raw_url: str | None) -> str:
    u = str(raw_url or "").strip()
    if not u:
        return ""
    try:
        parts = urlsplit(u)
        iid = str(jellyfin_receiver.extract_item_id_from_url(u) or "").strip().lower().replace("-", "")
        if iid:
            media_source_id = _extract_jellyfin_media_source_id_from_url(u)
            return f"{iid}::{media_source_id}" if media_source_id else iid
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))
    except Exception:
        return u


def remember_recent_jellyfin_stop(now: dict | None) -> None:
    if not isinstance(now, dict):
        return
    url = str(now.get("url") or "").strip()
    item_id = _canonical_jellyfin_item_id(
        now.get("jellyfin_item_id") or jellyfin_receiver.extract_item_id_from_url(url)
    )
    url_key = _canonical_jellyfin_url_key(url)
    media_source_id = str(
        now.get("jellyfin_media_source_id") or _extract_jellyfin_media_source_id_from_url(url) or ""
    ).strip().lower()
    if not item_id and not url_key:
        return
    with _RECENT_JELLYFIN_STOP_LOCK:
        _RECENT_JELLYFIN_STOP.update(
            {
                "ts": time.time(),
                "item_id": item_id,
                "url_key": url_key,
                "media_source_id": media_source_id,
            }
        )


def recent_jellyfin_stop_matches(
    *,
    item_id: str | None = None,
    source_url: str | None = None,
    media_source_id: str | None = None,
) -> bool:
    try:
        window_sec = max(0.0, float(os.getenv("RELAYTV_JELLYFIN_RECENT_STOP_SUPPRESS_SEC", "6.0")))
    except Exception:
        window_sec = 6.0
    if window_sec <= 0.0:
        return False
    with _RECENT_JELLYFIN_STOP_LOCK:
        last = dict(_RECENT_JELLYFIN_STOP)
    last_ts = float(last.get("ts") or 0.0)
    if last_ts <= 0.0 or (time.time() - last_ts) > window_sec:
        return False
    item_id_norm = _canonical_jellyfin_item_id(item_id)
    url_key = _canonical_jellyfin_url_key(source_url)
    media_source_norm = str(media_source_id or _extract_jellyfin_media_source_id_from_url(source_url) or "").strip().lower()
    last_item_id = _canonical_jellyfin_item_id(last.get("item_id"))
    last_url_key = str(last.get("url_key") or "").strip()
    last_media_source = str(last.get("media_source_id") or "").strip().lower()
    if item_id_norm and last_item_id and item_id_norm == last_item_id:
        if not media_source_norm or not last_media_source or media_source_norm == last_media_source:
            return True
    return bool(url_key and last_url_key and url_key == last_url_key)


def _jellyfin_stopped_payload_from_now(now: dict | None) -> dict | None:
    if not isinstance(now, dict):
        return None
    item_id = str(now.get("jellyfin_item_id") or "").strip()
    if not item_id:
        return None
    pos = None
    try:
        rp = now.get("resume_pos")
        pos = float(rp) if rp is not None else None
    except Exception:
        pos = None
    if pos is None:
        try:
            pos = float(getattr(state, "SESSION_POSITION", 0.0) or 0.0)
        except Exception:
            pos = 0.0
    payload = {
        "ItemId": item_id,
        "IsPaused": False,
    }
    pos_ticks = max(0, int((pos or 0.0) * 10_000_000))
    play_session_id = str(now.get("jellyfin_play_session_id") or "").strip()
    if play_session_id:
        payload["PlaySessionId"] = play_session_id
    media_source_id = str(now.get("jellyfin_media_source_id") or "").strip()
    if not media_source_id:
        try:
            qs = parse_qs(urlparse(str(now.get("url") or "")).query or "")
            media_source_id = str((qs.get("mediaSourceId") or qs.get("MediaSourceId") or [""])[0] or "").strip()
        except Exception:
            media_source_id = ""
    if media_source_id:
        payload["MediaSourceId"] = media_source_id
    try:
        d = now.get("duration")
        if d is not None:
            dur = float(d)
            if dur >= 0:
                run_ticks = int(dur * 10_000_000)
                payload["RunTimeTicks"] = run_ticks
                if _jellyfin_should_snap_complete(pos_ticks, run_ticks):
                    pos_ticks = run_ticks
                played_pct = _jellyfin_played_percentage(pos_ticks, run_ticks)
                if played_pct is not None:
                    payload["PlayedPercentage"] = played_pct
    except Exception:
        pass
    payload["PositionTicks"] = pos_ticks
    return payload


def _emit_jellyfin_stopped_from_now(now: dict | None) -> None:
    if not isinstance(now, dict):
        return
    if bool(now.get("jellyfin_stopped_emitted")):
        return
    remember_recent_jellyfin_stop(now)
    payload = _jellyfin_stopped_payload_from_now(now)
    if not isinstance(payload, dict):
        return
    try:
        updated = dict(now)
        updated["jellyfin_stopped_emitted"] = True
        state.set_now_playing(updated)
    except Exception:
        pass

    def _run() -> None:
        try:
            jellyfin_receiver.send_progress_payload_once(payload)
        except Exception:
            pass
        try:
            jellyfin_receiver.send_playback_stopped_once(payload)
        except Exception:
            pass

    try:
        threading.Thread(target=_run, daemon=True, name="relaytv-jellyfin-stopped-player").start()
    except Exception:
        pass


def _looks_like_jellyfin_media_url(url: str) -> bool:
    try:
        p = urlsplit(str(url or "").strip())
        path = (p.path or "").lower()
        if "/items/" in path or "/videos/" in path:
            return True
        host = (p.netloc or "").lower()
        return "jellyfin" in host
    except Exception:
        return False


def _is_generic_runtime_title(title: object, url: object) -> bool:
    t = str(title or "").strip()
    if not t:
        return True
    low = t.lower()
    if low in {"stream", "download", "video", "playback", "master", "master.m3u8", "main", "main.m3u8"}:
        return True
    u = str(url or "").strip()
    return bool(u and t == u)


def _hydrate_jellyfin_resume_metadata(now: dict) -> dict:
    out = dict(now)
    src = str(out.get("url") or out.get("input") or "").strip()
    item_id = str(out.get("jellyfin_item_id") or "").strip()
    if not src:
        return out
    if not item_id:
        try:
            item_id = str(jellyfin_receiver.extract_item_id_from_url(src) or "").strip()
        except Exception:
            item_id = ""
    if not item_id and not _looks_like_jellyfin_media_url(src):
        return out

    out["provider"] = "jellyfin"
    if item_id:
        out["jellyfin_item_id"] = item_id

    try:
        token = ""
        try:
            q = dict(parse_qsl(urlsplit(src).query, keep_blank_values=True))
            token = str(q.get("api_key") or q.get("ApiKey") or "").strip()
        except Exception:
            token = ""
        if not token:
            token = str(jellyfin_receiver.session_token() or jellyfin_receiver.api_key() or "").strip()
        origin = ""
        try:
            p = urlsplit(src)
            if p.scheme and p.netloc:
                origin = f"{p.scheme}://{p.netloc}"
        except Exception:
            origin = ""
        meta = (
            jellyfin_receiver.get_item_metadata(item_id, token_override=token, server_url_override=origin)
            if item_id
            else {}
        )
    except Exception:
        meta = {}

    if isinstance(meta, dict):
        title = str(meta.get("title") or "").strip()
        channel = str(meta.get("channel") or "").strip()
        thumb = str(meta.get("thumbnail") or "").strip()
        if title and _is_generic_runtime_title(out.get("title"), out.get("url")):
            out["title"] = title
        if channel and not out.get("channel"):
            out["channel"] = channel
        if thumb and not out.get("thumbnail"):
            out["thumbnail"] = thumb
        if out.get("resume_pos") is None:
            try:
                rp = meta.get("resume_pos")
                if rp is not None:
                    out["resume_pos"] = float(rp)
            except Exception:
                pass
    return out


def _restore_session_on_startup_if_needed() -> bool:
    """Best-effort: resume the persisted now-playing item before auto-next queue."""
    sess = getattr(state, "SESSION_STATE", "idle")
    now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    if sess not in ("playing", "paused") or not now:
        return False
    try:
        now = _hydrate_jellyfin_resume_metadata(now)
        state.set_now_playing(now)
    except Exception:
        pass
    inp = (now.get("input") or now.get("url") or "").strip()
    if not inp:
        return False

    start_pos = getattr(state, "SESSION_POSITION", None)
    if start_pos is None:
        start_pos = now.get("resume_pos")

    try:
        resumed = play_item(
            now,
            use_resolver=True,
            cec=False,
            clear_queue=False,
            mode="startup_resume",
            start_pos=(float(start_pos) if start_pos is not None else None),
        )
        if sess == "paused":
            try:
                mpv_set("pause", True)
            except Exception:
                pass
            state.set_session_state("paused")
        else:
            state.set_session_state("playing")
            state.set_pause_reason(None)
        state.set_now_playing(resumed)
        return True
    except Exception as e:
        logger.warning("startup_session_restore_failed error=%s", e)
        return False


def _repair_orphan_runtime_playback(props: dict[str, Any] | None = None) -> bool:
    """Stop runtime playback if media is active but RelayTV lost session state."""
    if playback_transitioning() or auto_next_transitioning():
        return False
    if natural_idle_reset_holding():
        return False
    if getattr(state, "SESSION_STATE", "idle") == "closed":
        return False
    try:
        explicit_stop_hold = float(getattr(state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0) or 0.0) > (time.time() + 60.0)
    except Exception:
        explicit_stop_hold = False
    if explicit_stop_hold:
        return False
    if isinstance(getattr(state, "NOW_PLAYING", None), dict):
        return False
    with state.QUEUE_LOCK:
        if state.QUEUE:
            return False

    if not isinstance(props, dict):
        try:
            props = mpv_get_many(["path", "core-idle", "eof-reached"])
        except Exception:
            return False

    path = str(props.get("path") or "").strip()
    core_idle = props.get("core-idle")
    eof_reached = props.get("eof-reached")
    if not path:
        return False
    if core_idle is True:
        return False
    if eof_reached is True:
        return False

    logger.warning("orphan_runtime_playback_reset path=%s", path)
    try:
        stop_mpv(restart_splash=True)
    except Exception:
        return False
    try:
        state.set_session_state("idle")
    except Exception:
        pass
    try:
        state.set_session_position(None)
    except Exception:
        pass
    try:
        state.set_pause_reason(None)
    except Exception:
        pass
    return True


def natural_idle_reset_holding() -> bool:
    try:
        return float(_NATURAL_IDLE_RESET_UNTIL or 0.0) > time.time()
    except Exception:
        return False


def _session_tracker_worker() -> None:
    """Continuously persist playback position/state so reboots can resume accurately."""
    while True:
        time.sleep(2)
        if not _is_playing():
            continue
        try:
            props = mpv_get_many(["time-pos", "pause", "playlist-pos", "playlist-count", "path", "core-idle", "eof-reached"])
            _consume_mpv_queued_next_if_started(props)
            now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
            if not now:
                if _repair_orphan_runtime_playback(props):
                    continue
                _prime_mpv_up_next_from_queue()
                continue
            pos = props.get("time-pos")
            paused = bool(props.get("pause"))
            if pos is not None:
                pos_f = float(pos)
                updated = dict(now)
                updated["resume_pos"] = pos_f
                state.set_now_playing(updated)
                state.set_session_position(pos_f)
                state.set_session_state("paused" if paused else "playing")
            if not paused:
                state.set_pause_reason(None)
            _prime_mpv_up_next_from_queue()
        except Exception:
            continue


def _qt_audio_watchdog_tick(now_ts: float | None = None) -> bool:
    """Best-effort periodic audio recovery for Qt runtime playback."""
    global _QT_AUDIO_RECOVERY_LAST_TS
    if not _qt_shell_backend_enabled():
        return False
    if playback_transitioning() or auto_next_transitioning():
        return False
    if not _is_playing():
        return False
    settings = getattr(state, "get_settings", lambda: {})()
    if _audio_device_explicitly_configured(settings):
        return False
    if _audio_output_ready():
        return False

    now = float(now_ts) if now_ts is not None else time.monotonic()
    try:
        cooldown = max(0.0, float(os.getenv("RELAYTV_QT_AUDIO_RECOVERY_COOLDOWN", "8")))
    except Exception:
        cooldown = 8.0
    if (now - float(_QT_AUDIO_RECOVERY_LAST_TS)) < cooldown:
        return False

    _recover_audio_output_if_needed(settings)
    _QT_AUDIO_RECOVERY_LAST_TS = now
    return True


def _qt_audio_watchdog_worker() -> None:
    while True:
        try:
            interval = max(0.25, float(os.getenv("RELAYTV_QT_AUDIO_WATCHDOG_INTERVAL", "2.0")))
        except Exception:
            interval = 2.0
        time.sleep(interval)
        try:
            _qt_audio_watchdog_tick()
        except Exception:
            continue


def start_qt_audio_watchdog_worker() -> None:
    global _QT_AUDIO_WATCHDOG_THREAD_STARTED
    with _QT_AUDIO_WATCHDOG_THREAD_LOCK:
        if _QT_AUDIO_WATCHDOG_THREAD_STARTED:
            return
        threading.Thread(target=_qt_audio_watchdog_worker, daemon=True).start()
        _QT_AUDIO_WATCHDOG_THREAD_STARTED = True


def start_autoplay_worker() -> None:
    global _AUTOPLAY_THREAD_STARTED
    with _AUTOPLAY_THREAD_LOCK:
        if _AUTOPLAY_THREAD_STARTED:
            return
        threading.Thread(target=_autoplay_next_worker, daemon=True).start()
        _AUTOPLAY_THREAD_STARTED = True


def start_session_tracker_worker() -> None:
    global _SESSION_TRACKER_THREAD_STARTED
    with _SESSION_TRACKER_THREAD_LOCK:
        if _SESSION_TRACKER_THREAD_STARTED:
            return
        threading.Thread(target=_session_tracker_worker, daemon=True).start()
        _SESSION_TRACKER_THREAD_STARTED = True


def restart_current(apply_mode: str | None = None) -> dict | None:
    """Restart current playback to apply settings. Best-effort."""
    try:
        if not state.NOW_PLAYING:
            return None
        inp = (state.NOW_PLAYING.get("input") or state.NOW_PLAYING.get("url") or "").strip()
        if not inp:
            return None
        # capture position if possible
        pos = None
        try:
            if is_playing():
                pos = mpv_get("time-pos")
        except Exception:
            pos = None
        stop_mpv()
        # Use settings-driven video mode by default; apply_mode can force x11/drm.
        if apply_mode:
            os.environ["RELAYTV_VIDEO_MODE"] = apply_mode
        now = play_item(inp, use_resolver=True, cec=False, clear_queue=False, mode="resume", start_pos=(float(pos) if pos is not None else None))
        return now
    except Exception as e:
        logger.warning("restart_current_failed error=%s", e)
        return None
