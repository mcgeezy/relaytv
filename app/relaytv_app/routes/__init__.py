# SPDX-License-Identifier: GPL-3.0-only
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
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
from ..integrations import iptv_service, jellyfin_receiver, jellyfin_service
from ..realtime import (
    HEARTBEAT_SEC,
    PROTOCOL_VERSION,
    SUBPROTOCOL,
    OVERLAY_CHANNEL,
    UI_CHANNEL,
    RealtimeEvent,
    RealtimeSubscriptionClosed,
    realtime_hub,
    websocket_origin_allowed,
)
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
from .iptv import router as iptv_router
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
from .peers import (
    PeerCreateReq as PeerCreateReq,
    PeerPatchReq as PeerPatchReq,
    PeerSendReq as PeerSendReq,
    peers_add as peers_add,
    peers_identity as peers_identity,
    peers_list as peers_list,
    peers_probe as peers_probe,
    peers_probe_saved as peers_probe_saved,
    peers_remove as peers_remove,
    peers_send as peers_send,
    peers_update as peers_update,
    router as peers_router,
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
    _play_now_item as _play_now_item,
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
    ShareReq as ShareReq,
    share as share,
    share_target as share_target,
    smart as smart,
    stop as stop,
    toggle_pause as toggle_pause,
    volume as volume,
)
from .playback import router as playback_router
from .plex import router as plex_router
from .postlive import router as postlive_router
from .queue import router as queue_router
from .realtime import router as realtime_router
from .settings import (
    SettingsReq as SettingsReq,
    YouTubeCookiesUploadReq as YouTubeCookiesUploadReq,
    clear_youtube_cookies as clear_youtube_cookies,
    get_settings as get_settings,
    router as settings_router,
    update_settings as update_settings,
    upload_youtube_cookies as upload_youtube_cookies,
)
from .seerr import router as seerr_router
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
router.include_router(iptv_router)
router.include_router(jellyfin_router)
router.include_router(peers_router)
router.include_router(playback_router)
router.include_router(plex_router)
router.include_router(postlive_router)
router.include_router(queue_router)
router.include_router(realtime_router)
router.include_router(settings_router)
router.include_router(seerr_router)
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
    subscribers = realtime_hub.subscriber_count(OVERLAY_CHANNEL)
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
        # Why the last play produced nothing, when it produced nothing. mpv is
        # launched with --no-terminal, so this is the only place its reason
        # surfaces without turning on debug and reproducing the failure.
        "last_playback_error": (getattr(player, "last_playback_error", lambda: None)() or None),
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
        "native_qt_mpv_runtime_path": public_media.sanitize_public_url(
            qt_runtime_telemetry.get("mpv_runtime_path")
        ),
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
# Realtime event publication and SSE compatibility adapters
# =========================

def _x11_overlay_push(event: dict) -> None:
    """Push a toast/overlay event to any connected X11 overlay clients."""
    if realtime_hub.subscriber_count(OVERLAY_CHANNEL) <= 0:
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
    realtime_hub.publish(OVERLAY_CHANNEL, str(event.get("type") or "toast"), event)

