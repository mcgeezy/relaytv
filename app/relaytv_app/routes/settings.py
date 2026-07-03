# SPDX-License-Identifier: GPL-3.0-only
import os
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import player, state, upload_store
from ..debug import get_logger
from ..integrations import jellyfin_receiver


router = APIRouter()
logger = get_logger("routes.settings")


class SettingsReq(BaseModel):
    device_name: str | None = None
    video_mode: str | None = None
    drm_connector: str | None = None
    drm_mode: str | None = None
    audio_device: str | None = None
    quality_mode: str | None = None
    quality_cap: str | None = None
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
    idle_notifications_enabled: bool | None = None
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
    out["idle_notifications_enabled"] = bool(out.get("idle_notifications_enabled", True))
    return out


def _settings_flag(value: object, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


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


def _sync_idle_visual_surfaces_after_settings() -> None:
    from . import _sync_idle_visual_surfaces_after_settings as sync_idle_visual_surfaces_after_settings

    sync_idle_visual_surfaces_after_settings()


@router.get("/settings")
def get_settings():
    raw = state.get_settings() if hasattr(state, "get_settings") else {}
    return _settings_for_client(raw)


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

    updated = state.update_settings(patch) if hasattr(state, "update_settings") else patch
    if "quality_mode" in requested_keys and updated.get("quality_mode") is not None:
        os.environ["RELAYTV_QUALITY_MODE"] = str(updated.get("quality_mode") or "").strip()
    if "quality_cap" in requested_keys and updated.get("quality_cap") is not None:
        os.environ["RELAYTV_QUALITY_CAP"] = str(updated.get("quality_cap") or "").strip()
    if requested_keys.intersection({"ytdlp_format", "quality_mode"}):
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
    if "cec_enabled" in requested_keys and updated.get("cec_enabled") is not None:
        cec_on = _settings_flag(updated.get("cec_enabled"))
        os.environ["RELAYTV_CEC"] = "1" if cec_on else "0"
        os.environ["RELAYTV_CEC_ENABLED"] = "1" if cec_on else "0"
        try:
            if cec_on:
                player.start_cec_monitor()
            else:
                player.stop_cec_monitor()
        except Exception as exc:
            logger.warning("cec_monitor_settings_apply_failed error=%s", exc)
    if "idle_dashboard_enabled" in requested_keys and updated.get("idle_dashboard_enabled") is not None:
        os.environ["RELAYTV_IDLE_DASHBOARD_ENABLED"] = "1" if bool(updated.get("idle_dashboard_enabled")) else "0"
    if "idle_notifications_enabled" in requested_keys and updated.get("idle_notifications_enabled") is not None:
        os.environ["RELAYTV_IDLE_NOTIFICATIONS_ENABLED"] = "1" if bool(updated.get("idle_notifications_enabled")) else "0"
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
        os.environ["RELAYTV_JELLYFIN_AUTH_ENABLED"] = "1"

    live_applied: list[str] = []
    live_apply_failed: list[str] = []
    playing_now = bool(player.is_playing())
    if (not apply_now) and playing_now:
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
    if requested_keys.intersection({"idle_dashboard_enabled", "idle_notifications_enabled"}):
        idle_keys = sorted(requested_keys.intersection({"idle_dashboard_enabled", "idle_notifications_enabled"}))
        try:
            _sync_idle_visual_surfaces_after_settings()
            live_applied.extend(k for k in idle_keys if k not in live_applied)
        except Exception:
            live_apply_failed.extend(k for k in idle_keys if k not in live_apply_failed)
    if "cec_enabled" in requested_keys and "cec_enabled" not in live_apply_failed and "cec_enabled" not in live_applied:
        live_applied.append("cec_enabled")

    sess_for_apply = str(getattr(state, "SESSION_STATE", "idle") or "idle").strip().lower()
    apply_restart_allowed = bool(apply_now and sess_for_apply in ("playing", "paused") and isinstance(state.NOW_PLAYING, dict))
    now = None
    apply_performed = False
    apply_succeeded = False
    if apply_restart_allowed:
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
