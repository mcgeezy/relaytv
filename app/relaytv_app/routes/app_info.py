# SPDX-License-Identifier: GPL-3.0-only
import json as _json
import os
import re
import threading
import time
import urllib.request

from fastapi import APIRouter


router = APIRouter()

_APP_INFO_REPO = "mcgeezy/relaytv"
_APP_INFO_CACHE_LOCK = threading.Lock()
_APP_INFO_CACHE: dict[str, object] = {"checked_at": 0.0, "latest": None, "error": ""}


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


def _pyproject_version() -> str:
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "pyproject.toml"))
    try:
        text = open(path, encoding="utf-8").read()
    except Exception:
        return ""
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', text)
    return match.group(1).strip() if match else ""


def _release_tag_url(tag: str) -> str:
    safe = str(tag or "").strip()
    if not safe:
        return ""
    return f"https://github.com/{_APP_INFO_REPO}/releases/tag/{safe}"


def _release_version_parts(value: object) -> tuple[int, int, int] | None:
    text = str(value or "").strip()
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$", text)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _update_check_disabled() -> bool:
    return _env_choice("RELAYTV_UPDATE_CHECK_DISABLED") is True


def _latest_release_from_github() -> tuple[dict[str, object] | None, str, float]:
    now = time.time()
    try:
        ttl = max(60, int(float(os.getenv("RELAYTV_UPDATE_CHECK_TTL_SEC", "21600") or "21600")))
    except Exception:
        ttl = 21600
    with _APP_INFO_CACHE_LOCK:
        cached_at = float(_APP_INFO_CACHE.get("checked_at") or 0.0)
        if cached_at and (now - cached_at) < ttl:
            latest = _APP_INFO_CACHE.get("latest")
            return (latest if isinstance(latest, dict) else None, str(_APP_INFO_CACHE.get("error") or ""), cached_at)

    latest: dict[str, object] | None = None
    error = ""
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{_APP_INFO_REPO}/releases/latest",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "RelayTV update check",
            },
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            raw = resp.read(65536)
        data = _json.loads(raw.decode("utf-8"))
        if isinstance(data, dict):
            latest = {
                "tag_name": str(data.get("tag_name") or ""),
                "name": str(data.get("name") or data.get("tag_name") or ""),
                "html_url": str(data.get("html_url") or ""),
                "published_at": str(data.get("published_at") or ""),
            }
    except Exception as exc:
        error = exc.__class__.__name__

    checked_at = time.time()
    with _APP_INFO_CACHE_LOCK:
        _APP_INFO_CACHE["checked_at"] = checked_at
        _APP_INFO_CACHE["latest"] = latest
        _APP_INFO_CACHE["error"] = error
    return latest, error, checked_at


def _app_info_payload() -> dict[str, object]:
    image_version = str(os.getenv("RELAYTV_IMAGE_VERSION") or "").strip()
    package_version = _pyproject_version()
    release_version = image_version if _release_version_parts(image_version) else package_version
    display_version = image_version if image_version and image_version != "local" else (package_version or "local")
    revision = str(os.getenv("RELAYTV_IMAGE_REVISION") or "").strip()
    created = str(os.getenv("RELAYTV_IMAGE_CREATED") or "").strip()
    source_url = str(os.getenv("RELAYTV_IMAGE_SOURCE") or f"https://github.com/{_APP_INFO_REPO}").strip()
    if not source_url:
        source_url = f"https://github.com/{_APP_INFO_REPO}"
    release_tag = release_version if str(release_version or "").startswith("v") else f"v{release_version}" if release_version else ""

    latest: dict[str, object] | None = None
    update_error = ""
    checked_at = 0.0
    update_available: bool | None = None
    if _update_check_disabled():
        update_error = "disabled"
    else:
        latest, update_error, checked_at = _latest_release_from_github()
        current_parts = _release_version_parts(release_version)
        latest_parts = _release_version_parts((latest or {}).get("tag_name") if latest else "")
        if current_parts and latest_parts:
            update_available = latest_parts > current_parts

    return {
        "name": "RelayTV",
        "version": display_version,
        "release_version": release_version or "",
        "package_version": package_version,
        "image_version": image_version,
        "revision": revision,
        "revision_short": revision[:12] if revision else "",
        "image_created": created,
        "source_url": source_url,
        "changelog_url": f"https://github.com/{_APP_INFO_REPO}/blob/main/CHANGELOG.md",
        "releases_url": f"https://github.com/{_APP_INFO_REPO}/releases",
        "current_release_url": _release_tag_url(release_tag),
        "latest_release": latest,
        "update_available": update_available,
        "update_check_error": update_error,
        "update_checked_at": checked_at,
    }


@router.get("/app/info")
def app_info() -> dict[str, object]:
    return _app_info_payload()
