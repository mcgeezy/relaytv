# SPDX-License-Identifier: GPL-3.0-only
from fastapi import APIRouter, HTTPException, Request, File, Form, UploadFile
from fastapi.responses import (
    StreamingResponse,
    HTMLResponse,
    RedirectResponse,
    JSONResponse,
    FileResponse,
    Response,
)
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

import time
import datetime
import asyncio
import json as _json
import math
import os
import re
import threading
import uuid
import socket
import mimetypes
import tempfile
import urllib.request
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import state, resolver, player, devices, discovery_mdns, video_profile, upload_store
from .integrations import jellyfin_receiver
from .debug import debug_log, get_logger
from .thumb_cache import THUMB_DIR, ensure_cached_sync, attach_local_thumbnail, thumb_id, local_rel_path

router = APIRouter()
logger = get_logger("routes")
_JELLYFIN_PLAY_DEBOUNCE_LOCK = threading.Lock()
_JELLYFIN_LAST_PLAY: dict[str, object] = {"ts": 0.0, "url": "", "item_id": "", "start_pos": None}
_JELLYFIN_COMMAND_DEDUPE_LOCK = threading.Lock()
_JELLYFIN_RECENT_COMMAND_IDS: dict[str, float] = {}
_JELLYFIN_UI_ACTION_DEDUPE_LOCK = threading.Lock()
_JELLYFIN_LAST_UI_ACTION: dict[str, object] = {"ts": 0.0, "command": "", "item_id": "", "resume_pos": None}


def _env_choice(name: str) -> bool | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in ("1", "true", "yes", "on", "enable", "enabled"):
        return True
    if text in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    return None


def _static_root_candidates() -> list[str]:
    roots: list[str] = []
    env_root = (os.getenv("RELAYTV_STATIC_DIR") or "").strip()
    if env_root:
        roots.append(env_root)
    roots.extend(
        [
            os.path.abspath(os.path.join(os.path.dirname(__file__), "static")),
            "/app/relaytv_app/static",
            os.path.join(os.getcwd(), "static"),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static")),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "static")),
            "/app/static",
            "/opt/dev/relaytv/static",
            "/workspace/relaytv/static",
        ]
    )
    out: list[str] = []
    seen: set[str] = set()
    for root in roots:
        norm = os.path.abspath(root)
        if norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def _resolve_static_asset(*parts: str) -> str | None:
    for root in _static_root_candidates():
        path = os.path.join(root, *parts)
        if os.path.exists(path):
            return path
    return None


def _fallback_svg(label: str = "not available") -> str:
    safe = (label or "not available").replace("<", "").replace(">", "")
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<svg xmlns='http://www.w3.org/2000/svg' width='128' height='128' viewBox='0 0 128 128' role='img' "
        f"aria-label='{safe}'>"
        "<rect width='128' height='128' rx='18' fill='#0f1c35'/>"
        "<path d='M24 84h80' stroke='#5a88c8' stroke-width='8' stroke-linecap='round'/>"
        "<circle cx='49' cy='52' r='10' fill='#7fb3ff'/><circle cx='79' cy='52' r='10' fill='#7fb3ff'/>"
        "</svg>"
    )


def _resolve_brand_svg_path(
    name: str,
    *,
    explicit_env: str | None = None,
    fallback_name: str = "logo.svg",
) -> str | None:
    env_name = str(explicit_env or "").strip()
    if env_name:
        explicit = (os.getenv(env_name) or "").strip()
        if explicit and os.path.exists(explicit):
            return explicit
    path = _resolve_static_asset("brand", name)
    if path and os.path.exists(path):
        return path
    fallback = _resolve_static_asset("brand", fallback_name)
    if fallback and os.path.exists(fallback):
        return fallback
    return None


def _resolve_brand_asset_path(
    name: str,
    *,
    explicit_env: str | None = None,
    fallback_names: tuple[str, ...] = (),
) -> str | None:
    env_name = str(explicit_env or "").strip()
    if env_name:
        explicit = (os.getenv(env_name) or "").strip()
        if explicit and os.path.exists(explicit):
            return explicit
    path = _resolve_static_asset("brand", name)
    if path and os.path.exists(path):
        return path
    for fallback_name in fallback_names:
        fallback = _resolve_static_asset("brand", fallback_name)
        if fallback and os.path.exists(fallback):
            return fallback
    return None


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


_WEATHER_ICON_ALIASES: dict[str, list[str]] = {
    "clear_day.svg": ["clear_day.svg", "sunny.svg"],
    "clear_night.svg": ["clear_night.svg"],
    "mostly_clear_day.svg": ["mostly_clear_day.svg", "mostly_sunny.svg", "sunny_with_cloudy.svg"],
    "mostly_clear_night.svg": ["mostly_clear_night.svg", "clear_night.svg"],
    "partly_cloudy_day.svg": ["partly_cloudy_day.svg", "cloudy_with_sunny.svg", "partly_cloudy.svg", "sunny_with_cloudy.svg"],
    "partly_cloudy_night.svg": ["partly_cloudy_night.svg", "partly_cloudy.svg", "clear_night.svg"],
    "cloudy.svg": ["cloudy.svg"],
    "haze_fog_dust_smoke.svg": ["haze_fog_dust_smoke.svg", "cloudy.svg", "windy.svg"],
    "drizzle.svg": ["drizzle.svg"],
    "showers_rain.svg": ["showers_rain.svg", "cloudy_with_rain.svg", "rain_with_cloudy.svg", "rain_with_sunny.svg"],
    "heavy_rain.svg": ["heavy_rain.svg", "cloudy_with_rain.svg", "rain_with_cloudy.svg"],
    "mixed_rain_hail_sleet.svg": ["mixed_rain_hail_sleet.svg", "sleet_hail.svg", "icy.svg"],
    "flurries.svg": ["flurries.svg", "showers_snow.svg", "snow_with_cloudy.svg", "snow_with_sunny.svg"],
    "heavy_snow.svg": ["heavy_snow.svg", "cloudy_with_snow.svg", "snow_with_cloudy.svg"],
    "icy.svg": ["icy.svg", "sleet_hail.svg"],
    "thunderstorms.svg": ["thunderstorms.svg", "isolated_thunderstorms.svg", "strong_thunderstorms.svg"],
    "strong_thunderstorms.svg": [
        "strong_thunderstorms.svg",
        "isolated_scattered_thunderstorms_day.svg",
        "isolated_scattered_thunderstorms_night.svg",
        "thunderstorms.svg",
    ],
    "tornado.svg": ["tornado.svg"],
    "hurricane.svg": ["hurricane.svg", "tropical_storm_hurricane.svg"],
    "not-available.svg": ["not-available.svg"],
}


def _weather_icon_theme(theme: object) -> str:
    text = str(theme or "").strip().lower()
    return "light" if text == "light" else "dark"


def _weather_icon_candidates(asset_name: str, theme: str) -> list[tuple[str, ...]]:
    aliases = _WEATHER_ICON_ALIASES.get(asset_name, [asset_name])
    candidates: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for themed in (theme, "light" if theme == "dark" else "dark"):
        for name in aliases:
            key = ("weather", themed, name)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(key)
    fallback = ("weather", "not-available.svg")
    if fallback not in seen:
        candidates.append(fallback)
    return candidates


@router.get("/thumbs/{filename}")
async def thumbs(filename: str):
    # Security: only allow simple filenames like <hex>.jpg
    if "/" in filename or "\\" in filename or ".." in filename:
        return Response(status_code=400)
    path = os.path.join(THUMB_DIR, filename)
    if os.path.exists(path):
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
    # Try to materialize on-demand from the stored mapping (best effort).
    thumb_id = filename[:-4] if filename.lower().endswith(".jpg") else filename
    ok = await asyncio.to_thread(ensure_cached_sync, thumb_id)
    if ok and os.path.exists(path):
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
    return Response(status_code=404)

# =========================
# API Models
# =========================

class PlayReq(BaseModel):
    url: str
    use_ytdlp: bool = True
    cec: bool = False  # default false (most setups don't have /dev/cec0)


class EnqueueReq(BaseModel):
    url: str

class QueueRemoveReq(BaseModel):
    index: int


class QueueMoveReq(BaseModel):
    from_index: int
    to_index: int



class VolumeReq(BaseModel):
    delta: float | None = None
    set: float | None = None


class MuteReq(BaseModel):
    set: bool | None = None


class SeekReq(BaseModel):
    sec: float


class SeekAbsReq(BaseModel):
    sec: float


class HistoryPlayReq(BaseModel):
    index: int


class PlayNowReq(BaseModel):
    """Play immediately, optionally preserving current playback into the queue."""

    url: str
    preserve_current: bool = True
    preserve_to: str = "queue_front"  # future: other strategies
    resume_current: bool = True
    reason: str | None = None
    title: str | None = None
    thumbnail: str | None = None


class PlayTemporaryReq(BaseModel):
    url: str
    resume: bool = True
    resume_mode: str = "auto"
    timeout_sec: float | None = 15.0
    volume_override: float | None = None


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


class JellyfinCommandReq(BaseModel):
    action: str | None = None
    url: str | None = None
    start_pos: float | None = None
    use_ytdlp: bool = True
    payload: dict | None = None


class JellyfinConnectReq(BaseModel):
    server_url: str
    api_key: str | None = None
    device_name: str | None = None
    heartbeat_sec: int | None = None
    register_now: bool = False


class JellyfinItemActionReq(BaseModel):
    item_id: str
    command: str = "play_now"  # play_now|play_next|play_last|resume
    resume_pos: float | None = None


class JellyfinAudioSelectReq(BaseModel):
    index: int


class JellyfinSubtitleSelectReq(BaseModel):
    index: int



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
        os.getenv("RELAYTV_VIDEO_MODE", "")
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
    return upload_store.annotate_item(item)


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
    port = int(os.getenv("PORT", "8787"))
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
    .tTxt{font-size:var(--toast-text-font);line-height:1.28;}
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

class PlayAtReq(BaseModel):
    url: str
    start_at: float



class SettingsReq(BaseModel):
    device_name: str | None = None
    video_mode: str | None = None  # auto|x11|drm
    drm_connector: str | None = None
    drm_mode: str | None = None
    audio_device: str | None = None
    quality_mode: str | None = None  # auto_profile|manual
    quality_cap: str | None = None  # empty|360|480|720|1080
    ytdlp_format: str | None = None
    youtube_cookies_path: str | None = None
    youtube_use_invidious: bool | None = None
    youtube_invidious_base: str | None = None
    sub_lang: str | None = None
    cec_enabled: str | None = None
    tv_takeover_enabled: str | None = None
    tv_pause_on_input_change: str | None = None
    tv_auto_resume_on_return: str | None = None
    volume: float | None = None
    idle_dashboard_enabled: bool | None = None
    idle_qr_enabled: bool | None = None
    idle_qr_size: int | None = None
    idle_panels: dict[str, dict] | None = None
    weather: dict | None = None
    uploads: dict | None = None
    jellyfin_enabled: bool | None = None
    jellyfin_server_url: str | None = None
    jellyfin_username: str | None = None
    jellyfin_password: str | None = None
    jellyfin_user_id: str | None = None
    jellyfin_audio_lang: str | None = None
    jellyfin_sub_lang: str | None = None
    jellyfin_playback_mode: str | None = None
    apply_now: bool = False


class YouTubeCookiesUploadReq(BaseModel):
    cookies_text: str
    filename: str | None = None


_TEMP_PLAYBACK_LOCK = threading.Lock()
_TEMP_PLAYBACK_STACK: list[dict] = []


def _capture_current_playback_state() -> dict | None:
    if not player.is_playing() or not isinstance(state.NOW_PLAYING, dict):
        return None

    with player.MPV_LOCK:
        props = player.mpv_get_many(["time-pos", "pause"])
    pos = props.get("time-pos")
    paused = bool(props.get("pause"))
    try:
        pos_f = float(pos) if pos is not None else None
    except Exception:
        pos_f = None

    with state.QUEUE_LOCK:
        queue_snapshot = list(state.QUEUE)

    return {
        "now_playing": dict(state.NOW_PLAYING),
        "position": pos_f,
        "paused": paused,
        "queue": queue_snapshot,
    }


def _restore_playback_state(snapshot: dict) -> None:
    now = snapshot.get("now_playing") if isinstance(snapshot, dict) else None
    if not isinstance(now, dict):
        return

    with state.QUEUE_LOCK:
        state.QUEUE[:] = list(snapshot.get("queue") or [])
    state.persist_queue()

    start_pos = snapshot.get("position")
    resumed = player.play_item(
        now,
        use_resolver=True,
        cec=False,
        clear_queue=False,
        mode="temporary_resume",
        start_pos=(float(start_pos) if start_pos is not None else None),
    )
    if snapshot.get("paused"):
        try:
            player.mpv_set("pause", True)
            state.set_session_state("paused")
            state.set_pause_reason("temporary")
        except Exception:
            pass
    else:
        state.set_session_state("playing")
        state.set_pause_reason(None)
    state.set_now_playing(resumed)


def _complete_temporary_playback(frame_id: str, reason: str) -> bool:
    with _TEMP_PLAYBACK_LOCK:
        if not _TEMP_PLAYBACK_STACK or _TEMP_PLAYBACK_STACK[-1].get("id") != frame_id:
            return False
        frame = _TEMP_PLAYBACK_STACK.pop()

    if frame.get("resume") and isinstance(frame.get("snapshot"), dict):
        try:
            _restore_playback_state(frame["snapshot"])
        except Exception as e:
            logger.warning("temporary_restore_failed frame_id=%s error=%s", frame_id, e)
            return False
    return True


def _temporary_watchdog(frame_id: str, timeout_sec: float | None) -> None:
    started = time.time()
    while True:
        time.sleep(0.25)
        with _TEMP_PLAYBACK_LOCK:
            is_top = bool(_TEMP_PLAYBACK_STACK) and _TEMP_PLAYBACK_STACK[-1].get("id") == frame_id
        if not is_top:
            return
        if timeout_sec is not None and (time.time() - started) >= float(timeout_sec):
            _complete_temporary_playback(frame_id, reason="timeout")
            return
        if not player.is_playing():
            _complete_temporary_playback(frame_id, reason="ended")
            return

# =========================
# API Endpoints
# =========================

@router.post("/play")
def play(req: PlayReq):
    """Immediate play; clears queue."""
    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0
    item = _smart_item_from_url(req.url or "")
    start_pos = item.get("resume_pos") if isinstance(item, dict) else None
    now = player.play_item(
        item,
        use_resolver=req.use_ytdlp,
        cec=req.cec,
        clear_queue=True,
        mode="play",
        start_pos=(float(start_pos) if start_pos is not None else None),
    )
    return {"status": "playing", "now_playing": now}


@router.post("/enqueue")
@router.post("/queue/add")
@router.post("/api/queue/add")
@router.post("/v1/queue/add")
def enqueue(req: EnqueueReq):
    try:
        item = _smart_item_from_url(req.url or "", lightweight=True)
    except TypeError:
        # Compatibility for tests/patches that mock _smart_item_from_url(url).
        item = _smart_item_from_url(req.url or "")
    with state.QUEUE_LOCK:
        state.QUEUE.append(item)
        qlen = len(state.QUEUE)
        queue_snapshot = list(state.QUEUE)
    state.persist_queue()
    try:
        player.prefetch_queue_item_stream(item)
    except Exception:
        pass
    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    try:
        _push_queue_added_toast_async(item, req.url or "item")
    except Exception:
        pass
    _ui_event_push_queue("add", queue=queue_snapshot, queue_length=qlen, source="enqueue")
    return {"status": "queued", "item": item, "queue_length": qlen, "now_playing": state.NOW_PLAYING}


@router.post("/next")
def next_track():
    try:
        result = dict(player.advance_queue_playback(mode="next", prefer_playlist_next=True, poll_sleep=time.sleep))
    except player.QueueAdvanceEmptyError:
        raise HTTPException(status_code=400, detail="Queue is empty")
    if result.get("method") == "dequeue_play_item":
        result.pop("method", None)
    return result


@router.post("/clear")
def clear():
    with state.QUEUE_LOCK:
        state.QUEUE.clear()
        queue_snapshot: list[object] = []
    state.persist_queue()
    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    _ui_event_push_queue("clear", queue=queue_snapshot, queue_length=0, source="clear")
    return {"status": "cleared"}


@router.get("/queue")
def queue():
    with state.QUEUE_LOCK:
        q = list(state.QUEUE)
    return {
        "now_playing": _annotate_upload_item(state.NOW_PLAYING),
        "queue": _annotate_upload_items(q),
        "queue_length": len(q),
    }


@router.get("/history")
def history():
    with state.HISTORY_LOCK:
        h = list(state.HISTORY)
    return {
        "history": _annotate_upload_items(h),
        "history_length": len(h),
        "limit": state.HISTORY_LIMIT,
    }


@router.post("/history/clear")
def history_clear():
    with state.HISTORY_LOCK:
        state.HISTORY.clear()
    state.persist_history()
    return {"status": "cleared"}


@router.post("/history/play")
def history_play(req: HistoryPlayReq):
    """Play an item from history by index.

    Default behavior:
      - Preserve current playback into the *front* of the queue at its current position
      - Play the selected history item immediately

    This supports "announcement" / "interrupt" style playback without losing your place.
    """
    idx = int(req.index)
    with state.HISTORY_LOCK:
        if idx < 0 or idx >= len(state.HISTORY):
            raise HTTPException(status_code=400, detail="index out of range")
        it = dict(state.HISTORY[idx])
    url = it.get("url")
    if not isinstance(url, str) or not url.strip():
        raise HTTPException(status_code=400, detail="history item missing url")

    return play_now(PlayNowReq(url=url.strip(), preserve_current=True, reason="history"))


def _preserve_current_to_queue_front() -> dict | None:
    """If something is playing, capture it and insert at front of queue with resume_pos."""
    if not player.is_playing():
        return None
    now = state.NOW_PLAYING
    if not isinstance(now, dict):
        return None

    # Capture current position safely.
    pos = None
    with player.MPV_LOCK:
        try:
            pos = player.mpv_get("time-pos")
        except Exception:
            pos = None
    try:
        pos_f = float(pos) if pos is not None else None
    except Exception:
        pos_f = None

    url = now.get("url")
    if not isinstance(url, str) or not url.strip():
        return None

    preserved = {
        "url": url.strip(),
        "title": now.get("title") or url.strip(),
        "provider": now.get("provider"),
    }
    if isinstance(now.get("channel"), str) and now.get("channel"):
        preserved["channel"] = now.get("channel")
    # Preserve thumbnail refs when available
    if isinstance(now.get("thumbnail"), str) and now.get("thumbnail"):
        preserved["thumbnail"] = now.get("thumbnail")
    if isinstance(now.get("thumbnail_local"), str) and now.get("thumbnail_local"):
        preserved["thumbnail_local"] = now.get("thumbnail_local")
    if isinstance(now.get("jellyfin_item_id"), str) and now.get("jellyfin_item_id"):
        preserved["jellyfin_item_id"] = now.get("jellyfin_item_id")
    if isinstance(now.get("jellyfin_media_source_id"), str) and now.get("jellyfin_media_source_id"):
        preserved["jellyfin_media_source_id"] = now.get("jellyfin_media_source_id")
    if pos_f is not None:
        preserved["resume_pos"] = pos_f

    with state.QUEUE_LOCK:
        state.QUEUE.insert(0, preserved)
        snapshot = {"queue": list(state.QUEUE), "saved_at": int(time.time())}
    try:
        state.persist_queue_payload(snapshot)
    except Exception as e:
        logger.warning("queue_persist_failed route=play_now_preserve error=%s", e)
    return preserved


@router.post("/play_now")
def play_now(req: PlayNowReq):
    """Play immediately, optionally preserving the currently playing item.

    If `preserve_current` is true and something is playing, the current item is
    moved to the *front* of the queue with its current position saved as
    `resume_pos`, then the requested URL begins playback.
    """
    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0

    preserved = None
    if req.preserve_current and req.preserve_to == "queue_front" and req.resume_current:
        preserved = _preserve_current_to_queue_front()

    now = player.play_item(req.url, use_resolver=True, cec=False, clear_queue=False, mode=(req.reason or "play_now"))
    try:
        title = now.get("title") if isinstance(now, dict) else None
        _push_overlay_toast(
            text=f"Playing now: {title or req.url}",
            duration=_playback_notification_display_sec(),
            level="success",
            icon="play",
            image_url=(now.get("thumbnail_local") or now.get("thumbnail")) if isinstance(now, dict) else None,
        )
    except Exception:
        pass
    with state.QUEUE_LOCK:
        qlen = len(state.QUEUE)
        queue_snapshot = list(state.QUEUE)
    if preserved is not None or qlen:
        _ui_event_push_queue("play_now", queue=queue_snapshot, queue_length=qlen, source="play_now")
    return {"ok": True, "action": "played", "now_playing": now, "preserved": preserved, "queue_length": qlen}


@router.post("/play_temporary")
def play_temporary(req: PlayTemporaryReq):
    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0
    snapshot = _capture_current_playback_state()
    frame_id = str(uuid.uuid4())
    frame = {
        "id": frame_id,
        "resume": bool(req.resume),
        "snapshot": snapshot,
        "started_at": time.time(),
    }

    if req.volume_override is not None:
        try:
            player.mpv_set("volume", max(0.0, min(200.0, float(req.volume_override) * 100.0)))
        except Exception:
            pass

    with _TEMP_PLAYBACK_LOCK:
        _TEMP_PLAYBACK_STACK.append(frame)

    now = player.play_item(req.url, use_resolver=True, cec=False, clear_queue=False, mode="play_temporary")
    try:
        title = now.get("title") if isinstance(now, dict) else None
        _push_overlay_toast(
            text=f"Temporary playback: {title or req.url}",
            duration=_playback_notification_display_sec(),
            level="warn",
            icon="play",
            image_url=(now.get("thumbnail_local") or now.get("thumbnail")) if isinstance(now, dict) else None,
        )
    except Exception:
        pass
    timeout = float(req.timeout_sec) if req.timeout_sec is not None and req.timeout_sec > 0 else None
    threading.Thread(target=_temporary_watchdog, args=(frame_id, timeout), daemon=True).start()
    return {"ok": True, "temporary_id": frame_id, "now_playing": now, "stack_depth": len(_TEMP_PLAYBACK_STACK)}


@router.post("/play_temporary/cancel")
def play_temporary_cancel():
    with _TEMP_PLAYBACK_LOCK:
        if not _TEMP_PLAYBACK_STACK:
            raise HTTPException(status_code=400, detail="No temporary playback in progress")
        frame_id = _TEMP_PLAYBACK_STACK[-1].get("id")
    restored = _complete_temporary_playback(frame_id, reason="cancel")
    return {"ok": restored, "stack_depth": len(_TEMP_PLAYBACK_STACK)}


@router.post("/overlay")
def overlay(req: OverlayReq):
    text = (req.text or "").strip()
    if not text and req.image_url:
        text = f"[image] {req.image_url}"
    if not text:
        raise HTTPException(status_code=400, detail="text or image_url is required")
    duration_ms = max(250, int(float(req.duration) * 1000.0))
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


@router.get("/notifications/capabilities")
def notifications_capabilities():
    return _notification_capabilities()


@router.get("/runtime/capabilities")
def runtime_capabilities():
    return _runtime_capabilities()


@router.get("/discovery/status")
def discovery_status():
    return {"mdns": discovery_mdns.status()}


def _require_jellyfin_catalog_ready() -> dict[str, object]:
    st = jellyfin_receiver.status()
    if not bool(st.get("enabled")):
        raise HTTPException(status_code=503, detail="jellyfin integration disabled")
    if not bool(st.get("running")):
        raise HTTPException(status_code=503, detail="jellyfin integration not running")
    if not str(st.get("server_url") or "").strip():
        raise HTTPException(status_code=400, detail="jellyfin server_url not configured")
    return st


@router.get("/integrations/jellyfin/status")
def jellyfin_integration_status():
    """Jellyfin receiver integration status (scaffold for casting integration)."""
    return jellyfin_receiver.status()


@router.post("/integrations/jellyfin/catalog/cache_clear")
def jellyfin_catalog_cache_clear():
    """Clear Jellyfin catalog cache for operator troubleshooting."""
    st = jellyfin_receiver.status()
    if not bool(st.get("enabled")):
        raise HTTPException(status_code=503, detail="jellyfin integration disabled")
    out = jellyfin_receiver.clear_catalog_cache(reason="manual_api")
    _ui_event_push_jellyfin(
        "catalog_cache_clear",
        refresh_active_tab=True,
        refresh_settings=True,
        refresh_status=True,
        reason="manual_api",
    )
    return {"ok": True, "status": out}


@router.get("/jellyfin/home")
def jellyfin_home(limit: int = 24, refresh: bool = False):
    st = _require_jellyfin_catalog_ready()
    lim = max(1, min(60, int(limit)))
    try:
        payload = jellyfin_receiver.get_home_rows(limit=lim, refresh=bool(refresh))
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin home rows")
    refreshed = jellyfin_receiver.status()
    return {
        "ok": True,
        "rows": payload.get("rows") if isinstance(payload, dict) else [],
        "generated_ts": payload.get("generated_ts") if isinstance(payload, dict) else None,
        "connected": bool(refreshed.get("connected")),
        "last_error": refreshed.get("last_error"),
        "device_name": st.get("device_name"),
    }


@router.get("/jellyfin/search")
def jellyfin_search(q: str = "", limit: int = 30, refresh: bool = False):
    st = _require_jellyfin_catalog_ready()
    query = str(q or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="q is required")
    lim = max(1, min(100, int(limit)))
    try:
        payload = jellyfin_receiver.search_catalog(query, limit=lim, refresh=bool(refresh))
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to search jellyfin catalog")
    refreshed = jellyfin_receiver.status()
    return {
        "ok": True,
        "query": payload.get("query") if isinstance(payload, dict) else query,
        "count": int(payload.get("count") or 0) if isinstance(payload, dict) else 0,
        "items": payload.get("items") if isinstance(payload, dict) else [],
        "connected": bool(refreshed.get("connected")),
        "last_error": refreshed.get("last_error"),
        "device_name": st.get("device_name"),
    }


@router.get("/jellyfin/movies")
def jellyfin_movies(
    sort: str = "added",
    limit: int = 60,
    start: int = 0,
    starts_with: str = "",
    refresh: bool = False,
):
    st = _require_jellyfin_catalog_ready()
    lim = max(1, min(5000, int(limit)))
    start_index = max(0, int(start))
    try:
        payload = jellyfin_receiver.list_movies(
            sort=str(sort or "added").strip().lower(),
            limit=lim,
            start_index=start_index,
            starts_with=str(starts_with or "").strip(),
            refresh=bool(refresh),
        )
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin movies")
    refreshed = jellyfin_receiver.status()
    return {
        "ok": True,
        "sort": payload.get("sort") if isinstance(payload, dict) else str(sort or "added").strip().lower(),
        "items": payload.get("items") if isinstance(payload, dict) else [],
        "count": int(payload.get("count") or 0) if isinstance(payload, dict) else 0,
        "start_index": int(payload.get("start_index") or start_index) if isinstance(payload, dict) else start_index,
        "limit": int(payload.get("limit") or lim) if isinstance(payload, dict) else lim,
        "next_start_index": payload.get("next_start_index") if isinstance(payload, dict) else None,
        "starts_with": str(payload.get("starts_with") or "") if isinstance(payload, dict) else str(starts_with or "").strip().upper(),
        "connected": bool(refreshed.get("connected")),
        "last_error": refreshed.get("last_error"),
        "device_name": st.get("device_name"),
    }


@router.get("/jellyfin/tv/series")
def jellyfin_tv_series(
    sort: str = "title_asc",
    limit: int = 60,
    start: int = 0,
    starts_with: str = "",
    refresh: bool = False,
):
    st = _require_jellyfin_catalog_ready()
    lim = max(1, min(5000, int(limit)))
    start_index = max(0, int(start))
    try:
        payload = jellyfin_receiver.list_series(
            sort=str(sort or "title_asc").strip().lower(),
            limit=lim,
            start_index=start_index,
            starts_with=str(starts_with or "").strip(),
            refresh=bool(refresh),
        )
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin series")
    refreshed = jellyfin_receiver.status()
    return {
        "ok": True,
        "sort": payload.get("sort") if isinstance(payload, dict) else str(sort or "title_asc").strip().lower(),
        "items": payload.get("items") if isinstance(payload, dict) else [],
        "count": int(payload.get("count") or 0) if isinstance(payload, dict) else 0,
        "start_index": int(payload.get("start_index") or start_index) if isinstance(payload, dict) else start_index,
        "limit": int(payload.get("limit") or lim) if isinstance(payload, dict) else lim,
        "next_start_index": payload.get("next_start_index") if isinstance(payload, dict) else None,
        "starts_with": str(payload.get("starts_with") or "") if isinstance(payload, dict) else str(starts_with or "").strip().upper(),
        "connected": bool(refreshed.get("connected")),
        "last_error": refreshed.get("last_error"),
        "device_name": st.get("device_name"),
    }


@router.get("/jellyfin/tv/series/{series_id}/seasons")
def jellyfin_tv_series_seasons(series_id: str, refresh: bool = False):
    _require_jellyfin_catalog_ready()
    sid = str(series_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="series_id is required")
    try:
        payload = jellyfin_receiver.list_series_seasons(sid, refresh=bool(refresh))
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin seasons")
    refreshed = jellyfin_receiver.status()
    return {
        "ok": True,
        "series_id": sid,
        "seasons": payload.get("seasons") if isinstance(payload, dict) else [],
        "count": int(payload.get("count") or 0) if isinstance(payload, dict) else 0,
        "connected": bool(refreshed.get("connected")),
        "last_error": refreshed.get("last_error"),
    }


@router.get("/jellyfin/tv/series/{series_id}/episodes")
def jellyfin_tv_series_episodes(
    series_id: str,
    season_id: str = "",
    season_number: int | None = None,
    refresh: bool = False,
):
    _require_jellyfin_catalog_ready()
    sid = str(series_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="series_id is required")
    try:
        payload = jellyfin_receiver.list_series_episodes(
            sid,
            season_id=str(season_id or "").strip(),
            season_number=season_number,
            refresh=bool(refresh),
        )
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin episodes")
    refreshed = jellyfin_receiver.status()
    return {
        "ok": True,
        "series_id": sid,
        "season_id": payload.get("season_id") if isinstance(payload, dict) else str(season_id or "").strip(),
        "season_number": payload.get("season_number") if isinstance(payload, dict) else season_number,
        "episodes": payload.get("episodes") if isinstance(payload, dict) else [],
        "count": int(payload.get("count") or 0) if isinstance(payload, dict) else 0,
        "connected": bool(refreshed.get("connected")),
        "last_error": refreshed.get("last_error"),
    }


@router.post("/jellyfin/tv/series/{series_id}/play_all")
def jellyfin_tv_series_play_all(series_id: str, refresh: bool = False):
    _require_jellyfin_catalog_ready()
    sid = str(series_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="series_id is required")
    try:
        payload = jellyfin_receiver.list_series_episodes(sid, refresh=bool(refresh))
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin series episodes")
    episodes = payload.get("episodes") if isinstance(payload, dict) and isinstance(payload.get("episodes"), list) else []
    item_ids = [str(ep.get("item_id") or "").strip() for ep in episodes if isinstance(ep, dict) and str(ep.get("item_id") or "").strip()]
    if not item_ids:
        raise HTTPException(status_code=404, detail="no episodes available for series")
    first = next((ep for ep in episodes if isinstance(ep, dict) and str(ep.get("item_id") or "").strip() == item_ids[0]), {})
    out = jellyfin_integration_command(
        JellyfinCommandReq(
            action="Play",
            payload={"ItemIds": item_ids, "PlayCommand": "PlayNow"},
            play_command="PlayNow",
            use_ytdlp=False,
        )
    )
    return {
        "ok": bool(out.get("ok", True)),
        "series_id": sid,
        "series_title": str(first.get("series_name") or first.get("title") or "").strip(),
        "queued_count": max(0, len(item_ids) - 1),
        "started_item_id": item_ids[0],
        "play_result": out,
    }


@router.get("/jellyfin/item/{item_id}")
def jellyfin_item_detail(item_id: str, refresh: bool = False):
    _require_jellyfin_catalog_ready()
    iid = str(item_id or "").strip()
    if not iid:
        raise HTTPException(status_code=400, detail="item_id is required")
    try:
        item = jellyfin_receiver.get_item_detail(iid, refresh=bool(refresh))
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin item detail")
    refreshed = jellyfin_receiver.status()
    if not item:
        return JSONResponse(
            {
                "ok": False,
                "reason": "not_found",
                "item_id": iid,
                "connected": bool(refreshed.get("connected")),
                "last_error": refreshed.get("last_error"),
            },
            status_code=404,
        )
    return {
        "ok": True,
        "item": item,
        "connected": bool(refreshed.get("connected")),
        "last_error": refreshed.get("last_error"),
    }


@router.get("/jellyfin/item/{item_id}/adjacent")
def jellyfin_item_adjacent(item_id: str, refresh: bool = False):
    _require_jellyfin_catalog_ready()
    iid = str(item_id or "").strip()
    if not iid:
        raise HTTPException(status_code=400, detail="item_id is required")
    try:
        nav = jellyfin_receiver.get_adjacent_episodes(iid, refresh=bool(refresh))
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin adjacent episodes")
    refreshed = jellyfin_receiver.status()
    prev = nav.get("prev") if isinstance(nav, dict) and isinstance(nav.get("prev"), dict) else None
    nxt = nav.get("next") if isinstance(nav, dict) and isinstance(nav.get("next"), dict) else None
    return {
        "ok": True,
        "item_id": iid,
        "prev": prev,
        "next": nxt,
        "connected": bool(refreshed.get("connected")),
        "last_error": refreshed.get("last_error"),
    }


@router.get("/jellyfin/audio/options")
def jellyfin_audio_options(refresh: bool = False):
    _require_jellyfin_catalog_ready()
    now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    if not isinstance(now, dict) or not now:
        raise HTTPException(status_code=409, detail="no active now_playing item")
    provider = str(now.get("provider") or "").strip().lower()
    item_id = str(now.get("jellyfin_item_id") or "").strip()
    if not item_id:
        item_id = _extract_jellyfin_item_id_from_url_raw(str(now.get("url") or ""))
    if provider != "jellyfin" and not item_id:
        raise HTTPException(status_code=409, detail="now_playing is not a jellyfin item")
    if not item_id:
        raise HTTPException(status_code=409, detail="missing jellyfin item_id for current playback")
    try:
        detail = jellyfin_receiver.get_item_detail(item_id, refresh=bool(refresh))
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin stream options")

    audio_streams = detail.get("audio_streams") if isinstance(detail, dict) and isinstance(detail.get("audio_streams"), list) else []
    current_idx = _first_nonempty_str(
        [
            str(now.get("jellyfin_audio_stream_index") or "").strip(),
            _extract_jellyfin_audio_stream_index_from_url(str(now.get("url") or "")),
        ]
    )
    runtime_idx, runtime_lang = _jellyfin_runtime_selected_audio_stream(audio_streams)
    if runtime_idx is not None:
        current_idx = str(runtime_idx)
    selected_numeric: int | None = None
    try:
        if current_idx != "":
            selected_numeric = int(current_idx)
    except Exception:
        selected_numeric = None

    options: list[dict[str, object]] = []
    current_opt: dict[str, object] | None = None
    fallback_default: dict[str, object] | None = None
    fallback_first: dict[str, object] | None = None
    for row in audio_streams:
        if not isinstance(row, dict):
            continue
        try:
            idx_int = int(row.get("index"))
        except Exception:
            continue
        opt = {
            "index": idx_int,
            "language": str(row.get("language") or "").strip(),
            "display": str(row.get("display") or "").strip(),
            "is_default": bool(row.get("is_default")),
            "is_current": False,
        }
        if fallback_first is None:
            fallback_first = opt
        if bool(opt["is_default"]) and fallback_default is None:
            fallback_default = opt
        if selected_numeric is not None and idx_int == selected_numeric:
            opt["is_current"] = True
            current_opt = opt
        options.append(opt)

    if current_opt is None and fallback_default is not None:
        for opt in options:
            if int(opt.get("index")) == int(fallback_default.get("index")):
                opt["is_current"] = True
                current_opt = opt
                break
    if current_opt is None and fallback_first is not None:
        for opt in options:
            if int(opt.get("index")) == int(fallback_first.get("index")):
                opt["is_current"] = True
                current_opt = opt
                break

    current_lang = _first_nonempty_str(
        [
            str((current_opt or {}).get("language") or "").strip(),
            runtime_lang,
            str(now.get("jellyfin_audio_language") or "").strip(),
            str(now.get("audio_language") or "").strip(),
            str(detail.get("audio_language") if isinstance(detail, dict) else "").strip(),
        ]
    )
    return {
        "ok": True,
        "item_id": item_id,
        "current_audio_stream_index": (current_opt or {}).get("index"),
        "current_audio_language": current_lang,
        "options": options,
    }


@router.get("/jellyfin/subtitle/options")
def jellyfin_subtitle_options(refresh: bool = False):
    _require_jellyfin_catalog_ready()
    now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    if not isinstance(now, dict) or not now:
        raise HTTPException(status_code=409, detail="no active now_playing item")
    provider = str(now.get("provider") or "").strip().lower()
    item_id = str(now.get("jellyfin_item_id") or "").strip()
    if not item_id:
        item_id = _extract_jellyfin_item_id_from_url_raw(str(now.get("url") or ""))
    if provider != "jellyfin" and not item_id:
        raise HTTPException(status_code=409, detail="now_playing is not a jellyfin item")
    if not item_id:
        raise HTTPException(status_code=409, detail="missing jellyfin item_id for current playback")
    try:
        detail = jellyfin_receiver.get_item_detail(item_id, refresh=bool(refresh))
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin subtitle options")

    subtitle_streams = detail.get("subtitle_streams") if isinstance(detail, dict) and isinstance(detail.get("subtitle_streams"), list) else []
    current_idx = _first_nonempty_str(
        [
            str(now.get("jellyfin_subtitle_stream_index") or "").strip(),
            _extract_jellyfin_subtitle_stream_index_from_url(str(now.get("url") or "")),
        ]
    )
    runtime_idx, runtime_lang, runtime_off = _jellyfin_runtime_selected_subtitle_stream(subtitle_streams)
    if runtime_off:
        current_idx = "-1"
    elif runtime_idx is not None:
        current_idx = str(runtime_idx)
    current_is_off = current_idx == "-1"
    selected_numeric: int | None = None
    try:
        if not current_is_off and current_idx != "":
            selected_numeric = int(current_idx)
    except Exception:
        selected_numeric = None

    options: list[dict[str, object]] = [{
        "index": -1,
        "language": "",
        "display": "Off",
        "is_default": False,
        "is_current": current_is_off,
        "is_off": True,
    }]
    current_opt: dict[str, object] | None = options[0] if current_is_off else None
    fallback_default: dict[str, object] | None = None
    fallback_first: dict[str, object] | None = None
    for row in subtitle_streams:
        if not isinstance(row, dict):
            continue
        try:
            idx_int = int(row.get("index"))
        except Exception:
            continue
        opt = {
            "index": idx_int,
            "language": str(row.get("language") or "").strip(),
            "display": str(row.get("display") or "").strip(),
            "is_default": bool(row.get("is_default")),
            "is_current": False,
            "is_off": False,
        }
        if fallback_first is None:
            fallback_first = opt
        if bool(opt["is_default"]) and fallback_default is None:
            fallback_default = opt
        if selected_numeric is not None and idx_int == selected_numeric:
            opt["is_current"] = True
            current_opt = opt
        options.append(opt)

    if current_opt is None and fallback_default is not None:
        for opt in options:
            if int(opt.get("index")) == int(fallback_default.get("index")):
                opt["is_current"] = True
                current_opt = opt
                break
    if current_opt is None and fallback_first is not None:
        for opt in options:
            if int(opt.get("index")) == int(fallback_first.get("index")):
                opt["is_current"] = True
                current_opt = opt
                break

    current_lang = "off" if current_is_off else _first_nonempty_str(
        [
            str((current_opt or {}).get("language") or "").strip(),
            runtime_lang,
            str(now.get("jellyfin_subtitle_language") or "").strip(),
            str(now.get("subtitle_language") or "").strip(),
            str(detail.get("subtitle_language") if isinstance(detail, dict) else "").strip(),
        ]
    )
    return {
        "ok": True,
        "item_id": item_id,
        "current_subtitle_stream_index": -1 if current_is_off else (current_opt or {}).get("index"),
        "current_subtitle_language": current_lang,
        "current_subtitle_off": current_is_off,
        "options": options,
    }


@router.post("/jellyfin/audio/select")
def jellyfin_audio_select(req: JellyfinAudioSelectReq):
    st = _require_jellyfin_catalog_ready()
    now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    if not isinstance(now, dict) or not now:
        raise HTTPException(status_code=409, detail="no active now_playing item")
    provider = str(now.get("provider") or "").strip().lower()
    item_id = str(now.get("jellyfin_item_id") or "").strip()
    if not item_id:
        item_id = _extract_jellyfin_item_id_from_url_raw(str(now.get("url") or ""))
    if provider != "jellyfin" and not item_id:
        raise HTTPException(status_code=409, detail="now_playing is not a jellyfin item")
    if not item_id:
        raise HTTPException(status_code=409, detail="missing jellyfin item_id for current playback")

    try:
        detail = jellyfin_receiver.get_item_detail(item_id)
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin item detail")

    audio_streams = detail.get("audio_streams") if isinstance(detail, dict) and isinstance(detail.get("audio_streams"), list) else []
    try:
        requested_idx = int(req.index)
    except Exception:
        raise HTTPException(status_code=400, detail="audio index must be an integer")
    if requested_idx < 0:
        raise HTTPException(status_code=400, detail="audio index must be non-negative")
    requested_audio_language = ""
    requested_audio_display = ""
    if audio_streams:
        valid = False
        for row in audio_streams:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("index")) == requested_idx:
                    valid = True
                    requested_audio_language = str(row.get("language") or "").strip()
                    requested_audio_display = str(row.get("display") or "").strip()
                    break
            except Exception:
                continue
        if not valid:
            raise HTTPException(status_code=400, detail="requested audio stream index is unavailable")
    preferred_audio_lang = _normalize_lang_pref(requested_audio_language)
    queue_retargeted = 0
    if preferred_audio_lang:
        try:
            state.update_settings({"jellyfin_audio_lang": preferred_audio_lang})
        except Exception:
            pass
        try:
            os.environ["RELAYTV_JELLYFIN_AUDIO_LANG"] = preferred_audio_lang
        except Exception:
            pass
        try:
            queue_retargeted = int(_retarget_jellyfin_queue_stream_preferences())
        except Exception:
            queue_retargeted = 0

    was_playing = bool(player.is_playing())
    try:
        props = player.mpv_get_many(["time-pos", "pause"]) if was_playing else {}
    except Exception:
        props = {}
    start_pos: float | None = None
    if isinstance(props, dict):
        try:
            raw_pos = props.get("time-pos")
            if raw_pos is not None:
                start_pos = float(raw_pos)
        except Exception:
            start_pos = None
    if start_pos is None:
        try:
            raw_resume = now.get("resume_pos")
            if raw_resume is not None:
                start_pos = float(raw_resume)
        except Exception:
            start_pos = None
    was_paused = bool((props or {}).get("pause")) or str(getattr(state, "SESSION_STATE", "") or "").strip().lower() == "paused"
    pause_reason = state.get_pause_reason() if hasattr(state, "get_pause_reason") else None

    media_source_id = _first_nonempty_str(
        [
            str(now.get("jellyfin_media_source_id") or "").strip(),
            _extract_jellyfin_media_source_id_from_url(str(now.get("url") or "")),
            str(detail.get("media_source_id") if isinstance(detail, dict) else "").strip(),
        ]
    )
    subtitle_stream_index = _first_nonempty_str(
        [
            str(now.get("jellyfin_subtitle_stream_index") or "").strip(),
            _extract_jellyfin_subtitle_stream_index_from_url(str(now.get("url") or "")),
        ]
    )
    audio_stream_index = str(requested_idx)

    # Fast-path: when mpv exposes multiple audio tracks, switch in-place first.
    # This avoids a full stream handoff and tends to be more reliable for
    # HLS/master playlists that carry alternate audio groups.
    if _jellyfin_try_set_mpv_audio_track(language=requested_audio_language, display=requested_audio_display):
        now_out = _jellyfin_enrich_now_stream_metadata(
            dict(now),
            detail=detail if isinstance(detail, dict) else {},
            audio_stream_index=audio_stream_index,
            subtitle_stream_index=subtitle_stream_index,
        )
        state.set_now_playing(now_out)
        _jellyfin_emit_progress_hint()
        return {
            "ok": True,
            "method": "mpv_runtime_aid",
            "item_id": item_id,
            "current_audio_stream_index": requested_idx,
            "current_audio_language": str(now_out.get("jellyfin_audio_language") or now_out.get("audio_language") or "").strip(),
            "queued_items_retargeted": queue_retargeted,
            "now_playing": now_out,
        }

    try:
        settings_snapshot = state.get_settings()
    except Exception:
        settings_snapshot = {}
    auth_token = _jellyfin_access_token()

    source_url = _build_jellyfin_item_stream_url(
        item_id,
        server_url=str(st.get("server_url") or ""),
        api_key=auth_token,
        media_source_id=media_source_id,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
    )
    selected_stream = _select_jellyfin_playback_url(
        item_id=item_id,
        source_url=source_url,
        server_url=str(st.get("server_url") or ""),
        api_key=auth_token,
        media_source_id=media_source_id,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
        settings=settings_snapshot if isinstance(settings_snapshot, dict) else {},
    )
    source_url = _normalize_jellyfin_source_url(
        str(selected_stream.get("url") or source_url),
        server_url=str(st.get("server_url") or ""),
        api_key=auth_token,
    )
    source_url = _apply_jellyfin_stream_params(
        source_url,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
    )
    media_source_id = _first_nonempty_str(
        [
            str(selected_stream.get("media_source_id") or "").strip(),
            _extract_jellyfin_media_source_id_from_url(source_url),
            media_source_id,
        ]
    )
    if not source_url:
        raise HTTPException(status_code=502, detail="unable to build jellyfin stream url")

    play_payload = {
        "url": source_url,
        "provider": "jellyfin",
        "title": str(now.get("title") or "").strip() or f"Jellyfin item {item_id}",
        **({"channel": now.get("channel")} if now.get("channel") else {}),
        **({"thumbnail": now.get("thumbnail")} if now.get("thumbnail") else {}),
        **({"thumbnail_local": now.get("thumbnail_local")} if now.get("thumbnail_local") else {}),
        "jellyfin_item_id": item_id,
        **({"jellyfin_media_source_id": media_source_id} if media_source_id else {}),
    }

    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0
    switched = player.play_item(
        play_payload,
        use_resolver=False,
        cec=False,
        clear_queue=False,
        mode="jellyfin_audio_switch",
        start_pos=start_pos,
    )
    now_out = switched if isinstance(switched, dict) else dict(play_payload)
    now_out["jellyfin_item_id"] = item_id
    if media_source_id:
        now_out["jellyfin_media_source_id"] = media_source_id
    now_out["jellyfin_stream_mode"] = str(selected_stream.get("mode") or "direct")
    now_out["jellyfin_stream_reason"] = str(selected_stream.get("reason") or "")
    now_out = _jellyfin_enrich_now_stream_metadata(
        now_out,
        detail=detail if isinstance(detail, dict) else {},
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
    )
    _jellyfin_try_set_mpv_audio_track(language=requested_audio_language, display=requested_audio_display)
    state.set_now_playing(now_out)
    if was_paused:
        try:
            player.mpv_set("pause", True)
        except Exception:
            pass
        state.set_session_state("paused")
        state.set_pause_reason(pause_reason if pause_reason is not None else "user")
    _jellyfin_emit_progress_hint()
    return {
        "ok": True,
        "item_id": item_id,
        "current_audio_stream_index": requested_idx,
        "current_audio_language": str(now_out.get("jellyfin_audio_language") or now_out.get("audio_language") or "").strip(),
        "queued_items_retargeted": queue_retargeted,
        "now_playing": now_out,
    }


@router.post("/jellyfin/subtitle/select")
def jellyfin_subtitle_select(req: JellyfinSubtitleSelectReq):
    st = _require_jellyfin_catalog_ready()
    now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    if not isinstance(now, dict) or not now:
        raise HTTPException(status_code=409, detail="no active now_playing item")
    provider = str(now.get("provider") or "").strip().lower()
    item_id = str(now.get("jellyfin_item_id") or "").strip()
    if not item_id:
        item_id = _extract_jellyfin_item_id_from_url_raw(str(now.get("url") or ""))
    if provider != "jellyfin" and not item_id:
        raise HTTPException(status_code=409, detail="now_playing is not a jellyfin item")
    if not item_id:
        raise HTTPException(status_code=409, detail="missing jellyfin item_id for current playback")

    try:
        detail = jellyfin_receiver.get_item_detail(item_id)
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin item detail")

    subtitle_streams = detail.get("subtitle_streams") if isinstance(detail, dict) and isinstance(detail.get("subtitle_streams"), list) else []
    try:
        requested_idx = int(req.index)
    except Exception:
        raise HTTPException(status_code=400, detail="subtitle index must be an integer")
    if requested_idx < -1:
        raise HTTPException(status_code=400, detail="subtitle index must be -1 or non-negative")
    requested_subtitle_language = ""
    requested_subtitle_display = ""
    if requested_idx >= 0 and subtitle_streams:
        valid = False
        for row in subtitle_streams:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("index")) == requested_idx:
                    valid = True
                    requested_subtitle_language = str(row.get("language") or "").strip()
                    requested_subtitle_display = str(row.get("display") or "").strip()
                    break
            except Exception:
                continue
        if not valid:
            raise HTTPException(status_code=400, detail="requested subtitle stream index is unavailable")
    preferred_subtitle_lang = "off" if requested_idx < 0 else _normalize_lang_pref(requested_subtitle_language)
    queue_retargeted = 0
    try:
        state.update_settings({"jellyfin_sub_lang": preferred_subtitle_lang})
    except Exception:
        pass
    try:
        os.environ["RELAYTV_JELLYFIN_SUB_LANG"] = preferred_subtitle_lang
    except Exception:
        pass
    try:
        queue_retargeted = int(_retarget_jellyfin_queue_stream_preferences())
    except Exception:
        queue_retargeted = 0

    was_playing = bool(player.is_playing())
    try:
        props = player.mpv_get_many(["time-pos", "pause"]) if was_playing else {}
    except Exception:
        props = {}
    start_pos: float | None = None
    if isinstance(props, dict):
        try:
            raw_pos = props.get("time-pos")
            if raw_pos is not None:
                start_pos = float(raw_pos)
        except Exception:
            start_pos = None
    if start_pos is None:
        try:
            raw_resume = now.get("resume_pos")
            if raw_resume is not None:
                start_pos = float(raw_resume)
        except Exception:
            start_pos = None
    was_paused = bool((props or {}).get("pause")) or str(getattr(state, "SESSION_STATE", "") or "").strip().lower() == "paused"
    pause_reason = state.get_pause_reason() if hasattr(state, "get_pause_reason") else None

    media_source_id = _first_nonempty_str(
        [
            str(now.get("jellyfin_media_source_id") or "").strip(),
            _extract_jellyfin_media_source_id_from_url(str(now.get("url") or "")),
            str(detail.get("media_source_id") if isinstance(detail, dict) else "").strip(),
        ]
    )
    audio_stream_index = _first_nonempty_str(
        [
            str(now.get("jellyfin_audio_stream_index") or "").strip(),
            _extract_jellyfin_audio_stream_index_from_url(str(now.get("url") or "")),
        ]
    )
    subtitle_stream_index = "-1" if requested_idx < 0 else str(requested_idx)

    if _jellyfin_try_set_mpv_subtitle_track(
        language=requested_subtitle_language,
        display=requested_subtitle_display,
        preferred_stream_index=(requested_idx if requested_idx >= 0 else None),
        off=(requested_idx < 0),
    ):
        now_out = _jellyfin_enrich_now_stream_metadata(
            dict(now),
            detail=detail if isinstance(detail, dict) else {},
            audio_stream_index=audio_stream_index,
            subtitle_stream_index=subtitle_stream_index,
        )
        state.set_now_playing(now_out)
        _jellyfin_emit_progress_hint()
        return {
            "ok": True,
            "method": "mpv_runtime_sid",
            "item_id": item_id,
            "current_subtitle_stream_index": requested_idx,
            "current_subtitle_language": str(now_out.get("jellyfin_subtitle_language") or now_out.get("subtitle_language") or "").strip(),
            "current_subtitle_off": requested_idx < 0,
            "queued_items_retargeted": queue_retargeted,
            "now_playing": now_out,
        }

    try:
        settings_snapshot = state.get_settings()
    except Exception:
        settings_snapshot = {}
    auth_token = _jellyfin_access_token()

    source_url = _build_jellyfin_item_stream_url(
        item_id,
        server_url=str(st.get("server_url") or ""),
        api_key=auth_token,
        media_source_id=media_source_id,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
    )
    selected_stream = _select_jellyfin_playback_url(
        item_id=item_id,
        source_url=source_url,
        server_url=str(st.get("server_url") or ""),
        api_key=auth_token,
        media_source_id=media_source_id,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
        settings=settings_snapshot if isinstance(settings_snapshot, dict) else {},
    )
    source_url = _normalize_jellyfin_source_url(
        str(selected_stream.get("url") or source_url),
        server_url=str(st.get("server_url") or ""),
        api_key=auth_token,
    )
    source_url = _apply_jellyfin_stream_params(
        source_url,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
    )
    media_source_id = _first_nonempty_str(
        [
            str(selected_stream.get("media_source_id") or "").strip(),
            _extract_jellyfin_media_source_id_from_url(source_url),
            media_source_id,
        ]
    )
    if not source_url:
        raise HTTPException(status_code=502, detail="unable to build jellyfin stream url")

    play_payload = {
        "url": source_url,
        "provider": "jellyfin",
        "title": str(now.get("title") or "").strip() or f"Jellyfin item {item_id}",
        **({"channel": now.get("channel")} if now.get("channel") else {}),
        **({"thumbnail": now.get("thumbnail")} if now.get("thumbnail") else {}),
        **({"thumbnail_local": now.get("thumbnail_local")} if now.get("thumbnail_local") else {}),
        "jellyfin_item_id": item_id,
        **({"jellyfin_media_source_id": media_source_id} if media_source_id else {}),
    }

    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0
    switched = player.play_item(
        play_payload,
        use_resolver=False,
        cec=False,
        clear_queue=False,
        mode="jellyfin_subtitle_switch",
        start_pos=start_pos,
    )
    now_out = switched if isinstance(switched, dict) else dict(play_payload)
    now_out["jellyfin_item_id"] = item_id
    if media_source_id:
        now_out["jellyfin_media_source_id"] = media_source_id
    now_out["jellyfin_stream_mode"] = str(selected_stream.get("mode") or "direct")
    now_out["jellyfin_stream_reason"] = str(selected_stream.get("reason") or "")
    now_out = _jellyfin_enrich_now_stream_metadata(
        now_out,
        detail=detail if isinstance(detail, dict) else {},
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
    )
    _jellyfin_try_set_mpv_subtitle_track(
        language=requested_subtitle_language,
        display=requested_subtitle_display,
        preferred_stream_index=(requested_idx if requested_idx >= 0 else None),
        off=(requested_idx < 0),
    )
    state.set_now_playing(now_out)
    if was_paused:
        try:
            player.mpv_set("pause", True)
        except Exception:
            pass
        state.set_session_state("paused")
        state.set_pause_reason(pause_reason if pause_reason is not None else "user")
    _jellyfin_emit_progress_hint()
    return {
        "ok": True,
        "item_id": item_id,
        "current_subtitle_stream_index": requested_idx,
        "current_subtitle_language": str(now_out.get("jellyfin_subtitle_language") or now_out.get("subtitle_language") or "").strip(),
        "current_subtitle_off": requested_idx < 0,
        "queued_items_retargeted": queue_retargeted,
        "now_playing": now_out,
    }


@router.post("/jellyfin/action")
def jellyfin_item_action(req: JellyfinItemActionReq):
    _require_jellyfin_catalog_ready()
    item_id = str(req.item_id or "").strip()
    if not item_id:
        raise HTTPException(status_code=400, detail="item_id is required")
    command = str(req.command or "play_now").strip().lower()
    if command not in {"play_now", "play_next", "play_last", "resume"}:
        raise HTTPException(status_code=400, detail="unsupported command")

    payload: dict[str, object] = {"ItemId": item_id, "PlayCommand": "PlayNow"}
    if command == "play_next":
        payload["PlayCommand"] = "PlayNext"
    elif command == "play_last":
        payload["PlayCommand"] = "PlayLast"

    start_pos: float | None = None
    if command == "resume":
        if req.resume_pos is not None:
            try:
                rp = float(req.resume_pos)
                if rp > 0:
                    start_pos = rp
            except Exception:
                start_pos = None
        if start_pos is None:
            try:
                meta = jellyfin_receiver.get_item_detail(item_id)
                rp2 = float(meta.get("resume_pos")) if isinstance(meta, dict) and meta.get("resume_pos") is not None else None
                if rp2 is not None and rp2 > 0:
                    start_pos = rp2
            except Exception:
                start_pos = None

    if _jellyfin_should_suppress_duplicate_ui_action(command, item_id, start_pos):
        return {
            "ok": True,
            "action": "ui_suppressed",
            "suppressed_duplicate_ui_action": True,
            "ui_command": command,
            "item_id": item_id,
            "resolved_resume_pos": start_pos,
        }

    out = jellyfin_integration_command(
        JellyfinCommandReq(
            action="Play",
            start_pos=start_pos,
            use_ytdlp=True,
            payload=payload,
        )
    )
    if isinstance(out, dict):
        out = dict(out)
        out["ui_command"] = command
        out["item_id"] = item_id
        out["resolved_resume_pos"] = start_pos
    return out


@router.post("/integrations/jellyfin/connect")
def jellyfin_integration_connect(req: JellyfinConnectReq):
    server_url = (req.server_url or "").strip()
    if not server_url:
        raise HTTPException(status_code=400, detail="server_url is required")
    settings_name = ""
    try:
        settings_name = str((state.get_settings() if hasattr(state, "get_settings") else {}).get("device_name") or "").strip()
    except Exception:
        settings_name = ""
    out = jellyfin_receiver.connect(
        server_url=server_url,
        api_key=req.api_key,
        device_name=(req.device_name or settings_name or None),
        heartbeat_sec=req.heartbeat_sec,
    )
    if bool(req.register_now):
        out = dict(out)
        out["register"] = jellyfin_receiver.register_receiver_once()
    _reset_jellyfin_command_state()
    _ui_event_push_jellyfin("connect", refresh_active_tab=True, refresh_settings=True, refresh_status=True)
    return out


@router.post("/integrations/jellyfin/disconnect")
def jellyfin_integration_disconnect():
    _reset_jellyfin_command_state()
    out = jellyfin_receiver.disconnect()
    _ui_event_push_jellyfin("disconnect", refresh_active_tab=True, refresh_settings=True, refresh_status=True)
    return out


@router.post("/integrations/jellyfin/register")
def jellyfin_integration_register():
    """Force a single Jellyfin receiver registration handshake."""
    st = jellyfin_receiver.status()
    if not bool(st.get("enabled")):
        raise HTTPException(status_code=503, detail="jellyfin integration disabled")
    out = jellyfin_receiver.register_receiver_once()
    _ui_event_push_jellyfin("register", refresh_active_tab=True, refresh_settings=True, refresh_status=True)
    if not bool(out.get("ok")):
        return JSONResponse(out, status_code=202)
    return out


def _first_nonempty_str(values: list[object]) -> str:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _jellyfin_access_token() -> str:
    """Prefer authenticated login-session token; fall back to configured API key."""
    return _first_nonempty_str([jellyfin_receiver.session_token(), jellyfin_receiver.api_key()])


def _extract_jellyfin_play_url(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    # Common direct fields.
    url = _first_nonempty_str(
        [
            payload.get("url"),
            payload.get("stream_url"),
            payload.get("playback_url"),
            payload.get("direct_stream_url"),
            payload.get("transcoding_url"),
        ]
    )
    if url:
        return url

    # Common nested item/media-source fields.
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    media_sources = payload.get("MediaSources")
    if not isinstance(media_sources, list):
        media_sources = item.get("MediaSources") if isinstance(item, dict) else None
    first_media = media_sources[0] if isinstance(media_sources, list) and media_sources and isinstance(media_sources[0], dict) else {}
    url = _first_nonempty_str(
        [
            first_media.get("DirectStreamUrl"),
            first_media.get("TranscodingUrl"),
        ]
    )
    if url:
        return url

    # Fallback for command payload wrappers.
    play_cmd = payload.get("playCommand") if isinstance(payload.get("playCommand"), dict) else {}
    return _first_nonempty_str([play_cmd.get("url"), play_cmd.get("stream_url"), play_cmd.get("playback_url")])


def _extract_jellyfin_item_id(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    direct = _first_nonempty_str([payload.get("item_id"), payload.get("itemId"), payload.get("ItemId"), payload.get("id"), payload.get("Id")])
    if direct:
        return direct
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    return _first_nonempty_str([item.get("id"), item.get("Id"), item.get("item_id"), item.get("itemId")])


def _canonical_jellyfin_item_id(raw: str | None) -> str:
    """Normalize Jellyfin ids for dedupe across hyphenated/non-hyphenated forms."""
    s = str(raw or "").strip().lower()
    if not s:
        return ""
    return s.replace("-", "")


def _canonical_jellyfin_media_source_id(raw: str | None) -> str:
    return str(raw or "").strip().lower()


def _extract_jellyfin_item_id_from_url(raw_url: str | None) -> str:
    u = str(raw_url or "").strip()
    if not u:
        return ""
    try:
        parts = urlsplit(u)
        segs = [seg for seg in (parts.path or "").split("/") if seg]
        for idx, seg in enumerate(segs):
            low = seg.lower()
            if low in ("videos", "items") and idx + 1 < len(segs):
                return _canonical_jellyfin_item_id(segs[idx + 1])
    except Exception:
        return ""
    return ""


def _canonical_jellyfin_url_key(raw_url: str | None) -> str:
    """
    Build a stable Jellyfin media key from url for dedupe.
    Prefer canonical item id from /Videos/<id>/ or /Items/<id>/ path and
    include mediaSourceId when present so multi-version items do not collapse.
    """
    u = str(raw_url or "").strip()
    if not u:
        return ""
    try:
        parts = urlsplit(u)
        iid = _extract_jellyfin_item_id_from_url(u)
        if iid:
            q = dict(parse_qsl(parts.query, keep_blank_values=True))
            mid = _canonical_jellyfin_media_source_id(
                _first_nonempty_str([q.get("mediaSourceId"), q.get("MediaSourceId"), q.get("mediasourceid")])
            )
            if mid:
                return f"{iid}::{mid}"
            return iid
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))
    except Exception:
        return u


def _extract_jellyfin_media_source_id(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    direct = _first_nonempty_str(
        [
            payload.get("media_source_id"),
            payload.get("mediaSourceId"),
            payload.get("MediaSourceId"),
        ]
    )
    if direct:
        return direct
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    media_sources = payload.get("MediaSources")
    if not isinstance(media_sources, list):
        media_sources = item.get("MediaSources") if isinstance(item, dict) else None
    first_media = media_sources[0] if isinstance(media_sources, list) and media_sources and isinstance(media_sources[0], dict) else {}
    return _first_nonempty_str(
        [
            first_media.get("Id"),
            first_media.get("id"),
            first_media.get("MediaSourceId"),
            first_media.get("mediaSourceId"),
        ]
    )


def _extract_jellyfin_item_ids(payload: dict | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("ItemIds")
    if not isinstance(raw, list):
        raw = payload.get("item_ids")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for it in raw:
        s = str(it or "").strip()
        if s:
            out.append(s)
    return out


def _extract_jellyfin_playlist_items(payload: dict | None) -> list[dict[str, str]]:
    """
    Return playlist entries from common Jellyfin payload shapes.
    Output entries use keys: id, title, media_source_id.
    """
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, str]] = []

    def _append_entry(item_id: str, title: str = "", media_source_id: str = "") -> None:
        iid = str(item_id or "").strip()
        if not iid:
            return
        out.append(
            {
                "id": iid,
                "title": str(title or "").strip(),
                "media_source_id": str(media_source_id or "").strip(),
            }
        )

    # Rich Items list (e.g., [{"Id":"...","Name":"...","MediaSourceId":"..."}]).
    for key in ("Items", "items", "PlaylistItems", "playlist_items"):
        raw_items = payload.get(key)
        if isinstance(raw_items, list):
            for it in raw_items:
                if not isinstance(it, dict):
                    continue
                item_id = _first_nonempty_str([it.get("Id"), it.get("id"), it.get("ItemId"), it.get("itemId")])
                title = _first_nonempty_str([it.get("Name"), it.get("name"), it.get("Title"), it.get("title")])
                media_source_id = _first_nonempty_str(
                    [it.get("MediaSourceId"), it.get("mediaSourceId"), it.get("MediaSourceID"), it.get("media_source_id")]
                )
                _append_entry(item_id, title, media_source_id)
            if out:
                return out

    # Fallback to ItemIds list.
    for iid in _extract_jellyfin_item_ids(payload):
        _append_entry(iid)
    return out


def _extract_jellyfin_play_mode(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    mode = _first_nonempty_str(
        [
            payload.get("PlayCommand"),
            payload.get("play_command"),
            payload.get("PlayMode"),
            payload.get("play_mode"),
            payload.get("commandMode"),
            payload.get("mode"),
        ]
    ).lower()
    aliases = {
        "playnext": "playnext",
        "next": "playnext",
        "playlast": "playlast",
        "enqueue": "playlast",
        "playnow": "playnow",
        "replaceall": "playnow",
    }
    return aliases.get(mode, mode)


def _normalize_jellyfin_action(action: str | None, payload: dict | None) -> str:
    raw = (action or "").strip().lower()
    if not raw and isinstance(payload, dict):
        raw = _first_nonempty_str(
            [
                payload.get("action"),
                payload.get("Action"),
                payload.get("command"),
                payload.get("Command"),
                payload.get("name"),
                payload.get("Name"),
            ]
        ).lower()
    aliases = {
        "playnow": "play",
        "playnext": "next",
        "nexttrack": "next",
        "previoustrack": "previous",
        "setvolume": "set_volume",
        "muteaudio": "mute",
        "unmuteaudio": "unmute",
        "pauseplayback": "pause",
        "resumeback": "resume",
        "unpauseplayback": "resume",
        "resumeplayback": "resume",
    }
    return aliases.get(raw, raw)


def _jellyfin_ticks_to_seconds(value: object) -> float | None:
    try:
        v = float(value)
    except Exception:
        return None
    # Jellyfin ticks are 10,000,000 per second.
    if abs(v) > 1_000_000:
        return v / 10_000_000.0
    return v


def _extract_jellyfin_seek_seconds(req: JellyfinCommandReq) -> float | None:
    if req.start_pos is not None:
        return float(req.start_pos)
    payload = req.payload if isinstance(req.payload, dict) else {}
    for key in ("position", "seek", "PositionMs", "position_ms"):
        if key in payload:
            sec = _jellyfin_ticks_to_seconds(payload.get(key))
            if sec is not None:
                # PositionMs style values should be converted from ms when small.
                if key.lower().endswith("ms"):
                    return sec / 1000.0
                return sec
    for key in ("PositionTicks", "position_ticks", "SeekPositionTicks", "seek_position_ticks"):
        if key in payload:
            sec = _jellyfin_ticks_to_seconds(payload.get(key))
            if sec is not None:
                return sec
    return None


def _extract_jellyfin_start_seconds(req: JellyfinCommandReq) -> float | None:
    if req.start_pos is not None:
        return float(req.start_pos)
    payload = req.payload if isinstance(req.payload, dict) else {}
    for key in ("StartPositionTicks", "start_position_ticks", "position", "PositionTicks"):
        if key in payload:
            sec = _jellyfin_ticks_to_seconds(payload.get(key))
            if sec is not None:
                return sec
    return None


def _extract_jellyfin_command_id(req: JellyfinCommandReq) -> str:
    payload = req.payload if isinstance(req.payload, dict) else {}
    return _first_nonempty_str(
        [
            payload.get("CommandId"),
            payload.get("commandId"),
            payload.get("MessageId"),
            payload.get("messageId"),
            payload.get("EventId"),
            payload.get("eventId"),
        ]
    )


def _reset_jellyfin_command_state() -> None:
    with _JELLYFIN_PLAY_DEBOUNCE_LOCK:
        _JELLYFIN_LAST_PLAY.update({"ts": 0.0, "url": "", "item_id": "", "start_pos": None})
    with _JELLYFIN_COMMAND_DEDUPE_LOCK:
        _JELLYFIN_RECENT_COMMAND_IDS.clear()
    with _JELLYFIN_UI_ACTION_DEDUPE_LOCK:
        _JELLYFIN_LAST_UI_ACTION.update({"ts": 0.0, "command": "", "item_id": "", "resume_pos": None})


def _jellyfin_is_duplicate_command(command_id: str) -> bool:
    cid = str(command_id or "").strip()
    if not cid:
        return False
    ttl = max(1.0, float(os.getenv("RELAYTV_JELLYFIN_COMMAND_ID_TTL_SEC", "30")))
    now_ts = time.time()
    with _JELLYFIN_COMMAND_DEDUPE_LOCK:
        # prune expired ids
        expired = [k for k, ts in _JELLYFIN_RECENT_COMMAND_IDS.items() if (now_ts - ts) > ttl]
        for k in expired:
            _JELLYFIN_RECENT_COMMAND_IDS.pop(k, None)
        if cid in _JELLYFIN_RECENT_COMMAND_IDS:
            _JELLYFIN_RECENT_COMMAND_IDS[cid] = now_ts
            return True
        _JELLYFIN_RECENT_COMMAND_IDS[cid] = now_ts
        return False


def _extract_jellyfin_volume(req: JellyfinCommandReq) -> float | None:
    payload = req.payload if isinstance(req.payload, dict) else {}
    for key in ("volume", "Volume", "volume_level", "VolumeLevel"):
        if key in payload:
            try:
                v = float(payload.get(key))
                return max(0.0, min(200.0, v))
            except Exception:
                continue
    return None


def _jellyfin_should_suppress_duplicate_play(url: str, item_id: str, start_pos: float | None) -> bool:
    window_sec = max(0.0, float(os.getenv("RELAYTV_JELLYFIN_PLAY_DEBOUNCE_SEC", "1.5")))
    if window_sec <= 0:
        return False
    now_ts = time.time()
    with _JELLYFIN_PLAY_DEBOUNCE_LOCK:
        last_ts = float(_JELLYFIN_LAST_PLAY.get("ts") or 0.0)
        if now_ts - last_ts > window_sec:
            _JELLYFIN_LAST_PLAY.update({"ts": now_ts, "url": url, "item_id": item_id, "start_pos": start_pos})
            return False
        same_url = str(_JELLYFIN_LAST_PLAY.get("url") or "") == str(url or "")
        same_item = str(_JELLYFIN_LAST_PLAY.get("item_id") or "") == str(item_id or "")
        last_start = _JELLYFIN_LAST_PLAY.get("start_pos")
        try:
            delta = abs(float(last_start) - float(start_pos)) if (last_start is not None and start_pos is not None) else 0.0
        except Exception:
            delta = 0.0
        same_start = (last_start is None and start_pos is None) or (delta < 1.0)
        suppressed = same_url and (same_item or (not item_id)) and same_start
        _JELLYFIN_LAST_PLAY.update({"ts": now_ts, "url": url, "item_id": item_id, "start_pos": start_pos})
        return suppressed


def _jellyfin_should_suppress_duplicate_ui_action(command: str, item_id: str, resume_pos: float | None) -> bool:
    window_sec = max(0.0, float(os.getenv("RELAYTV_JELLYFIN_UI_ACTION_DEDUPE_SEC", "1.5")))
    if window_sec <= 0:
        return False
    now_ts = time.time()
    norm_cmd = str(command or "").strip().lower()
    norm_item_id = _canonical_jellyfin_item_id(item_id)
    with _JELLYFIN_UI_ACTION_DEDUPE_LOCK:
        last_ts = float(_JELLYFIN_LAST_UI_ACTION.get("ts") or 0.0)
        if now_ts - last_ts > window_sec:
            _JELLYFIN_LAST_UI_ACTION.update(
                {"ts": now_ts, "command": norm_cmd, "item_id": norm_item_id, "resume_pos": resume_pos}
            )
            return False
        same_cmd = str(_JELLYFIN_LAST_UI_ACTION.get("command") or "") == norm_cmd
        same_item = str(_JELLYFIN_LAST_UI_ACTION.get("item_id") or "") == norm_item_id
        last_resume = _JELLYFIN_LAST_UI_ACTION.get("resume_pos")
        try:
            delta = abs(float(last_resume) - float(resume_pos)) if (last_resume is not None and resume_pos is not None) else 0.0
        except Exception:
            delta = 0.0
        same_resume = (last_resume is None and resume_pos is None) or (delta < 1.0)
        suppressed = same_cmd and same_item and same_resume
        _JELLYFIN_LAST_UI_ACTION.update(
            {"ts": now_ts, "command": norm_cmd, "item_id": norm_item_id, "resume_pos": resume_pos}
        )
        return suppressed


def _normalize_jellyfin_source_url(raw_url: str, *, server_url: str, api_key: str) -> str:
    u = (raw_url or "").strip()
    if not u:
        return ""
    if u.startswith("http://") or u.startswith("https://"):
        return u
    base = (server_url or "").strip().rstrip("/")
    if not base:
        return u
    path = u if u.startswith("/") else f"/{u}"
    abs_url = f"{base}{path}"
    token = (api_key or "").strip()
    if not token:
        return abs_url
    try:
        parts = urlsplit(abs_url)
        q = dict(parse_qsl(parts.query, keep_blank_values=True))
        if "api_key" not in q and "ApiKey" not in q:
            q["api_key"] = token
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))
        return abs_url
    except Exception:
        return abs_url


def _build_jellyfin_item_stream_url(
    item_id: str,
    *,
    server_url: str,
    api_key: str,
    media_source_id: str = "",
    audio_stream_index: str = "",
    subtitle_stream_index: str = "",
) -> str:
    iid = (item_id or "").strip()
    base = (server_url or "").strip().rstrip("/")
    if not iid or not base:
        return ""
    params = {"static": "true"}
    if media_source_id:
        params["mediaSourceId"] = media_source_id
    if audio_stream_index != "":
        params["audioStreamIndex"] = str(audio_stream_index)
    if subtitle_stream_index != "":
        params["subtitleStreamIndex"] = str(subtitle_stream_index)
    if api_key:
        params["api_key"] = api_key
    return f"{base}/Videos/{iid}/stream?{urlencode(params)}"


def _build_jellyfin_item_transcode_url(
    item_id: str,
    *,
    server_url: str,
    api_key: str,
    media_source_id: str = "",
    audio_stream_index: str = "",
    subtitle_stream_index: str = "",
    max_height: int | None = None,
    max_streaming_bitrate: int | None = None,
) -> str:
    iid = (item_id or "").strip()
    base = (server_url or "").strip().rstrip("/")
    if not iid or not base:
        return ""
    params: dict[str, str] = {
        "VideoCodec": "h264",
        "AudioCodec": "aac,mp3,ac3,eac3,opus",
        "SegmentContainer": "ts",
        "BreakOnNonKeyFrames": "True",
    }
    if api_key:
        params["api_key"] = api_key
    if media_source_id:
        params["MediaSourceId"] = media_source_id
    if audio_stream_index != "":
        params["AudioStreamIndex"] = str(audio_stream_index)
    if subtitle_stream_index != "":
        params["SubtitleStreamIndex"] = str(subtitle_stream_index)
    if max_height is not None:
        try:
            h = int(max_height)
            if h > 0:
                params["MaxHeight"] = str(h)
        except Exception:
            pass
    if max_streaming_bitrate is not None:
        try:
            bps = int(max_streaming_bitrate)
            if bps > 0:
                params["MaxStreamingBitrate"] = str(bps)
                params["VideoBitrate"] = str(bps)
        except Exception:
            pass
    return f"{base}/Videos/{iid}/master.m3u8?{urlencode(params)}"


def _normalize_jellyfin_playback_mode(raw: object) -> str:
    s = str(raw or "").strip().lower()
    if s in ("direct", "transcode", "auto"):
        return s
    return "auto"


def _effective_jellyfin_playback_mode(settings: dict | None = None) -> str:
    src = settings if isinstance(settings, dict) else (state.get_settings() if hasattr(state, "get_settings") else {})
    val = src.get("jellyfin_playback_mode") if isinstance(src, dict) else None
    if val is None or str(val).strip() == "":
        val = os.getenv("RELAYTV_JELLYFIN_PLAYBACK_MODE", "auto")
    return _normalize_jellyfin_playback_mode(val)


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _native_jellyfin_auto_transcode_guard_active(*, profile: dict | None = None) -> bool:
    # Native composed playback used to blanket-force transcode in auto mode.
    # That was safe but too conservative, especially on healthy Intel/QSV and
    # Intel/VAAPI hosts. Keep an override env, otherwise only force transcode
    # for native runtimes with riskier decode profiles.
    try:
        native_active = bool(player.native_qt_runtime_active())
    except Exception:
        native_active = False
    if not native_active:
        return False

    raw = str(os.getenv("RELAYTV_JELLYFIN_NATIVE_AUTO_TRANSCODE") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False

    vp = profile if isinstance(profile, dict) else {}
    decode_profile = str(vp.get("decode_profile") or "").strip().lower()
    if decode_profile in ("intel_amd64_qsv", "intel_amd64_vaapi", "nvidia_cuda"):
        return False
    if decode_profile in ("software", "arm_safe", "vaapi_generic", "vulkan_generic"):
        return True
    # Unknown native profile stays conservative.
    return True


def _jellyfin_target_max_streaming_bitrate(
    *,
    profile: dict | None = None,
    settings: dict | None = None,
) -> int:
    # Allow an explicit override for deployments that need tighter control.
    try:
        raw = int(float(os.getenv("RELAYTV_JELLYFIN_MAX_STREAMING_BITRATE", "0") or "0"))
        if raw > 0:
            return raw
    except Exception:
        pass

    cap = 0
    vp = profile if isinstance(profile, dict) else {}
    if isinstance(settings, dict):
        try:
            qmode = str(settings.get("quality_mode") or "").strip().lower()
            qcap = str(settings.get("quality_cap") or "").strip()
            if qmode == "manual" and qcap and qcap.isdigit():
                cap = int(qcap)
        except Exception:
            cap = 0
    if cap <= 0:
        try:
            cap = int(vp.get("display_cap_height") or 0)
        except Exception:
            cap = 0

    if cap <= 0:
        return 18_000_000
    if cap <= 360:
        return 2_500_000
    if cap <= 480:
        return 4_000_000
    if cap <= 720:
        return 8_000_000
    if cap <= 1080:
        return 18_000_000
    if cap <= 1440:
        return 28_000_000
    return 35_000_000


def _jellyfin_auto_prefers_transcode(
    *,
    item_detail: dict | None,
    profile: dict | None,
) -> tuple[bool, str]:
    detail = item_detail if isinstance(item_detail, dict) else {}
    vp = profile if isinstance(profile, dict) else {}
    codec = str(detail.get("video_codec") or "").strip().lower()
    try:
        height = int(detail.get("video_height") or 0)
    except Exception:
        height = 0
    try:
        bit_depth = int(detail.get("video_bit_depth") or 0)
    except Exception:
        bit_depth = 0
    try:
        bitrate = int(detail.get("video_bitrate") or 0)
    except Exception:
        bitrate = 0

    decode_profile = str(vp.get("decode_profile") or "").strip().lower()
    av1_allowed = bool(vp.get("av1_allowed"))
    try:
        display_cap_height = int(vp.get("display_cap_height") or 0)
    except Exception:
        display_cap_height = 0

    if codec in ("av1", "av01") and not av1_allowed:
        return True, "av1_not_allowed"
    if display_cap_height > 0 and height > 0 and height > display_cap_height:
        return True, "exceeds_display_cap"
    if decode_profile in ("software", "arm_safe"):
        if codec in ("hevc", "h265", "av1", "vp9") and height >= 1080:
            return True, "software_decode_high_cost"
        if bit_depth > 8 and codec in ("hevc", "h265", "av1"):
            return True, "software_decode_10bit"
        if bitrate > 25_000_000:
            return True, "software_decode_high_bitrate"
    if codec in ("hevc", "h265") and bit_depth > 8 and decode_profile not in (
        "intel_amd64_qsv",
        "intel_amd64_vaapi",
        "nvidia_cuda",
    ):
        return True, "limited_hevc_10bit_support"
    return False, "direct_ok"


def _select_jellyfin_playback_url(
    *,
    item_id: str,
    source_url: str,
    server_url: str,
    api_key: str,
    media_source_id: str = "",
    audio_stream_index: str = "",
    subtitle_stream_index: str = "",
    settings: dict | None = None,
) -> dict[str, str]:
    iid = str(item_id or "").strip()
    src = str(source_url or "").strip()
    base = str(server_url or "").strip()
    tok = str(api_key or "").strip()
    mid = str(media_source_id or "").strip()
    aidx = str(audio_stream_index or "").strip()
    sidx = str(subtitle_stream_index or "").strip()
    mode = _effective_jellyfin_playback_mode(settings)
    if not iid:
        return {"url": src, "mode": "direct", "reason": "no_item_id", "media_source_id": mid}

    detail = {}
    try:
        detail = jellyfin_receiver.get_item_detail(iid)
    except Exception:
        detail = {}
    profile = {}
    try:
        profile = video_profile.get_profile() or {}
    except Exception:
        profile = {}

    prefer_transcode = False
    reason = "direct_mode"
    if mode == "transcode":
        prefer_transcode = True
        reason = "forced_transcode_mode"
    elif mode == "auto":
        if _native_jellyfin_auto_transcode_guard_active(profile=profile):
            prefer_transcode = True
            reason = "native_auto_transcode"
        elif not isinstance(detail, dict) or not detail:
            # Compatibility-first fallback: if detail lookup fails, prefer
            # transcode to avoid repeated direct-play failures on unknown codecs.
            prefer_transcode = True
            reason = "auto_no_detail"
        else:
            prefer_transcode, reason = _jellyfin_auto_prefers_transcode(item_detail=detail, profile=profile)

    if not src:
        src = _build_jellyfin_item_stream_url(
            iid,
            server_url=base,
            api_key=tok,
            media_source_id=mid,
            audio_stream_index=aidx,
            subtitle_stream_index=sidx,
        )

    if not prefer_transcode:
        return {"url": src, "mode": "direct", "reason": reason, "media_source_id": mid}

    try:
        cap_height = int(profile.get("display_cap_height") or 0)
    except Exception:
        cap_height = 0
    target_bitrate = _jellyfin_target_max_streaming_bitrate(profile=profile, settings=settings if isinstance(settings, dict) else None)
    selected = jellyfin_receiver.resolve_playback_url(
        iid,
        prefer_transcode=True,
        media_source_id=mid,
        audio_stream_index=aidx,
        subtitle_stream_index=sidx,
        max_height=(cap_height if cap_height > 0 else None),
        max_streaming_bitrate=target_bitrate,
    )
    t_url = str((selected or {}).get("url") or "").strip()
    t_method = str((selected or {}).get("method") or "").strip()
    out_mid = _first_nonempty_str([str((selected or {}).get("media_source_id") or "").strip(), mid])
    if not t_url:
        t_url = _build_jellyfin_item_transcode_url(
            iid,
            server_url=base,
            api_key=tok,
            media_source_id=out_mid,
            audio_stream_index=aidx,
            subtitle_stream_index=sidx,
            max_height=(cap_height if cap_height > 0 else None),
            max_streaming_bitrate=target_bitrate,
        )
        t_method = "fallback_master"
    if t_url:
        return {
            "url": t_url,
            "mode": "transcode",
            "reason": reason if reason != "forced_transcode_mode" else "forced_transcode_mode",
            "media_source_id": out_mid,
            "method": t_method,
        }
    return {"url": src, "mode": "direct", "reason": "transcode_unavailable", "media_source_id": mid}


def _extract_jellyfin_audio_stream_index(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("AudioStreamIndex", "audioStreamIndex", "audio_stream_index"):
        if key in payload:
            try:
                return str(int(payload.get(key)))
            except Exception:
                continue
    return ""


def _extract_jellyfin_subtitle_stream_index(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("SubtitleStreamIndex", "subtitleStreamIndex", "subtitle_stream_index"):
        if key in payload:
            try:
                return str(int(payload.get(key)))
            except Exception:
                continue
    return ""


def _apply_jellyfin_stream_params(url: str, *, audio_stream_index: str = "", subtitle_stream_index: str = "") -> str:
    u = str(url or "").strip()
    if not u:
        return ""
    if audio_stream_index == "" and subtitle_stream_index == "":
        return u
    try:
        p = urlsplit(u)
        q = dict(parse_qsl(p.query, keep_blank_values=True))
        # Normalize key case by endpoint type:
        #  - direct stream endpoints use lower camel-case keys
        #  - master transcode endpoints commonly use PascalCase keys
        path = str(p.path or "").strip().lower()
        is_master = path.endswith("/master.m3u8")
        audio_key = "AudioStreamIndex" if is_master else "audioStreamIndex"
        sub_key = "SubtitleStreamIndex" if is_master else "subtitleStreamIndex"
        for k in ("audioStreamIndex", "AudioStreamIndex", "audiostreamindex"):
            q.pop(k, None)
        for k in ("subtitleStreamIndex", "SubtitleStreamIndex", "subtitlestreamindex"):
            q.pop(k, None)
        if audio_stream_index != "":
            q[audio_key] = str(audio_stream_index)
        if subtitle_stream_index != "":
            q[sub_key] = str(subtitle_stream_index)
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))
    except Exception:
        return u


def _apply_jellyfin_media_source_param(url: str, *, media_source_id: str = "") -> str:
    u = str(url or "").strip()
    mid = str(media_source_id or "").strip()
    if not u or not mid:
        return u
    try:
        p = urlsplit(u)
        q = dict(parse_qsl(p.query, keep_blank_values=True))
        if "mediaSourceId" not in q and "MediaSourceId" not in q:
            q["mediaSourceId"] = mid
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))
    except Exception:
        return u


def _normalize_lang_pref(raw: str) -> str:
    text = str(raw or "").strip().lower().replace("_", "-")
    if "," in text:
        text = text.split(",", 1)[0].strip()
    return text


def _language_aliases(raw: str) -> set[str]:
    base = _normalize_lang_pref(raw)
    if not base:
        return set()
    aliases: set[str] = {base}
    # Normalize common locale stems.
    if "-" in base:
        aliases.add(base.split("-", 1)[0])
    # Map common ISO-639-2 and alternate spellings to ISO-639-1 stems.
    to_stem = {
        "eng": "en",
        "por": "pt",
        "spa": "es",
        "jpn": "ja",
        "deu": "de",
        "ger": "de",
        "fra": "fr",
        "fre": "fr",
        "ita": "it",
        "rus": "ru",
        "kor": "ko",
        "zho": "zh",
        "chi": "zh",
        "ara": "ar",
        "ces": "cs",
        "cze": "cs",
        "nld": "nl",
        "dut": "nl",
        "pol": "pl",
        "tur": "tr",
        "hun": "hu",
        "dan": "da",
        "fin": "fi",
        "ron": "ro",
        "rum": "ro",
        "swe": "sv",
        "ell": "el",
        "gre": "el",
        "nob": "nb",
    }
    stem = to_stem.get(base, "")
    if stem:
        aliases.add(stem)
    short = base.split("-", 1)[0]
    if short in to_stem:
        aliases.add(to_stem[short])
    # Regional variants used by stream metadata.
    if "pt" in aliases:
        aliases.update({"pt-br", "pt-pt"})
    if "en" in aliases:
        aliases.update({"en-us", "en-gb"})
    if "es" in aliases:
        aliases.update({"es-419", "es-es"})
    return {a for a in aliases if a}


def _language_matches(pref: str, candidate: str) -> bool:
    p_alias = _language_aliases(pref)
    c_alias = _language_aliases(candidate)
    if not p_alias or not c_alias:
        return False
    if p_alias.intersection(c_alias):
        return True
    return False


def _preferred_jellyfin_stream_indices(item_id: str) -> tuple[str, str]:
    iid = str(item_id or "").strip()
    if not iid:
        return "", ""
    settings = state.get_settings() if hasattr(state, "get_settings") else {}
    audio_pref = _normalize_lang_pref(str(settings.get("jellyfin_audio_lang") or ""))
    sub_pref = _normalize_lang_pref(str(settings.get("jellyfin_sub_lang") or ""))
    if not audio_pref and not sub_pref:
        return "", ""
    sub_off = sub_pref in {"off", "none", "disabled", "no", "false", "0"}
    try:
        detail = jellyfin_receiver.get_item_detail(iid)
    except Exception:
        detail = {}
    audio_streams = detail.get("audio_streams") if isinstance(detail, dict) else []
    subtitle_streams = detail.get("subtitle_streams") if isinstance(detail, dict) else []
    audio_idx = ""
    sub_idx = "-1" if sub_off else ""
    if audio_pref and isinstance(audio_streams, list):
        for stream in audio_streams:
            if not isinstance(stream, dict):
                continue
            if _language_matches(audio_pref, str(stream.get("language") or "")):
                try:
                    audio_idx = str(int(stream.get("index")))
                except Exception:
                    audio_idx = ""
                if audio_idx:
                    break
    if sub_pref and (not sub_off) and isinstance(subtitle_streams, list):
        for stream in subtitle_streams:
            if not isinstance(stream, dict):
                continue
            if _language_matches(sub_pref, str(stream.get("language") or "")):
                try:
                    sub_idx = str(int(stream.get("index")))
                except Exception:
                    sub_idx = ""
                if sub_idx:
                    break
    return audio_idx, sub_idx


def _retarget_jellyfin_queue_stream_preferences() -> int:
    """Best-effort: rewrite queued Jellyfin URLs using current language prefs."""
    with state.QUEUE_LOCK:
        snapshot = list(state.QUEUE)
    if not snapshot:
        return 0

    changed = 0
    updated_queue: list[object] = list(snapshot)
    for i, entry in enumerate(snapshot):
        if not isinstance(entry, dict):
            continue
        raw_url = str(entry.get("url") or "").strip()
        if not raw_url:
            continue
        provider = str(entry.get("provider") or "").strip().lower()
        item_id = str(entry.get("jellyfin_item_id") or "").strip()
        if not item_id:
            item_id = _extract_jellyfin_item_id_from_url_raw(raw_url)
        if provider != "jellyfin" and not item_id:
            continue
        pref_audio_idx, pref_sub_idx = _preferred_jellyfin_stream_indices(item_id) if item_id else ("", "")
        if pref_audio_idx == "" and pref_sub_idx == "":
            continue
        next_url = _apply_jellyfin_stream_params(
            raw_url,
            audio_stream_index=pref_audio_idx,
            subtitle_stream_index=pref_sub_idx,
        )
        media_source_id = _first_nonempty_str(
            [
                str(entry.get("jellyfin_media_source_id") or "").strip(),
                _extract_jellyfin_media_source_id_from_url(raw_url),
            ]
        )
        next_url = _apply_jellyfin_media_source_param(next_url, media_source_id=media_source_id)
        if next_url and next_url != raw_url:
            out_entry = dict(entry)
            out_entry["url"] = next_url
            if item_id:
                out_entry["jellyfin_item_id"] = item_id
            updated_queue[i] = out_entry
            changed += 1

    if changed <= 0:
        return 0

    with state.QUEUE_LOCK:
        state.QUEUE[:] = updated_queue
    try:
        state.persist_queue()
    except Exception:
        pass
    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    return changed


def _extract_api_key_from_url(url: str) -> str:
    try:
        q = dict(parse_qsl(urlsplit(str(url or "").strip()).query, keep_blank_values=True))
        return str(q.get("api_key") or q.get("ApiKey") or "").strip()
    except Exception:
        return ""


def _extract_jellyfin_media_source_id_from_url(url: str) -> str:
    try:
        q = dict(parse_qsl(urlsplit(str(url or "").strip()).query, keep_blank_values=True))
        return _first_nonempty_str(
            [
                q.get("mediaSourceId"),
                q.get("MediaSourceId"),
                q.get("mediasourceid"),
            ]
        )
    except Exception:
        return ""


def _first_playable_jellyfin_episode(payload: dict | None) -> dict[str, object]:
    episodes = payload.get("episodes") if isinstance(payload, dict) and isinstance(payload.get("episodes"), list) else []
    for episode in episodes:
        if not isinstance(episode, dict):
            continue
        episode_id = str(episode.get("item_id") or "").strip()
        if not episode_id:
            continue
        episode_type = str(episode.get("type") or "").strip().lower()
        if episode_type in ("", "episode"):
            return episode
    return {}


def _resolve_jellyfin_playable_item(item_id: str, *, media_source_id: str = "") -> dict[str, object]:
    iid = str(item_id or "").strip()
    if not iid:
        return {"item_id": "", "detail": {}, "media_source_id": ""}

    try:
        detail = jellyfin_receiver.get_item_detail(iid)
    except Exception:
        detail = {}

    item_type = str(detail.get("type") if isinstance(detail, dict) else "").strip().lower()
    if item_type not in ("series", "season"):
        return {
            "item_id": iid,
            "detail": detail if isinstance(detail, dict) else {},
            "media_source_id": _first_nonempty_str([
                str(media_source_id or "").strip(),
                detail.get("media_source_id") if isinstance(detail, dict) else "",
            ]),
        }

    series_id = ""
    season_id = ""
    season_number = None
    if item_type == "series":
        series_id = iid
    else:
        season_id = iid
        series_id = _first_nonempty_str([
            detail.get("series_id") if isinstance(detail, dict) else "",
            detail.get("SeriesId") if isinstance(detail, dict) else "",
        ])
        try:
            raw_season = detail.get("season_number") if isinstance(detail, dict) else None
            season_number = int(raw_season) if raw_season is not None else None
        except Exception:
            season_number = None

    if not series_id:
        raise HTTPException(status_code=404, detail=f"jellyfin {item_type} is not directly playable")

    episodes_payload = jellyfin_receiver.list_series_episodes(
        series_id,
        season_id=season_id,
        season_number=season_number,
    )
    episode = _first_playable_jellyfin_episode(episodes_payload)
    resolved_item_id = str((episode.get("item_id") if isinstance(episode, dict) else "") or "").strip()
    if not resolved_item_id:
        raise HTTPException(status_code=404, detail=f"no playable episode available for jellyfin {item_type}")

    resolved_detail = episode if isinstance(episode, dict) else {}
    if resolved_item_id != iid:
        try:
            fetched = jellyfin_receiver.get_item_detail(resolved_item_id)
        except Exception:
            fetched = {}
        if isinstance(fetched, dict) and fetched:
            resolved_detail = fetched

    return {
        "item_id": resolved_item_id,
        "detail": resolved_detail if isinstance(resolved_detail, dict) else {},
        "media_source_id": _first_nonempty_str([
            resolved_detail.get("media_source_id") if isinstance(resolved_detail, dict) else "",
            episode.get("media_source_id") if isinstance(episode, dict) else "",
            (str(media_source_id or "").strip() if resolved_item_id == iid else ""),
        ]),
    }


def _extract_jellyfin_audio_stream_index_from_url(url: str) -> str:
    try:
        q = dict(parse_qsl(urlsplit(str(url or "").strip()).query, keep_blank_values=True))
        for key in ("audioStreamIndex", "AudioStreamIndex", "audiostreamindex"):
            if key in q:
                return str(int(str(q.get(key) or "").strip()))
    except Exception:
        return ""
    return ""


def _extract_jellyfin_subtitle_stream_index_from_url(url: str) -> str:
    try:
        q = dict(parse_qsl(urlsplit(str(url or "").strip()).query, keep_blank_values=True))
        for key in ("subtitleStreamIndex", "SubtitleStreamIndex", "subtitlestreamindex"):
            if key in q:
                return str(int(str(q.get(key) or "").strip()))
    except Exception:
        return ""
    return ""


def _extract_jellyfin_item_id_from_url_raw(raw_url: str | None) -> str:
    u = str(raw_url or "").strip()
    if not u:
        return ""
    try:
        parts = urlsplit(u)
        segs = [seg for seg in (parts.path or "").split("/") if seg]
        for idx, seg in enumerate(segs):
            low = seg.lower()
            if low in ("videos", "items") and idx + 1 < len(segs):
                return str(segs[idx + 1] or "").strip()
    except Exception:
        return ""
    return ""


def _jellyfin_url_origin(url: str) -> str:
    try:
        p = urlsplit(str(url or "").strip())
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""
    return ""


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


def _smart_item_from_url(url: str, *, start_pos: float | None = None, lightweight: bool = False) -> dict:
    """
    Build a playback item for smart/jellyfin paths.
    If the URL looks like Jellyfin media, enrich title/thumbnail/resume from Jellyfin APIs.
    """
    shared = str(url or "").strip()
    item_id = jellyfin_receiver.extract_item_id_from_url(shared)
    st = jellyfin_receiver.status()
    if item_id and (st.get("server_url") or _looks_like_jellyfin_media_url(shared)):
        origin = _jellyfin_url_origin(shared)
        server_url = origin or str(st.get("server_url") or "")
        link_api_key = _extract_api_key_from_url(shared)
        token = link_api_key or _jellyfin_access_token()
        pref_audio_idx, pref_sub_idx = _preferred_jellyfin_stream_indices(item_id)
        normalized_url = _normalize_jellyfin_source_url(shared, server_url=server_url, api_key=token)
        normalized_url = _apply_jellyfin_stream_params(
            normalized_url,
            audio_stream_index=pref_audio_idx,
            subtitle_stream_index=pref_sub_idx,
        )
        # Share links commonly use /Items/<id>/Download - convert to stream endpoint for consistency.
        low_path = (urlsplit(shared).path or "").lower()
        if "/items/" in low_path and "/download" in low_path:
            normalized_url = _build_jellyfin_item_stream_url(
                item_id,
                server_url=server_url,
                api_key=token,
                audio_stream_index=pref_audio_idx,
                subtitle_stream_index=pref_sub_idx,
            )
        media_source_id = _extract_jellyfin_media_source_id_from_url(normalized_url)
        try:
            settings_snapshot = state.get_settings()
        except Exception:
            settings_snapshot = {}
        selected = _select_jellyfin_playback_url(
            item_id=item_id,
            source_url=normalized_url,
            server_url=server_url,
            api_key=token,
            media_source_id=media_source_id,
            audio_stream_index=pref_audio_idx,
            subtitle_stream_index=pref_sub_idx,
            settings=settings_snapshot,
        )
        normalized_url = _normalize_jellyfin_source_url(
            str(selected.get("url") or normalized_url),
            server_url=server_url,
            api_key=token,
        )
        media_source_id = _first_nonempty_str(
            [
                str(selected.get("media_source_id") or "").strip(),
                _extract_jellyfin_media_source_id_from_url(normalized_url),
                media_source_id,
            ]
        )
        item: dict[str, object] = {
            "url": normalized_url,
            "provider": "jellyfin",
            "jellyfin_item_id": item_id,
            **({"jellyfin_media_source_id": media_source_id} if media_source_id else {}),
            "jellyfin_stream_mode": str(selected.get("mode") or "direct"),
            "jellyfin_stream_reason": str(selected.get("reason") or ""),
        }
        meta = jellyfin_receiver.get_item_metadata(item_id, token_override=token, server_url_override=server_url)
        if isinstance(meta, dict):
            title = str(meta.get("title") or "").strip()
            channel = str(meta.get("channel") or "").strip()
            thumb = str(meta.get("thumbnail") or "").strip()
            if title:
                item["title"] = title
            if channel:
                item["channel"] = channel
            if thumb:
                item["thumbnail"] = thumb
            if start_pos is None:
                try:
                    rp = meta.get("resume_pos")
                    if rp is not None:
                        item["resume_pos"] = float(rp)
                except Exception:
                    pass
        if start_pos is not None:
            try:
                item["resume_pos"] = float(start_pos)
            except Exception:
                pass
        return attach_local_thumbnail(item)
    # Non-Jellyfin path: existing resolver behavior.
    try:
        return resolver.make_item(shared, lightweight=lightweight)
    except TypeError:
        # Compatibility for tests/patches that mock make_item(url) without kwargs.
        return resolver.make_item(shared)


def _is_generic_playback_title(title: object, url: object) -> bool:
    t = str(title or "").strip()
    if not t:
        return True
    low = t.lower()
    if low in {"stream", "download", "video", "playback", "master", "master.m3u8", "main", "main.m3u8"}:
        return True
    u = str(url or "").strip()
    if u and t == u:
        return True
    return False


def _merge_jellyfin_playback_metadata(now: dict, enriched: dict) -> dict:
    out = dict(now)
    provider = str(enriched.get("provider") or "").strip().lower()
    if provider == "jellyfin" and str(out.get("provider") or "").strip().lower() != "jellyfin":
        out["provider"] = "jellyfin"

    if enriched.get("title") and _is_generic_playback_title(out.get("title"), out.get("url")):
        out["title"] = enriched.get("title")

    if enriched.get("channel") and not out.get("channel"):
        out["channel"] = enriched.get("channel")
    if enriched.get("thumbnail") and not out.get("thumbnail"):
        out["thumbnail"] = enriched.get("thumbnail")
    if enriched.get("thumbnail_local") and not out.get("thumbnail_local"):
        out["thumbnail_local"] = enriched.get("thumbnail_local")

    # Keep canonical Jellyfin identifiers from enriched metadata when present.
    if enriched.get("jellyfin_item_id"):
        out["jellyfin_item_id"] = enriched.get("jellyfin_item_id")
    if enriched.get("jellyfin_media_source_id"):
        out["jellyfin_media_source_id"] = enriched.get("jellyfin_media_source_id")
    return out


def _jellyfin_enrich_now_stream_metadata(
    now: dict,
    *,
    detail: dict | None = None,
    audio_stream_index: str = "",
    subtitle_stream_index: str = "",
) -> dict:
    out = dict(now or {})
    meta = detail if isinstance(detail, dict) else {}
    audio_streams = meta.get("audio_streams") if isinstance(meta.get("audio_streams"), list) else []
    subtitle_streams = meta.get("subtitle_streams") if isinstance(meta.get("subtitle_streams"), list) else []
    if audio_streams:
        out["audio_streams"] = audio_streams
    if subtitle_streams:
        out["subtitle_streams"] = subtitle_streams

    selected_audio = str(audio_stream_index or out.get("jellyfin_audio_stream_index") or "").strip()
    if not selected_audio:
        selected_audio = _extract_jellyfin_audio_stream_index_from_url(str(out.get("url") or ""))
    selected_sub = str(subtitle_stream_index or out.get("jellyfin_subtitle_stream_index") or "").strip()
    if not selected_sub:
        selected_sub = _extract_jellyfin_subtitle_stream_index_from_url(str(out.get("url") or ""))

    selected_audio_lang = ""
    if isinstance(audio_streams, list):
        target_idx = None
        try:
            target_idx = int(selected_audio) if selected_audio != "" else None
        except Exception:
            target_idx = None
        if target_idx is not None:
            for row in audio_streams:
                if not isinstance(row, dict):
                    continue
                try:
                    if int(row.get("index")) == target_idx:
                        selected_audio_lang = str(row.get("language") or "").strip()
                        break
                except Exception:
                    continue
        if not selected_audio_lang:
            for row in audio_streams:
                if isinstance(row, dict) and bool(row.get("is_default")) and str(row.get("language") or "").strip():
                    selected_audio_lang = str(row.get("language") or "").strip()
                    break

    selected_sub_lang = "off" if selected_sub == "-1" else ""
    if isinstance(subtitle_streams, list) and selected_sub != "-1":
        target_sub_idx = None
        try:
            target_sub_idx = int(selected_sub) if selected_sub != "" else None
        except Exception:
            target_sub_idx = None
        if target_sub_idx is not None:
            for row in subtitle_streams:
                if not isinstance(row, dict):
                    continue
                try:
                    if int(row.get("index")) == target_sub_idx:
                        selected_sub_lang = str(row.get("language") or "").strip()
                        break
                except Exception:
                    continue
        if not selected_sub_lang:
            for row in subtitle_streams:
                if isinstance(row, dict) and bool(row.get("is_default")) and str(row.get("language") or "").strip():
                    selected_sub_lang = str(row.get("language") or "").strip()
                    break

    if selected_audio != "":
        out["jellyfin_audio_stream_index"] = selected_audio
    if selected_sub != "":
        out["jellyfin_subtitle_stream_index"] = selected_sub

    out["audio_language"] = selected_audio_lang or str(meta.get("audio_language") or out.get("audio_language") or "").strip()
    out["subtitle_language"] = selected_sub_lang or str(meta.get("subtitle_language") or out.get("subtitle_language") or "").strip()
    out["jellyfin_audio_language"] = str(out.get("audio_language") or "").strip()
    out["jellyfin_subtitle_language"] = str(out.get("subtitle_language") or "").strip()
    return out


def _jellyfin_track_type_is_subtitle(raw_type: object) -> bool:
    return str(raw_type or "").strip().lower() in {"sub", "subtitle", "subtitles"}


def _jellyfin_try_set_mpv_audio_track(
    *,
    language: str = "",
    display: str = "",
    preferred_stream_index: int | None = None,
) -> bool:
    target_lang = _normalize_lang_pref(str(language or ""))
    target_display = str(display or "").strip().lower()
    target_display_tokens = [tok for tok in re.split(r"[^a-z0-9]+", target_display) if len(tok) >= 3]
    try:
        track_list = player.mpv_get("track-list")
    except Exception:
        track_list = None
    if not isinstance(track_list, list):
        return False

    candidates: list[tuple[int, int]] = []
    for idx, row in enumerate(track_list):
        if not isinstance(row, dict):
            continue
        if str(row.get("type") or "").strip().lower() != "audio":
            continue
        try:
            tid = int(row.get("id"))
        except Exception:
            continue
        if tid <= 0:
            continue
        src_id = None
        ff_index = None
        try:
            src_id = int(row.get("src-id"))
        except Exception:
            src_id = None
        try:
            ff_index = int(row.get("ff-index"))
        except Exception:
            ff_index = None
        lang = _normalize_lang_pref(str(row.get("lang") or row.get("language") or ""))
        title = str(row.get("title") or row.get("name") or "").strip().lower()
        score = 0
        if preferred_stream_index is not None:
            if ff_index is not None and ff_index == preferred_stream_index:
                score += 20
            if src_id is not None and (src_id - 1) == preferred_stream_index:
                score += 18
        if target_lang and _language_matches(target_lang, lang):
            score += 6
        if target_display and title:
            if target_display in title or title in target_display:
                score += 4
            elif target_display_tokens:
                token_hits = sum(1 for tok in target_display_tokens if tok in title)
                score += min(3, token_hits)
        if score <= 0:
            continue
        # Stable tie-break by track order.
        candidates.append((score * 1000 - idx, tid))
    if not candidates:
        return False
    candidates.sort(reverse=True)
    selected_tid = int(candidates[0][1])

    try:
        player.mpv_set("aid", selected_tid)
    except Exception:
        return False

    # Confirm selection if possible.
    try:
        updated = player.mpv_get("track-list")
    except Exception:
        updated = None
    if isinstance(updated, list):
        for row in updated:
            if not isinstance(row, dict):
                continue
            if str(row.get("type") or "").strip().lower() != "audio":
                continue
            if not bool(row.get("selected")):
                continue
            src_id = None
            ff_index = None
            try:
                src_id = int(row.get("src-id"))
            except Exception:
                src_id = None
            try:
                ff_index = int(row.get("ff-index"))
            except Exception:
                ff_index = None
            if preferred_stream_index is not None:
                if ff_index is not None and ff_index == preferred_stream_index:
                    return True
                if src_id is not None and (src_id - 1) == preferred_stream_index:
                    return True
            lang = _normalize_lang_pref(str(row.get("lang") or row.get("language") or ""))
            title = str(row.get("title") or row.get("name") or "").strip().lower()
            if target_lang and _language_matches(target_lang, lang):
                return True
            if target_display and target_display in title:
                return True
    return False


def _jellyfin_try_set_mpv_subtitle_track(
    *,
    language: str = "",
    display: str = "",
    preferred_stream_index: int | None = None,
    off: bool = False,
) -> bool:
    if off:
        try:
            player.mpv_set("sid", "no")
            try:
                player.mpv_set("sub-visibility", False)
            except Exception:
                pass
            return True
        except Exception:
            return False

    target_lang = _normalize_lang_pref(str(language or ""))
    target_display = str(display or "").strip().lower()
    target_display_tokens = [tok for tok in re.split(r"[^a-z0-9]+", target_display) if len(tok) >= 3]
    try:
        track_list = player.mpv_get("track-list")
    except Exception:
        track_list = None
    if not isinstance(track_list, list):
        return False

    candidates: list[tuple[int, int]] = []
    for idx, row in enumerate(track_list):
        if not isinstance(row, dict):
            continue
        if not _jellyfin_track_type_is_subtitle(row.get("type")):
            continue
        try:
            tid = int(row.get("id"))
        except Exception:
            continue
        if tid <= 0:
            continue
        src_id = None
        ff_index = None
        try:
            src_id = int(row.get("src-id"))
        except Exception:
            src_id = None
        try:
            ff_index = int(row.get("ff-index"))
        except Exception:
            ff_index = None
        lang = _normalize_lang_pref(str(row.get("lang") or row.get("language") or ""))
        title = str(row.get("title") or row.get("name") or "").strip().lower()
        score = 0
        if preferred_stream_index is not None:
            if ff_index is not None and ff_index == preferred_stream_index:
                score += 20
            if src_id is not None and (src_id - 1) == preferred_stream_index:
                score += 18
        if target_lang and _language_matches(target_lang, lang):
            score += 6
        if target_display and title:
            if target_display in title or title in target_display:
                score += 4
            elif target_display_tokens:
                token_hits = sum(1 for tok in target_display_tokens if tok in title)
                score += min(3, token_hits)
        if score <= 0:
            continue
        candidates.append((score * 1000 - idx, tid))
    if not candidates:
        return False
    candidates.sort(reverse=True)
    selected_tid = int(candidates[0][1])

    try:
        player.mpv_set("sid", selected_tid)
        try:
            player.mpv_set("sub-visibility", True)
        except Exception:
            pass
    except Exception:
        return False

    try:
        updated = player.mpv_get("track-list")
    except Exception:
        updated = None
    if isinstance(updated, list):
        for row in updated:
            if not isinstance(row, dict):
                continue
            if not _jellyfin_track_type_is_subtitle(row.get("type")):
                continue
            if not bool(row.get("selected")):
                continue
            src_id = None
            ff_index = None
            try:
                src_id = int(row.get("src-id"))
            except Exception:
                src_id = None
            try:
                ff_index = int(row.get("ff-index"))
            except Exception:
                ff_index = None
            if preferred_stream_index is not None:
                if ff_index is not None and ff_index == preferred_stream_index:
                    return True
                if src_id is not None and (src_id - 1) == preferred_stream_index:
                    return True
            lang = _normalize_lang_pref(str(row.get("lang") or row.get("language") or ""))
            title = str(row.get("title") or row.get("name") or "").strip().lower()
            if target_lang and _language_matches(target_lang, lang):
                return True
            if target_display and target_display in title:
                return True
    return False


def _jellyfin_runtime_selected_audio_stream(audio_streams: list[dict[str, object]]) -> tuple[int | None, str]:
    """Resolve selected Jellyfin audio stream index from mpv runtime track data."""
    try:
        track_list = player.mpv_get("track-list")
    except Exception:
        track_list = None
    if not isinstance(track_list, list):
        return None, ""

    selected: dict[str, object] | None = None
    for row in track_list:
        if not isinstance(row, dict):
            continue
        if str(row.get("type") or "").strip().lower() != "audio":
            continue
        if bool(row.get("selected")):
            selected = row
            break
    if not isinstance(selected, dict):
        return None, ""

    selected_lang = _normalize_lang_pref(str(selected.get("lang") or selected.get("language") or ""))
    selected_title = str(selected.get("title") or selected.get("name") or "").strip().lower()
    selected_title_tokens = [tok for tok in re.split(r"[^a-z0-9]+", selected_title) if len(tok) >= 3]

    try:
        ff_index = int(selected.get("ff-index"))
    except Exception:
        ff_index = None
    try:
        src_id = int(selected.get("src-id"))
    except Exception:
        src_id = None

    if ff_index is not None:
        for row in audio_streams:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("index")) == ff_index:
                    return ff_index, selected_lang
            except Exception:
                continue
    if src_id is not None:
        candidate = int(src_id) - 1
        for row in audio_streams:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("index")) == candidate:
                    return candidate, selected_lang
            except Exception:
                continue

    best_idx: int | None = None
    best_score = 0
    for row in audio_streams:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("index"))
        except Exception:
            continue
        score = 0
        row_lang = _normalize_lang_pref(str(row.get("language") or ""))
        if selected_lang and _language_matches(selected_lang, row_lang):
            score += 3
        row_display = str(row.get("display") or "").strip().lower()
        if selected_title and row_display:
            if selected_title in row_display or row_display in selected_title:
                score += 2
            elif selected_title_tokens:
                token_hits = sum(1 for tok in selected_title_tokens if tok in row_display)
                score += min(2, token_hits)
        if score > best_score:
            best_score = score
            best_idx = idx
    return best_idx, selected_lang


def _jellyfin_runtime_selected_subtitle_stream(subtitle_streams: list[dict[str, object]]) -> tuple[int | None, str, bool]:
    """Resolve selected Jellyfin subtitle stream index from mpv runtime track data."""
    try:
        props = player.mpv_get_many(["track-list", "sid", "sub-visibility"])
    except Exception:
        props = {}
    track_list = props.get("track-list") if isinstance(props, dict) else None
    sid_raw = props.get("sid") if isinstance(props, dict) else None
    sub_visible = props.get("sub-visibility") if isinstance(props, dict) else None
    sid_text = str(sid_raw or "").strip().lower()
    sid_off = sid_text in {"no", "0", "-1", "false"}
    if sub_visible is False or sid_off:
        return None, "off", True
    if not isinstance(track_list, list):
        return None, "", False

    selected: dict[str, object] | None = None
    for row in track_list:
        if not isinstance(row, dict):
            continue
        if not _jellyfin_track_type_is_subtitle(row.get("type")):
            continue
        if bool(row.get("selected")):
            selected = row
            break
    if not isinstance(selected, dict):
        return None, "", False

    selected_lang = _normalize_lang_pref(str(selected.get("lang") or selected.get("language") or ""))
    selected_title = str(selected.get("title") or selected.get("name") or "").strip().lower()
    selected_title_tokens = [tok for tok in re.split(r"[^a-z0-9]+", selected_title) if len(tok) >= 3]

    try:
        ff_index = int(selected.get("ff-index"))
    except Exception:
        ff_index = None
    try:
        src_id = int(selected.get("src-id"))
    except Exception:
        src_id = None

    if ff_index is not None:
        for row in subtitle_streams:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("index")) == ff_index:
                    return ff_index, selected_lang, False
            except Exception:
                continue
    if src_id is not None:
        candidate = int(src_id) - 1
        for row in subtitle_streams:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("index")) == candidate:
                    return candidate, selected_lang, False
            except Exception:
                continue

    best_idx: int | None = None
    best_score = 0
    for row in subtitle_streams:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("index"))
        except Exception:
            continue
        score = 0
        row_lang = _normalize_lang_pref(str(row.get("language") or ""))
        if selected_lang and _language_matches(selected_lang, row_lang):
            score += 3
        row_display = str(row.get("display") or "").strip().lower()
        if selected_title and row_display:
            if selected_title in row_display or row_display in selected_title:
                score += 2
            elif selected_title_tokens:
                token_hits = sum(1 for tok in selected_title_tokens if tok in row_display)
                score += min(2, token_hits)
        if score > best_score:
            best_score = score
            best_idx = idx
    return best_idx, selected_lang, False
    return None, selected_lang


def _jellyfin_emit_progress_hint() -> None:
    """Trigger an immediate best-effort progress push without blocking request paths."""
    def _run() -> None:
        try:
            jellyfin_receiver.send_progress_once()
        except Exception:
            pass

    try:
        threading.Thread(target=_run, daemon=True, name="relaytv-jellyfin-progress-hint").start()
    except Exception:
        pass


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


def _jellyfin_snap_position_ticks(pos_ticks: int, run_ticks: int | None = None) -> int:
    pos = max(0, int(pos_ticks or 0))
    try:
        run = int(run_ticks) if run_ticks is not None else None
    except Exception:
        run = None
    if run is None or run <= 0:
        return pos
    if pos >= run:
        return run
    if pos >= int(run * _jellyfin_complete_ratio()):
        return run
    remain_sec = _jellyfin_complete_remaining_sec()
    if remain_sec > 0.0:
        remain_ticks = int(remain_sec * 10_000_000)
        if (run - pos) <= max(0, remain_ticks):
            return run
    return pos


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


def _jellyfin_stopped_snapshot_from_now(
    now: dict | None,
    position_sec: float | None = None,
    duration_sec: float | None = None,
) -> dict | None:
    if not isinstance(now, dict):
        return None
    item_id = str(now.get("jellyfin_item_id") or "").strip()
    if not item_id:
        return None
    pos_f = None
    if position_sec is not None:
        try:
            pos_f = float(position_sec)
        except Exception:
            pos_f = None
    if pos_f is None:
        try:
            rp = now.get("resume_pos")
            pos_f = float(rp) if rp is not None else None
        except Exception:
            pos_f = None
    if pos_f is None:
        try:
            pos_f = float(getattr(state, "SESSION_POSITION", 0.0) or 0.0)
        except Exception:
            pos_f = 0.0
    dur_f = None
    if duration_sec is not None:
        try:
            dur_f = float(duration_sec)
        except Exception:
            dur_f = None
    if dur_f is None:
        try:
            d = now.get("duration")
            dur_f = float(d) if d is not None else None
        except Exception:
            dur_f = None
    payload = {
        "ItemId": item_id,
        "IsPaused": False,
    }
    pos_ticks = max(0, int((pos_f or 0.0) * 10_000_000))
    if dur_f is not None and dur_f >= 0:
        run_ticks = int(dur_f * 10_000_000)
        payload["RunTimeTicks"] = run_ticks
        pos_ticks = _jellyfin_snap_position_ticks(pos_ticks, run_ticks)
        played_pct = _jellyfin_played_percentage(pos_ticks, run_ticks)
        if played_pct is not None:
            payload["PlayedPercentage"] = played_pct
    payload["PositionTicks"] = pos_ticks
    play_session_id = str(now.get("jellyfin_play_session_id") or "").strip()
    if play_session_id:
        payload["PlaySessionId"] = play_session_id
    media_source_id = _first_nonempty_str(
        [
            now.get("jellyfin_media_source_id"),
            _extract_jellyfin_media_source_id_from_url(str(now.get("url") or "")),
        ]
    )
    if media_source_id:
        payload["MediaSourceId"] = media_source_id
    return payload


def _jellyfin_stopped_snapshot(position_sec: float | None = None, duration_sec: float | None = None) -> dict | None:
    now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    return _jellyfin_stopped_snapshot_from_now(now, position_sec, duration_sec)


def _jellyfin_emit_stopped_payload(payload: dict | None) -> None:
    if not isinstance(payload, dict) or not payload:
        return

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
        threading.Thread(target=_run, daemon=True, name="relaytv-jellyfin-stopped-hint").start()
    except Exception:
        pass


def _jellyfin_emit_stopped_hint(position_sec: float | None = None, duration_sec: float | None = None) -> None:
    try:
        player.remember_recent_jellyfin_stop(state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None)
    except Exception:
        pass
    _jellyfin_emit_stopped_payload(_jellyfin_stopped_snapshot(position_sec, duration_sec))


def _can_preserve_closed_session() -> bool:
    """Return True only when user stop/close should keep a resumable item."""
    try:
        if bool(getattr(player, "native_qt_playback_explicitly_ended", lambda: False)()):
            return False
    except Exception:
        pass
    try:
        return bool(player.is_playing()) and isinstance(state.NOW_PLAYING, dict)
    except Exception:
        return False


def _idle_dashboard_enabled_for_player() -> bool:
    try:
        return bool(getattr(player, "_idle_dashboard_enabled", lambda: True)())
    except Exception:
        return True


def _jellyfin_progress_snapshot() -> dict | None:
    now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    if not now:
        return None
    item_id = str(now.get("jellyfin_item_id") or "").strip()
    if not item_id:
        return None
    is_playing = bool(player.is_playing())
    props = player.mpv_get_many(["pause", "time-pos", "duration", "mute", "volume"]) if is_playing else {}

    pos = props.get("time-pos") if isinstance(props, dict) else None
    dur = props.get("duration") if isinstance(props, dict) else None
    muted = props.get("mute") if isinstance(props, dict) else None
    volume = props.get("volume") if isinstance(props, dict) else None

    try:
        if pos is not None:
            pos_f = float(pos)
        elif now.get("resume_pos") is not None:
            pos_f = float(now.get("resume_pos"))
        else:
            pos_f = float(state.SESSION_POSITION or 0.0)
    except Exception:
        pos_f = 0.0
    try:
        if dur is not None:
            dur_f = float(dur)
        elif now.get("duration") is not None:
            dur_f = float(now.get("duration"))
        else:
            dur_f = None
    except Exception:
        dur_f = None

    pos_ticks = max(0, int(pos_f * 10_000_000))
    payload = {
        "ItemId": item_id,
        "IsPaused": bool(props.get("pause")) if is_playing and isinstance(props, dict) else (not is_playing),
    }
    play_session_id = str(now.get("jellyfin_play_session_id") or "").strip()
    if play_session_id:
        payload["PlaySessionId"] = play_session_id
    media_source_id = _first_nonempty_str(
        [
            now.get("jellyfin_media_source_id"),
            _extract_jellyfin_media_source_id_from_url(str(now.get("url") or "")),
        ]
    )
    if media_source_id:
        payload["MediaSourceId"] = media_source_id
    if dur_f is not None and dur_f >= 0:
        run_ticks = int(dur_f * 10_000_000)
        payload["RunTimeTicks"] = run_ticks
        pos_ticks = _jellyfin_snap_position_ticks(pos_ticks, run_ticks)
        played_pct = _jellyfin_played_percentage(pos_ticks, run_ticks)
        if played_pct is not None:
            payload["PlayedPercentage"] = played_pct
    payload["PositionTicks"] = pos_ticks
    if muted is not None:
        payload["IsMuted"] = bool(muted)
    if volume is not None:
        try:
            payload["VolumeLevel"] = int(float(volume))
        except Exception:
            pass
    return payload


jellyfin_receiver.register_progress_provider(_jellyfin_progress_snapshot)


@router.post("/integrations/jellyfin/push")
def jellyfin_integration_push():
    """
    Legacy Jellyfin plugin ingress retained only to emit a clear deprecation error.
    """
    raise HTTPException(
        status_code=410,
        detail="jellyfin plugin ingress deprecated; use RelayTV native Jellyfin client or /integrations/jellyfin/command",
    )


@router.post("/integrations/jellyfin/command")
def jellyfin_integration_command(req: JellyfinCommandReq):
    """Normalized Jellyfin command ingress (v1: Play/Stop/Pause/Resume/Seek/Next)."""
    st = jellyfin_receiver.status()
    if not bool(st.get("enabled")):
        raise HTTPException(status_code=503, detail="jellyfin integration disabled")

    action = _normalize_jellyfin_action(req.action, req.payload)
    jellyfin_receiver.mark_command(action)
    jellyfin_receiver.mark_heartbeat()
    command_id = _extract_jellyfin_command_id(req)
    if _jellyfin_is_duplicate_command(command_id):
        return {"ok": True, "action": action or "unknown", "suppressed_duplicate_command": True}

    try:
        if action == "play":
            source_url = (req.url or "").strip()
            playlist_items = _extract_jellyfin_playlist_items(req.payload)
            item_ids = [it.get("id", "") for it in playlist_items if isinstance(it, dict)]
            play_mode = _extract_jellyfin_play_mode(req.payload)
            if not source_url:
                source_url = _extract_jellyfin_play_url(req.payload)
            item_id = _extract_jellyfin_item_id(req.payload)
            if not item_id and item_ids:
                item_id = item_ids[0]
            requested_item_id = str(item_id or "").strip()
            media_source_id = _extract_jellyfin_media_source_id(req.payload)
            explicit_audio_idx = _extract_jellyfin_audio_stream_index(req.payload)
            explicit_sub_idx = _extract_jellyfin_subtitle_stream_index(req.payload)
            try:
                settings_snapshot = state.get_settings()
            except Exception:
                settings_snapshot = {}
            auth_token = _jellyfin_access_token()
            pref_audio_idx = ""
            pref_sub_idx = ""
            resolved_detail: dict[str, object] = {}
            if item_id:
                resolved_item = _resolve_jellyfin_playable_item(item_id, media_source_id=media_source_id)
                item_id = str(resolved_item.get("item_id") or item_id).strip()
                resolved_detail = resolved_item.get("detail") if isinstance(resolved_item.get("detail"), dict) else {}
                media_source_id = _first_nonempty_str([
                    resolved_item.get("media_source_id") if isinstance(resolved_item, dict) else "",
                    media_source_id,
                ])
                if requested_item_id and item_id and item_id != requested_item_id:
                    if item_ids and item_ids[0] == requested_item_id:
                        item_ids[0] = item_id
                    if playlist_items and isinstance(playlist_items[0], dict) and str(playlist_items[0].get("id") or "").strip() == requested_item_id:
                        playlist_items[0] = {
                            **playlist_items[0],
                            "id": item_id,
                            "media_source_id": media_source_id or str(playlist_items[0].get("media_source_id") or "").strip(),
                        }
                pref_audio_idx, pref_sub_idx = _preferred_jellyfin_stream_indices(item_id)
                if not media_source_id:
                    detail = resolved_detail if isinstance(resolved_detail, dict) else {}
                    if not detail:
                        try:
                            detail = jellyfin_receiver.get_item_detail(item_id)
                        except Exception:
                            detail = {}
                    media_source_id = _first_nonempty_str(
                        [
                            detail.get("media_source_id") if isinstance(detail, dict) else "",
                            detail.get("MediaSourceId") if isinstance(detail, dict) else "",
                        ]
                    )
            audio_stream_index = explicit_audio_idx or pref_audio_idx
            subtitle_stream_index = explicit_sub_idx or pref_sub_idx
            if not source_url and item_id:
                source_url = _build_jellyfin_item_stream_url(
                    item_id,
                    server_url=str(st.get("server_url") or ""),
                    api_key=auth_token,
                    media_source_id=media_source_id,
                    audio_stream_index=audio_stream_index,
                    subtitle_stream_index=subtitle_stream_index,
                )
            source_url = _normalize_jellyfin_source_url(
                source_url,
                server_url=str(st.get("server_url") or ""),
                api_key=auth_token,
            )
            source_url = _apply_jellyfin_stream_params(
                source_url,
                audio_stream_index=audio_stream_index,
                subtitle_stream_index=subtitle_stream_index,
            )
            source_url = _apply_jellyfin_media_source_param(source_url, media_source_id=media_source_id)
            if not media_source_id:
                media_source_id = _extract_jellyfin_media_source_id_from_url(source_url)
            selected_stream: dict[str, str] = {"mode": "direct", "reason": "", "media_source_id": media_source_id}
            if item_id:
                selected_stream = _select_jellyfin_playback_url(
                    item_id=item_id,
                    source_url=source_url,
                    server_url=str(st.get("server_url") or ""),
                    api_key=auth_token,
                    media_source_id=media_source_id,
                    audio_stream_index=audio_stream_index,
                    subtitle_stream_index=subtitle_stream_index,
                    settings=settings_snapshot,
                )
                source_url = _normalize_jellyfin_source_url(
                    str(selected_stream.get("url") or source_url),
                    server_url=str(st.get("server_url") or ""),
                    api_key=auth_token,
                )
                source_url = _apply_jellyfin_stream_params(
                    source_url,
                    audio_stream_index=audio_stream_index,
                    subtitle_stream_index=subtitle_stream_index,
                )
                media_source_id = _first_nonempty_str(
                    [
                        str(selected_stream.get("media_source_id") or "").strip(),
                        _extract_jellyfin_media_source_id_from_url(source_url),
                        media_source_id,
                    ]
                )
            if not source_url:
                raise HTTPException(status_code=400, detail="play command requires url")
            start_sec = _extract_jellyfin_start_seconds(req)
            try:
                suppress_recent_stop = bool(
                    getattr(player, "recent_jellyfin_stop_matches", lambda **_: False)(
                        item_id=item_id,
                        source_url=source_url,
                        media_source_id=media_source_id,
                    )
                )
            except Exception:
                suppress_recent_stop = False
            if suppress_recent_stop and (start_sec is None or float(start_sec) <= 1.0):
                return {
                    "ok": True,
                    "action": "play",
                    "suppressed_recent_stop_replay": True,
                    "now_playing": state.NOW_PLAYING,
                }
            if _jellyfin_should_suppress_duplicate_play(source_url, item_id, start_sec):
                return {"ok": True, "action": "play", "suppressed_duplicate": True, "now_playing": state.NOW_PLAYING}
            # If a play command explicitly asks to queue and we are already playing,
            # add items to queue without interrupting current playback.
            if play_mode in ("playnext", "playlast") and player.is_playing():
                try:
                    settings_snapshot = state.get_settings()
                except Exception:
                    settings_snapshot = {}
                queued = []
                existing_item_media: dict[str, set[str]] = {}
                existing_urls: set[str] = set()
                def _remember(iid_raw: object, url_raw: object, mid_raw: object = "") -> None:
                    iid = _canonical_jellyfin_item_id(iid_raw)
                    if not iid:
                        iid = _extract_jellyfin_item_id_from_url(str(url_raw or ""))
                    mid = _canonical_jellyfin_media_source_id(mid_raw)
                    if not mid:
                        mid = _canonical_jellyfin_media_source_id(
                            _extract_jellyfin_media_source_id_from_url(str(url_raw or ""))
                        )
                    if iid:
                        mids = existing_item_media.setdefault(iid, set())
                        mids.add(mid)
                    qurl_existing = _canonical_jellyfin_url_key(url_raw)
                    if qurl_existing:
                        existing_urls.add(qurl_existing)

                _remember(
                    (state.NOW_PLAYING or {}).get("jellyfin_item_id"),
                    (state.NOW_PLAYING or {}).get("url"),
                    (state.NOW_PLAYING or {}).get("jellyfin_media_source_id"),
                )
                for q in list(state.QUEUE):
                    if isinstance(q, dict):
                        _remember(q.get("jellyfin_item_id"), q.get("url"), q.get("jellyfin_media_source_id"))
                    else:
                        _remember("", q, "")

                def _seen(iid_raw: str, qurl_raw: str, mid_raw: str = "") -> bool:
                    iid = _canonical_jellyfin_item_id(iid_raw)
                    if not iid:
                        iid = _extract_jellyfin_item_id_from_url(qurl_raw)
                    mid = _canonical_jellyfin_media_source_id(mid_raw)
                    if not mid:
                        mid = _canonical_jellyfin_media_source_id(_extract_jellyfin_media_source_id_from_url(qurl_raw))

                    if iid:
                        seen_mids = existing_item_media.get(iid)
                        if seen_mids:
                            # Allow different known media-source variants of the same item.
                            # Unknown/blank media source is treated as duplicate-safe and blocks additional variants.
                            if not mid:
                                return True
                            if mid in seen_mids or "" in seen_mids:
                                return True

                    qurl = _canonical_jellyfin_url_key(qurl_raw)
                    if qurl and qurl in existing_urls:
                        return True
                    return False

                source_for_queue = source_url
                if source_for_queue:
                    selected_queue = _select_jellyfin_playback_url(
                        item_id=item_id,
                        source_url=source_for_queue,
                        server_url=str(st.get("server_url") or ""),
                        api_key=auth_token,
                        media_source_id=media_source_id,
                        audio_stream_index=audio_stream_index,
                        subtitle_stream_index=subtitle_stream_index,
                        settings=settings_snapshot,
                    )
                    source_for_queue = _normalize_jellyfin_source_url(
                        str(selected_queue.get("url") or source_for_queue),
                        server_url=str(st.get("server_url") or ""),
                        api_key=auth_token,
                    )
                    q_item = _smart_item_from_url(source_for_queue)
                    q_title = str(q_item.get("title") or "") if isinstance(q_item, dict) else ""
                    q_channel = str(q_item.get("channel") or "") if isinstance(q_item, dict) else ""
                    source_media_source_id = _first_nonempty_str(
                        [
                            media_source_id,
                            q_item.get("jellyfin_media_source_id") if isinstance(q_item, dict) else "",
                            _extract_jellyfin_media_source_id_from_url(source_for_queue),
                        ]
                    )
                    if not _seen(item_id, source_for_queue, source_media_source_id):
                        queued.append(
                            {
                                "url": source_for_queue,
                                "title": q_title or (f"Jellyfin item {item_id}" if item_id else "Jellyfin item"),
                                "provider": "jellyfin",
                                **({"channel": q_channel} if q_channel else {}),
                                **({"thumbnail": q_item.get("thumbnail")} if isinstance(q_item, dict) and q_item.get("thumbnail") else {}),
                                **({"jellyfin_item_id": item_id} if item_id else {}),
                                **({"jellyfin_media_source_id": source_media_source_id} if source_media_source_id else {}),
                                "jellyfin_stream_mode": str(selected_queue.get("mode") or ""),
                                "jellyfin_stream_reason": str(selected_queue.get("reason") or ""),
                            }
                        )
                        _remember(item_id, source_for_queue, source_media_source_id)
                # Prefer rich playlist items when available.
                for entry in playlist_items:
                    iid = str(entry.get("id") or "").strip()
                    if not iid:
                        continue
                    q_audio_idx, q_sub_idx = _preferred_jellyfin_stream_indices(iid)
                    if explicit_audio_idx:
                        q_audio_idx = explicit_audio_idx
                    if explicit_sub_idx:
                        q_sub_idx = explicit_sub_idx
                    qurl = _build_jellyfin_item_stream_url(
                        iid,
                        server_url=str(st.get("server_url") or ""),
                        api_key=auth_token,
                        media_source_id=str(entry.get("media_source_id") or "").strip(),
                        audio_stream_index=q_audio_idx,
                        subtitle_stream_index=q_sub_idx,
                    )
                    if not qurl:
                        continue
                    selected_q = _select_jellyfin_playback_url(
                        item_id=iid,
                        source_url=qurl,
                        server_url=str(st.get("server_url") or ""),
                        api_key=auth_token,
                        media_source_id=str(entry.get("media_source_id") or "").strip(),
                        audio_stream_index=q_audio_idx,
                        subtitle_stream_index=q_sub_idx,
                        settings=settings_snapshot,
                    )
                    qurl = _normalize_jellyfin_source_url(
                        str(selected_q.get("url") or qurl),
                        server_url=str(st.get("server_url") or ""),
                        api_key=auth_token,
                    )
                    if qurl == source_for_queue:
                        continue
                    q_media_source_id = str(entry.get("media_source_id") or "").strip()
                    if _seen(iid, qurl, q_media_source_id):
                        continue
                    qtitle = str(entry.get("title") or "").strip() or f"Jellyfin item {iid}"
                    queued.append(
                        {
                            "url": qurl,
                            "title": qtitle,
                            "provider": "jellyfin",
                            "jellyfin_item_id": iid,
                            **({"jellyfin_media_source_id": q_media_source_id} if q_media_source_id else {}),
                            "jellyfin_stream_mode": str(selected_q.get("mode") or ""),
                            "jellyfin_stream_reason": str(selected_q.get("reason") or ""),
                        }
                    )
                    _remember(iid, qurl, q_media_source_id)
                if queued:
                    with state.QUEUE_LOCK:
                        if play_mode == "playnext":
                            state.QUEUE[:0] = queued
                        else:
                            state.QUEUE.extend(queued)
                        qlen = len(state.QUEUE)
                        queue_snapshot = list(state.QUEUE)
                    try:
                        state.persist_queue()
                    except Exception:
                        pass
                    try:
                        player.prime_mpv_up_next_from_queue(force=True)
                    except Exception:
                        pass
                    try:
                        lead = queued[0] if isinstance(queued[0], dict) else {}
                        lead_title = str((lead or {}).get("title") or "").strip()
                        queued_count = len(queued)
                        if queued_count > 1:
                            qtext = f"Queued {queued_count} items"
                        else:
                            qtext = f"Queued next: {lead_title or 'item'}"
                        _push_overlay_toast(
                            text=qtext,
                            duration=_playback_notification_display_sec(),
                            level="info",
                            icon="share",
                            image_url=(lead.get("thumbnail") if isinstance(lead, dict) else None),
                        )
                    except Exception:
                        pass
                    _jellyfin_emit_progress_hint()
                    _ui_event_push_queue("jellyfin_queue", queue=queue_snapshot, queue_length=qlen, source="jellyfin")
                    _ui_event_push_jellyfin(
                        "queue_only",
                        refresh_active_tab=True,
                        refresh_status=True,
                        reason=play_mode,
                    )
                    return {"ok": True, "action": "queue_only", "queue_mode": play_mode, "queued": len(queued), "queue_length": qlen}
            stopped_payload = None
            if play_mode == "playnow":
                cur = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
                if isinstance(cur, dict) and bool(player.is_playing()):
                    cur_copy = dict(cur)
                    cur_item_id = _canonical_jellyfin_item_id(cur_copy.get("jellyfin_item_id"))
                    cur_url_key = _canonical_jellyfin_url_key(cur_copy.get("url"))
                    next_item_id = _canonical_jellyfin_item_id(item_id)
                    next_url_key = _canonical_jellyfin_url_key(source_url)
                    replacing = False
                    if cur_item_id and next_item_id:
                        replacing = cur_item_id != next_item_id
                    elif cur_url_key and next_url_key:
                        replacing = cur_url_key != next_url_key
                    if replacing:
                        pos = None
                        dur = None
                        try:
                            with player.MPV_LOCK:
                                pos = player.mpv_get("time-pos")
                                dur = player.mpv_get("duration")
                        except Exception:
                            pos = None
                            dur = None
                        stopped_payload = _jellyfin_stopped_snapshot_from_now(cur_copy, pos, dur)
            clear_queue_for_play = play_mode == "playnow"
            state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0
            play_item_payload = _smart_item_from_url(source_url, start_pos=start_sec)
            play_target = play_item_payload if isinstance(play_item_payload, dict) else source_url
            now = player.play_item(
                play_target,
                use_resolver=bool(req.use_ytdlp),
                cec=False,
                clear_queue=clear_queue_for_play,
                mode="jellyfin_play",
                start_pos=start_sec,
            )
            # Preserve Jellyfin identifiers for progress/session reporting.
            if isinstance(now, dict):
                now = dict(now)
                if isinstance(play_item_payload, dict):
                    now = _merge_jellyfin_playback_metadata(now, play_item_payload)
                if item_id:
                    now["jellyfin_item_id"] = item_id
                    if media_source_id:
                        now["jellyfin_media_source_id"] = media_source_id
                    now["jellyfin_stream_mode"] = str(selected_stream.get("mode") or "direct")
                    now["jellyfin_stream_reason"] = str(selected_stream.get("reason") or "")
                    play_session_id = _first_nonempty_str([
                        (req.payload or {}).get("play_session_id") if isinstance(req.payload, dict) else "",
                        (req.payload or {}).get("PlaySessionId") if isinstance(req.payload, dict) else "",
                    ])
                    if play_session_id:
                        now["jellyfin_play_session_id"] = play_session_id
                    try:
                        detail = jellyfin_receiver.get_item_detail(item_id)
                    except Exception:
                        detail = {}
                    now = _jellyfin_enrich_now_stream_metadata(
                        now,
                        detail=detail if isinstance(detail, dict) else {},
                        audio_stream_index=audio_stream_index,
                        subtitle_stream_index=subtitle_stream_index,
                    )
                state.set_now_playing(now)
            # Playlist-style play command support: enqueue remaining ItemIds.
            if item_ids and len(item_ids) > 1:
                extra_items = playlist_items[1:] if len(playlist_items) > 1 else [{"id": iid, "title": "", "media_source_id": ""} for iid in item_ids[1:]]
                queued = []
                seen_item_media: dict[str, set[str]] = {}
                seen_urls: set[str] = set()

                def _remember_seen(iid_raw: object, url_raw: object, mid_raw: object = "") -> None:
                    iid = _canonical_jellyfin_item_id(iid_raw)
                    if not iid:
                        iid = _extract_jellyfin_item_id_from_url(str(url_raw or ""))
                    mid = _canonical_jellyfin_media_source_id(mid_raw)
                    if not mid:
                        mid = _canonical_jellyfin_media_source_id(_extract_jellyfin_media_source_id_from_url(str(url_raw or "")))
                    if iid:
                        seen_item_media.setdefault(iid, set()).add(mid)
                    key = _canonical_jellyfin_url_key(url_raw)
                    if key:
                        seen_urls.add(key)

                def _seen(iid_raw: str, url_raw: str, mid_raw: str = "") -> bool:
                    iid = _canonical_jellyfin_item_id(iid_raw)
                    if not iid:
                        iid = _extract_jellyfin_item_id_from_url(url_raw)
                    mid = _canonical_jellyfin_media_source_id(mid_raw)
                    if not mid:
                        mid = _canonical_jellyfin_media_source_id(_extract_jellyfin_media_source_id_from_url(url_raw))
                    if iid:
                        mids = seen_item_media.get(iid)
                        if mids:
                            if not mid:
                                return True
                            if mid in mids or "" in mids:
                                return True
                    key = _canonical_jellyfin_url_key(url_raw)
                    return bool(key and key in seen_urls)

                _remember_seen(now.get("jellyfin_item_id") if isinstance(now, dict) else "", now.get("url") if isinstance(now, dict) else "", now.get("jellyfin_media_source_id") if isinstance(now, dict) else "")
                for existing in list(state.QUEUE):
                    if isinstance(existing, dict):
                        _remember_seen(existing.get("jellyfin_item_id"), existing.get("url"), existing.get("jellyfin_media_source_id"))
                    else:
                        _remember_seen("", existing, "")

                for entry in extra_items:
                    iid = str(entry.get("id") or "").strip()
                    if not iid:
                        continue
                    q_audio_idx, q_sub_idx = _preferred_jellyfin_stream_indices(iid)
                    if explicit_audio_idx:
                        q_audio_idx = explicit_audio_idx
                    if explicit_sub_idx:
                        q_sub_idx = explicit_sub_idx
                    qurl = _build_jellyfin_item_stream_url(
                        iid,
                        server_url=str(st.get("server_url") or ""),
                        api_key=auth_token,
                        media_source_id=str(entry.get("media_source_id") or "").strip(),
                        audio_stream_index=q_audio_idx,
                        subtitle_stream_index=q_sub_idx,
                    )
                    if not qurl:
                        continue
                    q_media_source_id = str(entry.get("media_source_id") or "").strip()
                    selected_q = _select_jellyfin_playback_url(
                        item_id=iid,
                        source_url=qurl,
                        server_url=str(st.get("server_url") or ""),
                        api_key=auth_token,
                        media_source_id=q_media_source_id,
                        audio_stream_index=q_audio_idx,
                        subtitle_stream_index=q_sub_idx,
                        settings=settings_snapshot if isinstance(settings_snapshot, dict) else {},
                    )
                    qurl = _normalize_jellyfin_source_url(
                        str(selected_q.get("url") or qurl),
                        server_url=str(st.get("server_url") or ""),
                        api_key=auth_token,
                    )
                    q_media_source_id = _first_nonempty_str(
                        [
                            str(selected_q.get("media_source_id") or "").strip(),
                            _extract_jellyfin_media_source_id_from_url(qurl),
                            q_media_source_id,
                        ]
                    )
                    if _seen(iid, qurl, q_media_source_id):
                        continue
                    qtitle = str(entry.get("title") or "").strip() or f"Jellyfin item {iid}"
                    queued.append(
                        {
                            "url": qurl,
                            "title": qtitle,
                            "provider": "jellyfin",
                            "jellyfin_item_id": iid,
                            **({"jellyfin_media_source_id": q_media_source_id} if q_media_source_id else {}),
                            "jellyfin_stream_mode": str(selected_q.get("mode") or ""),
                            "jellyfin_stream_reason": str(selected_q.get("reason") or ""),
                        }
                    )
                    _remember_seen(iid, qurl, q_media_source_id)
                if queued:
                    with state.QUEUE_LOCK:
                        state.QUEUE.extend(queued)
                        queue_snapshot = list(state.QUEUE)
                    try:
                        state.persist_queue()
                    except Exception:
                        pass
                    try:
                        player.prime_mpv_up_next_from_queue(force=True)
                    except Exception:
                        pass
                    _ui_event_push_queue("jellyfin_playlist", queue=queue_snapshot, queue_length=len(queue_snapshot), source="jellyfin")
            if isinstance(stopped_payload, dict) and stopped_payload:
                _jellyfin_emit_stopped_payload(stopped_payload)
            _jellyfin_emit_progress_hint()
            _ui_event_push_jellyfin("play", refresh_active_tab=True, refresh_status=True, reason=play_mode or "play")
            return {"ok": True, "action": "play", "now_playing": now}

        if action == "stop":
            res = stop()
            return {"ok": True, "action": "stop", "result": res}

        if action == "pause":
            out = {"ok": True, "action": "pause", "result": pause()}
            _jellyfin_emit_progress_hint()
            return out

        if action in ("resume", "unpause"):
            out = {"ok": True, "action": "resume", "result": resume()}
            _jellyfin_emit_progress_hint()
            return out

        if action == "seek":
            sec = _extract_jellyfin_seek_seconds(req)
            if sec is None:
                raise HTTPException(status_code=400, detail="seek command requires start_pos or payload.position")
            out = {"ok": True, "action": "seek", "result": seek_abs(SeekAbsReq(sec=float(sec)))}
            _jellyfin_emit_progress_hint()
            return out

        if action == "next":
            out = {"ok": True, "action": "next", "result": next_track()}
            _jellyfin_emit_progress_hint()
            return out

        if action == "previous":
            out = {"ok": True, "action": "previous", "result": previous()}
            _jellyfin_emit_progress_hint()
            return out

        if action == "set_volume":
            vol = _extract_jellyfin_volume(req)
            if vol is None:
                raise HTTPException(status_code=400, detail="set_volume requires payload.VolumeLevel or payload.volume")
            out = {"ok": True, "action": "set_volume", "result": volume(VolumeReq(set=vol))}
            _jellyfin_emit_progress_hint()
            return out

        if action == "mute":
            out = {"ok": True, "action": "mute", "result": mute(MuteReq(set=True))}
            _jellyfin_emit_progress_hint()
            return out

        if action == "unmute":
            out = {"ok": True, "action": "unmute", "result": mute(MuteReq(set=False))}
            _jellyfin_emit_progress_hint()
            return out

        raise HTTPException(status_code=400, detail=f"unsupported jellyfin action: {req.action}")
    except HTTPException:
        raise
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=500, detail=f"jellyfin command failed: {e}")


@router.post("/integrations/jellyfin/heartbeat")
def jellyfin_integration_heartbeat():
    """Force a single Jellyfin progress heartbeat (debug/validation helper)."""
    st = jellyfin_receiver.status()
    if not bool(st.get("enabled")):
        raise HTTPException(status_code=503, detail="jellyfin integration disabled")
    out = jellyfin_receiver.send_progress_once()
    if not bool(out.get("ok")):
        return JSONResponse(out, status_code=202)
    return out


@router.get("/integrations/jellyfin/progress_snapshot")
def jellyfin_integration_progress_snapshot():
    """Return the current outbound Jellyfin progress payload (debug helper)."""
    st = jellyfin_receiver.status()
    if not bool(st.get("enabled")):
        raise HTTPException(status_code=503, detail="jellyfin integration disabled")
    payload = _jellyfin_progress_snapshot()
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "reason": "no_payload"}, status_code=202)
    return {"ok": True, "payload": payload}


@router.post("/integrations/jellyfin/stopped")
def jellyfin_integration_stopped():
    """Force a single Jellyfin playback-stopped report using current snapshot."""
    st = jellyfin_receiver.status()
    if not bool(st.get("enabled")):
        raise HTTPException(status_code=503, detail="jellyfin integration disabled")
    payload = _jellyfin_stopped_snapshot()
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "reason": "no_payload"}, status_code=202)
    out = jellyfin_receiver.send_playback_stopped_once(payload)
    if not bool(out.get("ok")):
        return JSONResponse(out, status_code=202)
    return {"ok": True, "payload": payload, "result": out}


@router.get("/integrations/jellyfin/stopped_snapshot")
def jellyfin_integration_stopped_snapshot():
    """Return the current outbound Jellyfin playback-stopped payload (debug helper)."""
    st = jellyfin_receiver.status()
    if not bool(st.get("enabled")):
        raise HTTPException(status_code=503, detail="jellyfin integration disabled")
    payload = _jellyfin_stopped_snapshot()
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "reason": "no_payload"}, status_code=202)
    return {"ok": True, "payload": payload}

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


@router.get("/assets/logo.svg")
def relaytv_logo_svg_asset():
    path = _resolve_brand_svg_path("logo.svg", explicit_env="RELAYTV_LOGO_PATH")
    if path and os.path.exists(path):
        return FileResponse(path, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})
    return Response(_relaytv_svg(512), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/assets/banner.svg")
def relaytv_banner_svg_asset():
    path = _resolve_brand_svg_path("banner.svg", explicit_env="RELAYTV_BANNER_PATH")
    if path and os.path.exists(path):
        return FileResponse(path, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})
    return Response(_relaytv_svg(512), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/assets/banner.png")
def relaytv_banner_png_asset():
    path = _resolve_brand_asset_path(
        "banner.png",
        explicit_env="RELAYTV_BANNER_PATH",
        fallback_names=("banner.svg", "logo.svg"),
    )
    if path and os.path.exists(path):
        return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})
    return Response(_relaytv_svg(512), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})




@router.get("/pwa/brand/logo.svg")
def pwa_brand_logo_svg_asset():
    path = _resolve_brand_svg_path("logo.svg", explicit_env="RELAYTV_LOGO_PATH")
    if path and os.path.exists(path):
        return FileResponse(path, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})
    return Response(_relaytv_svg(512), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/pwa/brand/banner.svg")
def pwa_brand_banner_svg_asset():
    path = _resolve_brand_svg_path("banner.svg", explicit_env="RELAYTV_BANNER_PATH")
    if path and os.path.exists(path):
        return FileResponse(path, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})
    return Response(_relaytv_svg(512), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/pwa/brand/banner.png")
def pwa_brand_banner_png_asset():
    path = _resolve_brand_asset_path(
        "banner.png",
        explicit_env="RELAYTV_BANNER_PATH",
        fallback_names=("banner.svg", "logo.svg"),
    )
    if path and os.path.exists(path):
        return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})
    return Response(_relaytv_svg(512), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/pwa/weather/{asset_name}")
def pwa_weather_asset(asset_name: str, theme: str | None = None):
    safe_name = os.path.basename(asset_name)
    if safe_name != asset_name or not safe_name.endswith(".svg"):
        return Response(status_code=400)
    icon_theme = _weather_icon_theme(theme)
    for parts in _weather_icon_candidates(safe_name, icon_theme):
        path = _resolve_static_asset(*parts)
        if path and os.path.exists(path):
            return FileResponse(path, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})
    return Response(_fallback_svg(safe_name.removesuffix(".svg")), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=300"})


@router.post("/play_at")
def play_at(req: PlayAtReq):
    def _delayed_play() -> None:
        delay = max(0.0, float(req.start_at) - time.time())
        if delay > 0:
            time.sleep(delay)
        try:
            player.play_item(req.url, use_resolver=True, cec=False, clear_queue=False, mode="play_at")
        except Exception as e:
            logger.warning("play_at_failed start_at=%s error=%s", req.start_at, e)

    threading.Thread(target=_delayed_play, daemon=True).start()
    return {"ok": True, "url": req.url, "start_at": req.start_at}


@router.get("/snapshots/{filename}")
async def get_snapshot(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        return Response(status_code=400)
    snap_dir = os.getenv("RELAYTV_SNAPSHOT_DIR", "/data/snapshots")
    path = os.path.join(snap_dir, filename)
    if not os.path.exists(path):
        return Response(status_code=404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.post("/snapshot")
@router.get("/snapshot")
def snapshot():
    if not player.is_playing():
        raise HTTPException(status_code=409, detail="No active playback for snapshot")
    snap_dir = os.getenv("RELAYTV_SNAPSHOT_DIR", "/data/snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    name = f"snapshot-{int(time.time() * 1000)}.jpg"
    path = os.path.join(snap_dir, name)
    player.mpv_command(["screenshot-to-file", path, "video"])
    return {"ok": True, "image_url": f"/snapshots/{name}"}


@router.post("/previous")
def previous():
    """Back button semantics.

    - If current position > ~5s, restart current playback (seek to 0)
    - Else, play the most recent *different* history entry and preserve current to queue front
    """
    if player.is_playing():
        try:
            with player.MPV_LOCK:
                pos = player.mpv_get("time-pos")
            if pos is not None and float(pos) > 5.0:
                player.mpv_command(["seek", 0.0, "absolute"])
                return {"ok": True, "action": "restart"}
        except Exception:
            pass

    cur_url = None
    if isinstance(state.NOW_PLAYING, dict):
        u = state.NOW_PLAYING.get("url")
        if isinstance(u, str) and u.strip():
            cur_url = u.strip()

    chosen = None
    with state.HISTORY_LOCK:
        for i, it in enumerate(state.HISTORY):
            if not isinstance(it, dict):
                continue
            u = it.get("url")
            if not isinstance(u, str) or not u.strip():
                continue
            u = u.strip()
            if cur_url and u == cur_url:
                continue
            chosen = dict(it)
            # Remove so repeated /previous walks back through history
            state.HISTORY.pop(i)
            break
    if chosen is None:
        raise HTTPException(status_code=400, detail="No previous history item")
    try:
        state.persist_history()
    except Exception:
        pass

    return play_now(PlayNowReq(url=chosen.get("url"), preserve_current=True, reason="previous"))


@router.post("/queue/remove")
def queue_remove(req: QueueRemoveReq):
    with state.QUEUE_LOCK:
        idx = int(req.index)
        if idx < 0 or idx >= len(state.QUEUE):
            raise HTTPException(status_code=400, detail="index out of range")
        removed = state.QUEUE.pop(idx)
        snapshot = {"queue": list(state.QUEUE), "saved_at": int(time.time())}

    try:
        state.persist_queue_payload(snapshot)
    except Exception as e:
        logger.warning("queue_persist_failed route=queue_remove error=%s", e)
    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    _ui_event_push_queue("remove", queue=snapshot["queue"], queue_length=len(snapshot["queue"]), source="queue_remove")

    return {"status": "removed", "removed": removed, "queue": snapshot["queue"], "queue_length": len(snapshot["queue"])}


def _queue_item_dedupe_key(item: object) -> tuple[str, str]:
    if not isinstance(item, dict):
        return ("raw", str(item))
    provider = str(item.get("provider") or "").strip().lower()
    url = str(item.get("url") or "").strip()
    if provider == "jellyfin":
        iid = _canonical_jellyfin_item_id(item.get("jellyfin_item_id"))
        if iid:
            return ("jellyfin_id", iid)
        ukey = _canonical_jellyfin_url_key(url)
        if ukey:
            return ("jellyfin_url", ukey)
    if url:
        return ("url", url)
    title = str(item.get("title") or "").strip()
    return ("title", f"{provider}|{title}")


@router.post("/queue/dedupe")
def queue_dedupe():
    with state.QUEUE_LOCK:
        original = list(state.QUEUE)
        seen: set[tuple[str, str]] = set()
        deduped: list[object] = []
        removed = 0
        for entry in original:
            key = _queue_item_dedupe_key(entry)
            if key in seen:
                removed += 1
                continue
            seen.add(key)
            deduped.append(entry)
        changed = len(deduped) != len(original)
        if changed:
            state.QUEUE[:] = deduped
            snapshot = {"queue": list(state.QUEUE), "saved_at": int(time.time())}
            try:
                state.persist_queue_payload(snapshot)
            except Exception as e:
                logger.warning("queue_persist_failed route=queue_dedupe error=%s", e)
    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    if changed:
        _ui_event_push_queue("dedupe", queue=list(state.QUEUE), queue_length=len(state.QUEUE), source="queue_dedupe")
    return {
        "status": "deduped",
        "changed": changed,
        "removed_count": removed,
        "queue_length": len(state.QUEUE),
        "queue": list(state.QUEUE),
    }


@router.post("/queue/move")
def queue_move(req: QueueMoveReq):
    frm = int(req.from_index)
    to = int(req.to_index)
    with state.QUEUE_LOCK:
        n = len(state.QUEUE)
        if n == 0:
            raise HTTPException(status_code=400, detail="queue is empty")
        if frm < 0 or frm >= n or to < 0 or to >= n:
            raise HTTPException(status_code=400, detail="index out of range")
        item = state.QUEUE.pop(frm)
        state.QUEUE.insert(to, item)
        snapshot = {"queue": list(state.QUEUE), "saved_at": int(time.time())}

        try:
            state.persist_queue_payload(snapshot)
        except Exception as e:
            logger.warning("queue_persist_failed route=queue_move error=%s", e)
    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    _ui_event_push_queue("move", queue=snapshot["queue"], queue_length=len(snapshot["queue"]), source="queue_move")

    return {"status": "moved", "queue": snapshot["queue"], "queue_length": len(snapshot["queue"])}


@router.get("/share")
def share(url: str | None = None, link: str | None = None, cec: bool = True):
    shared = (url or link or "").strip()
    if not shared:
        raise HTTPException(status_code=400, detail="Missing url or link query parameter")
    item = _smart_item_from_url(shared)
    start_pos = item.get("resume_pos") if isinstance(item, dict) else None
    now = player.play_item(
        item,
        use_resolver=True,
        cec=cec,
        clear_queue=True,
        mode="share",
        start_pos=(float(start_pos) if start_pos is not None else None),
    )
    return {"status": "playing", "now_playing": now, "source": "share_target"}


@router.post("/smart")
def smart(req: PlayReq):
    # Reset auto-next suppression on user-initiated actions
    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0
    """
    One-button behavior:
      - If mpv is currently playing -> enqueue
      - Else -> play immediately (clears queue)
    """
    if player.is_playing():
        item = _smart_item_from_url(req.url or "", lightweight=True)
        with state.QUEUE_LOCK:
            state.QUEUE.append(item)
            qlen = len(state.QUEUE)
        state.persist_queue()
        try:
            player.prefetch_queue_item_stream(item)
        except Exception:
            pass
        try:
            player.prime_mpv_up_next_from_queue(force=True)
        except Exception:
            pass
        try:
            _push_queue_added_toast_async(item, req.url or "item")
        except Exception:
            pass
        return {"status": "queued", "item": item, "queue_length": qlen, "now_playing": state.NOW_PLAYING}

    item = _smart_item_from_url(req.url or "")
    start_pos = item.get("resume_pos") if isinstance(item, dict) else None
    now = player.play_item(
        item,
        use_resolver=req.use_ytdlp,
        cec=req.cec,
        clear_queue=True,
        mode="smart_play",
        start_pos=(float(start_pos) if start_pos is not None else None),
    )
    return {"status": "playing", "now_playing": now}


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
    sess = str(getattr(state, "SESSION_STATE", "idle") or "idle").strip().lower()
    now = getattr(state, "NOW_PLAYING", None)
    if sess != "paused" or not isinstance(now, dict):
        return None

    try:
        result = player.mpv_set_result("pause", False)
    except Exception:
        return None
    if not isinstance(result, dict) or result.get("error") != "success":
        return None

    resumed = dict(now)
    resumed["closed"] = False
    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0
    state.set_now_playing(resumed)
    state.set_session_state("playing")
    state.set_pause_reason(None)
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
@router.post("/pause")
def pause():
    result = _control_result_or_raise(player.mpv_set_result("pause", True), action="pause")
    state.set_session_state("paused")
    state.set_pause_reason("user")
    return {"ok": True, "paused": True, **_control_ack_payload(result)}


@router.post("/resume")
def resume():
    result = _control_result_or_raise(player.mpv_set_result("pause", False), action="resume")
    state.set_session_state("playing")
    state.set_pause_reason(None)
    return {"ok": True, "paused": False, **_control_ack_payload(result)}


@router.post("/toggle_pause")
def toggle_pause():
    cur = bool(player.mpv_get("pause"))
    target = not cur
    result = _control_result_or_raise(player.mpv_set_result("pause", target), action="toggle_pause")
    state.set_session_state("paused" if target else "playing")
    state.set_pause_reason("user" if target else None)
    return {"ok": True, "paused": target, **_control_ack_payload(result)}


@router.post("/playback/play")
def playback_play():
    """
    User-facing Play semantics:
      - If mpv is running: toggle pause/resume
      - Else if current session is resumable and NOW_PLAYING exists: resume at saved position
      - Else: play next item from queue (if any)
    """
    # If already playing, behave as play/pause for stale clients that still call
    # /playback/play instead of /playback/toggle.
    if player.is_playing():
        cur = bool(player.mpv_get("pause"))
        target = not cur
        result = _control_result_or_raise(player.mpv_set_result("pause", target), action="playback_play")
        state.set_session_state("paused" if target else "playing")
        state.set_pause_reason("user" if target else None)
        return {
            "ok": True,
            "action": ("pause" if target else "resume"),
            "paused": target,
            "now_playing": state.NOW_PLAYING,
            **_control_ack_payload(result),
        }

    paused_resume = _resume_paused_current_session_in_place(action="resume")
    if paused_resume is not None:
        return paused_resume

    # If runtime dropped out but app state still has a resumable current item,
    # prefer resuming that item over consuming the queue.
    sess = str(getattr(state, "SESSION_STATE", "idle") or "idle").strip().lower()
    if sess in {"closed", "paused", "playing"} and state.NOW_PLAYING:
        now = state.NOW_PLAYING
        # Reuse resolved stream/audio where possible.
        stream = now.get("stream")
        audio = now.get("audio")
        pos = now.get("resume_pos")
        if pos is None:
            pos = getattr(state, "SESSION_POSITION", None)
        state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0

        if isinstance(stream, str) and stream.strip():
            resume_result: dict[str, object] | None = None
            with player.MPV_LOCK:
                stream_url = stream.strip()
                audio_url = audio.strip() if isinstance(audio, str) and audio.strip() else None
                if not player._load_stream_in_existing_mpv(stream_url, audio_url=audio_url):
                    player.start_mpv(stream_url, audio_url=audio_url)
            # Seek after start
            if pos is not None:
                try:
                    player.mpv_seek_absolute_with_retry(float(pos), tries=25, delay=0.12)
                except Exception:
                    pass
            try:
                resume_result = _control_result_or_raise(player.mpv_set_result("pause", False), action="resume_session")
            except Exception:
                resume_result = None
            resumed = dict(now)
            resumed["started"] = int(time.time())
            resumed["mode"] = "resume"
            resumed["closed"] = False
            state.set_now_playing(resumed)
            state.set_session_state("playing")
            state.set_pause_reason(None)
            return {"ok": True, "action": "resume_session", "now_playing": state.NOW_PLAYING, **_control_ack_payload(resume_result)}

        # Fallback: re-resolve/play via play_item
        resumed = player.play_item(
            now,
            use_resolver=True,
            cec=False,
            clear_queue=False,
            mode="resume",
            start_pos=(float(pos) if pos is not None else None),
        )
        resumed["closed"] = False
        state.set_now_playing(resumed)
        state.set_session_state("playing")
        state.set_pause_reason(None)
        return {"ok": True, "action": "resume_session", "now_playing": state.NOW_PLAYING}

    # Else: play next queue item
    try:
        handoff = player.advance_queue_playback(mode="play_next", prefer_playlist_next=False)
    except player.QueueAdvanceEmptyError:
        raise HTTPException(status_code=400, detail="Queue is empty")
    return {"ok": True, "action": "play_next", "now_playing": handoff.get("now_playing")}

@router.post("/playback/toggle")
def playback_toggle():
    """
    Single button behavior:
      - If playing: toggle pause
      - If not playing: behave like /playback/play
    """
    if player.is_playing():
        cur = bool(player.mpv_get("pause"))
        target = not cur
        result = _control_result_or_raise(player.mpv_set_result("pause", target), action="toggle_pause")
        state.set_session_state("paused" if target else "playing")
        state.set_pause_reason("user" if target else None)
        return {"ok": True, "action": "toggle_pause", "paused": target, **_control_ack_payload(result)}
    return playback_play()



@router.post("/seek")
def seek(req: SeekReq):
    hold_sec = _seek_transition_hold_sec()
    state.AUTO_NEXT_SUPPRESS_UNTIL = max(float(getattr(state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0) or 0.0), time.time() + hold_sec)
    try:
        player._mark_playback_transition(hold_sec)
    except Exception:
        pass
    result = _seek_relative_result(float(req.sec))
    return {"ok": True, "seeked": req.sec, **_control_ack_payload(result)}


@router.post("/seek_abs")
def seek_abs(req: SeekAbsReq):
    # Seek to absolute position (seconds)
    hold_sec = _seek_transition_hold_sec()
    state.AUTO_NEXT_SUPPRESS_UNTIL = max(float(getattr(state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0) or 0.0), time.time() + hold_sec)
    try:
        player._mark_playback_transition(hold_sec)
    except Exception:
        pass
    result = _seek_absolute_result(float(req.sec))
    return {"ok": True, "seeked_to": req.sec, **_control_ack_payload(result)}

@router.post("/volume")
def volume(req: VolumeReq):
    if req.set is not None:
        v = max(0.0, min(200.0, float(req.set)))
        result = _control_result_or_raise(player.mpv_set_result("volume", v), action="volume")
        state.update_settings({"volume": v})
        return {"ok": True, "volume": v, **_control_ack_payload(result)}
    if req.delta is not None:
        cur = float(player.mpv_get("volume") or 0.0)
        v = max(0.0, min(200.0, cur + float(req.delta)))
        result = _control_result_or_raise(player.mpv_set_result("volume", v), action="volume")
        state.update_settings({"volume": v})
        return {"ok": True, "volume": v, **_control_ack_payload(result)}
    raise HTTPException(status_code=400, detail="Provide delta or set")


@router.post("/mute")
def mute(req: MuteReq):
    """Toggle mute (or explicitly set it) using mpv's native mute property."""
    try:
        cur = bool(player.mpv_get("mute"))
    except Exception:
        cur = False
    target = (not cur) if req.set is None else bool(req.set)
    try:
        result = _control_result_or_raise(player.mpv_set_result("mute", target), action="mute")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"mute failed: {e}")
    return {"ok": True, "mute": target, **_control_ack_payload(result)}



@router.post("/close")
def close():
    """Close the player but keep session resumable (queue preserved)."""
    # Prevent the autoplay worker from immediately advancing.
    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 3600 * 24  # 24h (reset on resume/play)

    pos = None
    dur = None
    preserve_resume = _can_preserve_closed_session() or isinstance(state.NOW_PLAYING, dict)
    try:
        if bool(getattr(player, "native_qt_playback_explicitly_ended", lambda: False)()):
            preserve_resume = False
    except Exception:
        pass
    if preserve_resume:
        # Lock before reading time-pos to avoid race with stop

        with player.MPV_LOCK:

            try:
                pos = player.mpv_get("time-pos")
            except Exception:
                pos = None
            try:
                dur = player.mpv_get("duration")
            except Exception:
                dur = None
        if pos is None and isinstance(state.NOW_PLAYING, dict):
            pos = state.NOW_PLAYING.get("resume_pos")
        try:
            state.set_session_position(float(pos) if pos is not None else None)
        except Exception:
            state.set_session_position(None)

        # Also store on NOW_PLAYING so resume can use it even if SESSION_POSITION is lost
        try:
            if isinstance(state.NOW_PLAYING, dict) and pos is not None:
                np = dict(state.NOW_PLAYING)
                np["resume_pos"] = float(pos)
                np["closed"] = True
                np["closed_at"] = int(time.time())
                state.set_now_playing(np)
        except Exception:
            pass
    elif getattr(state, "SESSION_STATE", "idle") != "closed":
        try:
            state.set_now_playing(None)
        except Exception:
            pass
        try:
            state.set_session_position(None)
        except Exception:
            pass

    state.set_session_state("closed" if preserve_resume else "idle")
    keep_qt_shell = bool(
        preserve_resume
        and _idle_dashboard_enabled_for_player()
        and getattr(player, "_qt_shell_backend_enabled", lambda: False)()
    )
    stopped_in_place = False
    if keep_qt_shell:
        with player.MPV_LOCK:
            stopped_in_place = bool(getattr(player, "stop_playback_keep_qt_shell", lambda: False)())
    if not stopped_in_place:
        with player.MPV_LOCK:
            player.stop_mpv(restart_splash=_idle_dashboard_enabled_for_player())

    if preserve_resume:
        _jellyfin_emit_stopped_hint(pos, dur)
    return {
        "status": ("closed" if preserve_resume else "idle"),
        "resume_available": bool(preserve_resume and state.NOW_PLAYING),
        "position": pos,
        "kept_player_shell": bool(stopped_in_place),
    }


@router.post("/resume/clear")
def clear_resumable_session():
    """Clear retained now-playing/resume state and return to idle."""
    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 3600 * 24
    with player.MPV_LOCK:
        player.stop_mpv()
    state.set_now_playing(None)
    state.set_session_position(None)
    state.set_session_state("idle")
    try:
        state.persist_queue()
    except Exception:
        pass
    return {"status": "cleared", "resume_available": False}


@router.post("/resume_session")
def resume_session():
    """Resume a previously closed session (best-effort)."""
    if getattr(state, "SESSION_STATE", "idle") != "closed":
        raise HTTPException(status_code=400, detail="No closed session to resume")

    now = state.NOW_PLAYING
    if not now:
        raise HTTPException(status_code=400, detail="No item to resume")

    # Allow autoplay again
    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0

    # Reuse resolved stream/audio from NOW_PLAYING to avoid re-resolving.
    stream = now.get("stream")
    audio = now.get("audio")

    if not isinstance(stream, str) or not stream.strip():
        # Fallback: re-resolve if missing
        resumed = player.play_item(
            now,
            use_resolver=True,
            cec=False,
            clear_queue=False,
            mode="resume",
            start_pos=(float(now.get("resume_pos")) if now.get("resume_pos") is not None else getattr(state, "SESSION_POSITION", None)),
        )
    else:
        with player.MPV_LOCK:
            stream_url = stream.strip()
            audio_url = audio.strip() if isinstance(audio, str) and audio.strip() else None
            if not player._load_stream_in_existing_mpv(stream_url, audio_url=audio_url):
                player.start_mpv(stream_url, audio_url=audio_url)
        resumed = dict(now)
        resumed["started"] = int(time.time())
        resumed["mode"] = "resume"
        resumed["closed"] = False
        state.set_now_playing(resumed)
        state.set_session_state("playing")

    resume_result: dict[str, object] | None = None
    # Seek to last known position
    pos = now.get("resume_pos")
    if pos is None:
        pos = getattr(state, "SESSION_POSITION", None)
    if pos is not None:
        try:
            player.mpv_seek_absolute_with_retry(float(pos), tries=25, delay=0.12)
        except Exception:
            pass

        try:
            resume_result = _control_result_or_raise(player.mpv_set_result("pause", False), action="resume_session")
        except Exception:
            resume_result = None

    return {"status": "resumed", "now_playing": state.NOW_PLAYING, **_control_ack_payload(resume_result)}


@router.post("/stop")
def stop():
    """User stop with resume support; always return to idle visuals."""
    # Suppress autoplay after an explicit user stop until user starts playback again.
    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 3600 * 24

    pos = None
    dur = None
    stop_hint_now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    emit_stopped_hint = isinstance(stop_hint_now, dict) and bool(stop_hint_now.get("jellyfin_item_id"))
    preserve_resume = _can_preserve_closed_session()
    if preserve_resume:
        with player.MPV_LOCK:
            pos = player.mpv_get("time-pos")
            dur = player.mpv_get("duration")
        try:
            state.set_session_position(float(pos) if pos is not None else None)
        except Exception:
            state.set_session_position(None)

    if preserve_resume:
        # Preserve current item for play-button resume path.
        try:
            if isinstance(state.NOW_PLAYING, dict):
                np = dict(state.NOW_PLAYING)
                if pos is not None:
                    np["resume_pos"] = float(pos)
                np["closed"] = True
                np["closed_at"] = int(time.time())
                state.set_now_playing(np)
        except Exception:
            pass
    elif getattr(state, "SESSION_STATE", "idle") != "closed":
        try:
            state.set_now_playing(None)
        except Exception:
            pass
        try:
            state.set_session_position(None)
        except Exception:
            pass

    if preserve_resume:
        state.set_session_state("closed")
        with player.MPV_LOCK:
            player.stop_mpv()
        _jellyfin_emit_stopped_hint(pos, dur)
        return {"status": "stopped", "resume_available": bool(state.NOW_PLAYING), "position": pos}

    if emit_stopped_hint:
        _jellyfin_emit_stopped_hint(pos, dur)
    if getattr(state, "SESSION_STATE", "idle") != "closed":
        try:
            state.set_now_playing(None)
        except Exception:
            pass
        try:
            state.set_session_position(None)
        except Exception:
            pass
    state.set_session_state("idle")
    with player.MPV_LOCK:
        player.stop_mpv()
    return {"status": ("stopped" if emit_stopped_hint else "idle"), "resume_available": False, "position": pos}


def _session_playing_fast() -> tuple[str, bool, bool]:
    """Cheap playing-state estimate for high-frequency UI polling."""
    sess = str(getattr(state, "SESSION_STATE", "idle") or "idle").strip().lower() or "idle"
    paused = sess == "paused"
    playing = sess in ("playing", "paused")
    try:
        explicit_stop_hold = float(getattr(state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0) or 0.0) > (time.time() + 60.0)
    except Exception:
        explicit_stop_hold = False
    has_now_playing = isinstance(getattr(state, "NOW_PLAYING", None), dict)
    queue_length = len(getattr(state, "QUEUE", []) or [])
    natural_idle_hold = bool(getattr(player, "natural_idle_reset_holding", lambda: False)())
    if explicit_stop_hold and queue_length <= 0 and sess == "closed":
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
    if backend_ready is False and has_now_playing and sess_val not in ("idle", "closed"):
        return "degraded", "backend_not_ready"
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
            and (
                bool(getattr(player, "auto_next_transitioning", lambda: False)())
                or queue_length > 0
            )
        )
        transition_active = bool(manual_transition or queue_handoff_transition)
    except Exception:
        transition_active = False
    try:
        explicit_stop_hold = float(getattr(state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0) or 0.0) > (time.time() + 60.0)
    except Exception:
        explicit_stop_hold = False
    natural_idle_hold = bool(getattr(player, "natural_idle_reset_holding", lambda: False)())
    closed_stop_hold = explicit_stop_hold and queue_length <= 0 and sess == "closed"
    natural_idle_clear_hold = natural_idle_hold and queue_length <= 0 and (not has_now_playing) and sess in ("idle", "closed")
    payload: dict[str, object] = {
        "state": sess,
        "idle_dashboard_enabled": bool((state.get_settings() if hasattr(state, "get_settings") else {}).get("idle_dashboard_enabled", True)),
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
    if not bool(qt_runtime.get("selected")):
        runtime_state, runtime_reason = _derive_playback_runtime_state(
            sess=sess,
            playing=bool(payload.get("playing")),
            paused=bool(payload.get("paused")),
            has_now_playing=has_now_playing,
            queue_length=queue_length,
            transition_active=transition_active,
            telemetry_source="none",
            telemetry_freshness="unknown",
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

    field_map = (
        ("position", "mpv_runtime_time_pos"),
        ("duration", "mpv_runtime_duration"),
        ("volume", "mpv_runtime_volume"),
        ("mute", "mpv_runtime_mute"),
    )
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
        try:
            props = player.mpv_get_many(["pause", "volume", "mute", "time-pos", "duration"])
        except Exception:
            props = {}
        if isinstance(props, dict):
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
            fallback_paused = props.get("pause")
            if isinstance(fallback_paused, bool):
                payload["paused"] = fallback_paused
    if runtime_playing and not closed_stop_hold and not natural_idle_clear_hold:
        payload["playing"] = True
        payload["state"] = "paused" if bool(payload.get("paused")) else "playing"
    elif closed_stop_hold:
        payload["playing"] = False
        payload["paused"] = False
        payload["state"] = "closed"
    elif natural_idle_clear_hold:
        payload["playing"] = False
        payload["paused"] = False
        payload["state"] = "idle"
    transition_active = bool(transition_active or (
        bool(payload.get("playing"))
        and (not bool(payload.get("paused")))
        and (not runtime_playing)
        and str(payload.get("state") or "") != "closed"
        and (has_now_playing or queue_length > 0)
    ))
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
    )
    payload.update(state.update_playback_runtime_state(runtime_state, runtime_reason))
    return payload


@router.get("/playback/state")
def playback_state():
    """Lightweight playback state for overlay/browser polling."""
    return _playback_state_fast_snapshot()


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
            and (
                bool(getattr(player, "auto_next_transitioning", lambda: False)())
                or len(q) > 0
            )
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
    if explicit_stop_hold and (not q) and str(sess or "idle").strip().lower() == "closed":
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
        if sess == "paused" and isinstance(state.NOW_PLAYING, dict):
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
        "jellyfin_playback_mode": _effective_jellyfin_playback_mode(settings_snapshot),
        "pause_reason": state.get_pause_reason() if hasattr(state, "get_pause_reason") else None,
        "resume_available": resume_avail,
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

                if (time.time() - last_emit_ts) >= 15.0:
                    ping = _json.dumps({"type": "ping", "ts": time.time()}, separators=(",", ":"), ensure_ascii=False)
                    yield f"event: ping\ndata: {ping}\n\n"
                    last_emit_ts = time.time()
        finally:
            _UI_EVENT_SUBS.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/ui/events")
async def ui_events(request: Request):
    return await _ui_events_sse(request)


@router.get("/tv/status")
def tv_status():
    tv = state.get_tv_state() if hasattr(state, "get_tv_state") else {}
    cec_controller = player.cec_controller_status() if hasattr(player, "cec_controller_status") else {}
    return {
        "tv_power_state": tv.get("tv_power_status"),
        "active_source_phys_addr": tv.get("active_source_phys_addr"),
        "last_cec_event_ts": tv.get("last_event_ts"),
        "last_cec_event": tv.get("last_event"),
        "tv_control_method": tv.get("control_method", "cec-client"),
        "cec_controller": cec_controller,
        "confidence": tv.get("confidence", {}),
    }


@router.get("/devices")
def get_devices():
    return devices.discover()

@router.get("/settings")
def get_settings():
    raw = state.get_settings() if hasattr(state, "get_settings") else {}
    return _settings_for_client(raw)


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


def _settings_for_client(raw: dict | None) -> dict:
    """Return a UI-safe settings view without exposing secret values."""
    out = dict(raw or {})
    has_jf_pw = bool(str(out.get("jellyfin_password") or "").strip())
    yt_cookie_path = str(out.get("youtube_cookies_path") or "").strip()
    has_yt_cookies = bool(yt_cookie_path) and os.path.exists(yt_cookie_path)
    out["jellyfin_password"] = ""
    out["jellyfin_api_key"] = ""
    out["jellyfin_password_configured"] = has_jf_pw
    out["youtube_cookies_path"] = ""
    out["youtube_cookies_configured"] = has_yt_cookies
    out["youtube_use_invidious"] = bool(out.get("youtube_use_invidious"))
    out["youtube_invidious_base"] = str(out.get("youtube_invidious_base") or "").strip()
    out["idle_dashboard_enabled"] = bool(out.get("idle_dashboard_enabled", True))
    return out


def _sync_upload_env_from_settings(updated: dict | None) -> None:
    payload = updated if isinstance(updated, dict) else {}
    uploads = payload.get("uploads") if isinstance(payload.get("uploads"), dict) else {}
    try:
        max_size_gb = float(uploads.get("max_size_gb", 5.0))
    except Exception:
        max_size_gb = 5.0
    try:
        retention_hours = int(uploads.get("retention_hours", 24))
    except Exception:
        retention_hours = 24
    os.environ["RELAYTV_UPLOAD_MAX_SIZE_GB"] = str(max(0.25, min(500.0, round(max_size_gb, 2))))
    os.environ["RELAYTV_UPLOAD_RETENTION_HOURS"] = str(max(1, min(24 * 90, retention_hours)))


def _youtube_cookie_target_path() -> str:
    return (
        os.getenv("RELAYTV_YTDLP_COOKIES_UPLOAD_PATH")
        or os.getenv("RELAYTV_YTDLP_COOKIES")
        or os.getenv("YTDLP_COOKIES")
        or "/data/cookies.txt"
    ).strip()


def _normalize_invidious_base(value: object) -> str:
    base = str(value or "").strip()
    if not base:
        return ""
    base = base.rstrip("/")
    if not re.match(r"^https?://", base, flags=re.IGNORECASE):
        return ""
    return base


@router.post("/settings/youtube/cookies")
def upload_youtube_cookies(req: YouTubeCookiesUploadReq):
    text = str(req.cookies_text or "")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="cookies_text is empty")
    if len(normalized.encode("utf-8")) > (3 * 1024 * 1024):
        raise HTTPException(status_code=400, detail="cookies_text too large (max 3MB)")

    entries = [ln for ln in normalized.split("\n") if ln.strip() and not ln.lstrip().startswith("#")]
    if entries and not any("\t" in ln for ln in entries):
        raise HTTPException(
            status_code=400,
            detail="cookies.txt must be in Netscape format (tab-separated cookie rows)",
        )

    target = _youtube_cookie_target_path()
    if not target:
        raise HTTPException(status_code=400, detail="No youtube cookies target path configured")
    try:
        parent = os.path.dirname(target) or "."
        os.makedirs(parent, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(normalized)
            if not normalized.endswith("\n"):
                f.write("\n")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed writing cookies file: {exc}")

    updated = state.update_settings({"youtube_cookies_path": target}) if hasattr(state, "update_settings") else {"youtube_cookies_path": target}
    os.environ["RELAYTV_YTDLP_COOKIES"] = target
    return {
        "ok": True,
        "settings": _settings_for_client(updated),
    }


@router.post("/settings/youtube/cookies/clear")
def clear_youtube_cookies():
    updated = state.update_settings({"youtube_cookies_path": ""}) if hasattr(state, "update_settings") else {"youtube_cookies_path": ""}
    os.environ["RELAYTV_YTDLP_COOKIES"] = ""
    return {
        "ok": True,
        "settings": _settings_for_client(updated),
    }


def _uploaded_media_title(title: str | None, filename: str, public_name: str) -> str:
    return str(title or "").strip() or os.path.basename(filename or public_name) or public_name


def _uploaded_media_meta(upload_id: str, *, filename: str, public_name: str, title: str, content_type: str, size_bytes: int = 0) -> dict:
    return {
        "id": upload_id,
        "filename": os.path.basename(filename or public_name),
        "public_name": public_name,
        "stored_name": public_name,
        "title": title,
        "mime_type": content_type,
        "size_bytes": int(size_bytes),
        "created_unix": float(time.time()),
    }


def _progressive_fallback_toast() -> None:
    try:
        _push_overlay_toast(
            text="Upload still in progress. Waiting for full file for reliable playback.",
            duration=_playback_notification_display_sec(),
            level="warn",
            icon="clock",
        )
    except Exception:
        pass


async def _play_uploaded_item(item: dict, *, mode: str) -> dict:
    return await run_in_threadpool(player.play_item, item, False, False, False, mode)


def _enqueue_uploaded_media_url(url: str) -> dict:
    try:
        item = _smart_item_from_url(url or "", lightweight=True)
    except TypeError:
        item = _smart_item_from_url(url or "")
    with state.QUEUE_LOCK:
        state.QUEUE.append(item)
        qlen = len(state.QUEUE)
        queue_snapshot = list(state.QUEUE)
    state.persist_queue()
    try:
        player.prefetch_queue_item_stream(item)
    except Exception:
        pass
    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    try:
        _push_queue_added_toast_async(item, url or "item")
    except Exception:
        pass
    _ui_event_push_queue("add", queue=queue_snapshot, queue_length=qlen, source="ingest_media_enqueue")
    return {"status": "queued", "item": item, "queue_length": qlen, "now_playing": state.NOW_PLAYING}


@router.post("/ingest/media")
async def ingest_media(request: Request, file: UploadFile = File(...), title: str | None = Form(None)):
    settings_snapshot = state.get_settings() if hasattr(state, "get_settings") else {}
    upload_store.cleanup_uploads(settings_snapshot)
    filename = str(getattr(file, "filename", "") or "").strip()
    content_type = str(getattr(file, "content_type", "") or "").split(";", 1)[0].strip().lower()
    if not upload_store.is_allowed_upload(content_type, filename):
        raise HTTPException(
            status_code=400,
            detail="Unsupported media type; upload video/mp4, video/webm, audio/mpeg, or audio/mp4",
        )

    max_bytes = upload_store.max_upload_bytes(settings_snapshot)
    upload_id = upload_store.new_upload_id()
    public_name = upload_store.sanitize_upload_filename(filename, content_type=content_type)
    target_dir = upload_store.upload_dir(upload_id)
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="relaytv-upload-", suffix=".part", dir=target_dir)
    os.close(fd)
    final_path = os.path.join(target_dir, public_name)
    size_bytes = 0
    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise HTTPException(status_code=413, detail="Uploaded media exceeds configured storage size limit")
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if size_bytes <= 0:
            raise HTTPException(status_code=400, detail="Uploaded media is empty")
        os.replace(tmp_path, final_path)
        media_title = str(title or "").strip() or os.path.basename(filename or public_name) or public_name
        meta = {
            "id": upload_id,
            "filename": os.path.basename(filename or public_name),
            "public_name": public_name,
            "stored_name": public_name,
            "title": media_title,
            "mime_type": content_type,
            "size_bytes": int(size_bytes),
            "created_unix": float(time.time()),
        }
        upload_store.write_metadata(upload_id, meta)
        media_url = str(request.url_for("get_uploaded_media", upload_id=upload_id, filename=public_name))
        item = upload_store.build_item(meta, absolute_url=media_url)
        cleanup_result = upload_store.cleanup_uploads(settings_snapshot)
        return {
            "ok": True,
            "media_id": upload_id,
            "media_path": upload_store.upload_public_path(upload_id, public_name),
            "url": media_url,
            "item": item,
            "cleanup": cleanup_result,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("media_ingest_failed filename=%s error=%s", filename, exc)
        raise HTTPException(status_code=500, detail="Failed storing uploaded media") from exc
    finally:
        try:
            await file.close()
        except Exception:
            pass
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


@router.post("/ingest/media/enqueue")
async def ingest_media_enqueue(request: Request, file: UploadFile = File(...), title: str | None = Form(None)):
    created = await ingest_media(request, file=file, title=title)
    media_url = str(created.get("url") or "").strip() if isinstance(created, dict) else ""
    if not media_url:
        raise HTTPException(status_code=500, detail="Uploaded media missing playback URL")
    result = _enqueue_uploaded_media_url(media_url)
    out = dict(created)
    out.update({"action": "enqueue", "result": result})
    return out


@router.post("/ingest/media/play")
async def ingest_media_play(request: Request, file: UploadFile = File(...), title: str | None = Form(None)):
    settings_snapshot = state.get_settings() if hasattr(state, "get_settings") else {}
    upload_store.cleanup_uploads(settings_snapshot)
    filename = str(getattr(file, "filename", "") or "").strip()
    content_type = str(getattr(file, "content_type", "") or "").split(";", 1)[0].strip().lower()
    if not upload_store.is_allowed_upload(content_type, filename):
        raise HTTPException(
            status_code=400,
            detail="Unsupported media type; upload video/mp4, video/webm, audio/mpeg, or audio/mp4",
        )

    max_bytes = upload_store.max_upload_bytes(settings_snapshot)
    upload_id = upload_store.new_upload_id()
    public_name = upload_store.sanitize_upload_filename(filename, content_type=content_type)
    media_title = _uploaded_media_title(title, filename, public_name)
    meta = _uploaded_media_meta(
        upload_id,
        filename=filename,
        public_name=public_name,
        title=media_title,
        content_type=content_type,
        size_bytes=0,
    )
    upload_store.write_metadata(upload_id, meta)

    media_url = str(request.url_for("get_uploaded_media", upload_id=upload_id, filename=public_name))
    target_dir = upload_store.upload_dir(upload_id)
    os.makedirs(target_dir, exist_ok=True)
    final_path = os.path.join(target_dir, public_name)
    size_bytes = 0
    progressive_started = False
    fallback_reason = ""
    fallback_toast_sent = False
    playback_mode = "full_upload"
    now_playing = None
    stored_ok = False
    session = upload_store.new_play_session(meta)
    session["path"] = final_path
    upload_store.write_session(upload_id, session)
    try:
        with open(final_path, "wb") as out:
            while True:
                chunk_started = time.time()
                chunk = await file.read(1024 * 1024)
                chunk_finished = time.time()
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise HTTPException(status_code=413, detail="Uploaded media exceeds configured storage size limit")
                out.write(chunk)
                out.flush()
                os.fsync(out.fileno())

                session = upload_store.mark_session_progress(
                    session,
                    bytes_received=size_bytes,
                    chunk_size=len(chunk),
                    chunk_started_unix=chunk_started,
                    chunk_finished_unix=chunk_finished,
                    path=final_path,
                )
                upload_store.write_session(upload_id, session)

                if progressive_started or session.get("fallback_to_full_upload") is True:
                    continue

                ready, reason = upload_store.progressive_start_ready(meta, session)
                if ready:
                    item = upload_store.build_item(meta, absolute_url=media_url)
                    item["_local_stream_path"] = final_path
                    try:
                        now_playing = await _play_uploaded_item(item, mode="ingest_media_play")
                        progressive_started = True
                        playback_mode = "progressive"
                        session = upload_store.mark_session_progressive_started(session)
                        upload_store.write_session(upload_id, session)
                    except Exception as exc:
                        logger.warning("progressive_upload_start_failed id=%s error=%s", upload_id, exc)
                        fallback_reason = "start_failed"
                        session = upload_store.mark_session_fallback(session, fallback_reason)
                        upload_store.write_session(upload_id, session)
                elif reason not in ("buffering", "warming_up", "waiting_for_upload"):
                    fallback_reason = str(reason or "").strip() or "fallback_full_upload"
                    session = upload_store.mark_session_fallback(session, fallback_reason)
                    upload_store.write_session(upload_id, session)
                    _progressive_fallback_toast()
                    fallback_toast_sent = True

            out.flush()
            os.fsync(out.fileno())

        if size_bytes <= 0:
            raise HTTPException(status_code=400, detail="Uploaded media is empty")

        meta["size_bytes"] = int(size_bytes)
        upload_store.write_metadata(upload_id, meta)
        session = upload_store.mark_session_complete(session)
        upload_store.write_session(upload_id, session)

        item = upload_store.build_item(meta, absolute_url=media_url)
        if not progressive_started:
            if session.get("fallback_to_full_upload") is True and not fallback_toast_sent:
                _progressive_fallback_toast()
                fallback_toast_sent = True
            item["_local_stream_path"] = final_path
            now_playing = await _play_uploaded_item(item, mode="ingest_media_play")
            playback_mode = "full_upload"
            session = upload_store.mark_session_completed_playback(session, mode=playback_mode)
            upload_store.write_session(upload_id, session)

        cleanup_result = upload_store.cleanup_uploads(settings_snapshot)
        stored_ok = True
        return {
            "ok": True,
            "media_id": upload_id,
            "media_path": upload_store.upload_public_path(upload_id, public_name),
            "url": media_url,
            "item": upload_store.build_item(meta, absolute_url=media_url),
            "now_playing": now_playing,
            "playback_mode": playback_mode,
            "fallback_reason": fallback_reason,
            "cleanup": cleanup_result,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("media_ingest_play_failed filename=%s error=%s", filename, exc)
        raise HTTPException(status_code=500, detail="Failed storing uploaded media for playback") from exc
    finally:
        try:
            await file.close()
        except Exception:
            pass
        if not stored_ok:
            try:
                upload_store.delete_upload(upload_id)
            except Exception:
                pass


@router.get("/media/uploads/{upload_id}/{filename}", name="get_uploaded_media")
def get_uploaded_media(upload_id: str, filename: str):
    meta = upload_store.load_metadata(upload_id)
    if not isinstance(meta, dict):
        raise HTTPException(status_code=410, detail="Uploaded media expired or removed")
    expected_name = os.path.basename(str(meta.get("public_name") or meta.get("filename") or "").strip())
    if os.path.basename(filename) != expected_name:
        raise HTTPException(status_code=404, detail="Uploaded media not found")
    path = upload_store.stored_file_path(meta)
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=410, detail="Uploaded media expired or removed")
    return FileResponse(
        path,
        media_type=str(meta.get("mime_type") or "application/octet-stream"),
        filename=str(meta.get("filename") or expected_name),
        headers={"Cache-Control": "private, max-age=60"},
    )


@router.post("/settings")
def update_settings(req: SettingsReq):
    if hasattr(req, "model_dump"):
        patch = req.model_dump(exclude_unset=True)
    else:
        patch = req.dict(exclude_unset=True)
    apply_now = bool(patch.pop("apply_now", False))
    requested_keys = set(patch.keys())
    existing = state.get_settings() if hasattr(state, "get_settings") else {}
    candidate_invidious_base = _normalize_invidious_base(
        patch.get("youtube_invidious_base", (existing or {}).get("youtube_invidious_base", ""))
    )
    if "youtube_invidious_base" in requested_keys:
        patch["youtube_invidious_base"] = candidate_invidious_base
    candidate_use_invidious = bool(patch.get("youtube_use_invidious", (existing or {}).get("youtube_use_invidious")))
    if candidate_use_invidious and not candidate_invidious_base:
        raise HTTPException(status_code=400, detail="YouTube Invidious server is required when Invidious mode is enabled")
    # Update persisted settings
    updated = state.update_settings(patch) if hasattr(state, "update_settings") else patch
    # Also reflect into process environment for components that read env directly (yt-dlp format/args)
    if "quality_mode" in requested_keys and updated.get("quality_mode") is not None:
        os.environ["RELAYTV_QUALITY_MODE"] = str(updated.get("quality_mode") or "").strip()
    if "quality_cap" in requested_keys and updated.get("quality_cap") is not None:
        os.environ["RELAYTV_QUALITY_CAP"] = str(updated.get("quality_cap") or "").strip()
    if requested_keys.intersection({"ytdlp_format", "quality_mode"}):
        # In auto-profile mode we intentionally clear global YTDLP_FORMAT so
        # runtime policy can synthesize provider/profile-aware formats.
        if "quality_mode" in requested_keys:
            qmode = str(updated.get("quality_mode") or "").strip().lower()
        elif "ytdlp_format" in requested_keys:
            qmode = "manual"
        else:
            qmode = str(updated.get("quality_mode") or os.getenv("RELAYTV_QUALITY_MODE") or "").strip().lower()
        if qmode in ("auto", "auto_profile", "profile"):
            os.environ["YTDLP_FORMAT"] = ""
        elif updated.get("ytdlp_format") is not None:
            os.environ["YTDLP_FORMAT"] = str(updated.get("ytdlp_format") or "")
    if "youtube_cookies_path" in requested_keys and updated.get("youtube_cookies_path") is not None:
        os.environ["RELAYTV_YTDLP_COOKIES"] = str(updated.get("youtube_cookies_path") or "").strip()
    if requested_keys.intersection({"youtube_use_invidious", "youtube_invidious_base"}):
        use_invid = bool(updated.get("youtube_use_invidious"))
        invid_base = str(updated.get("youtube_invidious_base") or "").strip()
        os.environ["USE_INVIDIOUS"] = "true" if use_invid else "false"
        os.environ["INVIDIOUS_BASE"] = invid_base
    if "uploads" in requested_keys:
        _sync_upload_env_from_settings(updated)
        upload_store.cleanup_uploads(updated)
    if "audio_device" in requested_keys and updated.get("audio_device") is not None:
        configured_audio = str(updated.get("audio_device") or "").strip()
        # Explicit empty value means "Auto": clear process-level override.
        if not configured_audio:
            os.environ["MPV_AUDIO_DEVICE"] = ""
        else:
            os.environ["MPV_AUDIO_DEVICE"] = configured_audio
    if "video_mode" in requested_keys and updated.get("video_mode") is not None:
        os.environ["RELAYTV_VIDEO_MODE"] = str(updated.get("video_mode") or "auto")
    if "device_name" in requested_keys and updated.get("device_name") is not None:
        clean_name = str(updated.get("device_name") or "RelayTV")
        os.environ["RELAYTV_DEVICE_NAME"] = clean_name
        os.environ["RELAYTV_JELLYFIN_DEVICE_NAME"] = clean_name
        os.environ["RELAYTV_JELLYFIN_CLIENT_NAME"] = clean_name
    if "drm_connector" in requested_keys and updated.get("drm_connector") is not None:
        os.environ["RELAYTV_DRM_CONNECTOR"] = str(updated.get("drm_connector") or "")
    if "sub_lang" in requested_keys and updated.get("sub_lang") is not None:
        os.environ["RELAYTV_SUB_LANG"] = str(updated.get("sub_lang") or "")
    if "idle_dashboard_enabled" in requested_keys and updated.get("idle_dashboard_enabled") is not None:
        os.environ["RELAYTV_IDLE_DASHBOARD_ENABLED"] = "1" if bool(updated.get("idle_dashboard_enabled")) else "0"
    if "idle_qr_enabled" in requested_keys and updated.get("idle_qr_enabled") is not None:
        os.environ["RELAYTV_IDLE_QR_ENABLED"] = "1" if bool(updated.get("idle_qr_enabled")) else "0"
    if "idle_qr_size" in requested_keys and updated.get("idle_qr_size") is not None:
        os.environ["RELAYTV_IDLE_QR_SIZE"] = str(int(updated.get("idle_qr_size")))
    if "jellyfin_enabled" in requested_keys and updated.get("jellyfin_enabled") is not None:
        os.environ["RELAYTV_JELLYFIN_ENABLED"] = "1" if bool(updated.get("jellyfin_enabled")) else "0"
    if "jellyfin_server_url" in requested_keys and updated.get("jellyfin_server_url") is not None:
        os.environ["RELAYTV_JELLYFIN_SERVER_URL"] = str(updated.get("jellyfin_server_url") or "").strip()
    if "jellyfin_username" in requested_keys and updated.get("jellyfin_username") is not None:
        os.environ["RELAYTV_JELLYFIN_USERNAME"] = str(updated.get("jellyfin_username") or "").strip()
    if "jellyfin_password" in requested_keys and updated.get("jellyfin_password") is not None:
        os.environ["RELAYTV_JELLYFIN_PASSWORD"] = str(updated.get("jellyfin_password") or "").strip()
    if "jellyfin_user_id" in requested_keys and updated.get("jellyfin_user_id") is not None:
        os.environ["RELAYTV_JELLYFIN_USER_ID"] = str(updated.get("jellyfin_user_id") or "").strip()
    if "jellyfin_audio_lang" in requested_keys and updated.get("jellyfin_audio_lang") is not None:
        os.environ["RELAYTV_JELLYFIN_AUDIO_LANG"] = str(updated.get("jellyfin_audio_lang") or "").strip().lower()
    if "jellyfin_sub_lang" in requested_keys and updated.get("jellyfin_sub_lang") is not None:
        os.environ["RELAYTV_JELLYFIN_SUB_LANG"] = str(updated.get("jellyfin_sub_lang") or "").strip().lower()
    if "jellyfin_playback_mode" in requested_keys and updated.get("jellyfin_playback_mode") is not None:
        os.environ["RELAYTV_JELLYFIN_PLAYBACK_MODE"] = str(updated.get("jellyfin_playback_mode") or "auto").strip().lower()
    if requested_keys.intersection({"jellyfin_enabled", "jellyfin_server_url", "jellyfin_username", "jellyfin_password"}):
        # Settings-based Jellyfin integration is auth-session only.
        os.environ["RELAYTV_JELLYFIN_AUTH_ENABLED"] = "1"

    live_applied: list[str] = []
    live_apply_failed: list[str] = []
    playing_now = bool(player.is_playing())
    if (not apply_now) and playing_now:
        # Apply safe runtime settings without restarting playback.
        try:
            if "volume" in requested_keys and updated.get("volume") is not None:
                v = max(0.0, min(200.0, float(updated.get("volume"))))
                player.mpv_set("volume", v)
                live_applied.append("volume")
        except Exception:
            live_apply_failed.append("volume")
        try:
            if "audio_device" in requested_keys and updated.get("audio_device") is not None:
                cur_audio = str(os.getenv("MPV_AUDIO_DEVICE") or "").strip()
                if not cur_audio:
                    try:
                        cur_audio = str(getattr(player, "_effective_audio_device", lambda s=None: "")() or "").strip()
                    except Exception:
                        cur_audio = ""
                player.mpv_set("audio-device", (cur_audio or "auto"))
                live_applied.append("audio_device")
        except Exception:
            live_apply_failed.append("audio_device")
        try:
            if "sub_lang" in requested_keys and updated.get("sub_lang") is not None:
                cur_sub = str(updated.get("sub_lang") or "").strip()
                if cur_sub:
                    player.mpv_set("sub-auto", "fuzzy")
                else:
                    player.mpv_set("sub-auto", "no")
                player.mpv_set("slang", cur_sub)
                live_applied.append("sub_lang")
        except Exception:
            live_apply_failed.append("sub_lang")
        try:
            if requested_keys.intersection({"ytdlp_format", "quality_mode", "quality_cap"}):
                fmt_settings = dict(updated or {})
                if "quality_mode" not in fmt_settings and ("ytdlp_format" in requested_keys):
                    fmt_settings["quality_mode"] = "manual"
                cur_fmt = str(getattr(player, "_effective_ytdl_format", lambda s=None: "")(fmt_settings) or "").strip()
                player.mpv_set("options/ytdl-format", cur_fmt)
                live_applied.append("ytdlp_format")
        except Exception:
            live_apply_failed.append("ytdlp_format")
    if "device_name" in requested_keys and updated.get("device_name") is not None:
        try:
            jellyfin_receiver.set_device_identity(str(updated.get("device_name") or "RelayTV"))
            live_applied.append("device_name")
        except Exception:
            live_apply_failed.append("device_name")
    jellyfin_setting_keys = {
        "jellyfin_enabled",
        "jellyfin_server_url",
        "jellyfin_username",
        "jellyfin_password",
    }
    if requested_keys.intersection(jellyfin_setting_keys):
        try:
            jf_enabled = bool(updated.get("jellyfin_enabled"))
            jf_server = str(updated.get("jellyfin_server_url") or "").strip()
            jf_user = str(updated.get("jellyfin_username") or "").strip()
            jf_pw = str(updated.get("jellyfin_password") or "").strip()
            jf_device_name = str(updated.get("device_name") or "RelayTV")
            if jf_enabled and jf_server and jf_user and jf_pw:
                jellyfin_receiver.connect(
                    server_url=jf_server,
                    api_key="",
                    device_name=jf_device_name,
                )
                live_applied.extend(
                    k for k in sorted(requested_keys.intersection(jellyfin_setting_keys)) if k not in live_applied
                )
            elif jf_enabled and not jf_server:
                live_apply_failed.extend(
                    k for k in sorted(requested_keys.intersection(jellyfin_setting_keys)) if k not in live_apply_failed
                )
                jellyfin_receiver.mark_error("jellyfin_server_url_required")
            elif jf_enabled and (not jf_user or not jf_pw):
                live_apply_failed.extend(
                    k for k in sorted(requested_keys.intersection(jellyfin_setting_keys)) if k not in live_apply_failed
                )
                jellyfin_receiver.mark_error("jellyfin_login_required")
            else:
                jellyfin_receiver.disconnect()
                live_applied.extend(
                    k for k in sorted(requested_keys.intersection(jellyfin_setting_keys)) if k not in live_applied
                )
        except Exception:
            live_apply_failed.extend(
                k for k in sorted(requested_keys.intersection(jellyfin_setting_keys)) if k not in live_apply_failed
            )
    if "jellyfin_user_id" in requested_keys:
        try:
            jellyfin_receiver.refresh_catalog_profile()
            if "jellyfin_user_id" not in live_applied:
                live_applied.append("jellyfin_user_id")
        except Exception:
            if "jellyfin_user_id" not in live_apply_failed:
                live_apply_failed.append("jellyfin_user_id")
    if "jellyfin_playback_mode" in requested_keys and "jellyfin_playback_mode" not in live_applied:
        live_applied.append("jellyfin_playback_mode")

    now = None
    apply_performed = False
    apply_succeeded = False
    if apply_now:
        if hasattr(player, "restart_current"):
            apply_performed = True
            now = player.restart_current()
            apply_succeeded = now is not None

    restart_sensitive_keys = {"video_mode", "drm_connector", "drm_mode"}
    restart_sensitive_pending = [] if apply_now else sorted(k for k in requested_keys if k in restart_sensitive_keys)
    restart_recommended = (not apply_now) and playing_now and bool(restart_sensitive_pending)

    return {
        "ok": True,
        "playing": playing_now,
        "apply_now": apply_now,
        "apply_performed": apply_performed,
        "apply_succeeded": apply_succeeded,
        "settings": _settings_for_client(updated),
        "now_playing": now,
        "live_applied": live_applied,
        "live_apply_failed": live_apply_failed,
        "restart_sensitive_pending": restart_sensitive_pending,
        "restart_recommended": restart_recommended,
    }

@router.get("/ui")
def ui():
    html = r"""<!doctype html>
<html lang="en">
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="theme-color" content="#0b0f19" />
  <link rel="manifest" href="/manifest.json" />
  <meta name="mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <link rel="icon" type="image/svg+xml" href="/pwa/brand/logo.svg?v=2" />
  <link rel="shortcut icon" href="/pwa/brand/logo.svg?v=2" />
  <link rel="apple-touch-icon" href="/pwa/brand/logo.svg?v=2" />
  <title>RelayTV</title>
  <style>
    :root{
      --bg0:#070a12;
      --bg1:#0b0f19;
      --card: rgba(255,255,255,.08);
      --card2: rgba(255,255,255,.06);
      --stroke: rgba(255,255,255,.22);
      --text:#eaf0ff;
      --muted: rgba(234,240,255,.70);
      --muted2: rgba(234,240,255,.55);
      --shadow: 0 14px 40px rgba(0,0,0,.45);
      --radius: 18px;
      --btn: rgba(255,255,255,.18);
      --btnH: rgba(255,255,255,.26);
      --btnA: rgba(255,255,255,.34);
      --accent: rgba(120,180,255,.90);
      --good: rgba(120,255,190,.85);
      --warn: rgba(255,210,120,.90);
      --danger: rgba(255,120,140,.90);
    }

    /* Respect system light mode if you want; comment this block if you want always-dark */
    @media (prefers-color-scheme: light) {
      :root{
        --bg0:#edf2ff;
        --bg1:#f7f9ff;
        --card: rgba(255,255,255,.78);
        --card2: rgba(255,255,255,.70);
        --stroke: rgba(15,23,42,.14);
        --text:#0c1324;
        --muted: rgba(12,19,36,.72);
        --muted2: rgba(12,19,36,.58);
        --shadow: 0 14px 40px rgba(2,6,23,.12);
        --btn: rgba(255,255,255,.85);
        --btnH: rgba(255,255,255,.95);
        --btnA: rgba(235,240,255,.98);
      }
    }

    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      color: var(--text);
      background:
        radial-gradient(1200px 600px at 10% 0%, rgba(120,180,255,.22), transparent 55%),
        radial-gradient(900px 520px at 90% 10%, rgba(180,120,255,.16), transparent 60%),
        radial-gradient(800px 600px at 50% 110%, rgba(120,255,190,.12), transparent 55%),
        linear-gradient(180deg, var(--bg0), var(--bg1));
      padding: max(14px, env(safe-area-inset-top)) 14px max(16px, env(safe-area-inset-bottom));
    }

    .wrap{
      max-width: 920px;
      margin: 0 auto;
      display: grid;
      gap: 14px;
    }

    header{
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap: 10px;
      padding: 2px 2px 6px;
    }
    h1{
      margin: 0;
      font-weight: 700;
      letter-spacing: .2px;
      font-size: clamp(18px, 2.6vw, 26px);
    }
    .pill{
      display:flex;
      align-items:center;
      gap: 8px;
      padding: 8px 10px;
      border-radius: 999px;
      background: var(--card2);
      border: 1px solid var(--stroke);
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
      font-size: 13px;
      color: var(--muted);
    }

    .hdrRight{ display:flex; align-items:center; gap: 10px; }
    .jfLaunch{
      display: none;
      min-height: 0 !important;
      width: auto !important;
      padding: 8px 12px !important;
      border-radius: 12px !important;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .04em;
      text-transform: uppercase;
    }
    .jfLaunch.show{ display: inline-flex; }
    .hdrAddBtn{
      min-height: 0 !important;
      width: auto !important;
      padding: 8px 12px !important;
      border-radius: 12px !important;
      font-size: 20px;
      line-height: 1;
      font-weight: 800;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }
    .hdrMenuWrap{
      position: relative;
      display: inline-flex;
      align-items: center;
    }
    .hdrMenuBtn{
      min-height: 0 !important;
      width: auto !important;
      border-radius: 12px !important;
      padding: 8px 10px !important;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      font-size: 12px;
      color: var(--muted);
      background: var(--card2);
      border: 1px solid var(--stroke);
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
    }
    .hdrMenuBtn .menuLabel{
      text-transform: uppercase;
      letter-spacing: .05em;
      font-weight: 700;
      font-size: 12px;
      color: var(--muted2);
    }
    .hdrMenuBtn .menuIcon{
      font-size: 16px;
      line-height: 1;
      opacity: .9;
    }
    .hdrMenuBtn[aria-expanded="true"]{
      border-color: rgba(120,180,255,.72);
      box-shadow: 0 0 0 2px rgba(120,180,255,.20), 0 10px 24px rgba(0,0,0,.28);
    }
    .hdrMenuPanel{
      position: absolute;
      right: 0;
      top: calc(100% + 8px);
      min-width: 176px;
      z-index: 60;
      display: grid;
      gap: 6px;
      padding: 8px;
      border-radius: 12px;
      background: rgba(8, 16, 34, .96);
      border: 1px solid var(--stroke);
      box-shadow: 0 16px 38px rgba(0,0,0,.40);
    }
    .hdrMenuItem{
      min-height: 0 !important;
      width: 100% !important;
      justify-content: flex-start;
      border-radius: 10px !important;
      padding: 9px 10px !important;
      font-size: 13px;
      font-weight: 700;
      box-shadow: none !important;
      background: rgba(255,255,255,.08);
      border: 1px solid var(--stroke);
    }
    .hdrMenuItem:hover{
      transform: none;
      background: rgba(120,180,255,.22);
      border-color: rgba(120,180,255,.52);
    }
    .hdrMenuItem:active{ transform: none; }
    .iconBtn{
      height: 36px;
      width: 40px;
      display:flex;
      align-items:center;
      justify-content:center;
      border-radius: 12px;
      padding: 0;
    }

    /* Smaller icon buttons for header/modals */
    .iconBtn.sm{
      height: 34px;
      width: 36px;
      border-radius: 12px;
      font-size: 18px;
      line-height: 1;
    }

    /* Manual URL entry */
    .fieldRow{ display:flex; gap: 10px; align-items:center; margin-top: 12px; }
    .urlInput{
      width: 100%;
      padding: 12px 12px;
      border-radius: 14px;
      border: 1px solid var(--stroke);
      background: rgba(0,0,0,.12);
      color: var(--text);
      outline: none;
    }
    @media (prefers-color-scheme: light){
      .urlInput,
      .notifyInput{ background: rgba(255,255,255,.92); }
    }
    .helperTxt{ font-size: 12px; color: var(--muted2); margin-top: 8px; min-height: 16px; }
    .helperTxt.err{ color: #ff9aa8; }
    .helperTxt.ok{ color: #9df6b7; }
    .addDivider{
      height: 1px;
      margin: 16px 0 14px;
      background: linear-gradient(90deg, transparent, rgba(125,211,252,.42), transparent);
    }
    .notifySection{
      display: grid;
      gap: 10px;
    }
    .notifyTitle{
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .notifyGrid{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 150px 120px;
      gap: 10px;
      align-items: end;
    }
    @media (max-width: 720px){
      .notifyGrid{ grid-template-columns: 1fr; }
    }
    .notifyField{
      display: grid;
      gap: 6px;
      min-width: 0;
    }
    .notifyField label{
      font-size: 12px;
      color: var(--muted2);
      font-weight: 700;
    }
    .notifyInput{
      width: 100%;
      padding: 11px 12px;
      border-radius: 14px;
      border: 1px solid var(--stroke);
      background: rgba(0,0,0,.12);
      color: var(--text);
      outline: none;
    }
    textarea.notifyInput{
      min-height: 78px;
      resize: vertical;
      line-height: 1.35;
      font-family: inherit;
    }
    .notifyFile{
      font-size: 12px;
      color: var(--muted);
    }
    .notifyActions{
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:10px;
      flex-wrap:wrap;
    }
    .notifyActions button{
      min-height: 42px;
      padding: 10px 14px;
      border-radius: 14px;
    }

    .hidden{ display:none !important; }

    .modalBackdrop{
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,.48);
      backdrop-filter: blur(6px);
      -webkit-backdrop-filter: blur(6px);
      display:flex;
      align-items: center;
      justify-content: center;
      padding: 18px;
      z-index: 9999;
    }
    .modal{
      width: min(920px, 100%);
      max-height: min(80vh, 760px);
      overflow: auto;
      background: var(--card);
      border: 1px solid var(--stroke);
      border-radius: 18px;
      box-shadow: var(--shadow);
      padding: 14px;
    }
    .modalTop{ display:flex; align-items:center; justify-content:space-between; gap: 10px; }
    .modalTitle{ font-weight: 700; letter-spacing: .2px; }
    .modalBtns{ display:flex; gap: 10px; }
    .histList{ display:grid; gap: 10px; margin-top: 12px; }
    .histItem{
      position: relative;
      display:flex;
      gap: 10px;
      align-items: stretch;
      padding: 12px;
      border-radius: 18px;
      background: var(--card2);
      border: 1px solid var(--stroke);
      overflow: hidden;
    }
    .histItem.isUnavailable{
      opacity: .72;
      filter: saturate(.78);
    }
    .histProvBg{
      position:absolute;
      inset: 0;
      pointer-events:none;
      opacity: .12;
      display:flex;
      align-items:center;
      justify-content:flex-start;
      padding-left: 10px;
    }
    .histProvBg img{
      width: 84px;
      height: 84px;
      border-radius: 22px;
      border: 1px solid rgba(255,255,255,.10);
    }
    .histMeta{
      min-width:0;
      flex: 1 1 auto;
      position: relative;
      z-index: 2;
      display:flex;
      flex-direction: column;
      gap: 2px;
    }
    .histTitle{
      font-weight: 650;
      line-height: 1.2;
      min-width: 0;
      display:flex;
      align-items:flex-start;
      gap: 8px;
    }
    .histTitleText{
      min-width:0;
      overflow: hidden;
      text-overflow: ellipsis;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
    }
    .histSub{ font-size: 12px; color: var(--muted2); margin-top: 4px; word-break: break-word; }
    .histBtns{ margin-top: 8px; display:flex; gap: 8px; flex-wrap: wrap; }
    .histBtns button{ min-height: 0; padding: 8px 12px; font-size: 13px; border-radius: 12px; }
    .mediaBadge{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      min-height: 18px;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .02em;
      white-space: nowrap;
      border: 1px solid rgba(125, 220, 255, .34);
      background: rgba(0, 153, 255, .14);
      color: #dff5ff;
      box-shadow: inset 0 1px 0 rgba(255,255,255,.06);
    }
    .mediaBadge.unavailable{
      border-color: rgba(248, 113, 113, .32);
      background: rgba(220, 38, 38, .14);
      color: #ffd9df;
    }
    .dot{
      width:10px;height:10px;border-radius:999px;
      background: rgba(255,255,255,.35);
      box-shadow: 0 0 0 4px rgba(255,255,255,.06);
    }
    .dot.playing{ background: var(--good); box-shadow: 0 0 0 4px rgba(120,255,190,.15); }
    .dot.paused { background: var(--warn); box-shadow: 0 0 0 4px rgba(255,210,120,.15); }
    .dot.closed { background: var(--danger); box-shadow: 0 0 0 4px rgba(255,120,140,.15); }

    .card{
      background: var(--card);
      border: 1px solid var(--stroke);
      border-radius: var(--radius);
      padding: 14px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(14px);
      -webkit-backdrop-filter: blur(14px);
    }
/* Light mode: increase separation/contrast for tiles and buttons */
@media (prefers-color-scheme: light){
  .card{
    background: rgba(255,255,255,.78);
    border-color: rgba(15,23,42,.12);
    box-shadow: 0 10px 26px rgba(2,6,23,.10);
  }
  .qTile{
    background: rgba(255,255,255,.70);
    border-color: rgba(15,23,42,.16);
    box-shadow: 0 6px 18px rgba(2,6,23,.08);
  }
  .chip{
    background: rgba(255,255,255,.70);
    border-color: rgba(15,23,42,.14);
    box-shadow: inset 0 1px 0 rgba(255,255,255,.82);
  }
  .metaRow .chip,
  .nowBottomBar .chip{
    background: linear-gradient(180deg, rgba(59, 130, 246, .82), rgba(37, 99, 235, .76));
    border-color: rgba(29, 78, 216, .34);
    color: #f8fbff;
    box-shadow: inset 0 1px 0 rgba(255,255,255,.18), 0 8px 18px rgba(30, 64, 175, .16);
  }
  .progress{
    background: rgba(255,255,255,.70);
    border-color: rgba(15,23,42,.12);
    box-shadow: inset 0 1px 0 rgba(255,255,255,.78);
  }
  button{
    border-color: rgba(15,23,42,.16);
    box-shadow: 0 6px 14px rgba(2,6,23,.06);
  }
  button:hover{ box-shadow: 0 10px 18px rgba(2,6,23,.08); }
}

    .topgrid{
      display:grid;
      gap: 14px;
      grid-template-columns: 1.25fr .75fr;
    }
    @media (max-width: 760px){
      .topgrid{ grid-template-columns: 1fr; }
    }

    .label{
      font-size: 12px;
      letter-spacing: .4px;
      text-transform: uppercase;
      color: var(--muted2);
      margin-bottom: 8px;
    }

    .sectionTitle{
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap: 10px;
      padding: 10px 12px;
      border-radius: 14px;
      background: linear-gradient(180deg, rgba(255,255,255,.10), rgba(255,255,255,.06));
      border: 1px solid var(--stroke);
      box-shadow: 0 10px 26px rgba(0,0,0,.22);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .9px;
      text-transform: uppercase;
      color: var(--text);
      margin-bottom: 10px;
      position: relative;
      overflow: hidden;
    }
    .sectionTitle::before{
      content:"";
      position:absolute;
      left: 10px;
      top: 50%;
      transform: translateY(-50%);
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 0 0 6px rgba(120,180,255,.12);
      opacity: .95;
    }
    .sectionTitle{
      padding-left: 28px;
    }

    .nowCol{
      display:grid;
      gap: 14px;
    }
    .nowSkipBtn{
      margin-left: auto;
      width: 28px;
      height: 28px;
      flex: 0 0 auto;
      min-height: 0;
      border-radius: 999px;
      padding: 0;
      font-size: 14px;
      font-weight: 800;
      line-height: 1;
      border: 1px solid rgba(248, 113, 113, .72);
      background: rgba(220, 38, 38, .88);
      color: #fff;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 8px 16px rgba(127, 29, 29, .45);
    }
    .nowSkipBtn:hover{ background: rgba(239, 68, 68, .92); transform: none; }
    .nowSkipBtn:active{ transform: none; }
    .nowBottomBar{
      margin-top: 10px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }
    .nowPosChip{
      margin-right: auto;
    }
    .nowMetaActions{
      display:flex;
      align-items:center;
      justify-content:flex-end;
      gap: 10px;
      flex-wrap: wrap;
      margin-left: auto;
    }
    .nowLangBtn{
      min-height: 0 !important;
      height: 36px;
      padding: 0 12px !important;
      border-radius: 12px !important;
      font-size: 13px !important;
      font-weight: 700 !important;
      letter-spacing: .2px;
      background: rgba(56, 189, 248, .20);
      border-color: rgba(56, 189, 248, .55);
      box-shadow: 0 8px 18px rgba(7, 89, 133, .25) !important;
    }
    .nowLangBtn:hover{
      background: rgba(56, 189, 248, .26);
      border-color: rgba(56, 189, 248, .7);
      transform: none;
    }
    .langModal{
      width: min(560px, 100%);
    }
    .langList{
      margin-top: 10px;
      display: grid;
      gap: 8px;
    }
    .langOpt{
      min-height: 0 !important;
      justify-content: flex-start !important;
      gap: 8px !important;
      padding: 10px 12px !important;
      border-radius: 12px !important;
      font-size: 14px !important;
      text-align: left;
    }
    .langOpt.active{
      border-color: rgba(52, 211, 153, .65);
      background: rgba(16, 185, 129, .18);
    }
    .langOptIdx{
      opacity: .85;
      font-weight: 700;
      min-width: 34px;
    }
    .langOptMeta{
      opacity: .78;
      font-size: 12px;
      margin-left: auto;
    }

    /* Keep thumbnail backgrounds off the remote buttons tile */
    .remoteCard{
      background-image: none !important;
    }
    .remoteCard button:not(.danger){
      border-color: rgba(56, 189, 248, .58);
      background: linear-gradient(180deg, rgba(56, 189, 248, .96), rgba(37, 99, 235, .94));
      color: #eff6ff;
      box-shadow: 0 10px 22px rgba(30, 64, 175, .40);
    }
    .remoteCard button:not(.danger):hover{
      background: linear-gradient(180deg, rgba(56, 189, 248, .99), rgba(29, 78, 216, .96));
      box-shadow: 0 14px 28px rgba(30, 64, 175, .45);
      transform: translateY(-1px);
    }
    .remoteCard button:not(.danger):active{
      background: linear-gradient(180deg, rgba(37, 99, 235, .95), rgba(29, 78, 216, .92));
      transform: translateY(1px);
    }
    .remoteCard button:not(.danger) .bIcon{
      background: rgba(7, 31, 71, .35);
      border-color: rgba(191, 219, 254, .34);
    }
    .remoteVolumeRow{
      margin-top: 10px;
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      align-items: center;
      gap: 12px;
      padding: 10px 12px;
      border-radius: 16px;
      border: 1px solid rgba(56, 189, 248, .32);
      background: linear-gradient(180deg, rgba(9, 20, 42, .34), rgba(9, 20, 42, .24));
    }
    .remoteVolumeValue{
      min-width: 58px;
      font-size: 14px;
      font-weight: 800;
      letter-spacing: .02em;
      color: #eef6ff;
      text-align: left;
    }
    .remoteVolumeSlider{
      --remote-vol-pct: 50%;
      --remote-vol-fill-start: rgba(12, 74, 164, .98);
      --remote-vol-fill-end: rgba(30, 64, 175, .94);
      --remote-vol-track-start: rgba(191, 219, 254, .34);
      --remote-vol-track-end: rgba(96, 165, 250, .28);
      --remote-vol-track-border: rgba(191, 219, 254, .24);
      width: 100%;
      margin: 0;
      appearance: none;
      -webkit-appearance: none;
      background: transparent;
      cursor: pointer;
    }
    .remoteVolumeSlider:focus{ outline: none; }
    .remoteVolumeSlider::-webkit-slider-runnable-track{
      height: 8px;
      border-radius: 999px;
      background: linear-gradient(
        90deg,
        var(--remote-vol-fill-start) 0%,
        var(--remote-vol-fill-end) var(--remote-vol-pct),
        var(--remote-vol-track-start) var(--remote-vol-pct),
        var(--remote-vol-track-end) 100%
      );
      border: 1px solid var(--remote-vol-track-border);
    }
    .remoteVolumeSlider::-webkit-slider-thumb{
      -webkit-appearance: none;
      appearance: none;
      margin-top: -5px;
      width: 18px;
      height: 18px;
      border-radius: 999px;
      border: 1px solid rgba(125, 211, 252, .72);
      background: linear-gradient(180deg, rgba(56, 189, 248, .98), rgba(37, 99, 235, .95));
      box-shadow: 0 4px 12px rgba(30, 64, 175, .40), inset 0 1px 0 rgba(255,255,255,.28);
    }
    .remoteVolumeSlider::-moz-range-track{
      height: 8px;
      border-radius: 999px;
      background: linear-gradient(180deg, var(--remote-vol-track-start), var(--remote-vol-track-end));
      border: 1px solid var(--remote-vol-track-border);
    }
    .remoteVolumeSlider::-moz-range-progress{
      height: 8px;
      border-radius: 999px;
      background: linear-gradient(180deg, var(--remote-vol-fill-start), var(--remote-vol-fill-end));
    }
    .remoteVolumeSlider::-moz-range-thumb{
      width: 18px;
      height: 18px;
      border-radius: 999px;
      border: 1px solid rgba(125, 211, 252, .72);
      background: linear-gradient(180deg, rgba(56, 189, 248, .98), rgba(37, 99, 235, .95));
      box-shadow: 0 4px 12px rgba(30, 64, 175, .40), inset 0 1px 0 rgba(255,255,255,.28);
    }

    @media (prefers-color-scheme: light){
      .sectionTitle{
        background: linear-gradient(180deg, rgba(255,255,255,.98), rgba(255,255,255,.86));
        box-shadow: 0 10px 22px rgba(2,6,23,.10);
      }
      #nowTopCard.hasBg .sectionTitle{
        background: linear-gradient(180deg, rgba(255,255,255,.10), rgba(255,255,255,.06));
        border-color: rgba(255,255,255,.18);
        box-shadow: 0 10px 26px rgba(0,0,0,.22);
        color: #f7fbff;
      }
      #nowTopCard:not(.hasBg) .sectionTitle{
        background: linear-gradient(180deg, rgba(255,255,255,.97), rgba(243,248,255,.90));
        border-color: rgba(15,23,42,.14);
        box-shadow: 0 10px 22px rgba(2,6,23,.10);
        color: #0c1324;
      }
      .remoteVolumeRow{
        background: linear-gradient(180deg, rgba(255,255,255,.94), rgba(236, 246, 255, .88));
        border-color: rgba(37, 99, 235, .18);
      }
      .remoteVolumeValue{
        color: #0c1324;
      }
      .remoteVolumeSlider{
        --remote-vol-fill-start: rgba(8, 47, 120, .98);
        --remote-vol-fill-end: rgba(29, 78, 216, .94);
        --remote-vol-track-start: rgba(191, 219, 254, .78);
        --remote-vol-track-end: rgba(147, 197, 253, .66);
        --remote-vol-track-border: rgba(59, 130, 246, .20);
      }
      .remoteVolumeSlider::-webkit-slider-runnable-track{
        border-color: var(--remote-vol-track-border);
      }
      .remoteVolumeSlider::-moz-range-track{
        border-color: var(--remote-vol-track-border);
      }
      .remoteVolumeSlider::-webkit-slider-thumb{
        border-color: rgba(125, 211, 252, .84);
        background: linear-gradient(180deg, rgba(56, 189, 248, 1), rgba(37, 99, 235, .97));
      }
      .remoteVolumeSlider::-moz-range-thumb{
        border-color: rgba(125, 211, 252, .84);
        background: linear-gradient(180deg, rgba(56, 189, 248, 1), rgba(37, 99, 235, .97));
      }
    }
      .sectionTitle::before{
        box-shadow: 0 0 0 6px rgba(120,180,255,.16);
      }
    }

    .nowTitle{
      font-size: clamp(14px, 2.2vw, 18px);
      font-weight: 650;
      line-height: 1.25;
      margin: 0 0 6px 0;
    }
    .nowSubRow{
      display:flex;
      align-items:center;
      gap: 8px;
      min-width: 0;
    }
    .providerIcon{
      flex: 0 0 auto;
      width: 18px;
      height: 18px;
      display:grid;
      place-items:center;
      border-radius: 6px;
      background: var(--btn);
      border: 1px solid var(--stroke);
      font-size: 11px;
      line-height: 1;
    }

    .providerIcon img{
      width: 12px;
      height: 12px;
      border-radius: 3px;
      display:block;
    }

    .fav{
      width: 16px;
      height: 16px;
      border-radius: 5px;
      flex: 0 0 auto;
      display:block;
    }

    /* Background thumbnails */
    .hasBg{
      background-repeat: no-repeat !important;
      background-size: cover !important;
      background-position: center !important;
    }
    /* Text scrim for readability over dark/busy thumbnails */
    .scrim{
      background: rgba(0,0,0,.30);
      border: 1px solid rgba(255,255,255,.16);
      border-radius: 14px;
      padding: 8px 10px;
      backdrop-filter: blur(6px);
      -webkit-backdrop-filter: blur(6px);
    }
    .hasBg .nowTitle, .hasBg .qTitle{ text-shadow: 0 3px 12px rgba(0,0,0,.85); }
    .hasBg .muted, .hasBg .qUrl{ text-shadow: 0 2px 10px rgba(0,0,0,.85); }
    .hasBg .chip{
      background: rgba(0,0,0,.35);
      border-color: rgba(255,255,255,.18);
      backdrop-filter: blur(6px);
      -webkit-backdrop-filter: blur(6px);
    }
    /* Apply scrim to queue text block when a thumbnail background is present */
    li.hasBg .qBody{
      background: rgba(0,0,0,.30);
      border: 1px solid rgba(255,255,255,.16);
      border-radius: 14px;
      padding: 8px 10px;
      backdrop-filter: blur(6px);
      -webkit-backdrop-filter: blur(6px);
    }

@media (prefers-color-scheme: light){
  /* Keep metadata containers on image-backed cards dark-glass in light mode too. */
  .scrim{
    background: rgba(8, 13, 24, .42);
    border-color: rgba(255,255,255,.18);
    color: #f7fbff;
  }
  li.hasBg .qBody{
    background: rgba(8, 13, 24, .42);
    border-color: rgba(255,255,255,.18);
    color: #f7fbff;
  }
  .scrim #now,
  .scrim .nowSubRow,
  .scrim .muted,
  li.hasBg .qTitle,
  li.hasBg .qTitleText,
  li.hasBg .qChan,
  li.hasBg .qUrl{
    color: #f7fbff;
  }
  .scrim .muted,
  li.hasBg .qChan,
  li.hasBg .qUrl{
    color: rgba(234,240,255,.84);
  }
  .hasBg .nowTitle, .hasBg .qTitle{ text-shadow: 0 3px 12px rgba(0,0,0,.72); }
  .hasBg .muted, .hasBg .qUrl{ text-shadow: 0 2px 10px rgba(0,0,0,.60); }
}

    .muted{
      color: var(--muted);
      font-size: 13px;
      word-break: break-word;
    }
    .metaRow{
      margin-top: 8px;
      display:flex;
      flex-wrap:wrap;
      gap: 10px;
      color: var(--muted2);
      font-size: 14px;
    }



.progress{
  margin-top: 12px;
  height: 14px;
  border-radius: 999px;
  background: rgba(255,255,255,.10);
  border: 1px solid var(--stroke);
  overflow: hidden;
  cursor: pointer;
  position: relative;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
}
@media (prefers-color-scheme: light){
  .progress{ background: rgba(0,0,0,.06); }
}
.progressFill{
  height: 100%;
  width: 0%;
  background: var(--accent);
  border-radius: 999px;
}
    .chip{
      display:inline-flex;
      align-items:center;
      gap: 6px;
      padding: 8px 10px;
      border-radius: 999px;
      background: rgba(255,255,255,.10);
      border: 1px solid var(--stroke);
      color: var(--text);
      font-weight: 650;
      letter-spacing: .1px;
    }

    .chipSep{ opacity: .7; padding: 0 2px; }

    .mutePill{
      margin-left: 6px;
      font-size: 11px;
      padding: 3px 7px;
      border-radius: 999px;
      border: 1px solid rgba(255,120,140,.35);
      background: rgba(255,120,140,.12);
      color: var(--text);
      letter-spacing: .8px;
      font-weight: 800;
    }

    .controls{
      display:grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    .controls2{
      display:grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-top: 10px;
    }
    @media (max-width: 520px){
      .controls{ grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .controls2{ grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }

    button{
      appearance:none;
      border: 1px solid var(--stroke);
      background: var(--btn);
      color: var(--text);
      transition: background .15s ease, box-shadow .15s ease, transform .15s ease, border-color .15s ease;
      border-radius: 16px;
      padding: 14px 12px;
      font-size: 15px;
      font-weight: 600;
      display:flex;
      align-items:center;
      justify-content:center;
      gap: 10px;
      cursor:pointer;
      user-select:none;
      -webkit-tap-highlight-color: transparent;
      min-height: 52px;
      overflow: hidden;
    
      box-shadow: 0 10px 24px rgba(0,0,0,.38);
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
    }
    button > span{ min-width:0; }
    button > span:last-child{
      flex: 1 1 auto;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    button:hover{ background: var(--btnH); box-shadow: 0 14px 30px rgba(0,0,0,.48); transform: translateY(-1px); }
    button:active{ background: var(--btnA); transform: translateY(1px); }

    .bIcon{
      width: 28px;
      height: 28px;
      border-radius: 12px;
      display:grid;
      place-items:center;
      background: rgba(0,0,0,.18);
      border: 1px solid rgba(255,255,255,.10);
      font-size: 16px;
      line-height: 1;
    }
    @media (prefers-color-scheme: light){
      .bIcon{ background: rgba(0,0,0,.06); border-color: rgba(0,0,0,.10); }
    }

    .danger{ border-color: rgba(255,120,140,.35); }
    .danger .bIcon{ background: rgba(255,120,140,.12); border-color: rgba(255,120,140,.25); }

    
    /* Queue tiles */
    .queueList{
      margin: 0;
      padding: 0;
      list-style: none;
      display: grid;
      gap: 10px;
    }

    .qTile{
      position: relative;
      display:flex;
      align-items: stretch;
      gap: 10px;
      padding: 12px;
      border-radius: 18px;
      background: var(--card2);
      border: 1px solid var(--stroke);
      user-select: none;
    }
    .qTile.isUnavailable{
      opacity: .72;
      filter: saturate(.78);
    }

    .qTile.dragOver{
      outline: 2px solid rgba(120,180,255,.55);
      outline-offset: 2px;
    }

    .qProvBg{
      position:absolute;
      inset: 0;
      pointer-events:none;
      opacity: .12;
      filter: blur(.2px);
      display:flex;
      align-items:center;
      justify-content:flex-start;
      padding-left: 10px;
    }
    .qProvBg img{
      width: 84px;
      height: 84px;
      border-radius: 22px;
      border: 1px solid rgba(255,255,255,.10);
    }

    .qHandle{
      flex: 0 0 auto;
      width: 38px;
      height: 36px;
      display:flex;
      align-items:center;
      justify-content:center;
      align-self: center;
      border-radius: 12px;
      background: rgba(0, 153, 255, .16);
      border: 1px solid rgba(95, 205, 255, .34);
      color: rgba(125, 220, 255, .94);
      box-shadow: inset 0 1px 0 rgba(255,255,255,.08);
      cursor: grab;
      position: relative;
      z-index: 2;
      touch-action: none; /* allow pointermove without scrolling */
      user-select: none;
    }
    .qHandle:hover{
      background: rgba(0, 153, 255, .22);
      border-color: rgba(125, 220, 255, .46);
    }
    .qHandle:active{ cursor: grabbing; }

    body.noScroll{
      overflow: hidden;
      touch-action: none;
    }
    body.jfNoScroll{
      overflow: hidden;
      touch-action: none;
    }
    .qGrip{
      width: 16px;
      height: 16px;
      opacity: .9;
    }

    /* Override global button sizing for compact queue controls */
    .qDelBtn{
      flex: 0 0 auto;
      align-self: center;
      min-height: 0;
      padding: 0;
      width: 28px;
      height: 28px;
      border-radius: 999px;
      font-size: 14px;
      font-weight: 800;
      line-height: 1;
      border: 1px solid rgba(248, 113, 113, .72);
      background: rgba(220, 38, 38, .88);
      color: #fff;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      position: relative;
      z-index: 2;
      box-shadow: 0 8px 16px rgba(127, 29, 29, .45);
    }
    .qDelBtn:hover{ background: rgba(239, 68, 68, .92); transform: none; }
    .qDelBtn:active{ transform: none; }

    .qBody{
      flex: 1 1 auto;
      min-width: 0;
      display:flex;
      flex-direction: column;
      justify-content: center;
      gap: 2px;
      position: relative;
      z-index: 2;
    }

    .qTitle{
      color: var(--text);
      font-weight: 650;
      line-height: 1.2;
      display:flex;
      align-items:flex-start;
      gap: 8px;
      min-width: 0;
    }
    .qTitleText{
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
    }

    .qChan{
      font-size: 12px;
      color: var(--muted2);
      margin-top: 2px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .qUrl{
      color: var(--muted2);
      font-size: 12px;
      word-break: break-word;
      overflow: hidden;
      text-overflow: ellipsis;
      display: -webkit-box;
      -webkit-line-clamp: 1;
      -webkit-box-orient: vertical;
    }

.footerRow{
      display:flex;
      justify-content:space-between;
      gap: 10px;
      flex-wrap:wrap;
      margin-top: 8px;
      color: var(--muted2);
      font-size: 12px;
    }
    .link{
      color: var(--accent);
      text-decoration: none;
    }
.hint { font-size: 12px; opacity: 0.75; margin-top: 6px; }
.chk { display:flex; align-items:center; gap:8px; font-size: 14px; }
.toggleRow{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:14px;
  margin-top:10px;
  padding:12px 0 10px;
}
.toggleCopy{min-width:0;}
.toggleTitle{font-size:14px;font-weight:700;color:var(--txt);}
.toggleHint{margin-top:4px;font-size:12px;line-height:1.35;color:var(--muted);}
.toggleSwitch{
  position:relative;
  display:inline-flex;
  align-items:center;
  flex:0 0 auto;
  width:54px;
  height:30px;
}
.toggleSwitch input{position:absolute;opacity:0;width:1px;height:1px;}
.toggleTrack{
  position:absolute;
  inset:0;
  border-radius:999px;
  background:rgba(255,255,255,.13);
  border:1px solid rgba(255,255,255,.18);
  box-shadow:inset 0 1px 4px rgba(0,0,0,.25);
  transition:background .18s ease,border-color .18s ease,box-shadow .18s ease;
}
.toggleTrack::after{
  content:"";
  position:absolute;
  top:3px;
  left:3px;
  width:22px;
  height:22px;
  border-radius:999px;
  background:rgba(245,250,255,.96);
  box-shadow:0 4px 12px rgba(0,0,0,.35);
  transition:transform .18s ease,background .18s ease;
}
.toggleSwitch input:checked + .toggleTrack{
  background:linear-gradient(135deg, rgba(45,212,191,.92), rgba(56,189,248,.90));
  border-color:rgba(125,211,252,.70);
  box-shadow:0 0 0 3px rgba(56,189,248,.12), inset 0 1px 4px rgba(0,0,0,.18);
}
.toggleSwitch input:checked + .toggleTrack::after{transform:translateX(24px);background:#fff;}
.toggleSwitch input:focus-visible + .toggleTrack{outline:2px solid rgba(125,211,252,.90);outline-offset:3px;}
.modalBottom { display:flex; justify-content:flex-end; margin-top: 14px; }
.fieldLbl { display:block; font-size: 13px; opacity: 0.8; margin-bottom: 6px; }
.settingsGroup{
  margin-top: 12px;
  border: 1px solid var(--stroke);
  border-radius: 14px;
  background: linear-gradient(180deg, rgba(26, 52, 96, .34), rgba(12, 26, 54, .28));
  overflow: hidden;
}
.settingsGroup > summary{
  cursor: pointer;
  list-style: none;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  font-size: 14px;
  font-weight: 700;
  border-bottom: 1px solid transparent;
  user-select: none;
}
.settingsGroup > summary::-webkit-details-marker{ display:none; }
.settingsGroup > summary::after{
  content: '▾';
  margin-left: auto;
  opacity: .8;
}
.settingsGroup:not([open]) > summary::after{ content: '▸'; }
.settingsGroup[open] > summary{ border-bottom-color: var(--stroke); }
.settingsBody{ padding: 4px 14px 12px; }
.settingsBody .fieldRow{ margin-top: 10px; }
.sectionStatus{
  display:inline-flex;
  align-items:center;
  font-size: 11px;
  letter-spacing: .02em;
  text-transform: uppercase;
  border-radius: 999px;
  padding: 2px 8px;
  border: 1px solid rgba(255,255,255,.22);
  background: rgba(255,255,255,.08);
}
.sectionStatus.up{
  color: #c8ffd8;
  border-color: rgba(74, 222, 128, .62);
  background: rgba(16, 185, 129, .20);
}
.sectionStatus.down{
  color: #ffd6db;
  border-color: rgba(248, 113, 113, .62);
  background: rgba(239, 68, 68, .20);
}
.sectionStatus.warn{
  color: #ffe9bf;
  border-color: rgba(251, 191, 36, .62);
  background: rgba(245, 158, 11, .20);
}
.sectionStatus.unknown{
  color: #e3ecff;
  border-color: rgba(148, 163, 184, .55);
  background: rgba(148, 163, 184, .16);
}
.weatherLocStack{display:flex;flex-direction:column;gap:6px;flex:1 1 320px;min-width:0}
.weatherLocRow{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.weatherLocRow .input{flex:1 1 260px}
.weatherLocMeta{font-size:12px;opacity:.82;margin-top:6px;min-height:16px}
.aboutLinks{display:grid;gap:12px;margin-top:10px}
.aboutLink{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  color:var(--text);
  text-decoration:none;
  padding:12px 14px;
  border-radius:14px;
  border:1px solid var(--stroke);
  background:rgba(255,255,255,.06);
}
.aboutLink:hover{
  border-color:rgba(56,189,248,.62);
  background:rgba(56,189,248,.12);
}
.aboutLink small{
  display:block;
  margin-top:3px;
  color:var(--muted2);
  font-size:12px;
  line-height:1.35;
}
.aboutSupportImg{
  max-width:220px;
  width:100%;
  height:auto;
  display:block;
}
.aboutSupportLink{
  justify-content:center;
  background:rgba(255,221,0,.10);
  border-color:rgba(255,221,0,.28);
}
.inlineApplyRow{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:12px}
.inlineApplyMsg{font-size:12px;opacity:.9;min-height:16px}
.inlineApplyMsg.ok{color:#9df6b7}
.inlineApplyMsg.err{color:#ffb0b9}
.settingsBody .btn.electricBlue,
.modalBottom .btn.electricBlue{
  border-color: rgba(56, 189, 248, .72);
  background: linear-gradient(180deg, rgba(56, 189, 248, .98), rgba(37, 99, 235, .95));
  color: #eef6ff;
  box-shadow: 0 12px 28px rgba(30, 64, 175, .44), inset 0 1px 0 rgba(255, 255, 255, .28);
}
.settingsBody .btn.electricBlue:hover,
.modalBottom .btn.electricBlue:hover{
  border-color: rgba(125, 211, 252, .9);
  background: linear-gradient(180deg, rgba(56, 189, 248, 1), rgba(29, 78, 216, .97));
  box-shadow: 0 16px 34px rgba(30, 64, 175, .5), inset 0 1px 0 rgba(255, 255, 255, .33);
}
.settingsBody .btn.electricBlue:active,
.modalBottom .btn.electricBlue:active{
  background: linear-gradient(180deg, rgba(37, 99, 235, .96), rgba(29, 78, 216, .94));
  box-shadow: 0 8px 20px rgba(30, 64, 175, .38), inset 0 1px 0 rgba(255, 255, 255, .2);
}
.settingsBody .btn.electricBlue:focus-visible,
.modalBottom .btn.electricBlue:focus-visible{
  outline: none;
  box-shadow: 0 0 0 3px rgba(125, 211, 252, .4), 0 14px 30px rgba(30, 64, 175, .45);
}
.settingsBody .btn.electricBlue:disabled,
.modalBottom .btn.electricBlue:disabled{
  opacity: 1;
  cursor: not-allowed;
  transform: none;
  border-color: rgba(56, 189, 248, .42);
  background: linear-gradient(180deg, rgba(56, 189, 248, .56), rgba(37, 99, 235, .5));
  color: rgba(239, 246, 255, .9);
  box-shadow: 0 8px 20px rgba(30, 64, 175, .28), inset 0 1px 0 rgba(255, 255, 255, .16);
}
@media (prefers-color-scheme: light){
  .hdrMenuPanel{
    background: linear-gradient(180deg, rgba(186, 227, 255, .96), rgba(145, 202, 255, .92));
    border-color: rgba(37, 99, 235, .24);
    box-shadow: 0 18px 36px rgba(37, 99, 235, .18);
  }
  .hdrMenuItem{
    background: rgba(255,255,255,.72);
    border-color: rgba(37, 99, 235, .16);
    color: #0c1324;
  }
  .hdrMenuItem:hover{
    background: rgba(219, 239, 255, .92);
    border-color: rgba(56, 189, 248, .48);
  }
  .hdrMenuBtn{
    background: linear-gradient(180deg, rgba(255,255,255,.92), rgba(223, 238, 255, .88));
    border-color: rgba(37, 99, 235, .16);
    color: #0c1324;
  }
  .hdrMenuBtn .menuLabel,
  .hdrMenuBtn .menuIcon{
    color: rgba(12,19,36,.72);
  }
  .settingsGroup{
    background: linear-gradient(180deg, rgba(191, 228, 255, .62), rgba(227, 241, 255, .54));
    border-color: rgba(37, 99, 235, .18);
    box-shadow: inset 0 1px 0 rgba(255,255,255,.72), 0 8px 22px rgba(37, 99, 235, .08);
  }
  .settingsGroup > summary{
    background: linear-gradient(180deg, rgba(123, 196, 255, .24), rgba(255,255,255,.08));
  }
  .settingsBody select.input,
  .settingsBody .input[type="text"],
  .settingsBody .input[type="password"],
  .settingsBody .input[type="url"],
  .settingsBody .input[type="number"]{
    background: rgba(255,255,255,.96);
    color: #0c1324;
    border-color: rgba(15,23,42,.16);
    box-shadow: inset 0 1px 0 rgba(255,255,255,.82), 0 1px 2px rgba(15,23,42,.04);
  }
  .settingsBody select.input option{
    background: #ffffff;
    color: #0c1324;
  }
}

/* Jellyfin browse */
.jellyfinCard{ margin-top: 14px; }
.jfShell{
  position: fixed;
  inset: 0;
  z-index: 1200;
  padding: max(14px, env(safe-area-inset-top)) 14px max(16px, env(safe-area-inset-bottom));
  background:
    radial-gradient(1200px 600px at 10% 0%, rgba(120,180,255,.22), transparent 55%),
    radial-gradient(900px 520px at 90% 10%, rgba(180,120,255,.16), transparent 60%),
    radial-gradient(800px 600px at 50% 110%, rgba(120,255,190,.12), transparent 55%),
    linear-gradient(180deg, var(--bg0), var(--bg1));
  overflow: auto;
}
.jfShellInner{
  max-width: 1140px;
  margin: 0 auto;
  display: grid;
  gap: 12px;
}
.jfShellHead{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap: 10px;
}
.jfShellBack{
  min-height: 0;
  padding: 8px 12px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 700;
}
.jfShellTitle{
  font-size: 14px;
  color: var(--muted2);
  letter-spacing: .06em;
  text-transform: uppercase;
}
.jfCardHead{
  display:flex;
  align-items:center;
  gap: 8px;
  flex-wrap: nowrap;
}
.jfCardHeadLabel{
  white-space: nowrap;
  flex: 0 0 auto;
}
.jfCardSearchWrap{
  flex: 1 1 180px;
  width: clamp(150px, 54vw, 360px);
  min-width: 150px;
  max-width: 360px;
  margin-left: auto;
}
.jfCardSearch{
  width: 100%;
  height: 38px;
  padding: 0 15px;
  border-radius: 999px;
  border: 1px solid rgba(148, 163, 184, .30);
  background: linear-gradient(180deg, rgba(15, 23, 42, .90), rgba(30, 41, 59, .72));
  box-shadow: inset 0 1px 0 rgba(255,255,255,.05), 0 8px 22px rgba(2, 6, 23, .22);
  color: #edf4ff;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: .01em;
  text-transform: none;
}
.jfCardSearch::placeholder{
  color: rgba(219, 228, 245, .68);
}
.jfCardSearch:focus{
  outline: none;
  border-color: rgba(96, 165, 250, .72);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.05), 0 0 0 3px rgba(96, 165, 250, .16), 0 10px 24px rgba(30, 64, 175, .22);
}
@media (max-width: 420px){
  .jfCardHead{
    gap: 6px;
  }
  .jfCardSearchWrap{
    flex-basis: 140px;
    width: clamp(140px, 52vw, 220px);
    min-width: 140px;
  }
}
@media (max-width: 340px){
  .jfCardHead{
    flex-wrap: wrap;
  }
  .jfCardSearchWrap{
    width: 100%;
    max-width: none;
    min-width: 0;
    flex: 1 1 100%;
    margin-left: 0;
  }
}
.jfTabs{
  display:grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  width: 100%;
  padding: 6px;
  border-radius: 12px;
  border: 1px solid rgba(148, 163, 184, .30);
  background: rgba(8, 16, 34, .58);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.05);
}
.jfTabBtn{
  min-height: 0;
  width: 100%;
  padding: 9px 10px;
  border-radius: 9px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: .02em;
  text-transform: uppercase;
  border: 1px solid rgba(148, 163, 184, .24);
  background: rgba(22, 33, 58, .70);
  color: #dbe4f5;
  transition: background .16s ease, border-color .16s ease, color .16s ease, box-shadow .16s ease;
}
.jfTabBtn.active{
  border-color: rgba(56, 189, 248, .92);
  background: linear-gradient(180deg, rgba(56, 189, 248, .95), rgba(37, 99, 235, .95));
  color: #f8fbff;
  box-shadow: 0 0 0 1px rgba(56, 189, 248, .45), 0 8px 18px rgba(30, 64, 175, .35);
}
.jfTabBtn:hover{
  transform: none;
  background: rgba(36, 52, 88, .84);
}
.jfTabBtn.active:hover{
  background: linear-gradient(180deg, rgba(56, 189, 248, .98), rgba(37, 99, 235, .98));
}
.jfTabBtn:focus-visible{
  outline: 2px solid rgba(147, 197, 253, .95);
  outline-offset: 1px;
}
.jellyfinCard.jfOffline{
  border-color: rgba(248,113,113,.45);
  box-shadow: 0 0 0 1px rgba(239,68,68,.18) inset;
}
.jfTop{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-top: 10px; }
.jfSearch{ flex: 1 1 320px; }
.hiddenCtl{ display:none !important; }
.jfTop.hiddenCtl{ display:none; }
.jfSort{
  min-height: 0;
  height: 36px;
  border-radius: 10px;
  padding: 0 10px;
  border: 1px solid var(--stroke);
  background: rgba(255,255,255,.08);
  color: var(--text);
}
.jfAlphaIndicator{
  position: absolute;
  right: 20px;
  top: 10px;
  z-index: 4;
  font-size: 12px;
  font-weight: 700;
  border-radius: 999px;
  border: 1px solid var(--stroke);
  padding: 4px 9px;
  color: #dbeafe;
  background: rgba(2, 6, 23, .72);
  opacity: 0;
  transform: translateY(-4px);
  transition: opacity .18s ease, transform .18s ease;
  pointer-events: none;
}
.jfAlphaIndicator.show{
  opacity: 1;
  transform: translateY(0);
}
.jfStatus{
  font-size: 12px;
  color: var(--muted2);
  margin-left: auto;
}
.jfStatus.ok{ color:#9df6b7; }
.jfStatus.err{ color:#ffb0b9; }
.jfHint{
  width: 100%;
  font-size: 11px;
  color: var(--muted2);
}
.jfActionStatus{
  display: none;
  width: 100%;
  min-height: 16px;
  font-size: 12px;
  color: var(--muted2);
}
.jfActionStatus:not(:empty){ display:block; }
.jfActionStatus.ok{ color:#9df6b7; }
.jfActionStatus.err{ color:#ffb0b9; }
.jfUnavailable{
  border: 1px dashed var(--stroke);
  border-radius: 12px;
  padding: 14px;
  display: grid;
  gap: 10px;
  color: var(--muted2);
  background: rgba(255,255,255,.03);
}
.jfUnavailableTitle{
  font-size: 13px;
  color: #ffb0b9;
}
.jfGrid{
  margin-top: 10px;
  display: grid;
  gap: 12px;
  grid-template-columns: minmax(0, 1fr);
  position: relative;
  min-height: 220px;
}
.jfRows{ display: grid; gap: 10px; min-width: 0; }
.jfRowsPad{ padding-right: 0; }
.jfRow{ display:grid; gap: 6px; }
.jfRowTitle{ font-size: 13px; color: var(--muted2); letter-spacing: .02em; text-transform: uppercase; }
.jfScroller{
  display:flex;
  gap:10px;
  overflow-x:auto;
  padding-bottom: 4px;
  scrollbar-width: thin;
}
.jfRow.catalogNoTitle .jfRowTitle{ display:none; }
.jfCatalogScroller{
  display:grid;
  grid-template-columns: repeat(auto-fill, minmax(178px, 1fr));
  gap: 10px;
  overflow-y: auto;
  overflow-x: hidden;
  max-height: clamp(300px, calc(100vh - 310px), 75vh);
  padding-right: 6px;
  scrollbar-width: thin;
  scrollbar-color: rgba(148,163,184,.45) rgba(15,23,42,.30);
}
.jfCatalogScroller::-webkit-scrollbar{ width: 10px; }
.jfCatalogScroller::-webkit-scrollbar-thumb{
  background: rgba(148,163,184,.45);
  border-radius: 999px;
}
.jfCatalogScroller::-webkit-scrollbar-track{
  background: rgba(15,23,42,.30);
  border-radius: 999px;
}
.jfCatalogScroller .jfItem{
  width: auto;
  min-height: 216px;
  grid-template-rows: 120px minmax(96px, auto);
}
.jfCatalogScroller .jfItem:hover{
  transform: none;
}
.jfCatalogScroller .jfThumb{
  height: 120px;
}
.jfCatalogScroller .jfMeta{
  min-height: 96px;
  display: grid;
  grid-template-rows: minmax(30px, auto) 16px 28px;
  align-content: stretch;
  align-items: start;
  row-gap: 4px;
}
.jfCatalogScroller .jfItemTitle,
.jfCatalogScroller .jfItemSub{
  line-height: 1.25;
}
.jfCatalogScroller .jfItemTitle{
  white-space: normal;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  min-height: 34px;
}
.jfCatalogScroller .jfItemSub{
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-height: 16px;
}
.jfCatalogScroller .jfQuickRow{
  margin-top: 0;
  align-self: end;
}
.jfCatalogMovies .jfMeta{
  grid-template-rows: 18px 16px 28px;
}
.jfCatalogMovies .jfItemTitle{
  white-space: nowrap;
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  -webkit-line-clamp: unset;
  -webkit-box-orient: unset;
  min-height: 18px;
}
.jfCatalogTv .jfMeta{
  grid-template-rows: 18px 16px 28px;
  row-gap: 0;
}
.jfCatalogTv .jfItemTitle{
  white-space: nowrap;
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  -webkit-line-clamp: unset;
  -webkit-box-orient: unset;
  min-height: 18px;
}
.jfCatalogEpisodes{
  grid-template-columns: repeat(auto-fill, minmax(168px, 1fr));
  max-height: clamp(220px, calc(100vh - 420px), 52vh);
}
.jfItem{
  min-height: 0;
  padding: 0;
  width: 196px;
  border-radius: 14px;
  text-align: left;
  display: grid;
  grid-template-rows: 110px auto;
  background: var(--card2);
  border: 1px solid var(--stroke);
  cursor: pointer;
}
.jfItem:hover{ transform: translateY(-1px); }
.jfItem:focus-visible{
  outline: 2px solid rgba(120,180,255,.75);
  outline-offset: 2px;
}
.jfItem.selected{
  border-color: rgba(120,180,255,.72);
  box-shadow: 0 0 0 2px rgba(120,180,255,.25), 0 10px 24px rgba(0,0,0,.35);
}
.jfThumb{
  border-radius: 13px 13px 0 0;
  overflow: hidden;
  background: rgba(255,255,255,.06);
}
.jfThumb img{
  width: 100%;
  height: 100%;
  object-fit: cover;
  display:block;
}
.jfMeta{
  padding: 10px;
  display:grid;
  gap:4px;
}
.jfQuickRow{
  margin-top: 6px;
  display:flex;
  gap:6px;
}
.jfQuickBtn{
  min-height: 0;
  height: 28px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  border: 1px solid rgba(56, 189, 248, .72);
  background: linear-gradient(180deg, rgba(56, 189, 248, .95), rgba(37, 99, 235, .92));
  color: #f8fbff;
  box-shadow: 0 6px 14px rgba(30, 64, 175, .30);
}
.jfQuickBtn:hover{
  background: linear-gradient(180deg, rgba(56, 189, 248, .98), rgba(29, 78, 216, .96));
}
.jfQuickBtn:active{
  background: linear-gradient(180deg, rgba(37, 99, 235, .94), rgba(29, 78, 216, .90));
}
.jfQuickBtn:focus-visible{
  outline: 2px solid rgba(147, 197, 253, .95);
  outline-offset: 1px;
}
.jfItemTitle{
  font-size: 14px;
  font-weight: 650;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.jfItemSub{
  font-size: 12px;
  color: var(--muted2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.jfDetail{
  display: none;
  border: 1px solid var(--stroke);
  border-radius: 14px;
  background: linear-gradient(180deg, #132f56 0%, #0c2444 100%);
  padding: 10px;
  min-height: 220px;
  position: absolute;
  z-index: 1301;
  top: 0;
  right: 0;
  transform: none;
  width: min(430px, calc(100% - 8px));
  max-height: calc(100vh - 190px);
  overflow: auto;
  box-shadow: 0 20px 46px rgba(0,0,0,.45);
}
.jfGrid.detailOpen .jfRowsPad{ padding-right: min(440px, 38vw); }
.jfGrid.detailOpen .jfDetail{ display: block; }
.jfDetailBackdrop{
  display: none;
  position: fixed;
  inset: 0;
  z-index: 1300;
  background: rgba(2, 8, 24, .62);
  pointer-events: none;
}
.jfGrid.detailOpen .jfDetailBackdrop{ display: block; }
.jfDetailClose{
  min-height: 0;
  padding: 7px 11px;
  border-radius: 999px;
  font-size: 12px;
  margin-bottom: 8px;
}
.jfDetailThumbWrap{
  position: relative;
}
.jfDetailThumb{
  width: 100%;
  max-height: 220px;
  object-fit: cover;
  border-radius: 10px;
  border: 1px solid var(--stroke);
  background: rgba(255,255,255,.06);
}
.jfThumbNav{
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 34px;
  height: 34px;
  border-radius: 999px;
  border: 1px solid rgba(255,255,255,.30);
  background: rgba(0,0,0,.42);
  color: #fff;
  display:flex;
  align-items:center;
  justify-content:center;
  font-weight: 700;
  font-size: 16px;
  line-height: 1;
  padding: 0;
  z-index: 2;
}
.jfThumbNav:hover{
  background: rgba(0,0,0,.56);
  transform: translateY(-50%);
}
.jfThumbNav:active{ transform: translateY(-50%); }
.jfThumbNav:focus-visible{ transform: translateY(-50%); }
.jfThumbNav:disabled{
  opacity: .38;
  cursor: default;
}
.jfThumbNav.prev{ left: 10px; }
.jfThumbNav.next{ right: 10px; }
.jfDetailTitle{ font-size: 18px; font-weight: 700; margin-top: 8px; }
.jfDetailSub{ font-size: 13px; color: var(--muted2); margin-top: 4px; }
.jfDetailBody{ font-size: 13px; color: var(--text); margin-top: 8px; line-height: 1.35; }
.jfChips{
  margin-top: 8px;
  display:flex;
  flex-wrap:wrap;
  gap:6px;
}
.jfChip{
  font-size: 11px;
  color: var(--muted2);
  border: 1px solid var(--stroke);
  background: rgba(255,255,255,.05);
  border-radius: 999px;
  padding: 3px 8px;
  text-transform: uppercase;
  letter-spacing: .02em;
}
.jfActionRow{
  margin-top: 10px;
  display:grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap:8px;
}
.jfActionMsg{
  margin-top: 8px;
  font-size: 12px;
  color: var(--muted2);
  min-height: 16px;
}
.jfActionMsg.ok{ color:#9df6b7; }
.jfActionMsg.err{ color:#ffb0b9; }
@media (prefers-color-scheme: light){
  .jfShell{
    background:
      radial-gradient(1200px 600px at 10% 0%, rgba(59, 130, 246, .16), transparent 55%),
      radial-gradient(900px 520px at 90% 10%, rgba(14, 165, 233, .10), transparent 60%),
      radial-gradient(800px 600px at 50% 110%, rgba(16, 185, 129, .08), transparent 55%),
      linear-gradient(180deg, #eef4ff, #f8fbff);
  }
  .jellyfinCard{
    background: linear-gradient(180deg, rgba(255,255,255,.95), rgba(245, 249, 255, .92));
    border-color: rgba(15, 23, 42, .12);
    box-shadow: 0 20px 46px rgba(15, 23, 42, .10);
  }
  .jfShellTitle,
  .jfStatus,
  .jfHint,
  .jfRowTitle,
  .jfActionStatus,
  .jfDetailSub,
  .jfChip,
  .jfItemSub{
    color: rgba(15, 23, 42, .62);
  }
  .jfCardSearch{
    border-color: rgba(59, 130, 246, .18);
    background: linear-gradient(180deg, rgba(255,255,255,.98), rgba(239, 246, 255, .94));
    box-shadow: inset 0 1px 0 rgba(255,255,255,.65), 0 8px 22px rgba(15, 23, 42, .08);
    color: #0f172a;
  }
  .jfCardSearch::placeholder{
    color: rgba(15, 23, 42, .42);
  }
  .jfTabs{
    border-color: rgba(59, 130, 246, .16);
    background: rgba(255,255,255,.80);
    box-shadow: inset 0 1px 0 rgba(255,255,255,.72), 0 10px 24px rgba(15, 23, 42, .06);
  }
  .jfTabBtn{
    border-color: rgba(59, 130, 246, .14);
    background: rgba(255,255,255,.88);
    color: rgba(15, 23, 42, .72);
  }
  .jfTabBtn:hover{
    background: rgba(219, 234, 254, .88);
  }
  .jfAlphaIndicator{
    border-color: rgba(59, 130, 246, .18);
    color: #0f172a;
    background: rgba(255,255,255,.88);
    box-shadow: 0 8px 18px rgba(15, 23, 42, .08);
  }
  .jfUnavailable{
    border-color: rgba(15, 23, 42, .12);
    background: rgba(255,255,255,.72);
  }
  .jfCatalogScroller{
    scrollbar-color: rgba(148,163,184,.55) rgba(226,232,240,.82);
  }
  .jfCatalogScroller::-webkit-scrollbar-thumb{
    background: rgba(148,163,184,.55);
  }
  .jfCatalogScroller::-webkit-scrollbar-track{
    background: rgba(226,232,240,.82);
  }
  .jfItem{
    background: linear-gradient(180deg, rgba(255,255,255,.98), rgba(244, 249, 255, .94));
    border-color: rgba(15, 23, 42, .12);
    box-shadow: 0 12px 28px rgba(15, 23, 42, .08);
  }
  .jfItem:hover{
    box-shadow: 0 16px 34px rgba(15, 23, 42, .12);
  }
  .jfItem.selected{
    border-color: rgba(37, 99, 235, .48);
    box-shadow: 0 0 0 2px rgba(59, 130, 246, .16), 0 16px 36px rgba(15, 23, 42, .14);
  }
  .jfThumb{
    background: linear-gradient(180deg, rgba(226, 232, 240, .90), rgba(241, 245, 249, .98));
  }
  .jfMeta{
    background: linear-gradient(180deg, rgba(255,255,255,.28), rgba(255,255,255,.08));
  }
  .jfItemTitle,
  .jfDetailTitle,
  .jfDetailBody{
    color: #0f172a;
  }
  .jfDetail{
    border-color: rgba(15, 23, 42, .12);
    background: linear-gradient(180deg, rgba(255,255,255,.98), rgba(244, 248, 255, .95));
    box-shadow: 0 24px 56px rgba(15, 23, 42, .14);
  }
  .jfDetailThumb{
    background: rgba(226, 232, 240, .70);
    border-color: rgba(15, 23, 42, .10);
  }
  .jfThumbNav{
    border-color: rgba(15, 23, 42, .12);
    background: rgba(255,255,255,.82);
    color: #0f172a;
  }
  .jfThumbNav:hover{
    background: rgba(255,255,255,.94);
  }
  .jfChip{
    border-color: rgba(15, 23, 42, .10);
    background: rgba(255,255,255,.72);
  }
}
@media (max-width: 980px){
  .jfGrid{ grid-template-columns: 1fr; }
  .jfGrid.detailOpen .jfRowsPad{ padding-right: 0; }
  .jfShell.jfDetailLock{
    overflow: hidden;
    touch-action: none;
  }
  .jfGrid.detailOpen .jfDetailBackdrop{
    pointer-events: auto;
  }
  .jfCatalogScroller{
    grid-template-columns: repeat(auto-fill, minmax(154px, 1fr));
    max-height: min(60vh, 680px);
  }
  .jfCatalogScroller .jfItem{
    min-height: 188px;
    grid-template-rows: 102px minmax(84px, auto);
  }
  .jfCatalogScroller .jfThumb{
    height: 102px;
  }
  .jfCatalogEpisodes{
    grid-template-columns: repeat(auto-fill, minmax(142px, 1fr));
    max-height: min(45vh, 420px);
  }
  .jfSeasonWrap{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(116px, 1fr));
    gap: 8px;
    overflow-x: hidden;
    overflow-y: auto;
    max-height: min(34vh, 340px);
    padding-right: 4px;
  }
  .jfSeasonWrap .jfItem{
    width: auto;
    min-height: 168px;
    grid-template-rows: 96px minmax(72px, auto);
  }
  .jfSeasonWrap .jfThumb{
    height: 96px;
  }
  .jfSeasonWrap .jfMeta{
    min-height: 72px;
    grid-template-rows: 16px 14px 28px;
    row-gap: 2px;
    padding: 8px;
  }
  .jfSeasonWrap .jfItemTitle{
    font-size: 13px;
    white-space: nowrap;
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    -webkit-line-clamp: unset;
    -webkit-box-orient: unset;
    min-height: 16px;
  }
  .jfSeasonWrap .jfItemSub{
    font-size: 11px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    min-height: 14px;
  }
  .jfSeasonWrap .jfQuickBtn{
    width: 100%;
  }
  .jfDetail{
    position: fixed;
    top: 50%;
    left: 50%;
    right: auto;
    transform: translate(-50%, -50%);
    width: min(660px, calc(100vw - 18px));
    max-height: min(86vh, 760px);
    border-radius: 14px;
    padding-bottom: calc(10px + env(safe-area-inset-bottom) / 2);
  }
}
</style>
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
      <h1 id="appBrandName">RelayTV</h1>
      <div class="hdrRight">
        <button id="jellyfinOpenBtn" class="jfLaunch" title="Open Jellyfin" aria-label="Open Jellyfin">Jellyfin</button>
        <button id="addUrlBtn" class="hdrAddBtn" title="Add URL">＋</button>
        <div id="hdrMenuWrap" class="hdrMenuWrap">
          <button id="hdrMenuBtn" class="hdrMenuBtn" title="Menu" aria-label="Menu" aria-expanded="false" aria-haspopup="menu" aria-controls="hdrMenuPanel">
            <span class="menuLabel">MENU</span>
            <span class="menuIcon">☰</span>
          </button>
          <div id="hdrMenuPanel" class="hdrMenuPanel hidden" role="menu" aria-label="Header menu">
            <button id="histBtn" class="hdrMenuItem" role="menuitem" title="History">History</button>
            <button id="aboutBtn" class="hdrMenuItem" role="menuitem" title="About RelayTV">About</button>
            <button id="settingsBtn" class="hdrMenuItem" role="menuitem" title="Settings">Settings</button>
          </div>
        </div>
      </div>
    </header>

    <!-- Hidden by default: manual URL modal (opened via ＋ button) -->
    <div id="addBackdrop" class="modalBackdrop hidden" role="dialog" aria-modal="true">
      <div class="modal">
        <div class="modalTop">
          <div class="modalTitle">Add URL</div>
          <div class="modalBtns">
            <button id="addCloseBtn" class="iconBtn sm" title="Close" aria-label="Close">✕</button>
          </div>
        </div>

        <div class="fieldRow">
          <input id="addUrlInput" class="urlInput" type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="Paste a video URL…" />
          <button id="addPasteBtn" class="iconBtn sm" title="Paste from clipboard" aria-label="Paste">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <path d="M9 4h6a2 2 0 0 1 2 2v2H7V6a2 2 0 0 1 2-2Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>
              <path d="M7 8H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V10a2 2 0 0 0-2-2h-1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </button>
        </div>

        <div class="modalBtns" style="margin-top: 12px; justify-content:flex-end;">
          <button id="addQueueBtn" title="Add to queue">Queue</button>
          <button id="addPlayBtn" class="good" title="Play now">Play</button>
        </div>

        <div id="addHelperTxt" class="helperTxt" data-default="Tip: Clipboard paste works automatically on modern browsers (https/PWA/localhost only). “Queue” keeps the current playback.">Tip: Clipboard paste works automatically on modern browsers (https/PWA/localhost only). “Queue” keeps the current playback.</div>

        <div class="addDivider" aria-hidden="true"></div>

        <div id="notifySection" class="notifySection">
          <div class="notifyTitle">Send Toast Notification</div>
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
        </div>
      </div>
    </div>

    <!-- Hidden by default: history modal (opened via 🕘 button) -->
    <div id="histBackdrop" class="modalBackdrop hidden" role="dialog" aria-modal="true">
      <div class="modal">
        <div class="modalTop">
          <div class="modalTitle">History</div>
          <div class="modalBtns">
            <button id="histClearBtn" class="danger" title="Clear history">Clear</button>
            <button id="histCloseBtn" title="Close">Close</button>
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
          <div class="aboutLinks">
            <a id="aboutGithubLink" class="aboutLink" href="https://github.com/mcgeezy/relaytv" target="_blank" rel="noopener noreferrer">
              <span>
                <strong>GitHub Repository</strong>
                <small>Source code, issues, releases, and documentation.</small>
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
      <section id="nowTopCard" class="card">
        <div class="sectionTitle">NOW PLAYING <button id="nowSkipBtn" class="nowSkipBtn hidden" title="Stop current and play next up" aria-label="Play next up">✕</button></div>

        <div class="nowTitle">
          <div class="scrim" style="min-width:0;">
            <div id="now">Ready</div>
            <div class="nowSubRow">
              <span id="picon" class="providerIcon">🎞️</span>
              <div id="nowSub" class="muted" style="min-width:0;"></div>
            </div>
          </div>
        </div>

        <div id="progress" class="progress" title="Drag to seek (or tap)">
          <div id="progFill" class="progressFill"></div>
        </div>
        <div class="nowBottomBar">
          <span class="chip nowPosChip" title="Playback time">⏱ <span id="pos">--:--</span> <span class="chipSep">/</span> <span id="dur">--:--</span></span>
          <div class="nowMetaActions">
            <span class="chip" title="Queue length">📥 <span id="qlen">0</span> queued</span>
            <button id="nowLangBtn" class="nowLangBtn hidden" title="Audio language" aria-label="Audio language">Audio</button>
            <button id="nowSubLangBtn" class="nowLangBtn hidden" title="Subtitle language" aria-label="Subtitle language">Subs</button>
          </div>
        </div>
      </section>

      <section id="remoteCard" class="card remoteCard">
        <div class="sectionTitle">REMOTE</div>

        <div class="controls" style="margin-top:12px; grid-template-columns: repeat(2, minmax(0, 1fr));">
          <button onclick="post('/playback/toggle')"><span class="bIcon">⏯️</span><span>Play/Pause</span></button>
          <button onclick="post('/next')"><span class="bIcon">⏭️</span><span>Next</span></button>
          <button onclick="post('/mute')" id="muteBtn"><span class="bIcon">🔇</span><span>Mute</span></button>
          <button class="danger" onclick="post('/close')" id="closeBtn"><span class="bIcon">✖️</span><span>Close</span></button>
        </div>

        <div class="controls2">
          <button onclick="post('/seek',{sec:-10})"><span class="bIcon">↩️</span><span>-10s</span></button>
          <button onclick="post('/seek',{sec:+30})"><span class="bIcon">↪️</span><span>+30s</span></button>
        </div>
        <div class="remoteVolumeRow">
          <div id="remoteVolValue" class="remoteVolumeValue">--%</div>
          <input id="remoteVolSlider" class="remoteVolumeSlider" type="range" min="0" max="200" step="1" value="100" aria-label="Volume" />
        </div>
      </section>
    </div>

      <aside class="card">
        <div class="sectionTitle">QUEUE</div>
        <ol id="queue" class="queueList"></ol>
        <div class="footerRow">
          <span>Tip: Share again while playing to enqueue</span>
          <span><a class="link" href="#" onclick="post('/clear');return false;">clear</a></span>
        </div>
      </aside>
    </div>

    <div id="jellyfinShell" class="jfShell hidden" aria-hidden="true">
      <div class="jfShellInner">
        <div class="jfShellHead">
          <button id="jfShellBackBtn" class="jfShellBack">← Back</button>
          <div class="jfShellTitle">Jellyfin</div>
        </div>
        <div class="jfTabs" role="tablist" aria-label="Jellyfin sections">
          <button class="jfTabBtn active" id="jfTabDashboardBtn" data-jf-tab="dashboard" role="tab" aria-selected="true" aria-controls="jellyfinCard" tabindex="0">Dashboard</button>
          <button class="jfTabBtn" id="jfTabMoviesBtn" data-jf-tab="movies" role="tab" aria-selected="false" aria-controls="jellyfinCard" tabindex="-1">Movies</button>
          <button class="jfTabBtn" id="jfTabTvBtn" data-jf-tab="tv" role="tab" aria-selected="false" aria-controls="jellyfinCard" tabindex="-1">TV</button>
        </div>
        <section id="jellyfinCard" class="card jellyfinCard" role="tabpanel">
          <div class="sectionTitle jfCardHead">
            <span class="jfCardHeadLabel">JELLYFIN</span>
            <div class="jfCardSearchWrap">
              <input id="jfSearchInput" class="input jfCardSearch" placeholder="Search Jellyfin titles…" aria-label="Search Jellyfin" />
            </div>
          </div>
          <div class="jfTop">
            <select id="jfSortSelect" class="jfSort hiddenCtl" aria-label="Sort catalog"></select>
            <span id="jfStatus" class="jfStatus">Loading…</span>
            <div class="jfHint">Arrows navigate • 1/2/3 switch tabs • Enter opens detail • P/N/L/R trigger Play/Next/Last/Resume</div>
            <div id="jfActionStatus" class="jfActionStatus" aria-live="polite"></div>
          </div>
          <div id="jfGrid" class="jfGrid">
            <div class="jfRowsPad">
              <div id="jfRows" class="jfRows"></div>
            </div>
            <div id="jfDetailBackdrop" class="jfDetailBackdrop" aria-hidden="true"></div>
            <aside id="jfDetail" class="jfDetail muted">Select a Jellyfin item to view details.</aside>
            <div id="jfAlphaIndicator" class="jfAlphaIndicator" aria-hidden="true">A</div>
          </div>
        </section>
      </div>
    </div>
  </div>

<script>
function _fetchWithTimeout(url, opts, timeoutMs){
  const ms = Number(timeoutMs || 0);
  if (!(Number.isFinite(ms) && ms > 0) || typeof AbortController === 'undefined'){
    return fetch(url, opts || {});
  }
  const controller = new AbortController();
  const finalOpts = Object.assign({}, opts || {}, {signal: controller.signal});
  const timer = setTimeout(() => {
    try { controller.abort(); } catch(_e) {}
  }, ms);
  return fetch(url, finalOpts).finally(() => clearTimeout(timer));
}

async function post(path, body) {
  try {
    await _fetchWithTimeout(path, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: body ? JSON.stringify(body) : '{}'
    }, 1800);
  } catch(_e) {
    // Keep controls responsive even if a transient request stalls.
  }
  refresh().catch(() => null);
}

// --- Manual URL modal + clipboard helpers
async function clipboardText(){
  try {
    // Clipboard read is restricted in many contexts (must be secure context + user gesture).
    if (!window.isSecureContext) return "";
    if (!navigator.clipboard || !navigator.clipboard.readText) return "";
    return (await navigator.clipboard.readText()) || "";
  } catch (_) {
    return "";
  }
}

function looksLikeUrl(s){
  if (!s) return false;
  const t = String(s).trim();
  return /^https?:\/\//i.test(t) || /^www\./i.test(t);
}

function normalizeUrl(s){
  const t = String(s || "").trim();
  if (!t) return "";
  if (/^https?:\/\//i.test(t)) return t;
  if (/^www\./i.test(t)) return "https://" + t;
  return t;
}

function _setAddHelper(msg, kind){
  const el = document.getElementById('addHelperTxt');
  if (!el) return;
  el.classList.remove('err', 'ok');
  if (kind === 'err' || kind === 'ok') el.classList.add(kind);
  if (String(msg || '').trim()) {
    el.textContent = String(msg).trim();
    return;
  }
  el.textContent = String(el.getAttribute('data-default') || '').trim();
}

async function openAddUrl(){
  const bd = document.getElementById('addBackdrop');
  const inp = document.getElementById('addUrlInput');
  if (!bd || !inp) return;
  if (!bd.classList.contains('hidden')) return;
  bd.classList.remove('hidden');
  _uiPushLayer();
  _setAddHelper('', '');
  const clip = await clipboardText();
  if (looksLikeUrl(clip) && !inp.value.trim()) inp.value = normalizeUrl(clip);
  inp.focus();
  inp.select();
}

function closeAddUrl(opts){
  const bd = document.getElementById('addBackdrop');
  if (!bd) return;
  const fromNav = !!(opts && opts.fromNav);
  if (!fromNav && !bd.classList.contains('hidden') && __uiNavDepth > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  bd.classList.add('hidden');
}

async function pasteIntoAddUrl(){
  const inp = document.getElementById('addUrlInput');
  if (!inp) return;
  let clip = '';
  let blockedReason = '';
  if (!window.isSecureContext) {
    blockedReason = 'Paste unavailable here. Use HTTPS/localhost (secure context) to access clipboard.';
  } else if (!navigator.clipboard || !navigator.clipboard.readText) {
    blockedReason = 'Paste unavailable in this browser/runtime (Clipboard API not exposed).';
  } else {
    try {
      clip = (await navigator.clipboard.readText()) || '';
    } catch (_e) {
      blockedReason = 'Clipboard access blocked. Allow clipboard permissions and retry.';
    }
  }
  if (clip) {
    inp.value = normalizeUrl(clip);
    _setAddHelper('Pasted from clipboard.', 'ok');
  } else if (blockedReason) {
    _setAddHelper(blockedReason, 'err');
  } else {
    _setAddHelper('Clipboard is empty.', '');
  }
  inp.focus();
  inp.select();
}

async function submitAddUrl(mode){
  const inp = document.getElementById('addUrlInput');
  if (!inp) return;
  const url = normalizeUrl(inp.value);
  if (!looksLikeUrl(url)) {
    alert('Please enter a valid URL (starting with http(s):// or www.)');
    inp.focus();
    return;
  }

  if (mode === 'queue') {
    await post('/enqueue', {url});
  } else {
    await post('/play_now', {url, preserve_current:true, preserve_to:'queue_front', resume_current:true, reason:'add_menu'});
  }
  closeAddUrl();
}

function _setNotifyHelper(msg, kind){
  const el = document.getElementById('notifyHelperTxt');
  if (!el) return;
  el.classList.remove('err', 'ok');
  if (kind === 'err' || kind === 'ok') el.classList.add(kind);
  el.textContent = String(msg || '').trim();
}

function readNotifyImageDataUrl(file){
  return new Promise((resolve, reject) => {
    if (!file) {
      resolve('');
      return;
    }
    if (!String(file.type || '').toLowerCase().startsWith('image/')) {
      reject(new Error('Please choose an image file.'));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new Error('Could not read selected image.'));
    reader.readAsDataURL(file);
  });
}

async function submitNotificationToast(){
  const textEl = document.getElementById('notifyTextInput');
  const imageEl = document.getElementById('notifyImageInput');
  const imageUrlEl = document.getElementById('notifyImageUrlInput');
  const posEl = document.getElementById('notifyPositionSelect');
  const durEl = document.getElementById('notifyDurationInput');
  const sendBtn = document.getElementById('notifySendBtn');
  const text = String(textEl?.value || '').trim();
  if (!text) {
    _setNotifyHelper('Enter notification text first.', 'err');
    if (textEl) textEl.focus();
    return;
  }
  const position = String(posEl?.value || 'top-left').trim() || 'top-left';
  let duration = Number(durEl?.value || 5);
  if (!Number.isFinite(duration)) duration = 5;
  duration = Math.min(30, Math.max(0.8, duration));
  const payload = {text, position, duration, level:'info', icon:'info'};
  try {
    if (sendBtn) sendBtn.disabled = true;
    _setNotifyHelper('Sending…', '');
    const file = imageEl && imageEl.files && imageEl.files.length ? imageEl.files[0] : null;
    const imageUrl = file ? await readNotifyImageDataUrl(file) : String(imageUrlEl?.value || '').trim();
    if (imageUrl) {
      const normalizedImageUrl = normalizeUrl(imageUrl);
      if (!/^(https?:\/\/|\/|data:image\/)/i.test(normalizedImageUrl)) {
        throw new Error('Image URL must start with http(s)://, www., /, or data:image/.');
      }
      payload.image_url = normalizedImageUrl;
    }
    const r = await _fetchWithTimeout('/overlay', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    }, 5000);
    if (!r.ok) {
      let detail = '';
      try {
        const body = await r.json();
        detail = body && body.detail ? (typeof body.detail === 'string' ? body.detail : (body.detail.message || body.detail.error || '')) : '';
      } catch(_e) {}
      throw new Error(detail || `Notification failed (${r.status})`);
    }
    _setNotifyHelper('Notification sent.', 'ok');
    if (imageEl) imageEl.value = '';
    if (imageUrlEl) imageUrlEl.value = '';
  } catch (e) {
    _setNotifyHelper(e && e.message ? e.message : 'Notification failed.', 'err');
  } finally {
    if (sendBtn) sendBtn.disabled = false;
  }
}

function fmtTime(s){
  if (s == null || isNaN(s)) return '--:--';
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s/3600);
  const m = Math.floor((s%3600)/60);
  const sec = s%60;
  return (h>0?`${h}:`:'') + String(m).padStart(2,'0') + ':' + String(sec).padStart(2,'0');
}

let __lastStatus = null;
let __lastStatusFullFetchTs = 0;
let __uiEventSource = null;
let __uiEventSourceLastTs = 0;
let __uiEventReconnectTimer = 0;
let __remoteVolumeKnownValue = null;

function _mergePlaybackStateIntoStatus(base, fast){
  const out = Object.assign({}, (base && typeof base === 'object') ? base : {});
  const src = (fast && typeof fast === 'object') ? fast : null;
  if (!src) return out;
  [
    'state',
    'playing',
    'paused',
    'queue_length',
    'playback_telemetry_source',
    'playback_telemetry_freshness',
    'position',
    'duration',
    'volume',
    'mute',
  ].forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(src, key)) out[key] = src[key];
  });
  if (Object.prototype.hasOwnProperty.call(src, 'has_now_playing')) {
    out.has_now_playing = !!src.has_now_playing;
    if (!out.has_now_playing && !src.playing && !src.paused) {
      out.now_playing = null;
      out.resume_available = false;
    }
  }
  return out;
}

function _shouldRefreshFullStatus(st, fast){
  if (!st || typeof st !== 'object') return true;
  if (!fast || typeof fast !== 'object') return true;
  const now = Date.now();
  const fastPlaying = !!fast.playing;
  const maxAgeMs = fastPlaying ? 5000 : 12000;
  if ((now - __lastStatusFullFetchTs) > maxAgeMs) return true;
  const hasNow = _hasNowPlayingItem(st, st.now_playing || {});
  if (!!fast.has_now_playing !== !!hasNow) return true;
  if (Array.isArray(st.queue) && Number(st.queue.length || 0) !== Number(fast.queue_length || 0)) return true;
  if (!Array.isArray(st.queue) && Number(fast.queue_length || 0) > 0) return true;
  return false;
}

async function _fetchFastPlaybackState(){
  const r = await _fetchWithTimeout('/playback/state', {cache:'no-store'}, 900);
  if (!r.ok) throw new Error(`playback_state ${r.status}`);
  return await r.json();
}

async function _fetchFullStatus(){
  const r = await _fetchWithTimeout('/status', {cache:'no-store'}, 1600);
  if (!r.ok) throw new Error(`status ${r.status}`);
  const st = await r.json();
  __lastStatusFullFetchTs = Date.now();
  return st;
}

function _uiEventMarkAlive(){
  __uiEventSourceLastTs = Date.now();
}

function _uiEventHealthy(){
  return !!(__uiEventSource && ((Date.now() - __uiEventSourceLastTs) < 10000));
}

function _scheduleUiEventReconnect(){
  if (__uiEventReconnectTimer) return;
  __uiEventReconnectTimer = window.setTimeout(() => {
    __uiEventReconnectTimer = 0;
    connectUiEventStream();
  }, 2000);
}

function _parseUiEventPayload(ev){
  try {
    return JSON.parse(ev && ev.data ? ev.data : '{}');
  } catch (_e) {
    return null;
  }
}

// Queue drag state (prevents UI refresh from nuking DOM mid-drag)
let __draggingQueue = false;

let __dragStartTs = 0;
let __queueDnDBound = false;
let __queueDnDCleanup = null;

function _queueTileFromPoint(x, y){
  const el = document.elementFromPoint(x, y);
  if (!el) return null;
  return el.closest ? el.closest('.qTile') : null;
}

function bindQueuePointerDnD(){
  if (__queueDnDBound) return;
  __queueDnDBound = true;

  const ol = document.getElementById('queue');
  if (!ol) return;

  let startFrom = null;
  let overTo = null;
  let startX = 0, startY = 0;
  let active = false;
  const MOVE_PX = 4;

  const cleanup = () => {
    __draggingQueue = false;
    active = false;
    startFrom = null;
    overTo = null;
    __dragStartTs = 0;
    document.body.classList.remove('noScroll');
    document.querySelectorAll('.qTile.dragging').forEach(x => x.classList.remove('dragging'));
    document.querySelectorAll('.qTile.dragOver').forEach(x => x.classList.remove('dragOver'));
  };

  __queueDnDCleanup = cleanup;

  const finish = async () => {
    const from = startFrom;
    const to = overTo;
    const didDrag = active; // capture before cleanup() resets state
    cleanup();
    if (didDrag && from != null && to != null && from !== to) {
      await qMove(from, to);
    }
  };

  ol.addEventListener('pointerdown', (e) => {
    const handle = e.target && e.target.closest ? e.target.closest('.qHandle') : null;
    if (!handle) return;
    const tile = handle.closest('.qTile');
    if (!tile) return;

    // Only primary mouse button; touch/pen OK.
    if (e.button != null && e.button !== 0) return;

    const fromIdx = parseInt(tile.dataset.index || '', 10);
    if (isNaN(fromIdx)) return;

    startFrom = fromIdx;
    overTo = fromIdx;
    startX = e.clientX || 0;
    startY = e.clientY || 0;
    active = false;

    __draggingQueue = true;
    __dragStartTs = Date.now();

    tile.classList.add('dragging');
    document.body.classList.add('noScroll');

    try { ol.setPointerCapture(e.pointerId); } catch(_){}
    try { e.preventDefault(); } catch(_){}
  }, {passive:false});

  ol.addEventListener('pointermove', (e) => {
    if (!__draggingQueue || startFrom == null) return;

    const dx = (e.clientX || 0) - startX;
    const dy = (e.clientY || 0) - startY;
    if (!active && (Math.abs(dx) + Math.abs(dy) < MOVE_PX)) return;
    active = true;

    const tile = _queueTileFromPoint(e.clientX, e.clientY);
    if (!tile) return;
    const toIdx = parseInt(tile.dataset.index || '', 10);
    if (isNaN(toIdx)) return;
    overTo = toIdx;

    document.querySelectorAll('.qTile.dragOver').forEach(x => x.classList.remove('dragOver'));
    tile.classList.add('dragOver');

    try { e.preventDefault(); } catch(_){}
  }, {passive:false});

  ol.addEventListener('pointerup', async (e) => { try { e.preventDefault(); } catch(_){} await finish(); }, {passive:false});
  ol.addEventListener('pointercancel', async (e) => { try { e.preventDefault(); } catch(_){} await finish(); }, {passive:false});
  const __winUp = async (e) => {
    if (!__draggingQueue) return;
    try { e.preventDefault(); } catch(_){}
    await finish();
  };
  window.addEventListener('pointerup', __winUp, {passive:false});
  window.addEventListener('pointercancel', __winUp, {passive:false});
  window.addEventListener('blur', () => cleanup(), {once:false});
}


// Scrubber state
let __scrubbing = false;
let __scrubPct = 0;
let __uiNavDepth = 0;

function _isHiddenEl(el){
  return !el || el.classList.contains('hidden');
}

function _uiRefreshInteractionLockActive(){
  if (__draggingQueue) return true;
  const modalIds = ['addBackdrop', 'histBackdrop', 'aboutBackdrop', 'settingsBackdrop', 'langBackdrop'];
  for (const id of modalIds) {
    const el = document.getElementById(id);
    if (!_isHiddenEl(el)) return true;
  }
  const menu = document.getElementById('hdrMenuPanel');
  if (menu && !menu.classList.contains('hidden')) return true;
  return false;
}

function _uiPushLayer(){
  try {
    history.pushState({relaytv_ui: 1, t: Date.now()}, '');
    __uiNavDepth += 1;
  } catch (_e) {}
}

function _uiCloseTopLayerFromNav(){
  if (_jfIsDetailOpen()) {
    _jfCloseDetailPanel({fromNav:true});
    return true;
  }
  if (__jfUiVisible) {
    closeJellyfinShell({fromNav:true, force:true});
    return true;
  }
  const langBd = document.getElementById('langBackdrop');
  if (!_isHiddenEl(langBd)) {
    closeNowLanguageModal({fromNav:true});
    return true;
  }
  const settingsBd = document.getElementById('settingsBackdrop');
  if (!_isHiddenEl(settingsBd)) {
    closeSettings({fromNav:true});
    return true;
  }
  const aboutBd = document.getElementById('aboutBackdrop');
  if (!_isHiddenEl(aboutBd)) {
    closeAbout({fromNav:true});
    return true;
  }
  const histBd = document.getElementById('histBackdrop');
  if (!_isHiddenEl(histBd)) {
    closeHistory({fromNav:true});
    return true;
  }
  const addBd = document.getElementById('addBackdrop');
  if (!_isHiddenEl(addBd)) {
    closeAddUrl({fromNav:true});
    return true;
  }
  const menu = document.getElementById('hdrMenuPanel');
  if (menu && !menu.classList.contains('hidden')) {
    closeHeaderMenu();
    return true;
  }
  return false;
}

function _safeUrlHost(u){
  try {
    const uu = new URL(u);
    return (uu.hostname || '').toLowerCase();
  } catch (_) {
    return '';
  }
}

function _looksLikeJellyfinMediaUrl(u){
  try {
    const uu = new URL(String(u || ''));
    const p = (uu.pathname || '').toLowerCase();
    const hasApi = uu.searchParams.has('api_key') || uu.searchParams.has('ApiKey');
    if ((p.includes('/videos/') || p.includes('/items/')) && (hasApi || p.includes('/stream'))) return true;
  } catch (_) {}
  return false;
}

function faviconUrl(input){
  const obj = (input && typeof input === 'object') ? input : null;
  const u = obj ? String(obj.url || '') : String(input || '');
  const provider = obj ? String(obj.provider || '').toLowerCase() : '';
  if (provider === 'jellyfin' || _looksLikeJellyfinMediaUrl(u)) {
    return '/pwa/jellyfin.svg';
  }
  const host = _safeUrlHost(u);
  if (!host) return '';
  // Google S2 favicon service (works well without CORS headaches for <img>)
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=64`;
}

function displaySub(item){
  if (item && String(item.provider || '').trim().toLowerCase() === 'upload') {
    return _uploadSummary(item);
  }
  // Prefer channel/uploader when available; otherwise show a shortened URL host.
  const ch = item?.channel || '';
  if (ch) return ch;
  const u = item?.url || '';
  try {
    const uu = new URL(u);
    return uu.hostname || u;
  } catch (_){
    return u;
  }
}

function _uploadKind(item){
  const mime = String(item?.mime_type || '').trim().toLowerCase();
  if (mime.startsWith('audio/')) return 'Uploaded audio';
  if (mime.startsWith('video/')) return 'Uploaded video';
  return 'Uploaded media';
}

function _uploadRemovedCopy(item){
  const mime = String(item?.mime_type || '').trim().toLowerCase();
  if (mime.startsWith('audio/')) return 'Uploaded audio removed';
  if (mime.startsWith('video/')) return 'Uploaded video removed';
  return 'Uploaded media removed';
}

function _formatUploadSize(bytes){
  const raw = Number(bytes);
  if (!Number.isFinite(raw) || raw <= 0) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = raw;
  let idx = 0;
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024;
    idx += 1;
  }
  const digits = value >= 100 || idx === 0 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[idx]}`;
}

function _uploadSummary(item){
  if (!item || String(item.provider || '').trim().toLowerCase() !== 'upload') return '';
  const base = item.available === false ? _uploadRemovedCopy(item) : _uploadKind(item);
  const size = _formatUploadSize(item.size_bytes);
  return size ? `${base} • ${size}` : base;
}

function _hasNowPlayingItem(st, np){
  if (st && (st.playing || st.paused)) return true;
  return !!(np && (np.title || np.url || np.stream));
}

function _isNowPlayingJellyfin(np){
  if (!np || typeof np !== 'object') return false;
  const provider = String(np.provider || '').trim().toLowerCase();
  if (provider === 'jellyfin') return true;
  if (String(np.jellyfin_item_id || '').trim()) return true;
  return _looksLikeJellyfinMediaUrl(String(np.url || ''));
}

function _labelNowAudioLanguage(np){
  const lang = String(
    (np && (np.jellyfin_audio_language || np.audio_language)) || ''
  ).trim();
  if (!lang) return 'Audio';
  return `Audio: ${lang.toUpperCase()}`;
}

function _labelNowSubtitleLanguage(np){
  const idx = String((np && np.jellyfin_subtitle_stream_index) || '').trim();
  const lang = String(
    (np && (np.jellyfin_subtitle_language || np.subtitle_language)) || ''
  ).trim();
  if (idx === '-1' || lang.toLowerCase() === 'off') return 'Subs: Off';
  if (!lang) return 'Subs';
  return `Subs: ${lang.toUpperCase()}`;
}

function _renderNowLanguageButton(st, np, hasNow){
  const btn = document.getElementById('nowLangBtn');
  if (!btn) return;
  const streamCount = Array.isArray(np && np.audio_streams) ? np.audio_streams.length : 0;
  const hasMultipleOrUnknown = (streamCount === 0) || (streamCount > 1);
  const show = !!(hasNow && _isNowPlayingJellyfin(np) && hasMultipleOrUnknown);
  btn.classList.toggle('hidden', !show);
  btn.disabled = !show;
  btn.textContent = _labelNowAudioLanguage(np);
}

function _renderNowSubtitleButton(st, np, hasNow){
  const btn = document.getElementById('nowSubLangBtn');
  if (!btn) return;
  const streamCount = Array.isArray(np && np.subtitle_streams) ? np.subtitle_streams.length : 0;
  const show = !!(hasNow && _isNowPlayingJellyfin(np) && streamCount > 0);
  btn.classList.toggle('hidden', !show);
  btn.disabled = !show;
  btn.textContent = _labelNowSubtitleLanguage(np);
}

function youtubeIdFromUrl(u){
  try {
    const uu = new URL(u);
    const host = (uu.hostname || '').toLowerCase();
    if (host.endsWith('youtu.be')) {
      const id = (uu.pathname || '').replace(/^\//,'').split('/')[0];
      return id || null;
    }
    if (host.includes('youtube.com')) {
      const v = uu.searchParams.get('v');
      if (v) return v;
      const p = uu.pathname || '';
      if (p.startsWith('/shorts/')) return p.split('/')[2] || null;
      if (p.startsWith('/embed/')) return p.split('/')[2] || null;
      if (p.startsWith('/live/')) return p.split('/')[2] || null;
    }
  } catch (_) {}
  return null;
}

function thumbUrl(item){
  // Prefer locally cached thumbnail, then upstream URL.
  const th = item?.thumbnail_local || item?.thumbnail || '';
  if (th) return th;

  const u = item?.url || '';
  const prov = item?.provider || '';
  if (prov === 'youtube') {
    const id = youtubeIdFromUrl(u);
    if (id) return `https://i.ytimg.com/vi/${encodeURIComponent(id)}/hqdefault.jpg`;
  }
  return '';
}

function setBg(el, imgUrl){
  if (!el) return;
  if (imgUrl) {
    el.classList.add('hasBg');
    // Overlay gradient keeps text readable over busy thumbs
    el.style.backgroundImage = `linear-gradient(to top, rgba(0,0,0,.45) 0%, rgba(0,0,0,.30) 40%, rgba(0,0,0,.10) 75%, rgba(0,0,0,.05) 100%), url('${imgUrl}')`;
  } else {
    el.classList.remove('hasBg');
    el.style.backgroundImage = '';
  }
}

function _setProgressFill(pct){
  const fill = document.getElementById('progFill');
  if (!fill) return;
  const clamped = Math.max(0, Math.min(1, pct));
  fill.style.width = `${(clamped*100).toFixed(2)}%`;
}

function _renderRemoteVolume(value, opts){
  const options = (opts && typeof opts === 'object') ? opts : {};
  const source = String(options.source || 'status');
  const label = document.getElementById('remoteVolValue');
  const slider = document.getElementById('remoteVolSlider');
  const num = Number(value);
  let safe = Number.isFinite(num) ? Math.max(0, Math.min(200, Math.round(num))) : null;
  const known = Number.isFinite(Number(__remoteVolumeKnownValue))
    ? Math.max(0, Math.min(200, Math.round(Number(__remoteVolumeKnownValue))))
    : null;
  if (safe === 0 && source !== 'user' && known != null && known > 0) {
    safe = known;
  }
  const effective = safe != null ? safe : known;
  if (slider) {
    if (effective != null && !slider.__draggingVolume) slider.value = String(effective);
    const liveDragValue = Math.max(0, Math.min(200, Number(slider.value || 100)));
    const base = slider.__draggingVolume ? liveDragValue : (effective != null ? effective : liveDragValue);
    slider.style.setProperty('--remote-vol-pct', `${((base / 200) * 100).toFixed(2)}%`);
    if (label) label.textContent = `${Math.round(base)}% Volume`;
  } else if (label) {
    label.textContent = effective == null ? '--% Volume' : `${effective}% Volume`;
  }
  if (effective != null) {
    __remoteVolumeKnownValue = effective;
    try { localStorage.setItem('relaytv.remoteVolume', String(effective)); } catch (_e) {}
  }
}

function initRemoteVolumeSlider(){
  const slider = document.getElementById('remoteVolSlider');
  if (!slider || slider.__volumeBound) return;
  slider.__volumeBound = true;

  try {
    const cached = Number(localStorage.getItem('relaytv.remoteVolume'));
    if (Number.isFinite(cached)) {
      __remoteVolumeKnownValue = Math.max(0, Math.min(200, Math.round(cached)));
      _renderRemoteVolume(cached, {source:'cache'});
    }
  } catch (_e) {}

  const commit = async () => {
    const val = Math.max(0, Math.min(200, Number(slider.value || 0)));
    slider.__draggingVolume = false;
    _renderRemoteVolume(val, {source:'user'});
    await post('/volume', {set: val});
  };

  slider.addEventListener('pointerdown', () => { slider.__draggingVolume = true; });
  slider.addEventListener('input', () => {
    slider.__draggingVolume = true;
    _renderRemoteVolume(slider.value, {source:'user'});
  });
  slider.addEventListener('change', commit);
  slider.addEventListener('pointerup', commit);
  slider.addEventListener('pointercancel', () => { slider.__draggingVolume = false; });
}

async function primeRemoteVolumeSlider(){
  try {
    if (__lastStatus && Number.isFinite(Number(__lastStatus.volume))) {
      _renderRemoteVolume(__lastStatus.volume, {source:'status'});
      return;
    }
    const r = await fetch('/status', {cache:'no-store'});
    if (!r.ok) return;
    const st = await r.json();
    if (st && Number.isFinite(Number(st.volume))) _renderRemoteVolume(st.volume, {source:'status'});
  } catch (_e) {}
}

function _updatePreviewTime(pct){
  // Show preview time while scrubbing
  const posEl = document.getElementById('pos');
  if (!posEl || !__lastStatus) return;
  const dur = __lastStatus.duration;
  if (dur == null || isNaN(dur) || dur <= 0) return;
  const sec = pct * dur;
  posEl.textContent = fmtTime(sec);
}

function _pctFromClientX(clientX){
  const bar = document.getElementById('progress');
  if (!bar) return 0;
  const rect = bar.getBoundingClientRect();
  const x = (clientX ?? 0) - rect.left;
  return Math.max(0, Math.min(1, x / Math.max(1, rect.width)));
}

async function _commitSeekFromPct(pct){
  if (!__lastStatus || !__lastStatus.playing) return;
  const dur = __lastStatus.duration;
  if (dur == null || isNaN(dur) || dur <= 0) return;
  const sec = pct * dur;
  await post('/seek_abs', {sec: sec});
}

function initScrubber(){
  const bar = document.getElementById('progress');
  if (!bar) return;

  // Avoid double-binding if UI hot reloads
  if (bar.__scrubberBound) return;
  bar.__scrubberBound = true;

  bar.addEventListener('pointerdown', (e) => {
    if (!__lastStatus || !__lastStatus.playing) return;
    const dur = __lastStatus.duration;
    if (dur == null || isNaN(dur) || dur <= 0) return;
    if (typeof e.preventDefault === 'function') e.preventDefault();

    __scrubbing = true;
    __scrubPct = _pctFromClientX(e.clientX);
    _setProgressFill(__scrubPct);
    _updatePreviewTime(__scrubPct);
    const pointerId = e.pointerId;

    try { bar.setPointerCapture(pointerId); } catch (_) {}

    const onMove = (ev) => {
      if (!__scrubbing) return;
      if (typeof ev.preventDefault === 'function') ev.preventDefault();
      __scrubPct = _pctFromClientX(ev.clientX);
      _setProgressFill(__scrubPct);
      _updatePreviewTime(__scrubPct);
    };

    const onUp = async (ev) => {
      if (!__scrubbing) return;
      if (typeof ev.preventDefault === 'function') ev.preventDefault();
      __scrubbing = false;

      try { bar.releasePointerCapture(pointerId); } catch (_) {}

      // Commit seek on release
      const pct = _pctFromClientX(ev.clientX);
      _setProgressFill(pct);
      await _commitSeekFromPct(pct);

      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
  });
}

function _applyQueueSnapshot(payload){
  if (!payload || typeof payload !== 'object' || !Array.isArray(payload.queue)) return false;
  const next = (__lastStatus && typeof __lastStatus === 'object') ? {...__lastStatus} : {};
  next.queue = payload.queue;
  next.queue_length = Number(payload.queue_length ?? payload.queue.length ?? 0);
  __lastStatus = next;
  return true;
}

async function qRemove(index){
  try {
    const res = await fetch('/queue/remove', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({index})});
    let payload = null;
    try { payload = await res.json(); } catch(_) {}
    if (!res.ok) {
      console.warn('queue/remove failed', res.status, payload);
    } else {
      _applyQueueSnapshot(payload);
    }
  } catch (e) {
    console.warn('queue/remove error', e);
  }
  await refresh();
}

async function qMove(from_index, to_index){
  try {
    const res = await fetch('/queue/move', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({from_index, to_index})});
    let payload = null;
    try { payload = await res.json(); } catch(_) {}
    if (!res.ok) {
      console.warn('queue/move failed', res.status, payload);
    } else {
      _applyQueueSnapshot(payload);
    }
  } catch (e) {
    console.warn('queue/move error', e);
  }
  await refresh();
}

let __jfBusy = false;
let __jfLastMode = 'home';
let __jfLastQuery = '';
let __jfSearchDebounceTimer = 0;
let __jfPendingSearch = null;
let __jfSelectedItemId = '';
let __jfSelectedItem = null;
let __jfActionBusy = false;
let __jfConnected = false;
let __jfActionStatusTimer = 0;
let __jfDetailNavToken = 0;
let __jfUiVisible = false;
let __jfLaunchVisible = false;
let __jfActiveTab = 'dashboard';
let __jfDashboardRows = [];
let __jfMoviesSort = 'added';
let __jfMoviesLimit = 120;
let __jfMoviesCount = 0;
let __jfTvSort = 'title_asc';
let __jfTvLimit = 120;
let __jfTvCount = 0;
let __jfTvSeriesId = '';
let __jfTvSeriesTitle = '';
let __jfTvSeriesThumb = '';
let __jfTvSeasonNumber = null;
let __jfTvViewMode = 'series';
let __jfLastFocus = null;
let __jfAlphaIndicatorTimer = 0;
let __jfResizeBound = false;
let __jfViewportBound = false;
const __JF_CATALOG_LIMIT = 5000;
const __JF_REQ_TIMEOUT_MS = 12000;
const __UI_FALLBACK_REFRESH_MS = 8000;
const __UI_EVENT_RECONNECT_MS = 5000;
const __JF_DASHBOARD_REFRESH_MS = 45000;

function _jfCanLaunchFromStatus(st){
  if (!st || typeof st !== 'object') return false;
  const enabled = !!st.jellyfin_enabled;
  const running = !!st.jellyfin_running;
  const connected = !!(st.jellyfin_connected || st.jellyfin_authenticated);
  return enabled && running && connected;
}

function _jfSetLaunchVisible(visible){
  __jfLaunchVisible = !!visible;
  const btn = document.getElementById('jellyfinOpenBtn');
  if (btn) {
    btn.classList.toggle('show', __jfLaunchVisible);
    btn.disabled = !__jfLaunchVisible;
  }
  if (!__jfLaunchVisible) closeJellyfinShell({fromNav:true, force:true});
}

function _jfSetShellVisible(visible){
  __jfUiVisible = !!visible;
  const shell = document.getElementById('jellyfinShell');
  if (!shell) return;
  if (__jfUiVisible) {
    shell.classList.remove('hidden');
    shell.setAttribute('aria-hidden', 'false');
  } else {
    shell.classList.add('hidden');
    shell.setAttribute('aria-hidden', 'true');
    shell.classList.remove('jfDetailLock');
    document.body.classList.remove('jfNoScroll');
  }
}

function openJellyfinShell(){
  if (!__jfLaunchVisible) return;
  if (__jfUiVisible) return;
  __jfLastFocus = document.activeElement || null;
  _jfSetShellVisible(true);
  _uiPushLayer();
  _jfSetActiveTab(__jfActiveTab || 'dashboard', {refresh:false});
  const backBtn = document.getElementById('jfShellBackBtn');
  if (backBtn) {
    requestAnimationFrame(() => backBtn.focus());
  }
}

function closeJellyfinShell(opts){
  const fromNav = !!(opts && opts.fromNav);
  const force = !!(opts && opts.force);
  if (!fromNav && !force && __jfUiVisible && __uiNavDepth > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  _jfSetShellVisible(false);
  _jfCloseDetailPanel({fromNav:true});
  const target =
    (__jfLastFocus && typeof __jfLastFocus.focus === 'function') ? __jfLastFocus :
    document.getElementById('jellyfinOpenBtn');
  if (target && typeof target.focus === 'function') {
    requestAnimationFrame(() => target.focus());
  }
  __jfLastFocus = null;
}

function _jfSetActiveTab(tab, opts){
  const next = String(tab || 'dashboard').toLowerCase();
  __jfActiveTab = (next === 'movies' || next === 'tv') ? next : 'dashboard';
  document.querySelectorAll('.jfTabBtn').forEach((b) => {
    const isActive = String(b.getAttribute('data-jf-tab') || '') === __jfActiveTab;
    b.classList.toggle('active', isActive);
    b.setAttribute('aria-selected', isActive ? 'true' : 'false');
    b.setAttribute('tabindex', isActive ? '0' : '-1');
  });
  const searchInput = document.getElementById('jfSearchInput');
  if (searchInput) searchInput.disabled = __jfActionBusy;
  _jfSyncTabControls();
  const force = !!(opts && opts.refresh);
  if (__jfLastMode === 'search' && __jfLastQuery) {
    _jfScheduleSearch(force, 0);
    return;
  }
  _jfLoadActiveTabDefault(force);
}

function _jfSyncTabControls(){
  const searchActive = (__jfLastMode === 'search' && !!__jfLastQuery);
  const isTvSeriesView = (__jfActiveTab === 'tv' && __jfTvViewMode === 'series');
  const showCatalogControls = !searchActive && ((__jfActiveTab === 'movies') || isTvSeriesView);
  const searchInput = document.getElementById('jfSearchInput');
  const sortSel = document.getElementById('jfSortSelect');
  const alphaIndicator = document.getElementById('jfAlphaIndicator');
  if (searchInput) {
    searchInput.placeholder = (__jfActiveTab === 'movies')
      ? 'Search movies…'
      : (__jfActiveTab === 'tv' ? 'Search TV series…' : 'Search Jellyfin titles…');
  }
  if (sortSel) sortSel.classList.toggle('hiddenCtl', !showCatalogControls);
  if (alphaIndicator && !showCatalogControls) alphaIndicator.classList.remove('show');
  if (!sortSel || !showCatalogControls) return;

  const opts = (__jfActiveTab === 'movies')
    ? [
        ['added', 'Recently Added'],
        ['title_asc', 'A-Z'],
        ['title_desc', 'Z-A'],
        ['year_desc', 'Year (new-old)'],
        ['year_asc', 'Year (old-new)'],
      ]
    : [
        ['title_asc', 'A-Z'],
        ['title_desc', 'Z-A'],
        ['added', 'Recently Added'],
        ['year_desc', 'Year (new-old)'],
        ['year_asc', 'Year (old-new)'],
      ];
  const selected = (__jfActiveTab === 'movies') ? __jfMoviesSort : __jfTvSort;
  sortSel.innerHTML = '';
  opts.forEach(([v, label]) => {
    const o = document.createElement('option');
    o.value = v;
    o.textContent = label;
    sortSel.appendChild(o);
  });
  sortSel.value = selected || opts[0][0];
  _jfSetAlphaIndicator('A', {show:false});
}

function _jfSetAlphaIndicator(letter, opts){
  const el = document.getElementById('jfAlphaIndicator');
  if (!el) return;
  const t = String(letter || '').trim().toUpperCase();
  el.textContent = t || 'A';
  const topPx = Number(opts && opts.topPx);
  if (Number.isFinite(topPx)) {
    el.style.top = `${Math.max(10, Math.round(topPx))}px`;
  }
  const show = !!(opts && opts.show);
  if (!show) {
    el.classList.remove('show');
    return;
  }
  el.classList.add('show');
  if (__jfAlphaIndicatorTimer) clearTimeout(__jfAlphaIndicatorTimer);
  __jfAlphaIndicatorTimer = setTimeout(() => {
    el.classList.remove('show');
    __jfAlphaIndicatorTimer = 0;
  }, 850);
}

function _jfTitleInitial(item){
  const txt = String((item && item.title) || '').trim().toUpperCase();
  if (!txt) return '#';
  const c = txt.charAt(0);
  return /[A-Z]/.test(c) ? c : '#';
}

function _jfIndicatorSortMode(rowId){
  const rid = String(rowId || '').trim().toLowerCase();
  if (__jfActiveTab === 'movies' && rid === 'movies') {
    return String(__jfMoviesSort || 'added').trim().toLowerCase();
  }
  if (__jfActiveTab === 'tv' && rid === 'tv_series') {
    return String(__jfTvSort || 'title_asc').trim().toLowerCase();
  }
  return 'title_asc';
}

function _jfExtractYearLabel(raw){
  const txt = String(raw || '').trim();
  if (!txt) return '';
  const m = txt.match(/\b(19|20)\d{2}\b/);
  return m && m[0] ? m[0] : '';
}

function _jfIndicatorLabelForNode(node, rowId){
  if (!node) return 'A';
  const mode = _jfIndicatorSortMode(rowId);
  const useYear = mode === 'added' || mode === 'year_desc' || mode === 'year_asc';
  if (useYear) {
    const year = _jfExtractYearLabel(
      node.getAttribute('data-item-year')
      || node.getAttribute('data-item-subtitle')
      || node.getAttribute('data-item-title')
      || ''
    );
    if (year) return year;
  }
  const title = String(node.getAttribute('data-item-title') || '').trim();
  return _jfTitleInitial({title});
}

function _jfIsNarrowViewport(){
  try {
    return window.matchMedia('(max-width: 980px)').matches;
  } catch (_e) {
    return window.innerWidth <= 980;
  }
}

function _jfSetDetailScrollLock(locked){
  const lock = !!locked && _jfIsNarrowViewport();
  const shell = document.getElementById('jellyfinShell');
  if (shell) shell.classList.toggle('jfDetailLock', lock);
  document.body.classList.toggle('jfNoScroll', lock);
}

function _jfPositionDetailPanel(){
  const detail = document.getElementById('jfDetail');
  if (!detail) return;
  if (_jfIsNarrowViewport()) {
    const shell = document.getElementById('jellyfinShell');
    const grid = document.getElementById('jfGrid');
    if (shell && grid) {
      const shellRect = shell.getBoundingClientRect();
      const gridRect = grid.getBoundingClientRect();
      const gutter = 12;
      const gridWidth = Math.max(0, Math.floor(grid.clientWidth || gridRect.width || shellRect.width || 0));
      const maxW = Math.max(220, Math.min(660, Math.floor(gridWidth - (gutter * 2))));
      const maxH = Math.max(220, Math.floor(shell.clientHeight - (gutter * 2)));
      detail.style.position = 'absolute';
      detail.style.left = `${Math.max(0, Math.round((grid.clientWidth - maxW) / 2))}px`;
      detail.style.right = 'auto';
      detail.style.width = `${maxW}px`;
      detail.style.maxWidth = `${maxW}px`;
      detail.style.maxHeight = `${maxH}px`;
      detail.style.transform = 'none';
      const panelH = Math.min(detail.offsetHeight || maxH, maxH);
      const rawTop = gutter - (gridRect.top - shellRect.top);
      const maxTop = Math.max(0, grid.scrollHeight - panelH - gutter);
      const top = Math.min(Math.round(rawTop), maxTop);
      detail.style.top = `${top}px`;
      return;
    }
  }
  detail.style.position = '';
  detail.style.width = '';
  detail.style.maxWidth = '';
  detail.style.maxHeight = '';
  detail.style.left = '';
  detail.style.right = '';
  detail.style.transform = '';
  const shell = document.getElementById('jellyfinShell');
  const grid = document.getElementById('jfGrid');
  if (!shell || !grid || !detail || !_jfIsDetailOpen()) return;
  const shellRect = shell.getBoundingClientRect();
  const gridRect = grid.getBoundingClientRect();
  const rawTop = 14 - (gridRect.top - shellRect.top);
  const maxTop = Math.max(0, grid.scrollHeight - detail.offsetHeight - 8);
  const top = Math.max(0, Math.min(Math.round(rawTop), maxTop));
  detail.style.top = `${top}px`;
}

function _jfCloseDetailPanel(opts){
  const grid = document.getElementById('jfGrid');
  const fromNav = !!(opts && opts.fromNav);
  if (!fromNav && _jfIsDetailOpen() && __uiNavDepth > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  if (grid) grid.classList.remove('detailOpen');
  const detail = document.getElementById('jfDetail');
  if (detail) {
    detail.style.top = '';
    detail.style.width = '';
    detail.style.maxWidth = '';
    detail.style.maxHeight = '';
    detail.style.left = '';
    detail.style.right = '';
    detail.style.transform = '';
    detail.style.position = '';
  }
  _jfSetDetailScrollLock(false);
  __jfSelectedItemId = '';
  __jfSelectedItem = null;
  _jfApplySelectionUi();
  _jfDetailPlaceholder('Select a Jellyfin item to view details.');
}

function _jfOpenDetailPanel(){
  const grid = document.getElementById('jfGrid');
  const wasOpen = _jfIsDetailOpen();
  if (grid) grid.classList.add('detailOpen');
  if (!wasOpen) _uiPushLayer();
  _jfSetDetailScrollLock(true);
  requestAnimationFrame(() => _jfPositionDetailPanel());
}

function _jfIsDetailOpen(){
  const grid = document.getElementById('jfGrid');
  return !!(grid && grid.classList.contains('detailOpen'));
}

function _jfSeriesItemFromNode(node){
  const item = _jfLightItemFromNode(node);
  if (!item) return null;
  item.type = String(node.getAttribute('data-item-type') || '').trim().toLowerCase();
  item.series_id = String(node.getAttribute('data-item-series-id') || '').trim();
  item.season_id = String(node.getAttribute('data-item-season-id') || '').trim();
  item.thumbnail = String(node.getAttribute('data-item-thumb') || '').trim();
  item.thumbnail_local = String(node.getAttribute('data-item-thumb-local') || '').trim();
  const sn = Number(node.getAttribute('data-item-season') || '');
  if (Number.isFinite(sn)) item.season_number = sn;
  return item;
}

function _jfOpenSeriesDetailFromRich(rich){
  if (!rich) return;
  const rType = String(rich.type || '').trim().toLowerCase();
  if (rType === 'nav_back') {
    loadJellyfinTvSeries(false);
    return;
  }
  if (rType === 'season') {
    __jfTvSeasonNumber = Number.isFinite(Number(rich.season_number)) ? Number(rich.season_number) : null;
    loadJellyfinTvSeriesDetail(rich.series_id || __jfTvSeriesId, {
      title: __jfTvSeriesTitle,
      thumbnail: __jfTvSeriesThumb || rich.thumbnail_local || rich.thumbnail || '',
      thumbnail_local: __jfTvSeriesThumb || rich.thumbnail_local || '',
    });
    return;
  }
  if (rType === 'series') {
    loadJellyfinTvSeriesDetail(rich.item_id, {
      title: rich.title,
      thumbnail: rich.thumbnail_local || rich.thumbnail || '',
      thumbnail_local: rich.thumbnail_local || '',
    });
    return;
  }
  // Episodes (and any future non-series entries) should open item detail panel.
  loadJellyfinDetail(rich.item_id);
}

function _jfIsSeriesNavType(rich){
  if (!rich || typeof rich !== 'object') return false;
  const rType = String(rich.type || '').trim().toLowerCase();
  return rType === 'series' || rType === 'season' || rType === 'nav_back';
}

function _jfSetStatus(text, kind){
  const el = document.getElementById('jfStatus');
  if (!el) return;
  el.textContent = text || '';
  el.classList.remove('ok', 'err');
  if (kind === 'ok' || kind === 'err') el.classList.add(kind);
}

function _jfSetConn(up, text){
  __jfConnected = !!up;
  const card = document.getElementById('jellyfinCard');
  if (card) card.classList.toggle('jfOffline', !__jfConnected);
  // Connection indicator is represented by jfStatus only.
  void text;
}

function _jfSetActionStatus(text, kind, holdMs){
  const el = document.getElementById('jfActionStatus');
  if (!el) return;
  el.classList.remove('ok', 'err');
  if (kind === 'ok' || kind === 'err') el.classList.add(kind);
  const msg = String(text || '').trim();
  if (/^(connected|ready)(\s*\(.*\))?$/i.test(msg)) {
    el.textContent = '';
    el.classList.remove('ok', 'err');
    return;
  }
  el.textContent = msg;
  if (__jfActionStatusTimer) {
    clearTimeout(__jfActionStatusTimer);
    __jfActionStatusTimer = 0;
  }
  const ttl = Number(holdMs);
  if (Number.isFinite(ttl) && ttl > 0) {
    __jfActionStatusTimer = setTimeout(() => {
      if (!el) return;
      el.textContent = '';
      el.classList.remove('ok', 'err');
      __jfActionStatusTimer = 0;
    }, ttl);
  }
}

function _jfFmtSec(sec){
  const n = Number(sec);
  if (!Number.isFinite(n) || n <= 0) return '';
  const m = Math.floor(n / 60);
  const s = Math.floor(n % 60);
  return `${m}:${String(s).padStart(2,'0')}`;
}

function _jfInt(val){
  const n = Number(val);
  if (!Number.isFinite(n)) return null;
  const i = Math.floor(n);
  return i >= 0 ? i : null;
}

function _jfEpisodeTuple(item){
  if (!item || typeof item !== 'object') return {season:null, episode:null};
  let season = _jfInt(item.season_number);
  let episode = _jfInt(item.episode_number);
  if (season != null && episode != null) return {season, episode};
  const sub = String(item.subtitle || '').trim();
  const m = sub.match(/S(\d{1,3})E(\d{1,4})/i);
  if (m) {
    season = _jfInt(m[1]);
    episode = _jfInt(m[2]);
  }
  return {season, episode};
}

function _jfSeriesKey(item){
  if (!item || typeof item !== 'object') return '';
  const s = String(item.series_name || item.title || '').trim().toLowerCase();
  return s;
}

function _jfEpisodeNav(item){
  if (!item || typeof item !== 'object') return {prev:null, next:null};
  const type = String(item.type || '').trim().toLowerCase();
  if (type !== 'episode') return {prev:null, next:null};
  const cur = _jfEpisodeTuple(item);
  const key = _jfSeriesKey(item);
  if (!key || cur.season == null || cur.episode == null) return {prev:null, next:null};

  const byId = new Map();
  document.querySelectorAll('#jfRows .jfItem').forEach((node) => {
    const iid = String(node.getAttribute('data-item-id') || '').trim();
    if (!iid) return;
    const nType = String(node.getAttribute('data-item-type') || '').trim().toLowerCase();
    if (nType !== 'episode') return;
    const nSeries = String(node.getAttribute('data-item-series') || '').trim().toLowerCase();
    if (!nSeries || nSeries !== key) return;
    let nSeason = _jfInt(node.getAttribute('data-item-season'));
    let nEpisode = _jfInt(node.getAttribute('data-item-episode'));
    if (nSeason == null || nEpisode == null) {
      const parsed = _jfEpisodeTuple({
        subtitle: String(node.getAttribute('data-item-subtitle') || '').trim(),
      });
      if (nSeason == null) nSeason = parsed.season;
      if (nEpisode == null) nEpisode = parsed.episode;
    }
    if (nSeason == null || nEpisode == null) return;
    if (!byId.has(iid)) {
      byId.set(iid, {
        item_id: iid,
        title: String(node.getAttribute('data-item-title') || '').trim(),
        subtitle: String(node.getAttribute('data-item-subtitle') || '').trim(),
        season_number: nSeason,
        episode_number: nEpisode,
      });
    }
  });

  if (!byId.size) return {prev:null, next:null};
  const items = Array.from(byId.values()).sort((a, b) => {
    const sa = _jfInt(a.season_number) ?? 0;
    const sb = _jfInt(b.season_number) ?? 0;
    if (sa !== sb) return sa - sb;
    const ea = _jfInt(a.episode_number) ?? 0;
    const eb = _jfInt(b.episode_number) ?? 0;
    return ea - eb;
  });
  const curId = String(item.item_id || '').trim();
  const curRank = (cur.season * 100000) + cur.episode;
  let prev = null;
  let next = null;
  for (const ep of items) {
    const sNum = _jfInt(ep.season_number);
    const num = _jfInt(ep.episode_number);
    if (sNum == null || num == null) continue;
    const rank = (sNum * 100000) + num;
    if (curId && String(ep.item_id || '') === curId) continue;
    if (rank < curRank) prev = ep;
    if (!next && rank > curRank) next = ep;
  }
  return {prev, next};
}

async function _jfOpenAdjacentEpisode(target, opts){
  const focusItem = !!(opts && opts.focusItem);
  const iid = String((target && target.item_id) || '').trim();
  if (!iid) return;
  await loadJellyfinDetail(iid, {keepDetail: true, preloadThumb: true});
  if (focusItem) {
    const nodes = Array.from(document.querySelectorAll('#jfRows .jfItem'));
    const found = nodes.find((n) => String(n.getAttribute('data-item-id') || '').trim() === iid);
    if (found) found.focus();
  }
}

async function _jfFetchAdjacentEpisodeNav(itemId){
  const iid = String(itemId || '').trim();
  if (!iid) return {prev:null, next:null};
  try {
    const j = await _jfFetchJson(`/jellyfin/item/${encodeURIComponent(iid)}/adjacent`);
    const prev = (j && typeof j.prev === 'object') ? j.prev : null;
    const next = (j && typeof j.next === 'object') ? j.next : null;
    return {prev, next};
  } catch (_e) {
    return {prev:null, next:null};
  }
}

function _jfSetThumbNavButton(btn, target){
  if (!btn) return;
  const iid = String((target && target.item_id) || '').trim();
  if (!iid) {
    btn.disabled = true;
    btn.style.display = 'none';
    btn.onclick = null;
    return;
  }
  btn.disabled = false;
  btn.style.display = '';
  btn.onclick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    _jfOpenAdjacentEpisode(target, {focusItem:false});
  };
}

function _jfPreloadImage(url){
  const src = String(url || '').trim();
  if (!src) return Promise.resolve();
  return new Promise((resolve) => {
    try {
      const img = new Image();
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        resolve();
      };
      img.onload = finish;
      img.onerror = finish;
      img.src = src;
      setTimeout(finish, 1200);
    } catch (_e) {
      resolve();
    }
  });
}

function _jfDetailPlaceholder(text){
  const host = document.getElementById('jfDetail');
  if (!host) return;
  host.className = 'jfDetail muted';
  host.textContent = text || 'Select a Jellyfin item to view details.';
}

function _jfApplySelectionUi(){
  document.querySelectorAll('.jfItem.selected').forEach((el) => el.classList.remove('selected'));
  if (!__jfSelectedItemId) return;
  const items = document.querySelectorAll('.jfItem');
  items.forEach((el) => {
    if ((el.getAttribute('data-item-id') || '') === __jfSelectedItemId) el.classList.add('selected');
  });
}

function _jfRenderDetail(item){
  const host = document.getElementById('jfDetail');
  if (!host) return;
  host.className = 'jfDetail';
  host.innerHTML = '';

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'jfDetailClose';
  closeBtn.textContent = '← Back';
  closeBtn.onclick = () => _jfCloseDetailPanel();
  host.appendChild(closeBtn);

  const isEpisode = String(item && item.type || '').trim().toLowerCase() === 'episode';
  const thumbWrap = document.createElement('div');
  thumbWrap.className = 'jfDetailThumbWrap';

  const thumb = document.createElement('img');
  thumb.className = 'jfDetailThumb';
  thumb.alt = '';
  thumb.loading = 'eager';
  thumb.src = item.thumbnail_local || item.thumbnail || '/pwa/weather/not-available.svg';
  thumb.addEventListener('load', () => _jfPositionDetailPanel(), {once:true});
  thumb.addEventListener('error', () => _jfPositionDetailPanel(), {once:true});
  thumbWrap.appendChild(thumb);

  const prevBtn = document.createElement('button');
  prevBtn.type = 'button';
  prevBtn.className = 'jfThumbNav prev';
  prevBtn.textContent = '<';
  prevBtn.title = 'Previous episode';
  prevBtn.disabled = true;
  prevBtn.style.display = 'none';
  thumbWrap.appendChild(prevBtn);

  const nextBtn = document.createElement('button');
  nextBtn.type = 'button';
  nextBtn.className = 'jfThumbNav next';
  nextBtn.textContent = '>';
  nextBtn.title = 'Next episode';
  nextBtn.disabled = true;
  nextBtn.style.display = 'none';
  thumbWrap.appendChild(nextBtn);

  const navToken = ++__jfDetailNavToken;
  if (isEpisode) {
    _jfFetchAdjacentEpisodeNav(item && item.item_id).then((nav) => {
      if (navToken !== __jfDetailNavToken) return;
      _jfSetThumbNavButton(prevBtn, nav && nav.prev ? nav.prev : null);
      _jfSetThumbNavButton(nextBtn, nav && nav.next ? nav.next : null);
    });
  }

  host.appendChild(thumbWrap);

  const title = document.createElement('div');
  title.className = 'jfDetailTitle';
  title.textContent = item.title || '(untitled)';
  host.appendChild(title);

  const sub = document.createElement('div');
  sub.className = 'jfDetailSub';
  const parts = [];
  if (item.subtitle) parts.push(item.subtitle);
  if (item.year) parts.push(String(item.year));
  const rt = _jfFmtSec(item.runtime_sec);
  if (rt) parts.push(rt);
  if (item.resume_pos && Number(item.resume_pos) > 0) parts.push(`Resume ${_jfFmtSec(item.resume_pos)}`);
  sub.textContent = parts.join(' · ');
  host.appendChild(sub);

  const chips = [];
  if (item.type) chips.push(String(item.type));
  if (item.season_number != null && item.episode_number != null) chips.push(`S${String(item.season_number).padStart(2,'0')}E${String(item.episode_number).padStart(2,'0')}`);
  if (item.resume_pos && Number(item.resume_pos) > 0) chips.push(`Resume ${_jfFmtSec(item.resume_pos)}`);
  if (item.audio_language) chips.push(`Audio ${String(item.audio_language)}`);
  if (item.subtitle_language) chips.push(`Subs ${String(item.subtitle_language)}`);
  if (chips.length) {
    const chipsWrap = document.createElement('div');
    chipsWrap.className = 'jfChips';
    chips.forEach((txt) => {
      const c = document.createElement('span');
      c.className = 'jfChip';
      c.textContent = txt;
      chipsWrap.appendChild(c);
    });
    host.appendChild(chipsWrap);
  }

  const audioAvail = Array.isArray(item.audio_streams)
    ? [...new Set(item.audio_streams.map((s) => String((s && s.language) || '').trim()).filter(Boolean))]
    : [];
  const subAvail = Array.isArray(item.subtitle_streams)
    ? [...new Set(item.subtitle_streams.map((s) => String((s && s.language) || '').trim()).filter(Boolean))]
    : [];
  const streamBits = [];
  if (audioAvail.length) streamBits.push(`Audio: ${audioAvail.slice(0, 6).join(', ')}`);
  if (subAvail.length) streamBits.push(`Subs: ${subAvail.slice(0, 6).join(', ')}`);
  if (streamBits.length) {
    const streamInfo = document.createElement('div');
    streamInfo.className = 'jfDetailSub';
    streamInfo.textContent = streamBits.join(' • ');
    host.appendChild(streamInfo);
  }

  if (item.overview) {
    const body = document.createElement('div');
    body.className = 'jfDetailBody';
    body.textContent = item.overview;
    host.appendChild(body);
  } else {
    const body = document.createElement('div');
    body.className = 'jfDetailBody muted';
    body.textContent = 'No overview available.';
    host.appendChild(body);
  }

  const actions = document.createElement('div');
  actions.className = 'jfActionRow';

  const mkBtn = (label, action) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn';
    b.textContent = label;
    b.onclick = () => jellyfinDetailAction(action);
    return b;
  };

  actions.appendChild(mkBtn('Play Now', 'play_now'));
  actions.appendChild(mkBtn('Play Next', 'play_next'));
  actions.appendChild(mkBtn('Play Last', 'play_last'));
  actions.appendChild(mkBtn('Resume', 'resume'));
  host.appendChild(actions);

  const msg = document.createElement('div');
  msg.id = 'jfActionMsg';
  msg.className = 'jfActionMsg';
  host.appendChild(msg);
  _jfOpenDetailPanel();
  requestAnimationFrame(() => _jfPositionDetailPanel());
}

function _jfBuildRowItemCard(item){
  const premiereText = String(item.premiere_date || item.PremiereDate || '').trim();
  const yearFromPremiere = (/^\d{4}/.test(premiereText) ? premiereText.slice(0, 4) : '');
  const titleText = String(
    item.title || item.name || item.Name || item.series_name || item.SeriesName || ''
  ).trim() || '(untitled)';
  const subtitleTextRaw = String(
    item.subtitle || item.Subtitle || item.sub_title || ''
  ).trim();
  const yearText = String(
    item.year || item.production_year || item.ProductionYear || yearFromPremiere || ''
  ).trim();
  const itemType = String(item.type || item.Type || '').trim().toLowerCase();
  let subtitleText = subtitleTextRaw;
  if (itemType === 'movie' && subtitleTextRaw) {
    const m = subtitleTextRaw.match(/\b(19|20)\d{2}\b/);
    if (m && m[0]) subtitleText = m[0];
  }
  if (!subtitleText) {
    subtitleText = yearText || (itemType === 'movie' ? 'Movie' : '');
  }
  const btn = document.createElement('div');
  btn.className = 'jfItem';
  btn.tabIndex = 0;
  btn.setAttribute('role', 'button');
  btn.dataset.itemId = String(item.item_id || '').trim();
  btn.dataset.itemTitle = titleText;
  btn.dataset.itemSubtitle = subtitleText;
  btn.dataset.itemYear = yearText;
  btn.dataset.itemResumePos = String(item.resume_pos != null ? item.resume_pos : '');
  btn.dataset.itemType = itemType;
  btn.dataset.itemSeason = String(item.season_number != null ? item.season_number : '');
  btn.dataset.itemEpisode = String(item.episode_number != null ? item.episode_number : '');
  btn.dataset.itemSeries = String(item.series_name || item.SeriesName || titleText).trim();
  btn.dataset.itemSeriesId = String(item.series_id || '').trim();
  btn.dataset.itemSeasonId = String(item.season_id || '').trim();
  btn.dataset.itemThumb = String(item.thumbnail || '').trim();
  btn.dataset.itemThumbLocal = String(item.thumbnail_local || '').trim();
  btn.setAttribute('aria-label', `${titleText} ${subtitleText}`.trim());

  const tWrap = document.createElement('div');
  tWrap.className = 'jfThumb';
  const img = document.createElement('img');
  img.alt = '';
  img.loading = (__jfActiveTab === 'dashboard') ? 'lazy' : 'eager';
  img.decoding = 'async';
  img.src = item.thumbnail_local || item.thumbnail || '/pwa/weather/not-available.svg';
  tWrap.appendChild(img);

  const meta = document.createElement('div');
  meta.className = 'jfMeta';
  const itTitle = document.createElement('div');
  itTitle.className = 'jfItemTitle';
  itTitle.textContent = titleText;
  const itSub = document.createElement('div');
  itSub.className = 'jfItemSub';
  itSub.textContent = subtitleText;
  meta.appendChild(itTitle);
  meta.appendChild(itSub);

  const iType = itemType;
  if (__jfActiveTab === 'tv' && iType === 'series') {
    const quick = document.createElement('div');
    quick.className = 'jfQuickRow';
    const bView = document.createElement('button');
    bView.type = 'button';
    bView.className = 'jfQuickBtn';
    bView.setAttribute('data-jf-action', 'view_series');
    bView.textContent = 'View';
    const bPlayAll = document.createElement('button');
    bPlayAll.type = 'button';
    bPlayAll.className = 'jfQuickBtn';
    bPlayAll.setAttribute('data-jf-action', 'play_all_series');
    bPlayAll.textContent = 'Play All';
    quick.appendChild(bView);
    quick.appendChild(bPlayAll);
    meta.appendChild(quick);
  } else if (__jfActiveTab === 'tv' && (iType === 'season' || iType === 'nav_back')) {
    const quick = document.createElement('div');
    quick.className = 'jfQuickRow';
    const bView = document.createElement('button');
    bView.type = 'button';
    bView.className = 'jfQuickBtn';
    bView.setAttribute('data-jf-action', 'view_series');
    bView.textContent = iType === 'nav_back' ? 'Back' : 'View';
    quick.appendChild(bView);
    meta.appendChild(quick);
  } else {
    const quick = document.createElement('div');
    quick.className = 'jfQuickRow';
    const bPlay = document.createElement('button');
    bPlay.type = 'button';
    bPlay.className = 'jfQuickBtn';
    bPlay.setAttribute('data-jf-action', 'play_now');
    bPlay.textContent = 'Play Now';
    const bNext = document.createElement('button');
    bNext.type = 'button';
    bNext.className = 'jfQuickBtn';
    bNext.setAttribute('data-jf-action', 'play_last');
    bNext.textContent = 'Queue';
    quick.appendChild(bPlay);
    quick.appendChild(bNext);
    meta.appendChild(quick);
  }

  btn.appendChild(tWrap);
  btn.appendChild(meta);
  return btn;
}

function _jfRenderRows(rows){
  const host = document.getElementById('jfRows');
  if (!host) return;
  host.innerHTML = '';
  if (!Array.isArray(rows) || !rows.length) {
    host.innerHTML = '<div class="muted">No Jellyfin items available.</div>';
    return;
  }
  const hostFrag = document.createDocumentFragment();
  rows.forEach((row) => {
    const rowId = String((row && row.id) || '').trim();
    const isCatalogRow = (__jfActiveTab !== 'dashboard') && (rowId === 'movies' || rowId === 'tv_series' || rowId === 'tv_episodes');
    const hideRowTitle = rowId === 'movies' || rowId === 'tv_series';
    const wrap = document.createElement('div');
    wrap.className = 'jfRow';
    if (isCatalogRow) wrap.classList.add('catalog');
    if (hideRowTitle) wrap.classList.add('catalogNoTitle');
    wrap.dataset.rowId = rowId;

    const title = document.createElement('div');
    title.className = 'jfRowTitle';
    title.textContent = row.title || 'Results';
    wrap.appendChild(title);

    const scroller = document.createElement('div');
    scroller.className = 'jfScroller';
    if (isCatalogRow) scroller.classList.add('jfCatalogScroller');
    if (rowId === 'movies') scroller.classList.add('jfCatalogMovies');
    if (rowId === 'tv_series' || rowId === 'tv_episodes') scroller.classList.add('jfCatalogTv');
    if (rowId === 'tv_episodes') scroller.classList.add('jfCatalogEpisodes');
    if (rowId === 'tv_seasons') scroller.classList.add('jfSeasonWrap');

    const items = Array.isArray(row.items) ? row.items : [];
    if (!items.length) {
      const empty = document.createElement('div');
      empty.className = 'muted';
      empty.textContent = 'No items';
      scroller.appendChild(empty);
    } else {
      const itemFrag = document.createDocumentFragment();
      items.forEach((item) => itemFrag.appendChild(_jfBuildRowItemCard(item)));
      scroller.appendChild(itemFrag);
    }

    wrap.appendChild(scroller);
    hostFrag.appendChild(wrap);
    if (isCatalogRow) {
      const nodes = Array.from(scroller.querySelectorAll('.jfItem'));
      if (!nodes.length) return;
      const update = (showIndicator) => {
        const boxTop = Math.max(0, scroller.scrollTop || 0);
        let pick = nodes.find((node) => {
          const nt = Math.max(0, node.offsetTop || 0);
          return nt >= boxTop;
        }) || nodes[0];
        for (const node of nodes) {
          const nt = Math.max(0, node.offsetTop || 0);
          if (nt <= (boxTop + 6)) pick = node;
          else break;
        }
        const canScroll = (scroller.scrollHeight - scroller.clientHeight) > 8;
        let topPx = null;
        if (canScroll) {
          const grid = document.getElementById('jfGrid');
          if (grid) {
            const gridRect = grid.getBoundingClientRect();
            const scrollRect = scroller.getBoundingClientRect();
            const ratio = Math.max(0, Math.min(1, boxTop / Math.max(1, scroller.scrollHeight - scroller.clientHeight)));
            const trackTop = Math.max(0, scrollRect.top - gridRect.top);
            const thumbRange = Math.max(0, scrollRect.height - 28);
            topPx = trackTop + (ratio * thumbRange);
          }
        }
        _jfSetAlphaIndicator(_jfIndicatorLabelForNode(pick, rowId), {show: !!showIndicator && canScroll, topPx});
      };
      let rafId = 0;
      scroller.addEventListener('scroll', () => {
        if (rafId) return;
        rafId = requestAnimationFrame(() => {
          rafId = 0;
          update(true);
        });
      }, {passive: true});
      update(false);
    }
  });
  host.appendChild(hostFrag);
  _jfApplySelectionUi();
}

function _jfSetBrowseUnavailable(reason){
  const host = document.getElementById('jfRows');
  if (!host) return;
  const wrap = document.createElement('div');
  wrap.className = 'jfUnavailable';
  const title = document.createElement('div');
  title.className = 'jfUnavailableTitle';
  title.textContent = 'Jellyfin is unavailable.';
  const body = document.createElement('div');
  const msg = String(reason || '').trim();
  body.textContent = msg || 'Check credentials/server URL, then reconnect.';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn jfReconnectInline';
  btn.textContent = 'Reconnect';
  wrap.appendChild(title);
  wrap.appendChild(body);
  wrap.appendChild(btn);
  host.innerHTML = '';
  host.appendChild(wrap);
}

async function _jfFetchWithTimeout(url, options, timeoutMs){
  const opts = Object.assign({}, options || {});
  const ms = Number(timeoutMs);
  const useTimeout = Number.isFinite(ms) && ms > 0;
  let timer = 0;
  let controller = null;
  if (useTimeout && typeof AbortController !== 'undefined') {
    controller = new AbortController();
    opts.signal = controller.signal;
    timer = setTimeout(() => {
      try { controller.abort(); } catch (_e) {}
    }, ms);
  }
  try {
    return await fetch(url, opts);
  } catch (e) {
    const name = String(e && e.name || '');
    if (name === 'AbortError') {
      const sec = Math.max(1, Math.round((useTimeout ? ms : __JF_REQ_TIMEOUT_MS) / 1000));
      throw new Error(`Request timed out (${sec}s)`);
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function _jfFetchJson(url){
  const r = await _jfFetchWithTimeout(url, {cache:'no-store'}, __JF_REQ_TIMEOUT_MS);
  let body = {};
  try { body = await r.json(); } catch (_e) {}
  if (!r.ok) {
    const msg = body.detail || body.reason || `HTTP ${r.status}`;
    throw new Error(String(msg));
  }
  return body;
}

async function loadJellyfinHome(force){
  if (__jfBusy) return;
  __jfBusy = true;
  try {
    _jfSetStatus('Loading…');
    _jfSetConn(false, 'Checking…');
    const j = await _jfFetchJson(`/jellyfin/home?limit=24${force ? '&refresh=1' : ''}`);
    __jfDashboardRows = Array.isArray(j.rows) ? j.rows : [];
    if (__jfActiveTab === 'dashboard') _jfRenderRows(__jfDashboardRows);
    __jfLastMode = 'home';
    __jfLastQuery = '';
    _jfApplySelectionUi();
    const up = !!(j.connected || j.authenticated);
    const reason = String(j.last_error || '').trim();
    _jfSetConn(up, up ? 'Connected' : (reason ? `Unavailable · ${reason}` : 'Unavailable'));
    if (!up) _jfSetBrowseUnavailable(reason);
    _jfSetStatus(j.connected ? 'Ready' : 'Ready (degraded)', j.connected ? 'ok' : '');
  } catch (e) {
    const msg = String(e?.message || e);
    _jfSetBrowseUnavailable(msg);
    _jfDetailPlaceholder('Jellyfin unavailable.');
    _jfSetConn(false, 'Unavailable');
    _jfSetStatus(`Error: ${msg}`, 'err');
  } finally {
    __jfBusy = false;
    _jfFlushPendingSearch();
  }
}

async function runJellyfinSearch(force){
  if (__jfBusy) {
    _jfQueuePendingSearch(force);
    return;
  }
  const q = (document.getElementById('jfSearchInput')?.value || '').trim();
  if (!q) {
    await _jfLoadActiveTabDefault(true);
    return;
  }
  __jfBusy = true;
  try {
    _jfSetStatus(`Searching "${q}"…`);
    _jfSetConn(false, 'Checking…');
    const j = await _jfFetchJson(`/jellyfin/search?q=${encodeURIComponent(q)}&limit=30${force ? '&refresh=1' : ''}`);
    const scopedItems = _jfFilterSearchItems(j.items || []);
    _jfRenderRows([{id:'search', title:_jfSearchTitle(q), items: scopedItems}]);
    __jfLastMode = 'search';
    __jfLastQuery = q;
    _jfApplySelectionUi();
    const up = !!(j.connected || j.authenticated);
    const reason = String(j.last_error || '').trim();
    _jfSetConn(up, up ? 'Connected' : (reason ? `Unavailable · ${reason}` : 'Unavailable'));
    if (!up) _jfSetBrowseUnavailable(reason);
    _jfSetStatus(`${scopedItems.length} result(s)`, 'ok');
  } catch (e) {
    _jfSetBrowseUnavailable(String(e?.message || e));
    _jfSetConn(false, 'Unavailable');
    _jfSetStatus(`Search failed: ${String(e?.message || e)}`, 'err');
  } finally {
    __jfBusy = false;
    _jfFlushPendingSearch();
  }
}

async function loadJellyfinMovies(force){
  if (__jfBusy) return;
  __jfBusy = true;
  try {
    _jfSetStatus('Loading movies…');
    _jfSetConn(false, 'Checking…');
    const qs = new URLSearchParams();
    qs.set('sort', __jfMoviesSort || 'added');
    qs.set('limit', String(__JF_CATALOG_LIMIT));
    qs.set('start', '0');
    if (force) qs.set('refresh', '1');
    const j = await _jfFetchJson(`/jellyfin/movies?${qs.toString()}`);
    const items = Array.isArray(j.items) ? j.items : [];
    __jfMoviesLimit = Math.max(1, Number(j.limit || __JF_CATALOG_LIMIT));
    __jfMoviesCount = Math.max(0, Number(j.count || items.length));
    _jfRenderRows([{id:'movies', title:'Movies', items}]);
    __jfLastMode = 'movies';
    __jfLastQuery = '';
    _jfApplySelectionUi();
    const up = !!(j.connected);
    const reason = String(j.last_error || '').trim();
    _jfSetConn(up, up ? 'Connected' : (reason ? `Unavailable · ${reason}` : 'Unavailable'));
    if (!up) _jfSetBrowseUnavailable(reason);
    _jfSetStatus(`Movies · ${Number(j.count || items.length)} item(s)`, up ? 'ok' : '');
    _jfSyncTabControls();
  } catch (e) {
    const msg = String(e?.message || e);
    _jfSetBrowseUnavailable(msg);
    _jfSetConn(false, 'Unavailable');
    _jfSetStatus(`Movies failed: ${msg}`, 'err');
  } finally {
    __jfBusy = false;
    _jfFlushPendingSearch();
  }
}

async function loadJellyfinTvSeries(force){
  if (__jfBusy) return;
  __jfBusy = true;
  try {
    _jfSetStatus('Loading series…');
    _jfSetConn(false, 'Checking…');
    const qs = new URLSearchParams();
    qs.set('sort', __jfTvSort || 'title_asc');
    qs.set('limit', String(__JF_CATALOG_LIMIT));
    qs.set('start', '0');
    if (force) qs.set('refresh', '1');
    const j = await _jfFetchJson(`/jellyfin/tv/series?${qs.toString()}`);
    const items = Array.isArray(j.items) ? j.items : [];
    __jfTvLimit = Math.max(1, Number(j.limit || __JF_CATALOG_LIMIT));
    __jfTvCount = Math.max(0, Number(j.count || items.length));
    _jfRenderRows([{id:'tv_series', title:'TV Series', items}]);
    __jfLastMode = 'tv';
    __jfLastQuery = '';
    _jfApplySelectionUi();
    __jfTvSeriesId = '';
    __jfTvSeriesTitle = '';
    __jfTvSeriesThumb = '';
    __jfTvSeasonNumber = null;
    __jfTvViewMode = 'series';
    const up = !!(j.connected);
    const reason = String(j.last_error || '').trim();
    _jfSetConn(up, up ? 'Connected' : (reason ? `Unavailable · ${reason}` : 'Unavailable'));
    if (!up) _jfSetBrowseUnavailable(reason);
    _jfSetStatus(`TV · ${Number(j.count || items.length)} series`, up ? 'ok' : '');
    _jfSyncTabControls();
  } catch (e) {
    const msg = String(e?.message || e);
    _jfSetBrowseUnavailable(msg);
    _jfSetConn(false, 'Unavailable');
    _jfSetStatus(`TV failed: ${msg}`, 'err');
  } finally {
    __jfBusy = false;
    _jfFlushPendingSearch();
  }
}

function _jfQueuePendingSearch(force){
  const nextForce = !!force;
  if (__jfPendingSearch && __jfPendingSearch.force) return;
  __jfPendingSearch = {force: nextForce};
}

function _jfFlushPendingSearch(){
  if (!__jfPendingSearch) return;
  const pending = __jfPendingSearch;
  __jfPendingSearch = null;
  _jfScheduleSearch(!!pending.force, 0);
}

function _jfScheduleSearch(force, delayMs){
  if (__jfSearchDebounceTimer) {
    clearTimeout(__jfSearchDebounceTimer);
    __jfSearchDebounceTimer = 0;
  }
  const waitMs = Number.isFinite(Number(delayMs)) ? Math.max(0, Number(delayMs)) : (force ? 0 : 280);
  __jfSearchDebounceTimer = window.setTimeout(() => {
    __jfSearchDebounceTimer = 0;
    runJellyfinSearch(!!force);
  }, waitMs);
}

function _jfFilterSearchItems(items){
  const list = Array.isArray(items) ? items : [];
  if (__jfActiveTab === 'movies') {
    return list.filter((item) => String(item && item.type ? item.type : '').toLowerCase() === 'movie');
  }
  if (__jfActiveTab === 'tv') {
    return list.filter((item) => String(item && item.type ? item.type : '').toLowerCase() === 'series');
  }
  return list;
}

function _jfSearchTitle(q){
  if (__jfActiveTab === 'movies') return `Movies · ${q}`;
  if (__jfActiveTab === 'tv') return `TV · ${q}`;
  return `Search · ${q}`;
}

function _jfLoadActiveTabDefault(force){
  _jfCloseDetailPanel();
  if (__jfActiveTab === 'dashboard') {
    loadJellyfinHome(!!force);
    return;
  }
  if (__jfActiveTab === 'movies') {
    loadJellyfinMovies(!!force);
    return;
  }
  __jfTvSeriesId = '';
  __jfTvSeriesTitle = '';
  __jfTvSeriesThumb = '';
  __jfTvSeasonNumber = null;
  loadJellyfinTvSeries(!!force);
}

async function loadJellyfinTvSeriesDetail(seriesId, opts){
  const sid = String(seriesId || '').trim();
  if (!sid) return;
  const title = String((opts && opts.title) || __jfTvSeriesTitle || 'Series').trim();
  const thumb = String(
    (opts && (opts.thumbnail_local || opts.thumbnail)) ||
    __jfTvSeriesThumb ||
    ''
  ).trim();
  const refresh = !!(opts && opts.refresh);
  if (__jfBusy) return;
  __jfBusy = true;
  try {
    _jfSetStatus('Loading seasons…');
    const seasonRes = await _jfFetchJson(`/jellyfin/tv/series/${encodeURIComponent(sid)}/seasons${refresh ? '?refresh=1' : ''}`);
    const seasons = Array.isArray(seasonRes.seasons) ? seasonRes.seasons : [];
    let seasonNum = __jfTvSeasonNumber;
    if (!Number.isFinite(Number(seasonNum))) {
      const first = seasons.find((s) => Number.isFinite(Number(s && s.season_number)));
      seasonNum = first ? Number(first.season_number) : null;
    }

    let epUrl = `/jellyfin/tv/series/${encodeURIComponent(sid)}/episodes`;
    const epQs = new URLSearchParams();
    if (Number.isFinite(Number(seasonNum))) epQs.set('season_number', String(Number(seasonNum)));
    if (refresh) epQs.set('refresh', '1');
    const epQuery = epQs.toString();
    if (epQuery) epUrl += `?${epQuery}`;
    const epRes = await _jfFetchJson(epUrl);
    const episodes = Array.isArray(epRes.episodes) ? epRes.episodes : [];

    const seasonItems = seasons.map((s) => ({
      item_id: `season:${sid}:${String(s && s.season_number || '')}`,
      title: String(s && s.title || 'Season').trim(),
      subtitle: String(s && s.subtitle || '').trim(),
      type: 'season',
      series_id: sid,
      season_id: String(s && s.season_id || '').trim(),
      season_number: Number(s && s.season_number),
      thumbnail: String((s && (s.thumbnail_local || s.thumbnail)) || '').trim(),
      thumbnail_local: String((s && s.thumbnail_local) || '').trim(),
    }));
    const rows = [
      {id:'tv_back', title:`${title}`, items:[{item_id:'tv_back', title:'← Back to Series', subtitle:'Return to all series', type:'nav_back', thumbnail: thumb, thumbnail_local: thumb}]},
      {id:'tv_seasons', title:'Seasons', items: seasonItems},
      {id:'tv_episodes', title:`Episodes${Number.isFinite(Number(seasonNum)) ? ` · Season ${Number(seasonNum)}` : ''}`, items: episodes},
    ];
    __jfTvSeriesId = sid;
    __jfTvSeriesTitle = title;
    __jfTvSeriesThumb = thumb;
    __jfTvSeasonNumber = Number.isFinite(Number(seasonNum)) ? Number(seasonNum) : null;
    __jfTvViewMode = 'detail';
    __jfSelectedItemId = Number.isFinite(Number(seasonNum)) ? `season:${sid}:${Number(seasonNum)}` : '';
    _jfRenderRows(rows);
    _jfApplySelectionUi();
    _jfSetStatus(`TV · ${title}`, 'ok');
    _jfSyncTabControls();
  } catch (e) {
    _jfSetStatus(`Series load failed: ${String(e?.message || e)}`, 'err');
  } finally {
    __jfBusy = false;
  }
}

async function _jfPlayAllSeries(seriesId, title){
  const sid = String(seriesId || '').trim();
  if (!sid) return;
  if (__jfActionBusy) {
    _jfSetActionStatus('Action already in progress…', '');
    return;
  }
  __jfActionBusy = true;
  _jfSetActionButtonsDisabled(true);
  _jfSetActionStatus('Queueing series…', '');
  try {
    const r = await _jfFetchWithTimeout(`/jellyfin/tv/series/${encodeURIComponent(sid)}/play_all`, {method: 'POST'}, __JF_REQ_TIMEOUT_MS);
    let j = {};
    try { j = await r.json(); } catch (_e) {}
    if (!r.ok || !j || j.ok === false) {
      const msg = String((j && (j.detail || j.reason || j.error)) || `HTTP ${r.status}`);
      _jfSetActionStatus(`Play All failed: ${msg}`, 'err', 12000);
      return;
    }
    const qn = Number(j.queued_count || 0);
    const label = String(j.series_title || title || '').trim() || 'Series';
    _jfSetActionStatus(`Play All queued: ${label} (${qn} up next)`, 'ok', 8000);
    await refresh();
  } catch (e) {
    _jfSetActionStatus(`Play All failed: ${String(e?.message || e)}`, 'err', 12000);
  } finally {
    __jfActionBusy = false;
    _jfSetActionButtonsDisabled(false);
  }
}

async function reconnectJellyfin(){
  if (__jfBusy) return;
  _jfSetStatus('Reconnecting…');
  try {
    const r = await _jfFetchWithTimeout('/integrations/jellyfin/register', {method:'POST'}, __JF_REQ_TIMEOUT_MS);
    const body = await r.json().catch(() => ({}));
    if (!r.ok || (body && body.ok === false)) {
      const msg = String((body && (body.reason || body.error || body.detail)) || `HTTP ${r.status}`);
      _jfSetConn(false, 'Unavailable');
      _jfSetStatus(`Reconnect failed: ${msg}`, 'err');
      _jfSetBrowseUnavailable(msg);
      return;
    }
    if (__jfLastMode === 'search' && __jfLastQuery) {
      await runJellyfinSearch(true);
      return;
    }
    await _jfLoadActiveTabDefault(true);
  } catch (e) {
    const msg = String(e?.message || e);
    _jfSetConn(false, 'Unavailable');
    _jfSetStatus(`Reconnect failed: ${msg}`, 'err');
    _jfSetBrowseUnavailable(msg);
  }
}

async function loadJellyfinDetail(itemId, opts){
  const iid = String(itemId || '').trim();
  if (!iid) return;
  const keepDetail = !!(opts && opts.keepDetail);
  const preloadThumb = !!(opts && opts.preloadThumb);
  __jfSelectedItemId = iid;
  _jfApplySelectionUi();
  _jfKeepSelectedItemInView(iid);
  if (!keepDetail) {
    _jfOpenDetailPanel();
    _jfDetailPlaceholder('Loading details…');
  }
  try {
    const j = await _jfFetchJson(`/jellyfin/item/${encodeURIComponent(iid)}`);
    __jfSelectedItem = (j && j.item) ? j.item : null;
    if (preloadThumb && __jfSelectedItem) {
      await _jfPreloadImage(__jfSelectedItem.thumbnail_local || __jfSelectedItem.thumbnail || '');
    }
    _jfRenderDetail(__jfSelectedItem || {});
    requestAnimationFrame(() => _jfKeepSelectedItemInView(iid));
  } catch (e) {
    __jfSelectedItem = null;
    _jfOpenDetailPanel();
    _jfDetailPlaceholder(`Failed to load detail: ${String(e?.message || e)}`);
  }
}

function _jfActionMsg(text, kind){
  const el = document.getElementById('jfActionMsg');
  if (!el) return;
  el.classList.remove('ok', 'err');
  if (kind === 'ok' || kind === 'err') el.classList.add(kind);
  el.textContent = text || '';
}

function _jfLightItemFromNode(node){
  if (!node) return null;
  const iid = String(node.getAttribute('data-item-id') || node.dataset.itemId || '').trim();
  if (!iid) return null;
  const out = {
    item_id: iid,
    title: String(node.getAttribute('data-item-title') || node.dataset.itemTitle || '').trim(),
    subtitle: String(node.getAttribute('data-item-subtitle') || node.dataset.itemSubtitle || '').trim(),
  };
  const rpRaw = String(node.getAttribute('data-item-resume-pos') || node.dataset.itemResumePos || '').trim();
  const rp = Number(rpRaw);
  if (Number.isFinite(rp) && rp > 0) out.resume_pos = rp;
  return out;
}

function _jfRowItems(row){
  if (!row) return [];
  return Array.from(row.querySelectorAll('.jfScroller .jfItem'));
}

function _jfKeepSelectedItemInView(itemId){
  const iid = String(itemId || '').trim();
  if (!iid) return;
  const all = Array.from(document.querySelectorAll('#jfRows .jfItem'));
  const node = all.find((n) => String(n.getAttribute('data-item-id') || '').trim() === iid);
  if (!node) return;
  try {
    node.scrollIntoView({block:'nearest', inline:'nearest', behavior:'smooth'});
  } catch (_e) {}
  const scroller = node.closest('.jfScroller');
  if (!scroller) return;
  const nl = node.offsetLeft;
  const nr = nl + node.offsetWidth;
  const sl = scroller.scrollLeft;
  const sr = sl + scroller.clientWidth;
  if (nl < sl || nr > sr) {
    const targetLeft = Math.max(0, Math.round(nl - ((scroller.clientWidth - node.offsetWidth) / 2)));
    try { scroller.scrollTo({left: targetLeft, behavior: 'smooth'}); } catch (_e) { scroller.scrollLeft = targetLeft; }
  }
  const nt = node.offsetTop;
  const nb = nt + node.offsetHeight;
  const st = scroller.scrollTop;
  const sb = st + scroller.clientHeight;
  if (nt < st || nb > sb) {
    const targetTop = Math.max(0, Math.round(nt - ((scroller.clientHeight - node.offsetHeight) / 2)));
    try { scroller.scrollTo({top: targetTop, behavior: 'smooth'}); } catch (_e) { scroller.scrollTop = targetTop; }
  }
}

function _jfMoveHorizontal(item, delta){
  const row = item && item.closest ? item.closest('.jfRow') : null;
  if (!row) return false;
  const items = _jfRowItems(row);
  if (!items.length) return false;
  const idx = items.indexOf(item);
  if (idx < 0) return false;
  const next = items[idx + delta];
  if (!next) return false;
  next.focus();
  return true;
}

function _jfMoveVertical(item, delta){
  const row = item && item.closest ? item.closest('.jfRow') : null;
  if (!row) return false;
  const rows = Array.from(document.querySelectorAll('#jfRows .jfRow'));
  if (!rows.length) return false;
  const rowIdx = rows.indexOf(row);
  if (rowIdx < 0) return false;
  const nextRow = rows[rowIdx + delta];
  if (!nextRow) return false;
  const curItems = _jfRowItems(row);
  const curIdx = Math.max(0, curItems.indexOf(item));
  const nextItems = _jfRowItems(nextRow);
  if (!nextItems.length) return false;
  const target = nextItems[Math.min(curIdx, nextItems.length - 1)];
  target.focus();
  return true;
}

function _jfFocusSelectedItem(){
  const selected = document.querySelector('.jfItem.selected');
  if (selected) {
    selected.focus();
    return true;
  }
  const first = document.querySelector('.jfItem');
  if (first) {
    first.focus();
    return true;
  }
  return false;
}

function _jfFocusDetailPrimary(){
  const btn =
    document.querySelector('#jfDetail .jfThumbNav:not(:disabled)') ||
    document.querySelector('#jfDetail .jfActionRow button');
  if (!btn) return false;
  btn.focus();
  return true;
}

function _jfNotifyAction(target, text, kind){
  const msg = String(text || '');
  const pending = msg.endsWith('…') || msg.endsWith('...');
  const holdMs = pending ? 0 : (kind === 'err' ? 12000 : 8000);
  _jfSetActionStatus(msg, kind, holdMs);
  if (target === 'detail') {
    _jfActionMsg(text, kind);
    return;
  }
}

function _jfSetActionButtonsDisabled(disabled){
  document.querySelectorAll('#jfDetail .jfActionRow button, #jfRows .jfQuickBtn').forEach((b) => {
    b.disabled = !!disabled;
  });
  const searchInput = document.getElementById('jfSearchInput');
  if (searchInput) searchInput.disabled = !!disabled;
  const sortSel = document.getElementById('jfSortSelect');
  if (sortSel) sortSel.disabled = !!disabled || (__jfLastMode === 'search');
  _jfSyncTabControls();
}

async function _jfPerformItemAction(item, kind, target){
  if (__jfActionBusy) {
    _jfNotifyAction(target, 'Action already in progress…', '');
    return {ok: false};
  }
  if (!__jfConnected) {
    _jfNotifyAction(target, 'Jellyfin unavailable. Reconnect first.', 'err');
    return {ok: false};
  }
  __jfActionBusy = true;
  _jfSetActionButtonsDisabled(true);
  const itemId = String(item && item.item_id ? item.item_id : '').trim();
  if (!itemId) {
    _jfNotifyAction(target, 'Select a Jellyfin item first.', 'err');
    __jfActionBusy = false;
    _jfSetActionButtonsDisabled(false);
    return {ok: false};
  }

  const body = {item_id: itemId, command: kind};
  let human = 'Play';
  if (kind === 'play_next') {
    human = 'Play Next';
  } else if (kind === 'play_last') {
    human = 'Queue';
  } else {
    human = (kind === 'resume') ? 'Resume' : 'Play Now';
    if (kind === 'resume') {
      const rp = Number(item.resume_pos);
      if (Number.isFinite(rp) && rp > 0) body.resume_pos = rp;
    }
  }

  _jfNotifyAction(target, `${human}…`, '');
  try {
    const r = await _jfFetchWithTimeout('/jellyfin/action', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body),
    }, __JF_REQ_TIMEOUT_MS);
    let j = {};
    try { j = await r.json(); } catch (_e) {}
    if (!r.ok || !j || j.ok === false) {
      const msg = (j && (j.detail || j.reason || j.error)) ? (j.detail || j.reason || j.error) : `HTTP ${r.status}`;
      _jfNotifyAction(target, `Action failed: ${msg}`, 'err');
      return {ok: false};
    }

    if (j && j.suppressed_duplicate_ui_action) {
      _jfNotifyAction(target, 'Ignored duplicate action.', 'ok');
      return {ok: true};
    }
    if (j && j.suppressed_duplicate_command) {
      _jfNotifyAction(target, 'Ignored duplicate command.', 'ok');
      return {ok: true};
    }
    if (j && j.suppressed_duplicate) {
      _jfNotifyAction(target, 'Ignored duplicate play request.', 'ok');
      return {ok: true};
    }

    let msg = `${human} sent.`;
    if (j.action === 'queue_only') {
      const n = Number(j.queued || 0);
      const qlen = Number(j.queue_length || 0);
      msg = n > 0 ? `Queued ${n} item${n === 1 ? '' : 's'} · Queue ${qlen}` : `Already queued · Queue ${qlen}`;
    } else if (j.action === 'play') {
      const np = (j.now_playing && typeof j.now_playing === 'object') ? j.now_playing : {};
      const label = String(np.title || item.title || '').trim();
      if (kind === 'resume') {
        const rp = Number(j.resolved_resume_pos || item.resume_pos || 0);
        const rpTxt = (Number.isFinite(rp) && rp > 0) ? ` from ${_jfFmtSec(rp)}` : '';
        msg = label ? `Now playing: ${label}${rpTxt}` : `Resume started${rpTxt}`;
      } else {
        msg = label ? `Now playing: ${label}` : `${human} started`;
      }
    }

    _jfNotifyAction(target, msg, 'ok');
    await refresh();
    return {ok: true};
  } catch (e) {
    _jfNotifyAction(target, `Action failed: ${String(e?.message || e)}`, 'err');
    return {ok: false};
  } finally {
    __jfActionBusy = false;
    _jfSetActionButtonsDisabled(false);
  }
}

async function jellyfinDetailAction(kind){
  const item = __jfSelectedItem;
  if (!item) {
    _jfActionMsg('Select a Jellyfin item first.', 'err');
    return;
  }
  await _jfPerformItemAction(item, kind, 'detail');
}

function bindJellyfinUi(){
  const launchBtn = document.getElementById('jellyfinOpenBtn');
  const shellBack = document.getElementById('jfShellBackBtn');
  const detailBackdrop = document.getElementById('jfDetailBackdrop');
  const searchInput = document.getElementById('jfSearchInput');
  const sortSelect = document.getElementById('jfSortSelect');
  const rows = document.getElementById('jfRows');
  const detail = document.getElementById('jfDetail');
  const tabBtns = Array.from(document.querySelectorAll('.jfTabBtn'));

  if (!__jfResizeBound) {
    const onResize = () => {
      if (!_jfIsDetailOpen()) return;
      _jfSetDetailScrollLock(true);
      _jfPositionDetailPanel();
    };
    window.addEventListener('resize', onResize, {passive:true});
    window.addEventListener('orientationchange', onResize, {passive:true});
    __jfResizeBound = true;
  }
  if (!__jfViewportBound && window.visualViewport && typeof window.visualViewport.addEventListener === 'function') {
    const onViewportChange = () => {
      if (!_jfIsDetailOpen()) return;
      _jfSetDetailScrollLock(true);
      _jfPositionDetailPanel();
    };
    window.visualViewport.addEventListener('resize', onViewportChange, {passive:true});
    window.visualViewport.addEventListener('scroll', onViewportChange, {passive:true});
    __jfViewportBound = true;
  }

  if (launchBtn) launchBtn.onclick = () => openJellyfinShell();
  if (shellBack) shellBack.onclick = () => closeJellyfinShell();
  if (detailBackdrop) detailBackdrop.onclick = () => _jfCloseDetailPanel();
  tabBtns.forEach((btn) => {
    btn.onclick = () => {
      const tab = String(btn.getAttribute('data-jf-tab') || '').trim();
      _jfSetActiveTab(tab, {refresh:false});
    };
    btn.addEventListener('keydown', (e) => {
      const key = String(e.key || '');
      const idx = tabBtns.indexOf(btn);
      if (idx < 0) return;
      if (key === 'ArrowRight') {
        const next = tabBtns[(idx + 1) % tabBtns.length];
        if (next) {
          next.focus();
          next.click();
          e.preventDefault();
        }
        return;
      }
      if (key === 'ArrowLeft') {
        const prev = tabBtns[(idx - 1 + tabBtns.length) % tabBtns.length];
        if (prev) {
          prev.focus();
          prev.click();
          e.preventDefault();
        }
        return;
      }
      if (key === 'Home') {
        const first = tabBtns[0];
        if (first) {
          first.focus();
          first.click();
          e.preventDefault();
        }
        return;
      }
      if (key === 'End') {
        const last = tabBtns[tabBtns.length - 1];
        if (last) {
          last.focus();
          last.click();
          e.preventDefault();
        }
      }
    });
  });
  if (sortSelect) {
    sortSelect.onchange = () => {
      const v = String(sortSelect.value || '').trim().toLowerCase();
      if (__jfActiveTab === 'movies') {
        __jfMoviesSort = v || 'added';
      }
      if (__jfActiveTab === 'tv') {
        __jfTvSort = v || 'title_asc';
        __jfTvViewMode = 'series';
      }
      _jfSetActiveTab(__jfActiveTab, {refresh:false});
    };
  }

  if (searchInput) {
    searchInput.addEventListener('input', () => _jfScheduleSearch(false));
    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') _jfScheduleSearch(true, 0);
      if (e.key === 'ArrowDown') {
        const first = document.querySelector('.jfItem');
        if (first) {
          first.focus();
          e.preventDefault();
        }
      }
      if (e.key === 'Escape') {
        searchInput.value = '';
        _jfLoadActiveTabDefault(true);
        e.preventDefault();
      }
    });
  }
  if (rows) {
    rows.addEventListener('click', (e) => {
      const reconnect = e.target && e.target.closest ? e.target.closest('.jfReconnectInline') : null;
      if (reconnect) {
        reconnectJellyfin();
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      const quick = e.target && e.target.closest ? e.target.closest('.jfQuickBtn') : null;
      const target = e.target && e.target.closest ? e.target.closest('.jfItem') : null;
      if (!target) return;
      const rich = _jfSeriesItemFromNode(target);
      if (__jfActiveTab === 'tv' && _jfIsSeriesNavType(rich)) {
        _jfOpenSeriesDetailFromRich(rich);
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      if (quick) {
        const action = String(quick.getAttribute('data-jf-action') || '').trim();
        if (__jfActiveTab === 'tv' && rich) {
          if (action === 'view_series') {
            _jfOpenSeriesDetailFromRich(rich);
            e.preventDefault();
            e.stopPropagation();
            return;
          }
          if (action === 'play_all_series' && rich.type === 'series') {
            _jfPlayAllSeries(rich.item_id, rich.title);
            e.preventDefault();
            e.stopPropagation();
            return;
          }
        }
        const item = _jfLightItemFromNode(target);
        if (item && action) _jfPerformItemAction(item, action, 'status');
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      const iid = target.dataset.itemId || '';
      loadJellyfinDetail(iid);
    });

    rows.addEventListener('keydown', (e) => {
      const target = e.target && e.target.closest ? e.target.closest('.jfItem') : null;
      if (!target) return;
      const quick = e.target && e.target.closest ? e.target.closest('.jfQuickBtn') : null;
      const iid = String(target.getAttribute('data-item-id') || '').trim();
      const item = _jfLightItemFromNode(target);
      const rich = _jfSeriesItemFromNode(target);
      const key = String(e.key || '');
      if (key === 'ArrowRight') {
        if (quick) {
          const btns = Array.from(target.querySelectorAll('.jfQuickBtn'));
          const idx = btns.indexOf(quick);
          if (idx >= 0 && idx + 1 < btns.length) {
            btns[idx + 1].focus();
            e.preventDefault();
            return;
          }
        }
        if (_jfMoveHorizontal(target, +1)) {
          e.preventDefault();
          return;
        }
        if (_jfFocusDetailPrimary()) {
          e.preventDefault();
          return;
        }
      }
      if (key === 'ArrowLeft') {
        if (quick) {
          const btns = Array.from(target.querySelectorAll('.jfQuickBtn'));
          const idx = btns.indexOf(quick);
          if (idx > 0) {
            btns[idx - 1].focus();
            e.preventDefault();
            return;
          }
        }
        if (_jfMoveHorizontal(target, -1)) {
          e.preventDefault();
          return;
        }
      }
      if (key === 'ArrowDown') {
        if (quick) {
          target.focus();
          e.preventDefault();
          return;
        }
        if (_jfMoveVertical(target, +1)) {
          e.preventDefault();
          return;
        }
      }
      if (key === 'ArrowUp') {
        if (quick) {
          target.focus();
          e.preventDefault();
          return;
        }
        if (_jfMoveVertical(target, -1)) {
          e.preventDefault();
          return;
        }
      }
      if (key === 'Enter') {
        if (__jfActiveTab === 'tv' && _jfIsSeriesNavType(rich)) {
          _jfOpenSeriesDetailFromRich(rich);
          e.preventDefault();
          return;
        }
        if (quick) {
          const action = String(quick.getAttribute('data-jf-action') || '').trim();
          if (__jfActiveTab === 'tv' && rich) {
            if (action === 'view_series') {
              _jfOpenSeriesDetailFromRich(rich);
              e.preventDefault();
              return;
            }
            if (action === 'play_all_series' && rich.type === 'series') {
              _jfPlayAllSeries(rich.item_id, rich.title);
              e.preventDefault();
              return;
            }
          }
          if (item && action) _jfPerformItemAction(item, action, 'status');
          e.preventDefault();
          return;
        }
        if (iid) loadJellyfinDetail(iid);
        e.preventDefault();
        return;
      }
      if ((key === 'p' || key === 'P') && item) {
        _jfPerformItemAction(item, 'play_now', 'status');
        e.preventDefault();
        return;
      }
      if ((key === 'n' || key === 'N') && item) {
        _jfPerformItemAction(item, 'play_next', 'status');
        e.preventDefault();
        return;
      }
      if ((key === 'l' || key === 'L') && item) {
        _jfPerformItemAction(item, 'play_last', 'status');
        e.preventDefault();
        return;
      }
      if ((key === 'r' || key === 'R') && item) {
        _jfPerformItemAction(item, 'resume', 'status');
        e.preventDefault();
      }
    });
  }
  if (detail) {
    detail.addEventListener('keydown', (e) => {
      const navBtn = e.target && e.target.closest ? e.target.closest('.jfThumbNav') : null;
      const actionBtn = e.target && e.target.closest ? e.target.closest('.jfActionRow button') : null;
      if (!navBtn && !actionBtn) return;
      const all = Array.from(detail.querySelectorAll('.jfActionRow button'));
      const idx = actionBtn ? all.indexOf(actionBtn) : -1;
      const key = String(e.key || '');
      if (navBtn) {
        const left = detail.querySelector('.jfThumbNav.prev');
        const right = detail.querySelector('.jfThumbNav.next');
        if (key === 'ArrowRight') {
          if (navBtn === left && right && !right.disabled) {
            right.focus();
            e.preventDefault();
            return;
          }
          if (navBtn === right && all.length) {
            all[0].focus();
            e.preventDefault();
          }
          return;
        }
        if (key === 'ArrowLeft') {
          if (navBtn === right && left && !left.disabled) {
            left.focus();
            e.preventDefault();
            return;
          }
          if (_jfFocusSelectedItem()) e.preventDefault();
          return;
        }
        if (key === 'ArrowDown') {
          if (all.length) {
            all[0].focus();
            e.preventDefault();
          }
          return;
        }
        if (key === 'ArrowUp') {
          if (_jfFocusSelectedItem()) e.preventDefault();
          return;
        }
        if (key === 'Escape') {
          _jfCloseDetailPanel();
          if (_jfFocusSelectedItem()) e.preventDefault();
        }
        return;
      }
      if (idx < 0) return;
      if (key === 'ArrowRight') {
        if (idx + 1 < all.length) {
          all[idx + 1].focus();
          e.preventDefault();
        }
        return;
      }
      if (key === 'ArrowLeft') {
        if (idx > 0) {
          all[idx - 1].focus();
          e.preventDefault();
          return;
        }
        if (_jfFocusSelectedItem()) {
          e.preventDefault();
        }
        return;
      }
      if (key === 'ArrowDown') {
        if (idx + 2 < all.length) {
          all[idx + 2].focus();
          e.preventDefault();
          return;
        }
        if (idx + 1 < all.length) {
          all[idx + 1].focus();
          e.preventDefault();
        }
        return;
      }
      if (key === 'ArrowUp') {
        if (idx - 2 >= 0) {
          all[idx - 2].focus();
          e.preventDefault();
          return;
        }
        if (_jfFocusSelectedItem()) {
          e.preventDefault();
        }
        return;
      }
      if (key === 'Escape') {
        _jfCloseDetailPanel();
        if (_jfFocusSelectedItem()) e.preventDefault();
      }
    });
  }
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && __jfUiVisible) {
      if (_jfIsDetailOpen()) {
        _jfCloseDetailPanel();
      } else {
        closeJellyfinShell();
      }
      e.preventDefault();
      return;
    }
    if (!__jfUiVisible) return;
    const activeTag = (document.activeElement && document.activeElement.tagName) ? document.activeElement.tagName.toLowerCase() : '';
    const typing = activeTag === 'input' || activeTag === 'textarea';
    if (!typing && e.key === '/') {
      if (searchInput) {
        searchInput.focus();
        searchInput.select();
      }
      e.preventDefault();
    }
    if (!typing && (e.key === '1' || e.key === '2' || e.key === '3')) {
      const keyMap = { '1': 'dashboard', '2': 'movies', '3': 'tv' };
      const nextTab = keyMap[e.key];
      if (nextTab) {
        _jfSetActiveTab(nextTab, {refresh:false});
        const tabBtn = document.querySelector(`.jfTabBtn[data-jf-tab="${nextTab}"]`);
        if (tabBtn && typeof tabBtn.focus === 'function') tabBtn.focus();
        e.preventDefault();
      }
    }
    if (!typing && (e.key === 'j' || e.key === 'J')) {
      const first = document.querySelector('.jfItem');
      if (first) {
        first.focus();
        e.preventDefault();
      }
    }
  });
}

function renderStatus(st) {
  if (!st) return;
  if (_uiRefreshInteractionLockActive()) return;
  _jfSetLaunchVisible(_jfCanLaunchFromStatus(st));

  // state pill
  const dot = document.getElementById('dot');
  const state = document.getElementById('state');
  const brand = document.getElementById('appBrandName');
  const sess = st.state || (st.playing ? (st.paused ? 'paused' : 'playing') : 'idle');
  if (brand) brand.textContent = st.device_name || 'RelayTV';
  if (dot) {
    dot.className = 'dot' + (sess === 'playing' ? ' playing' : (sess === 'paused' ? ' paused' : (sess === 'closed' ? ' closed' : '')));
  }
  if (state) state.textContent = sess;

  // now playing
  const np = st.now_playing || {};
  const picon = document.getElementById('picon');
  const hasNow = _hasNowPlayingItem(st, np);
  const fav = hasNow ? faviconUrl(np) : '/pwa/brand/logo.svg';
  picon.innerHTML = fav ? `<img src="${fav}" alt="" />` : '🎞️';
  document.getElementById('now').textContent = hasNow ? (np.title || 'Now Playing') : 'Ready';
  document.getElementById('nowSub').textContent = hasNow ? (displaySub(np) || '') : '';
  if (picon) picon.classList.toggle('hidden', !hasNow);
  _renderNowLanguageButton(st, np, hasNow);
  _renderNowSubtitleButton(st, np, hasNow);
  const nowSkipBtn = document.getElementById('nowSkipBtn');
  if (nowSkipBtn) {
    const canSkipNow = !!hasNow;
    nowSkipBtn.classList.toggle('hidden', !canSkipNow);
    nowSkipBtn.onclick = async (e) => {
      try { if (e) e.preventDefault(); } catch(_){}
      if (Number(st.queue_length || 0) > 0) {
        await post('/next');
      } else {
        await post('/close');
      }
    };
  }

  // background thumbnail (YouTube supported; others fall back to none)
  setBg(document.getElementById('nowTopCard'), thumbUrl(np));

  const posTxt = fmtTime(st.position);
  const durTxt = fmtTime(st.duration);

  // Only overwrite the pos readout if not scrubbing
  if (!__scrubbing) document.getElementById('pos').textContent = posTxt;
  document.getElementById('dur').textContent = durTxt;

  _renderRemoteVolume(st.volume);
  const mute = !!st.mute;
  const mb = document.getElementById('muteBtn');
  if (mb){
    // update label/icon subtly
    mb.querySelector('.bIcon').textContent = mute ? '🔇' : '🔈';
    mb.querySelector('span:last-child').textContent = mute ? 'Unmute' : 'Mute';
  }
  document.getElementById('qlen').textContent = st.queue_length || 0;

  // progress bar fill
  if (!__scrubbing && st.position != null && st.duration != null && st.duration > 0) {
    _setProgressFill(st.position / st.duration);
  } else if (!__scrubbing && (!st.playing || st.duration == null || st.duration <= 0)) {
    _setProgressFill(0);
  }

  // queue list
  const ol = document.getElementById('queue');

  // If a drag got stuck (e.g., pointerup missed), recover so UI keeps rendering.
  if (__draggingQueue && __dragStartTs && (Date.now() - __dragStartTs) > 8000) {
    try { if (typeof __queueDnDCleanup === 'function') __queueDnDCleanup(); } catch(_e) {}
  }

  if (!__draggingQueue) {
    ol.innerHTML = '';
    (st.queue || []).forEach((item, idx) => {
    const li = document.createElement('li');
    li.className = 'qTile';
    if (item && item.available === false) li.classList.add('isUnavailable');
    li.dataset.index = String(idx);

    setBg(li, thumbUrl(item));

    // Big, faint provider logo behind handle
    const bg = document.createElement('div');
    bg.className = 'qProvBg';
    const bgFav = faviconUrl(item);
    if (bgFav){
      bg.innerHTML = `<img src="${bgFav}" alt="" />`;
      li.appendChild(bg);
    }

    // Drag handle (hamburger)
    const handle = document.createElement('div');
    handle.className = 'qHandle';
    handle.innerHTML = `
      <svg class="qGrip" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <path d="M8 6h8M8 12h8M8 18h8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      </svg>`;
    handle.title = 'Drag to reorder';

    const body = document.createElement('div');
    body.className = 'qBody';

    const title = document.createElement('div');
    title.className = 'qTitle';

    const favImg = document.createElement('img');
    favImg.className = 'fav';
    favImg.alt = '';
    favImg.loading = 'lazy';
    favImg.src = faviconUrl(item) || '';

    const tspan = document.createElement('span');
    tspan.className = 'qTitleText';
    tspan.textContent = item.title || item.url || '';

    if (favImg.src) title.appendChild(favImg);
    title.appendChild(tspan);
    const titleBadge = _uploadBadge(item);
    if (titleBadge) title.insertAdjacentHTML('beforeend', titleBadge);

    const chan = document.createElement('div');
    chan.className = 'qChan';
    chan.textContent = displaySub(item) || '';

    body.appendChild(title);
    body.appendChild(chan);

    const del = document.createElement('button');
    del.className = 'qDelBtn';
    del.textContent = '✕';
    del.title = 'Remove from queue';
    del.onclick = () => qRemove(idx);

    li.appendChild(handle);
    li.appendChild(body);
    li.appendChild(del);
    ol.appendChild(li);
  });
  }

  // Bind once (event delegation on the <ol>)
  bindQueuePointerDnD();

}

async function refresh() {
  let st = __lastStatus || null;
  let fast = null;
  try {
    fast = await _fetchFastPlaybackState();
    st = _mergePlaybackStateIntoStatus(st, fast);
  } catch(_e) {}

  try {
    if (_shouldRefreshFullStatus(st, fast)) {
      const full = await _fetchFullStatus();
      st = fast ? _mergePlaybackStateIntoStatus(full, fast) : full;
    }
  } catch(_e) {
    if (!st) return;
  }

  if (!st) return;
  __lastStatus = st;
  renderStatus(st);
}

function _applyUiPlaybackEvent(payload){
  if (!payload || typeof payload !== 'object') return;
  _uiEventMarkAlive();
  const merged = _mergePlaybackStateIntoStatus(__lastStatus || {}, payload);
  __lastStatus = merged;
  renderStatus(merged);
}

function _applyUiStatusEvent(payload){
  if (!payload || typeof payload !== 'object') return;
  _uiEventMarkAlive();
  __lastStatus = payload;
  __lastStatusFullFetchTs = Date.now();
  renderStatus(payload);
}

function _applyUiQueueEvent(payload){
  if (!payload || typeof payload !== 'object') return;
  _uiEventMarkAlive();
  const applied = _applyQueueSnapshot(payload);
  if (applied && __lastStatus) renderStatus(__lastStatus);
  if (!applied || _uiRefreshInteractionLockActive()) {
    refresh().catch(() => {});
  }
}

function _applyUiJellyfinEvent(payload){
  if (!payload || typeof payload !== 'object') return;
  _uiEventMarkAlive();

  const settingsBd = document.getElementById('settingsBackdrop');
  const settingsOpen = !!(settingsBd && !settingsBd.classList.contains('hidden'));
  if (payload.refresh_settings && settingsOpen) {
    loadSettingsUi().catch(console.warn);
  }

  if (!payload.refresh_active_tab || !__jfUiVisible) {
    if (payload.refresh_status) refresh().catch(() => {});
    return;
  }

  if (__jfBusy) {
    window.setTimeout(() => _applyUiJellyfinEvent(payload), 700);
    return;
  }

  if (__jfLastMode === 'search' && __jfLastQuery) {
    runJellyfinSearch(true).catch(console.warn);
  } else if (__jfActiveTab === 'tv' && __jfTvViewMode === 'detail' && __jfTvSeriesId) {
    loadJellyfinTvSeriesDetail(__jfTvSeriesId, {
      title: __jfTvSeriesTitle,
      thumbnail: __jfTvSeriesThumb,
      thumbnail_local: __jfTvSeriesThumb,
      refresh: true,
    }).catch(console.warn);
  } else {
    _jfLoadActiveTabDefault(true);
  }

  if (__jfSelectedItemId && _jfIsDetailOpen()) {
    loadJellyfinDetail(__jfSelectedItemId, {keepDetail:true}).catch(console.warn);
  }
}

function connectUiEventStream(){
  if (__uiEventSource) return;
  let es = null;
  try {
    es = new EventSource('/ui/events');
  } catch (_e) {
    _scheduleUiEventReconnect();
    return;
  }
  __uiEventSource = es;
  _uiEventMarkAlive();

  es.addEventListener('hello', (ev) => {
    _uiEventMarkAlive();
    const payload = _parseUiEventPayload(ev);
    if (payload && payload.type === 'hello' && !__lastStatus) {
      refresh().catch(() => {});
    }
  });
  es.addEventListener('ping', () => {
    _uiEventMarkAlive();
  });
  es.addEventListener('playback', (ev) => {
    _applyUiPlaybackEvent(_parseUiEventPayload(ev));
  });
  es.addEventListener('status', (ev) => {
    _applyUiStatusEvent(_parseUiEventPayload(ev));
  });
  es.addEventListener('queue', (ev) => {
    _applyUiQueueEvent(_parseUiEventPayload(ev));
  });
  es.addEventListener('jellyfin', (ev) => {
    _applyUiJellyfinEvent(_parseUiEventPayload(ev));
  });
  es.onerror = () => {
    if (__uiEventSource !== es) return;
    try { es.close(); } catch (_e) {}
    __uiEventSource = null;
    _scheduleUiEventReconnect();
  };
}

// --- History modal (hidden by default)
async function fetchHistory(){
  const r = await fetch('/history', {cache:'no-store'});
  return await r.json();
}

function closeHeaderMenu(){
  const panel = document.getElementById('hdrMenuPanel');
  const btn = document.getElementById('hdrMenuBtn');
  if (panel) panel.classList.add('hidden');
  if (btn) btn.setAttribute('aria-expanded', 'false');
}

function bindHeaderMenu(){
  const wrap = document.getElementById('hdrMenuWrap');
  const btn = document.getElementById('hdrMenuBtn');
  const panel = document.getElementById('hdrMenuPanel');
  if (!btn || !panel || !wrap) return;

  btn.onclick = (e) => {
    try { if (e) e.preventDefault(); } catch(_){}
    const isHidden = panel.classList.contains('hidden');
    panel.classList.toggle('hidden', !isHidden);
    btn.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
  };
  panel.addEventListener('pointerdown', (e) => {
    try { e.stopPropagation(); } catch(_){}
  });
  panel.addEventListener('click', (e) => {
    try { e.stopPropagation(); } catch(_){}
  });

  document.addEventListener('click', (e) => {
    if (panel.classList.contains('hidden')) return;
    const t = e && e.target;
    if (t && t.closest && t.closest('#hdrMenuWrap')) return;
    closeHeaderMenu();
  });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeHeaderMenu();
  });
}

function _fmtTs(ts){
  try {
    const d = new Date((ts||0)*1000);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleString();
  } catch (_) { return ''; }
}

function _uploadBadge(item){
  if (!item || String(item.provider || '').trim().toLowerCase() !== 'upload') return '';
  const unavailable = item.available === false;
  return `<span class="mediaBadge${unavailable ? ' unavailable' : ''}">${unavailable ? 'Removed' : 'Uploaded'}</span>`;
}

function openHistory(){
  closeHeaderMenu();
  const bd = document.getElementById('histBackdrop');
  if (!bd || !bd.classList.contains('hidden')) return;
  bd.classList.remove('hidden');
  _uiPushLayer();
  renderHistory();
}

function closeHistory(opts){
  const bd = document.getElementById('histBackdrop');
  if (!bd) return;
  const fromNav = !!(opts && opts.fromNav);
  if (!fromNav && !bd.classList.contains('hidden') && __uiNavDepth > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  bd.classList.add('hidden');
}

async function renderHistory(){
  const list = document.getElementById('histList');
  if (!list) return;
  list.innerHTML = '';

  const data = await fetchHistory();
  const items = data.history || [];
  if (!items.length){
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.textContent = 'No history yet.';
    list.appendChild(empty);
    return;
  }

  items.forEach((it, idx) => {
    const available = it && it.available !== false;
    const row = document.createElement('div');
    row.className = 'histItem';
    if (!available) row.classList.add('isUnavailable');
    setBg(row, thumbUrl(it));

    const bgFav = faviconUrl(it);
    if (bgFav){
      const bg = document.createElement('div');
      bg.className = 'histProvBg';
      bg.innerHTML = `<img src="${bgFav}" alt="" />`;
      row.appendChild(bg);
    }

    const meta = document.createElement('div');
    meta.className = 'histMeta';

    const title = document.createElement('div');
    title.className = 'histTitle';

    const fav = faviconUrl(it);
    if (fav){
      const favImg = document.createElement('img');
      favImg.className = 'fav';
      favImg.alt = '';
      favImg.loading = 'lazy';
      favImg.src = fav;
      title.appendChild(favImg);
    }

    const tspan = document.createElement('span');
    tspan.className = 'histTitleText';
    tspan.textContent = it.title || it.url || '(unknown)';
    title.appendChild(tspan);
    const titleBadge = _uploadBadge(it);
    if (titleBadge) title.insertAdjacentHTML('beforeend', titleBadge);

    const channel = document.createElement('div');
    channel.className = 'histSub';
    channel.textContent = displaySub(it) || '';

    const sub = document.createElement('div');
    sub.className = 'histSub';
    sub.textContent = `${_fmtTs(it.ts)}  •  ${it.mode || ''}`.trim();

    const url = document.createElement('div');
    url.className = 'histSub';
    url.textContent = available ? (it.url || '') : 'Playback unavailable: stored upload was removed';

    const btns = document.createElement('div');
    btns.className = 'histBtns';

    const play = document.createElement('button');
    play.textContent = 'Play';
    play.disabled = !available;
    play.onclick = async () => {
      if (!available) return;
      await fetch('/history/play', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({index: idx})});
      closeHistory();
      await refresh();
    };

    const queue = document.createElement('button');
    queue.textContent = 'Queue';
    queue.disabled = !available;
    queue.onclick = async () => {
      if (!available) return;
      if (!it.url) return;
      await fetch('/enqueue', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({url: it.url})});
      await refresh();
    };

    btns.appendChild(play);
    btns.appendChild(queue);

    meta.appendChild(title);
    meta.appendChild(channel);
    meta.appendChild(sub);
    meta.appendChild(url);
    meta.appendChild(btns);

    row.appendChild(meta);
    list.appendChild(row);
  });
}

function bindHistoryUi(){
  const btn = document.getElementById('histBtn');
  const closeBtn = document.getElementById('histCloseBtn');
  const clearBtn = document.getElementById('histClearBtn');
  const bd = document.getElementById('histBackdrop');

  if (btn) btn.onclick = openHistory;
  if (closeBtn) closeBtn.onclick = closeHistory;
  if (clearBtn) clearBtn.onclick = async () => {
    await fetch('/history/clear', {method:'POST', headers:{'Content-Type':'application/json'}, body: '{}'});
    await renderHistory();
  };
  if (bd) bd.addEventListener('click', (e) => {
    if (e.target === bd) closeHistory();
  });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeHistory();
  });
}

function openAbout(){
  closeHeaderMenu();
  const bd = document.getElementById('aboutBackdrop');
  if (!bd || !bd.classList.contains('hidden')) return;
  bd.classList.remove('hidden');
  _uiPushLayer();
}

function closeAbout(opts){
  const bd = document.getElementById('aboutBackdrop');
  if (!bd) return;
  const fromNav = !!(opts && opts.fromNav);
  if (!fromNav && !bd.classList.contains('hidden') && __uiNavDepth > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  bd.classList.add('hidden');
}

function bindAboutUi(){
  const btn = document.getElementById('aboutBtn');
  const closeBtn = document.getElementById('aboutCloseBtn');
  const bd = document.getElementById('aboutBackdrop');
  if (btn) btn.onclick = openAbout;
  if (closeBtn) closeBtn.onclick = closeAbout;
  if (bd) bd.addEventListener('click', (e) => {
    if (e.target === bd) closeAbout();
  });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeAbout();
  });
}

function closeNowLanguageModal(opts){
  const bd = document.getElementById('langBackdrop');
  if (!bd) return;
  const fromNav = !!(opts && opts.fromNav);
  if (!fromNav && !bd.classList.contains('hidden') && __uiNavDepth > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  bd.classList.add('hidden');
}

async function _fetchNowLanguageOptions(refresh){
  const url = `/jellyfin/audio/options${refresh ? '?refresh=1' : ''}`;
  const r = await fetch(url, {cache:'no-store'});
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = String((body && (body.detail || body.reason || body.error)) || `HTTP ${r.status}`);
    throw new Error(msg);
  }
  return body;
}

function _renderNowLanguageOptions(optionsBody){
  const list = document.getElementById('langList');
  const cur = document.getElementById('langCurrent');
  const msg = document.getElementById('langMsg');
  if (!list || !cur || !msg) return;
  msg.classList.remove('ok', 'err');
  msg.textContent = '';
  list.innerHTML = '';

  const currentLang = String(optionsBody.current_audio_language || '').trim();
  const currentIdx = optionsBody.current_audio_stream_index;
  const currentIdxText = (currentIdx === 0 || Number.isInteger(currentIdx)) ? String(currentIdx) : '--';
  cur.textContent = currentLang ? `Current: ${currentLang.toUpperCase()} (#${currentIdxText})` : `Current audio track: #${currentIdxText}`;

  const rows = Array.isArray(optionsBody.options) ? optionsBody.options : [];
  if (!rows.length) {
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.textContent = 'No alternate audio streams were reported for this item.';
    list.appendChild(empty);
    return;
  }

  rows.forEach((row) => {
    const idx = Number(row && row.index);
    if (!Number.isInteger(idx)) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `langOpt${row && row.is_current ? ' active' : ''}`;
    const lang = String((row && row.language) || '').trim();
    const display = String((row && row.display) || '').trim();
    const suffix = [];
    if (row && row.is_default) suffix.push('default');
    if (row && row.is_current) suffix.push('active');
    btn.innerHTML = `
      <span class="langOptIdx">#${idx}</span>
      <span>${lang ? lang.toUpperCase() : 'Unknown language'}${display ? ` — ${display}` : ''}</span>
      <span class="langOptMeta">${suffix.join(' · ')}</span>
    `;
    btn.disabled = !!(row && row.is_current);
    btn.onclick = async () => {
      const oldText = btn.textContent || '';
      btn.disabled = true;
      btn.textContent = `Switching to #${idx}…`;
      try {
        const r = await fetch('/jellyfin/audio/select', {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({index: idx})
        });
        const b = await r.json().catch(() => ({}));
        if (!r.ok) {
          throw new Error(String((b && (b.detail || b.reason || b.error)) || `HTTP ${r.status}`));
        }
        msg.classList.remove('err');
        msg.classList.add('ok');
        const switchedLang = String((b && b.current_audio_language) || '').trim();
        msg.textContent = switchedLang
          ? `Audio switched to ${switchedLang.toUpperCase()}.`
          : `Audio switched to track #${idx}.`;
        await refresh();
        const latest = await _fetchNowLanguageOptions(false);
        _renderNowLanguageOptions(latest);
      } catch (e) {
        btn.disabled = false;
        btn.textContent = oldText;
        msg.classList.remove('ok');
        msg.classList.add('err');
        msg.textContent = `Switch failed: ${e && e.message ? e.message : e}`;
      }
    };
    list.appendChild(btn);
  });
}

async function openNowLanguageModal(){
  closeHeaderMenu();
  const bd = document.getElementById('langBackdrop');
  const msg = document.getElementById('langMsg');
  const cur = document.getElementById('langCurrent');
  const list = document.getElementById('langList');
  if (!bd || !cur || !list) return;
  if (!bd.classList.contains('hidden')) return;
  bd.classList.remove('hidden');
  _uiPushLayer();
  if (msg) {
    msg.classList.remove('ok', 'err');
    msg.textContent = '';
  }
  cur.textContent = 'Loading audio tracks…';
  list.innerHTML = '';
  try {
    const optionsBody = await _fetchNowLanguageOptions(false);
    _renderNowLanguageOptions(optionsBody);
  } catch (e) {
    if (msg) {
      msg.classList.add('err');
      msg.textContent = `Audio tracks unavailable: ${e && e.message ? e.message : e}`;
    }
  }
}

function bindNowLanguageUi(){
  const btn = document.getElementById('nowLangBtn');
  const closeBtn = document.getElementById('langCloseBtn');
  const bd = document.getElementById('langBackdrop');
  if (btn) btn.onclick = openNowLanguageModal;
  if (closeBtn) closeBtn.onclick = () => closeNowLanguageModal();
  if (bd) bd.addEventListener('click', (e) => {
    if (e.target === bd) closeNowLanguageModal();
  });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeNowLanguageModal();
  });
}

function closeNowSubtitleModal(opts){
  const bd = document.getElementById('subLangBackdrop');
  if (!bd) return;
  const fromNav = !!(opts && opts.fromNav);
  if (!fromNav && !bd.classList.contains('hidden') && __uiNavDepth > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  bd.classList.add('hidden');
}

async function _fetchNowSubtitleOptions(refresh){
  const url = `/jellyfin/subtitle/options${refresh ? '?refresh=1' : ''}`;
  const r = await fetch(url, {cache:'no-store'});
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = String((body && (body.detail || body.reason || body.error)) || `HTTP ${r.status}`);
    throw new Error(msg);
  }
  return body;
}

function _renderNowSubtitleOptions(optionsBody){
  const list = document.getElementById('subLangList');
  const cur = document.getElementById('subLangCurrent');
  const msg = document.getElementById('subLangMsg');
  if (!list || !cur || !msg) return;
  msg.classList.remove('ok', 'err');
  msg.textContent = '';
  list.innerHTML = '';

  const currentOff = !!(optionsBody && optionsBody.current_subtitle_off);
  const currentLang = String(optionsBody.current_subtitle_language || '').trim();
  const currentIdx = optionsBody.current_subtitle_stream_index;
  const currentIdxText = currentOff
    ? 'Off'
    : ((currentIdx === 0 || Number.isInteger(currentIdx)) ? String(currentIdx) : '--');
  cur.textContent = currentOff
    ? 'Current: Off'
    : (currentLang ? `Current: ${currentLang.toUpperCase()} (#${currentIdxText})` : `Current subtitle track: #${currentIdxText}`);

  const rows = Array.isArray(optionsBody.options) ? optionsBody.options : [];
  if (!rows.length) {
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.textContent = 'No subtitle streams were reported for this item.';
    list.appendChild(empty);
    return;
  }

  rows.forEach((row) => {
    const idx = Number(row && row.index);
    if (!Number.isInteger(idx)) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `langOpt${row && row.is_current ? ' active' : ''}`;
    const isOff = !!(row && row.is_off);
    const lang = String((row && row.language) || '').trim();
    const display = String((row && row.display) || '').trim();
    const suffix = [];
    if (row && row.is_default) suffix.push('default');
    if (row && row.is_current) suffix.push('active');
    btn.innerHTML = `
      <span class="langOptIdx">${isOff ? 'OFF' : `#${idx}`}</span>
      <span>${isOff ? 'Off' : (lang ? lang.toUpperCase() : 'Unknown language')}${display && !isOff ? ` — ${display}` : ''}</span>
      <span class="langOptMeta">${suffix.join(' · ')}</span>
    `;
    btn.disabled = !!(row && row.is_current);
    btn.onclick = async () => {
      const oldText = btn.textContent || '';
      btn.disabled = true;
      btn.textContent = isOff ? 'Turning subtitles off…' : `Switching to #${idx}…`;
      try {
        const r = await fetch('/jellyfin/subtitle/select', {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({index: idx})
        });
        const b = await r.json().catch(() => ({}));
        if (!r.ok) {
          throw new Error(String((b && (b.detail || b.reason || b.error)) || `HTTP ${r.status}`));
        }
        msg.classList.remove('err');
        msg.classList.add('ok');
        const switchedOff = !!(b && b.current_subtitle_off);
        const switchedLang = String((b && b.current_subtitle_language) || '').trim();
        msg.textContent = switchedOff
          ? 'Subtitles turned off.'
          : (switchedLang ? `Subtitles switched to ${switchedLang.toUpperCase()}.` : `Subtitles switched to track #${idx}.`);
        await refresh();
        const latest = await _fetchNowSubtitleOptions(false);
        _renderNowSubtitleOptions(latest);
      } catch (e) {
        btn.disabled = false;
        btn.textContent = oldText;
        msg.classList.remove('ok');
        msg.classList.add('err');
        msg.textContent = `Subtitle switch failed: ${e && e.message ? e.message : e}`;
      }
    };
    list.appendChild(btn);
  });
}

async function openNowSubtitleModal(){
  closeHeaderMenu();
  const bd = document.getElementById('subLangBackdrop');
  const msg = document.getElementById('subLangMsg');
  const cur = document.getElementById('subLangCurrent');
  const list = document.getElementById('subLangList');
  if (!bd || !cur || !list) return;
  if (!bd.classList.contains('hidden')) return;
  bd.classList.remove('hidden');
  _uiPushLayer();
  if (msg) {
    msg.classList.remove('ok', 'err');
    msg.textContent = '';
  }
  cur.textContent = 'Loading subtitle tracks…';
  list.innerHTML = '';
  try {
    const optionsBody = await _fetchNowSubtitleOptions(false);
    _renderNowSubtitleOptions(optionsBody);
  } catch (e) {
    if (msg) {
      msg.classList.add('err');
      msg.textContent = `Subtitle tracks unavailable: ${e && e.message ? e.message : e}`;
    }
  }
}

function bindNowSubtitleUi(){
  const btn = document.getElementById('nowSubLangBtn');
  const closeBtn = document.getElementById('subLangCloseBtn');
  const bd = document.getElementById('subLangBackdrop');
  if (btn) btn.onclick = openNowSubtitleModal;
  if (closeBtn) closeBtn.onclick = () => closeNowSubtitleModal();
  if (bd) bd.addEventListener('click', (e) => {
    if (e.target === bd) closeNowSubtitleModal();
  });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeNowSubtitleModal();
  });
}

function openSettings(){
  closeHeaderMenu();
  const bd = document.getElementById('settingsBackdrop');
  if (!bd || !bd.classList.contains('hidden')) return;
  bd.classList.remove('hidden');
  _uiPushLayer();
  loadSettingsUi().catch(console.warn);
}
function closeSettings(opts){
  const bd = document.getElementById('settingsBackdrop');
  if (!bd) return;
  const fromNav = !!(opts && opts.fromNav);
  if (!fromNav && !bd.classList.contains('hidden') && __uiNavDepth > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  bd.classList.add('hidden');
}

function qualityToFormat(q) {
  // Keep this in sync with server-side state._normalize_ytdlp_format.
  if (q === 'worst') return 'worst';
  if (q === '360' || q === '480' || q === '720' || q === '1080') {
    return `bestvideo[vcodec!*=av01][height<=${q}][fps<=60]+bestaudio/best[height<=${q}][fps<=60]/best`;
  }
  // Auto -> server picks compatibility default.
  return '';
}

const IDLE_PANEL_CATALOG = __IDLE_PANEL_CATALOG__;

function renderIdlePanelSettings(cfg){
  const host = document.getElementById('setIdlePanels');
  if (!host) return;
  host.innerHTML = '';
  Object.entries(IDLE_PANEL_CATALOG).forEach(([key, meta]) => {
    const panel = (cfg && cfg[key]) || {};
    const enabled = !!panel.enabled;
    const layout = panel.layout || (meta.layouts && meta.layouts[0]) || 'default';

    const row = document.createElement('div');
    row.className = 'fieldRow';
    row.innerHTML = `<label class="chk"><input type="checkbox" data-idle-enable="${key}" ${enabled ? 'checked' : ''}/> ${meta.title}</label><div class="hint">${meta.desc || ''}</div>`;

    const sel = document.createElement('select');
    sel.className = 'input';
    sel.setAttribute('data-idle-layout', key);
    (meta.layouts || ['default']).forEach((opt) => {
      const o = document.createElement('option');
      o.value = opt;
      o.textContent = opt;
      sel.appendChild(o);
    });
    sel.value = layout;
    row.appendChild(sel);
    host.appendChild(row);
  });
}

function collectIdlePanelSettings(){
  const out = {};
  Object.keys(IDLE_PANEL_CATALOG).forEach((key) => {
    const enabled = !!document.querySelector(`[data-idle-enable="${key}"]`)?.checked;
    const layout = document.querySelector(`[data-idle-layout="${key}"]`)?.value || (IDLE_PANEL_CATALOG[key].layouts || ['default'])[0] || 'default';
    out[key] = {enabled, layout};
  });
  return out;
}

const WEATHER_LOCATION_STATE = { latitude: null, longitude: null, location_name: '' };

function setWeatherLocationMeta(msg){
  const el = document.getElementById('setWeatherLocationMeta');
  if (el) el.textContent = msg || '';
}

function setWeatherLocation(name, latitude, longitude){
  WEATHER_LOCATION_STATE.latitude = Number.isFinite(Number(latitude)) ? Number(latitude) : null;
  WEATHER_LOCATION_STATE.longitude = Number.isFinite(Number(longitude)) ? Number(longitude) : null;
  WEATHER_LOCATION_STATE.location_name = String(name || '').trim();
  const cityInput = document.getElementById('setWeatherCity');
  if (cityInput) cityInput.value = WEATHER_LOCATION_STATE.location_name;
}

function weatherLocationSummary(name, lat, lon){
  const label = String(name || '').trim() || 'Selected location';
  const sLat = Number.isFinite(Number(lat)) ? Number(lat).toFixed(4) : '--';
  const sLon = Number.isFinite(Number(lon)) ? Number(lon).toFixed(4) : '--';
  return `${label} (${sLat}, ${sLon})`;
}

async function geocodeWeatherCity(cityQuery){
  const q = String(cityQuery || '').trim();
  if (!q) return null;
  const url = `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(q)}&count=1&language=en&format=json`;
  const r = await fetch(url, {cache:'no-store'});
  if (!r.ok) return null;
  const j = await r.json();
  const first = Array.isArray(j.results) ? j.results[0] : null;
  if (!first) return null;
  const parts = [first.name, first.admin1, first.country].filter(Boolean);
  return {
    latitude: Number(first.latitude),
    longitude: Number(first.longitude),
    location_name: parts.join(', ') || q,
  };
}

function defaultJellyfinServerUrl(){
  try {
    const host = (window.location.hostname || '').trim();
    if (host && host !== 'localhost' && host !== '127.0.0.1') return `http://${host}:8096`;
  } catch (_e) {}
  return 'http://127.0.0.1:8096';
}

async function loadSettingsUi(){
  const [devRes, setRes, jfRes] = await Promise.all([
    fetch('/devices'),
    fetch('/settings'),
    fetch('/integrations/jellyfin/status').catch(() => null)
  ]);
  const dev = await devRes.json();
  const cur = await setRes.json();
  const jfStatus = (jfRes && jfRes.ok) ? await jfRes.json() : null;
  const deviceName = document.getElementById('setDeviceName');
  const audioDev = document.getElementById('setAudioDev');
  const qual = document.getElementById('setQuality');
  const ytUseInvidious = document.getElementById('setYtUseInvidious');
  const ytInvidiousBase = document.getElementById('setYtInvidiousBase');
  const ytCookiesFile = document.getElementById('setYtCookiesFile');
  const ytCookiesState = document.getElementById('setYtCookiesState');
  const subs = document.getElementById('setSubs');
  const idleDashboardEnabled = document.getElementById('setIdleDashboardEnabled');
  const idleQrEnabled = document.getElementById('setIdleQrEnabled');
  const idleQrSize = document.getElementById('setIdleQrSize');
  const idleQrSizeVal = document.getElementById('setIdleQrSizeVal');
  const wDays = document.getElementById('setWeatherDays');
  const uploadMaxSize = document.getElementById('setUploadMaxSize');
  const uploadRetentionHours = document.getElementById('setUploadRetentionHours');
  const jfEnabled = document.getElementById('setJfEnabled');
  const jfServerUrl = document.getElementById('setJfServerUrl');
  const jfUsername = document.getElementById('setJfUsername');
  const jfUserId = document.getElementById('setJfUserId');
  const jfPwInput = document.getElementById('setJfPassword');
  const jfClearPw = document.getElementById('setJfClearPassword');
  const jfPwState = document.getElementById('setJfPasswordState');
  const jfAudioLang = document.getElementById('setJfAudioLang');
  const jfSubLang = document.getElementById('setJfSubLang');
  const jfPlaybackMode = document.getElementById('setJfPlaybackMode');
  const jfSyncDiag = document.getElementById('setJfSyncDiag');
  const jfCacheClearMsg = document.getElementById('setJfCacheClearResult');

  if (deviceName) deviceName.value = (cur.device_name || 'RelayTV');
  if (ytUseInvidious) ytUseInvidious.checked = !!cur.youtube_use_invidious;
  if (ytInvidiousBase) ytInvidiousBase.value = (cur.youtube_invidious_base || '');
  if (ytCookiesFile) ytCookiesFile.value = '';
  if (ytCookiesState) {
    ytCookiesState.classList.remove('ok', 'err');
    ytCookiesState.textContent = cur.youtube_cookies_configured ? 'cookies.txt is configured.' : 'No cookies.txt uploaded.';
  }
  if (jfEnabled) jfEnabled.checked = !!cur.jellyfin_enabled;
  if (jfServerUrl) jfServerUrl.value = (cur.jellyfin_server_url || defaultJellyfinServerUrl());
  if (jfUsername) jfUsername.value = (cur.jellyfin_username || '');
  if (jfUserId) jfUserId.value = (cur.jellyfin_user_id || '');
  if (jfAudioLang) jfAudioLang.value = (cur.jellyfin_audio_lang || '');
  if (jfSubLang) jfSubLang.value = (cur.jellyfin_sub_lang || '');
  if (jfPlaybackMode) jfPlaybackMode.value = (cur.jellyfin_playback_mode || 'auto');
  if (jfPwInput) jfPwInput.value = '';
  if (jfClearPw) jfClearPw.checked = false;
  if (jfPwState) {
    const hasPw = !!cur.jellyfin_password_configured;
    jfPwState.textContent = hasPw ? 'Password is stored.' : 'No password stored.';
    jfPwState.setAttribute('data-configured', hasPw ? '1' : '0');
  }
  const jfBadge = document.getElementById('setJfStatus');
  if (jfBadge) {
    const up = !!(jfStatus && jfStatus.enabled && (jfStatus.connected || jfStatus.authenticated));
    jfBadge.textContent = up ? 'Connected' : 'Down';
    jfBadge.classList.remove('up', 'down');
    jfBadge.classList.add(up ? 'up' : 'down');
  }
  if (jfSyncDiag) {
    if (!jfStatus) {
      jfSyncDiag.textContent = 'Status unavailable.';
    } else {
      const pOk = Number(jfStatus.progress_success_count || 0);
      const pFail = Number(jfStatus.progress_failure_count || 0);
      const sOk = Number(jfStatus.stopped_success_count || 0);
      const sFail = Number(jfStatus.stopped_failure_count || 0);
      const sSupp = Number(jfStatus.stopped_suppressed_count || 0);
      const pLat = Number.isFinite(Number(jfStatus.last_progress_latency_ms)) ? `${Number(jfStatus.last_progress_latency_ms)}ms` : 'n/a';
      const sLat = Number.isFinite(Number(jfStatus.last_stopped_latency_ms)) ? `${Number(jfStatus.last_stopped_latency_ms)}ms` : 'n/a';
      const auth = jfStatus.authenticated ? 'yes' : 'no';
      const catalogUserId = (jfStatus.catalog_user_id || '').toString().trim();
      const catalogUserSource = (jfStatus.catalog_user_source || 'none').toString().trim();
      const catalogUser = catalogUserId ? `${catalogUserId} (${catalogUserSource || 'preferred'})` : 'auto';
      const cacheEntries = Number(jfStatus.catalog_cache_entries || 0);
      const cacheMax = Number(jfStatus.catalog_cache_max_entries || 0);
      const cacheDiag = cacheMax > 0 ? `${cacheEntries}/${cacheMax}` : String(cacheEntries);
      const cacheClears = Number(jfStatus.catalog_cache_clears || 0);
      const cacheClearReason = (jfStatus.catalog_cache_last_cleared_reason || '').toString().trim();
      const health = (jfStatus.sync_health || 'unknown').toString();
      const healthReason = (jfStatus.sync_health_reason || '').toString().trim();
      const err = (jfStatus.last_error || '').toString().trim();
      jfSyncDiag.textContent =
        `Health: ${health}${healthReason ? ` (${healthReason})` : ''} · Auth: ${auth} · Catalog user: ${catalogUser} · Cache: ${cacheDiag} (clears: ${cacheClears}${cacheClearReason ? `, ${cacheClearReason}` : ''}) · Progress ok/fail: ${pOk}/${pFail} (${pLat}) · Stopped ok/fail: ${sOk}/${sFail} (${sLat}) · Stop dedupe: ${sSupp}` +
        (err ? ` · Last error: ${err}` : '');
    }
  }
  if (jfCacheClearMsg) {
    jfCacheClearMsg.classList.remove('ok', 'err');
    jfCacheClearMsg.textContent = '';
  }

  if (audioDev){
    audioDev.innerHTML = '';
    const optAuto = document.createElement('option');
    optAuto.value = '';
    optAuto.textContent = 'Auto';
    audioDev.appendChild(optAuto);

    (dev.alsa_devices || []).forEach(d => {
      const o = document.createElement('option');
      o.value = d.id;
      o.textContent = d.desc ? `${d.id} — ${d.desc}` : d.id;
      audioDev.appendChild(o);
    });
    audioDev.value = (cur.audio_device || '');
  }

  // Quality dropdown from quality_mode/quality_cap (fallback: ytdlp_format heuristic)
  if (qual){
    const qMode = (cur.quality_mode || '').toString().toLowerCase();
    let sel = '';
    if (qMode === 'auto' || qMode === 'auto_profile' || qMode === 'profile') {
      const cap = (cur.quality_cap || '').toString().trim();
      sel = cap || '';
    } else {
      const yf = (cur.ytdlp_format || '').toString();
      const m = yf.match(/height<=([0-9]+)/);
      if (m) sel = m[1];
      if (yf.trim() === 'worst') sel = 'worst';
    }
    qual.value = sel;
  }

  if (subs){
    subs.value = (cur.sub_lang || '');
  }
  if (idleDashboardEnabled) idleDashboardEnabled.checked = (cur.idle_dashboard_enabled !== false);
  if (idleQrEnabled) idleQrEnabled.checked = (cur.idle_qr_enabled !== false);
  if (idleQrSize) {
    const size = Number(cur.idle_qr_size);
    const safe = Number.isFinite(size) ? Math.max(96, Math.min(280, Math.round(size))) : 168;
    idleQrSize.value = String(safe);
    if (idleQrSizeVal) idleQrSizeVal.textContent = `${safe}px`;
  }

  if (wDays) wDays.value = (cur.weather && cur.weather.forecast_days) ? String(cur.weather.forecast_days) : '7';
  if (uploadMaxSize) {
    const maxSize = Number(cur.uploads && cur.uploads.max_size_gb);
    uploadMaxSize.value = String(Number.isFinite(maxSize) ? maxSize : 5);
  }
  if (uploadRetentionHours) {
    const retention = Number(cur.uploads && cur.uploads.retention_hours);
    uploadRetentionHours.value = String(Number.isFinite(retention) ? retention : 24);
  }

  const weather = cur.weather || {};
  setWeatherLocation(
    weather.location_name || 'New York, NY',
    weather.latitude,
    weather.longitude,
  );
  setWeatherLocationMeta(weatherLocationSummary(WEATHER_LOCATION_STATE.location_name, WEATHER_LOCATION_STATE.latitude, WEATHER_LOCATION_STATE.longitude));

  renderIdlePanelSettings(cur.idle_panels || {});
}

function bindSettingsUi(){
  const btn = document.getElementById('settingsBtn');
  const closeBtn = document.getElementById('settingsCloseBtn');
  const saveBtn = document.getElementById('settingsSaveBtn');
  const bd = document.getElementById('settingsBackdrop');

  if (btn) btn.onclick = openSettings;
  if (closeBtn) closeBtn.onclick = closeSettings;
  if (bd) bd.addEventListener('click', (e) => { if (e.target === bd) closeSettings(); });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const open = bd && !bd.classList.contains('hidden');
      if (open) closeSettings();
    }
  });

  const weatherCityInput = document.getElementById('setWeatherCity');
  const weatherFindBtn = document.getElementById('setWeatherFindBtn');
  const idleQrSize = document.getElementById('setIdleQrSize');
  const idleQrSizeVal = document.getElementById('setIdleQrSizeVal');
  const jfApplyBtn = document.getElementById('setJfApplyBtn');
  const jfApplyMsg = document.getElementById('setJfApplyResult');
  const jfCacheClearBtn = document.getElementById('setJfCacheClearBtn');
  const jfCacheClearMsg = document.getElementById('setJfCacheClearResult');
  const ytUploadBtn = document.getElementById('setYtCookiesUploadBtn');
  const ytClearBtn = document.getElementById('setYtCookiesClearBtn');
  const ytCookiesFile = document.getElementById('setYtCookiesFile');
  const ytCookiesState = document.getElementById('setYtCookiesState');

  function setYtCookiesStatus(text, cls){
    if (!ytCookiesState) return;
    ytCookiesState.classList.remove('ok', 'err');
    if (cls) ytCookiesState.classList.add(cls);
    ytCookiesState.textContent = text || '';
  }

  if (weatherFindBtn) weatherFindBtn.onclick = async () => {
    const city = weatherCityInput?.value || '';
    if (!city.trim()) {
      setWeatherLocationMeta('Enter a city to search.');
      return;
    }
    setWeatherLocationMeta('Looking up city…');
    const found = await geocodeWeatherCity(city);
    if (!found) {
      setWeatherLocationMeta('City not found. Try adding state/country.');
      return;
    }
    setWeatherLocation(found.location_name, found.latitude, found.longitude);
    setWeatherLocationMeta(weatherLocationSummary(found.location_name, found.latitude, found.longitude));
  };

  if (idleQrSize) {
    const syncQrSizeLabel = () => {
      const n = Number(idleQrSize.value || '168');
      const safe = Number.isFinite(n) ? Math.max(96, Math.min(280, Math.round(n))) : 168;
      if (idleQrSizeVal) idleQrSizeVal.textContent = `${safe}px`;
    };
    idleQrSize.addEventListener('input', syncQrSizeLabel);
    syncQrSizeLabel();
  }

  if (ytUploadBtn) ytUploadBtn.onclick = async () => {
    const file = ytCookiesFile?.files && ytCookiesFile.files[0] ? ytCookiesFile.files[0] : null;
    if (!file) {
      setYtCookiesStatus('Choose a cookies.txt file first.', 'err');
      return;
    }
    ytUploadBtn.disabled = true;
    setYtCookiesStatus('Uploading cookies.txt…');
    try {
      const text = await file.text();
      const r = await fetch('/settings/youtube/cookies', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({cookies_text: text, filename: file.name || ''})
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        setYtCookiesStatus(`Upload failed: ${String((j && j.detail) || `HTTP ${r.status}`)}`, 'err');
        return;
      }
      setYtCookiesStatus('cookies.txt uploaded and applied.', 'ok');
      if (ytCookiesFile) ytCookiesFile.value = '';
      await loadSettingsUi();
    } catch (e) {
      setYtCookiesStatus(`Upload failed: ${e && e.message ? e.message : e}`, 'err');
    } finally {
      ytUploadBtn.disabled = false;
    }
  };

  if (ytClearBtn) ytClearBtn.onclick = async () => {
    ytClearBtn.disabled = true;
    setYtCookiesStatus('Clearing cookies configuration…');
    try {
      const r = await fetch('/settings/youtube/cookies/clear', {method:'POST'});
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        setYtCookiesStatus(`Clear failed: ${String((j && j.detail) || `HTTP ${r.status}`)}`, 'err');
        return;
      }
      setYtCookiesStatus('cookies.txt configuration cleared.', 'ok');
      if (ytCookiesFile) ytCookiesFile.value = '';
      await loadSettingsUi();
    } catch (e) {
      setYtCookiesStatus(`Clear failed: ${e && e.message ? e.message : e}`, 'err');
    } finally {
      ytClearBtn.disabled = false;
    }
  };

  async function applyJellyfinOnly(){
    if (jfApplyMsg) {
      jfApplyMsg.classList.remove('ok', 'err');
      jfApplyMsg.textContent = '';
    }
    const jfEnabled = !!document.getElementById('setJfEnabled')?.checked;
    const jfServer = (document.getElementById('setJfServerUrl')?.value || '').trim();
    const jfUser = (document.getElementById('setJfUsername')?.value || '').trim();
    const jfUserId = (document.getElementById('setJfUserId')?.value || '').trim();
    const jfPass = (document.getElementById('setJfPassword')?.value || '').trim();
    const jfClearPw = !!document.getElementById('setJfClearPassword')?.checked;
    const jfPwConfigured = (document.getElementById('setJfPasswordState')?.getAttribute('data-configured') || '') === '1';
    const jfAudioLang = (document.getElementById('setJfAudioLang')?.value || '').trim().toLowerCase();
    const jfSubLang = (document.getElementById('setJfSubLang')?.value || '').trim().toLowerCase();
    const jfPlaybackMode = (document.getElementById('setJfPlaybackMode')?.value || 'auto').trim().toLowerCase();
    const deviceName = (document.getElementById('setDeviceName')?.value || '').trim();

    if (jfEnabled) {
      if (!jfServer) { if (jfApplyMsg){ jfApplyMsg.classList.add('err'); jfApplyMsg.textContent='Server URL is required.'; } return; }
      if (!jfUser) { if (jfApplyMsg){ jfApplyMsg.classList.add('err'); jfApplyMsg.textContent='Username is required.'; } return; }
      if (!jfPass && !jfPwConfigured) { if (jfApplyMsg){ jfApplyMsg.classList.add('err'); jfApplyMsg.textContent='Password is required.'; } return; }
    }

    const payload = {
      device_name: deviceName || 'RelayTV',
      jellyfin_enabled: jfEnabled,
      jellyfin_server_url: jfServer,
      jellyfin_username: jfUser,
      jellyfin_user_id: jfUserId,
      jellyfin_audio_lang: jfAudioLang,
      jellyfin_sub_lang: jfSubLang,
      jellyfin_playback_mode: (jfPlaybackMode === 'direct' || jfPlaybackMode === 'transcode') ? jfPlaybackMode : 'auto',
      apply_now: true
    };
    if (jfPass || jfClearPw) payload.jellyfin_password = jfClearPw ? '' : jfPass;

    if (jfApplyBtn) jfApplyBtn.disabled = true;
    try {
      const r = await fetch('/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
      if (!r.ok) {
        if (jfApplyMsg) {
          jfApplyMsg.classList.add('err');
          jfApplyMsg.textContent = 'Apply failed.';
        }
        return;
      }
      const body = await r.json().catch(() => ({}));
      const failed = Array.isArray(body.live_apply_failed) ? body.live_apply_failed : [];
      if (failed.length) {
        if (jfApplyMsg) {
          jfApplyMsg.classList.add('err');
          jfApplyMsg.textContent = `Apply failed: ${failed.join(', ')}`;
        }
      } else {
        if (jfApplyMsg) {
          jfApplyMsg.classList.add('ok');
          jfApplyMsg.textContent = 'Jellyfin settings applied.';
        }
      }
      await loadSettingsUi();
    } catch (_e) {
      if (jfApplyMsg) {
        jfApplyMsg.classList.add('err');
        jfApplyMsg.textContent = 'Apply failed.';
      }
    } finally {
      if (jfApplyBtn) jfApplyBtn.disabled = false;
    }
  }

  if (jfApplyBtn) jfApplyBtn.onclick = applyJellyfinOnly;

  async function clearJellyfinCatalogCache(){
    if (jfCacheClearMsg) {
      jfCacheClearMsg.classList.remove('ok', 'err');
      jfCacheClearMsg.textContent = '';
    }
    if (jfCacheClearBtn) jfCacheClearBtn.disabled = true;
    try {
      const r = await fetch('/integrations/jellyfin/catalog/cache_clear', {method:'POST'});
      if (!r.ok) {
        if (jfCacheClearMsg) {
          jfCacheClearMsg.classList.add('err');
          jfCacheClearMsg.textContent = 'Cache clear failed.';
        }
        return;
      }
      if (jfCacheClearMsg) {
        jfCacheClearMsg.classList.add('ok');
        jfCacheClearMsg.textContent = 'Catalog cache cleared.';
      }
      await loadSettingsUi();
    } catch (_e) {
      if (jfCacheClearMsg) {
        jfCacheClearMsg.classList.add('err');
        jfCacheClearMsg.textContent = 'Cache clear failed.';
      }
    } finally {
      if (jfCacheClearBtn) jfCacheClearBtn.disabled = false;
    }
  }

  if (jfCacheClearBtn) jfCacheClearBtn.onclick = clearJellyfinCatalogCache;

  if (saveBtn) saveBtn.onclick = async () => {
    const deviceName = (document.getElementById('setDeviceName')?.value || '').trim();
    const audioDev = document.getElementById('setAudioDev')?.value || '';
    const qual = document.getElementById('setQuality')?.value || '';
    const ytUseInvidious = !!document.getElementById('setYtUseInvidious')?.checked;
    const ytInvidiousBase = (document.getElementById('setYtInvidiousBase')?.value || '').trim();
    const subs = document.getElementById('setSubs')?.value || '';
    const idleDashboardEnabled = document.getElementById('setIdleDashboardEnabled')?.checked !== false;
    const idleQrEnabled = !!document.getElementById('setIdleQrEnabled')?.checked;
    const idleQrSize = Number(document.getElementById('setIdleQrSize')?.value || '168');
    const idleQrSizeSafe = Number.isFinite(idleQrSize) ? Math.max(96, Math.min(280, Math.round(idleQrSize))) : 168;
    const weatherDays = Number(document.getElementById('setWeatherDays')?.value || '7');
    const uploadMaxSize = Number(document.getElementById('setUploadMaxSize')?.value || '5');
    const uploadRetentionHours = Number(document.getElementById('setUploadRetentionHours')?.value || '24');
    const jfEnabled = !!document.getElementById('setJfEnabled')?.checked;
    const jfServer = (document.getElementById('setJfServerUrl')?.value || '').trim();
    const jfUser = (document.getElementById('setJfUsername')?.value || '').trim();
    const jfUserId = (document.getElementById('setJfUserId')?.value || '').trim();
    const jfPass = (document.getElementById('setJfPassword')?.value || '').trim();
    const jfClearPw = !!document.getElementById('setJfClearPassword')?.checked;
    const jfPwConfigured = (document.getElementById('setJfPasswordState')?.getAttribute('data-configured') || '') === '1';
    const jfAudioLang = (document.getElementById('setJfAudioLang')?.value || '').trim().toLowerCase();
    const jfSubLang = (document.getElementById('setJfSubLang')?.value || '').trim().toLowerCase();
    const jfPlaybackMode = (document.getElementById('setJfPlaybackMode')?.value || 'auto').trim().toLowerCase();
    const typedCity = weatherCityInput?.value || '';
    if (typedCity.trim() && typedCity.trim() !== WEATHER_LOCATION_STATE.location_name) {
      const found = await geocodeWeatherCity(typedCity);
      if (found) {
        setWeatherLocation(found.location_name, found.latitude, found.longitude);
      }
    }
    if (ytUseInvidious && !ytInvidiousBase) {
      alert('Invidious server URL is required when YouTube Invidious mode is enabled.');
      return;
    }
    if (jfEnabled) {
      if (!jfServer) { alert('Jellyfin server URL is required.'); return; }
      if (!jfUser) { alert('Jellyfin username is required.'); return; }
      if (!jfPass && !jfPwConfigured) { alert('Jellyfin password is required.'); return; }
    }

    const payload = {
      device_name: deviceName || 'RelayTV',
      audio_device: audioDev,
      quality_mode: (qual ? 'manual' : 'auto_profile'),
      quality_cap: (qual && qual !== 'worst') ? qual : '',
      ytdlp_format: (qual ? qualityToFormat(qual) : ''),
      youtube_use_invidious: ytUseInvidious,
      youtube_invidious_base: ytInvidiousBase,
      sub_lang: subs,
      idle_dashboard_enabled: idleDashboardEnabled,
      idle_qr_enabled: idleQrEnabled,
      idle_qr_size: idleQrSizeSafe,
      idle_panels: collectIdlePanelSettings(),
      weather: {
        forecast_days: [1,3,7].includes(weatherDays) ? weatherDays : 7,
        latitude: Number.isFinite(WEATHER_LOCATION_STATE.latitude) ? WEATHER_LOCATION_STATE.latitude : 40.7128,
        longitude: Number.isFinite(WEATHER_LOCATION_STATE.longitude) ? WEATHER_LOCATION_STATE.longitude : -74.006,
        location_name: (WEATHER_LOCATION_STATE.location_name || typedCity || 'New York, NY').trim()
      },
      uploads: {
        max_size_gb: Number.isFinite(uploadMaxSize) ? Math.max(0.25, Math.min(500, Number(uploadMaxSize.toFixed(2)))) : 5,
        retention_hours: Number.isFinite(uploadRetentionHours) ? Math.max(1, Math.min(2160, Math.round(uploadRetentionHours))) : 24
      },
      jellyfin_enabled: jfEnabled,
      jellyfin_server_url: jfServer,
      jellyfin_username: jfUser,
      jellyfin_user_id: jfUserId,
      jellyfin_audio_lang: jfAudioLang,
      jellyfin_sub_lang: jfSubLang,
      jellyfin_playback_mode: (jfPlaybackMode === 'direct' || jfPlaybackMode === 'transcode') ? jfPlaybackMode : 'auto',
      apply_now: true
    };
    if (jfPass || jfClearPw) payload.jellyfin_password = jfClearPw ? '' : jfPass;
    const r = await fetch('/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
    if (!r.ok) {
      alert('Failed to save settings');
      return;
    }
    closeSettings();
  };
}


function bindAddUrlUi(){
  const btn = document.getElementById('addUrlBtn');
  const bd  = document.getElementById('addBackdrop');
  const closeBtn = document.getElementById('addCloseBtn');
  const pasteBtn = document.getElementById('addPasteBtn');
  const playBtn  = document.getElementById('addPlayBtn');
  const queueBtn = document.getElementById('addQueueBtn');
  const inp      = document.getElementById('addUrlInput');
  const notifyBtn = document.getElementById('notifySendBtn');

  if (btn) btn.onclick = openAddUrl;
  if (closeBtn) closeBtn.onclick = closeAddUrl;
  if (pasteBtn) pasteBtn.onclick = pasteIntoAddUrl;
  if (playBtn) playBtn.onclick = ()=>submitAddUrl('play');
  if (queueBtn) queueBtn.onclick = ()=>submitAddUrl('queue');
  if (notifyBtn) notifyBtn.onclick = submitNotificationToast;

  if (bd) bd.addEventListener('click', (e) => {
    if (e.target === bd) closeAddUrl();
  });

  // Some browsers only allow clipboard reads after a user gesture.
  if (inp) inp.addEventListener('focus', async ()=>{
    if (inp.value.trim()) return;
    const clip = await clipboardText();
    if (looksLikeUrl(clip)) inp.value = normalizeUrl(clip);
  });

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeAddUrl();
    // When the modal is open, Enter defaults to Play
    const open = bd && !bd.classList.contains('hidden');
    const target = e.target;
    if (open && e.key === 'Enter' && !(target && target.closest && target.closest('#notifySection'))) submitAddUrl('play');
  });
}

// Bind UI handlers only after the full DOM is parsed. The Settings modal markup
// is defined after this script block in the HTML template.
window.addEventListener('DOMContentLoaded', () => {
  initScrubber();
  initRemoteVolumeSlider();
  primeRemoteVolumeSlider().catch(() => {});
  bindHeaderMenu();
  bindHistoryUi();
  bindAboutUi();
  bindNowLanguageUi();
  bindNowSubtitleUi();
  bindSettingsUi();
  bindAddUrlUi();
  bindJellyfinUi();
  _jfSetShellVisible(false);
  _jfSetActiveTab('dashboard', {refresh:false});
  try { history.replaceState(Object.assign({}, history.state || {}, {relaytv_root: 1}), ''); } catch (_e) {}
  window.addEventListener('popstate', () => {
    if (__uiNavDepth > 0) __uiNavDepth = Math.max(0, __uiNavDepth - 1);
    _uiCloseTopLayerFromNav();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    if (!__uiEventSource) connectUiEventStream();
    if (!_uiEventHealthy()) refresh().catch(() => {});
  });
  connectUiEventStream();
  refresh();
  setInterval(() => {
    if (_uiEventHealthy()) return;
    refresh().catch(() => {});
  }, __UI_FALLBACK_REFRESH_MS);
  setInterval(() => {
    if (__uiEventSource) return;
    connectUiEventStream();
  }, __UI_EVENT_RECONNECT_MS);
  setInterval(() => {
    if (document.visibilityState !== 'visible') return;
    if (!__jfUiVisible) return;
    if (__jfActiveTab !== 'dashboard') return;
    if (__jfLastMode === 'search' && __jfLastQuery) return;
    loadJellyfinHome(false);
  }, __JF_DASHBOARD_REFRESH_MS);
});
</script>
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
      <summary>YouTube</summary>
      <div class="settingsBody">
        <label class="chk"><input type="checkbox" id="setYtUseInvidious" /> Use Invidious server for YouTube playback</label>
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
        <details class="settingsGroup">
          <summary>Show QR in Idle</summary>
          <div class="settingsBody">
            <label class="chk"><input type="checkbox" id="setIdleQrEnabled" /> Show connect QR in idle (bottom-right)</label>
            <div class="hint">Displays a scannable code for the current RelayTV remote URL, with logo center.</div>
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
      <summary>Jellyfin Integration <span id="setJfStatus" class="sectionStatus down">Down</span></summary>
      <div class="settingsBody">
        <label class="chk"><input type="checkbox" id="setJfEnabled" /> Enable Jellyfin integration</label>

        <div class="fieldRow">
          <label class="fieldLbl">Jellyfin server</label>
          <input id="setJfServerUrl" class="input" placeholder="http://10.0.55.2:8096" />
          <div class="hint">Use your local Jellyfin base URL, for example `http://10.0.55.2:8096`.</div>
        </div>

        <div class="fieldRow">
          <label class="fieldLbl">Username</label>
          <input id="setJfUsername" class="input" placeholder="jellyfin username" />
        </div>
        <div class="fieldRow">
          <label class="fieldLbl">Preferred user ID (optional)</label>
          <input id="setJfUserId" class="input" placeholder="Jellyfin user Id (UUID)" />
          <div class="hint">Optional profile override for catalog browsing on this TV. Leave blank to use the authenticated user.</div>
        </div>
        <div class="fieldRow">
          <label class="fieldLbl">Password</label>
          <input id="setJfPassword" class="input" type="password" autocomplete="new-password" placeholder="(leave blank to keep existing)" />
          <label class="chk"><input type="checkbox" id="setJfClearPassword" /> Clear stored password</label>
          <div class="hint" id="setJfPasswordState"></div>
        </div>
        <div class="fieldRow">
          <label class="fieldLbl">Preferred audio language</label>
          <input id="setJfAudioLang" class="input" placeholder="e.g. en, pt-BR" />
          <div class="hint">Used when selecting Jellyfin audio tracks if available.</div>
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
          <button type="button" id="setJfApplyBtn" class="btn electricBlue">Apply Jellyfin</button>
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
    return HTMLResponse(content=html)






# =========================
# PWA (RelayTV) - no filesystem writes
# =========================

_RELAYTV_THEME = "#0b0f19"
_STATIC_ROOT = os.getenv("RELAYTV_STATIC_DIR") or os.path.join(os.path.dirname(__file__), "static")
_PWA_STATIC_ROOT = os.path.join(_STATIC_ROOT, "pwa")


def _safe_static_join(base: str, relative_path: str) -> str | None:
    rel = (relative_path or "").strip().replace("\\", "/")
    if not rel or rel.startswith("/"):
        return None
    full = os.path.abspath(os.path.join(base, rel))
    base_abs = os.path.abspath(base)
    try:
        if os.path.commonpath([base_abs, full]) != base_abs:
            return None
    except Exception:
        return None
    return full

@router.get("/manifest.json")
def pwa_manifest():
    return JSONResponse({
        "name": "RelayTV",
        "short_name": "RelayTV",
        "start_url": "/ui",
        "scope": "/",
        "display": "standalone",
        "background_color": _RELAYTV_THEME,
        "theme_color": _RELAYTV_THEME,
        "orientation": "portrait",
        "share_target": {"action": "/share", "method": "GET", "params": {"url": "url"}},
        "icons": [
            {"src": "/pwa/brand/logo.svg", "sizes": "192x192", "type": "image/svg+xml"},
            {"src": "/pwa/brand/logo.svg", "sizes": "512x512", "type": "image/svg+xml"}
        ]
    })

def _relaytv_svg(size: int = 512) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 512 512" role="img" aria-label="RelayTV">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#ff6a00"/>
      <stop offset="1" stop-color="#ffb14a"/>
    </linearGradient>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#0b0f19"/>
      <stop offset="1" stop-color="#070a12"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="512" height="512" rx="120" fill="url(#bg)"/>
  <circle cx="256" cy="256" r="170" fill="none" stroke="url(#g)" stroke-width="26" opacity="0.95"/>
  <circle cx="182" cy="332" r="18" fill="url(#g)"/>
  <path d="M176 296c34 10 60 36 70 70" fill="none" stroke="url(#g)" stroke-width="22" stroke-linecap="round" opacity="0.95"/>
  <path d="M176 252c59 14 104 59 118 118" fill="none" stroke="url(#g)" stroke-width="22" stroke-linecap="round" opacity="0.78"/>
  <path d="M176 210c84 16 148 80 164 164" fill="none" stroke="url(#g)" stroke-width="22" stroke-linecap="round" opacity="0.62"/>
  <rect x="278" y="154" width="114" height="114" rx="34" fill="rgba(255,255,255,0.08)" stroke="rgba(255,255,255,0.14)" stroke-width="2"/>
  <text x="335" y="228" text-anchor="middle" font-family="ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif" font-size="64" font-weight="800" fill="white">B</text>
</svg>'''


def _jellyfin_svg(size: int = 128) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 128 128" role="img" aria-label="Jellyfin">
  <defs>
    <linearGradient id="jg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#7f5af0"/>
      <stop offset="1" stop-color="#00b7ff"/>
    </linearGradient>
    <radialGradient id="jb" cx="50%" cy="40%" r="65%">
      <stop offset="0" stop-color="#1f2436"/>
      <stop offset="1" stop-color="#0b0f19"/>
    </radialGradient>
  </defs>
  <rect x="6" y="6" width="116" height="116" rx="26" fill="url(#jb)" stroke="rgba(255,255,255,0.18)" />
  <polygon points="64,28 95,82 33,82" fill="url(#jg)" />
  <circle cx="64" cy="90" r="8" fill="#8dc2ff" opacity="0.95" />
</svg>'''

@router.get("/pwa/icon.svg")
def pwa_icon_svg():
    brand = _safe_static_join(_PWA_STATIC_ROOT, "brand/logo.svg")
    if brand and os.path.exists(brand):
        return FileResponse(brand, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})
    legacy_brand = _resolve_static_asset("brand", "logo.svg")
    if legacy_brand and os.path.exists(legacy_brand):
        return FileResponse(legacy_brand, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})
    asset = _safe_static_join(_PWA_STATIC_ROOT, "icon.svg")
    if asset and os.path.exists(asset):
        return FileResponse(asset, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})
    return Response(_relaytv_svg(512), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/favicon.ico")
def favicon_ico():
    return RedirectResponse(url="/pwa/brand/logo.svg?v=2")

@router.get("/pwa/splash.svg")
def pwa_splash_svg():
    asset = _safe_static_join(_PWA_STATIC_ROOT, "splash.svg")
    if asset and os.path.exists(asset):
        return FileResponse(asset, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})
    return Response(_relaytv_svg(512), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/pwa/jellyfin.svg")
def pwa_jellyfin_svg():
    asset = _safe_static_join(_PWA_STATIC_ROOT, "jellyfin.svg")
    if asset and os.path.exists(asset):
        return FileResponse(asset, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})
    return Response(_jellyfin_svg(128), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/pwa/{asset_path:path}")
def pwa_static_asset(asset_path: str):
    # Look under static/pwa first, then static/ for compatibility.
    for root in (_PWA_STATIC_ROOT, _STATIC_ROOT):
        asset = _safe_static_join(root, asset_path)
        if asset and os.path.exists(asset) and os.path.isfile(asset):
            media_type = mimetypes.guess_type(asset)[0] or "application/octet-stream"
            return FileResponse(asset, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})
    return Response(status_code=404)

@router.get("/sw.js")
def pwa_sw():
    # Minimal service worker to satisfy "installable" checks in Chromium browsers.
    js = "self.addEventListener('install', e => self.skipWaiting());\nself.addEventListener('activate', e => e.waitUntil(clients.claim()));\n"
    return Response(js, media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@router.get("/")
def root():
    return RedirectResponse(url="/ui")


@router.get("/health")
def health() -> dict[str, bool]:
    # Keep the health payload intentionally minimal so basic liveness checks and
    # smoke tests can rely on a stable response contract.
    return {"ok": True}
