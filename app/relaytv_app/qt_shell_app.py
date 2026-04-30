# SPDX-License-Identifier: GPL-3.0-only
"""RelayTV Qt shell prototype.

Runs a single Qt process that owns both:
- an embedded mpv playback surface
- a transparent WebEngine overlay
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.util
import json
import os
import platform
import shlex
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

try:
    import qrcode
except Exception:
    qrcode = None

try:
    from . import state as app_state
except Exception:
    app_state = None

from .debug import get_logger


logger = get_logger("qt_shell")


def _eprint(*a: object) -> None:
    logger.info(" ".join(str(part) for part in a))


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _has_opt(args: list[str], opt: str) -> bool:
    return any(a == opt or a.startswith(opt + "=") for a in args)


def _env_choice(name: str) -> bool | None:
    v = os.getenv(name)
    if v is None:
        return None
    text = v.strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return None


def _append_env_flags(name: str, flags: list[str]) -> None:
    current = (os.getenv(name) or "").strip()
    try:
        parts = shlex.split(current) if current else []
    except Exception:
        parts = current.split() if current else []
    changed = False
    for flag in flags:
        if flag not in parts:
            parts.append(flag)
            changed = True
    if changed or not current:
        os.environ[name] = " ".join(parts).strip()


def _overlay_software_mode_enabled() -> bool:
    override = _env_choice("RELAYTV_QT_OVERLAY_SOFTWARE")
    if override is not None:
        return bool(override)
    arch = (platform.machine() or "").strip().lower()
    return arch in ("aarch64", "arm64", "armv7l", "armv6l")


def _embedded_web_overlay_enabled() -> bool:
    override = _env_choice("RELAYTV_QT_OVERLAY_ENABLED")
    if override is not None:
        return bool(override)
    return True


def _libmpv_enabled() -> bool:
    override = _env_choice("RELAYTV_QT_LIBMPV")
    if override is not None:
        return bool(override)
    arch = (platform.machine() or "").strip().lower()
    return arch not in ("aarch64", "arm64", "armv7l", "armv6l")


def _native_overlay_toasts_enabled() -> bool:
    override = _env_choice("RELAYTV_QT_NATIVE_TOASTS")
    if override is not None:
        return bool(override)
    return False


def _native_idle_overlay_enabled() -> bool:
    override = _env_choice("RELAYTV_QT_NATIVE_IDLE")
    if override is not None:
        return bool(override)
    return False


def _native_overlay_toasts_use_toplevel(*, use_libmpv: bool) -> bool:
    override = _env_choice("RELAYTV_QT_NATIVE_TOASTS_TOPLEVEL")
    if override is not None:
        return bool(override)
    machine = (platform.machine() or "").strip().lower()
    return (not use_libmpv) and machine in ("aarch64", "arm64")


def _native_idle_overlay_use_toplevel(*, use_libmpv: bool) -> bool:
    override = _env_choice("RELAYTV_QT_NATIVE_IDLE_TOPLEVEL")
    if override is not None:
        return bool(override)
    machine = (platform.machine() or "").strip().lower()
    return (not use_libmpv) and machine in ("aarch64", "arm64")


def _prefer_wayland_window_flags(qpa_platform: str, host_session_type: str) -> bool:
    qpa = str(qpa_platform or "").strip().lower()
    host = str(host_session_type or "").strip().lower()
    if qpa and qpa not in ("xcb", "x11") and "wayland" in qpa:
        return True
    # XWayland on a Wayland host still needs regular top-level window semantics
    # for fullscreen idle/toast/overlay surfaces to stay visible above desktop.
    return host == "wayland"


def _cursor_autohide_enabled() -> bool:
    override = _env_choice("RELAYTV_QT_CURSOR_AUTOHIDE")
    if override is not None:
        return bool(override)
    return True


def _cursor_autohide_timeout_ms(default: int = 2000) -> int:
    raw = (os.getenv("RELAYTV_QT_CURSOR_AUTOHIDE_MS") or "").strip()
    if not raw:
        raw = (os.getenv("RELAYTV_QT_CURSOR_HIDE_MS") or "").strip()
    if not raw:
        raw = (os.getenv("RELAYTV_QT_CURSOR_AUTOHIDE_SEC") or "").strip()
        if raw:
            try:
                return max(250, min(15000, int(float(raw) * 1000.0)))
            except Exception:
                return int(default)
    if not raw:
        return int(default)
    try:
        return max(250, min(15000, int(float(raw))))
    except Exception:
        return int(default)


def _cursor_autohide_debug_enabled() -> bool:
    return _env_bool("RELAYTV_QT_CURSOR_DEBUG", False)


def _cursor_debug(msg: str) -> None:
    if _cursor_autohide_debug_enabled():
        _eprint(f"qt-shell cursor: {msg}")


def _split_env_args(name: str) -> list[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw)
    except ValueError as e:
        _eprint(f"{name} parse error: {e}; raw={raw!r}")
        return []


def _qt_runtime_status_file() -> str:
    return (os.getenv("RELAYTV_QT_RUNTIME_STATUS_FILE") or "/tmp/relaytv-qt-runtime.json").strip()


def _qt_runtime_control_file() -> str:
    return (os.getenv("RELAYTV_QT_RUNTIME_CONTROL_FILE") or "/tmp/relaytv-qt-runtime-control.json").strip()


def _native_idle_qr_size(value: object, default: int = 168) -> int:
    try:
        size = int(float(value))
    except Exception:
        size = int(default)
    return max(96, min(280, size))


def _native_idle_qr_enabled(settings: object) -> bool:
    if not isinstance(settings, dict):
        return True
    return settings.get("idle_qr_enabled") is not False


def _pick_public_idle_qr_url(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    urls: list[str] = []
    for key in ("public_urls", "urls"):
        raw = payload.get(key)
        if isinstance(raw, list):
            urls.extend(str(v or "").strip() for v in raw)
    for url in urls:
        text = str(url or "").strip()
        lowered = text.lower()
        if not text:
            continue
        if "127.0.0.1" in lowered or "localhost" in lowered:
            continue
        if lowered.startswith(("http://", "https://")):
            return text
    return ""


def _derive_native_idle_public_ui_url(overlay_url: str) -> str:
    try:
        parsed = urlsplit(str(overlay_url or "").strip())
        scheme = parsed.scheme or "http"
        port = int(parsed.port or 8787)
    except Exception:
        scheme = "http"
        port = 8787
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            ip = str(probe.getsockname()[0] or "").strip()
        finally:
            probe.close()
        if ip and not ip.startswith("127."):
            return f"{scheme}://{ip}:{port}/ui"
    except Exception:
        pass
    try:
        for _family, _stype, _proto, _canon, sockaddr in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = str((sockaddr or [""])[0] or "").strip()
            if ip and not ip.startswith("127."):
                return f"{scheme}://{ip}:{port}/ui"
    except Exception:
        pass
    return ""


def _native_idle_device_name() -> str:
    try:
        if app_state is not None and hasattr(app_state, "get_settings"):
            settings = app_state.get_settings() or {}
            name = str(settings.get("device_name") or "").strip()
            if name:
                return name
    except Exception:
        pass
    for env_name in ("RELAYTV_DEVICE_NAME", "BRAVECAST_DEVICE_NAME", "DEVICE_NAME"):
        try:
            name = str(os.getenv(env_name) or "").strip()
            if name:
                return name
        except Exception:
            pass
    try:
        return str(socket.gethostname() or "").strip()
    except Exception:
        return ""


def _native_idle_logo_path() -> str:
    explicit_banner = (os.getenv("RELAYTV_BANNER_PATH") or "").strip()
    if explicit_banner and os.path.exists(explicit_banner):
        return explicit_banner
    explicit = (os.getenv("RELAYTV_LOGO_IMAGE") or "").strip()
    if explicit and os.path.exists(explicit):
        return explicit
    module_banner_png = os.path.abspath(os.path.join(os.path.dirname(__file__), "static", "brand", "banner.png"))
    module_banner = os.path.abspath(os.path.join(os.path.dirname(__file__), "static", "brand", "banner.svg"))
    module_logo = os.path.abspath(os.path.join(os.path.dirname(__file__), "static", "brand", "logo.svg"))
    for path in (
        module_banner_png,
        "/app/relaytv_app/static/brand/banner.png",
        "/data/assets/banner.png",
        module_banner,
        "/app/relaytv_app/static/brand/banner.svg",
        "/data/assets/banner.svg",
        module_logo,
        "/app/relaytv_app/static/brand/logo.svg",
        "/data/assets/logo.svg",
    ):
        if path and os.path.exists(path):
            return path
    return module_logo


def _native_idle_weather_code_label(code: object) -> str:
    try:
        value = int(float(code))
    except Exception:
        return "Weather unavailable"
    if value == 0:
        return "Clear"
    if value in (1, 2):
        return "Partly cloudy"
    if value == 3:
        return "Overcast"
    if value in (45, 48):
        return "Fog"
    if value in (51, 53, 55, 56, 57):
        return "Drizzle"
    if value in (61, 63, 65, 80, 81, 82):
        return "Rain"
    if value in (66, 67):
        return "Freezing rain"
    if value in (71, 73, 75, 77, 85, 86):
        return "Snow"
    if value in (95, 96, 99):
        return "Thunderstorm"
    return "Mixed"


def _native_idle_weather_url(settings_payload: object) -> str:
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


def _native_idle_weather_signature(settings_payload: object) -> str:
    settings = settings_payload if isinstance(settings_payload, dict) else {}
    idle_panels = settings.get("idle_panels") if isinstance(settings, dict) else {}
    weather_panel = idle_panels.get("weather") if isinstance(idle_panels, dict) else {}
    weather = settings.get("weather") if isinstance(settings, dict) else {}
    weather = weather if isinstance(weather, dict) else {}
    enabled = bool(isinstance(weather_panel, dict) and weather_panel.get("enabled"))
    lat = str(weather.get("latitude") or "40.7128").strip()
    lon = str(weather.get("longitude") or "-74.0060").strip()
    units = str(weather.get("units") or "imperial").strip().lower()
    days = str(weather.get("forecast_days") or "7").strip()
    return f"{enabled}|{lat}|{lon}|{units}|{days}"


def _native_idle_weather_layout(settings_payload: object) -> str:
    settings = settings_payload if isinstance(settings_payload, dict) else {}
    idle_panels = settings.get("idle_panels") if isinstance(settings, dict) else {}
    weather_panel = idle_panels.get("weather") if isinstance(idle_panels, dict) else {}
    layout = str(weather_panel.get("layout") or "split").strip().lower() if isinstance(weather_panel, dict) else "split"
    return layout if layout in ("split", "minimal") else "split"


def _optional_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _optional_int(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except Exception:
        return None


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return None


def _atomic_write_json(path: str, payload: dict[str, object]) -> None:
    if not path:
        return
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=True, separators=(",", ":"))
    os.replace(tmp, path)


def _first_wins_dedupe(args: list[str]) -> list[str]:
    """Drop duplicate singleton mpv options; keep the first value."""
    singletons = (
        "--input-ipc-server",
        "--audio-file",
        "--audio-device",
        "--volume",
        "--sub-auto",
        "--slang",
        "--osd-level",
        "--osd-playing-msg",
        "--term-playing-msg",
        "--osc",
        "--log-file",
        "--msg-level",
        "--profile",
        "--ytdl",
        "--script-opts",
        "--ytdl-format",
        "--ytdl-raw-options",
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


def _build_mpv_args(
    stream: str,
    wid: int,
    audio: str | None = None,
    ipc_path: str | None = None,
    audio_device: str | None = None,
    sub_lang: str | None = None,
    volume: float | None = None,
    ytdl_enabled: bool = True,
    ytdl_path: str | None = None,
    ytdl_format: str | None = None,
    ytdl_raw_options: str | None = None,
) -> list[str]:
    debug = _env_bool("MPV_DEBUG") or _env_bool("RELAYTV_DEBUG")
    extra = _split_env_args("RELAYTV_QT_SHELL_MPV_ARGS")
    if not extra:
        extra = _split_env_args("MPV_ARGS")
    qpa_platform = (os.getenv("QT_QPA_PLATFORM") or "").strip().lower()
    x11_bridge_mode = qpa_platform in ("xcb", "x11") or qpa_platform.startswith("xcb:")
    args = [
        "mpv",
        "--fs",
        f"--wid={wid}",
        "--no-terminal",
        "--force-window=yes",
        "--keep-open=no",
        "--osc=no",
        "--osd-level=0",
        "--osd-playing-msg=",
        "--term-playing-msg=",
    ]
    log_file = (os.getenv("MPV_LOG_FILE") or "").strip()
    if not log_file and debug:
        log_file = "/tmp/mpv.log"
    if log_file and not _has_opt(args + extra, "--log-file"):
        args.append(f"--log-file={log_file}")
    if debug and not _has_opt(args + extra, "--msg-level"):
        args.append("--msg-level=all=debug")
    arm_fast_default = _env_bool("RELAYTV_ARM_FAST_PROFILE", True)
    if arm_fast_default and (platform.machine() or "").lower() in ("aarch64", "arm64") and not _has_opt(args + extra, "--profile"):
        args.append("--profile=fast")
    if ipc_path:
        args.append(f"--input-ipc-server={ipc_path}")
    if audio:
        args.append(f"--audio-file={audio}")
    if audio_device:
        args.append(f"--audio-device={audio_device}")
    if sub_lang:
        args.append("--sub-auto=fuzzy")
        args.append(f"--slang={sub_lang}")
    if volume is not None:
        args.append(f"--volume={float(volume):g}")
    if ytdl_enabled:
        args.append("--ytdl=yes")
        ypath = (ytdl_path or "").strip() or "yt-dlp"
        args.append(f"--script-opts=ytdl_hook-ytdl_path={ypath}")
        yfmt = (ytdl_format or "").strip()
        if yfmt:
            args.append(f"--ytdl-format={yfmt}")
        yraw = (ytdl_raw_options or "").strip()
        if yraw:
            args.append(f"--ytdl-raw-options={yraw}")
    else:
        args.append("--ytdl=no")
    if x11_bridge_mode:
        # With QT_QPA_PLATFORM=xcb, force mpv off native Wayland so --wid
        # targets the same X11/XWayland display stack as the Qt window.
        if not _has_opt(args + extra, "--vo"):
            args.append("--vo=gpu")
        if not _has_opt(args + extra, "--gpu-context"):
            args.append("--gpu-context=x11egl")
    if extra:
        args.extend(extra)
    args = _first_wins_dedupe(args)
    args.append(stream)
    return args


def _with_cache_buster(url: str) -> str:
    """Append/replace a timestamp query parameter to force a fresh overlay fetch."""
    u = (url or "").strip()
    if not u:
        return u
    try:
        parts = urlsplit(u)
        q = dict(parse_qsl(parts.query, keep_blank_values=True))
        q["ts"] = str(int(time.time() * 1000))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))
    except Exception:
        return u


class _MpvOpenGLInitParams(ctypes.Structure):
    _fields_ = [
        ("get_proc_address", ctypes.c_void_p),
        ("get_proc_address_ctx", ctypes.c_void_p),
        ("extra_exts", ctypes.c_void_p),
    ]


class _MpvOpenGLFBO(ctypes.Structure):
    _fields_ = [
        ("fbo", ctypes.c_int),
        ("w", ctypes.c_int),
        ("h", ctypes.c_int),
        ("internal_format", ctypes.c_int),
    ]


class _MpvRenderParam(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("data", ctypes.c_void_p),
    ]


class _MpvNodeList(ctypes.Structure):
    pass


class _MpvNodeUnion(ctypes.Union):
    _fields_ = [
        ("string", ctypes.c_char_p),
        ("flag", ctypes.c_int),
        ("int64", ctypes.c_int64),
        ("double_", ctypes.c_double),
        ("list", ctypes.POINTER(_MpvNodeList)),
        ("ba", ctypes.c_void_p),
    ]


class _MpvNode(ctypes.Structure):
    _fields_ = [
        ("u", _MpvNodeUnion),
        ("format", ctypes.c_int),
    ]


_MpvNodeList._fields_ = [
    ("num", ctypes.c_int),
    ("values", ctypes.POINTER(_MpvNode)),
    ("keys", ctypes.POINTER(ctypes.c_char_p)),
]


_MPV_RENDER_PARAM_INVALID = 0
_MPV_RENDER_PARAM_API_TYPE = 1
_MPV_RENDER_PARAM_OPENGL_INIT_PARAMS = 2
_MPV_RENDER_PARAM_OPENGL_FBO = 3
_MPV_RENDER_PARAM_FLIP_Y = 4

_MPV_FORMAT_STRING = 1
_MPV_FORMAT_OSD_STRING = 2
_MPV_FORMAT_FLAG = 3
_MPV_FORMAT_INT64 = 4
_MPV_FORMAT_DOUBLE = 5
_MPV_FORMAT_NODE = 6
_MPV_FORMAT_NODE_ARRAY = 7
_MPV_FORMAT_NODE_MAP = 8


def _find_libmpv() -> str | None:
    p = ctypes.util.find_library("mpv")
    if p:
        return p
    for cand in ("libmpv.so.2", "libmpv.so"):
        try:
            ctypes.CDLL(cand)
            return cand
        except Exception:
            continue
    return None


def _as_c_str(value: str) -> bytes:
    return value.encode("utf-8", errors="ignore")


class _QtLibMpvPlayer:
    def __init__(self, *, debug: bool = False) -> None:
        self.debug = bool(debug)
        self._lib = None
        self._handle = ctypes.c_void_p()
        self._render_ctx = ctypes.c_void_p()
        self._api_type = ctypes.c_char_p(b"opengl")
        self._flip_y = ctypes.c_int(1)
        self._gl_init_params: _MpvOpenGLInitParams | None = None
        self._gl_proc_cb = None
        self._update_cb = None
        self._have_render = False
        self._track_list_cache: list[dict[str, object]] | None = None
        self._track_list_cache_ts = 0.0
        self._track_list_cache_aid: int | None = None
        self._track_list_cache_path = ""

    def _bind_api(self) -> None:
        if self._lib is not None:
            return
        lib_path = _find_libmpv()
        if not lib_path:
            raise RuntimeError("libmpv shared library not found")
        self._lib = ctypes.CDLL(lib_path)

        self._lib.mpv_create.restype = ctypes.c_void_p
        self._lib.mpv_set_option_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        self._lib.mpv_set_option_string.restype = ctypes.c_int
        self._lib.mpv_initialize.argtypes = [ctypes.c_void_p]
        self._lib.mpv_initialize.restype = ctypes.c_int
        self._lib.mpv_command.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p)]
        self._lib.mpv_command.restype = ctypes.c_int
        if hasattr(self._lib, "mpv_get_property_string"):
            self._lib.mpv_get_property_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            self._lib.mpv_get_property_string.restype = ctypes.c_void_p
        if hasattr(self._lib, "mpv_set_property_string"):
            self._lib.mpv_set_property_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
            self._lib.mpv_set_property_string.restype = ctypes.c_int
        if hasattr(self._lib, "mpv_get_property"):
            self._lib.mpv_get_property.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
            self._lib.mpv_get_property.restype = ctypes.c_int
        if hasattr(self._lib, "mpv_free"):
            self._lib.mpv_free.argtypes = [ctypes.c_void_p]
            self._lib.mpv_free.restype = None
        if hasattr(self._lib, "mpv_free_node_contents"):
            self._lib.mpv_free_node_contents.argtypes = [ctypes.POINTER(_MpvNode)]
            self._lib.mpv_free_node_contents.restype = None
        self._lib.mpv_error_string.argtypes = [ctypes.c_int]
        self._lib.mpv_error_string.restype = ctypes.c_char_p
        self._lib.mpv_terminate_destroy.argtypes = [ctypes.c_void_p]
        self._lib.mpv_terminate_destroy.restype = None
        self._lib.mpv_render_context_create.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            ctypes.POINTER(_MpvRenderParam),
        ]
        self._lib.mpv_render_context_create.restype = ctypes.c_int
        self._lib.mpv_render_context_free.argtypes = [ctypes.c_void_p]
        self._lib.mpv_render_context_free.restype = None
        self._lib.mpv_render_context_render.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MpvRenderParam)]
        self._lib.mpv_render_context_render.restype = None
        if hasattr(self._lib, "mpv_render_context_update"):
            self._lib.mpv_render_context_update.argtypes = [ctypes.c_void_p]
            self._lib.mpv_render_context_update.restype = ctypes.c_uint64
        if hasattr(self._lib, "mpv_render_context_set_update_callback"):
            self._lib.mpv_render_context_set_update_callback.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
            self._lib.mpv_render_context_set_update_callback.restype = None

    def _err(self, code: int) -> str:
        if self._lib is None:
            return f"mpv_error={code}"
        try:
            raw = self._lib.mpv_error_string(int(code))
            if raw:
                return raw.decode("utf-8", errors="ignore")
        except Exception:
            pass
        return f"mpv_error={code}"

    def _set_opt(self, name: str, value: str) -> None:
        if self._lib is None or not self._handle:
            raise RuntimeError("mpv handle unavailable")
        rc = int(self._lib.mpv_set_option_string(self._handle, _as_c_str(name), _as_c_str(value)))
        if rc < 0:
            raise RuntimeError(f"set_option {name}={value} failed: {self._err(rc)}")

    def _set_opt_best_effort(self, name: str, value: str) -> None:
        try:
            self._set_opt(name, value)
        except Exception as exc:
            if self.debug:
                _eprint(f"libmpv option skipped: {name}={value} ({exc})")

    def _command(self, *parts: str) -> None:
        if self._lib is None or not self._handle:
            raise RuntimeError("mpv handle unavailable")
        arr = (ctypes.c_char_p * (len(parts) + 1))()
        for idx, part in enumerate(parts):
            arr[idx] = _as_c_str(str(part))
        arr[len(parts)] = None
        rc = int(self._lib.mpv_command(self._handle, arr))
        if rc < 0:
            raise RuntimeError(f"mpv command failed ({' '.join(parts)}): {self._err(rc)}")

    @staticmethod
    def _command_arg(value: object) -> str:
        if isinstance(value, bool):
            return "yes" if value else "no"
        if value is None:
            return ""
        return str(value)

    def command_list(self, parts: list[object]) -> None:
        items = [self._command_arg(part) for part in list(parts or [])]
        if not items:
            raise RuntimeError("command list empty")
        self._command(*items)

    def load_stream(self, stream: str, audio: str | None = None) -> None:
        s = str(stream or "").strip()
        if not s:
            raise RuntimeError("stream required")
        a = str(audio or "").strip() if isinstance(audio, str) else ""
        if a:
            self._command("loadfile", s, "replace", "-1", f"audio-files-append={a}")
            return
        self._command("loadfile", s, "replace")

    def set_property(self, name: str, value: object) -> None:
        key = str(name or "").strip()
        if not key:
            raise RuntimeError("property name required")
        setter = getattr(self._lib, "mpv_set_property_string", None) if self._lib is not None else None
        arg = self._command_arg(value)
        if setter is not None and self._handle:
            rc = int(setter(self._handle, _as_c_str(key), _as_c_str(arg)))
            if rc < 0:
                raise RuntimeError(f"set_property {key}={arg} failed: {self._err(rc)}")
            return
        self._command("set", key, arg)

    def _get_property_string(self, name: str) -> str | None:
        if self._lib is None or not self._handle:
            raise RuntimeError("mpv handle unavailable")
        getter = getattr(self._lib, "mpv_get_property_string", None)
        freer = getattr(self._lib, "mpv_free", None)
        if getter is None or freer is None:
            raise RuntimeError("mpv_get_property_string unavailable")
        raw_ptr = ctypes.c_void_p(getter(self._handle, _as_c_str(name)))
        if not raw_ptr.value:
            return None
        try:
            raw = ctypes.cast(raw_ptr, ctypes.c_char_p).value
            if raw is None:
                return None
            return raw.decode("utf-8", errors="replace")
        finally:
            freer(raw_ptr)

    @staticmethod
    def _node_to_python(node: _MpvNode):
        fmt = int(getattr(node, "format", 0) or 0)
        if fmt in (_MPV_FORMAT_STRING, _MPV_FORMAT_OSD_STRING):
            raw = node.u.string
            if not raw:
                return ""
            try:
                return raw.decode("utf-8", errors="replace")
            except Exception:
                return ""
        if fmt == _MPV_FORMAT_FLAG:
            return bool(node.u.flag)
        if fmt == _MPV_FORMAT_INT64:
            return int(node.u.int64)
        if fmt == _MPV_FORMAT_DOUBLE:
            return float(node.u.double_)
        if fmt in (_MPV_FORMAT_NODE_ARRAY, _MPV_FORMAT_NODE_MAP):
            node_list_ptr = node.u.list
            if not node_list_ptr:
                return [] if fmt == _MPV_FORMAT_NODE_ARRAY else {}
            node_list = node_list_ptr.contents
            count = max(0, int(node_list.num or 0))
            if fmt == _MPV_FORMAT_NODE_ARRAY:
                return [
                    _QtLibMpvPlayer._node_to_python(node_list.values[idx])
                    for idx in range(count)
                ]
            out: dict[str, object] = {}
            for idx in range(count):
                key = ""
                try:
                    key_ptr = node_list.keys[idx] if node_list.keys else None
                    if key_ptr:
                        key = key_ptr.decode("utf-8", errors="replace")
                except Exception:
                    key = ""
                if not key:
                    key = str(idx)
                out[key] = _QtLibMpvPlayer._node_to_python(node_list.values[idx])
            return out
        return None

    def _get_property_node(self, name: str):
        if self._lib is None or not self._handle:
            raise RuntimeError("mpv handle unavailable")
        getter = getattr(self._lib, "mpv_get_property", None)
        freer = getattr(self._lib, "mpv_free_node_contents", None)
        if getter is None or freer is None:
            raise RuntimeError("mpv_get_property unavailable")
        node = _MpvNode()
        rc = int(getter(self._handle, _as_c_str(name), _MPV_FORMAT_NODE, ctypes.byref(node)))
        if rc < 0:
            raise RuntimeError(f"get_property {name} failed: {self._err(rc)}")
        try:
            return self._node_to_python(node)
        finally:
            freer(ctypes.byref(node))

    @staticmethod
    def _normalize_track_list(raw):
        if not isinstance(raw, list):
            return None
        out: list[dict[str, object]] = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            item: dict[str, object] = {}
            kind = str(row.get("type") or "").strip().lower()
            if kind:
                item["type"] = kind
            try:
                tid = int(row.get("id"))
                if tid > 0:
                    item["id"] = tid
            except Exception:
                pass
            for key in ("src-id", "ff-index"):
                try:
                    item[key] = int(row.get(key))
                except Exception:
                    pass
            for key in ("lang", "language", "title", "name"):
                val = str(row.get(key) or "").strip()
                if val:
                    item[key] = val
            if "selected" in row:
                item["selected"] = bool(row.get("selected"))
            if "default" in row:
                item["default"] = bool(row.get("default"))
            if item:
                out.append(item)
        return out

    def _runtime_track_list(self, *, aid: int | None, path: str, read_node) -> list[dict[str, object]] | None:
        path_norm = str(path or "").strip()
        if not path_norm:
            self._track_list_cache = None
            self._track_list_cache_ts = 0.0
            self._track_list_cache_aid = None
            self._track_list_cache_path = ""
            return None
        now = time.time()
        refresh_sec = max(1.0, float(os.getenv("RELAYTV_QT_TRACKLIST_REFRESH_SEC", "5.0") or "5.0"))
        should_refresh = self._track_list_cache is None
        if not should_refresh and path_norm != self._track_list_cache_path:
            should_refresh = True
        if not should_refresh and aid is not None and aid != self._track_list_cache_aid:
            should_refresh = True
        if not should_refresh and (now - float(self._track_list_cache_ts or 0.0)) >= refresh_sec:
            should_refresh = True
        if should_refresh:
            normalized = self._normalize_track_list(read_node("track-list"))
            if normalized is not None:
                self._track_list_cache = normalized
                self._track_list_cache_ts = now
                self._track_list_cache_aid = aid
                self._track_list_cache_path = path_norm
            elif self._track_list_cache_path != path_norm:
                self._track_list_cache = None
                self._track_list_cache_ts = 0.0
                self._track_list_cache_aid = aid
                self._track_list_cache_path = path_norm
        return self._track_list_cache

    def runtime_snapshot(self) -> dict[str, object]:
        out: dict[str, object] = {
            "mpv_runtime_initialized": True,
            "mpv_runtime_error": "",
        }
        prop_errors: list[str] = []

        def _read(name: str) -> str | None:
            try:
                return self._get_property_string(name)
            except Exception as exc:
                prop_errors.append(f"{name}:{type(exc).__name__}")
                return None

        def _read_node(name: str):
            try:
                return self._get_property_node(name)
            except Exception as exc:
                prop_errors.append(f"{name}:{type(exc).__name__}")
                return None

        paused = _optional_bool(_read("pause"))
        time_pos = _optional_float(_read("time-pos"))
        duration = _optional_float(_read("duration"))
        volume = _optional_float(_read("volume"))
        mute = _optional_bool(_read("mute"))
        core_idle = _optional_bool(_read("core-idle"))
        eof_reached = _optional_bool(_read("eof-reached"))
        path = str(_read("path") or "").strip()
        current_vo = str(_read("current-vo") or "").strip()
        current_ao = str(_read("current-ao") or "").strip()
        aid = _optional_int(_read("aid"))
        playlist_pos = _optional_int(_read("playlist-pos"))
        playlist_count = _optional_int(_read("playlist-count"))
        track_list = self._runtime_track_list(aid=aid, path=path, read_node=_read_node)

        playback_active = bool(path) and (core_idle is not True) and (eof_reached is not True)
        playback_started = playback_active and (
            (isinstance(time_pos, (int, float)) and float(time_pos) > 0.0)
            or (isinstance(duration, (int, float)) and float(duration) > 0.0)
        )

        out.update(
            {
                "mpv_runtime_paused": paused,
                "mpv_runtime_time_pos": time_pos,
                "mpv_runtime_duration": duration,
                "mpv_runtime_volume": volume,
                "mpv_runtime_mute": mute,
                "mpv_runtime_core_idle": core_idle,
                "mpv_runtime_eof_reached": eof_reached,
                "mpv_runtime_path": path,
                "mpv_runtime_current_vo": current_vo,
                "mpv_runtime_current_ao": current_ao,
                "mpv_runtime_aid": aid,
                "mpv_runtime_playlist_pos": playlist_pos,
                "mpv_runtime_playlist_count": playlist_count,
                "mpv_runtime_track_list": track_list,
                "mpv_runtime_playback_active": playback_active,
                "mpv_runtime_stream_loaded": bool(path),
                "mpv_runtime_playback_started": playback_started,
                "mpv_runtime_sample_detail": ("property_read_degraded" if prop_errors else ""),
            }
        )
        if prop_errors and not path:
            out["mpv_runtime_error"] = ",".join(prop_errors[:4])
        return out

    def start(
        self,
        *,
        stream: str,
        audio: str | None,
        ipc_path: str | None,
        audio_device: str | None,
        sub_lang: str | None,
        volume: float | None,
        ytdl_enabled: bool,
        ytdl_path: str | None,
        ytdl_format: str | None,
        ytdl_raw_options: str | None,
    ) -> None:
        self._bind_api()
        handle = ctypes.c_void_p(self._lib.mpv_create())
        if not handle:
            raise RuntimeError("mpv_create returned null handle")
        self._handle = handle

        # Ensure libmpv renders into our Qt OpenGL context, not a native window.
        self._set_opt("vo", "libmpv")
        self._set_opt_best_effort("gpu-api", "opengl")
        self._set_opt_best_effort("keep-open", "no")
        self._set_opt_best_effort("idle", "yes")
        self._set_opt_best_effort("osc", "no")
        self._set_opt_best_effort("osd-level", "0")
        self._set_opt_best_effort("osd-playing-msg", "")
        self._set_opt_best_effort("term-playing-msg", "")
        self._set_opt_best_effort("terminal", "no")
        self._set_opt_best_effort("force-window", "yes")

        log_file = (os.getenv("MPV_LOG_FILE") or "").strip()
        if not log_file and self.debug:
            log_file = "/tmp/mpv.log"
        if log_file:
            self._set_opt_best_effort("log-file", log_file)
        if self.debug:
            self._set_opt_best_effort("msg-level", "all=debug")

        arm_fast_default = _env_bool("RELAYTV_ARM_FAST_PROFILE", True)
        if arm_fast_default and (platform.machine() or "").lower() in ("aarch64", "arm64"):
            self._set_opt_best_effort("profile", "fast")

        if ipc_path:
            self._set_opt_best_effort("input-ipc-server", ipc_path)
        if audio_device:
            self._set_opt_best_effort("audio-device", audio_device)
        if sub_lang:
            self._set_opt_best_effort("sub-auto", "fuzzy")
            self._set_opt_best_effort("slang", sub_lang)
        if volume is not None:
            self._set_opt_best_effort("volume", f"{float(volume):g}")
        if ytdl_enabled:
            self._set_opt_best_effort("ytdl", "yes")
            ypath = (ytdl_path or "").strip() or "yt-dlp"
            self._set_opt_best_effort("script-opts", f"ytdl_hook-ytdl_path={ypath}")
            if (ytdl_format or "").strip():
                self._set_opt_best_effort("ytdl-format", (ytdl_format or "").strip())
            if (ytdl_raw_options or "").strip():
                self._set_opt_best_effort("ytdl-raw-options", (ytdl_raw_options or "").strip())
        else:
            self._set_opt_best_effort("ytdl", "no")

        # Honor CLI-style extra args best-effort (only option tokens).
        extra = _split_env_args("RELAYTV_QT_SHELL_MPV_ARGS")
        if not extra:
            extra = _split_env_args("MPV_ARGS")
        blocked_extra = {
            "wid",
            "vo",
            "gpu-context",
            "gpu-api",
            "wayland-display",
            "x11-display",
        }
        for tok in extra:
            t = str(tok or "").strip()
            if not t.startswith("--"):
                continue
            body = t[2:]
            if not body:
                continue
            if "=" in body:
                k, v = body.split("=", 1)
                if k in blocked_extra:
                    if self.debug:
                        _eprint(f"libmpv ignoring extra arg --{k}=...")
                    continue
                if k:
                    self._set_opt_best_effort(k, v)
                continue
            if body.startswith("no-") and len(body) > 3:
                k = body[3:]
                if k in blocked_extra:
                    if self.debug:
                        _eprint(f"libmpv ignoring extra arg --no-{k}")
                    continue
                self._set_opt_best_effort(k, "no")
                continue
            if body in blocked_extra:
                if self.debug:
                    _eprint(f"libmpv ignoring extra arg --{body}")
                continue
            self._set_opt_best_effort(body, "yes")

        rc = int(self._lib.mpv_initialize(self._handle))
        if rc < 0:
            raise RuntimeError(f"mpv_initialize failed: {self._err(rc)}")

        # Initial media is loaded after QOpenGLWidget.initializeGL() creates the
        # render context. Loading before that can leave libmpv playing audio
        # without selecting/painting the video track on some Qt/OpenGL stacks.

    def init_render_context(self, get_proc_address) -> None:
        if self._have_render:
            return
        if self._lib is None or not self._handle:
            raise RuntimeError("mpv handle unavailable")

        cb_type = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)

        def _cb(_ctx, name):
            try:
                addr = get_proc_address(name)
                if addr:
                    return int(addr)
            except Exception:
                pass
            return 0

        self._gl_proc_cb = cb_type(_cb)
        self._gl_init_params = _MpvOpenGLInitParams(
            ctypes.cast(self._gl_proc_cb, ctypes.c_void_p),
            ctypes.c_void_p(),
            ctypes.c_void_p(),
        )
        params = (_MpvRenderParam * 3)()
        params[0] = _MpvRenderParam(
            _MPV_RENDER_PARAM_API_TYPE,
            ctypes.cast(self._api_type, ctypes.c_void_p),
        )
        params[1] = _MpvRenderParam(
            _MPV_RENDER_PARAM_OPENGL_INIT_PARAMS,
            ctypes.cast(ctypes.pointer(self._gl_init_params), ctypes.c_void_p),
        )
        params[2] = _MpvRenderParam(_MPV_RENDER_PARAM_INVALID, ctypes.c_void_p())
        rc = int(self._lib.mpv_render_context_create(ctypes.byref(self._render_ctx), self._handle, params))
        if rc < 0:
            raise RuntimeError(f"mpv_render_context_create failed: {self._err(rc)}")
        if hasattr(self._lib, "mpv_render_context_set_update_callback"):
            cb_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
            self._update_cb = cb_type(lambda _ctx: None)
            self._lib.mpv_render_context_set_update_callback(self._render_ctx, self._update_cb, ctypes.c_void_p())
        self._have_render = True

    def render_context_ready(self) -> bool:
        return bool(self._have_render and self._render_ctx)

    def render(self, *, fbo: int, width: int, height: int) -> None:
        if not self._have_render or self._lib is None or not self._render_ctx:
            return
        if width <= 0 or height <= 0:
            return
        if hasattr(self._lib, "mpv_render_context_update"):
            try:
                self._lib.mpv_render_context_update(self._render_ctx)
            except Exception:
                pass
        fbo_state = _MpvOpenGLFBO(int(fbo), int(width), int(height), 0x8058)
        params = (_MpvRenderParam * 3)()
        params[0] = _MpvRenderParam(
            _MPV_RENDER_PARAM_OPENGL_FBO,
            ctypes.cast(ctypes.pointer(fbo_state), ctypes.c_void_p),
        )
        params[1] = _MpvRenderParam(
            _MPV_RENDER_PARAM_FLIP_Y,
            ctypes.cast(ctypes.pointer(self._flip_y), ctypes.c_void_p),
        )
        params[2] = _MpvRenderParam(_MPV_RENDER_PARAM_INVALID, ctypes.c_void_p())
        self._lib.mpv_render_context_render(self._render_ctx, params)

    def terminate(self) -> None:
        try:
            if self._lib is not None and self._render_ctx:
                if hasattr(self._lib, "mpv_render_context_set_update_callback"):
                    try:
                        self._lib.mpv_render_context_set_update_callback(self._render_ctx, ctypes.c_void_p(), ctypes.c_void_p())
                    except Exception:
                        pass
                self._lib.mpv_render_context_free(self._render_ctx)
        except Exception:
            pass
        self._render_ctx = ctypes.c_void_p()
        self._have_render = False
        self._update_cb = None
        try:
            if self._lib is not None and self._handle:
                self._lib.mpv_terminate_destroy(self._handle)
        except Exception:
            pass
        self._handle = ctypes.c_void_p()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    ap = argparse.ArgumentParser(description="RelayTV Qt shell (experimental)")
    ap.add_argument("--stream", default="")
    ap.add_argument("--audio", default="")
    ap.add_argument("--ipc-path", default=os.getenv("MPV_IPC_PATH", "/tmp/mpv.sock"))
    ap.add_argument("--audio-device", default="")
    ap.add_argument("--sub-lang", default="")
    ap.add_argument("--volume", type=float, default=None)
    ap.add_argument("--ytdl-enabled", default="1")
    ap.add_argument("--ytdl-path", default="")
    ap.add_argument("--ytdl-format", default="")
    ap.add_argument("--ytdl-raw-options", default="")
    ap.add_argument("--overlay-url", default=os.getenv("RELAYTV_QT_OVERLAY_URL", "http://127.0.0.1:8787/x11/overlay"))
    ap.add_argument("--window-title", default="RelayTV Qt Shell")
    args = ap.parse_args(argv)
    debug = _env_bool("RELAYTV_DEBUG") or _env_bool("MPV_DEBUG")
    qpa_platform = (os.getenv("QT_QPA_PLATFORM") or "").strip().lower()
    headless_qpa = qpa_platform in ("offscreen", "vnc", "minimal")
    overlay_enabled = _embedded_web_overlay_enabled()
    if headless_qpa and not _env_bool("RELAYTV_QT_OVERLAY_HEADLESS", False):
        overlay_enabled = False
    overlay_software_mode = overlay_enabled and _overlay_software_mode_enabled()
    if overlay_software_mode:
        _append_env_flags(
            "QTWEBENGINE_CHROMIUM_FLAGS",
            [
                "--disable-gpu",
                "--disable-gpu-compositing",
                "--disable-gpu-rasterization",
                "--disable-zero-copy",
            ],
        )

    try:
        from PySide6.QtCore import QEvent, QObject, QTimer, Qt, QUrl
        from PySide6.QtGui import QCursor, QGuiApplication, QOpenGLContext, QPainter, QPixmap
        from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
        from PySide6.QtOpenGLWidgets import QOpenGLWidget
        from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QSizePolicy, QVBoxLayout, QWidget
    except Exception as e:
        _eprint("RelayTV Qt shell requires PySide6.")
        _eprint("Install with: pip install 'relaytv[qt]' or pip install PySide6")
        _eprint("Error:", e)
        return 2

    QWebEngineView = None
    QWebEngineProfile = None
    QWebEngineSettings = None
    if overlay_enabled:
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView  # type: ignore[assignment]
            from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings  # type: ignore[assignment]
        except Exception as e:
            _eprint("qt-shell: disabling overlay; QtWebEngine unavailable:", e)
            overlay_enabled = False

    app = QApplication([sys.argv[0]])

    win = QMainWindow()
    win.setWindowTitle(args.window_title)
    win.setWindowFlags(Qt.FramelessWindowHint)

    native_toasts_enabled = (not headless_qpa) and _native_overlay_toasts_enabled()
    native_idle_enabled = (not headless_qpa) and _native_idle_overlay_enabled() and (not overlay_enabled)

    class _NativeToastLayer(QWidget):
        def __init__(self, parent: QWidget, *, overlay_url: str):
            super().__init__(parent)
            self._overlay_url = str(overlay_url or "")
            self._net = QNetworkAccessManager(self)
            self._toasts: list[QWidget] = []
            self._replies: set[object] = set()
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.setStyleSheet("background: transparent;")
            self._layout = QVBoxLayout(self)
            self._layout.setContentsMargins(22, 22, 22, 22)
            self._layout.setSpacing(11)
            self._layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        def _position_name(self, payload: dict[str, object]) -> str:
            position = str(payload.get("position") or "top-left").strip().lower()
            allowed = {"top-left", "top-right", "bottom-left", "bottom-right", "top-center"}
            return position if position in allowed else "top-left"

        def _layout_alignment_for_position(self, position: str) -> int:
            pos = str(position or "top-left").strip().lower()
            vertical = Qt.AlignBottom if pos.startswith("bottom-") else Qt.AlignTop
            if pos.endswith("right"):
                horizontal = Qt.AlignRight
            elif pos.endswith("center"):
                horizontal = Qt.AlignHCenter
            else:
                horizontal = Qt.AlignLeft
            return vertical | horizontal

        def _insert_index_for_position(self, position: str) -> int:
            return self._layout.count() if str(position or "").startswith("bottom-") else 0

        def _icon_text(self, payload: dict[str, object]) -> str:
            icon = str(payload.get("icon") or "").strip().lower()
            if icon:
                return {
                    "share": "↗",
                    "check": "✓",
                    "warn": "!",
                    "camera": "📷",
                    "play": "▶",
                    "info": "i",
                }.get(icon, icon[:1])
            level = str(payload.get("level") or "info").strip().lower()
            return {"success": "✓", "warn": "!", "error": "!"}.get(level, "•")

        def _sanitize_image_url(self, raw_url: str) -> str:
            text = str(raw_url or "").strip()
            if not text:
                return ""
            if text.startswith("/"):
                return urljoin(self._overlay_url, text)
            if text.lower().startswith(("http://", "https://", "data:image/")):
                return text
            return ""

        def _apply_image_bytes(self, label: QLabel, data: bytes) -> None:
            if not data:
                return
            pix = QPixmap()
            if not pix.loadFromData(data):
                return
            scaled = pix.scaled(380, 124, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            label.setPixmap(scaled)
            label.show()

        def _finish_reply(self, reply, label: QLabel, timer: QTimer) -> None:
            try:
                timer.stop()
            except Exception:
                pass
            try:
                self._replies.discard(reply)
            except Exception:
                pass
            try:
                if reply.error():
                    reply.deleteLater()
                    return
                self._apply_image_bytes(label, bytes(reply.readAll()))
            except Exception:
                pass
            try:
                reply.deleteLater()
            except Exception:
                pass

        def _load_image(self, label: QLabel, raw_url: str) -> None:
            raw_text = str(raw_url or "").strip()
            if raw_text.startswith("/thumbs/"):
                try:
                    thumb_dir = (os.getenv("RELAYTV_THUMB_DIR") or os.getenv("BRAVECAST_THUMB_DIR") or "/data/thumbs").strip()
                    thumb_name = os.path.basename(raw_text)
                    thumb_path = os.path.join(thumb_dir, thumb_name)
                    with open(thumb_path, "rb") as fh:
                        self._apply_image_bytes(label, fh.read())
                    return
                except Exception:
                    pass
            safe_url = self._sanitize_image_url(raw_url)
            if not safe_url:
                return
            if safe_url.lower().startswith("data:image/"):
                try:
                    _, payload = safe_url.split(",", 1)
                    self._apply_image_bytes(label, base64.b64decode(payload))
                except Exception:
                    pass
                return
            reply = self._net.get(QNetworkRequest(QUrl(safe_url)))
            timer = QTimer(reply)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda r=reply: (self._replies.discard(r), r.abort(), r.deleteLater()))
            reply.finished.connect(lambda r=reply, image_label=label, t=timer: self._finish_reply(r, image_label, t))
            self._replies.add(reply)
            timer.start(4000)

        def _remove_toast(self, widget: QWidget) -> None:
            try:
                if widget in self._toasts:
                    self._toasts.remove(widget)
            except RuntimeError:
                pass
            try:
                self._layout.removeWidget(widget)
            except RuntimeError:
                pass
            try:
                widget.hide()
            except RuntimeError:
                pass
            try:
                widget.deleteLater()
            except RuntimeError:
                pass
            if not self._toasts:
                self.hide()

        def clear_all(self) -> None:
            for widget in list(self._toasts):
                try:
                    self._layout.removeWidget(widget)
                except RuntimeError:
                    pass
                try:
                    widget.hide()
                except RuntimeError:
                    pass
                try:
                    widget.deleteLater()
                except RuntimeError:
                    pass
            self._toasts.clear()
            self.hide()

        def show_toast(self, payload: dict[str, object]) -> None:
            text = str(payload.get("text") or "").strip()
            if not text and payload.get("image_url"):
                text = "[image]"
            if not text:
                return
            position = self._position_name(payload)
            item_alignment = self._layout_alignment_for_position(position)
            self._layout.setAlignment(item_alignment)
            frame = QFrame(self)
            frame.setObjectName("qt-native-toast")
            frame.setMaximumWidth(430)
            frame.setStyleSheet(
                "QFrame#qt-native-toast{background:rgba(9,16,28,210);border:1px solid rgba(42,168,255,68);"
                "border-radius:15px;} QLabel{color:#eef4ff;font-size:16px;}"
            )
            box = QVBoxLayout(frame)
            box.setContentsMargins(14, 13, 14, 13)
            box.setSpacing(10)
            top = QHBoxLayout()
            top.setSpacing(10)
            ico = QLabel(self._icon_text(payload), frame)
            ico.setFixedWidth(18)
            msg = QLabel(text, frame)
            msg.setWordWrap(True)
            msg.setTextInteractionFlags(Qt.NoTextInteraction)
            top.addWidget(ico, 0, Qt.AlignTop)
            top.addWidget(msg, 1)
            box.addLayout(top)
            link_url = str(payload.get("link_url") or "").strip()
            if link_url:
                link = QLabel(str(payload.get("link_text") or link_url), frame)
                link.setWordWrap(True)
                link.setStyleSheet("color:#b9d6ff;font-size:13px;")
                box.addWidget(link)
            image_url = str(payload.get("image_url") or "").strip()
            if image_url:
                img = QLabel(frame)
                img.setFixedHeight(124)
                img.setAlignment(Qt.AlignCenter)
                img.setStyleSheet("background:rgba(255,255,255,10); border:1px solid rgba(130,170,220,64); border-radius:11px;")
                img.hide()
                box.addWidget(img)
                self._load_image(img, image_url)
            insert_at = self._insert_index_for_position(position)
            self._layout.insertWidget(insert_at, frame, 0, item_alignment)
            self._toasts.insert(insert_at, frame)
            while len(self._toasts) > 4:
                self._remove_toast(self._toasts[-1])
            self.show()
            self.raise_()
            ttl_sec = max(0.8, float(payload.get("duration") or 4.0))
            ttl_ms = min(30000, max(800, int(ttl_sec * 1000.0)))
            QTimer.singleShot(ttl_ms, lambda w=frame: self._remove_toast(w))

    class _NativeIdleLayer(QWidget):
        def __init__(self, parent: QWidget | None, *, overlay_url: str):
            super().__init__(parent)
            self._overlay_url = str(overlay_url or "")
            self._net = QNetworkAccessManager(self)
            self._replies: set[object] = set()
            self._qr_target_url = ""
            self._qr_enabled = True
            self._qr_size = _native_idle_qr_size(None)
            self._device_name = _native_idle_device_name()
            self._logo_path = _native_idle_logo_path()
            self._last_settings_payload: dict[str, object] | None = None
            self._weather_signature = ""
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.setStyleSheet("background: transparent;")

            root = QVBoxLayout(self)
            root.setContentsMargins(32, 28, 32, 24)
            root.setSpacing(18)

            top = QHBoxLayout()
            top.addStretch(1)
            self._logo_label = QLabel(self)
            self._logo_label.setAlignment(Qt.AlignCenter)
            self._logo_label.setMinimumSize(180, 72)
            self._logo_label.setStyleSheet("background:transparent;color:rgba(238,245,255,0.94);font-size:24px;font-weight:700;")
            top.addWidget(self._logo_label, 0, Qt.AlignCenter)
            top.addStretch(1)
            root.addLayout(top)
            self._apply_logo()

            stage = QHBoxLayout()
            stage.setSpacing(24)
            stage.addStretch(1)

            rail = QFrame(self)
            rail.setObjectName("qt-native-idle-rail")
            rail.setStyleSheet(
                "QFrame#qt-native-idle-rail{background:transparent;border:none;}"
                "QLabel{color:#eef4ff;}"
            )
            rail.setMaximumWidth(580)
            rail.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
            rail_box = QVBoxLayout(rail)
            rail_box.setContentsMargins(28, 24, 28, 24)
            rail_box.setSpacing(18)

            hero_row = QHBoxLayout()
            hero_row.setSpacing(22)

            time_col = QVBoxLayout()
            time_col.setContentsMargins(0, 0, 0, 0)
            time_col.setSpacing(4)
            self._time_label = QLabel(rail)
            self._time_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self._time_label.setStyleSheet("font-size:86px;font-weight:760;letter-spacing:-2px;color:rgba(240,247,255,0.98);")
            self._time_label.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Fixed)
            self._date_label = QLabel(rail)
            self._date_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self._date_label.setStyleSheet("font-size:28px;color:rgba(230,239,251,0.82);")
            self._date_label.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Fixed)
            time_col.addWidget(self._time_label)
            time_col.addWidget(self._date_label)
            time_col.addStretch(1)

            divider = QFrame(rail)
            divider.setFixedWidth(1)
            divider.setStyleSheet("background:rgba(220,234,255,0.24);border:none;")

            self._weather_frame = QFrame(rail)
            self._weather_frame.setObjectName("qt-native-idle-weather")
            self._weather_frame.setMinimumWidth(240)
            self._weather_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            self._weather_frame.setStyleSheet(
                "QFrame#qt-native-idle-weather{background:transparent;border:none;}"
            )
            weather_box = QVBoxLayout(self._weather_frame)
            weather_box.setContentsMargins(0, 0, 0, 0)
            weather_box.setSpacing(6)
            self._weather_location = QLabel("", self._weather_frame)
            self._weather_location.setWordWrap(True)
            self._weather_location.setStyleSheet("font-size:13px;color:rgba(197,214,235,0.76);")
            self._weather_temp = QLabel("--", self._weather_frame)
            self._weather_temp.setStyleSheet("font-size:58px;font-weight:780;letter-spacing:-2px;color:rgba(240,247,255,0.98);")
            self._weather_summary = QLabel("Weather unavailable", self._weather_frame)
            self._weather_summary.setWordWrap(True)
            self._weather_summary.setStyleSheet("font-size:18px;color:rgba(230,241,255,0.82);")
            self._weather_meta = QLabel("", self._weather_frame)
            self._weather_meta.setWordWrap(True)
            self._weather_meta.setStyleSheet("font-size:13px;color:rgba(220,233,255,0.88);")
            weather_box.addWidget(self._weather_location)
            weather_box.addSpacing(4)
            weather_box.addWidget(self._weather_temp)
            weather_box.addWidget(self._weather_summary)
            weather_box.addWidget(self._weather_meta)

            hero_row.addLayout(time_col, 1)
            hero_row.addWidget(divider)
            hero_row.addWidget(self._weather_frame, 1, Qt.AlignTop)
            hero_row.setAlignment(Qt.AlignTop)
            rail_box.addLayout(hero_row)

            self._forecast_frame = QFrame(rail)
            self._forecast_frame.setObjectName("qt-native-idle-forecast")
            self._forecast_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            self._forecast_frame.setStyleSheet(
                "QFrame#qt-native-idle-forecast{background:rgba(255,255,255,10);"
                "border:1px solid rgba(205,226,255,28);border-radius:20px;}"
            )
            forecast_box = QHBoxLayout(self._forecast_frame)
            forecast_box.setContentsMargins(14, 12, 14, 12)
            forecast_box.setSpacing(10)
            self._forecast_box = forecast_box
            rail_box.addWidget(self._forecast_frame)
            stage.addWidget(rail, 0, Qt.AlignRight | Qt.AlignTop)
            root.addLayout(stage)
            root.addStretch(1)

            footer = QHBoxLayout()
            footer.setSpacing(18)
            self._device_label = QLabel(self)
            self._device_label.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
            self._device_label.setStyleSheet("font-size:28px;font-weight:700;color:rgba(237,245,255,0.98);")
            self._device_label.setText(self._device_name)
            self._device_label.setVisible(bool(self._device_name))
            footer.addWidget(self._device_label, 1, Qt.AlignLeft | Qt.AlignBottom)

            self._qr_wrap = QFrame(self)
            self._qr_wrap.setObjectName("qt-native-idle-qr-wrap")
            self._qr_wrap.setStyleSheet(
                "QFrame#qt-native-idle-qr-wrap{background:rgba(8,15,27,214);"
                "border:1px solid rgba(205,226,255,38);border-radius:16px;}"
            )
            qr_box = QVBoxLayout(self._qr_wrap)
            qr_box.setContentsMargins(10, 10, 10, 8)
            qr_box.setSpacing(8)
            self._qr_label = QLabel(self._qr_wrap)
            self._qr_label.setAlignment(Qt.AlignCenter)
            self._qr_label.setStyleSheet("background:rgba(255,255,255,8); border:1px solid rgba(130,170,220,64); border-radius:14px;")
            self._qr_caption = QLabel(self._qr_wrap)
            self._qr_caption.setAlignment(Qt.AlignCenter)
            self._qr_caption.setWordWrap(True)
            self._qr_caption.setStyleSheet("font-size:11px;color:rgba(214,229,255,0.78);")
            qr_box.addWidget(self._qr_label, 0, Qt.AlignCenter)
            qr_box.addWidget(self._qr_caption, 0, Qt.AlignCenter)
            footer.addWidget(self._qr_wrap, 0, Qt.AlignRight | Qt.AlignBottom)
            root.addLayout(footer)

            self._clock = QTimer(self)
            self._clock.setInterval(1000)
            self._clock.timeout.connect(self._update_clock)
            self._clock.start()
            self._qr_refresh = QTimer(self)
            self._qr_refresh.setInterval(30000)
            self._qr_refresh.timeout.connect(self._refresh_qr)
            self._qr_refresh.start()
            self._weather_refresh = QTimer(self)
            self._weather_refresh.setInterval(180000)
            self._weather_refresh.timeout.connect(lambda: self._refresh_weather(force=True))
            self._weather_refresh.start()
            self._update_clock()
            self._apply_qr_visibility(False)
            self._apply_weather(None, None)
            QTimer.singleShot(0, self._refresh_qr)
            self.hide()

        def _apply_logo(self) -> None:
            pix = QPixmap(self._logo_path)
            if not pix.isNull():
                self._logo_label.setPixmap(pix.scaled(220, 84, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self._logo_label.setText("")
                return
            self._logo_label.setPixmap(QPixmap())
            self._logo_label.setText("RelayTV")

        def _apply_qr_visibility(self, visible: bool) -> None:
            self._qr_wrap.setVisible(bool(visible))
            self._qr_label.setVisible(bool(visible))
            self._qr_caption.setVisible(bool(visible))

        def _api_url(self, path: str) -> str:
            return urljoin(self._overlay_url or "http://127.0.0.1:8787/x11/overlay", path)

        def _request_json(self, path: str, callback) -> None:
            self._request_json_url(self._api_url(path), callback)

        def _request_json_url(self, url: str, callback) -> None:
            reply = self._net.get(QNetworkRequest(QUrl(str(url or ""))))
            self._replies.add(reply)
            reply.finished.connect(lambda r=reply, cb=callback: self._finish_json_request(r, cb))

        def _finish_json_request(self, reply, callback) -> None:
            try:
                if reply.error():
                    callback(None)
                    return
                raw = bytes(reply.readAll())
                callback(json.loads(raw.decode("utf-8", errors="replace")))
            except Exception:
                callback(None)
            finally:
                try:
                    self._replies.discard(reply)
                except Exception:
                    pass
                try:
                    reply.deleteLater()
                except Exception:
                    pass

        def _refresh_qr(self) -> None:
            self._request_json("/settings", self._apply_settings)
            local_url = _derive_native_idle_public_ui_url(self._overlay_url)
            if local_url:
                self._apply_host_urls({"public_urls": [local_url]})
            else:
                self._request_json("/x11/host_urls", self._apply_host_urls)

        def _apply_settings(self, payload: object) -> None:
            self._qr_enabled = _native_idle_qr_enabled(payload)
            if isinstance(payload, dict):
                self._last_settings_payload = dict(payload)
                self._qr_size = _native_idle_qr_size(payload.get("idle_qr_size"), self._qr_size)
                device_name = str(payload.get("device_name") or "").strip() or self._device_name
                self._device_name = device_name
                self._device_label.setText(device_name)
                self._device_label.setVisible(bool(device_name))
                self._refresh_weather(payload)
                _eprint(f"qt-shell native-idle device-name={device_name or '-'}")
            else:
                self._qr_size = _native_idle_qr_size(None, self._qr_size)
                self._device_label.setText(self._device_name)
                self._device_label.setVisible(bool(self._device_name))
                _eprint(f"qt-shell native-idle device-name={self._device_name or '-'}")
            _eprint(f"qt-shell native-idle qr settings enabled={self._qr_enabled} size={self._qr_size}")
            try:
                self.layout().activate()
                self.updateGeometry()
                self.update()
            except Exception:
                pass
            self._refresh_qr_image()

        def _apply_host_urls(self, payload: object) -> None:
            self._qr_target_url = _pick_public_idle_qr_url(payload)
            _eprint(f"qt-shell native-idle qr target={self._qr_target_url or '-'}")
            if (not self._qr_enabled) or (not self._qr_target_url):
                self._apply_qr_visibility(False)
                return
            pix = self._render_qr_pixmap(self._qr_target_url, self._qr_size)
            if pix is None or pix.isNull():
                _eprint("qt-shell native-idle qr render failed")
                self._apply_qr_visibility(False)
                return
            self._qr_label.setPixmap(pix)
            self._qr_label.setFixedSize(self._qr_size, self._qr_size)
            self._qr_caption.setText(str(self._qr_target_url or "").replace("http://", "").replace("https://", ""))
            self._apply_qr_visibility(True)
            try:
                self.layout().activate()
                self.updateGeometry()
                self.update()
            except Exception:
                pass
            _eprint(f"qt-shell native-idle qr visible size={self._qr_size}")

        def _refresh_qr_image(self) -> None:
            self._apply_host_urls({"public_urls": [self._qr_target_url]} if self._qr_target_url else {})

        def _refresh_weather(self, settings_payload: object | None = None, *, force: bool = False) -> None:
            payload = settings_payload if isinstance(settings_payload, dict) else self._last_settings_payload
            if not isinstance(payload, dict):
                self._apply_weather(None, None)
                return
            idle_panels = payload.get("idle_panels") if isinstance(payload, dict) else {}
            weather_panel = idle_panels.get("weather") if isinstance(idle_panels, dict) else {}
            if not (isinstance(weather_panel, dict) and weather_panel.get("enabled")):
                self._apply_weather(None, payload)
                return
            sig = _native_idle_weather_signature(payload)
            if (not force) and sig == self._weather_signature and self._weather_frame.isVisible():
                return
            self._weather_signature = sig
            self._request_json("/idle/weather", lambda data, p=dict(payload): self._apply_weather(data, p))

        def _apply_weather(self, payload: object, settings_payload: object | None) -> None:
            settings = settings_payload if isinstance(settings_payload, dict) else {}
            idle_panels = settings.get("idle_panels") if isinstance(settings, dict) else {}
            weather_panel = idle_panels.get("weather") if isinstance(idle_panels, dict) else {}
            enabled = bool(isinstance(weather_panel, dict) and weather_panel.get("enabled"))
            layout_mode = _native_idle_weather_layout(settings)
            show_forecast = layout_mode != "minimal"
            if not enabled:
                self._weather_frame.setVisible(False)
                self._forecast_frame.setVisible(False)
                return
            weather = settings.get("weather") if isinstance(settings, dict) else {}
            weather = weather if isinstance(weather, dict) else {}
            if (not isinstance(payload, dict)) or (not isinstance(payload.get("current"), dict)):
                location = str(weather.get("location_name") or "").strip() or "Local forecast"
                self._weather_location.setText(location)
                self._weather_temp.setText("--")
                self._weather_summary.setText("Live forecast unavailable")
                self._weather_meta.setText("")
                self._weather_location.setVisible(layout_mode != "minimal")
                self._weather_meta.setVisible(layout_mode != "minimal")
                self._weather_frame.setVisible(True)
                self._forecast_frame.setVisible(False)
                return
            current = payload.get("current") if isinstance(payload, dict) else {}
            current = current if isinstance(current, dict) else {}
            daily = payload.get("daily") if isinstance(payload, dict) else {}
            daily = daily if isinstance(daily, dict) else {}
            units = str(weather.get("units") or "imperial").strip().lower()
            unit_sym = "°C" if units == "metric" else "°F"
            wind_unit = "km/h" if units == "metric" else "mph"
            temp = _optional_float(current.get("temperature_2m"))
            feels = _optional_float(current.get("apparent_temperature"))
            wind = _optional_float(current.get("wind_speed_10m"))
            code = current.get("weather_code")
            location = str(weather.get("location_name") or "").strip() or "Local forecast"
            self._weather_location.setText(location)
            self._weather_location.setVisible(layout_mode != "minimal")
            self._weather_temp.setText(f"{int(round(temp))}{unit_sym}" if temp is not None else "--")
            self._weather_summary.setText(_native_idle_weather_code_label(code))
            meta_parts: list[str] = []
            if feels is not None:
                meta_parts.append(f"Feels {int(round(feels))}{unit_sym}")
            if wind is not None:
                meta_parts.append(f"Wind {int(round(wind))} {wind_unit}")
            self._weather_meta.setText("  •  ".join(meta_parts))
            self._weather_meta.setVisible(layout_mode != "minimal")
            self._weather_frame.setVisible(True)
            if show_forecast:
                self._apply_weather_forecast(daily, units, unit_sym, wind_unit)
            else:
                self._forecast_frame.setVisible(False)
            try:
                self.layout().activate()
                self.updateGeometry()
                self.update()
            except Exception:
                pass

        def _apply_weather_forecast(self, daily: dict[str, object], units: str, unit_sym: str, wind_unit: str) -> None:
            while self._forecast_box.count():
                item = self._forecast_box.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            times = daily.get("time") if isinstance(daily, dict) else None
            times = times if isinstance(times, list) else []
            mins = daily.get("temperature_2m_min") if isinstance(daily, dict) else None
            mins = mins if isinstance(mins, list) else []
            maxs = daily.get("temperature_2m_max") if isinstance(daily, dict) else None
            maxs = maxs if isinstance(maxs, list) else []
            codes = daily.get("weather_code") if isinstance(daily, dict) else None
            codes = codes if isinstance(codes, list) else []
            rains = daily.get("precipitation_probability_max") if isinstance(daily, dict) else None
            rains = rains if isinstance(rains, list) else []
            winds = daily.get("wind_speed_10m_max") if isinstance(daily, dict) else None
            winds = winds if isinstance(winds, list) else []

            count = min(len(times), len(mins), len(maxs), len(codes), 4)
            if count <= 0:
                self._forecast_frame.setVisible(False)
                return
            for idx in range(count):
                col = QFrame(self._forecast_frame)
                col.setStyleSheet("background:transparent;border:none;")
                col_box = QVBoxLayout(col)
                col_box.setContentsMargins(0, 0, 0, 0)
                col_box.setSpacing(3)
                dow = QLabel(col)
                try:
                    parsed = datetime.fromisoformat(str(times[idx]))
                    dow_text = parsed.strftime("%a")
                except Exception:
                    dow_text = "--"
                dow.setText(dow_text)
                dow.setStyleSheet("font-size:13px;font-weight:700;color:rgba(238,245,255,0.92);")
                summary = QLabel(_native_idle_weather_code_label(codes[idx]), col)
                summary.setWordWrap(True)
                summary.setStyleSheet("font-size:12px;color:rgba(226,237,251,0.74);")
                hi = _optional_float(maxs[idx])
                lo = _optional_float(mins[idx])
                rain = _optional_float(rains[idx]) if idx < len(rains) else None
                wind = _optional_float(winds[idx]) if idx < len(winds) else None
                range_label = QLabel(
                    f"{int(round(hi)) if hi is not None else '--'}{unit_sym}/"
                    f"{int(round(lo)) if lo is not None else '--'}{unit_sym}",
                    col,
                )
                range_label.setStyleSheet("font-size:14px;font-weight:700;color:rgba(243,248,255,0.98);")
                details = []
                if rain is not None:
                    details.append(f"Rain {int(round(rain))}%")
                if wind is not None:
                    details.append(f"Wind {int(round(wind))} {wind_unit}")
                meta = QLabel("  •  ".join(details), col)
                meta.setWordWrap(True)
                meta.setStyleSheet("font-size:11px;color:rgba(214,229,255,0.68);")
                col_box.addWidget(dow)
                col_box.addWidget(summary)
                col_box.addWidget(range_label)
                col_box.addWidget(meta)
                self._forecast_box.addWidget(col, 1)
            self._forecast_frame.setVisible(True)

        def _render_qr_pixmap(self, text: str, size: int) -> QPixmap | None:
            payload = str(text or "").strip()
            if (not payload) or qrcode is None:
                return None
            try:
                qr = qrcode.QRCode(
                    version=None,
                    error_correction=qrcode.constants.ERROR_CORRECT_M,
                    box_size=1,
                    border=2,
                )
                qr.add_data(payload)
                qr.make(fit=True)
                matrix = qr.get_matrix()
            except Exception:
                return None
            if not matrix:
                return None
            cells = len(matrix)
            if cells <= 0:
                return None
            pix = QPixmap(size, size)
            pix.fill(Qt.white)
            painter = QPainter(pix)
            try:
                painter.setRenderHint(QPainter.Antialiasing, False)
            except Exception:
                pass
            cell_px = max(1, size // cells)
            draw_size = cell_px * cells
            offset = max(0, (size - draw_size) // 2)
            for row_idx, row in enumerate(matrix):
                y = offset + (row_idx * cell_px)
                for col_idx, cell in enumerate(row):
                    if not cell:
                        continue
                    x = offset + (col_idx * cell_px)
                    painter.fillRect(x, y, cell_px, cell_px, Qt.black)
            painter.end()
            return pix

        def _update_clock(self) -> None:
            now = time.localtime()
            self._time_label.setText(time.strftime("%I:%M %p", now).lstrip("0"))
            self._date_label.setText(time.strftime("%A, %B %d", now))

        def set_idle_active(self, active: bool) -> None:
            if active:
                self._update_clock()
                self._request_json("/settings", self._apply_settings)
                self._refresh_weather(force=True)
                self.show()
                self.raise_()
                try:
                    self.activateWindow()
                except Exception:
                    pass
            else:
                self.hide()

    use_libmpv = _libmpv_enabled() and (not headless_qpa)
    libmpv_player: _QtLibMpvPlayer | None = None

    class _LibMpvVideoWidget(QOpenGLWidget):
        def __init__(self, parent, player: _QtLibMpvPlayer):
            super().__init__(parent)
            self._player = player
            self.setObjectName("video-surface")
            self.setStyleSheet("background: #000;")
            self._render_error_logged = False
            self._render_timer = QTimer(self)
            self._render_timer.setInterval(max(10, int(float(os.getenv("RELAYTV_QT_LIBMPV_FRAME_MS", "16")))))
            self._render_timer.timeout.connect(self.update)
            self._render_timer.start()

        def _get_proc_address(self, name_ptr) -> int:
            try:
                if not name_ptr:
                    return 0
                name_bytes = b""
                if isinstance(name_ptr, (bytes, bytearray)):
                    name_bytes = bytes(name_ptr)
                elif isinstance(name_ptr, str):
                    name_bytes = name_ptr.encode("utf-8", errors="ignore")
                elif isinstance(name_ptr, int):
                    name_bytes = ctypes.cast(ctypes.c_void_p(name_ptr), ctypes.c_char_p).value or b""
                else:
                    name_bytes = ctypes.cast(name_ptr, ctypes.c_char_p).value or b""
                if not name_bytes:
                    return 0
                ctx = QOpenGLContext.currentContext()
                if ctx is None:
                    return 0
                addr = ctx.getProcAddress(name_bytes)
                if not addr:
                    # Some Qt builds expose an overload that takes str.
                    addr = ctx.getProcAddress(name_bytes.decode("utf-8", errors="ignore"))
                if addr is None:
                    return 0
                return int(addr)
            except Exception:
                return 0

        def initializeGL(self) -> None:  # noqa: N802
            try:
                self._player.init_render_context(self._get_proc_address)
            except Exception as exc:
                _eprint(f"qt-shell libmpv initializeGL failed: {exc}")

        def paintGL(self) -> None:  # noqa: N802
            try:
                # Always clear to opaque black first so idle/transition frames
                # never reveal the compositor/desktop through this surface.
                ctx = QOpenGLContext.currentContext()
                if ctx is not None:
                    f = ctx.functions()
                    f.glViewport(0, 0, int(self.width()), int(self.height()))
                    f.glClearColor(0.0, 0.0, 0.0, 1.0)
                    f.glClear(0x00004000)  # GL_COLOR_BUFFER_BIT
                self._player.render(
                    fbo=int(self.defaultFramebufferObject()),
                    width=int(self.width()),
                    height=int(self.height()),
                )
            except Exception as exc:
                if not self._render_error_logged:
                    _eprint(f"qt-shell libmpv paintGL failed: {exc}")
                    self._render_error_logged = True

        def closeEvent(self, event) -> None:  # noqa: N802
            try:
                self._render_timer.stop()
            except Exception:
                pass
            super().closeEvent(event)

    stream = (args.stream or "").strip()
    ipc_path = (args.ipc_path or "").strip()
    runtime_status_file = _qt_runtime_status_file()
    runtime_control_file = _qt_runtime_control_file()
    if ipc_path:
        try:
            if os.path.exists(ipc_path):
                os.remove(ipc_path)
        except Exception:
            pass
    if runtime_status_file:
        try:
            if os.path.exists(runtime_status_file):
                os.remove(runtime_status_file)
        except Exception:
            pass
    if runtime_control_file:
        try:
            if os.path.exists(runtime_control_file):
                os.remove(runtime_control_file)
        except Exception:
            pass

    if use_libmpv:
        try:
            libmpv_player = _QtLibMpvPlayer(debug=debug)
            libmpv_player.start(
                stream=stream,
                audio=((args.audio or "").strip() or None),
                ipc_path=(ipc_path or None),
                audio_device=((args.audio_device or "").strip() or None),
                sub_lang=((args.sub_lang or "").strip() or None),
                volume=args.volume,
                ytdl_enabled=((args.ytdl_enabled or "1").strip().lower() in ("1", "true", "yes", "on")),
                ytdl_path=((args.ytdl_path or "").strip() or None),
                ytdl_format=((args.ytdl_format or "").strip() or None),
                ytdl_raw_options=((args.ytdl_raw_options or "").strip() or None),
            )
            if debug:
                _eprint("qt-shell libmpv engine active")
            video_widget = _LibMpvVideoWidget(win, libmpv_player)
        except Exception as exc:
            _eprint(f"qt-shell libmpv disabled, falling back to mpv subprocess: {exc}")
            use_libmpv = False
            libmpv_player = None
            video_widget = QWidget(win)
            video_widget.setObjectName("video-surface")
            video_widget.setStyleSheet("background: #000;")
            try:
                video_widget.setAttribute(Qt.WA_NativeWindow, True)
            except Exception:
                pass
    else:
        video_widget = QWidget(win)
        video_widget.setObjectName("video-surface")
        video_widget.setStyleSheet("background: #000;")
        # mpv --wid embedding on X11/XWayland needs a native child window ID.
        try:
            video_widget.setAttribute(Qt.WA_NativeWindow, True)
        except Exception:
            pass

    win.setCentralWidget(video_widget)

    host_session_type = (
        os.getenv("RELAYTV_HOST_SESSION_TYPE")
        or os.getenv("XDG_SESSION_TYPE")
        or ""
    ).strip().lower()
    # Runtime layering behavior should follow the active Qt platform plugin.
    # If delegate forces xcb on a Wayland host, keep X11 overlay semantics.
    if qpa_platform in ("xcb", "x11") or qpa_platform.startswith("xcb:"):
        is_wayland = False
    elif qpa_platform:
        is_wayland = "wayland" in qpa_platform
    else:
        is_wayland = host_session_type == "wayland"
    use_wayland_window_flags = _prefer_wayland_window_flags(qpa_platform, host_session_type)
    overlay_toplevel_mode = (os.getenv("RELAYTV_QT_OVERLAY_TOPLEVEL") or "auto").strip().lower()
    force_toplevel = overlay_toplevel_mode in ("1", "true", "yes", "on")
    force_child = overlay_toplevel_mode in ("0", "false", "no", "off")
    use_toplevel_overlay = force_toplevel or (not force_child and not is_wayland)
    arch = (platform.machine() or "").strip().lower()
    # NUC/amd64 Wayland stacks are more likely to stall libmpv repaint when a
    # transparent top-level overlay is present; keep child-overlay there.
    # On Raspberry Pi/ARM, top-level overlay has been the more reliable path.
    libmpv_force_child_arch = arch in ("x86_64", "amd64")
    if (
        use_libmpv
        and libmpv_force_child_arch
        and use_toplevel_overlay
        and (not _env_bool("RELAYTV_QT_LIBMPV_TOPLEVEL_OVERLAY", False))
    ):
        # In libmpv mode, a full-screen transparent top-level overlay can cause
        # compositors to throttle/occlude the underlying GL surface, resulting in
        # audio-only playback and black video. Keep overlay in-window by default.
        use_toplevel_overlay = False
        if debug:
            _eprint("qt-shell: libmpv forcing child overlay (set RELAYTV_QT_LIBMPV_TOPLEVEL_OVERLAY=1 to override)")

    overlay_win: QMainWindow | None = None
    overlay: QWebEngineView | None = None
    native_toast_host: _NativeToastLayer | None = None
    native_idle_host: _NativeIdleLayer | None = None
    native_toast_toplevel = False
    native_idle_toplevel = False
    overlay_parent_is_main_window = False
    overlay_raise_timer = None
    overlay_last_rect = None
    overlay_load_deferred: list[callable] = []
    if overlay_enabled:
        if debug:
            mode = "toplevel" if use_toplevel_overlay else "child"
            render_mode = "software" if overlay_software_mode else "default"
            _eprint(f"qt-shell overlay mode={mode} render={render_mode} session={host_session_type or 'unknown'} qpa={qpa_platform or 'default'}")

        if use_toplevel_overlay:
            # Keep overlay in a dedicated transparent top-level window.
            # On some X11/GPU stacks, native mpv rendering to the video widget can occlude
            # child widgets even when raised.
            overlay_win = QMainWindow()
            overlay_win.setWindowTitle("RelayTV Qt Overlay")
            # Wayland compositors can treat Qt.Tool as a transient that fails to
            # layer above fullscreen surfaces; prefer a regular top-level window.
            overlay_flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            if use_wayland_window_flags:
                overlay_flags |= Qt.Window
            else:
                overlay_flags |= Qt.Tool
            if not use_wayland_window_flags:
                overlay_flags |= Qt.X11BypassWindowManagerHint
            overlay_win.setWindowFlags(overlay_flags)
            overlay_win.setAttribute(Qt.WA_TranslucentBackground, True)
            if not use_wayland_window_flags:
                overlay_win.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            overlay = QWebEngineView(overlay_win)  # type: ignore[operator]
        else:
            # Wayland-safe mode: use a child overlay to avoid compositor-specific
            # black/opaque behavior on transparent top-level windows.
            parent_widget = win if is_wayland else video_widget
            overlay_parent_is_main_window = bool(parent_widget is win)
            overlay = QWebEngineView(parent_widget)  # type: ignore[operator]
            if overlay_parent_is_main_window:
                overlay.setGeometry(win.rect())
            else:
                overlay.setGeometry(video_widget.rect())
            try:
                overlay.setAttribute(Qt.WA_AlwaysStackOnTop, True)
            except Exception:
                pass
            try:
                # Native child surface improves stacking reliability when mpv is
                # rendering into a native video widget.
                overlay.setAttribute(Qt.WA_NativeWindow, True)
            except Exception:
                pass

        overlay.setAttribute(Qt.WA_TranslucentBackground, True)
        if not is_wayland:
            overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        overlay.setStyleSheet("background: transparent;")
        overlay.page().setBackgroundColor(Qt.transparent)
        # Prevent stale idle/overlay UI by disabling persistent cache/profile state.
        try:
            prof = overlay.page().profile()
            prof.setHttpCacheType(QWebEngineProfile.NoCache)  # type: ignore[union-attr]
            prof.setPersistentCookiesPolicy(QWebEngineProfile.NoPersistentCookies)  # type: ignore[union-attr]
        except Exception:
            pass
        try:
            settings = overlay.settings()
            settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)  # type: ignore[union-attr]
            settings.setAttribute(QWebEngineSettings.LocalStorageEnabled, False)  # type: ignore[union-attr]
            # Never render Chromium's default "site can't be reached" page over video.
            settings.setAttribute(QWebEngineSettings.ErrorPageEnabled, False)  # type: ignore[union-attr]
        except Exception:
            pass
        _overlay_failures = {"n": 0}
        _overlay_placeholder = {"active": False}
        _overlay_blank_html = (
            "<!doctype html><html><head><meta charset='utf-8'/>"
            "<style>html,body{margin:0;width:100%;height:100%;background:transparent;overflow:hidden;}</style>"
            "</head><body></body></html>"
        )

        def _load_overlay() -> None:
            _overlay_placeholder["active"] = False
            target = _with_cache_buster(args.overlay_url)
            if debug:
                _eprint(f"qt-shell overlay load: {target}")
            overlay.setUrl(QUrl(target))

        def _show_overlay_placeholder() -> None:
            _overlay_placeholder["active"] = True
            try:
                overlay.setHtml(_overlay_blank_html, QUrl("about:blank"))
            except Exception:
                pass

        def _on_overlay_load_finished(ok: bool) -> None:
            if _overlay_placeholder["active"]:
                # Ignore successful load signals from our temporary blank page.
                return
            if debug:
                try:
                    _eprint(f"qt-shell overlay loadFinished ok={ok} url={overlay.url().toString()}")
                except Exception:
                    _eprint(f"qt-shell overlay loadFinished ok={ok}")
            # Startup race guard: if RelayTV HTTP server is not ready yet, retry
            # loading the overlay page with bounded exponential backoff.
            if ok:
                _overlay_failures["n"] = 0
                return
            _show_overlay_placeholder()
            _overlay_failures["n"] = min(8, int(_overlay_failures["n"]) + 1)
            delay_ms = min(5000, 250 * (2 ** _overlay_failures["n"]))
            if debug:
                _eprint(f"qt-shell overlay retry in {delay_ms}ms")
            QTimer.singleShot(delay_ms, _load_overlay)

        overlay.loadFinished.connect(_on_overlay_load_finished)
        if debug:
            overlay.loadStarted.connect(lambda: _eprint("qt-shell overlay loadStarted"))
        overlay_load_deferred.append(_load_overlay)
        if overlay_win is not None:
            overlay_win.setCentralWidget(overlay)

    if native_toasts_enabled:
        native_toast_toplevel = overlay_win is None and _native_overlay_toasts_use_toplevel(use_libmpv=use_libmpv)
        toast_parent = None if native_toast_toplevel else (overlay_win if overlay_win is not None else (win if overlay_parent_is_main_window else video_widget))
        native_toast_host = _NativeToastLayer(toast_parent, overlay_url=args.overlay_url)
        if native_toast_toplevel:
            toast_flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            if use_wayland_window_flags:
                toast_flags |= Qt.Window
            else:
                toast_flags |= Qt.Tool | Qt.X11BypassWindowManagerHint
            native_toast_host.setWindowFlags(toast_flags)
            native_toast_host.setAttribute(Qt.WA_TranslucentBackground, True)
            if not use_wayland_window_flags:
                native_toast_host.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        native_toast_host.hide()

    if native_idle_enabled:
        native_idle_toplevel = _native_idle_overlay_use_toplevel(use_libmpv=use_libmpv)
        idle_parent = None if native_idle_toplevel else win
        native_idle_host = _NativeIdleLayer(idle_parent, overlay_url=args.overlay_url)
        if native_idle_toplevel:
            idle_flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            if use_wayland_window_flags:
                idle_flags |= Qt.Window
            else:
                idle_flags |= Qt.Tool | Qt.X11BypassWindowManagerHint
            native_idle_host.setWindowFlags(idle_flags)
            native_idle_host.setAttribute(Qt.WA_TranslucentBackground, True)
            if not use_wayland_window_flags:
                native_idle_host.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        native_idle_host.hide()

    def _layout_overlay() -> None:
        nonlocal overlay_last_rect
        # Keep overlay/toast layers exactly aligned with the visible player bounds.
        if overlay is None and native_toast_host is None and native_idle_host is None:
            return
        if overlay_win is not None:
            rect = win.frameGeometry()
            if overlay_last_rect != rect:
                overlay_win.setGeometry(rect)
                overlay_last_rect = rect
            if native_toast_host is not None:
                native_toast_host.setGeometry(overlay_win.rect())
                native_toast_host.raise_()
            return
        target_rect = win.rect() if overlay_parent_is_main_window else video_widget.rect()
        if overlay is not None:
            # Child-overlay mode: fit the video surface and keep highest z-order.
            overlay.setGeometry(target_rect)
            overlay.raise_()
        if native_toast_host is not None:
            if native_toast_toplevel:
                native_toast_host.setGeometry(win.frameGeometry())
            else:
                native_toast_host.setGeometry(target_rect)
            native_toast_host.raise_()
        if native_idle_host is not None:
            if native_idle_toplevel:
                native_idle_host.setGeometry(win.frameGeometry())
            else:
                native_idle_host.setGeometry(win.rect())
            if native_idle_host.isVisible():
                native_idle_host.raise_()

    def _playback_surface_active() -> bool:
        if libmpv_player is not None:
            try:
                snap = libmpv_player.runtime_snapshot()
                if not isinstance(snap, dict):
                    return False
                if snap.get("mpv_runtime_playback_active") is True:
                    return True
                # YouTube and other network-backed starts can briefly flap
                # playback_active false while a stream is already loaded.
                # Keep the playback surface visible until the loaded path is
                # fully cleared, otherwise the UI visibly falls back to idle
                # and then re-enters playback a moment later.
                path = str(snap.get("mpv_runtime_path") or "").strip()
                if path and snap.get("mpv_runtime_eof_reached") is not True:
                    if (
                        snap.get("mpv_runtime_stream_loaded") is True
                        or snap.get("mpv_runtime_playback_started") is True
                    ):
                        return True
                return False
            except Exception:
                return False
        try:
            return bool(mpv_proc and mpv_proc.poll() is None)
        except Exception:
            return False

    def _sync_idle_visibility() -> None:
        if native_idle_host is None:
            return
        idle_active = not _playback_surface_active()
        if idle_active and native_toast_host is not None:
            native_toast_host.clear_all()
        native_idle_host.set_idle_active(idle_active)

    def _nudge_overlay_stack() -> None:
        try:
            if overlay_win is not None:
                overlay_win.raise_()
                if native_toast_host is not None:
                    native_toast_host.raise_()
                return
            if overlay is not None:
                overlay.raise_()
            if native_toast_host is not None:
                native_toast_host.raise_()
            if native_idle_host is not None and native_idle_host.isVisible():
                native_idle_host.raise_()
        except Exception:
            pass

    cursor_autohide_enabled = (not headless_qpa) and _cursor_autohide_enabled()
    cursor_autohide_timeout_ms = _cursor_autohide_timeout_ms()
    cursor_hidden = False
    cursor_hide_timer = None
    cursor_force_hide_timer = None
    cursor_force_hide_remaining = 0
    cursor_widgets: list[QWidget] = []
    cursor_widget_ids: set[int] = set()
    _cursor_debug(
        "startup "
        f"enabled={cursor_autohide_enabled} timeout_ms={cursor_autohide_timeout_ms} "
        f"headless={headless_qpa} qpa={qpa_platform or 'default'}"
    )

    def _set_mouse_tracking(widget: QWidget | None) -> None:
        if widget is None:
            return
        try:
            widget.setMouseTracking(True)
        except Exception:
            pass

    def _install_cursor_filter(widget: QWidget | None, filt: QObject | None) -> None:
        if widget is None or filt is None:
            return
        try:
            widget.installEventFilter(filt)
        except Exception:
            pass

    def _register_cursor_widget(widget: QWidget | None) -> None:
        if widget is None:
            return
        try:
            wid = id(widget)
            if wid in cursor_widget_ids:
                return
            cursor_widget_ids.add(wid)
            cursor_widgets.append(widget)
            _cursor_debug(f"register-widget class={widget.__class__.__name__} name={widget.objectName() or '-'}")
        except Exception:
            pass

    def _register_cursor_widget_tree(widget: QWidget | None) -> None:
        if widget is None:
            return
        _register_cursor_widget(widget)
        try:
            for child in widget.findChildren(QWidget):
                _register_cursor_widget(child)
        except Exception:
            pass

    def _live_cursor_windows() -> list[tuple[object, str]]:
        out: list[tuple[object, str]] = []
        seen: set[int] = set()
        for widget in list(cursor_widgets):
            try:
                handle = widget.windowHandle()
            except Exception:
                handle = None
            if handle is None:
                continue
            try:
                hid = id(handle)
            except Exception:
                continue
            if hid in seen:
                continue
            seen.add(hid)
            label = f"{widget.__class__.__name__}:{widget.objectName() or '-'}"
            out.append((handle, label))
        try:
            for handle in QGuiApplication.topLevelWindows():
                try:
                    hid = id(handle)
                except Exception:
                    continue
                if hid in seen:
                    continue
                seen.add(hid)
                out.append((handle, "top-level"))
            _cursor_debug(f"live-window-scan count={len(out)}")
        except Exception as exc:
            _cursor_debug(f"live-window-scan-failed err={exc!r}")
        return out

    def _show_cursor(reason: str = "activity") -> None:
        nonlocal cursor_hidden, cursor_force_hide_remaining
        if not cursor_autohide_enabled:
            return
        try:
            if cursor_force_hide_timer is not None:
                cursor_force_hide_timer.stop()
            cursor_force_hide_remaining = 0
        except Exception:
            pass
        if cursor_hidden:
            try:
                app.restoreOverrideCursor()
            except Exception:
                pass
            for widget in cursor_widgets:
                try:
                    widget.unsetCursor()
                    _cursor_debug(
                        "unset-cursor "
                        f"reason={reason} class={widget.__class__.__name__} name={widget.objectName() or '-'}"
                    )
                except Exception:
                    _cursor_debug(
                        "unset-cursor-failed "
                        f"reason={reason} class={widget.__class__.__name__} "
                        f"name={widget.objectName() or '-'}"
                    )
            for window, owner in _live_cursor_windows():
                try:
                    window.unsetCursor()
                    _cursor_debug(
                        f"unset-window-cursor reason={reason} class={window.__class__.__name__} "
                        f"owner={owner}"
                    )
                except Exception as exc:
                    _cursor_debug(
                        f"unset-window-cursor-failed reason={reason} class={window.__class__.__name__} "
                        f"owner={owner} err={exc!r}"
                    )
            cursor_hidden = False
            _cursor_debug(f"show reason={reason} widgets={len(cursor_widgets)}")
        try:
            if cursor_hide_timer is not None:
                cursor_hide_timer.start(cursor_autohide_timeout_ms)
                _cursor_debug(f"timer-start reason={reason} timeout_ms={cursor_autohide_timeout_ms}")
        except Exception:
            pass

    def _hide_cursor(reason: str = "timer") -> None:
        nonlocal cursor_hidden, cursor_force_hide_remaining
        if not cursor_autohide_enabled:
            return
        try:
            app.setOverrideCursor(QCursor(Qt.BlankCursor))
            for widget in cursor_widgets:
                try:
                    widget.setCursor(QCursor(Qt.BlankCursor))
                    _cursor_debug(
                        "set-blank-cursor "
                        f"reason={reason} class={widget.__class__.__name__} name={widget.objectName() or '-'}"
                    )
                except Exception:
                    _cursor_debug(
                        "set-blank-cursor-failed "
                        f"reason={reason} class={widget.__class__.__name__} "
                        f"name={widget.objectName() or '-'}"
                    )
            for window, owner in _live_cursor_windows():
                try:
                    window.setCursor(QCursor(Qt.BlankCursor))
                    _cursor_debug(
                        f"set-blank-window-cursor reason={reason} class={window.__class__.__name__} "
                        f"owner={owner}"
                    )
                except Exception as exc:
                    _cursor_debug(
                        f"set-blank-window-cursor-failed reason={reason} class={window.__class__.__name__} "
                        f"owner={owner} err={exc!r}"
                    )
            if not cursor_hidden:
                _cursor_debug(f"hide reason={reason} widgets={len(cursor_widgets)}")
            else:
                _cursor_debug(f"rehide reason={reason} widgets={len(cursor_widgets)}")
            cursor_hidden = True
            try:
                if is_wayland and cursor_force_hide_timer is not None and (reason != "refresh"):
                    cursor_force_hide_remaining = 8
                    cursor_force_hide_timer.start()
                    _cursor_debug(
                        f"force-hide-timer-start interval_ms=750 remaining={cursor_force_hide_remaining}"
                    )
            except Exception:
                pass
        except Exception:
            _cursor_debug(f"hide-failed reason={reason}")

    def _force_hide_refresh() -> None:
        nonlocal cursor_force_hide_remaining
        if cursor_force_hide_remaining <= 0:
            try:
                if cursor_force_hide_timer is not None:
                    cursor_force_hide_timer.stop()
            except Exception:
                pass
            _cursor_debug("force-hide-timer-stop")
            return
        cursor_force_hide_remaining -= 1
        _hide_cursor(reason="refresh")
        if cursor_force_hide_remaining <= 0:
            try:
                if cursor_force_hide_timer is not None:
                    cursor_force_hide_timer.stop()
            except Exception:
                pass
            _cursor_debug("force-hide-timer-stop")

    class _ResizeFilter(QObject):
        def eventFilter(self, _obj, event):  # noqa: N802 (Qt naming)
            if event.type() == QEvent.Resize:
                _layout_overlay()
            return False

    class _CursorActivityFilter(QObject):
        def eventFilter(self, _obj, event):  # noqa: N802 (Qt naming)
            etype = event.type()
            if etype in (
                QEvent.MouseMove,
                QEvent.MouseButtonPress,
                QEvent.MouseButtonRelease,
                QEvent.Wheel,
            ):
                try:
                    if hasattr(event, "spontaneous") and (not bool(event.spontaneous())):
                        return False
                except Exception:
                    pass
                _show_cursor(reason=f"event:{int(etype)}")
            return False

    resize_filter = _ResizeFilter()
    video_widget.installEventFilter(resize_filter)
    win.installEventFilter(resize_filter)
    _register_cursor_widget_tree(win)
    _register_cursor_widget_tree(video_widget)
    _set_mouse_tracking(win)
    _set_mouse_tracking(video_widget)
    if overlay_win is not None:
        _register_cursor_widget_tree(overlay_win)
        _set_mouse_tracking(overlay_win)
    if native_idle_host is not None:
        _register_cursor_widget_tree(native_idle_host)
        _set_mouse_tracking(native_idle_host)
    if native_toast_host is not None:
        _register_cursor_widget_tree(native_toast_host)
        _set_mouse_tracking(native_toast_host)
    cursor_filter = None
    if cursor_autohide_enabled:
        cursor_hide_timer = QTimer()
        cursor_hide_timer.setSingleShot(True)
        cursor_hide_timer.timeout.connect(lambda: _hide_cursor(reason="timer"))
        cursor_force_hide_timer = QTimer()
        cursor_force_hide_timer.setInterval(750)
        cursor_force_hide_timer.timeout.connect(_force_hide_refresh)
        cursor_filter = _CursorActivityFilter()
        _install_cursor_filter(win, cursor_filter)
        _install_cursor_filter(video_widget, cursor_filter)
        _install_cursor_filter(overlay_win, cursor_filter)
        _install_cursor_filter(native_idle_host, cursor_filter)
        _install_cursor_filter(native_toast_host, cursor_filter)
        if overlay is not None:
            _register_cursor_widget_tree(overlay)
            _install_cursor_filter(overlay, cursor_filter)

    if headless_qpa:
        # Offscreen/minimal platforms do not represent a real window stack.
        # Keep a small realized widget tree without fullscreen window semantics.
        win.resize(1280, 720)
        win.show()
    else:
        win.showFullScreen()
        try:
            win.raise_()
            win.activateWindow()
        except Exception:
            pass
        if overlay_win is not None:
            try:
                if is_wayland and win.windowHandle() and overlay_win.windowHandle():
                    overlay_win.windowHandle().setTransientParent(win.windowHandle())
                    overlay_win.setScreen(win.windowHandle().screen())
            except Exception:
                pass
            overlay_win.showFullScreen()
            try:
                overlay_win.raise_()
                overlay_win.activateWindow()
            except Exception:
                pass
    if overlay is not None and overlay_win is None:
        overlay.show()
        overlay.raise_()
    for fn in overlay_load_deferred:
        QTimer.singleShot(0, fn)
    _layout_overlay()
    _sync_idle_visibility()
    if cursor_autohide_enabled:
        QTimer.singleShot(0, lambda: _hide_cursor(reason="startup"))
    if overlay is not None and (not headless_qpa) and (not is_wayland):
        try:
            overlay_raise_timer = QTimer()
            overlay_raise_timer.setInterval(900)
            overlay_raise_timer.timeout.connect(_nudge_overlay_stack)
            overlay_raise_timer.start()
        except Exception:
            overlay_raise_timer = None
    try:
        # Ensure native widgets are realized/mapped before passing --wid to mpv.
        app.processEvents()
    except Exception:
        pass

    wid = int(video_widget.winId())
    mpv_proc: subprocess.Popen | None = None

    if use_libmpv and libmpv_player is not None and stream:
        initial_stream = stream
        initial_audio = (args.audio or None)
        initial_load_attempts = {"n": 0}

        def _load_initial_libmpv_stream() -> None:
            if libmpv_player is None:
                return
            initial_load_attempts["n"] += 1
            try:
                if not libmpv_player.render_context_ready():
                    if initial_load_attempts["n"] < 80:
                        QTimer.singleShot(50, _load_initial_libmpv_stream)
                        return
                    raise RuntimeError("libmpv render context not ready")
                libmpv_player.load_stream(initial_stream, initial_audio)
                _sync_idle_visibility()
                _nudge_overlay_stack()
            except Exception as exc:
                _eprint(f"qt-shell libmpv initial load failed: {exc}")

        QTimer.singleShot(0, _load_initial_libmpv_stream)

    def _subprocess_mpv_running() -> bool:
        try:
            return bool(mpv_proc and mpv_proc.poll() is None)
        except Exception:
            return False

    def _wait_for_subprocess_mpv_ipc(timeout_sec: float = 2.5) -> bool:
        if not ipc_path:
            return False
        deadline = time.time() + max(0.1, float(timeout_sec or 0.0))
        while time.time() < deadline:
            if os.path.exists(ipc_path):
                return True
            if mpv_proc is not None:
                try:
                    if mpv_proc.poll() is not None:
                        return False
                except Exception:
                    return False
            try:
                app.processEvents()
            except Exception:
                pass
            time.sleep(0.05)
        return os.path.exists(ipc_path)

    def _subprocess_mpv_ipc_request(command: list[object], *, timeout_sec: float = 2.0) -> dict[str, object]:
        if not ipc_path:
            raise RuntimeError("mpv_ipc_unavailable")
        if not _wait_for_subprocess_mpv_ipc(timeout_sec=max(timeout_sec, 0.5)):
            raise RuntimeError("mpv_ipc_unavailable")
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.settimeout(max(0.2, float(timeout_sec or 0.0)))
            client.connect(ipc_path)
            payload = json.dumps({"command": list(command or [])}, ensure_ascii=True, separators=(",", ":")).encode("utf-8") + b"\n"
            client.sendall(payload)
            data = b""
            while b"\n" not in data:
                chunk = client.recv(65536)
                if not chunk:
                    break
                data += chunk
            line = data.splitlines()[0] if data else b""
            if not line:
                raise RuntimeError("mpv_ipc_no_response")
            result = json.loads(line.decode("utf-8"))
            if not isinstance(result, dict):
                raise RuntimeError("mpv_ipc_invalid_response")
            return result
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _spawn_subprocess_mpv(stream_url: str, audio_url: str | None = None) -> None:
        nonlocal mpv_proc
        if not stream_url:
            raise RuntimeError("stream_empty")
        if ipc_path:
            try:
                if os.path.exists(ipc_path):
                    os.remove(ipc_path)
            except Exception:
                pass
        mpv_env = dict(os.environ)
        qpa_platform = (os.getenv("QT_QPA_PLATFORM") or "").strip().lower()
        if qpa_platform in ("xcb", "x11") or qpa_platform.startswith("xcb:"):
            mpv_env.pop("WAYLAND_DISPLAY", None)
        mpv_proc = subprocess.Popen(
            _build_mpv_args(
                stream_url,
                wid=wid,
                audio=(audio_url or None),
                ipc_path=(ipc_path or None),
                audio_device=((args.audio_device or "").strip() or None),
                sub_lang=((args.sub_lang or "").strip() or None),
                volume=args.volume,
                ytdl_enabled=((args.ytdl_enabled or "1").strip().lower() in ("1", "true", "yes", "on")),
                ytdl_path=((args.ytdl_path or "").strip() or None),
                ytdl_format=((args.ytdl_format or "").strip() or None),
                ytdl_raw_options=((args.ytdl_raw_options or "").strip() or None),
            ),
            env=mpv_env,
        )
        if debug:
            _eprint(f"qt-shell mpv spawn pid={mpv_proc.pid} wid={wid}")
        time.sleep(0.25)
        if mpv_proc.poll() is not None:
            raise RuntimeError(f"mpv exited early rc={mpv_proc.returncode}")

    if (not use_libmpv) and stream:
        try:
            _spawn_subprocess_mpv(stream, (args.audio or None))
        except Exception as e:
            _eprint("Failed to start embedded mpv:", e)
            return 3

    def _shutdown(*_a):
        nonlocal mpv_proc, libmpv_player
        try:
            if mpv_proc and mpv_proc.poll() is None:
                mpv_proc.terminate()
                mpv_proc.wait(timeout=2)
        except Exception:
            try:
                if mpv_proc and mpv_proc.poll() is None:
                    mpv_proc.kill()
            except Exception:
                pass
        try:
            if libmpv_player is not None:
                libmpv_player.terminate()
        except Exception:
            pass
        mpv_proc = None
        app.quit()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    control_state: dict[str, object] = {
        "last_ts": 0.0,
        "last_action": "",
        "last_request_id": "",
        "last_handled": None,
        "last_ok": None,
        "last_error": "",
    }

    def _handle_runtime_control() -> None:
        if not runtime_control_file:
            return
        try:
            with open(runtime_control_file, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except FileNotFoundError:
            return
        except Exception:
            return

        action_ts = _optional_float(payload.get("ts")) or 0.0
        if action_ts <= float(control_state.get("last_ts") or 0.0):
            return

        action = str(payload.get("action") or "").strip().lower()
        request_id = str(payload.get("request_id") or "").strip()
        err = ""
        ok = True
        try:
            if action == "overlay_toast":
                if native_toast_host is None:
                    raise RuntimeError("native_toast_unavailable")
                native_toast_host.show_toast(payload)
            elif action == "set_property":
                if libmpv_player is not None:
                    libmpv_player.set_property(str(payload.get("name") or ""), payload.get("value"))
                else:
                    result = _subprocess_mpv_ipc_request(
                        ["set_property", str(payload.get("name") or ""), payload.get("value")]
                    )
                    if result.get("error") != "success":
                        raise RuntimeError(str(result.get("error") or "mpv_set_property_failed"))
            elif action == "load_stream":
                stream_url = str(payload.get("stream") or "").strip()
                audio_url = (str(payload.get("audio") or "").strip() or None)
                if libmpv_player is not None:
                    libmpv_player.load_stream(stream_url, audio_url)
                else:
                    if not _subprocess_mpv_running():
                        _spawn_subprocess_mpv(stream_url, audio_url)
                        if not _wait_for_subprocess_mpv_ipc():
                            raise RuntimeError("mpv_ipc_unavailable")
                    else:
                        command: list[object] = ["loadfile", stream_url, "replace"]
                        if audio_url:
                            command.extend(["-1", f"audio-files-append={audio_url}"])
                        result = _subprocess_mpv_ipc_request(command)
                        if result.get("error") != "success":
                            raise RuntimeError(str(result.get("error") or "mpv_loadfile_failed"))
                    _sync_idle_visibility()
                    _nudge_overlay_stack()
            elif action == "command":
                command = payload.get("command")
                if not isinstance(command, list) or not command:
                    raise RuntimeError("command_empty")
                if libmpv_player is not None:
                    libmpv_player.command_list(command)
                else:
                    result = _subprocess_mpv_ipc_request(command)
                    if result.get("error") != "success":
                        raise RuntimeError(str(result.get("error") or "mpv_command_failed"))
            else:
                raise RuntimeError("invalid_action")
        except Exception as exc:
            ok = False
            err = f"{type(exc).__name__}: {exc}"

        control_state["last_ts"] = action_ts
        control_state["last_action"] = action
        control_state["last_request_id"] = request_id
        control_state["last_handled"] = time.time()
        control_state["last_ok"] = ok
        control_state["last_error"] = err

    def _write_runtime_status() -> None:
        if not runtime_status_file:
            return
        payload: dict[str, object] = {
            "runtime": "qt_shell_native",
            "alive": True,
            "qt_shell_pid": os.getpid(),
            "ts": time.time(),
            "control_file": runtime_control_file,
            "last_control_action": str(control_state.get("last_action") or ""),
            "last_control_request_id": str(control_state.get("last_request_id") or ""),
            "last_control_handled": control_state.get("last_handled"),
            "last_control_ok": control_state.get("last_ok"),
            "last_control_error": str(control_state.get("last_error") or ""),
        }
        if libmpv_player is not None:
            payload.update(libmpv_player.runtime_snapshot())
        elif mpv_proc and mpv_proc.poll() is None:
            payload.update(
                {
                    "mpv_runtime_initialized": True,
                    "mpv_runtime_playback_active": True,
                    "mpv_runtime_sample_detail": "subprocess_runtime_no_native_snapshot",
                }
            )
        _atomic_write_json(runtime_status_file, payload)

    def _tick() -> None:
        if mpv_proc and mpv_proc.poll() is not None:
            app.quit()

    timer = QTimer()

    def _heartbeat() -> None:
        _tick()
        _handle_runtime_control()
        _sync_idle_visibility()
        _write_runtime_status()

    timer.timeout.connect(_heartbeat)
    timer.start(300)

    rc = app.exec()
    if runtime_status_file:
        try:
            if os.path.exists(runtime_status_file):
                os.remove(runtime_status_file)
        except Exception:
            pass
    _shutdown()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
