# SPDX-License-Identifier: GPL-3.0-only
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import (
    StreamingResponse,
    HTMLResponse,
    JSONResponse,
    Response,
)
from pydantic import BaseModel

import time
import datetime
import asyncio
import json as _json
import math
import os
import re
import threading
import socket
import urllib.request
from urllib.parse import urlencode, urlsplit, urlunsplit

from .. import config, discovery_mdns, playback_service, player, public_media, resolver, state, upload_store, video_profile, x11_overlay
from ..debug import debug_log, get_logger
from ..config import env_choice, runtime_config
from ..integrations import jellyfin_receiver, jellyfin_service
from ..thumb_cache import ensure_cached_sync, attach_local_thumbnail, thumb_id, local_rel_path
from .app_info import router as app_info_router
from .assets import _resolve_static_asset, router as assets_router
from .capabilities import (
    notifications_capabilities as notifications_capabilities,
    router as capabilities_router,
    runtime_capabilities as runtime_capabilities,
)
from .devices import router as devices_router
from .health import router as health_router
from .jellyfin import (
    JellyfinAudioSelectReq as JellyfinAudioSelectReq,
    JellyfinCommandReq as JellyfinCommandReq,
    JellyfinConnectReq as JellyfinConnectReq,
    JellyfinItemActionReq as JellyfinItemActionReq,
    JellyfinSubtitleSelectReq as JellyfinSubtitleSelectReq,
    _require_jellyfin_catalog_ready as _require_jellyfin_catalog_ready,
    jellyfin_audio_options as jellyfin_audio_options,
    jellyfin_audio_select as jellyfin_audio_select,
    jellyfin_catalog_cache_clear as jellyfin_catalog_cache_clear,
    jellyfin_integration_command as jellyfin_integration_command,
    jellyfin_integration_connect as jellyfin_integration_connect,
    jellyfin_integration_disconnect as jellyfin_integration_disconnect,
    jellyfin_integration_heartbeat as jellyfin_integration_heartbeat,
    jellyfin_integration_progress_snapshot as jellyfin_integration_progress_snapshot,
    jellyfin_integration_push as jellyfin_integration_push,
    jellyfin_integration_register as jellyfin_integration_register,
    jellyfin_integration_stopped as jellyfin_integration_stopped,
    jellyfin_integration_stopped_snapshot as jellyfin_integration_stopped_snapshot,
    jellyfin_home as jellyfin_home,
    jellyfin_integration_status as jellyfin_integration_status,
    jellyfin_item_action as jellyfin_item_action,
    jellyfin_item_adjacent as jellyfin_item_adjacent,
    jellyfin_item_detail as jellyfin_item_detail,
    jellyfin_movies as jellyfin_movies,
    jellyfin_search as jellyfin_search,
    jellyfin_subtitle_options as jellyfin_subtitle_options,
    jellyfin_subtitle_select as jellyfin_subtitle_select,
    jellyfin_tv_series as jellyfin_tv_series,
    jellyfin_tv_series_episodes as jellyfin_tv_series_episodes,
    jellyfin_tv_series_play_all as jellyfin_tv_series_play_all,
    jellyfin_tv_series_seasons as jellyfin_tv_series_seasons,
    router as jellyfin_router,
)
from .playback import (
    MuteReq as MuteReq,
    PlayAtReq as PlayAtReq,
    PlayNowReq as PlayNowReq,
    PlayReq as PlayReq,
    PlayTemporaryReq as PlayTemporaryReq,
    SeekAbsReq as SeekAbsReq,
    SeekReq as SeekReq,
    VolumeReq as VolumeReq,
    _preserve_current_to_queue_front as _preserve_current_to_queue_front,
    clear_now_playing as clear_now_playing,
    clear_resumable_session as clear_resumable_session,
    close as close,
    mute as mute,
    next_track as next_track,
    pause as pause,
    play as play,
    play_at as play_at,
    playback_play as playback_play,
    playback_state as playback_state,
    playback_toggle as playback_toggle,
    play_now as play_now,
    play_temporary as play_temporary,
    play_temporary_cancel as play_temporary_cancel,
    previous as previous,
    resume as resume,
    resume_session as resume_session,
    seek as seek,
    seek_abs as seek_abs,
    share as share,
    smart as smart,
    stop as stop,
    toggle_pause as toggle_pause,
    volume as volume,
)
from .playback import router as playback_router
from .postlive import router as postlive_router
from .queue import router as queue_router
from .settings import (
    SettingsReq as SettingsReq,
    YouTubeCookiesUploadReq as YouTubeCookiesUploadReq,
    clear_youtube_cookies as clear_youtube_cookies,
    get_settings as get_settings,
    router as settings_router,
    update_settings as update_settings,
    upload_youtube_cookies as upload_youtube_cookies,
)
from .snapshots import router as snapshots_router
from .status import router as status_router
from .ui import router as ui_router
from .uploads import (
    ingest_media as ingest_media,
    ingest_media_enqueue as ingest_media_enqueue,
    ingest_media_play as ingest_media_play,
    router as uploads_router,
)

router = APIRouter()
router.include_router(app_info_router)
router.include_router(assets_router)
router.include_router(capabilities_router)
router.include_router(devices_router)
router.include_router(health_router)
router.include_router(jellyfin_router)
router.include_router(playback_router)
router.include_router(postlive_router)
router.include_router(queue_router)
router.include_router(settings_router)
router.include_router(snapshots_router)
router.include_router(status_router)
router.include_router(ui_router)
router.include_router(uploads_router)
logger = get_logger("routes")


def _env_choice(name: str) -> bool | None:
    return env_choice(name, extended=True)


def _idle_weather_proxy_url(settings_payload: dict | None) -> str:
    settings = settings_payload if isinstance(settings_payload, dict) else {}
    weather = settings.get("weather") if isinstance(settings, dict) else {}
    weather = weather if isinstance(weather, dict) else {}
    try:
        lat = float(weather.get("latitude"))
    except Exception:
        lat = 40.7128
    try:
        lon = float(weather.get("longitude"))
    except Exception:
        lon = -74.0060
    units = "celsius" if str(weather.get("units") or "").strip().lower() == "metric" else "fahrenheit"
    wind_units = "kmh" if units == "celsius" else "mph"
    forecast_days = 7
    try:
        requested_days = int(weather.get("forecast_days") or 7)
        if requested_days in (1, 3, 7):
            forecast_days = requested_days
    except Exception:
        pass
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "current": "temperature_2m,weather_code,is_day,apparent_temperature,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max",
        "temperature_unit": units,
        "wind_speed_unit": wind_units,
        "forecast_days": str(forecast_days),
    }
    return "https://api.open-meteo.com/v1/forecast?" + urlencode(params)


# =========================
# API Models
# =========================

class OverlayReq(BaseModel):
    text: str | None = None
    duration: float = 5.0
    position: str = "top-left"
    style: dict | None = None
    image_url: str | None = None
    level: str = "info"
    icon: str | None = None
    link_url: str | None = None
    link_text: str | None = None


class OverlayClientStateReq(BaseModel):
    state: str
    reason: str | None = None
    client_event: str | None = None
    client_reason: str | None = None
    active_toasts: int | None = None


