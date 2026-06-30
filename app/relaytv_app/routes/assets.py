# SPDX-License-Identifier: GPL-3.0-only
import mimetypes
import os

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response


router = APIRouter()

_RELAYTV_THEME = "#0b0f19"
_STATIC_ROOT = os.getenv("RELAYTV_STATIC_DIR") or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static"))
_PWA_STATIC_ROOT = os.path.join(_STATIC_ROOT, "pwa")


def _static_root_candidates() -> list[str]:
    roots: list[str] = []
    env_root = (os.getenv("RELAYTV_STATIC_DIR") or "").strip()
    if env_root:
        roots.append(env_root)
    roots.extend(
        [
            os.path.abspath(os.path.join(os.path.dirname(__file__), "static")),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static")),
            "/app/relaytv_app/static",
            os.path.join(os.getcwd(), "static"),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "static")),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "static")),
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


@router.get("/manifest.json")
def pwa_manifest():
    return JSONResponse(
        {
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
                {"src": "/pwa/brand/logo.svg", "sizes": "512x512", "type": "image/svg+xml"},
            ],
        }
    )


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
