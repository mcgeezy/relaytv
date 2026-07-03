# SPDX-License-Identifier: GPL-3.0-only
import os
import platform
import re
from .config import env_bool as _env_bool
import subprocess
import threading
import time
from typing import Any

from . import devices

_CACHE_LOCK = threading.Lock()
_CACHE_TS = 0.0
_CACHE_PROFILE: dict[str, Any] | None = None


def _cache_ttl_sec() -> float:
    raw = (os.getenv("RELAYTV_VIDEO_PROFILE_TTL_SEC") or "30").strip()
    try:
        ttl = float(raw)
        return max(0.0, ttl)
    except Exception:
        return 30.0


def _run(argv: list[str], timeout: float = 2.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return subprocess.CompletedProcess(argv, 127, "", "not found")
    except Exception as e:
        return subprocess.CompletedProcess(argv, 1, "", str(e))


def _parse_mode_dims(mode: str) -> tuple[int, int] | None:
    m = re.match(r"^\s*(\d+)x(\d+)", str(mode or "").strip().lower())
    if not m:
        return None
    try:
        return int(m.group(1)), int(m.group(2))
    except Exception:
        return None




def _normalize_mode_string(mode: str) -> str:
    parsed = _parse_mode_dims(mode)
    if not parsed:
        return str(mode or "").strip()
    w, h = parsed
    return f"{w}x{h}"


def _display_active_mode_from_sysfs() -> tuple[str, int | None]:
    candidates = [
        "/sys/class/graphics/fb0/modes",
        "/sys/class/graphics/fb0/mode",
        "/sys/class/graphics/fb1/modes",
        "/sys/class/graphics/fb1/mode",
    ]
    for candidate in candidates:
        try:
            if not os.path.exists(candidate):
                continue
            with open(candidate, "r", encoding="utf-8", errors="ignore") as fh:
                for raw in fh.read().splitlines():
                    line = str(raw or "").strip()
                    if not line:
                        continue
                    parsed = _parse_mode_dims(line)
                    if not parsed and ":" in line:
                        parsed = _parse_mode_dims(line.split(":", 1)[1])
                    if not parsed:
                        continue
                    w, h = parsed
                    return f"{w}x{h}", int(h)
        except Exception:
            continue
    return "", None

def _display_cap_from_drm(connectors: list[dict[str, Any]]) -> tuple[str, str, int | None]:
    best_connector = ""
    best_mode = ""
    best_height = -1
    best_width = -1
    for conn in connectors:
        if str(conn.get("status") or "").strip().lower() != "connected":
            continue
        conn_name = str(conn.get("connector") or conn.get("sys_name") or "").strip()
        for mode in conn.get("modes") or []:
            parsed = _parse_mode_dims(str(mode))
            if not parsed:
                continue
            w, h = parsed
            if (h > best_height) or (h == best_height and w > best_width):
                best_height = h
                best_width = w
                best_mode = str(mode)
                best_connector = conn_name
    if best_height <= 0:
        return "", "", None
    return best_connector, best_mode, int(best_height)


def _ffmpeg_hwaccels() -> list[str]:
    p = _run(["ffmpeg", "-hide_banner", "-hwaccels"], timeout=2.5)
    txt = "\n".join([p.stdout or "", p.stderr or ""])
    known = {
        "vdpau",
        "cuda",
        "vaapi",
        "qsv",
        "drm",
        "opencl",
        "vulkan",
        "videotoolbox",
        "dxva2",
        "d3d11va",
        "mediacodec",
        "v4l2m2m",
    }
    out: list[str] = []
    for ln in txt.splitlines():
        k = ln.strip().lower()
        if k in known and k not in out:
            out.append(k)
    return out


def _mpv_av1_decode_paths() -> list[str]:
    p = _run(["mpv", "--hwdec=help"], timeout=2.5)
    txt = "\n".join([p.stdout or "", p.stderr or ""])
    out: list[str] = []
    for ln in txt.splitlines():
        line = ln.strip().lower()
        if "(av1-" not in line:
            continue
        # Example: "vaapi (av1-vaapi)"
        method = line.split(" ", 1)[0].strip()
        if method and method not in out:
            out.append(method)
    return out


def _decode_profile(arch: str, has_dri: bool, hwaccels: list[str]) -> str:
    machine = (arch or "").lower()
    accel = {a.lower() for a in hwaccels}
    if machine in ("aarch64", "arm64"):
        return "arm_safe"
    if not has_dri:
        return "software"
    if machine in ("x86_64", "amd64") and "qsv" in accel:
        return "intel_amd64_qsv"
    if machine in ("x86_64", "amd64") and "vaapi" in accel:
        return "intel_amd64_vaapi"
    if "cuda" in accel:
        return "nvidia_cuda"
    if "vaapi" in accel:
        return "vaapi_generic"
    if "vulkan" in accel:
        return "vulkan_generic"
    return "software"


def _av1_allowed(arch: str, av1_paths: list[str]) -> bool:
    env = os.getenv("RELAYTV_VIDEO_PROFILE_ALLOW_AV1")
    if env is not None and str(env).strip() != "":
        return _env_bool("RELAYTV_VIDEO_PROFILE_ALLOW_AV1", False)
    machine = (arch or "").lower()
    if machine in ("aarch64", "arm64"):
        return False
    return bool(av1_paths)


def _build_profile() -> dict[str, Any]:
    arch = (platform.machine() or "").strip() or "unknown"
    has_dri = os.path.exists("/dev/dri")
    has_render_node = os.path.exists("/dev/dri/renderD128")
    connectors = devices.list_drm_connectors()
    conn_name, preferred_mode, detected_cap = _display_cap_from_drm(connectors)
    active_mode, active_cap = _display_active_mode_from_sysfs()
    conn_mode = preferred_mode
    env_cap = (os.getenv("RELAYTV_DISPLAY_CAP_HEIGHT") or "").strip()
    display_cap_height = detected_cap
    display_cap_source = "drm"
    if active_cap is not None:
        conn_mode = _normalize_mode_string(active_mode)
        display_cap_height = int(active_cap)
        display_cap_source = "active_mode"
    if env_cap:
        try:
            display_cap_height = max(1, int(env_cap))
            display_cap_source = "env"
        except Exception:
            display_cap_source = "active_mode" if active_cap is not None else "drm"
    if display_cap_height is None:
        display_cap_source = "unknown"
    hwaccels = _ffmpeg_hwaccels()
    av1_paths = _mpv_av1_decode_paths()
    profile = {
        "generated_ts": int(time.time()),
        "arch": arch,
        "has_dri": bool(has_dri),
        "has_render_node": bool(has_render_node),
        "hwaccels": hwaccels,
        "display_connected_connector": conn_name,
        "display_connected_mode": conn_mode,
        "display_preferred_mode": preferred_mode,
        "display_active_mode": _normalize_mode_string(active_mode),
        "display_cap_height": display_cap_height,
        "display_cap_source": display_cap_source,
        "decode_profile": _decode_profile(arch, has_dri, hwaccels),
        "av1_decode_paths": av1_paths,
        "av1_allowed": _av1_allowed(arch, av1_paths),
    }
    return profile


def refresh_profile() -> dict[str, Any]:
    global _CACHE_TS, _CACHE_PROFILE
    profile = _build_profile()
    with _CACHE_LOCK:
        _CACHE_PROFILE = dict(profile)
        _CACHE_TS = time.time()
    return profile


def get_profile(*, force_refresh: bool = False) -> dict[str, Any]:
    global _CACHE_TS, _CACHE_PROFILE
    ttl = _cache_ttl_sec()
    with _CACHE_LOCK:
        cached = dict(_CACHE_PROFILE) if isinstance(_CACHE_PROFILE, dict) else None
        cached_ts = float(_CACHE_TS or 0.0)
    if not force_refresh and cached is not None and (time.time() - cached_ts) <= ttl:
        return cached
    return refresh_profile()


def warm_profile() -> None:
    try:
        get_profile(force_refresh=True)
    except Exception:
        pass