def _overlay_osd_debug_enabled() -> bool:
    v = (os.getenv("RELAYTV_OVERLAY_OSD_DEBUG") or os.getenv("OVERLAY_OSD_DEBUG") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _playback_notification_display_sec() -> float:
    """Default time standard playback notifications stay visible."""
    try:
        sec = float(os.getenv("RELAYTV_PLAYBACK_NOTIFY_DISPLAY_SEC", "3.5"))
    except Exception:
        sec = 3.5
    return max(0.8, sec)


def _playback_notification_fade_ms() -> int:
    """Toast enter/exit fade duration for overlay playback notifications."""
    try:
        ms = int(float(os.getenv("RELAYTV_PLAYBACK_NOTIFY_FADE_MS", "240")))
    except Exception:
        ms = 240
    return max(80, ms)


def _overlay_allow_images() -> bool:
    override = (os.getenv("RELAYTV_OVERLAY_TOAST_IMAGES") or "").strip().lower()
    if override in ("1", "true", "yes", "on"):
        return True
    if override in ("0", "false", "no", "off"):
        return False
    try:
        profile = video_profile.get_profile() or {}
        if str(profile.get("decode_profile") or "").strip().lower() == "arm_safe":
            software_override = (os.getenv("RELAYTV_QT_OVERLAY_SOFTWARE") or "").strip().lower()
            if software_override in ("0", "false", "no", "off"):
                return False
            return True
    except Exception:
        pass
    return True


def _overlay_prefers_native_qt_toast(image_url: str | None = None) -> bool:
    if not _qt_shell_runtime_running():
        return False
    override = _env_choice("RELAYTV_QT_NATIVE_TOASTS")
    if override is not None:
        return bool(override)
    overlay_enabled = _env_choice("RELAYTV_QT_OVERLAY_ENABLED")
    if overlay_enabled is None:
        overlay_enabled = True
    if overlay_enabled:
        return False
    try:
        profile = video_profile.get_profile() or {}
        return str(profile.get("decode_profile") or "").strip().lower() == "arm_safe"
    except Exception:
        return False


def _native_qt_toast_image_url(image_url: str | None) -> str | None:
    text = str(image_url or "").strip()
    if not text:
        return None
    if not _overlay_prefers_native_qt_toast(text):
        return text
    lowered = text.lower()
    if lowered.startswith("data:image/") or text.startswith("/"):
        return text
    if not lowered.startswith(("http://", "https://")):
        return text
    try:
        item = {"thumbnail": text}
        attach_local_thumbnail(item)
        local_thumb = str(item.get("thumbnail_local") or "").strip()
        if local_thumb:
            return local_thumb
        tid = thumb_id(text)
        if ensure_cached_sync(tid):
            return local_rel_path(tid)
    except Exception:
        pass
    return text


def _overlay_debug_bg_css() -> str:
    """Optional debug tint for diagnosing overlay visibility/z-order."""
    raw = (os.getenv("RELAYTV_OVERLAY_DEBUG_BG") or "").strip()
    if not raw:
        return "transparent"
    # Allow common CSS color syntaxes while blocking unsafe injection.
    if not re.fullmatch(r"[#(),.%\w\s-]{1,80}", raw):
        return "transparent"
    return raw


def _x11_mode_notifications() -> bool:
    if (os.getenv("RELAYTV_X11_OVERLAY") or "0").strip().lower() in ("1", "true", "yes", "on"):
        return True

    env_mode = (
        runtime_config.snapshot().raw("RELAYTV_VIDEO_MODE", "")
        or os.getenv("RELAYTV_MODE", "")
        or ""
    ).strip().lower()
    settings_mode = (
        (getattr(state, "get_settings", lambda: {})().get("video_mode"))
        or ""
    ).strip().lower()
    mode = (
        env_mode
        or settings_mode
    )
    if mode == "x11":
        return True
    if mode == "drm":
        return False

    # auto/unknown: rely on DISPLAY first; XDG_SESSION_TYPE can be blank in containers.
    xdg = os.getenv("XDG_SESSION_TYPE", "").strip().lower()
    if xdg == "wayland":
        return False
    try:
        return bool(getattr(player, "_has_x11_display", lambda: bool((os.getenv("DISPLAY") or "").strip()))())
    except Exception:
        return bool((os.getenv("DISPLAY") or "").strip())


def _qt_shell_runtime_running() -> bool:
    try:
        return bool(getattr(player, "_qt_shell_running", lambda: False)())
    except Exception:
        return False


def _host_session_type() -> str:
    return (
        os.getenv("RELAYTV_HOST_SESSION_TYPE")
        or os.getenv("XDG_SESSION_TYPE")
        or ""
    ).strip().lower()


def _display_session_available() -> bool:
    try:
        has_x11 = bool(getattr(player, "_has_x11_display", lambda: bool((os.getenv("DISPLAY") or "").strip()))())
    except Exception:
        has_x11 = bool((os.getenv("DISPLAY") or "").strip())
    try:
        has_wayland = bool(getattr(player, "_has_wayland_display", lambda: bool((os.getenv("WAYLAND_DISPLAY") or "").strip()))())
    except Exception:
        has_wayland = bool((os.getenv("WAYLAND_DISPLAY") or "").strip())
    if has_x11 or has_wayland:
        return True
    # Permissive fallback for CI/tests/containerized sessions where socket
    # probes can be unavailable but session env signals are authoritative.
    if (os.getenv("DISPLAY") or "").strip():
        return True
    if (os.getenv("WAYLAND_DISPLAY") or "").strip():
        return True
    return _host_session_type() in ("x11", "wayland")


def _overlay_only_notifications_mode() -> bool:
    return _x11_mode_notifications() or _qt_shell_runtime_running()


def _headless_runtime() -> bool:
    return (not _display_session_available()) and (not _qt_shell_runtime_running())


def _visual_runtime_mode() -> str:
    """Runtime visual mode: qt_shell | x11_display | wayland_display | headless."""
    if _qt_shell_runtime_running():
        return "qt_shell"
    try:
        has_x11 = bool(getattr(player, "_has_x11_display", lambda: bool((os.getenv("DISPLAY") or "").strip()))())
    except Exception:
        has_x11 = bool((os.getenv("DISPLAY") or "").strip())
    try:
        has_wayland = bool(getattr(player, "_has_wayland_display", lambda: bool((os.getenv("WAYLAND_DISPLAY") or "").strip()))())
    except Exception:
        has_wayland = bool((os.getenv("WAYLAND_DISPLAY") or "").strip())
    if has_wayland and not has_x11:
        return "wayland_display"
    if has_x11:
        return "x11_display"
    if _host_session_type() == "wayland":
        return "wayland_display"
    if _display_session_available():
        return "x11_display"
    return "headless"


def _native_qt_notification_runtime_enabled() -> bool:
    try:
        return _overlay_prefers_native_qt_toast(None)
    except Exception:
        return False


def _notification_strategy() -> str:
    """Runtime notification routing mode: native_qt | overlay | headless."""
    if _headless_runtime():
        return "headless"
    if _native_qt_notification_runtime_enabled():
        return "native_qt"
    return "overlay"


def _notifications_available() -> tuple[bool, str]:
    strategy = _notification_strategy()
    if strategy == "headless":
        return False, "headless_runtime"
    if strategy == "native_qt":
        return True, "native_qt"
    return True, "overlay"


def _native_qt_overlay_compat_metadata() -> dict[str, object]:
    native_idle_override = _env_choice("RELAYTV_QT_NATIVE_IDLE")
    native_toasts_override = _env_choice("RELAYTV_QT_NATIVE_TOASTS")
    return {
        "native_qt_idle_deprecated": True,
        "native_qt_idle_status": "override_only",
        "native_qt_idle_override_enabled": bool(native_idle_override),
        "native_qt_toasts_deprecated": True,
        "native_qt_toasts_status": "override_only",
        "native_qt_toasts_override_enabled": bool(native_toasts_override),
    }


def _notification_capabilities() -> dict:
    strategy = _notification_strategy()
    available, reason = _notifications_available()
    visual_runtime_mode = _visual_runtime_mode()
    try:
        subscribers = len(_X11_OVERLAY_SUBS)
    except Exception:
        subscribers = 0
    overlay_info = state.get_overlay_delivery_state_info() if hasattr(state, "get_overlay_delivery_state_info") else {}
    if hasattr(state, "update_overlay_delivery_state"):
        overlay_state = str(overlay_info.get("overlay_delivery_state") or "")
        overlay_age = overlay_info.get("overlay_delivery_last_client_event_age_sec")
        if not available:
            overlay_info = state.update_overlay_delivery_state("headless", reason, client_event="server", client_reason=reason)
        elif strategy == "native_qt":
            overlay_info = state.update_overlay_delivery_state("connected", "native_qt_ready", client_event="server", client_reason="native_qt_ready")
        elif subscribers <= 0:
            overlay_info = state.update_overlay_delivery_state("disconnected", "no_subscribers", client_event="server", client_reason="no_subscribers")
        elif isinstance(overlay_age, (int, float)) and overlay_age > 35.0 and overlay_state in ("connected", "displaying", "draining"):
            overlay_info = state.update_overlay_delivery_state("stale", "client_heartbeat_missing", client_event="server", client_reason="client_heartbeat_missing")
        elif overlay_state in ("", "disconnected", "headless"):
            overlay_info = state.update_overlay_delivery_state("connected", "subscriber_connected", client_event="server", client_reason="subscriber_connected")
    return {
        "visual_runtime_mode": visual_runtime_mode,
        "notification_strategy": strategy,
        "idle_notifications_enabled": _idle_notifications_enabled_for_player(),
        "notifications_available": available,
        "notifications_reason": reason,
        "overlay_subscribers": max(0, int(subscribers)),
        "notifications_deliverable": bool(available and (strategy == "native_qt" or subscribers > 0)),
        "headless_runtime": _headless_runtime(),
        "overlay_only_notifications": _overlay_only_notifications_mode(),
        **_native_qt_overlay_compat_metadata(),
        **overlay_info,
    }


def _runtime_capabilities(*, playing: bool | None = None) -> dict:
    qt_backend = False
    qt_running = False
    try:
        qt_backend = bool(getattr(player, "_qt_shell_backend_enabled", lambda: False)())
        qt_running = bool(getattr(player, "_qt_shell_running", lambda: False)())
    except Exception:
        qt_backend = False
        qt_running = False

    qt_mode_configured = str(getattr(player, "qt_runtime_mode_configured", lambda: "auto")() or "auto")
    qt_mode_effective = str(getattr(player, "qt_runtime_mode_effective", lambda: "embed")() or "embed")

    qt_shell_pid = None
    mpv_pid = None
    try:
        qproc = getattr(player, "QT_SHELL_PROC", None)
        if qproc is not None and getattr(qproc, "poll", lambda: 1)() is None:
            qt_shell_pid = int(getattr(qproc, "pid", 0) or 0) or None
    except Exception:
        qt_shell_pid = None
    try:
        mproc = getattr(player, "MPV_PROC", None)
        if mproc is not None and getattr(mproc, "poll", lambda: 1)() is None:
            mpv_pid = int(getattr(mproc, "pid", 0) or 0) or None
    except Exception:
        mpv_pid = None

    if playing is None:
        playing = bool(player.is_playing())
    display_session_available = _display_session_available()
    mpv_ipc_path = str(getattr(player, "IPC_PATH", os.getenv("MPV_IPC_PATH", "/tmp/mpv.sock")) or "/tmp/mpv.sock")
    ipc_socket_exists = os.path.exists(mpv_ipc_path)
    qt_overlay_url = (os.getenv("RELAYTV_QT_OVERLAY_URL") or "http://127.0.0.1:8787/x11/overlay").strip()
    qt_shell_module = (os.getenv("RELAYTV_QT_SHELL_MODULE") or "relaytv_app.qt_shell_app").strip()
    native_qt_ready = False
    if qt_backend and qt_mode_effective != "external_mpv":
        try:
            native_qt_ready = bool(
                getattr(player, "_qt_runtime_active", lambda **_: False)(
                    require_active_session=False
                )
            )
        except Exception:
            native_qt_ready = False
    if qt_backend and qt_mode_effective == "external_mpv":
        backend_ready = (mpv_pid is not None and ipc_socket_exists)
    else:
        backend_ready = (
            (qt_running and (ipc_socket_exists or native_qt_ready))
            if qt_backend
            else (bool(playing) and ipc_socket_exists)
        )
    if qt_backend and qt_mode_effective == "external_mpv" and mpv_pid is not None:
        player_runtime_engine = "qt_external_mpv"
    elif qt_running:
        player_runtime_engine = "qt_shell"
    elif mpv_pid is not None:
        player_runtime_engine = "mpv_process"
    else:
        player_runtime_engine = "none"
    configured_player_backend = ("qt" if qt_backend else "mpv")
    if qt_backend:
        if qt_mode_effective == "external_mpv":
            backend_runtime_mismatch = (player_runtime_engine not in ("qt_external_mpv", "qt_shell", "none"))
        else:
            backend_runtime_mismatch = (player_runtime_engine in ("mpv_process", "qt_external_mpv"))
    else:
        backend_runtime_mismatch = (player_runtime_engine in ("qt_shell", "qt_external_mpv"))

    caps = _notification_capabilities()
    profile: dict[str, object] = {}
    try:
        profile = dict(video_profile.get_profile() or {})
    except Exception:
        profile = {}
    qt_external_runtime = {}
    try:
        qt_external_runtime = dict(getattr(player, "qt_external_runtime_state", lambda: {})() or {})
    except Exception:
        qt_external_runtime = {}
    qt_runtime_telemetry = {}
    try:
        qt_runtime_telemetry = dict(getattr(player, "qt_shell_runtime_telemetry", lambda **_: {})() or {})
    except Exception:
        qt_runtime_telemetry = {}
    try:
        qt_shell_supervisor = dict(getattr(player, "qt_shell_supervisor_state", lambda: {})() or {})
    except Exception:
        qt_shell_supervisor = {}
    native_qt_selected = bool(qt_runtime_telemetry.get("selected"))
    native_qt_available = bool(qt_runtime_telemetry.get("available"))
    native_qt_freshness = str(qt_runtime_telemetry.get("freshness") or "")
    native_qt_playback_ready = any(
        qt_runtime_telemetry.get(key) is True
        for key in (
            "mpv_runtime_playback_active",
            "mpv_runtime_stream_loaded",
            "mpv_runtime_playback_started",
        )
    )
    if qt_backend and qt_mode_effective != "external_mpv":
        backend_ready = (qt_running and (native_qt_ready or native_qt_available or native_qt_playback_ready))
    native_qt_telemetry_source = "none"
    if qt_backend and qt_mode_effective != "external_mpv":
        if native_qt_selected and native_qt_available:
            native_qt_telemetry_source = "qt_runtime"
        elif native_qt_selected and native_qt_freshness == "stale":
            native_qt_telemetry_source = "qt_runtime_stale"
    playback_runtime_info = state.get_playback_runtime_state_info() if hasattr(state, "get_playback_runtime_state_info") else {}
    resolver_runtime_info: dict[str, object] = {}
    try:
        resolver_runtime_info = dict(
            getattr(resolver, "get_resolver_runtime_state", lambda: {})() or {}
        )
    except Exception:
        resolver_runtime_info = {}
    return {
        "player_backend": ("qt" if qt_backend else "mpv"),
        "configured_player_backend": configured_player_backend,
        "qt_runtime_mode_configured": qt_mode_configured,
        "qt_runtime_mode_effective": qt_mode_effective,
        "player_runtime_engine": player_runtime_engine,
        "backend_runtime_mismatch": backend_runtime_mismatch,
        "qt_shell_running": qt_running,
        "qt_shell_pid": qt_shell_pid,
        "mpv_pid": mpv_pid,
        "display_session_available": display_session_available,
        "x11_overlay_mode": _x11_mode_notifications(),
        "overlay_only_notifications": bool(caps.get("overlay_only_notifications")),
        "headless_runtime": bool(caps.get("headless_runtime")),
        "visual_runtime_mode": str(caps.get("visual_runtime_mode") or _visual_runtime_mode()),
        "notification_strategy": str(caps.get("notification_strategy") or _notification_strategy()),
        "notifications_available": bool(caps.get("notifications_available")),
        "notifications_reason": str(caps.get("notifications_reason") or ""),
        "overlay_subscribers": int(caps.get("overlay_subscribers") or 0),
        "native_qt_idle_deprecated": bool(caps.get("native_qt_idle_deprecated")),
        "native_qt_idle_status": str(caps.get("native_qt_idle_status") or ""),
        "native_qt_idle_override_enabled": bool(caps.get("native_qt_idle_override_enabled")),
        "native_qt_toasts_deprecated": bool(caps.get("native_qt_toasts_deprecated")),
        "native_qt_toasts_status": str(caps.get("native_qt_toasts_status") or ""),
        "native_qt_toasts_override_enabled": bool(caps.get("native_qt_toasts_override_enabled")),
        **playback_runtime_info,
        "notifications_deliverable": bool(caps.get("notifications_deliverable")),
        "mpv_ipc_path": mpv_ipc_path,
        "ipc_socket_exists": ipc_socket_exists,
        "qt_overlay_url": qt_overlay_url,
        "qt_shell_module": qt_shell_module,
        "backend_ready": backend_ready,
        "host_session_type": _host_session_type(),
        "qt_external_last_launch_ts": float(qt_external_runtime.get("last_launch_ts") or 0.0),
        "qt_external_last_fallback_to_x11": bool(qt_external_runtime.get("fallback_to_x11")),
        "qt_external_fallback_reason": str(qt_external_runtime.get("fallback_reason") or ""),
        "qt_external_last_mode_args": list(qt_external_runtime.get("mode_args") or []),
        "qt_external_video_health_last_ok": qt_external_runtime.get("video_health_last_ok"),
        "qt_external_video_health_last_ts": float(qt_external_runtime.get("video_health_last_ts") or 0.0),
        "qt_external_video_health_fail_count": int(qt_external_runtime.get("video_health_fail_count") or 0),
        "qt_shell_supervisor_enabled": bool(qt_shell_supervisor.get("enabled", True)),
        "qt_shell_supervisor_running": bool(qt_shell_supervisor.get("running")),
        "qt_shell_display_socket_available": bool(qt_shell_supervisor.get("display_socket_available")),
        "qt_shell_display_ready": bool(qt_shell_supervisor.get("display_ready")),
        "qt_shell_display_ready_since": float(qt_shell_supervisor.get("display_ready_since") or 0.0),
        "qt_shell_display_boot_grace_remaining_sec": float(
            qt_shell_supervisor.get("display_boot_grace_remaining_sec") or 0.0
        ),
        "qt_shell_supervisor_last_check_ts": float(qt_shell_supervisor.get("last_check_ts") or 0.0),
        "qt_shell_supervisor_last_action": str(qt_shell_supervisor.get("last_action") or ""),
        "qt_shell_supervisor_last_reason": str(qt_shell_supervisor.get("last_reason") or ""),
        "qt_shell_supervisor_last_restart_ts": float(qt_shell_supervisor.get("last_restart_ts") or 0.0),
        "qt_shell_supervisor_restart_count": int(qt_shell_supervisor.get("restart_count") or 0),
        "native_qt_telemetry_contract_version": str(qt_runtime_telemetry.get("contract_version") or "v1"),
        "native_qt_telemetry_source": native_qt_telemetry_source,
        "native_qt_telemetry_selected": bool(qt_runtime_telemetry.get("selected")),
        "native_qt_telemetry_available": bool(qt_runtime_telemetry.get("available")),
        "native_qt_telemetry_freshness": str(qt_runtime_telemetry.get("freshness") or "missing"),
        "native_qt_telemetry_age_sec": qt_runtime_telemetry.get("age_sec"),
        "native_qt_telemetry_path": str(qt_runtime_telemetry.get("path") or ""),
        "native_qt_telemetry_runtime": str(qt_runtime_telemetry.get("runtime") or ""),
        "native_qt_telemetry_alive": bool(qt_runtime_telemetry.get("alive")),
        "native_qt_telemetry_control_file": str(qt_runtime_telemetry.get("control_file") or ""),
        "native_qt_telemetry_last_control_action": str(qt_runtime_telemetry.get("last_control_action") or ""),
        "native_qt_telemetry_last_control_request_id": str(qt_runtime_telemetry.get("last_control_request_id") or ""),
        "native_qt_telemetry_last_control_handled": qt_runtime_telemetry.get("last_control_handled"),
        "native_qt_telemetry_last_control_ok": qt_runtime_telemetry.get("last_control_ok"),
        "native_qt_telemetry_last_control_error": str(qt_runtime_telemetry.get("last_control_error") or ""),
        "native_qt_overlay_enabled": qt_runtime_telemetry.get("qt_overlay_enabled"),
        "native_qt_overlay_software_mode": qt_runtime_telemetry.get("qt_overlay_software_mode"),
        "native_qt_overlay_load_ok": qt_runtime_telemetry.get("qt_overlay_load_ok"),
        "native_qt_overlay_load_failures": qt_runtime_telemetry.get("qt_overlay_load_failures"),
        "native_qt_overlay_visible": qt_runtime_telemetry.get("qt_overlay_visible"),
        "native_qt_native_idle_enabled": qt_runtime_telemetry.get("qt_native_idle_enabled"),
        "native_qt_native_idle_visible": qt_runtime_telemetry.get("qt_native_idle_visible"),
        "native_qt_mpv_runtime_initialized": qt_runtime_telemetry.get("mpv_runtime_initialized"),
        "native_qt_mpv_runtime_playback_active": qt_runtime_telemetry.get("mpv_runtime_playback_active"),
        "native_qt_mpv_runtime_stream_loaded": qt_runtime_telemetry.get("mpv_runtime_stream_loaded"),
        "native_qt_mpv_runtime_playback_started": qt_runtime_telemetry.get("mpv_runtime_playback_started"),
        "native_qt_mpv_runtime_paused": qt_runtime_telemetry.get("mpv_runtime_paused"),
        "native_qt_mpv_runtime_time_pos": qt_runtime_telemetry.get("mpv_runtime_time_pos"),
        "native_qt_mpv_runtime_duration": qt_runtime_telemetry.get("mpv_runtime_duration"),
        "native_qt_mpv_runtime_volume": qt_runtime_telemetry.get("mpv_runtime_volume"),
        "native_qt_mpv_runtime_mute": qt_runtime_telemetry.get("mpv_runtime_mute"),
        "native_qt_mpv_runtime_path": str(qt_runtime_telemetry.get("mpv_runtime_path") or ""),
        "native_qt_mpv_runtime_current_vo": str(qt_runtime_telemetry.get("mpv_runtime_current_vo") or ""),
        "native_qt_mpv_runtime_current_ao": str(qt_runtime_telemetry.get("mpv_runtime_current_ao") or ""),
        "native_qt_mpv_runtime_aid": qt_runtime_telemetry.get("mpv_runtime_aid"),
        "native_qt_mpv_runtime_sample_detail": str(qt_runtime_telemetry.get("mpv_runtime_sample_detail") or ""),
        "native_qt_fd_count": qt_runtime_telemetry.get("qt_shell_fd_count"),
        "native_qt_fd_limit": qt_runtime_telemetry.get("qt_shell_fd_limit"),
        "native_qt_fd_warn_threshold": qt_runtime_telemetry.get("qt_shell_fd_warn_threshold"),
        "native_qt_fd_critical_threshold": qt_runtime_telemetry.get("qt_shell_fd_critical_threshold"),
        "native_qt_fd_headroom": qt_runtime_telemetry.get("qt_shell_fd_headroom"),
        "native_qt_fd_pressure_pct": qt_runtime_telemetry.get("qt_shell_fd_pressure_pct"),
        "native_qt_fd_warning": bool(qt_runtime_telemetry.get("qt_shell_fd_warning")),
        "native_qt_fd_warning_level": str(qt_runtime_telemetry.get("qt_shell_fd_warning_level") or "unknown"),
        "resolver_provider": str(resolver_runtime_info.get("provider") or ""),
        "resolver_effective_format": str(resolver_runtime_info.get("effective_format") or ""),
        "resolver_last_transport": str(resolver_runtime_info.get("transport") or ""),
        "resolver_last_outcome_category": str(
            resolver_runtime_info.get("last_outcome_category") or "unknown"
        ),
        "resolver_last_error": str(resolver_runtime_info.get("last_error") or ""),
        "resolver_last_attempt_unix": float(resolver_runtime_info.get("last_attempt_unix") or 0.0),
        "resolver_last_success_unix": float(resolver_runtime_info.get("last_success_unix") or 0.0),
        "video_profile": profile,
        "display_cap_height": profile.get("display_cap_height"),
        "decode_profile": profile.get("decode_profile"),
        "av1_allowed": bool(profile.get("av1_allowed")),
    }


def _push_overlay_toast(
    *,
    text: str,
    duration: float = 4.0,
    level: str = "info",
    icon: str | None = None,
    image_url: str | None = None,
    link_url: str | None = None,
    link_text: str | None = None,
    position: str = "top-left",
    style: dict | None = None,
) -> str:
    """Deliver a toast through the active notification runtime."""
    image_url = _native_qt_toast_image_url(image_url)
    payload = {
        "type": "toast",
        "text": text,
        "duration": float(duration),
        "duration_ms": max(250, int(float(duration) * 1000.0)),
        "level": (level or "info"),
        "icon": icon,
        "link_url": link_url,
        "link_text": link_text,
        "position": position,
        "style": style or {},
        "image_url": image_url,
        "ts": time.time(),
    }
    if _overlay_prefers_native_qt_toast(image_url):
        try:
            result = player.qt_shell_runtime_overlay_toast(
                text=text,
                duration=float(duration),
                level=(level or "info"),
                icon=icon,
                image_url=image_url,
                link_url=link_url,
                link_text=link_text,
                position=position,
                style=style,
            )
            if isinstance(result, dict) and result.get("error") == "success":
                try:
                    if hasattr(state, "update_overlay_delivery_state"):
                        state.update_overlay_delivery_state(
                            "displaying",
                            "native_toast_pushed",
                            client_event="toast",
                            client_reason="native_toast_pushed",
                        )
                except Exception:
                    pass
                return "native_qt"
        except Exception:
            pass
        if image_url:
            payload["image_url"] = None
            try:
                if hasattr(state, "update_overlay_delivery_state"):
                    state.update_overlay_delivery_state(
                        "retrying",
                        "native_toast_failed_overlay_fallback",
                        client_event="toast",
                        client_reason="native_toast_failed_overlay_fallback",
                    )
            except Exception:
                pass
    _x11_overlay_push(payload)
    return "overlay"


def _queue_toast_metadata_wait_sec(item: object = None) -> float:
    raw = (os.getenv("RELAYTV_QUEUE_TOAST_METADATA_WAIT_SEC") or "1.2").strip()
    if isinstance(item, dict) and bool(item.get("_metadata_lightweight")):
        raw = (os.getenv("RELAYTV_QUEUE_TOAST_LIGHTWEIGHT_WAIT_SEC") or "20").strip()
    try:
        return max(0.0, min(float(raw), 30.0))
    except Exception:
        return 20.0 if isinstance(item, dict) and bool(item.get("_metadata_lightweight")) else 1.2



def _queue_toast_payload(item: object, fallback_label: str) -> tuple[str, str | None]:
    queue_label = str(fallback_label or "item")
    thumb = None
    if isinstance(item, dict):
        queue_label = str(item.get("title") or item.get("url") or queue_label)
        thumb = item.get("thumbnail_local") or item.get("thumbnail")
    return queue_label, (str(thumb).strip() if thumb else None)



def _queue_toast_allows_lightweight_payload(item: object) -> bool:
    if not isinstance(item, dict):
        return True
    if not bool(item.get("_metadata_lightweight")):
        return True
    provider = str(item.get("provider") or "").strip().lower()
    return provider in {"youtube"}



def _queue_toast_metadata_ready(item: object, fallback_label: str) -> bool:
    if not isinstance(item, dict):
        return True
    if not _queue_toast_allows_lightweight_payload(item):
        return False
    label, thumb = _queue_toast_payload(item, fallback_label)
    url = str(item.get("url") or "").strip()
    return bool(label and label != url and (thumb or not bool(item.get("_metadata_lightweight"))))



def _push_queue_added_toast(item: object, fallback_label: str) -> None:
    wait_deadline = time.time() + _queue_toast_metadata_wait_sec(item)
    while time.time() < wait_deadline:
        if _queue_toast_metadata_ready(item, fallback_label):
            break
        time.sleep(0.05)
    if not _queue_toast_metadata_ready(item, fallback_label):
        return
    queue_label, thumb = _queue_toast_payload(item, fallback_label)
    _push_overlay_toast(
        text=f"Added to queue: {queue_label}",
        duration=_playback_notification_display_sec(),
        level="info",
        icon="share",
        image_url=thumb,
    )



def _push_queue_added_toast_async(item: object, fallback_label: str) -> None:
    def _run() -> None:
        try:
            _push_queue_added_toast(item, fallback_label)
        except Exception:
            pass

    try:
        threading.Thread(target=_run, daemon=True, name="relaytv-queue-toast").start()
    except Exception:
        _run()



# =========================
# X11 Overlay notification hub (SSE)
# =========================

_X11_OVERLAY_SUBS: set[asyncio.Queue] = set()
_UI_EVENT_SUBS: set[asyncio.Queue] = set()

def _x11_overlay_push(event: dict) -> None:
    """Push a toast/overlay event to any connected X11 overlay clients."""
    if not _X11_OVERLAY_SUBS:
        try:
            if hasattr(state, "update_overlay_delivery_state"):
                state.update_overlay_delivery_state(
                    "disconnected",
                    "toast_dropped_no_subscribers",
                    client_event=str(event.get("type") or "toast"),
                    client_reason="toast_dropped_no_subscribers",
                )
        except Exception:
            pass
        return
    try:
        if hasattr(state, "update_overlay_delivery_state"):
            state.update_overlay_delivery_state(
                "displaying",
                "toast_pushed",
                client_event=str(event.get("type") or "toast"),
                client_reason="toast_pushed",
            )
    except Exception:
        pass
    payload = _json.dumps(event, separators=(",", ":"), ensure_ascii=False)
    dead: list[asyncio.Queue] = []
    for q in list(_X11_OVERLAY_SUBS):
        try:
            q.put_nowait(payload)
        except Exception:
            dead.append(q)
    for q in dead:
        try:
            _X11_OVERLAY_SUBS.discard(q)
        except Exception:
            pass

async def _x11_overlay_sse() -> object:
    """Server-Sent Events stream for X11 overlay."""
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=50)
    _X11_OVERLAY_SUBS.add(q)
    try:
        if hasattr(state, "update_overlay_delivery_state"):
            state.update_overlay_delivery_state(
                "connected",
                "subscriber_connected",
                client_event="subscriber",
                client_reason="subscriber_connected",
            )
    except Exception:
        pass
    # Send a hello so the client can confirm connectivity.
    try:
        q.put_nowait(_json.dumps({"type": "hello", "ts": time.time()}))
    except Exception:
        pass

    async def gen():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {_json.dumps({'type': 'ping', 'ts': time.time()}, separators=(',', ':'))}\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            _X11_OVERLAY_SUBS.discard(q)
            try:
                if hasattr(state, "update_overlay_delivery_state") and not _X11_OVERLAY_SUBS:
                    state.update_overlay_delivery_state(
                        "disconnected",
                        "subscriber_gone",
                        client_event="subscriber",
                        client_reason="subscriber_gone",
                    )
            except Exception:
                pass

    return StreamingResponse(gen(), media_type="text/event-stream")


def _ui_event_push(event_name: str, event: dict) -> None:
    """Push a lightweight UI event to any connected /ui SSE clients."""
    if not _UI_EVENT_SUBS:
        return
    payload = _json.dumps(event, separators=(",", ":"), ensure_ascii=False)
    dead: list[asyncio.Queue] = []
    for q in list(_UI_EVENT_SUBS):
        try:
            q.put_nowait((event_name, payload))
        except Exception:
            dead.append(q)
    for q in dead:
        try:
            _UI_EVENT_SUBS.discard(q)
        except Exception:
            pass


def _ui_event_push_queue(action: str, queue: list[object] | None = None, queue_length: int | None = None, source: str = "api") -> None:
    if queue is None:
        with state.QUEUE_LOCK:
            queue = list(state.QUEUE)
    queue = _annotate_upload_items(queue)
    qlen = int(queue_length) if queue_length is not None else len(queue)
    _ui_event_push(
        "queue",
        {
            "type": "queue",
            "action": str(action or "").strip() or "refresh",
            "source": str(source or "").strip() or "api",
            "queue_length": qlen,
            "queue": queue,
            "ts": time.time(),
        },
    )


def _annotate_upload_item(item: object) -> object:
    return public_media.public_media_item(upload_store.annotate_item(item))


def _annotate_upload_items(items: list[object] | None) -> list[object]:
    return [_annotate_upload_item(item) for item in list(items or [])]


def _ui_event_push_jellyfin(
    action: str,
    *,
    refresh_active_tab: bool = False,
    refresh_settings: bool = False,
    refresh_status: bool = True,
    reason: str = "",
) -> None:
    _ui_event_push(
        "jellyfin",
        {
            "type": "jellyfin",
            "action": str(action or "").strip() or "refresh",
            "reason": str(reason or "").strip(),
            "refresh_active_tab": bool(refresh_active_tab),
            "refresh_settings": bool(refresh_settings),
            "refresh_status": bool(refresh_status),
            "ts": time.time(),
        },
    )


def _host_urls() -> list[str]:
    port = config.server_port()
    out: list[str] = [f"http://127.0.0.1:{port}/ui", f"http://localhost:{port}/ui"]
    ips: set[str] = set()
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET)
        for info in infos:
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            ips.add(ip)
    except Exception:
        pass

    for ip in sorted(ips):
        out.append(f"http://{ip}:{port}/ui")
    seen: set[str] = set()
    deduped: list[str] = []
    for u in out:
        if u not in seen:
            deduped.append(u)
            seen.add(u)
    return deduped


def _public_host_urls() -> list[str]:
    out: list[str] = []
    for u in _host_urls():
        low = u.lower()
        if "127.0.0.1" in low or "localhost" in low:
            continue
        out.append(u)
    return out


def _best_connect_url(req: Request | None = None) -> str:
    urls = _public_host_urls()
    if urls:
        return urls[0]
    if req is not None:
        try:
            host = str(req.url.hostname or "").strip()
            scheme = str(req.url.scheme or "http").strip() or "http"
            port = req.url.port
            if host and host not in ("127.0.0.1", "localhost"):
                netloc = f"{host}:{port}" if port else host
                return f"{scheme}://{netloc}/ui"
        except Exception:
            pass
    for u in _host_urls():
        if "127.0.0.1" in u or "localhost" in u:
            continue
        return u
    return _host_urls()[0]


