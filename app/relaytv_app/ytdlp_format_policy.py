# SPDX-License-Identifier: GPL-3.0-only
import os
import platform
import re
from typing import Any


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def normalize_quality_mode(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in ("auto", "auto_profile", "profile"):
        return "auto_profile"
    if raw == "manual":
        return "manual"
    return "auto_profile" if _env_bool("RELAYTV_AUTO_STREAM_PROFILE", True) else "manual"


def _parse_cap(value: object) -> int | None:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw or raw == "auto" or raw == "worst":
        return None
    try:
        n = int(float(raw))
    except Exception:
        return None
    if n <= 0:
        return None
    return max(144, min(4320, n))


def extract_quality_cap_from_format(fmt: object) -> int | None:
    s = str(fmt or "").strip().lower()
    if not s:
        return None
    m = re.search(r"height<=([0-9]{3,4})", s)
    if not m:
        return None
    try:
        return _parse_cap(m.group(1))
    except Exception:
        return None


def _display_cap_height(profile: dict[str, Any] | None) -> int | None:
    if isinstance(profile, dict):
        cap = _parse_cap(profile.get("display_cap_height"))
        if cap is not None:
            return cap
    return _parse_cap(os.getenv("RELAYTV_DISPLAY_CAP_HEIGHT"))


def _user_cap(settings: dict[str, Any] | None) -> int | None:
    s = settings if isinstance(settings, dict) else {}
    cap = _parse_cap(s.get("quality_cap"))
    if cap is not None:
        return cap
    cap = _parse_cap(os.getenv("RELAYTV_QUALITY_CAP"))
    if cap is not None:
        return cap
    return extract_quality_cap_from_format(s.get("ytdlp_format"))


def _target_cap(settings: dict[str, Any] | None, profile: dict[str, Any] | None, mode: str) -> int:
    display_cap = _display_cap_height(profile)
    user_cap = _user_cap(settings)
    if mode == "auto_profile":
        if display_cap and user_cap:
            return min(display_cap, user_cap)
        if display_cap:
            return display_cap
        if user_cap:
            return user_cap
        return 1080
    return user_cap or 1080


def _provider_specific_env(provider: str) -> str:
    key = {
        "youtube": "YTDLP_FORMAT_YOUTUBE",
        "twitch": "YTDLP_FORMAT_TWITCH",
        "tiktok": "YTDLP_FORMAT_TIKTOK",
        "rumble": "YTDLP_FORMAT_RUMBLE",
        "bitchute": "YTDLP_FORMAT_BITCHUTE",
    }.get((provider or "").strip().lower(), "")
    return (os.getenv(key) or "").strip() if key else ""


def _av1_allowed(profile: dict[str, Any] | None) -> bool:
    if isinstance(profile, dict) and profile.get("av1_allowed") is not None:
        return bool(profile.get("av1_allowed"))
    return False


def _arm_default_quality_cap() -> int:
    return _parse_cap(os.getenv("RELAYTV_ARM_DEFAULT_QUALITY")) or 1080


def _auto_provider_format(provider: str, cap: int, *, av1_allowed: bool) -> str:
    p = str(provider or "other").strip().lower() or "other"
    if p == "rumble":
        # Rumble HLS manifests have shown that plain `best[...]` can still pick
        # the uncapped top rendition. Prefer `best*` first so yt-dlp applies the
        # height cap to the actual stream variant we hand off to mpv/resolver.
        return f"best*[height<={cap}][fps<=60]/best*[height<={cap}]/best[height<={cap}][fps<=60]/best"
    if p in ("twitch", "tiktok", "bitchute"):
        return f"best[height<={cap}][fps<=60]/best"
    vcodec = "" if av1_allowed else "[vcodec!*=av01]"
    return f"bestvideo{vcodec}[height<={cap}][fps<=60]+bestaudio/best{vcodec}[height<={cap}]/best"


def youtube_progressive_startup_format(
    settings: dict[str, Any] | None,
    *,
    profile: dict[str, Any] | None = None,
) -> str:
    s = settings if isinstance(settings, dict) else {}
    mode = normalize_quality_mode(s.get("quality_mode") or os.getenv("RELAYTV_QUALITY_MODE"))
    cap = _target_cap(s, profile, mode)
    if cap <= 0:
        cap = 720
    arm_cap = _arm_default_quality_cap()
    machine = (platform.machine() or "").lower()
    if machine in ("aarch64", "arm64") or (isinstance(profile, dict) and str(profile.get("decode_profile") or "").strip().lower() == "arm_safe"):
        cap = min(cap, arm_cap)
    return f"best*[height<={cap}][fps<=30][vcodec!=none][acodec!=none][vcodec^=avc1]/best*[height<={cap}][fps<=30][vcodec!=none][acodec!=none]/best[height<={cap}]/best"


def youtube_progressive_startup_candidates(
    settings: dict[str, Any] | None,
    *,
    profile: dict[str, Any] | None = None,
) -> list[str]:
    strict = youtube_progressive_startup_format(settings, profile=profile)
    s = settings if isinstance(settings, dict) else {}
    mode = normalize_quality_mode(s.get("quality_mode") or os.getenv("RELAYTV_QUALITY_MODE"))
    cap = _target_cap(s, profile, mode)
    if cap <= 0:
        cap = 720
    arm_cap = _arm_default_quality_cap()
    machine = (platform.machine() or "").lower()
    if machine in ("aarch64", "arm64") or (isinstance(profile, dict) and str(profile.get("decode_profile") or "").strip().lower() == "arm_safe"):
        cap = min(cap, arm_cap)
    return list(
        dict.fromkeys(
            [
                strict,
                f"best*[height<={cap}][vcodec!=none][acodec!=none]/best[height<={cap}]/best",
                f"best[height<={cap}]/best",
            ]
        )
    )


def youtube_progressive_startup_enabled(profile: dict[str, Any] | None = None) -> bool:
    env = os.getenv("RELAYTV_YOUTUBE_PROGRESSIVE_FIRST")
    if env is not None and str(env).strip() != "":
        return _env_bool("RELAYTV_YOUTUBE_PROGRESSIVE_FIRST", False)
    return False


def _arm_safe_if_needed(fmt: str, *, mode: str, cap: int) -> str:
    arch = (platform.machine() or "").lower()
    is_arm = arch in ("aarch64", "arm64")
    if not is_arm:
        return fmt
    if not _env_bool("RELAYTV_ARM_ENFORCE_SAFE_YTDL_FORMAT", False):
        return fmt

    arm_cap = _arm_default_quality_cap()
    safe_cap = min(arm_cap, cap) if cap > 0 else arm_cap
    safe_fmt = f"best[height<={safe_cap}][fps<=30][vcodec^=avc1]/best[height<={safe_cap}][fps<=30]/best[height<={safe_cap}]/best"
    low = (fmt or "").lower()
    heavy = ("bestvideo+" in low) or ("bestvideo[" in low) or (low == "bv*+ba/best")
    if mode == "auto_profile" or not low or heavy:
        return safe_fmt
    return fmt


def effective_ytdlp_format(
    settings: dict[str, Any] | None,
    *,
    provider: str = "other",
    profile: dict[str, Any] | None = None,
) -> str:
    s = settings if isinstance(settings, dict) else {}
    mode = normalize_quality_mode(s.get("quality_mode") or os.getenv("RELAYTV_QUALITY_MODE"))
    cap = _target_cap(s, profile, mode)

    provider_override = _provider_specific_env(provider)
    if provider_override:
        return _arm_safe_if_needed(provider_override, mode=mode, cap=cap)

    explicit = (s.get("ytdlp_format") or os.getenv("YTDLP_FORMAT") or "").strip()
    if mode == "manual" and explicit:
        return _arm_safe_if_needed(explicit, mode=mode, cap=cap)

    out = _auto_provider_format(provider, cap, av1_allowed=_av1_allowed(profile))
    return _arm_safe_if_needed(out, mode=mode, cap=cap)