async def _x11_overlay_sse() -> object:
    """Server-Sent Events stream for X11 overlay."""
    subscription = realtime_hub.subscribe(OVERLAY_CHANNEL, transport="sse", maxsize=50)
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
    async def gen():
        try:
            hello = _json.dumps({"type": "hello", "ts": time.time()})
            yield f"data: {hello}\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(subscription.get(), timeout=15.0)
                    payload = _json.dumps(message.data, separators=(",", ":"), ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {_json.dumps({'type': 'ping', 'ts': time.time()}, separators=(',', ':'))}\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            subscription.close()
            try:
                if (
                    hasattr(state, "update_overlay_delivery_state")
                    and realtime_hub.subscriber_count(OVERLAY_CHANNEL) <= 0
                ):
                    state.update_overlay_delivery_state(
                        "disconnected",
                        "subscriber_gone",
                        client_event="subscriber",
                        client_reason="subscriber_gone",
                    )
            except Exception:
                pass

    return StreamingResponse(gen(), media_type="text/event-stream")


async def _x11_overlay_websocket_session(websocket: WebSocket) -> None:
    if SUBPROTOCOL not in _websocket_requested_subprotocols(websocket):
        await websocket.close(code=1002, reason="unsupported realtime protocol")
        return
    if not websocket_origin_allowed(
        origin=websocket.headers.get("origin"),
        host=websocket.headers.get("host"),
        websocket_scheme=str(websocket.scope.get("scheme") or "ws"),
    ):
        await websocket.close(code=1008, reason="origin not allowed")
        return

    await websocket.accept(subprotocol=SUBPROTOCOL)
    subscription = realtime_hub.subscribe(
        OVERLAY_CHANNEL,
        transport="websocket",
        maxsize=50,
    )
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

    receive_task: asyncio.Task | None = None
    event_task: asyncio.Task | None = None
    try:
        hello = RealtimeEvent.create(
            "hello",
            0,
            {
                "protocol_version": PROTOCOL_VERSION,
                "heartbeat_sec": HEARTBEAT_SEC,
                "replay": False,
            },
        )
        await websocket.send_json(hello.envelope())
        receive_task = asyncio.create_task(websocket.receive())
        event_task = asyncio.create_task(subscription.get())
        while True:
            done, _pending = await asyncio.wait(
                {receive_task, event_task},
                timeout=float(HEARTBEAT_SEC),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive_task in done:
                incoming = receive_task.result()
                if incoming.get("type") == "websocket.disconnect":
                    break
                await websocket.close(code=1008, reason="read-only realtime channel")
                break
            if event_task in done:
                event = event_task.result()
                await websocket.send_json(event.envelope())
                while True:
                    try:
                        event = subscription.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    await websocket.send_json(event.envelope())
                event_task = asyncio.create_task(subscription.get())
                continue
            ping = RealtimeEvent.create(
                "ping",
                realtime_hub.current_sequence(OVERLAY_CHANNEL),
                {"type": "ping", "ts": time.time()},
            )
            await websocket.send_json(ping.envelope())
    except (WebSocketDisconnect, RealtimeSubscriptionClosed):
        pass
    finally:
        subscription.close()
        for task in (receive_task, event_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (receive_task, event_task) if task is not None),
            return_exceptions=True,
        )
        try:
            if (
                hasattr(state, "update_overlay_delivery_state")
                and realtime_hub.subscriber_count(OVERLAY_CHANNEL) <= 0
            ):
                state.update_overlay_delivery_state(
                    "disconnected",
                    "subscriber_gone",
                    client_event="subscriber",
                    client_reason="subscriber_gone",
                )
        except Exception:
            pass


def _ui_event_push(event_name: str, event: dict) -> None:
    """Push a lightweight UI event to any connected /ui SSE clients."""
    realtime_hub.publish(UI_CHANNEL, event_name, event)


def _ui_event_push_queue(action: str, queue: list[object] | None = None, queue_length: int | None = None, source: str = "api") -> None:
    if queue is None:
        with state.QUEUE_LOCK:
            state.ensure_queue_item_ids(state.QUEUE)
            queue = list(state.QUEUE)
    # Supplied snapshots already carry ids assigned at insertion under the
    # queue lock. Publication must never mutate their shared dictionaries.
    queue = _annotate_queue_items(queue)
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


def _annotate_queue_items(items: list[object] | None) -> list[object]:
    annotated: list[object] = []
    for item in list(items or []):
        public = _annotate_upload_item(item)
        queue_id = state.queue_item_id(item)
        if queue_id and isinstance(public, dict):
            public["queue_id"] = queue_id
        annotated.append(public)
    return annotated


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


def _ui_document(name: str) -> str:
    path = _resolve_static_asset("ui", name)
    if not path:
        raise RuntimeError(f"missing packaged UI document: {name}")
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _idle_html() -> str:
    return _ui_document("idle.html")


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
_jellyfin_catalog_token = jellyfin_service.catalog_token
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


def _jellyfin_integration_command_impl(req: JellyfinCommandReq, *, guard=None):
    """Normalized Jellyfin command ingress; product logic in jellyfin_service.

    This wrapper owns the route-side seams: playback control dispatch and
    UI eventing. The lambdas resolve module globals at call time so test
    monkeypatches on this module keep intercepting.
    """
    return jellyfin_service.handle_command(
        req,
        guard=guard,
        controls={
            "stop": lambda: stop(),
            "pause": lambda: pause(),
            "resume": lambda: resume(),
            "seek": lambda sec: seek_abs(SeekAbsReq(sec=float(sec))),
            "seek_relative": lambda delta: _seek_relative_result(float(delta)),
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
            if realtime_hub.subscriber_count(OVERLAY_CHANNEL) > 0:
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
    html = _ui_document("x11-overlay.html")
    html = html.replace("__PLAYBACK_NOTIFY_FADE_MS__", str(_playback_notification_fade_ms()))
    html = html.replace("__PLAYBACK_NOTIFY_DISPLAY_SEC__", str(_playback_notification_display_sec()))
    html = html.replace("__OVERLAY_DEBUG_BG__", _overlay_debug_bg_css())
    html = html.replace("__OVERLAY_ALLOW_IMAGES__", "true" if _overlay_allow_images() else "false")
    html = html.replace("__IDLE_CACHE_BUSTER__", str(int(time.time() * 1000)))
    html = html.replace("__UI_ASSET_V__", _ui_asset_version())
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
        "now_playing": _annotate_upload_item(state.NOW_PLAYING),
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
    plex_result = playback_service.seek_plex_conversion(delta_sec=delta_sec)
    if isinstance(plex_result, dict):
        return plex_result
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
    plex_result = playback_service.seek_plex_conversion(target_sec=target_sec)
    if isinstance(plex_result, dict):
        return plex_result
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
        state.ensure_queue_item_ids(state.QUEUE)
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
    # Treat the status assembler as the final trust boundary too. Runtime
    # adapters need the signed playback URL internally, but no adapter or test
    # double should be able to publish its credential-bearing form.
    runtime["native_qt_mpv_runtime_path"] = public_media.sanitize_public_url(
        runtime.get("native_qt_mpv_runtime_path")
    )
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
        elif (
            sess == "playing"
            and isinstance(state.NOW_PLAYING, dict)
            and (
                str(state.NOW_PLAYING.get("provider") or "").strip().lower() == "iptv"
                or player._item_looks_like_live_stream(state.NOW_PLAYING)
            )
        ):
            # Runtime telemetry can briefly disappear while a live stream
            # buffers. Keep only live/IPTV sessions open; ordinary VOD falls
            # through so a crashed player still demotes to idle instead of
            # sticking in a stale playing/buffering session.
            pass
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
    jf_control_auth_source = str(jf_status.get("control_auth_source") or "none")
    jf_cast_target_scope = str(jf_status.get("cast_target_scope") or "unavailable")
    jf_cast_target_ready = bool(jf_status.get("cast_target_ready"))
    jf_catalog_auth_source = str(jf_status.get("catalog_auth_source") or "none")
    jf_catalog_ready = bool(jf_status.get("catalog_ready"))
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
    jf_catalog_user_id_rejected = str(jf_status.get("catalog_user_id_rejected") or "")
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
    annotated_queue = _annotate_queue_items(q)
    iptv_status: dict[str, object] = {"enabled": iptv_service.enabled()}
    if iptv_status["enabled"]:
        try:
            iptv_status = iptv_service.status()
        except Exception:
            iptv_status.update({"source_count": 0, "channel_count": 0})
    # Durable-write health. A device whose disk stopped accepting writes used
    # to look completely healthy while quietly discarding every setting and
    # queue change; the key is absent while writes are landing, so the payload
    # only grows when something is actually wrong.
    persistence = state.persistence_health() if hasattr(state, "persistence_health") else {"ok": True}

    payload: dict[str, object] = {
        "state": sess,
        "device_name": str(settings_snapshot.get("device_name") or "RelayTV"),
        "idle_dashboard_enabled": bool(settings_snapshot.get("idle_dashboard_enabled", True)),
        "idle_notifications_enabled": bool(settings_snapshot.get("idle_notifications_enabled", True)),
        "mdns_advertising": bool(mdns.get("active")),
        "mdns_service_type": str(mdns.get("service_type") or ""),
        "iptv_enabled": bool(iptv_status.get("enabled")),
        "iptv_source_count": int(iptv_status.get("source_count") or 0),
        "iptv_channel_count": int(iptv_status.get("channel_count") or 0),
        "jellyfin_enabled": jf_enabled,
        "jellyfin_running": jf_running,
        "jellyfin_connected": jf_connected,
        "jellyfin_authenticated": jf_authenticated,
        "jellyfin_control_auth_source": jf_control_auth_source,
        "jellyfin_cast_target_scope": jf_cast_target_scope,
        "jellyfin_cast_target_ready": jf_cast_target_ready,
        "jellyfin_catalog_auth_source": jf_catalog_auth_source,
        "jellyfin_catalog_ready": jf_catalog_ready,
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
        "jellyfin_catalog_user_id_rejected": jf_catalog_user_id_rejected,
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
    if not persistence.get("ok", True):
        payload["persistence"] = persistence
    return payload


@router.get("/status")
def status():
    return _status_payload()


async def _ui_events_sse(request: Request) -> object:
    subscription = realtime_hub.subscribe(
        UI_CHANNEL,
        transport="sse",
        maxsize=100,
        replay_latest=True,
    )

    async def gen():
        last_emit_ts = 0.0

        try:
            hello = _json.dumps({"type": "hello", "ts": time.time()}, separators=(",", ":"), ensure_ascii=False)
            yield f"event: hello\ndata: {hello}\n\n"
            last_emit_ts = time.time()

            while True:
                if await request.is_disconnected():
                    break

                try:
                    message = await asyncio.wait_for(subscription.get(), timeout=5.0)
                    payload = _json.dumps(message.data, separators=(",", ":"), ensure_ascii=False)
                    yield f"event: {message.event}\ndata: {payload}\n\n"
                    last_emit_ts = time.time()
                    while True:
                        try:
                            message = subscription.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        payload = _json.dumps(message.data, separators=(",", ":"), ensure_ascii=False)
                        yield f"event: {message.event}\ndata: {payload}\n\n"
                        last_emit_ts = time.time()
                except asyncio.TimeoutError:
                    pass

                # Idle ping cadence must stay well inside the client's health
                # window (app.js _uiEventHealthy) or a quiet stream reads as dead.
                if (time.time() - last_emit_ts) >= 5.0:
                    ping = _json.dumps({"type": "ping", "ts": time.time()}, separators=(",", ":"), ensure_ascii=False)
                    yield f"event: ping\ndata: {ping}\n\n"
                    last_emit_ts = time.time()
        finally:
            subscription.close()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_UI_SAMPLER_TASK: asyncio.Task | None = None
_UI_SAMPLER_STOP: asyncio.Event | None = None


async def _ui_snapshot_sampler(stop_event: asyncio.Event) -> None:
    last_fast_json = ""
    last_status_json = ""
    last_has_now_playing = None
    last_queue_length = None
    last_full_ts = 0.0

    async def _wait(delay: float) -> None:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    while not stop_event.is_set():
        if realtime_hub.subscriber_count(UI_CHANNEL) <= 0:
            last_fast_json = ""
            last_status_json = ""
            last_has_now_playing = None
            last_queue_length = None
            last_full_ts = 0.0
            await _wait(0.25)
            continue

        try:
            now_ts = time.monotonic()
            fast = await asyncio.to_thread(_playback_state_fast_snapshot)
            fast_json = _json.dumps(
                fast,
                separators=(",", ":"),
                ensure_ascii=False,
                sort_keys=True,
            )
            queue_length = int(fast.get("queue_length") or 0)
            has_now_playing = bool(fast.get("has_now_playing"))
            force_full = (
                last_queue_length is None
                or queue_length != last_queue_length
                or has_now_playing != last_has_now_playing
                or (now_ts - last_full_ts) >= 5.0
            )

            if fast_json != last_fast_json:
                last_fast_json = fast_json
                realtime_hub.publish(UI_CHANNEL, "playback", fast)

            if force_full:
                full = await asyncio.to_thread(_status_payload)
                full_json = _json.dumps(
                    full,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if full_json != last_status_json:
                    last_status_json = full_json
                    realtime_hub.publish(UI_CHANNEL, "status", full)
                last_full_ts = time.monotonic()
                last_queue_length = queue_length
                last_has_now_playing = has_now_playing
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("ui_snapshot_sampler_failed", exc_info=True)
        await _wait(0.75)


async def start_realtime_runtime() -> None:
    global _UI_SAMPLER_STOP, _UI_SAMPLER_TASK
    if _UI_SAMPLER_TASK is not None and not _UI_SAMPLER_TASK.done():
        return
    _UI_SAMPLER_STOP = asyncio.Event()
    _UI_SAMPLER_TASK = asyncio.create_task(
        _ui_snapshot_sampler(_UI_SAMPLER_STOP),
        name="relaytv-ui-snapshot-sampler",
    )


async def stop_realtime_runtime() -> None:
    global _UI_SAMPLER_STOP, _UI_SAMPLER_TASK
    task = _UI_SAMPLER_TASK
    stop_event = _UI_SAMPLER_STOP
    _UI_SAMPLER_TASK = None
    _UI_SAMPLER_STOP = None
    if stop_event is not None:
        stop_event.set()
    if task is not None:
        await asyncio.gather(task, return_exceptions=True)


def _websocket_requested_subprotocols(websocket: WebSocket) -> set[str]:
    raw = str(websocket.headers.get("sec-websocket-protocol") or "")
    return {item.strip() for item in raw.split(",") if item.strip()}


async def _ui_websocket_session(websocket: WebSocket) -> None:
    if SUBPROTOCOL not in _websocket_requested_subprotocols(websocket):
        await websocket.close(code=1002, reason="unsupported realtime protocol")
        return
    if not websocket_origin_allowed(
        origin=websocket.headers.get("origin"),
        host=websocket.headers.get("host"),
        websocket_scheme=str(websocket.scope.get("scheme") or "ws"),
    ):
        await websocket.close(code=1008, reason="origin not allowed")
        return

    await websocket.accept(subprotocol=SUBPROTOCOL)
    subscription = realtime_hub.subscribe(
        UI_CHANNEL,
        transport="websocket",
        maxsize=100,
        replay_latest=True,
    )
    receive_task: asyncio.Task | None = None
    event_task: asyncio.Task | None = None
    try:
        hello = RealtimeEvent.create(
            "hello",
            0,
            {
                "protocol_version": PROTOCOL_VERSION,
                "heartbeat_sec": HEARTBEAT_SEC,
                "replay": False,
            },
        )
        await websocket.send_json(hello.envelope())
        receive_task = asyncio.create_task(websocket.receive())
        event_task = asyncio.create_task(subscription.get())

        while True:
            done, _pending = await asyncio.wait(
                {receive_task, event_task},
                timeout=float(HEARTBEAT_SEC),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive_task in done:
                incoming = receive_task.result()
                if incoming.get("type") == "websocket.disconnect":
                    break
                await websocket.close(code=1008, reason="read-only realtime channel")
                break
            if event_task in done:
                event = event_task.result()
                await websocket.send_json(event.envelope())
                while True:
                    try:
                        event = subscription.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    await websocket.send_json(event.envelope())
                event_task = asyncio.create_task(subscription.get())
                continue

            ping = RealtimeEvent.create(
                "ping",
                realtime_hub.current_sequence(UI_CHANNEL),
                {"type": "ping", "ts": time.time()},
            )
            await websocket.send_json(ping.envelope())
    except (WebSocketDisconnect, RealtimeSubscriptionClosed):
        pass
    finally:
        subscription.close()
        for task in (receive_task, event_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (receive_task, event_task) if task is not None),
            return_exceptions=True,
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
    html = _ui_document("index.html")
    html = html.replace("__IDLE_PANEL_CATALOG__", _json.dumps(_idle_panel_catalog(), separators=(",", ":"), ensure_ascii=False))
    html = html.replace("__UI_ASSET_V__", _ui_asset_version())
    # The shell must never be cached: it carries the asset version stamp that
    # busts the hour-long static UI asset cache after a deploy.
    return HTMLResponse(content=html, headers={"Cache-Control": "no-cache"})


def _ui_asset_version() -> str:
    stamp = 0
    for name in (
        "index.html",
        "idle.html",
        "x11-overlay.html",
        "api.js",
        "foundation.css",
        "app.css",
        "jellyfin.css",
        "plex.css",
        "iptv.css",
        "seerr.css",
        "peers.css",
        "realtime_transport.js",
        "navigation.js",
        "overlays.js",
        "remote.js",
        "settings.js",
        "store.js",
        "theme.js",
        "app.js",
        "jellyfin.js",
        "plex.js",
        "iptv.js",
        "seerr.js",
        "peers.js",
    ):
        path = _resolve_static_asset("ui", name)
        try:
            if path:
                stamp = max(stamp, int(os.path.getmtime(path)))
        except OSError:
            pass
    return str(stamp or int(time.time()))