def _render_connect_qr_svg(url: str, include_logo: bool = True) -> str:
    def _inline_logo_svg(x: int, y: int, w: int, h: int) -> str:
        try:
            explicit = (os.getenv("RELAYTV_LOGO_PATH") or "").strip()
            logo_path = explicit if explicit and os.path.exists(explicit) else _resolve_static_asset("brand", "logo.svg")
            if not logo_path or not os.path.exists(logo_path):
                return ""
            with open(logo_path, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()
            start = raw.find("<svg")
            if start < 0:
                return ""
            open_end = raw.find(">", start)
            close = raw.rfind("</svg>")
            if open_end < 0 or close <= open_end:
                return ""
            inner = raw[open_end + 1 : close]
            # Remove editor-only metadata for better embedded renderer compatibility.
            inner = re.sub(r"<sodipodi:namedview[\s\S]*?</sodipodi:namedview>", "", inner, flags=re.IGNORECASE)
            inner = re.sub(r"<sodipodi:namedview[\s\S]*?/>", "", inner, flags=re.IGNORECASE)
            return (
                f"<svg x='{x}' y='{y}' width='{w}' height='{h}' viewBox='0 0 120 120' "
                f"xmlns='http://www.w3.org/2000/svg' xmlns:xlink='http://www.w3.org/1999/xlink' "
                f"xmlns:inkscape='http://www.inkscape.org/namespaces/inkscape' "
                f"xmlns:sodipodi='http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd'>{inner}</svg>"
            )
        except Exception:
            return ""

    # Lazy import to avoid making runtime/test import-time hard dependent.
    try:
        import qrcode  # type: ignore
    except Exception:
        safe = (url or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        logo_markup = _inline_logo_svg(196, 276, 120, 120) if include_logo else ""
        return (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<svg xmlns='http://www.w3.org/2000/svg' width='512' height='512' viewBox='0 0 512 512' role='img' aria-label='RelayTV connect'>"
            "<rect width='512' height='512' rx='24' fill='#ffffff'/>"
            "<rect x='32' y='32' width='448' height='448' rx='20' fill='#0f172a' opacity='0.06'/>"
            "<text x='256' y='206' text-anchor='middle' font-size='24' font-family='ui-sans-serif,system-ui,Segoe UI,Arial' fill='#0f172a'>Install qrcode package for scannable QR</text>"
            f"<text x='256' y='242' text-anchor='middle' font-size='14' font-family='ui-monospace,Consolas,Menlo,monospace' fill='#334155'>{safe}</text>"
            + logo_markup
            + "</svg>"
        )

    qr = qrcode.QRCode(  # type: ignore[attr-defined]
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,  # type: ignore[attr-defined]
        box_size=1,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    modules = len(matrix)
    mod_px = 8
    size = modules * mod_px

    out: list[str] = [
        "<?xml version='1.0' encoding='UTF-8'?>",
        (
            f"<svg xmlns='http://www.w3.org/2000/svg' xmlns:xlink='http://www.w3.org/1999/xlink' "
            f"width='{size}' height='{size}' viewBox='0 0 {size} {size}' role='img' aria-label='RelayTV connect QR'>"
        ),
        f"<rect width='{size}' height='{size}' fill='#ffffff'/>",
    ]
    module_color = "#000000"
    for y, row in enumerate(matrix):
        x = 0
        while x < modules:
            while x < modules and not row[x]:
                x += 1
            if x >= modules:
                break
            start = x
            while x < modules and row[x]:
                x += 1
            run = x - start
            out.append(
                f"<rect x='{start * mod_px}' y='{y * mod_px}' width='{run * mod_px}' height='{mod_px}' fill='{module_color}'/>"
            )

    if include_logo:
        badge = max(56, int(size * 0.24))
        bx = (size - badge) // 2
        by = (size - badge) // 2
        logo = int(badge * 0.72)
        lx = (size - logo) // 2
        ly = (size - logo) // 2
        rad = max(8, int(badge * 0.18))
        out.append(
            f"<rect x='{bx}' y='{by}' width='{badge}' height='{badge}' rx='{rad}' ry='{rad}' fill='#ffffff' stroke='#dbe3f0' stroke-width='2'/>"
        )
        logo_markup = _inline_logo_svg(lx, ly, logo, logo)
        if logo_markup:
            out.append(logo_markup)
        else:
            out.append(
                f"<text x='{size//2}' y='{(size//2)+5}' text-anchor='middle' font-size='{max(10, int(logo*0.28))}' font-family='ui-sans-serif,system-ui,Segoe UI,Arial' fill='#0f172a' font-weight='700'>RelayTV</text>"
            )

    out.append("</svg>")
    return "".join(out)


def _idle_panel_catalog() -> dict[str, dict[str, object]]:
    return {
        "weather": {"title": "Weather", "desc": "Current + short outlook", "layouts": ["split", "minimal"]},
    }


def _idle_html() -> str:
    return r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>RelayTV Idle</title>
  <style>
    :root{
      --bg:#08111d;
      --bg2:#0d1827;
      --txt:#eef5ff;
      --muted:#b9cbe4;
      --panel:rgba(13,24,38,.36);
      --panel-strong:rgba(13,24,38,.56);
      --stroke:rgba(205,226,255,.18);
      --stroke-strong:rgba(205,226,255,.28);
      --accent:#57a3ff;
    }
    html{font-size:clamp(12px,1.4815vmin,32px)}
    *{box-sizing:border-box;cursor:none!important}
    html,body{margin:0;height:100%;font-family:Inter,system-ui,Segoe UI,Roboto,Arial,sans-serif;color:var(--txt);cursor:none!important}
    body{
      background:
        radial-gradient(56.25rem 32.5rem at 18% 82%, rgba(25,75,138,.20), transparent 68%),
        radial-gradient(68.75rem 40rem at 50% -12%, rgba(145,190,255,.12), transparent 60%),
        linear-gradient(180deg,var(--bg),var(--bg2));
      overflow:hidden;
      cursor:none!important
    }
    .wrap{height:100%;display:grid;grid-template-rows:auto 1fr auto;gap:1.125rem;padding:1.625rem 1.875rem 1.625rem}
    .top{display:flex;justify-content:center;align-items:flex-start;pointer-events:none;width:100%;overflow:visible}
    .heroLogo{
      display:block;
      width:min(36vw,22.5rem);
      max-width:calc(100vw - 3.75rem);
      height:auto;
      object-fit:contain;
      object-position:center;
      filter:drop-shadow(0 0 1.75rem rgba(133,191,255,.12));
      opacity:.96;
      margin-top:.125rem
    }
    .stage{
      min-height:0;
      display:flex;
      align-items:center;
      justify-content:center
    }
    .heroRail{
      width:min(100%,70rem);
      display:grid;
      gap:1.625rem;
      align-content:center
    }
    .timePanel{
      display:grid;
      grid-template-columns:minmax(0,1fr);
      grid-template-rows:auto auto;
      column-gap:2.125rem;
      row-gap:1.125rem;
      align-items:center;
      min-height:18.75rem;
      padding:.5rem .75rem 0;
    }
    .timePanel.hasWeather{
      grid-template-columns:minmax(0,1fr) minmax(18.75rem,26.875rem);
    }
    .timeMain{
      display:grid;
      justify-items:end;
      align-content:center;
      gap:.625rem;
      padding-right:.75rem
    }
    .timeDivider{
      display:none
    }
    .weatherHeroPanel{
      display:none;
      align-content:center;
      gap:.5rem;
      padding-left:2.125rem;
      position:relative
    }
    .timePanel.hasWeather .weatherHeroPanel{
      display:grid
    }
    .timePanel.hasWeather .weatherHeroPanel::before{
      content:"";
      position:absolute;
      left:0;
      top:0;
      bottom:0;
      width:1px;
      background:linear-gradient(180deg, transparent, rgba(235,242,255,.28), transparent)
    }
    .time{font-size:8rem;font-weight:300;font-variant-numeric:tabular-nums;line-height:.88;letter-spacing:-.05em;text-shadow:0 .625rem 2.25rem rgba(0,0,0,.16)}
    .date{margin-top:0;color:rgba(230,239,251,.86);font-size:2.125rem;letter-spacing:.01em}
    .urls{display:none}
    .pill{
      padding:.625rem 1rem;
      border-radius:999px;
      background:rgba(255,255,255,.04);
      border:1px solid rgba(205,226,255,.16);
      color:#e8f2ff;
      font:600 1.125rem/1.2 ui-monospace,Menlo,monospace
    }
    .forecastStrip{
      grid-column:1 / -1;
      display:none;
      grid-template-columns:minmax(0,1fr);
      align-content:start
    }
    .timePanel.hasWeather .forecastStrip{
      display:grid
    }
    .panel{
      background:none;
      border:none;
      border-radius:0;
      padding:0;
      box-shadow:none;
      backdrop-filter:none
    }
    .cardTitle{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:rgba(230,241,255,.44)}
    .cardDesc{margin-top:.375rem;color:rgba(197,214,235,.66);font-size:.8125rem}
    .cardValue{margin-top:.75rem;font-size:1.5rem;font-weight:750}
    .weatherNow{
      margin-top:0;
      position:relative;
      padding-right:0;
      display:grid;
      grid-template-columns:auto 1fr;
      gap:1.125rem;
      align-items:center
    }
    .weatherHeading{display:block}
    .weatherHeading .cardDesc{margin-top:.25rem}
    .weatherCurrent{display:block;margin-top:0}
    .weatherTemp{font-size:3.625rem;font-weight:400;line-height:.9;letter-spacing:-.04em}
    .weatherSummary{font-size:1.5rem;color:rgba(230,241,255,.90);margin-top:.25rem}
    .weatherMeta{margin-top:.625rem;display:flex;flex-wrap:wrap;gap:.4375rem .75rem;font-size:.8125rem;color:#dce9ff}
    .weatherMeta b{font-weight:760;color:#f3f8ff}
    .wxHero{
      position:relative;
      top:auto;
      right:auto;
      bottom:auto;
      display:grid;
      justify-items:center;
      align-items:center;
      width:7rem;
      min-width:7rem;
      height:7rem;
      padding-top:0;
      border-radius:0;
      border:none;
      background:none;
      overflow:visible
    }
    .wxHero::before{content:none}
    .wxCode{position:relative;width:7rem;height:7rem;object-fit:contain;filter:drop-shadow(0 .5rem 1rem rgba(0,0,0,.18))}
    .weatherDays{
      margin-top:0;
      padding-top:1.125rem;
      padding-bottom:1.125rem;
      position:relative;
      display:grid;
      grid-template-columns:repeat(auto-fit,minmax(7.375rem,1fr));
      gap:0
    }
    .weatherDays::before,
    .weatherDays::after{
      content:"";
      position:absolute;
      left:0;
      right:0;
      height:1px;
      background:linear-gradient(90deg, transparent, rgba(235,242,255,.28), transparent)
    }
    .weatherDays::before{top:0}
    .weatherDays::after{bottom:0}
    .weatherDay{
      display:grid;
      justify-items:center;
      align-content:center;
      gap:.5rem;
      min-height:8.5rem;
      padding:.875rem .625rem .75rem;
      position:relative
    }
    .weatherDay + .weatherDay::before{
      content:"";
      position:absolute;
      left:0;
      top:1.125rem;
      bottom:1.125rem;
      width:1px;
      background:linear-gradient(180deg, transparent, rgba(235,242,255,.28), transparent)
    }
    .weatherDow{font-size:.875rem;font-weight:700;color:#f0f6ff;text-transform:none;letter-spacing:.01em}
    .weatherCond{display:grid;justify-items:center;gap:.375rem;min-width:0}
    .weatherIcon{width:3.625rem;height:3.625rem;display:block;object-fit:contain}
    .weatherLabel{font-size:.8125rem;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .weatherRange{font-size:1.125rem;color:#f1f6ff;font-weight:500;white-space:nowrap}
    .weatherDatapoint{display:none}
    .footer{
      display:flex;
      justify-content:space-between;
      align-items:end;
      gap:1.125rem;
      min-height:3.375rem
    }
    .deviceName{
      font-size:1.875rem;
      font-weight:700;
      letter-spacing:.01em;
      color:#edf5ff;
      text-shadow:0 .375rem 1.5rem rgba(0,0,0,.25)
    }
    .statusWrap{
      display:grid;
      justify-items:end;
      gap:.3125rem
    }
    .footerStatus{
      color:rgba(221,233,248,.7);
      font-size:.875rem;
      letter-spacing:.14em;
      text-transform:uppercase
    }
    .footerMeta{
      color:rgba(197,214,235,.56);
      font-size:.75rem;
      letter-spacing:.08em;
      text-transform:uppercase
    }
    @media (max-width:760px){
      html{font-size:16px}
      .wrap{padding:18px 18px 20px}
      .stage{align-items:start}
      .heroRail{width:100%;justify-self:stretch}
      .heroLogo{width:min(92vw,360px);max-width:calc(100vw - 36px)}
      .timePanel{grid-template-columns:minmax(0,1fr);grid-template-rows:auto auto auto;row-gap:18px;min-height:0;padding-top:0}
      .timeMain{justify-items:start;padding-right:0}
      .timeDivider{display:none}
      .weatherHeroPanel{padding-left:0}
      .time{font-size:78px}
      .date{font-size:22px}
      .pill{font-size:13px;padding:8px 12px}
      .footer{align-items:start;flex-direction:column;gap:8px}
      .statusWrap{justify-items:start}
      .deviceName{font-size:22px}
      .weatherNow{grid-template-columns:1fr;justify-items:start}
      .wxHero{width:84px;min-width:84px;height:84px}
      .wxCode{width:84px;height:84px}
      .weatherDays{grid-template-columns:repeat(3,minmax(0,1fr))}
      .weatherDay{min-height:122px}
      .weatherDay + .weatherDay::before{top:14px;bottom:14px}
    }
    .hidden{display:none !important}
    .idleQrWrap{
      position:fixed;right:1.125rem;bottom:1.125rem;z-index:40;
      display:flex;flex-direction:column;align-items:center;gap:.5rem;
      background:rgba(8,15,27,.84);border:1px solid rgba(116,162,230,.28);border-radius:.875rem;
      padding:.625rem .625rem .5rem;box-shadow:0 .875rem 1.75rem rgba(0,0,0,.38);backdrop-filter:blur(.375rem)
    }
    .idleQrImg{
      width:var(--idleQrSizePx,10.5rem);height:var(--idleQrSizePx,10.5rem);display:block;background:#fff;border-radius:.625rem;
      border:1px solid rgba(116,162,230,.32)
    }
    .idleQrLabel{
      max-width:calc(var(--idleQrSizePx,10.5rem) + .25rem);color:#d8e8ff;font-size:.6875rem;line-height:1.2;text-align:center;
      white-space:nowrap;overflow:hidden;text-overflow:ellipsis
    }
    @media (max-width:760px){
      .idleQrWrap{right:10px;bottom:10px;padding:8px 8px 6px}
      .idleQrImg{width:var(--idleQrSizeMobilePx,7.25rem);height:var(--idleQrSizeMobilePx,7.25rem)}
      .idleQrLabel{max-width:calc(var(--idleQrSizeMobilePx,7.25rem) + 2px);font-size:10px}
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <img class="heroLogo" src="/pwa/brand/banner.png" alt="RelayTV banner"/>
    </div>
    <section class="stage">
      <div class="heroRail">
        <section class="timePanel">
          <div class="timeMain">
            <div id="clock" class="time">--:--</div>
            <div id="date" class="date">---</div>
            <div id="urls" class="urls"></div>
          </div>
          <div class="timeDivider"></div>
          <div id="weatherHeroPanel" class="weatherHeroPanel"></div>
          <div id="forecastStrip" class="forecastStrip"></div>
        </section>
      </div>
    </section>
    <div class="footer">
      <div id="idleDeviceName" class="deviceName">RelayTV</div>
      <div class="statusWrap">
        <div id="now" class="footerStatus">Idle</div>
        <div class="footerMeta">RelayTV Idle Dashboard</div>
      </div>
    </div>
  </div>
  <div id="idleQrWrap" class="idleQrWrap hidden" aria-hidden="true">
    <img id="idleQrImg" class="idleQrImg" src="" alt="Scan to open RelayTV remote"/>
    <div id="idleQrLabel" class="idleQrLabel"></div>
  </div>
  <script>
    const CATALOG = __IDLE_CATALOG__;
    const CLOCK_OFFSET_MINUTES = Number(__CLOCK_OFFSET_MINUTES__) || 0;
    const SERVER_NOW_MS = Number(__SERVER_NOW_MS__) || Date.now();
    const CLOCK_SKEW_MS = SERVER_NOW_MS - Date.now();
    let __idleQrEnabled = true;
    let __idleQrSize = 168;
    let __idleQrUrl = '';

    function _serverClockDate(){
      return new Date(Date.now() + CLOCK_SKEW_MS + (CLOCK_OFFSET_MINUTES * 60000));
    }
    function _fmtClockTime(date){
      const h24 = date.getUTCHours();
      const h12 = (h24 % 12) || 12;
      const min = String(date.getUTCMinutes()).padStart(2,'0');
      const ampm = h24 >= 12 ? 'PM' : 'AM';
      return `${h12}:${min} ${ampm}`;
    }
    function _fmtClockDate(date){
      const dow = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'][date.getUTCDay()];
      const mon = ['January','February','March','April','May','June','July','August','September','October','November','December'][date.getUTCMonth()];
      return `${dow}, ${mon} ${date.getUTCDate()}`;
    }
    function tick(){ const d=_serverClockDate(); document.getElementById('clock').textContent=_fmtClockTime(d); document.getElementById('date').textContent=_fmtClockDate(d); }
    setInterval(tick,1000); tick();

    function _applyIdleQrSizing(px){
      let size = Number(px);
      if (!Number.isFinite(size)) size = 168;
      size = Math.max(96, Math.min(280, Math.round(size)));
      __idleQrSize = size;
      const mobile = Math.max(88, Math.min(220, Math.round(size * 0.7)));
      const root = document.documentElement;
      if (root && root.style) {
        root.style.setProperty('--idleQrSizePx', `${size / 16}rem`);
        root.style.setProperty('--idleQrSizeMobilePx', `${mobile / 16}rem`);
      }
    }
    _applyIdleQrSizing(__idleQrSize);

    function setIdleQr(url){
      const wrap = document.getElementById('idleQrWrap');
      const img = document.getElementById('idleQrImg');
      const label = document.getElementById('idleQrLabel');
      if (!wrap || !img || !label) return;
      if (!__idleQrEnabled || !url) {
        wrap.classList.add('hidden');
        wrap.setAttribute('aria-hidden', 'true');
        return;
      }
      const target = String(url || '').trim();
      if (!target) {
        wrap.classList.add('hidden');
        wrap.setAttribute('aria-hidden', 'true');
        return;
      }
      if (__idleQrUrl !== target) {
        __idleQrUrl = target;
        img.src = `/qr/connect.svg?logo=1&u=${encodeURIComponent(target)}&ts=${Date.now()}`;
      }
      label.textContent = target.replace(/^https?:\/\//i, '');
      wrap.classList.remove('hidden');
      wrap.setAttribute('aria-hidden', 'false');
    }

    async function refreshUrls(){
      try{
        const r=await fetch('/x11/host_urls',{cache:'no-store'});
        const j=await r.json();
        const el=document.getElementById('urls');
        const src=((j.public_urls&&j.public_urls.length)?j.public_urls:(j.urls||[])).filter(u=>!u.includes('127.0.0.1')&&!u.includes('localhost'));
        if (el){
          el.innerHTML='';
          src.slice(0,4).forEach(u=>{ const p=document.createElement('div'); p.className='pill'; p.textContent=u; el.appendChild(p); });
        }
        setIdleQr(src[0] || '');
      }catch(_e){
        setIdleQr('');
      }
    }
    setInterval(refreshUrls,30000); refreshUrls();

    function wxCodeToAsset(code, isDay=true){
      if (code === 0) return isDay ? 'clear_day.svg' : 'clear_night.svg';
      if (code === 1) return isDay ? 'mostly_clear_day.svg' : 'mostly_clear_night.svg';
      if (code === 2) return isDay ? 'partly_cloudy_day.svg' : 'partly_cloudy_night.svg';
      if (code === 3) return 'cloudy.svg';
      if ([45,48].includes(code)) return 'haze_fog_dust_smoke.svg';
      if ([51,53,55,56,57].includes(code)) return 'drizzle.svg';
      if ([61,63,80,81].includes(code)) return 'showers_rain.svg';
      if ([65,82].includes(code)) return 'heavy_rain.svg';
      if ([66,67].includes(code)) return 'mixed_rain_hail_sleet.svg';
      if ([71,73,85].includes(code)) return 'flurries.svg';
      if ([75,86].includes(code)) return 'heavy_snow.svg';
      if (code === 77) return 'icy.svg';
      if (code === 95) return 'thunderstorms.svg';
      if ([96,99].includes(code)) return 'strong_thunderstorms.svg';
      return 'not-available.svg';
    }

    function wxTheme(){
      try{
        return (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) ? 'light' : 'dark';
      }catch(_e){
        return 'dark';
      }
    }

    function wxAssetImg(code, isDay, className='weatherIcon'){
      const asset = wxCodeToAsset(code, isDay);
      const label = wxCodeToLabel(code);
      const theme = wxTheme();
      return `<img class="${className}" src="/pwa/weather/${asset}?theme=${encodeURIComponent(theme)}" alt="${label}" loading="lazy" decoding="async"/>`;
    }

    function wxCodeToLabel(code){
      if (code === 0) return 'Clear';
      if ([1,2].includes(code)) return 'Partly cloudy';
      if (code === 3) return 'Overcast';
      if ([45,48].includes(code)) return 'Fog';
      if ([51,53,55,56,57].includes(code)) return 'Drizzle';
      if ([61,63,65,80,81,82].includes(code)) return 'Rain';
      if ([66,67].includes(code)) return 'Freezing rain';
      if ([71,73,75,77,85,86].includes(code)) return 'Snow';
      if ([95,96,99].includes(code)) return 'Thunderstorm';
      return 'Mixed';
    }

    async function fetchWeather(settings){
      const w = settings?.weather || {};
      const lat = Number.isFinite(Number(w.latitude)) ? Number(w.latitude) : 40.7128;
      const lon = Number.isFinite(Number(w.longitude)) ? Number(w.longitude) : -74.006;
      const forecastDays = [1,3,7].includes(Number(w.forecast_days)) ? Number(w.forecast_days) : 7;
      const units = (w.units === 'metric') ? 'celsius' : 'fahrenheit';
      const windUnits = (w.units === 'metric') ? 'kmh' : 'mph';
      const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m,weather_code,is_day,apparent_temperature,wind_speed_10m&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max&temperature_unit=${units}&wind_speed_unit=${windUnits}&forecast_days=${forecastDays}`;
      const r = await fetch(url, {cache:'no-store'});
      if (!r.ok) throw new Error('weather fetch failed');
      return await r.json();
    }

    const WEATHER_REFRESH_MS = 180000; // 3 minutes
    const WEATHER_REQUEST_TIMEOUT_MS = 9000;
    let __weatherCache = null;
    let __weatherCacheAt = 0;
    let __weatherInflight = null;
    let __weatherSig = '';
    let __idlePanelsRenderSig = '';

    function weatherSignature(settings){
      const w = settings?.weather || {};
      const lat = Number.isFinite(Number(w.latitude)) ? Number(w.latitude).toFixed(4) : '40.7128';
      const lon = Number.isFinite(Number(w.longitude)) ? Number(w.longitude).toFixed(4) : '-74.0060';
      const units = (w.units === 'metric') ? 'metric' : 'imperial';
      const days = [1,3,7].includes(Number(w.forecast_days)) ? String(Number(w.forecast_days)) : '7';
      return `${lat}|${lon}|${units}|${days}`;
    }

    function weatherViewSignature(settings, weatherData){
      const w = settings?.weather || {};
      const location = String(w.location_name || '').trim();
      const hasCurrent = !!(weatherData && weatherData.current);
      return `${weatherSignature(settings)}|${location}|${__weatherCacheAt}|${hasCurrent ? '1' : '0'}`;
    }

    async function weatherForIdle(settings){
      const sig = weatherSignature(settings);
      if (sig !== __weatherSig) {
        __weatherSig = sig;
        __weatherCache = null;
        __weatherCacheAt = 0;
      }

      const now = Date.now();
      if (__weatherCache && (now - __weatherCacheAt) < WEATHER_REFRESH_MS) {
        return __weatherCache;
      }
      if (__weatherInflight) {
        try { return await __weatherInflight; } catch(_e) {}
      }

      __weatherInflight = (async () => {
        try {
          const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error('weather timeout')), WEATHER_REQUEST_TIMEOUT_MS));
          const fresh = await Promise.race([fetchWeather(settings), timeout]);
          __weatherCache = fresh;
          __weatherCacheAt = Date.now();
          return fresh;
        } catch (_e) {
          // Keep last good weather data visible during API/rate-limit hiccups.
          if (__weatherCache) return __weatherCache;
          return null;
        } finally {
          __weatherInflight = null;
        }
      })();
      return await __weatherInflight;
    }

    function renderWeatherCard(heroCard, forecastCard, panel, settings, weatherData){
      const wset = settings?.weather || {};
      const unitSym = (wset.units === 'metric') ? '°C' : '°F';
      if (!weatherData || !weatherData.current){
        heroCard.innerHTML = `<div class="cardTitle">Weather</div><div class="cardDesc">Live forecast unavailable</div><div class="cardValue">${wxAssetImg(-1, true, "weatherIcon")} --</div>`;
        forecastCard.innerHTML = '';
        return;
      }
      const cur = weatherData.current;
      const temp = Number(cur.temperature_2m);
      const feelsLike = Number(cur.apparent_temperature);
      const windNow = Number(cur.wind_speed_10m);
      const code = Number(cur.weather_code);
      const days = [1,3,7].includes(Number(wset.forecast_days)) ? Number(wset.forecast_days) : 7;
      const layout = (panel.layout||'split');
      const daily = weatherData.daily || {};
      const times = Array.isArray(daily.time) ? daily.time : [];
      const mins = Array.isArray(daily.temperature_2m_min) ? daily.temperature_2m_min : [];
      const maxs = Array.isArray(daily.temperature_2m_max) ? daily.temperature_2m_max : [];
      const codes = Array.isArray(daily.weather_code) ? daily.weather_code : [];
      const rainChance = Array.isArray(daily.precipitation_probability_max) ? daily.precipitation_probability_max : [];
      const winds = Array.isArray(daily.wind_speed_10m_max) ? daily.wind_speed_10m_max : [];
      const isDay = Number(cur.is_day) === 1;
      const icon = wxAssetImg(code, isDay, "wxCode");
      const location = String(wset.location_name || '').trim();
      const windUnit = (wset.units === 'metric') ? 'km/h' : 'mph';
      const safeTemp = Number.isFinite(temp) ? `${Math.round(temp)}${unitSym}` : '--';
      const safeFeels = Number.isFinite(feelsLike) ? `${Math.round(feelsLike)}${unitSym}` : '--';
      const safeWind = Number.isFinite(windNow) ? `${Math.round(windNow)} ${windUnit}` : '--';
      const forecast = times.slice(0, days).map((t, i) => {
        const d = new Date(`${t}T00:00:00`);
        const dow = Number.isFinite(d.getTime()) ? d.toLocaleDateString([], {weekday:'short'}) : '--';
        const lo = Number(mins[i]);
        const hi = Number(maxs[i]);
        const c = Number(codes[i]);
        const rain = Number(rainChance[i]);
        const wind = Number(winds[i]);
        const condLabel = wxCodeToLabel(c);
        const range = `${Number.isFinite(hi) ? Math.round(hi) : '--'}${unitSym}/${Number.isFinite(lo) ? Math.round(lo) : '--'}${unitSym}`;
        return `<div class="weatherDay"><div class="weatherDow">${dow}</div><div class="weatherCond">${wxAssetImg(c, true)}<div class="weatherLabel">${condLabel}</div></div><div class="weatherRange">${range}</div><div class="weatherDatapoint rain">Rain ${Number.isFinite(rain) ? Math.round(rain) + '%' : '--'}</div><div class="weatherDatapoint wind">Wind ${Number.isFinite(wind) ? Math.round(wind) + ' ' + windUnit : '--'}</div></div>`;
      }).join('');
      heroCard.innerHTML = `
        <div class="weatherHero">
          <div class="weatherNow">
            <div class="wxHero">${icon}</div>
            <div class="weatherCurrent">
              <div class="weatherTemp">${safeTemp}</div>
              <div class="weatherSummary">${wxCodeToLabel(code)}</div>
              <div class="cardDesc">${location || 'Open-Meteo local forecast'}</div>
              <div class="weatherMeta"><span><b>Feels</b> ${safeFeels}</span><span><b>Wind</b> ${safeWind}</span></div>
            </div>
          </div>
        </div>
      `;
      forecastCard.innerHTML = `<div class="weatherDays">${forecast || '<div class="weatherDay"><div class="weatherDow">--</div><img class="weatherIcon" src="/pwa/weather/not-available.svg" alt="Unavailable"/><div class="weatherRange">--</div></div>'}</div>`;
    }

    function renderPanels(cfg, settings, weatherData){
      const timePanel = document.querySelector('.timePanel');
      const hero=document.getElementById('weatherHeroPanel');
      const forecast=document.getElementById('forecastStrip');
      if (!hero || !forecast || !timePanel) return;
      const weatherPanel = ((cfg && cfg.weather) || {});
      const weatherLayout = String(weatherPanel.layout || 'split');
      const renderSig = weatherPanel.enabled
        ? `weather|${weatherLayout}|${weatherViewSignature(settings, weatherData)}`
        : 'weather|disabled';
      if (renderSig === __idlePanelsRenderSig) return;
      __idlePanelsRenderSig = renderSig;
      timePanel.classList.toggle('hasWeather', !!weatherPanel.enabled);
      hero.innerHTML='';
      forecast.innerHTML='';
      if (!weatherPanel.enabled) return;
      renderWeatherCard(hero, forecast, weatherPanel, settings, weatherData);
    }

    async function refresh(){
      try{
        const [setRes, stRes] = await Promise.all([fetch('/settings',{cache:'no-store'}), fetch('/status',{cache:'no-store'})]);
        const settings=await setRes.json();
        const st=await stRes.json();
        const name = (settings.device_name || st.device_name || 'RelayTV');
        const dn = document.getElementById('idleDeviceName');
        if (dn) dn.textContent = name;
        __idleQrEnabled = (settings.idle_qr_enabled !== false);
        _applyIdleQrSizing(settings.idle_qr_size);
        if (!__idleQrEnabled) setIdleQr('');
        else if (__idleQrUrl) setIdleQr(__idleQrUrl);
        let weatherData = null;
        if (((settings.idle_panels||{}).weather||{}).enabled) {
          weatherData = await weatherForIdle(settings);
        }
        renderPanels(settings.idle_panels||{}, settings, weatherData);
        const np=st.now_playing||null;
        document.getElementById('now').textContent=np ? `Now Playing: ${np.title||np.url||'Playing'}` : 'Idle';
      }catch(_e){}
    }
    setInterval(refresh,3000); refresh();
  </script>
</body>
</html>"""

_X11_OVERLAY_HTML = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>RelayTV Overlay</title>
  <style>
    :root{
      --overlay-debug-bg:__OVERLAY_DEBUG_BG__;
      --bg0:#070b13;
      --bg1:#0b1220;
      --card:rgba(15,22,36,.72);
      --card-border:rgba(42,168,255,.22);
      --txt:rgba(241,247,255,.96);
      --muted:rgba(208,222,245,.72);
      --accent:#2aa8ff;
      --ok:#33d18f;
      --warn:#ffbf43;
      --err:#ff6f91;
      --shadow:0 22px 44px rgba(0,0,0,.40);
      --radius:20px;
      --overlay-scale:1;
      --toast-width:430px;
      --toast-gap:11px;
      --toast-edge:22px;
      --toast-edge-center:20px;
      --toast-pad-y:13px;
      --toast-pad-x:14px;
      --toast-radius:15px;
      --toast-shadow-y:16px;
      --toast-shadow-blur:36px;
      --toast-accent-width:4px;
      --toast-accent-glow:16px;
      --toast-top-gap:10px;
      --toast-icon-size:24px;
      --toast-icon-font:13px;
      --toast-text-font:14px;
      --toast-link-font:14px;
      --toast-link-gap:6px;
      --toast-image-gap:10px;
      --toast-image-height:124px;
      --toast-image-radius:11px;
    }
    html,body{height:100%;margin:0;overflow:hidden;background:var(--overlay-debug-bg);color:var(--txt);font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;cursor:none!important;}
    body *{cursor:none!important;}
    body,.stage{pointer-events:none;}
    .stage{position:fixed;inset:0;opacity:0;visibility:hidden;transition:opacity .20s ease,visibility .20s ease;}
    body.idle .stage{opacity:1;visibility:visible;}
    .idleFrame{position:absolute;inset:0;width:100%;height:100%;border:0;background:transparent;pointer-events:none;}

    .toasts{position:fixed;width:min(var(--toast-width),70vw);display:flex;flex-direction:column;gap:var(--toast-gap);pointer-events:none;}
    .toasts.top-left{top:var(--toast-edge);left:var(--toast-edge);right:auto;bottom:auto;transform:none;}
    .toasts.top-right{top:var(--toast-edge);right:var(--toast-edge);left:auto;bottom:auto;transform:none;}
    .toasts.bottom-right{bottom:var(--toast-edge);right:var(--toast-edge);left:auto;top:auto;transform:none;}
    .toasts.bottom-left{bottom:var(--toast-edge);left:var(--toast-edge);right:auto;top:auto;transform:none;}
    .toasts.top-center{top:var(--toast-edge-center);left:50%;right:auto;bottom:auto;transform:translateX(-50%);}
    .toast{position:relative;overflow:hidden;padding:var(--toast-pad-y) var(--toast-pad-x);border-radius:var(--toast-radius);background:rgba(9,16,28,.82);border:1px solid rgba(42,168,255,.26);box-shadow:0 var(--toast-shadow-y) var(--toast-shadow-blur) rgba(0,0,0,.44);
      opacity:0;transform:translateY(-8px) scale(.98);transition:opacity __PLAYBACK_NOTIFY_FADE_MS__ms ease,transform __PLAYBACK_NOTIFY_FADE_MS__ms ease,border-color __PLAYBACK_NOTIFY_FADE_MS__ms ease;pointer-events:auto;}
    .toast.show{opacity:1;transform:translateY(0) scale(1);}
    .toast::before{content:"";position:absolute;left:0;top:0;bottom:0;width:var(--toast-accent-width);background:var(--accent);box-shadow:0 0 var(--toast-accent-glow) var(--accent);}
    .toast.success{--accent:var(--ok)} .toast.warn{--accent:var(--warn)} .toast.error{--accent:var(--err)}
    .toastTop{display:flex;align-items:center;gap:var(--toast-top-gap);}
    .ico{width:var(--toast-icon-size);height:var(--toast-icon-size);border-radius:999px;background:rgba(255,255,255,.08);display:grid;place-items:center;font-size:var(--toast-icon-font);}
    .tTxt{font-size:var(--toast-text-font);line-height:1.28;white-space:pre-line;}
    .toast a{color:inherit;text-decoration:underline;font-weight:600;font-size:var(--toast-link-font);display:inline-block;margin-top:var(--toast-link-gap);pointer-events:auto;}
    .toast .img{margin-top:var(--toast-image-gap);width:100%;height:var(--toast-image-height);display:none;object-fit:cover;border-radius:var(--toast-image-radius);border:1px solid rgba(130,170,220,.25);background:rgba(255,255,255,.04)}
    .toast .img.ready{display:block;}

  </style>
</head>
<body class="playing">
  <div class="stage">
    <iframe class="idleFrame" src="about:blank" title="RelayTV Idle" aria-label="RelayTV Idle"></iframe>
  </div>

  <div class="toasts top-left" id="toasts"></div>

  <script>
    const $ = (id)=>document.getElementById(id);
    const iconMap = {share:"↗",check:"✓",warn:"!",camera:"📷",play:"▶",info:"i"};
    const overlayAllowToastImages = __OVERLAY_ALLOW_IMAGES__;
    let _wasPlaying = true;
    let _overlayEventSource = null;
    let _overlayLastEventTs = Date.now();
    let _overlayReportedState = '';
    const overlayToastMetrics = [
      ['--toast-width', 430],
      ['--toast-gap', 11],
      ['--toast-edge', 22],
      ['--toast-edge-center', 20],
      ['--toast-pad-y', 13],
      ['--toast-pad-x', 14],
      ['--toast-radius', 15],
      ['--toast-shadow-y', 16],
      ['--toast-shadow-blur', 36],
      ['--toast-accent-width', 4],
      ['--toast-accent-glow', 16],
      ['--toast-top-gap', 10],
      ['--toast-icon-size', 24],
      ['--toast-icon-font', 13],
      ['--toast-text-font', 14],
      ['--toast-link-font', 14],
      ['--toast-link-gap', 6],
      ['--toast-image-gap', 10],
      ['--toast-image-height', 124],
      ['--toast-image-radius', 11],
    ];

    function updateOverlayToastScale(){
      try{
        const vw = Math.max(1, Number(window.innerWidth || 1920));
        const vh = Math.max(1, Number(window.innerHeight || 1080));
        const scale = Math.min(4, Math.max(0.75, Math.min(vw / 1920, vh / 1080)));
        const root = document.documentElement;
        root.style.setProperty('--overlay-scale', scale.toFixed(3));
        for(const [name, base] of overlayToastMetrics){
          root.style.setProperty(name, `${Math.round(Number(base) * scale)}px`);
        }
      }catch(_e){}
    }

    updateOverlayToastScale();
    window.addEventListener('resize', updateOverlayToastScale, {passive:true});

    function overlayPlaybackVisible(state){
      const j = state || {};
      const sessionState = String(j.state || '').trim().toLowerCase();
      if (sessionState === 'closed') return false;
      const runtimeState = String(j.playback_runtime_state || '').trim().toLowerCase();
      const qtRuntimeActive = (
        j.native_qt_mpv_runtime_playback_active === true
        || j.native_qt_mpv_runtime_stream_loaded === true
        || j.native_qt_mpv_runtime_playback_started === true
      );
      const sessionActive = (
        j.playing === true
        || sessionState === 'playing'
        || sessionState === 'paused'
        || runtimeState === 'playing'
        || runtimeState === 'paused'
        || runtimeState === 'buffering'
        || runtimeState === 'degraded'
        || j.transition_in_progress === true
        || j.transitioning_between_items === true
      );
      if (j.native_qt_telemetry_selected) {
        return qtRuntimeActive || sessionActive;
      }
      return sessionActive;
    }

    function _overlayToastCount(){
      try{return document.querySelectorAll('#toasts .toast').length;}catch(_e){return 0;}
    }

    function reportOverlayState(state, reason='', clientEvent='client', clientReason='', force=false){
      try{
        const nextState = String(state || '').trim().toLowerCase() || 'connected';
        const nextReason = String(reason || '').trim().toLowerCase();
        if(!force && nextState === _overlayReportedState) return;
        _overlayReportedState = nextState;
        fetch('/x11/overlay/client_state', {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          keepalive:true,
          body: JSON.stringify({
            state: nextState,
            reason: nextReason,
            client_event: String(clientEvent || 'client').trim().toLowerCase(),
            client_reason: String(clientReason || nextReason).trim().toLowerCase(),
            active_toasts: _overlayToastCount(),
          })
        }).catch(()=>{});
      }catch(_e){}
    }

    function refreshIdleFrame(force=false){
      const frame = document.querySelector('.idleFrame');
      if (!frame) return;
      if (!force && !document.body.classList.contains('idle')) return;
      frame.src = `/idle?ts=${Date.now()}`;
    }

    function idleDashboardEnabled(state){
      return !(state && state.idle_dashboard_enabled === false);
    }

    let _statusPollTimer = null;
    let _statusPollInFlight = false;

    function scheduleNowPlayingPoll(delayMs){
      if (_statusPollTimer) clearTimeout(_statusPollTimer);
      const ms = Math.max(250, Number(delayMs || 800));
      _statusPollTimer = setTimeout(refreshNowPlaying, ms);
    }

    async function refreshNowPlaying(){
      if (_statusPollInFlight) return;
      _statusPollInFlight = true;
      let timeoutId = null;
      let nextDelay = 900;
      try{
        const ctrl = new AbortController();
        timeoutId = setTimeout(() => ctrl.abort(), 1200);
        let j = null;
        try {
          const fast = await fetch('/playback/state', {cache:'no-store', signal: ctrl.signal});
          if (fast.ok) j = await fast.json();
        } catch(_e) {}
        if (!j) {
          const r = await fetch('/status', {cache:'no-store', signal: ctrl.signal});
          j = await r.json();
        }
        const isPlaying = overlayPlaybackVisible(j);
        const idleEnabled = idleDashboardEnabled(j);
        if (isPlaying){
          document.body.classList.add('playing');
          document.body.classList.remove('idle');
          _wasPlaying = true;
          nextDelay = 450;
        } else if (idleEnabled) {
          document.body.classList.remove('playing');
          document.body.classList.add('idle');
          if (_wasPlaying) refreshIdleFrame(true);
          _wasPlaying = false;
          nextDelay = 900;
        } else {
          document.body.classList.remove('playing');
          document.body.classList.remove('idle');
          _wasPlaying = false;
          nextDelay = 900;
        }
      }catch(_e){
        nextDelay = 1200;
      }finally{
        if (timeoutId) clearTimeout(timeoutId);
        _statusPollInFlight = false;
        scheduleNowPlayingPoll(nextDelay);
      }
    }
    refreshNowPlaying();

    function addToast(msg){
      const root = $('toasts');
      if(!root) return;
      try{
        const allowedPositions = new Set(['top-right', 'top-left', 'bottom-right', 'bottom-left', 'top-center']);
        const position = allowedPositions.has(msg.position) ? msg.position : 'top-left';
        root.className = `toasts ${position}`;
        const el = document.createElement('div');
        const level = (msg.level || 'info').toLowerCase();
        el.className = `toast ${level === 'success' ? 'success' : level === 'warn' ? 'warn' : level === 'error' ? 'error' : ''}`;
        const top = document.createElement('div'); top.className='toastTop';
        const ico = document.createElement('div'); ico.className='ico'; ico.textContent = iconMap[msg.icon] || iconMap[level] || '•';
        const txt = document.createElement('div'); txt.className='tTxt'; txt.textContent = msg.text || '';
        top.append(ico, txt); el.appendChild(top);
        const linkUrl = msg.link_url;
        const linkText = msg.link_text || msg.link_url;
        if(linkUrl){
          const a = document.createElement('a');
          a.href = linkUrl;
          a.textContent = linkText;
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
          el.appendChild(a);
        }
        const rawImageUrl = String(msg.image_url || '').trim();
        if(overlayAllowToastImages && rawImageUrl){
          const safeImageUrl = /^(https?:\/\/|\/|data:image\/)/i.test(rawImageUrl) ? rawImageUrl : '';
          if(safeImageUrl){
            const img = document.createElement('img');
            img.className = 'img';
            img.alt = '';
            img.decoding = 'async';
            img.loading = 'eager';
            img.referrerPolicy = 'no-referrer';
            let imgSettled = false;
            const dropImg = ()=>{
              if(imgSettled) return;
              imgSettled = true;
              try{ clearTimeout(imgTimeout); }catch(_e){}
              try{ img.removeAttribute('src'); }catch(_e){}
              try{ img.remove(); }catch(_e){}
            };
            img.onload = ()=>{
              if(imgSettled) return;
              imgSettled = true;
              try{ clearTimeout(imgTimeout); }catch(_e){}
              img.classList.add('ready');
            };
            img.onerror = ()=>{ dropImg(); };
            const imgTimeout = setTimeout(()=>{ dropImg(); }, 4000);
            img.src = safeImageUrl;
            el.appendChild(img);
          }
        }
        root.prepend(el);
        while(root.children.length > 4){
          try{ root.lastElementChild?.remove(); }catch(_e){ break; }
        }
        reportOverlayState('displaying', 'toast_visible', 'toast', 'toast_visible', true);
        requestAnimationFrame(()=>el.classList.add('show'));
        const ttlSec = Number(msg.duration || __PLAYBACK_NOTIFY_DISPLAY_SEC__);
        const ttl = Math.min(30000, Math.max(800, Number.isFinite(ttlSec) ? Math.round(ttlSec * 1000) : Math.round(__PLAYBACK_NOTIFY_DISPLAY_SEC__ * 1000)));
        setTimeout(()=>{
          reportOverlayState('draining', 'toast_draining', 'toast', 'toast_draining', true);
          el.classList.remove('show');
          setTimeout(()=>{
            try{ el.remove(); }catch(_e){}
            if(_overlayToastCount() <= 0) reportOverlayState('connected', 'toast_drained', 'toast', 'toast_drained', true);
          }, __PLAYBACK_NOTIFY_FADE_MS__);
        }, ttl);
      }catch(_e){}
    }

    function connectEvents(){
      try{ _overlayEventSource?.close(); }catch(_e){}
      reportOverlayState('retrying', 'connect_start', 'sse', 'connect_start', true);
      const es = new EventSource('/x11/overlay/events');
      _overlayEventSource = es;
      _overlayLastEventTs = Date.now();
      es.onmessage = (ev)=>{
        _overlayLastEventTs = Date.now();
        try{
          const msg=JSON.parse(ev.data || '{}');
          reportOverlayState('connected', 'stream_connected', 'sse', msg.type === 'ping' ? 'stream_ping' : (msg.type || 'stream_event'), true);
          if(msg.type==='toast') addToast(msg);
        }catch(_e){}
      };
      es.onerror = ()=>{
        reportOverlayState('retrying', 'eventsource_error', 'sse', 'eventsource_error', true);
        try{es.close();}catch(_e){}
        if(_overlayEventSource === es) _overlayEventSource = null;
        setTimeout(connectEvents, 1200);
      };
    }
    connectEvents();
    setInterval(()=>{
      if(!_overlayEventSource) return;
      if((Date.now() - _overlayLastEventTs) < 30000) return;
      reportOverlayState('stale', 'stream_stale', 'sse', 'stream_stale', true);
      try{ _overlayEventSource.close(); }catch(_e){}
      _overlayEventSource = null;
      connectEvents();
    }, 10000);
  </script>
</body>
</html>"""

# Temporary playback moved to playback_service (Phase 3 M4). These aliases
# keep existing callers and tests working: the stack/lock are the same
# objects, so in-place mutation is shared with the service.
_TEMP_PLAYBACK_LOCK = playback_service._TEMP_PLAYBACK_LOCK
_TEMP_PLAYBACK_STACK = playback_service._TEMP_PLAYBACK_STACK
_discard_temporary_playback = playback_service.discard_temporary_playback
_discard_interrupted_playback_state = playback_service.discard_interrupted_playback_state
_capture_current_playback_state = playback_service.capture_current_playback_state
_restore_playback_state = playback_service.restore_playback_state
_complete_temporary_playback = playback_service.complete_temporary_playback
_temporary_watchdog = playback_service.temporary_watchdog

# =========================
# API Endpoints
# =========================

@router.post("/overlay")
def overlay(req: OverlayReq):
    text = (req.text or "").strip()
    if not text and req.image_url:
        text = f"[image] {req.image_url}"
    if not text:
        raise HTTPException(status_code=400, detail="text or image_url is required")
    duration_ms = max(250, int(float(req.duration) * 1000.0))
    _ensure_notification_surface(wait_for_subscriber=True)
    # In visual runtimes we use overlay toasts only.
    # In headless runtime, notifications are unavailable.
    x11_overlay_mode = _x11_mode_notifications()
    qt_overlay_mode = _qt_shell_runtime_running()
    caps = _notification_capabilities()
    strategy = str(caps.get("notification_strategy") or _notification_strategy())
    overlay_only_mode = True
    if _overlay_osd_debug_enabled():
        debug_log(
            "osd",
            f"/overlay text_len={len(text)} x11_overlay_mode={x11_overlay_mode} qt_overlay_mode={qt_overlay_mode} strategy={strategy} overlay_only_mode={overlay_only_mode} duration_ms={duration_ms} position={req.position!r}",
        )
    if strategy == "headless":
        raise HTTPException(
            status_code=503,
            detail={
                "error": "notifications_unavailable",
                "reason": "headless_runtime",
                "message": "notifications unavailable in headless runtime",
            },
        )
    delivery_mode = "overlay"
    image_url = _native_qt_toast_image_url(req.image_url)
    native_qt_toast = _overlay_prefers_native_qt_toast(image_url)
    try:
        if native_qt_toast:
            result = player.qt_shell_runtime_overlay_toast(
                text=text,
                duration=float(req.duration),
                level=(req.level or "info"),
                icon=req.icon,
                image_url=image_url,
                link_url=req.link_url,
                link_text=req.link_text,
                position=req.position,
                style=req.style,
            )
            if isinstance(result, dict) and result.get("error") == "success":
                delivery_mode = "native_qt"
                try:
                    if hasattr(state, "update_overlay_delivery_state"):
                        state.update_overlay_delivery_state(
                            "displaying",
                            "native_toast_pushed",
                            client_event="toast",
                            client_reason="native_toast_pushed",
                        )
                except Exception:
                    pass
            else:
                _push_overlay_toast(
                    text=text,
                    duration=float(req.duration),
                    level=(req.level or "info"),
                    icon=req.icon,
                    image_url=None,
                    link_url=req.link_url,
                    link_text=req.link_text,
                    position=req.position,
                    style=req.style,
                )
                delivery_mode = "overlay_fallback"
        else:
            _push_overlay_toast(
                text=text,
                duration=float(req.duration),
                level=(req.level or "info"),
                icon=req.icon,
                image_url=image_url,
                link_url=req.link_url,
                link_text=req.link_text,
                position=req.position,
                style=req.style,
            )
    except Exception:
        try:
            _push_overlay_toast(
                text=text,
                duration=float(req.duration),
                level=(req.level or "info"),
                icon=req.icon,
                image_url=None if native_qt_toast else image_url,
                link_url=req.link_url,
                link_text=req.link_text,
                position=req.position,
                style=req.style,
            )
            delivery_mode = "overlay_fallback"
        except Exception:
            pass
    return {
        "ok": True,
        "duration_ms": duration_ms,
        "position": req.position,
        "style": req.style or {},
        "visual_runtime_mode": str(caps.get("visual_runtime_mode") or _visual_runtime_mode()),
        "notification_strategy": strategy,
        "notifications_available": bool(caps.get("notifications_available")),
        "notifications_reason": str(caps.get("notifications_reason") or ""),
        "overlay_subscribers": int(caps.get("overlay_subscribers") or 0),
        "notifications_deliverable": bool(caps.get("notifications_deliverable")),
        "native_qt_idle_deprecated": bool(caps.get("native_qt_idle_deprecated")),
        "native_qt_idle_status": str(caps.get("native_qt_idle_status") or ""),
        "native_qt_idle_override_enabled": bool(caps.get("native_qt_idle_override_enabled")),
        "native_qt_toasts_deprecated": bool(caps.get("native_qt_toasts_deprecated")),
        "native_qt_toasts_status": str(caps.get("native_qt_toasts_status") or ""),
        "native_qt_toasts_override_enabled": bool(caps.get("native_qt_toasts_override_enabled")),
        "delivery_mode": delivery_mode,
    }

@router.post("/toast")
def toast(req: OverlayReq):
    """Alias for /overlay (mpv OSD + optional X11 overlay)."""
    return overlay(req)


@router.post("/notify")
def notify(req: OverlayReq):
    """Alias for /overlay to map cleanly to Home Assistant relaytv.notify services."""
    return overlay(req)


def _first_nonempty_str(values: list[object]) -> str:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


# Pure Jellyfin helpers (docs/ARCHITECTURE.md)
# live in integrations/jellyfin_service.py. These aliases keep the routes
# compatibility surface and existing monkeypatch targets stable.
_jellyfin_access_token = jellyfin_service.access_token
_extract_jellyfin_play_url = jellyfin_service.extract_play_url
_extract_jellyfin_item_id = jellyfin_service.extract_item_id
_canonical_jellyfin_item_id = jellyfin_service.canonical_item_id
_canonical_jellyfin_media_source_id = jellyfin_service.canonical_media_source_id
_extract_jellyfin_item_id_from_url = jellyfin_service.extract_item_id_from_url
_canonical_jellyfin_url_key = jellyfin_service.canonical_url_key
_extract_jellyfin_media_source_id = jellyfin_service.extract_media_source_id
_extract_jellyfin_item_ids = jellyfin_service.extract_item_ids
_extract_jellyfin_playlist_items = jellyfin_service.extract_playlist_items
_extract_jellyfin_play_mode = jellyfin_service.extract_play_mode
_normalize_jellyfin_action = jellyfin_service.normalize_action
_jellyfin_ticks_to_seconds = jellyfin_service.ticks_to_seconds
_extract_jellyfin_seek_seconds = jellyfin_service.extract_seek_seconds
_extract_jellyfin_start_seconds = jellyfin_service.extract_start_seconds
_extract_jellyfin_command_id = jellyfin_service.extract_command_id
_extract_jellyfin_volume = jellyfin_service.extract_volume
_normalize_jellyfin_source_url = jellyfin_service.normalize_source_url
_build_jellyfin_item_stream_url = jellyfin_service.build_item_stream_url
_build_jellyfin_item_transcode_url = jellyfin_service.build_item_transcode_url
_normalize_jellyfin_playback_mode = jellyfin_service.normalize_playback_mode
_extract_jellyfin_audio_stream_index = jellyfin_service.extract_audio_stream_index
_extract_jellyfin_subtitle_stream_index = jellyfin_service.extract_subtitle_stream_index
_apply_jellyfin_stream_params = jellyfin_service.apply_stream_params
_apply_jellyfin_media_source_param = jellyfin_service.apply_media_source_param
_extract_jellyfin_media_source_id_from_url = jellyfin_service.extract_media_source_id_from_url
_extract_jellyfin_audio_stream_index_from_url = jellyfin_service.extract_audio_stream_index_from_url
_extract_jellyfin_subtitle_stream_index_from_url = jellyfin_service.extract_subtitle_stream_index_from_url
_extract_jellyfin_item_id_from_url_raw = jellyfin_service.extract_item_id_from_url_raw
_jellyfin_url_origin = jellyfin_service.url_origin
_looks_like_jellyfin_media_url = jellyfin_service.looks_like_media_url
_jellyfin_track_type_is_subtitle = jellyfin_service.track_type_is_subtitle
_effective_jellyfin_playback_mode = jellyfin_service.effective_playback_mode
_native_jellyfin_auto_transcode_guard_active = jellyfin_service.native_auto_transcode_guard_active
_jellyfin_target_max_streaming_bitrate = jellyfin_service.target_max_streaming_bitrate
_jellyfin_auto_prefers_transcode = jellyfin_service.auto_prefers_transcode
_select_jellyfin_playback_url = jellyfin_service.select_playback_url
_first_playable_jellyfin_episode = jellyfin_service.first_playable_episode
_resolve_jellyfin_playable_item = jellyfin_service.resolve_playable_item
_normalize_lang_pref = jellyfin_service._normalize_lang_pref
_language_aliases = jellyfin_service._language_aliases
_language_matches = jellyfin_service._language_matches
_preferred_jellyfin_stream_indices = jellyfin_service.preferred_stream_indices
_retarget_jellyfin_queue_stream_preferences = jellyfin_service.retarget_queue_stream_preferences
_is_generic_playback_title = jellyfin_service._is_generic_playback_title
_merge_jellyfin_playback_metadata = jellyfin_service.merge_playback_metadata
_jellyfin_enrich_now_stream_metadata = jellyfin_service.enrich_now_stream_metadata
_jellyfin_try_set_mpv_audio_track = jellyfin_service.try_set_mpv_audio_track
_jellyfin_try_set_mpv_subtitle_track = jellyfin_service.try_set_mpv_subtitle_track
_jellyfin_runtime_selected_audio_stream = jellyfin_service.runtime_selected_audio_stream
_jellyfin_runtime_selected_subtitle_stream = jellyfin_service.runtime_selected_subtitle_stream


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


_jellyfin_emit_progress_hint = jellyfin_service.emit_progress_hint
_jellyfin_complete_ratio = jellyfin_service.complete_ratio
_jellyfin_complete_remaining_sec = jellyfin_service.complete_remaining_sec
_jellyfin_snap_position_ticks = jellyfin_service.snap_position_ticks
_jellyfin_played_percentage = jellyfin_service.played_percentage
_jellyfin_stopped_snapshot_from_now = jellyfin_service.stopped_snapshot_from_now
_jellyfin_stopped_snapshot = jellyfin_service.stopped_snapshot
_jellyfin_emit_stopped_payload = jellyfin_service.emit_stopped_payload
_jellyfin_emit_stopped_hint = jellyfin_service.emit_stopped_hint
_jellyfin_progress_snapshot = jellyfin_service.progress_snapshot
_extract_api_key_from_url = jellyfin_service._extract_api_key_from_url
_smart_item_from_url = jellyfin_service.smart_item_from_url
_reset_jellyfin_command_state = jellyfin_service.reset_command_state
_jellyfin_is_duplicate_command = jellyfin_service.is_duplicate_command
_jellyfin_should_suppress_duplicate_play = jellyfin_service.should_suppress_duplicate_play
_jellyfin_should_suppress_duplicate_ui_action = jellyfin_service.should_suppress_duplicate_ui_action


def _jellyfin_integration_command_impl(req: JellyfinCommandReq):
    """Normalized Jellyfin command ingress; product logic in jellyfin_service.

    This wrapper owns the route-side seams: playback control dispatch and
    UI eventing. The lambdas resolve module globals at call time so test
    monkeypatches on this module keep intercepting.
    """
    return jellyfin_service.handle_command(
        req,
        controls={
            "stop": lambda: stop(),
            "pause": lambda: pause(),
            "resume": lambda: resume(),
            "seek": lambda sec: seek_abs(SeekAbsReq(sec=float(sec))),
            "next": lambda: next_track(),
            "previous": lambda: previous(),
            "set_volume": lambda vol: volume(VolumeReq(set=vol)),
            "mute": lambda muted: mute(MuteReq(set=muted)),
        },
        ui={
            "toast": lambda **kw: _push_overlay_toast(**kw),
            "notification_display_sec": lambda: _playback_notification_display_sec(),
            "queue_event": lambda event, **kw: _ui_event_push_queue(event, **kw),
            "jellyfin_event": lambda event, **kw: _ui_event_push_jellyfin(event, **kw),
        },
    )


_can_preserve_closed_session = playback_service.can_preserve_closed_session


def _idle_dashboard_enabled_for_player() -> bool:
    try:
        return bool(getattr(player, "_idle_dashboard_enabled", lambda: True)())
    except Exception:
        return True


def _idle_notifications_enabled_for_player() -> bool:
    try:
        return bool(getattr(player, "idle_notifications_enabled", lambda: True)())
    except Exception:
        return True


def _idle_visual_surface_enabled_for_player() -> bool:
    try:
        return bool(getattr(player, "idle_visual_surface_enabled", lambda: True)())
    except Exception:
        return _idle_dashboard_enabled_for_player() or _idle_notifications_enabled_for_player()


def _ensure_notification_surface(*, wait_for_subscriber: bool = False) -> None:
    if not _idle_notifications_enabled_for_player():
        return
    try:
        qt_backend = bool(getattr(player, "_qt_shell_backend_enabled", lambda: False)())
        qt_running = bool(getattr(player, "_qt_shell_running", lambda: False)())
    except Exception:
        qt_backend = False
        qt_running = False
    if qt_running:
        try:
            x11_overlay.stop_overlay()
        except Exception:
            pass
    elif _idle_dashboard_enabled_for_player() and qt_backend:
        try:
            player.ensure_qt_shell_idle(force=True, allow_notification_fallback=True)
        except Exception:
            pass
    else:
        try:
            x11_overlay.start_overlay()
        except Exception:
            pass
    try:
        overlay_running = bool(x11_overlay.overlay_running())
    except Exception:
        overlay_running = False
    try:
        if not overlay_running and bool(getattr(player, "_qt_shell_backend_enabled", lambda: False)()):
            player.ensure_qt_shell_idle(force=True, allow_notification_fallback=True)
    except Exception:
        pass
    if not wait_for_subscriber:
        return
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        try:
            if len(_X11_OVERLAY_SUBS) > 0:
                return
        except Exception:
            pass
        try:
            if bool(getattr(player, "_qt_shell_running", lambda: False)()):
                return
        except Exception:
            pass
        time.sleep(0.05)


def _ensure_idle_dashboard_surface() -> None:
    if not _idle_dashboard_enabled_for_player():
        return
    try:
        if bool(getattr(player, "_qt_shell_backend_enabled", lambda: False)()):
            player.ensure_qt_shell_idle(force=True)
            return
    except Exception:
        pass
    try:
        player.start_splash_screen()
    except Exception:
        pass


def _sync_idle_visual_surfaces_after_settings() -> None:
    try:
        playing = bool(player.is_playing())
    except Exception:
        playing = False
    if playing:
        return
    if not _idle_notifications_enabled_for_player():
        try:
            x11_overlay.stop_overlay()
        except Exception:
            pass
    if _idle_visual_surface_enabled_for_player():
        _ensure_idle_dashboard_surface()
        _ensure_notification_surface(wait_for_subscriber=False)
    else:
        try:
            playback_service.stop_all(restart_splash=False)
        except Exception:
            pass




@router.get("/x11/overlay")
def x11_overlay_page():
    """Transparent X11 overlay page (hidden while playing; toast-capable)."""
    html = _X11_OVERLAY_HTML
    html = html.replace("__PLAYBACK_NOTIFY_FADE_MS__", str(_playback_notification_fade_ms()))
    html = html.replace("__PLAYBACK_NOTIFY_DISPLAY_SEC__", str(_playback_notification_display_sec()))
    html = html.replace("__OVERLAY_DEBUG_BG__", _overlay_debug_bg_css())
    html = html.replace("__OVERLAY_ALLOW_IMAGES__", "true" if _overlay_allow_images() else "false")
    html = html.replace("__IDLE_CACHE_BUSTER__", str(int(time.time() * 1000)))
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/idle")
def idle_page():
    html = _idle_html().replace("__IDLE_CATALOG__", _json.dumps(_idle_panel_catalog(), separators=(",", ":"), ensure_ascii=False))
    now = datetime.datetime.now().astimezone()
    offset_minutes = int((now.utcoffset() or datetime.timedelta(0)).total_seconds() // 60)
    html = html.replace("__CLOCK_OFFSET_MINUTES__", str(offset_minutes))
    html = html.replace("__SERVER_NOW_MS__", str(int(time.time() * 1000)))
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

@router.get("/x11/overlay/events")
async def x11_overlay_events():
    return await _x11_overlay_sse()


@router.post("/x11/overlay/client_state")
def x11_overlay_client_state(req: OverlayClientStateReq):
    next_state = str(req.state or "connected").strip().lower() or "connected"
    reason = str(req.reason or "client_report").strip().lower() or "client_report"
    client_event = str(req.client_event or "client").strip().lower() or "client"
    client_reason = str(req.client_reason or reason).strip().lower()
    allowed = {"headless", "disconnected", "connected", "displaying", "stale", "retrying", "draining"}
    if next_state not in allowed:
        next_state = "connected"
        reason = "client_report_normalized"
    info = (
        state.update_overlay_delivery_state(
            next_state,
            reason,
            client_event=client_event,
            client_reason=client_reason,
        )
        if hasattr(state, "update_overlay_delivery_state")
        else {}
    )
    return {
        "ok": True,
        "active_toasts": max(0, int(req.active_toasts or 0)),
        **info,
    }


@router.get("/x11/host_urls")
def x11_host_urls():
    urls = _host_urls()
    public = _public_host_urls()
    return {
        "urls": urls,
        "public_urls": public,
        "primary": (public[0] if public else (urls[0] if urls else None)),
    }


@router.get("/qr/connect.svg")
def qr_connect_svg(request: Request, u: str | None = None, logo: int = 1):
    target = str(u or "").strip()
    if not target:
        target = _best_connect_url(request)
    # Normalize to a UI endpoint so scans always land on the remote UI.
    try:
        parsed = urlsplit(target)
        if not parsed.path or parsed.path == "/":
            target = urlunsplit((parsed.scheme, parsed.netloc, "/ui", "", ""))
    except Exception:
        pass
    svg = _render_connect_qr_svg(target, include_logo=(int(logo) != 0))
    return Response(
        svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


# ---- IPC controls (used by web UI and optional HTTP Shortcuts later)

def _control_ack_payload(result: dict | None) -> dict[str, object]:
    if not isinstance(result, dict):
        return {}
    payload: dict[str, object] = {}
    request_id = str(result.get("request_id") or "").strip()
    if request_id:
        payload["request_id"] = request_id
    if "ack_observed" in result:
        payload["ack_observed"] = bool(result.get("ack_observed"))
    ack_reason = str(result.get("ack_reason") or "").strip()
    if ack_reason:
        payload["ack_reason"] = ack_reason
    return payload


def _control_result_or_raise(result: dict | None, *, action: str) -> dict[str, object]:
    if not isinstance(result, dict) or result.get("error") != "success":
        raise HTTPException(status_code=500, detail=f"{action} failed: {result}")
    return dict(result)


def _resume_paused_current_session_in_place(*, action: str = "resume") -> dict[str, object] | None:
    result = playback_service.resume_paused_in_place()
    if result is None:
        return None
    return {
        "ok": True,
        "action": action,
        "paused": False,
        "now_playing": state.NOW_PLAYING,
        **_control_ack_payload(result),
    }


def _seek_transition_hold_sec() -> float:
    raw = (os.getenv("RELAYTV_SEEK_TRANSITION_HOLD_SEC") or "").strip()
    if raw:
        try:
            return max(2.0, min(float(raw), 20.0))
        except Exception:
            pass
    return 6.0


def _qt_runtime_seek_via_time_pos(target_sec: float) -> dict[str, object] | None:
    try:
        target = float(target_sec)
    except Exception:
        return None
    if not math.isfinite(target):
        return None
    try:
        if not player._qt_shell_runtime_accepts_mpv_commands():
            return None
    except Exception:
        return None
    try:
        props = player.mpv_get_many(["time-pos", "duration"])
    except Exception:
        props = {}
    duration = None
    try:
        raw_duration = props.get("duration") if isinstance(props, dict) else None
        if raw_duration is not None:
            duration = float(raw_duration)
    except Exception:
        duration = None
    if duration is not None and math.isfinite(duration) and duration > 0.0:
        target = min(max(0.0, target), duration)
    else:
        target = max(0.0, target)
    return _control_result_or_raise(player.mpv_set_result("time-pos", target), action="seek")


def _seek_relative_result(delta_sec: float) -> dict[str, object]:
    try:
        delta = float(delta_sec)
    except Exception:
        delta = 0.0
    if math.isfinite(delta):
        try:
            props = player.mpv_get_many(["time-pos", "duration"])
        except Exception:
            props = {}
        try:
            current = float((props or {}).get("time-pos"))
        except Exception:
            current = None
        if current is not None and math.isfinite(current):
            result = _qt_runtime_seek_via_time_pos(current + delta)
            if isinstance(result, dict):
                return result
    return _control_result_or_raise(player.mpv_command(["seek", float(delta_sec), "relative"]), action="seek")


def _seek_absolute_result(target_sec: float) -> dict[str, object]:
    result = _qt_runtime_seek_via_time_pos(target_sec)
    if isinstance(result, dict):
        return result
    return _control_result_or_raise(player.mpv_command(["seek", float(target_sec), "absolute"]), action="seek_abs")


def _session_playing_fast() -> tuple[str, bool, bool]:
    """Cheap playing-state estimate for high-frequency UI polling."""
    sess = str(getattr(state, "SESSION_STATE", "idle") or "idle").strip().lower() or "idle"
    paused = sess == "paused"
    if bool(getattr(player, "startup_session_restore_pending", lambda: False)()):
        return sess, False, paused
    playing = sess in ("playing", "paused")
    try:
        explicit_stop_hold = float(getattr(state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0) or 0.0) > (time.time() + 60.0)
    except Exception:
        explicit_stop_hold = False
    has_now_playing = isinstance(getattr(state, "NOW_PLAYING", None), dict)
    queue_length = len(getattr(state, "QUEUE", []) or [])
    natural_idle_hold = bool(getattr(player, "natural_idle_reset_holding", lambda: False)())
    if explicit_stop_hold and sess == "closed":
        return sess, False, False
    if explicit_stop_hold and (not has_now_playing) and queue_length <= 0 and sess in ("idle", "closed"):
        return sess, False, False
    if natural_idle_hold and (not has_now_playing) and queue_length <= 0 and sess in ("idle", "closed"):
        return sess, False, False
    if not playing:
        try:
            if bool(getattr(player, "playback_transitioning", lambda: False)()):
                playing = True
        except Exception:
            pass
    if not playing and isinstance(getattr(state, "NOW_PLAYING", None), dict):
        if sess not in ("idle", "closed"):
            playing = True
    return sess, playing, paused


def _derive_playback_runtime_state(
    *,
    sess: str,
    playing: bool,
    paused: bool,
    has_now_playing: bool,
    queue_length: int,
    transition_active: bool = False,
    telemetry_source: str = "none",
    telemetry_freshness: str = "unknown",
    backend_ready: bool | None = None,
) -> tuple[str, str]:
    sess_val = str(sess or "idle").strip().lower() or "idle"
    source_val = str(telemetry_source or "none").strip().lower() or "none"
    freshness_val = str(telemetry_freshness or "unknown").strip().lower() or "unknown"
    transition_reason = "queue_handoff" if int(queue_length or 0) > 0 else "play_transition"

    if sess_val == "closed":
        return "closed", "session_closed"
    if backend_ready is False and has_now_playing and sess_val not in ("idle", "closed") and not transition_active:
        return "degraded", "backend_not_ready"
    if playing:
        if paused:
            return "paused", ("runtime_paused" if source_val != "none" else "session_paused")
        if transition_active:
            return "buffering", transition_reason
        if freshness_val == "stale":
            return "degraded", "telemetry_stale"
        return "playing", ("runtime_active" if source_val != "none" else "session_active")
    if transition_active:
        return "buffering", transition_reason
    if source_val == "qt_runtime_stale" or freshness_val == "stale":
        return "degraded", "telemetry_stale"
    if has_now_playing:
        if sess_val in ("playing", "paused"):
            return "buffering", "session_runtime_gap"
        if sess_val not in ("idle", "closed"):
            return "buffering", "session_open_no_media"
    return "idle", "no_active_session"


def _playback_state_fast_snapshot() -> dict[str, object]:
    sess, playing, paused = _session_playing_fast()
    has_now_playing = isinstance(getattr(state, "NOW_PLAYING", None), dict)
    queue_length = len(getattr(state, "QUEUE", []) or [])
    transition_active = False
    try:
        manual_transition = bool(getattr(player, "playback_transitioning", lambda: False)())
        queue_handoff_transition = (
            ((sess in ("playing", "paused")) or has_now_playing)
            and bool(getattr(player, "auto_next_transitioning", lambda: False)())
        )
        transition_active = bool(manual_transition or queue_handoff_transition)
    except Exception:
        transition_active = False
    try:
        explicit_stop_hold = float(getattr(state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0) or 0.0) > (time.time() + 60.0)
    except Exception:
        explicit_stop_hold = False
    natural_idle_hold = bool(getattr(player, "natural_idle_reset_holding", lambda: False)())
    closed_stop_hold = explicit_stop_hold and sess == "closed"
    natural_idle_clear_hold = natural_idle_hold and queue_length <= 0 and (not has_now_playing) and sess in ("idle", "closed")
    payload: dict[str, object] = {
        "state": sess,
        "idle_dashboard_enabled": bool((state.get_settings() if hasattr(state, "get_settings") else {}).get("idle_dashboard_enabled", True)),
        "idle_notifications_enabled": bool((state.get_settings() if hasattr(state, "get_settings") else {}).get("idle_notifications_enabled", True)),
        "playing": bool(playing),
        "paused": bool(paused),
        "has_now_playing": has_now_playing,
        "queue_length": queue_length,
        "playback_telemetry_source": "none",
        "playback_telemetry_freshness": "unknown",
        "position": None,
        "duration": None,
        "volume": None,
        "mute": None,
        "backend_ready": None,
        "native_qt_telemetry_selected": False,
        "native_qt_mpv_runtime_playback_active": None,
        "native_qt_mpv_runtime_stream_loaded": None,
        "native_qt_mpv_runtime_playback_started": None,
        "transitioning_between_items": transition_active,
        "transition_in_progress": transition_active,
        "ts": int(time.time() * 1000),
    }
    try:
        qt_runtime = dict(getattr(player, "qt_shell_runtime_telemetry", lambda **_: {})() or {})
    except Exception:
        qt_runtime = {}

    field_map = (
        ("position", "mpv_runtime_time_pos"),
        ("duration", "mpv_runtime_duration"),
        ("volume", "mpv_runtime_volume"),
        ("mute", "mpv_runtime_mute"),
    )

    def fill_from_mpv_ipc(*, force: bool = False) -> bool:
        if not force and not (bool(payload.get("playing")) or bool(payload.get("paused"))):
            return False
        try:
            props = player.mpv_get_many(["pause", "volume", "mute", "time-pos", "duration"])
        except Exception:
            props = {}
        if not isinstance(props, dict):
            return False
        filled = False
        fallback_map = {
            "position": "time-pos",
            "duration": "duration",
            "volume": "volume",
            "mute": "mute",
        }
        for field, key in fallback_map.items():
            value = props.get(key)
            if value is not None:
                payload[field] = value
                filled = True
        fallback_paused = props.get("pause")
        if isinstance(fallback_paused, bool):
            payload["paused"] = fallback_paused
            filled = True
        if filled:
            payload["playback_telemetry_source"] = "mpv_ipc"
            payload["playback_telemetry_freshness"] = "unknown"
            payload["backend_ready"] = True
        return filled

    if not bool(qt_runtime.get("selected")):
        fill_from_mpv_ipc()
        runtime_state, runtime_reason = _derive_playback_runtime_state(
            sess=sess,
            playing=bool(payload.get("playing")),
            paused=bool(payload.get("paused")),
            has_now_playing=has_now_playing,
            queue_length=queue_length,
            transition_active=transition_active,
            telemetry_source=str(payload.get("playback_telemetry_source") or "none"),
            telemetry_freshness=str(payload.get("playback_telemetry_freshness") or "unknown"),
            backend_ready=payload.get("backend_ready") if payload.get("backend_ready") is not None else None,
        )
        payload.update(state.update_playback_runtime_state(runtime_state, runtime_reason))
        return payload

    freshness = str(qt_runtime.get("freshness") or "unknown")
    source = "none"
    if bool(qt_runtime.get("available")):
        source = "qt_runtime"
    elif freshness == "stale":
        source = "qt_runtime_stale"
    if source != "none":
        payload["playback_telemetry_source"] = source
        payload["playback_telemetry_freshness"] = freshness
    payload["native_qt_telemetry_selected"] = bool(qt_runtime.get("selected"))
    payload["native_qt_mpv_runtime_playback_active"] = qt_runtime.get("mpv_runtime_playback_active")
    payload["native_qt_mpv_runtime_stream_loaded"] = qt_runtime.get("mpv_runtime_stream_loaded")
    payload["native_qt_mpv_runtime_playback_started"] = qt_runtime.get("mpv_runtime_playback_started")
    payload["backend_ready"] = bool(qt_runtime.get("available")) if qt_runtime.get("selected") is not None else None

    for field, key in field_map:
        value = qt_runtime.get(key)
        if value is not None:
            payload[field] = value

    runtime_paused = qt_runtime.get("mpv_runtime_paused")
    if isinstance(runtime_paused, bool):
        payload["paused"] = runtime_paused

    runtime_playing = any(
        qt_runtime.get(key) is True
        for key in ("mpv_runtime_playback_active", "mpv_runtime_stream_loaded", "mpv_runtime_playback_started")
    )
    sample_detail = str(qt_runtime.get("mpv_runtime_sample_detail") or "").strip().lower()
    missing_runtime_fields = [field for field, _key in field_map if payload.get(field) is None]
    if (bool(payload.get("playing")) or runtime_playing) and (missing_runtime_fields or sample_detail.startswith("subprocess_runtime")):
        fill_from_mpv_ipc(force=runtime_playing)
    if runtime_playing and not closed_stop_hold and not natural_idle_clear_hold:
        payload["playing"] = True
        payload["state"] = "paused" if bool(payload.get("paused")) else "playing"
    elif closed_stop_hold:
        transition_active = False
        payload["playing"] = False
        payload["paused"] = False
        payload["state"] = "closed"
        payload["native_qt_mpv_runtime_playback_active"] = False
        payload["native_qt_mpv_runtime_stream_loaded"] = False
        payload["native_qt_mpv_runtime_playback_started"] = False
    elif natural_idle_clear_hold:
        payload["playing"] = False
        payload["paused"] = False
        payload["state"] = "idle"
    transition_active = bool(transition_active)
    payload["transitioning_between_items"] = transition_active
    payload["transition_in_progress"] = transition_active
    runtime_state, runtime_reason = _derive_playback_runtime_state(
        sess=str(payload.get("state") or sess),
        playing=bool(payload.get("playing")),
        paused=bool(payload.get("paused")),
        has_now_playing=has_now_playing,
        queue_length=queue_length,
        transition_active=transition_active,
        telemetry_source=str(payload.get("playback_telemetry_source") or "none"),
        telemetry_freshness=str(payload.get("playback_telemetry_freshness") or "unknown"),
        backend_ready=payload.get("backend_ready") if payload.get("backend_ready") is not None else None,
    )
    payload.update(state.update_playback_runtime_state(runtime_state, runtime_reason))
    return payload


def _status_payload() -> dict[str, object]:
    settings_snapshot = state.get_settings() if hasattr(state, "get_settings") else {}
    with state.QUEUE_LOCK:
        q = list(state.QUEUE)
    sess = getattr(state, "SESSION_STATE", "idle")
    has_now_playing = isinstance(getattr(state, "NOW_PLAYING", None), dict)
    try:
        explicit_stop_hold = float(getattr(state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0) or 0.0) > (time.time() + 60.0)
    except Exception:
        explicit_stop_hold = False
    natural_idle_hold = bool(getattr(player, "natural_idle_reset_holding", lambda: False)())
    playing = player.is_playing()
    transitioning_between_items = False
    try:
        manual_transition = bool(getattr(player, "playback_transitioning", lambda: False)())
        queue_handoff_transition = (
            ((sess in ("playing", "paused")) or has_now_playing)
            and bool(getattr(player, "auto_next_transitioning", lambda: False)())
        )
        if (
            (not playing)
            and bool(getattr(player, "_qt_shell_backend_enabled", lambda: False)())
            and (sess != "closed")
            and (manual_transition or queue_handoff_transition)
        ):
            # Qt startup/handoff gaps: keep UI in playing mode to avoid idle flashes.
            playing = True
            transitioning_between_items = True
    except Exception:
        transitioning_between_items = False
    if explicit_stop_hold and str(sess or "idle").strip().lower() == "closed":
        playing = False
        transitioning_between_items = False
    elif explicit_stop_hold and (not has_now_playing) and (not q) and str(sess or "idle").strip().lower() in ("idle", "closed"):
        playing = False
        transitioning_between_items = False
    elif natural_idle_hold and (not has_now_playing) and (not q) and str(sess or "idle").strip().lower() in ("idle", "closed"):
        playing = False
        transitioning_between_items = False
    runtime = _runtime_capabilities(playing=playing)
    effective_ytdlp_format = None
    try:
        effective_ytdlp_format = str(getattr(player, "_effective_ytdl_format", lambda s=None: "")(settings_snapshot) or "")
    except Exception:
        effective_ytdlp_format = ""
    props: dict[str, object] = {}
    if playing:
        props = player.mpv_get_many(["pause", "volume", "mute", "time-pos", "duration"])
    paused = bool(props.get("pause")) if playing else False
    if playing and "pause" not in props:
        native_qt_paused = runtime.get("native_qt_mpv_runtime_paused")
        if isinstance(native_qt_paused, bool):
            paused = native_qt_paused
    # Lightweight session state (Phase 1 UX)
    if playing:
        sess = "paused" if paused else "playing"
        state.set_session_state(sess)
    elif sess not in ("closed",):
        if bool(getattr(player, "startup_session_restore_pending", lambda: False)()):
            # UI/status polling begins before the display runtime is ready.
            # Preserve the persisted candidate until the autoplay worker can
            # restore it instead of demoting it to idle and losing resume.
            paused = sess == "paused"
        elif sess == "paused" and isinstance(state.NOW_PLAYING, dict):
            # Preserve an explicit paused session during runtime telemetry gaps.
            # The autoplay worker treats idle as a natural end, so status/SSE
            # must not demote a resumable current item back to idle.
            playing = True
            paused = True
            state.set_session_state("paused")
        else:
            native_active = False
            try:
                native_active = bool(
                    getattr(player, "_qt_runtime_active", lambda **_: False)(
                        require_active_session=False
                    )
                )
            except Exception:
                native_active = False
            if native_active and isinstance(state.NOW_PLAYING, dict):
                sess = "playing"
                state.set_session_state(sess)
            else:
                sess = "idle"
                state.set_session_state(sess)
    now_playing = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else state.NOW_PLAYING
    # Cleanup stale resumable-close markers when session is no longer "closed".
    if (
        (not playing)
        and sess != "closed"
        and isinstance(now_playing, dict)
        and bool(now_playing.get("closed"))
    ):
        now_playing = None
        try:
            state.set_now_playing(None)
        except Exception:
            pass
    resume_avail = (sess == "closed") and bool(state.NOW_PLAYING)
    vol = props.get("volume") if playing else None
    mute = props.get("mute") if playing else None
    pos = props.get("time-pos") if playing else None
    dur = props.get("duration") if playing else None
    if playing and pos is None:
        native_qt_pos = runtime.get("native_qt_mpv_runtime_time_pos")
        if isinstance(native_qt_pos, (int, float)):
            pos = float(native_qt_pos)
    if playing and dur is None:
        native_qt_dur = runtime.get("native_qt_mpv_runtime_duration")
        if isinstance(native_qt_dur, (int, float)):
            dur = float(native_qt_dur)
    mdns = discovery_mdns.status()
    jf_status: dict[str, object] = {}
    try:
        jf_status = jellyfin_receiver.status() or {}
    except Exception:
        jf_status = {}
    jf_enabled = bool(jf_status.get("enabled"))
    jf_running = bool(jf_status.get("running"))
    jf_connected = bool(jf_status.get("connected"))
    jf_authenticated = bool(jf_status.get("authenticated"))
    jf_sync_health = str(jf_status.get("sync_health") or "")
    jf_sync_health_reason = str(jf_status.get("sync_health_reason") or "")
    jf_last_sync_age_sec = jf_status.get("last_sync_age_sec")
    jf_stopped_suppressed_count = int(jf_status.get("stopped_suppressed_count") or 0)
    jf_stopped_dedupe_enabled = bool(jf_status.get("stopped_dedupe_enabled"))
    jf_stopped_dedupe_window_sec = jf_status.get("stopped_dedupe_window_sec")
    jf_complete_ratio = jf_status.get("complete_ratio")
    jf_complete_remaining_sec = jf_status.get("complete_remaining_sec")
    jf_catalog_user_id = str(jf_status.get("catalog_user_id") or "")
    jf_catalog_user_source = str(jf_status.get("catalog_user_source") or "none")
    jf_catalog_cache_entries = int(jf_status.get("catalog_cache_entries") or 0)
    jf_catalog_cache_max_entries = int(jf_status.get("catalog_cache_max_entries") or 0)
    jf_catalog_cache_clears = int(jf_status.get("catalog_cache_clears") or 0)
    jf_catalog_cache_last_cleared_ts = jf_status.get("catalog_cache_last_cleared_ts")
    jf_last_error = str(jf_status.get("last_error") or "")
    jf_server_type = str(jf_status.get("server_type") or settings_snapshot.get("jellyfin_server_type") or "jellyfin").strip().lower()
    if jf_server_type not in ("jellyfin", "emby"):
        jf_server_type = "jellyfin"
    jf_server_url_configured = bool(
        str(jf_status.get("server_url") or "").strip() or str(settings_snapshot.get("jellyfin_server_url") or "").strip()
    )
    playback_telemetry_source = "none"
    playback_telemetry_freshness = "unknown"
    native_qt_runtime_mode = bool(
        runtime.get("player_backend") == "qt"
        and str(runtime.get("qt_runtime_mode_effective") or "") != "external_mpv"
    )
    if playing:
        native_qt_source = str(runtime.get("native_qt_telemetry_source") or "")
        if native_qt_source and native_qt_source != "none":
            playback_telemetry_source = native_qt_source
            playback_telemetry_freshness = str(runtime.get("native_qt_telemetry_freshness") or "unknown")
        elif (not native_qt_runtime_mode) and bool(runtime.get("ipc_socket_exists")):
            playback_telemetry_source = "mpv_ipc"
            playback_telemetry_freshness = "unknown"
    include_mpv_log_tail = str(os.getenv("RELAYTV_STATUS_INCLUDE_MPV_LOG", "0") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    runtime_state_info = state.update_playback_runtime_state(
        *_derive_playback_runtime_state(
            sess=str(sess or "idle"),
            playing=bool(playing),
            paused=bool(paused),
            has_now_playing=isinstance(now_playing, dict),
            queue_length=len(q),
            transition_active=bool(transitioning_between_items),
            telemetry_source=playback_telemetry_source,
            telemetry_freshness=playback_telemetry_freshness,
            backend_ready=runtime.get("backend_ready") if isinstance(runtime, dict) else None,
        )
    )
    annotated_now_playing = _annotate_upload_item(now_playing)
    annotated_queue = _annotate_upload_items(q)
    return {
        "state": sess,
        "device_name": str(settings_snapshot.get("device_name") or "RelayTV"),
        "idle_dashboard_enabled": bool(settings_snapshot.get("idle_dashboard_enabled", True)),
        "idle_notifications_enabled": bool(settings_snapshot.get("idle_notifications_enabled", True)),
        "mdns_advertising": bool(mdns.get("active")),
        "mdns_service_type": str(mdns.get("service_type") or ""),
        "jellyfin_enabled": jf_enabled,
        "jellyfin_running": jf_running,
        "jellyfin_connected": jf_connected,
        "jellyfin_authenticated": jf_authenticated,
        "jellyfin_sync_health": jf_sync_health,
        "jellyfin_sync_health_reason": jf_sync_health_reason,
        "jellyfin_last_sync_age_sec": jf_last_sync_age_sec,
        "jellyfin_stopped_suppressed_count": jf_stopped_suppressed_count,
        "jellyfin_stopped_dedupe_enabled": jf_stopped_dedupe_enabled,
        "jellyfin_stopped_dedupe_window_sec": jf_stopped_dedupe_window_sec,
        "jellyfin_complete_ratio": jf_complete_ratio,
        "jellyfin_complete_remaining_sec": jf_complete_remaining_sec,
        "jellyfin_catalog_user_id": jf_catalog_user_id,
        "jellyfin_catalog_user_source": jf_catalog_user_source,
        "jellyfin_catalog_cache_entries": jf_catalog_cache_entries,
        "jellyfin_catalog_cache_max_entries": jf_catalog_cache_max_entries,
        "jellyfin_catalog_cache_clears": jf_catalog_cache_clears,
        "jellyfin_catalog_cache_last_cleared_ts": jf_catalog_cache_last_cleared_ts,
        "jellyfin_last_error": jf_last_error,
        "jellyfin_server_type": jf_server_type,
        "jellyfin_server_url_configured": jf_server_url_configured,
        "jellyfin_playback_mode": _effective_jellyfin_playback_mode(settings_snapshot),
        "pause_reason": state.get_pause_reason() if hasattr(state, "get_pause_reason") else None,
        "resume_available": resume_avail,
        # Same authoritative signal the fast snapshot carries; without it the
        # client's fast/full views can disagree at idle and flap the UI.
        "has_now_playing": isinstance(now_playing, dict),
        "playing": playing,
        "paused": paused,
        "playback_telemetry_source": playback_telemetry_source,
        "playback_telemetry_freshness": playback_telemetry_freshness,
        "volume": vol,
        "mute": mute,
        "position": pos,
        "duration": dur,
        "now_playing": annotated_now_playing,
        "queue": annotated_queue,
        "queue_length": len(q),
        "transitioning_between_items": transitioning_between_items,
        "transition_in_progress": bool(transitioning_between_items),
        "last_transition_reason": str(runtime_state_info.get("playback_runtime_state_reason") or ""),
        "mpv_log_tail": player.get_mpv_log_tail(40) if include_mpv_log_tail else [],
        **runtime_state_info,
        **runtime,
        "effective_ytdlp_format": effective_ytdlp_format,
    }


@router.get("/status")
def status():
    return _status_payload()


async def _ui_events_sse(request: Request) -> object:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _UI_EVENT_SUBS.add(q)

    async def gen():
        last_fast_json = ""
        last_status_json = ""
        last_has_now_playing = None
        last_queue_length = None
        last_full_ts = 0.0
        last_emit_ts = 0.0

        try:
            hello = _json.dumps({"type": "hello", "ts": time.time()}, separators=(",", ":"), ensure_ascii=False)
            yield f"event: hello\ndata: {hello}\n\n"
            last_emit_ts = time.time()

            while True:
                if await request.is_disconnected():
                    break

                try:
                    event_name, payload = await asyncio.wait_for(q.get(), timeout=0.75)
                    yield f"event: {event_name}\ndata: {payload}\n\n"
                    last_emit_ts = time.time()
                    while True:
                        try:
                            event_name, payload = q.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        yield f"event: {event_name}\ndata: {payload}\n\n"
                        last_emit_ts = time.time()
                except asyncio.TimeoutError:
                    pass

                now_ts = time.time()
                fast = _playback_state_fast_snapshot()
                fast_json = _json.dumps(fast, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
                queue_length = int(fast.get("queue_length") or 0)
                has_now_playing = bool(fast.get("has_now_playing"))
                force_full = (
                    (last_queue_length is None)
                    or (queue_length != last_queue_length)
                    or (has_now_playing != last_has_now_playing)
                    or ((now_ts - last_full_ts) >= 5.0)
                )

                if fast_json != last_fast_json:
                    last_fast_json = fast_json
                    yield f"event: playback\ndata: {fast_json}\n\n"
                    last_emit_ts = now_ts

                if force_full:
                    full = _status_payload()
                    full_json = _json.dumps(full, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
                    if full_json != last_status_json:
                        last_status_json = full_json
                        yield f"event: status\ndata: {full_json}\n\n"
                        last_emit_ts = time.time()
                    last_full_ts = time.time()
                    last_queue_length = queue_length
                    last_has_now_playing = has_now_playing

                # Idle ping cadence must stay well inside the client's health
                # window (app.js _uiEventHealthy) or a quiet stream reads as dead.
                if (time.time() - last_emit_ts) >= 5.0:
                    ping = _json.dumps({"type": "ping", "ts": time.time()}, separators=(",", ":"), ensure_ascii=False)
                    yield f"event: ping\ndata: {ping}\n\n"
                    last_emit_ts = time.time()
        finally:
            _UI_EVENT_SUBS.discard(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/ui/events")
async def ui_events(request: Request):
    return await _ui_events_sse(request)


@router.get("/idle/weather")
def get_idle_weather():
    settings = state.get_settings() if hasattr(state, "get_settings") else {}
    idle_panels = settings.get("idle_panels") if isinstance(settings, dict) else {}
    weather_panel = idle_panels.get("weather") if isinstance(idle_panels, dict) else {}
    if not (isinstance(weather_panel, dict) and weather_panel.get("enabled")):
        raise HTTPException(status_code=404, detail="weather panel disabled")
    req = urllib.request.Request(_idle_weather_proxy_url(settings), headers={"User-Agent": "RelayTV/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = _json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.warning("idle_weather_proxy_failed error=%s", exc)
        raise HTTPException(status_code=502, detail="weather fetch failed") from exc
    return JSONResponse(payload)


@router.get("/ui")
def ui():
    html = r"""<!doctype html>
<html lang="en">
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="theme-color" content="#05070d" />
  <link rel="manifest" href="/manifest.json" />
  <meta name="mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <link rel="icon" type="image/svg+xml" href="/pwa/brand/logo.svg?v=2" />
  <link rel="shortcut icon" href="/pwa/brand/logo.svg?v=2" />
  <link rel="apple-touch-icon" href="/pwa/brand/logo.svg?v=2" />
  <title>RelayTV</title>
  <link rel="stylesheet" href="/static/ui/app.css?v=__UI_ASSET_V__" />
  <link rel="stylesheet" href="/static/ui/jellyfin.css?v=__UI_ASSET_V__" />
  <script>
    if ('serviceWorker' in navigator) {
      window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js').catch(()=>{});
      });
    }
  </script>
</head>
<body>
  <div class="wrap">
    <header>
      <div class="hdrBrand">
        <span class="hdrKicker" aria-hidden="true">RelayTV</span>
        <h1 id="appBrandName">RelayTV</h1>
      </div>
      <div class="hdrRight">
        <button id="jellyfinOpenBtn" class="jfLaunch" title="Open Jellyfin" aria-label="Open Jellyfin"><span class="jfDot" aria-hidden="true"></span><span class="jfBrand">Jellyfin</span></button>
        <button id="addUrlBtn" class="hdrAddBtn" title="Add URL" aria-label="Add URL">＋</button>
        <div id="hdrMenuWrap" class="hdrMenuWrap">
          <button id="hdrMenuBtn" class="hdrMenuBtn" title="Menu" aria-label="Menu" aria-expanded="false" aria-haspopup="menu" aria-controls="hdrMenuPanel">
            <svg class="menuGlyph" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/></svg>
          </button>
          <div id="hdrMenuPanel" class="hdrMenuPanel hidden" role="menu" aria-label="Header menu">
            <button id="histBtn" class="hdrMenuItem" role="menuitem" title="History"><svg class="miIcon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 5v4H8.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"/><path d="M5.2 9a8 8 0 1 1-1.1 4.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" fill="none"/><path d="M12 8.5V12l2.6 1.7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>History</button>
            <button id="aboutBtn" class="hdrMenuItem" role="menuitem" title="About RelayTV"><svg class="miIcon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.6" stroke="currentColor" stroke-width="1.8" fill="none"/><path d="M12 11.2v5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" fill="none"/><circle cx="12" cy="7.9" r="1.15" fill="currentColor"/></svg>About</button>
            <button id="settingsBtn" class="hdrMenuItem" role="menuitem" title="Settings"><svg class="miIcon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h8m6 0h2M4 12h2m6 0h8M4 17h10m6 0h0" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" fill="none"/><circle cx="15" cy="7" r="2" stroke="currentColor" stroke-width="1.8" fill="none"/><circle cx="9" cy="12" r="2" stroke="currentColor" stroke-width="1.8" fill="none"/><circle cx="17" cy="17" r="2" stroke="currentColor" stroke-width="1.8" fill="none"/></svg>Settings</button>
            <div class="hdrMenuTheme" role="group" aria-label="Theme">
              <span class="mtLabel">Theme</span>
              <div class="mtSeg">
                <button type="button" class="mtBtn" data-theme-mode="auto" role="menuitemradio" aria-checked="true">Auto</button>
                <button type="button" class="mtBtn" data-theme-mode="dark" role="menuitemradio" aria-checked="false">Dark</button>
                <button type="button" class="mtBtn" data-theme-mode="light" role="menuitemradio" aria-checked="false">Light</button>
              </div>
            </div>
            <div class="hdrMenuFoot"><span id="menuDeviceName">RelayTV</span><span id="menuAppVersion"></span></div>
          </div>
        </div>
      </div>
    </header>

    <div id="connBadge" class="connBadge hidden" role="status" aria-live="polite">Reconnecting…</div>

    <!-- Hidden by default: manual URL modal (opened via ＋ button) -->
    <div id="addBackdrop" class="modalBackdrop hidden" role="dialog" aria-modal="true">
      <div class="modal addModal">
        <div class="modalTop">
          <div class="modalTitle">Send to TV</div>
          <div class="modalBtns">
            <button id="addCloseBtn" class="iconBtn sm" title="Close" aria-label="Close">✕</button>
          </div>
        </div>

        <section class="amSection">
          <div class="amHead"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 14a5 5 0 0 0 7.07 0l2.83-2.83a5 5 0 0 0-7.07-7.07L11.5 5.43" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" fill="none"/><path d="M14 10a5 5 0 0 0-7.07 0L4.1 12.83a5 5 0 0 0 7.07 7.07l1.32-1.33" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" fill="none"/></svg><span>Play a link</span></div>
          <div class="fieldRow">
            <input id="addUrlInput" class="urlInput" type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="Paste a video URL…" />
            <button id="addPasteBtn" class="iconBtn sm" title="Paste from clipboard" aria-label="Paste">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                <path d="M9 4h6a2 2 0 0 1 2 2v2H7V6a2 2 0 0 1 2-2Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>
                <path d="M7 8H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V10a2 2 0 0 0-2-2h-1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              </svg>
            </button>
          </div>
          <div class="modalBtns amActions">
            <button id="addQueueBtn" title="Add to queue">Queue</button>
            <button id="addPlayBtn" class="good" title="Play now">Play</button>
          </div>
          <div id="addHelperTxt" class="helperTxt" data-default="Tip: Clipboard paste works automatically on modern browsers (https/PWA/localhost only). “Queue” keeps the current playback.">Tip: Clipboard paste works automatically on modern browsers (https/PWA/localhost only). “Queue” keeps the current playback.</div>
        </section>

        <section id="notifySection" class="amSection">
          <div class="amHead"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"/><path d="M13.7 21a2 2 0 0 1-3.4 0" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" fill="none"/></svg><span>Toast notification</span></div>
          <div class="notifyField">
            <label for="notifyTextInput">Text</label>
            <textarea id="notifyTextInput" class="notifyInput" maxlength="500" placeholder="Notification text…"></textarea>
          </div>
          <div class="notifyGrid">
            <div class="notifyField">
              <label for="notifyImageInput">Image (optional)</label>
              <input id="notifyImageInput" class="notifyFile" type="file" accept="image/*" />
              <input id="notifyImageUrlInput" class="notifyInput" type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="Or paste image URL…" />
            </div>
            <div class="notifyField">
              <label for="notifyPositionSelect">Screen location</label>
              <select id="notifyPositionSelect" class="notifyInput">
                <option value="top-left" selected>Top left</option>
                <option value="top-right">Top right</option>
                <option value="top-center">Top center</option>
                <option value="bottom-left">Bottom left</option>
                <option value="bottom-right">Bottom right</option>
              </select>
            </div>
            <div class="notifyField">
              <label for="notifyDurationInput">Seconds</label>
              <input id="notifyDurationInput" class="notifyInput" type="number" inputmode="decimal" min="0.8" max="30" step="0.5" value="5" />
            </div>
          </div>
          <div class="notifyActions">
            <div id="notifyHelperTxt" class="helperTxt" aria-live="polite"></div>
            <button id="notifySendBtn" class="good" title="Send notification">Send</button>
          </div>
        </section>
      </div>
    </div>

    <!-- Hidden by default: history modal (opened via 🕘 button) -->
    <div id="histBackdrop" class="modalBackdrop hidden" role="dialog" aria-modal="true">
      <div class="modal histModal">
        <div class="modalTop">
          <div class="modalTitle">History</div>
          <div class="modalBtns">
            <button id="histClearBtn" class="danger" title="Clear history">Clear</button>
            <button id="histCloseBtn" class="iconBtn sm" title="Close" aria-label="Close">✕</button>
          </div>
        </div>
        <div id="histList" class="histList"></div>
      </div>
    </div>

    <div id="langBackdrop" class="modalBackdrop hidden" role="dialog" aria-modal="true">
      <div class="modal langModal">
        <div class="modalTop">
          <div class="modalTitle">Audio Language</div>
          <div class="modalBtns">
            <button id="langCloseBtn" class="iconBtn sm" title="Close" aria-label="Close">✕</button>
          </div>
        </div>
        <div id="langCurrent" class="hint">Loading audio tracks…</div>
        <div id="langList" class="langList"></div>
        <div id="langMsg" class="helperTxt"></div>
      </div>
    </div>

    <div id="subLangBackdrop" class="modalBackdrop hidden" role="dialog" aria-modal="true">
      <div class="modal langModal">
        <div class="modalTop">
          <div class="modalTitle">Subtitle Language</div>
          <div class="modalBtns">
            <button id="subLangCloseBtn" class="iconBtn sm" title="Close" aria-label="Close">✕</button>
          </div>
        </div>
        <div id="subLangCurrent" class="hint">Loading subtitle tracks…</div>
        <div id="subLangList" class="langList"></div>
        <div id="subLangMsg" class="helperTxt"></div>
      </div>
    </div>

    <div id="aboutBackdrop" class="modalBackdrop hidden" role="dialog" aria-modal="true">
      <div class="modal">
        <div class="modalTop">
          <div class="modalTitle">About RelayTV</div>
          <div class="modalBtns">
            <button id="aboutCloseBtn" class="iconBtn sm" title="Close" aria-label="Close">✕</button>
          </div>
        </div>
        <div class="settingsBody">
          <div class="hint">RelayTV is a local-first TV playback and automation endpoint.</div>
          <div class="aboutMeta" aria-live="polite">
            <div class="aboutMetaRow">
              <div class="aboutMetaKey">Version</div>
              <div id="aboutVersionValue" class="aboutMetaVal">Loading…</div>
            </div>
            <div class="aboutMetaRow">
              <div class="aboutMetaKey">Revision</div>
              <div id="aboutRevisionValue" class="aboutMetaVal">Loading…</div>
            </div>
            <div class="aboutMetaRow">
              <div class="aboutMetaKey">Updated</div>
              <div id="aboutUpdateValue" class="aboutMetaVal aboutUpdate">Checking…</div>
            </div>
          </div>
          <div class="aboutLinks">
            <a id="aboutGithubLink" class="aboutLink" href="https://github.com/mcgeezy/relaytv" target="_blank" rel="noopener noreferrer">
              <span>
                <strong>GitHub Repository</strong>
                <small>Source code, issues, releases, and documentation.</small>
              </span>
              <span aria-hidden="true">↗</span>
            </a>
            <a id="aboutChangelogLink" class="aboutLink" href="https://github.com/mcgeezy/relaytv/blob/main/CHANGELOG.md" target="_blank" rel="noopener noreferrer">
              <span>
                <strong>Changelog</strong>
                <small>Release notes generated from merged pull requests.</small>
              </span>
              <span aria-hidden="true">↗</span>
            </a>
            <a id="aboutReleaseLink" class="aboutLink" href="https://github.com/mcgeezy/relaytv/releases" target="_blank" rel="noopener noreferrer">
              <span>
                <strong>Latest Release</strong>
                <small id="aboutReleaseLinkSub">Tags, release notes, and container image history.</small>
              </span>
              <span aria-hidden="true">↗</span>
            </a>
            <a id="aboutSupportLink" class="aboutLink aboutSupportLink" href="https://buymeacoffee.com/relaytv" target="_blank" rel="noopener noreferrer" aria-label="Support RelayTV on Buy Me a Coffee">
              <img class="aboutSupportImg" src="https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20coffee&emoji=%E2%98%95&slug=relaytv&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff" alt="Buy Me a Coffee"/>
            </a>
          </div>
        </div>
      </div>
    </div>

    <div class="topgrid">
      <div class="nowCol">
      <section id="nowTopCard" class="card nowCard">
        <div class="nHead">
          <span class="nHeadTitle">Now Playing</span>
          <span id="nowStateDot" class="nStateDot" aria-hidden="true"></span>
          <span id="nowStateTag" class="nStateTag hidden">Paused</span>
          <button id="nowSkipBtn" class="nSkipBtn hidden" title="Stop current and play next up" aria-label="Play next up">Skip</button>
        </div>

        <div class="nHero">
          <div id="nHeroArt" class="nHeroArt" aria-hidden="true"></div>
          <div class="nHeroFade" aria-hidden="true"></div>
          <div class="nHeroBody">
            <div id="now" class="nTitle">Ready</div>
            <div class="nMetaRow">
              <span id="picon" class="providerIcon">🎞️</span>
              <div id="nowSub" class="nChan" style="min-width:0;"></div>
              <div class="nTrackBtns">
                <button id="nowLangBtn" class="nGhostBtn hidden" title="Audio language" aria-label="Audio language">Audio</button>
                <button id="nowSubLangBtn" class="nGhostBtn hidden" title="Subtitle language" aria-label="Subtitle language">Subs</button>
              </div>
            </div>
          </div>
        </div>

        <div class="nIdleMsg" aria-hidden="true">Nothing playing — share a link or pick from the queue</div>

        <div id="progress" class="progress" title="Drag to seek (or tap)">
          <div id="progFill" class="progressFill"></div>
        </div>
        <div class="nTimeRow"><span id="pos">--:--</span><span id="dur">--:--</span></div>
      </section>

      <section id="remoteCard" class="card remoteCard">
        <div class="rGrid">
          <button id="playPauseBtn" class="rTile rBig" onclick="post('/playback/toggle')" aria-label="Play or pause">
            <span class="rRing">
              <svg class="rGlyph rGlyphPlay" viewBox="0 0 24 24" aria-hidden="true"><path d="M8.6 5.9v12.2a.9.9 0 0 0 1.37.77l9.6-6.1a.9.9 0 0 0 0-1.52l-9.6-6.1a.9.9 0 0 0-1.37.75z" fill="currentColor"/></svg>
              <svg class="rGlyph rGlyphPause" viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="5" width="3.6" height="14" rx="1.3" fill="currentColor"/><rect x="13.4" y="5" width="3.6" height="14" rx="1.3" fill="currentColor"/></svg>
            </span>
            <span class="rLabel">Play/Pause</span>
          </button>
          <button class="rTile rBig" onclick="post('/next')" aria-label="Play next">
            <span class="rRing">
              <svg class="rGlyph" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6.6v10.8a.85.85 0 0 0 1.3.72l8.2-5.4a.85.85 0 0 0 0-1.44L7.3 5.88A.85.85 0 0 0 6 6.6z" fill="currentColor"/><rect x="16.6" y="5.6" width="2.6" height="12.8" rx="1.1" fill="currentColor"/></svg>
            </span>
            <span class="rLabel">Next</span>
          </button>
          <button id="muteBtn" class="rTile rWide rMute" onclick="post('/mute')" aria-label="Toggle mute">
            <span class="rRing">
              <svg class="rGlyph" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9.6v4.8h3.1l4.6 4V5.6l-4.6 4H4z" fill="currentColor"/><path d="M15.6 9.9l4.2 4.2m0-4.2l-4.2 4.2" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></svg>
            </span>
            <span class="rLabel">Mute</span>
          </button>
          <button id="closeBtn" class="rTile rWide rClose" onclick="post('/close')" aria-label="Close playback">
            <span class="rRing">
              <svg class="rGlyph" viewBox="0 0 24 24" aria-hidden="true"><path d="M7.2 7.2l9.6 9.6m0-9.6l-9.6 9.6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
            </span>
            <span class="rLabel">Close</span>
          </button>
          <button class="rTile rSeek" onclick="post('/seek',{sec:-10})" aria-label="Back 10 seconds">
            <span class="rRing rRingSeek">
              <svg class="rGlyph rGlyphSeek" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.8a8.2 8.2 0 1 1-7.5 4.9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M4.9 3.2v5h5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><text x="12" y="15.4" text-anchor="middle" font-size="8.2" font-weight="700" fill="currentColor">10</text></svg>
            </span>
            <span class="rLabel">−10s</span>
          </button>
          <button class="rTile rSeek" onclick="post('/seek',{sec:+30})" aria-label="Forward 30 seconds">
            <span class="rRing rRingSeek">
              <svg class="rGlyph rGlyphSeek" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.8a8.2 8.2 0 1 0 7.5 4.9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M19.1 3.2v5h-5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><text x="12" y="15.4" text-anchor="middle" font-size="8.2" font-weight="700" fill="currentColor">30</text></svg>
            </span>
            <span class="rLabel">+30s</span>
          </button>
        </div>
        <div class="remoteVolumeRow">
          <span class="rVolIcon" aria-hidden="true">
            <svg viewBox="0 0 24 24"><path d="M4 9.6v4.8h3.1l4.6 4V5.6l-4.6 4H4z" fill="currentColor"/><path d="M15.3 9.2a4.4 4.4 0 0 1 0 5.6m2.5-8a8 8 0 0 1 0 10.4" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>
          </span>
          <input id="remoteVolSlider" class="remoteVolumeSlider" type="range" min="0" max="200" step="1" value="100" aria-label="Volume" />
          <div id="remoteVolValue" class="remoteVolumeValue">--%</div>
        </div>
      </section>
    </div>

      <aside class="card queueCard">
        <div class="qHead">
          <span class="qHeadTitle">Queue</span>
          <span id="queueCount" class="qCount">0</span>
          <button id="queueClearBtn" class="qClearBtn hidden" title="Clear queue" onclick="post('/clear')">Clear</button>
        </div>
        <ol id="queue" class="queueList"></ol>
        <div class="footerRow">
          <span>Tip: Share again while playing to enqueue</span>
        </div>
      </aside>
    </div>

    <div id="jellyfinShell" class="jfShell jfModern hidden" aria-hidden="true">
      <div class="jfShellInner">
        <header class="jfShellHead">
          <button id="jfShellBackBtn" class="jfShellBack" aria-label="Back to RelayTV"><span aria-hidden="true">←</span><span>Back</span></button>
          <div class="jfShellIdentity">
            <span class="jfShellMark" aria-hidden="true">▶</span>
            <div>
              <div class="jfShellEyebrow">RelayTV library</div>
              <div class="jfShellTitle"><span class="jfBrand">Jellyfin</span></div>
            </div>
          </div>
          <div id="jfConnection" class="jfConnection" role="status" aria-live="polite">
            <span class="jfConnectionDot" aria-hidden="true"></span>
            <span id="jfConnectionLabel">Checking…</span>
          </div>
        </header>
        <div class="jfWorkspace">
          <nav class="jfTabs" role="tablist" aria-label="Jellyfin sections">
            <button class="jfTabBtn active" id="jfTabDashboardBtn" data-jf-tab="dashboard" role="tab" aria-selected="true" aria-controls="jellyfinCard" tabindex="0"><span class="jfTabIcon" aria-hidden="true">⌂</span><span>Home</span></button>
            <button class="jfTabBtn" id="jfTabMoviesBtn" data-jf-tab="movies" role="tab" aria-selected="false" aria-controls="jellyfinCard" tabindex="-1"><span class="jfTabIcon" aria-hidden="true">▰</span><span>Movies</span></button>
            <button class="jfTabBtn" id="jfTabTvBtn" data-jf-tab="tv" role="tab" aria-selected="false" aria-controls="jellyfinCard" tabindex="-1"><span class="jfTabIcon" aria-hidden="true">▣</span><span>TV</span></button>
          </nav>
          <section id="jellyfinCard" class="card jellyfinCard" role="tabpanel">
            <div class="jfCardHead">
              <span class="jfCardHeadLabel" id="jfCardHeadLabel">JELLYFIN</span>
              <div class="jfCardSearchWrap">
                <span class="jfSearchIcon" aria-hidden="true">⌕</span>
                <input id="jfSearchInput" class="input jfCardSearch" placeholder="Search Jellyfin titles…" aria-label="Search Jellyfin" />
              </div>
              <select id="jfSortSelect" class="jfSort hiddenCtl" aria-label="Sort catalog"></select>
            </div>
            <div class="jfTop">
              <span id="jfStatus" class="jfStatus" role="status" aria-live="polite">Loading…</span>
              <div class="jfHint">Arrows navigate • 1/2/3 switch tabs • Enter opens detail • P/N/L/R trigger Play/Next/Last/Resume</div>
              <div id="jfActionStatus" class="jfActionStatus" aria-live="polite"></div>
            </div>
            <div id="jfGrid" class="jfGrid">
              <div class="jfRowsPad">
                <div id="jfRows" class="jfRows"></div>
              </div>
              <div id="jfDetailBackdrop" class="jfDetailBackdrop" aria-hidden="true"></div>
              <aside id="jfDetail" class="jfDetail muted" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="jfDetailTitle">Select an item to view details.</aside>
              <div id="jfAlphaIndicator" class="jfAlphaIndicator" aria-hidden="true">A</div>
            </div>
          </section>
        </div>
      </div>
    </div>
  </div>

  <script>window.RELAYTV_IDLE_PANEL_CATALOG = __IDLE_PANEL_CATALOG__;</script>
  <script src="/static/ui/app.js?v=__UI_ASSET_V__" defer></script>
  <script src="/static/ui/jellyfin.js?v=__UI_ASSET_V__" defer></script>
</body>
</html>
<!-- Settings modal -->
<div id="settingsBackdrop" class="modalBackdrop hidden" role="dialog" aria-modal="true">
  <div class="modal">
    <div class="modalTop">
      <div class="modalTitle">Settings</div>
      <div class="modalBtns">
        <button id="settingsCloseBtn" class="iconBtn sm" title="Close" aria-label="Close">✕</button>
      </div>
    </div>

    <details class="settingsGroup">
      <summary>Playback</summary>
      <div class="settingsBody">
        <div class="fieldRow">
          <label class="fieldLbl">Device name</label>
          <input id="setDeviceName" class="input" placeholder="RelayTV Living Room" maxlength="80" />
          <div class="hint">Used for UI branding and Jellyfin client/session identity.</div>
        </div>

        <div class="fieldRow">
          <label class="fieldLbl">Audio device</label>
          <select id="setAudioDev" class="input"></select>
          <div class="hint">Auto picks the HDMI ALSA device that best matches the active connector.</div>
        </div>

        <div class="fieldRow">
          <label class="fieldLbl">Quality cap</label>
          <select id="setQuality" class="input">
            <option value="">Auto</option>
            <option value="1080">≤1080p</option>
            <option value="720">≤720p</option>
            <option value="480">≤480p</option>
            <option value="360">≤360p</option>
            <option value="worst">Worst</option>
          </select>
          <div class="hint">Capped modes prefer non-AV1 formats with safe fallback for better compatibility.</div>
        </div>

        <div class="fieldRow">
          <label class="fieldLbl">Subtitles</label>
          <select id="setSubs" class="input">
            <option value="">Off</option>
            <option value="en">English</option>
          </select>
        </div>
      </div>
    </details>

    <details class="settingsGroup">
      <summary>TV Control <span id="setCecStatus" class="sectionStatus unknown">Unknown</span></summary>
      <div class="settingsBody">
        <div id="setCecAvailabilityHint" class="hint">Checking HDMI-CEC adapter availability.</div>
        <div class="toggleRow">
          <div class="toggleCopy">
            <div class="toggleTitle">Enable HDMI-CEC</div>
            <div class="toggleHint">Allow RelayTV to send TV power, input, and source commands through the host CEC adapter.</div>
          </div>
          <label class="toggleSwitch" for="setCecEnabled" title="Enable HDMI-CEC">
            <input type="checkbox" id="setCecEnabled" />
            <span class="toggleTrack" aria-hidden="true"></span>
          </label>
        </div>
        <div class="toggleRow">
          <div class="toggleCopy">
            <div class="toggleTitle">Switch to this HDMI input on playback</div>
            <div class="toggleHint">When playback starts from a share or play request, announce RelayTV as the active source.</div>
          </div>
          <label class="toggleSwitch" for="setTvTakeoverEnabled" title="Switch to this HDMI input on playback">
            <input type="checkbox" id="setTvTakeoverEnabled" />
            <span class="toggleTrack" aria-hidden="true"></span>
          </label>
        </div>
        <div class="toggleRow">
          <div class="toggleCopy">
            <div class="toggleTitle">Pause when TV leaves this input</div>
            <div class="toggleHint">Pause playback if CEC reports a different active HDMI source.</div>
          </div>
          <label class="toggleSwitch" for="setTvPauseOnInputChange" title="Pause when TV leaves this input">
            <input type="checkbox" id="setTvPauseOnInputChange" />
            <span class="toggleTrack" aria-hidden="true"></span>
          </label>
        </div>
        <div class="toggleRow">
          <div class="toggleCopy">
            <div class="toggleTitle">Resume when TV returns</div>
            <div class="toggleHint">Resume playback when CEC reports this RelayTV input as active again.</div>
          </div>
          <label class="toggleSwitch" for="setTvAutoResumeOnReturn" title="Resume when TV returns">
            <input type="checkbox" id="setTvAutoResumeOnReturn" />
            <span class="toggleTrack" aria-hidden="true"></span>
          </label>
        </div>
      </div>
    </details>

    <details class="settingsGroup">
      <summary>YouTube</summary>
      <div class="settingsBody">
        <div class="toggleRow">
          <div class="toggleCopy">
            <div class="toggleTitle">Keep yt-dlp up to date</div>
            <div class="toggleHint">Check for yt-dlp updates daily (and right away when enabled). Recommended: stale yt-dlp is the most common cause of YouTube playback failures.</div>
          </div>
          <label class="toggleSwitch" for="setYtdlpAutoUpdate" title="Keep yt-dlp up to date">
            <input type="checkbox" id="setYtdlpAutoUpdate" />
            <span class="toggleTrack" aria-hidden="true"></span>
          </label>
        </div>
        <div class="toggleRow">
          <div class="toggleCopy">
            <div class="toggleTitle">Use Invidious server for YouTube playback</div>
            <div class="toggleHint">Resolve YouTube playback through the configured Invidious base URL.</div>
          </div>
          <label class="toggleSwitch" for="setYtUseInvidious" title="Use Invidious server for YouTube playback">
            <input type="checkbox" id="setYtUseInvidious" />
            <span class="toggleTrack" aria-hidden="true"></span>
          </label>
        </div>
        <div class="fieldRow">
          <label class="fieldLbl">Invidious server</label>
          <input id="setYtInvidiousBase" class="input" placeholder="https://invidious.example.org" />
          <div class="hint">Used when Invidious mode is enabled. Enter base URL only.</div>
        </div>
        <div class="fieldRow">
          <label class="fieldLbl">Direct-play cookies.txt</label>
          <input id="setYtCookiesFile" class="input" type="file" accept=".txt,text/plain" />
          <div class="hint">Upload Netscape-format cookies.txt for yt-dlp (node/deno challenge flow).</div>
        </div>
        <div class="inlineApplyRow">
          <button type="button" id="setYtCookiesUploadBtn" class="btn electricBlue">Upload cookies.txt</button>
          <button type="button" id="setYtCookiesClearBtn" class="btn electricBlue">Clear cookies.txt</button>
          <div id="setYtCookiesState" class="inlineApplyMsg"></div>
        </div>
      </div>
    </details>

    <details class="settingsGroup">
      <summary>Idle Dashboard</summary>
      <div class="settingsBody">
        <div class="toggleRow">
          <div class="toggleCopy">
            <div class="toggleTitle">Show idle dashboard between plays</div>
            <div class="toggleHint">Turn off to return to the desktop while RelayTV stays ready for the next play.</div>
          </div>
          <label class="toggleSwitch" for="setIdleDashboardEnabled" title="Show idle dashboard between plays">
            <input type="checkbox" id="setIdleDashboardEnabled" />
            <span class="toggleTrack" aria-hidden="true"></span>
          </label>
        </div>
        <div class="toggleRow">
          <div class="toggleCopy">
            <div class="toggleTitle">Show toast notifications while idle</div>
            <div class="toggleHint">Keep a lightweight notification surface available when nothing is playing.</div>
          </div>
          <label class="toggleSwitch" for="setIdleNotificationsEnabled" title="Show toast notifications while idle">
            <input type="checkbox" id="setIdleNotificationsEnabled" />
            <span class="toggleTrack" aria-hidden="true"></span>
          </label>
        </div>
        <details class="settingsGroup">
          <summary>Show QR in Idle</summary>
          <div class="settingsBody">
            <div class="toggleRow">
              <div class="toggleCopy">
                <div class="toggleTitle">Show connect QR in idle</div>
                <div class="toggleHint">Display a scannable code for the current RelayTV remote URL, with logo center.</div>
              </div>
              <label class="toggleSwitch" for="setIdleQrEnabled" title="Show connect QR in idle">
                <input type="checkbox" id="setIdleQrEnabled" />
                <span class="toggleTrack" aria-hidden="true"></span>
              </label>
            </div>
            <div class="fieldRow">
              <label class="fieldLbl" for="setIdleQrSize">QR size <span id="setIdleQrSizeVal">168px</span></label>
              <input id="setIdleQrSize" class="input" type="range" min="96" max="280" step="4" value="168" />
              <div class="hint">Adjusts idle QR size for screen distance and room layout.</div>
            </div>
          </div>
        </details>
        <details class="settingsGroup">
          <summary>Weather</summary>
          <div class="settingsBody">
            <div class="hint">Powered by Open-Meteo. Pick a city so the idle card stays local.</div>
            <div class="hint">Enable/disable weather on idle and choose card layout.</div>
            <div id="setIdlePanels"></div>
            <div class="fieldRow">
              <label class="fieldLbl">Forecast range</label>
              <select id="setWeatherDays" class="input">
                <option value="1">1 day</option>
                <option value="3">3 days</option>
                <option value="7">7 days</option>
              </select>
            </div>
            <div class="fieldRow">
              <div class="weatherLocStack">
                <div class="hint">Use zip code.</div>
                <div class="weatherLocRow">
                  <input id="setWeatherCity" class="input" placeholder="e.g. Seattle, WA" autocomplete="off" />
                  <button type="button" id="setWeatherFindBtn" class="btn">Find city</button>
                </div>
                <div id="setWeatherLocationMeta" class="weatherLocMeta"></div>
              </div>
            </div>
          </div>
        </details>
      </div>
    </details>

    <details class="settingsGroup">
      <summary>Uploads</summary>
      <div class="settingsBody">
        <div class="hint">Uploaded videos are cleaned up when either limit is reached first.</div>
        <div class="fieldRow">
          <label class="fieldLbl">Storage max size (GB)</label>
          <input id="setUploadMaxSize" class="input" type="number" min="0.25" max="500" step="0.25" value="5" />
        </div>
        <div class="fieldRow">
          <label class="fieldLbl">Retention max hours</label>
          <input id="setUploadRetentionHours" class="input" type="number" min="1" max="2160" step="1" value="24" />
        </div>
      </div>
    </details>

    <details class="settingsGroup">
      <summary><span class="jfBrand">Jellyfin / Emby</span> Integration <span id="setJfStatus" class="sectionStatus unknown">Disabled</span></summary>
      <div class="settingsBody">
        <div class="toggleRow">
          <div class="toggleCopy">
            <div class="toggleTitle">Enable <span class="jfBrand">Jellyfin / Emby</span> integration</div>
            <div class="toggleHint">Show <span class="jfBrand">Jellyfin / Emby</span> browsing and playback controls when server settings are configured.</div>
          </div>
          <label class="toggleSwitch" for="setJfEnabled" title="Enable media server integration">
            <input type="checkbox" id="setJfEnabled" />
            <span class="toggleTrack" aria-hidden="true"></span>
          </label>
        </div>

        <div class="fieldRow">
          <label class="fieldLbl"><span class="jfBrand">Jellyfin / Emby</span> server</label>
          <input id="setJfServerUrl" class="input" placeholder="http://10.0.55.2:8096" />
          <div class="hint">Use your local Jellyfin or Emby base URL, for example `http://10.0.55.2:8096`. The server type is detected automatically.</div>
        </div>

        <div class="fieldRow">
          <label class="fieldLbl">Username</label>
          <input id="setJfUsername" class="input" placeholder="server username" />
        </div>
        <div class="fieldRow">
          <label class="fieldLbl">Preferred user ID (optional)</label>
          <input id="setJfUserId" class="input" placeholder="Server user Id (UUID)" />
          <div class="hint">Optional profile override for catalog browsing on this TV. Leave blank to use the authenticated user.</div>
        </div>
        <div class="fieldRow">
          <label class="fieldLbl">Password</label>
          <input id="setJfPassword" class="input" type="password" autocomplete="new-password" placeholder="(leave blank to keep existing)" />
          <div class="toggleRow">
            <div class="toggleCopy">
              <div class="toggleTitle">Clear stored password</div>
              <div class="toggleHint">Remove the saved server password on the next apply.</div>
            </div>
            <label class="toggleSwitch" for="setJfClearPassword" title="Clear stored password">
              <input type="checkbox" id="setJfClearPassword" />
              <span class="toggleTrack" aria-hidden="true"></span>
            </label>
          </div>
          <div class="hint" id="setJfPasswordState"></div>
        </div>
        <div class="fieldRow">
          <label class="fieldLbl">Preferred audio language</label>
          <input id="setJfAudioLang" class="input" placeholder="e.g. en, pt-BR" />
          <div class="hint">Used when selecting server audio tracks if available.</div>
        </div>
        <div class="fieldRow">
          <label class="fieldLbl">Preferred subtitle language</label>
          <input id="setJfSubLang" class="input" placeholder="e.g. en, pt-BR, or off" />
          <div class="hint">Set `off` to prefer no subtitles by default.</div>
        </div>
        <div class="fieldRow">
          <label class="fieldLbl">Playback mode</label>
          <select id="setJfPlaybackMode" class="input">
            <option value="auto">Auto (direct unless compatibility risk)</option>
            <option value="direct">Direct play preferred</option>
            <option value="transcode">Always transcode to compatibility stream</option>
          </select>
          <div class="hint">Auto uses host decode profile and display cap to choose direct or transcode.</div>
        </div>
        <div class="inlineApplyRow">
          <button type="button" id="setJfApplyBtn" class="btn electricBlue">Apply <span class="jfBrand">Jellyfin / Emby</span></button>
          <div id="setJfApplyResult" class="inlineApplyMsg"></div>
        </div>
        <div class="inlineApplyRow">
          <button type="button" id="setJfCacheClearBtn" class="btn electricBlue">Clear Catalog Cache</button>
          <div id="setJfCacheClearResult" class="inlineApplyMsg"></div>
        </div>
        <div id="setJfSyncDiag" class="hint"></div>
      </div>
    </details>

    <div class="modalBottom">
      <button id="settingsSaveBtn" class="btn primary electricBlue">Apply</button>
    </div>
  </div>
</div>


"""
    html = html.replace("__IDLE_PANEL_CATALOG__", _json.dumps(_idle_panel_catalog(), separators=(",", ":"), ensure_ascii=False))
    html = html.replace("__UI_ASSET_V__", _ui_asset_version())
    # The shell must never be cached: it carries the asset version stamp that
    # busts the hour-long static UI asset cache after a deploy.
    return HTMLResponse(content=html, headers={"Cache-Control": "no-cache"})


def _ui_asset_version() -> str:
    stamp = 0
    for name in ("app.css", "jellyfin.css", "app.js", "jellyfin.js"):
        path = _resolve_static_asset("ui", name)
        try:
            if path:
                stamp = max(stamp, int(os.path.getmtime(path)))
        except OSError:
            pass
    return str(stamp or int(time.time()))
