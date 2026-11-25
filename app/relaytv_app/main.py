# SPDX-License-Identifier: GPL-3.0-only
import os
import time
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse

from .player import (
    ensure_qt_shell_idle,
    qt_shell_backend_enabled,
    start_autoplay_worker,
    start_cec_monitor,
    start_qt_audio_watchdog_worker,
    start_qt_shell_supervisor_worker,
    start_session_tracker_worker,
    start_splash_screen,
    stop_cec_monitor,
    stop_mpv,
    stop_splash_screen,
)
from .x11_overlay import start_overlay as start_x11_overlay, stop_overlay as stop_x11_overlay
from . import api_auth
from .config import normalize_seerr_request_mode, runtime_config
from .routes import router, start_realtime_runtime, stop_realtime_runtime
from .state import get_settings, load_state_from_disk
from .thumb_cache import THUMB_DIR, start_worker as start_thumb_worker
from .integrations import iptv_service, jellyfin_receiver, plex_service, seerr_sessions
from . import discovery_mdns
from . import postlive_relay
from . import video_profile
from . import upload_store
from . import ytdlp_update
from . import youtube_playlist
from .debug import (
    configure_logging,
    get_logger,
    slow_request_threshold_ms,
    skip_slow_request_logging,
)


def create_app(*, testing: bool = False) -> FastAPI:
    configure_logging()
    http_logger = get_logger("http")

    def _sync_jellyfin_env_from_settings() -> None:
        """Sync the runtime config snapshot from persisted settings."""
        try:
            s = get_settings() if callable(get_settings) else {}
        except Exception:
            s = {}
        if not isinstance(s, dict):
            return
        from .routes.settings import is_allowed_youtube_cookies_path

        cookie_path = str(s.get("youtube_cookies_path") or "").strip()
        if not is_allowed_youtube_cookies_path(cookie_path):
            cookie_path = ""
        runtime_config.set_value("RELAYTV_YTDLP_COOKIES", cookie_path)
        if "device_name" in s and s.get("device_name") is not None:
            clean_name = str(s.get("device_name") or "RelayTV").strip() or "RelayTV"
            runtime_config.set_value("RELAYTV_DEVICE_NAME", clean_name)
            runtime_config.set_value("RELAYTV_JELLYFIN_DEVICE_NAME", clean_name)
            runtime_config.set_value("RELAYTV_JELLYFIN_CLIENT_NAME", clean_name)
        configured_audio = str(s.get("audio_device") or "").strip()
        if configured_audio:
            runtime_config.set_value("MPV_AUDIO_DEVICE", configured_audio)
        if "video_mode" in s and s.get("video_mode") is not None:
            runtime_config.set_value(
                "RELAYTV_VIDEO_MODE",
                str(s.get("video_mode") or "auto").strip().lower() or "auto",
            )
        if "drm_connector" in s and s.get("drm_connector") is not None:
            runtime_config.set_value(
                "RELAYTV_DRM_CONNECTOR", str(s.get("drm_connector") or "").strip()
            )
        if "sub_lang" in s and s.get("sub_lang") is not None:
            runtime_config.set_value("RELAYTV_SUB_LANG", str(s.get("sub_lang") or "").strip())
        if "cec_enabled" in s and s.get("cec_enabled") is not None:
            cec_raw = s.get("cec_enabled")
            cec_on = (
                cec_raw
                if isinstance(cec_raw, bool)
                else str(cec_raw).strip().lower() in ("1", "true", "yes", "on")
            )
            runtime_config.set_value("RELAYTV_CEC", "1" if cec_on else "0")
            runtime_config.set_value("RELAYTV_CEC_ENABLED", "1" if cec_on else "0")
        if "idle_dashboard_enabled" in s and s.get("idle_dashboard_enabled") is not None:
            runtime_config.set_value(
                "RELAYTV_IDLE_DASHBOARD_ENABLED",
                "1" if bool(s.get("idle_dashboard_enabled")) else "0",
            )
        if "idle_notifications_enabled" in s and s.get("idle_notifications_enabled") is not None:
            runtime_config.set_value(
                "RELAYTV_IDLE_NOTIFICATIONS_ENABLED",
                "1" if bool(s.get("idle_notifications_enabled")) else "0",
            )
        if "idle_qr_enabled" in s and s.get("idle_qr_enabled") is not None:
            runtime_config.set_value(
                "RELAYTV_IDLE_QR_ENABLED", "1" if bool(s.get("idle_qr_enabled")) else "0"
            )
        if "idle_qr_size" in s and s.get("idle_qr_size") is not None:
            try:
                runtime_config.set_value("RELAYTV_IDLE_QR_SIZE", str(int(s.get("idle_qr_size"))))
            except Exception:
                pass
        if "jellyfin_server_type" in s and s.get("jellyfin_server_type") is not None:
            runtime_config.set_value(
                "RELAYTV_JELLYFIN_SERVER_TYPE",
                str(s.get("jellyfin_server_type") or "jellyfin").strip().lower() or "jellyfin",
            )
        if "ytdlp_format" in s and s.get("ytdlp_format") is not None:
            qmode = str(s.get("quality_mode") or "").strip().lower()
            if qmode in ("auto", "auto_profile", "profile"):
                if not (runtime_config.snapshot().raw("YTDLP_FORMAT") or "").strip():
                    runtime_config.set_value("YTDLP_FORMAT", "")
            else:
                runtime_config.set_value("YTDLP_FORMAT", str(s.get("ytdlp_format") or "").strip())
        runtime_config.set_value(
            "RELAYTV_YTDLP_AUTO_UPDATE", "1" if bool(s.get("ytdlp_auto_update_enabled")) else "0"
        )
        runtime_config.set_value(
            "USE_INVIDIOUS", "true" if bool(s.get("youtube_use_invidious")) else "false"
        )
        runtime_config.set_value(
            "INVIDIOUS_BASE", str(s.get("youtube_invidious_base") or "").strip()
        )
        runtime_config.set_value(
            "RELAYTV_JELLYFIN_ENABLED", "1" if bool(s.get("jellyfin_enabled")) else "0"
        )
        runtime_config.set_value(
            "RELAYTV_JELLYFIN_SERVER_URL", str(s.get("jellyfin_server_url") or "").strip()
        )
        runtime_config.set_value(
            "RELAYTV_JELLYFIN_API_KEY", str(s.get("jellyfin_api_key") or "").strip()
        )
        runtime_config.set_value(
            "RELAYTV_JELLYFIN_AUTH_ENABLED",
            "1" if bool(s.get("jellyfin_auth_enabled", True)) else "0",
        )
        runtime_config.set_value(
            "RELAYTV_JELLYFIN_USERNAME", str(s.get("jellyfin_username") or "").strip()
        )
        runtime_config.set_value(
            "RELAYTV_JELLYFIN_PASSWORD", str(s.get("jellyfin_password") or "").strip()
        )
        runtime_config.set_value(
            "RELAYTV_JELLYFIN_USER_ID", str(s.get("jellyfin_user_id") or "").strip()
        )
        runtime_config.set_value(
            "RELAYTV_JELLYFIN_AUDIO_LANG", str(s.get("jellyfin_audio_lang") or "").strip().lower()
        )
        runtime_config.set_value(
            "RELAYTV_JELLYFIN_SUB_LANG", str(s.get("jellyfin_sub_lang") or "").strip().lower()
        )
        runtime_config.set_value(
            "RELAYTV_JELLYFIN_PLAYBACK_MODE",
            str(s.get("jellyfin_playback_mode") or "auto").strip().lower(),
        )
        runtime_config.set_value(
            "RELAYTV_IPTV_ENABLED", "1" if bool(s.get("iptv_enabled")) else "0"
        )
        runtime_config.set_value(
            "RELAYTV_SEERR_ENABLED", "1" if bool(s.get("seerr_enabled")) else "0"
        )
        runtime_config.set_value(
            "RELAYTV_SEERR_SERVER_URL", str(s.get("seerr_server_url") or "").strip()
        )
        runtime_config.set_value("RELAYTV_SEERR_API_KEY", str(s.get("seerr_api_key") or "").strip())
        runtime_config.set_value(
            "RELAYTV_SEERR_SHARED_REQUESTS_ENABLED",
            "1" if bool(s.get("seerr_shared_requests_enabled")) else "0",
        )
        runtime_config.set_value(
            "RELAYTV_SEERR_REQUEST_MODE",
            normalize_seerr_request_mode(
                s.get("seerr_request_mode"),
                shared_requests_enabled=bool(s.get("seerr_shared_requests_enabled")),
            ),
        )
        runtime_config.set_value(
            "RELAYTV_SEERR_REQUEST_USER_ID",
            str(s.get("seerr_request_user_id") or "").strip(),
        )
        uploads = s.get("uploads") if isinstance(s.get("uploads"), dict) else {}
        runtime_config.set_value(
            "RELAYTV_UPLOAD_MAX_SIZE_GB", str(uploads.get("max_size_gb") or 5.0)
        )
        runtime_config.set_value(
            "RELAYTV_UPLOAD_RETENTION_HOURS", str(uploads.get("retention_hours") or 24)
        )

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        # Serve normalized thumbnails (persisted via ./data:/data)
        try:
            os.makedirs(THUMB_DIR, exist_ok=True)
        except Exception:
            pass
        load_state_from_disk()
        # Capture operator-provided settings-bus env as defaults, then let the
        # persisted settings sync overwrite its subset in the snapshot.
        runtime_config.refresh_from_env()
        _sync_jellyfin_env_from_settings()
        upload_store.cleanup_uploads(get_settings() if callable(get_settings) else {})
        # Spool files from a previous process are unreachable (sessions and
        # the completed-spool registry are process-local) — reclaim the disk.
        postlive_relay.sweep_spool_root()
        if not testing:
            video_profile.warm_profile()
        await start_realtime_runtime()
        youtube_playlist.imports.start()
        workers_enabled = not (
            testing
            or os.getenv("RELAYTV_DISABLE_WORKERS", "0").strip() in ("1", "true", "yes", "on")
        )
        if workers_enabled:
            start_autoplay_worker()
            start_session_tracker_worker()
            start_qt_audio_watchdog_worker()
            start_qt_shell_supervisor_worker()
            start_cec_monitor()
            start_thumb_worker()
            upload_store.start_cleanup_worker()
            ytdlp_update.start_worker()
            start_x11_overlay()
            jellyfin_receiver.start()
            iptv_service.start_worker()
            discovery_mdns.start_async()
            discovery_mdns.start_browse_async()
            if qt_shell_backend_enabled():
                ensure_qt_shell_idle()
            else:
                start_splash_screen()
        try:
            yield
        finally:
            youtube_playlist.imports.stop()
            seerr_sessions.retire_all()
            await stop_realtime_runtime()
            jellyfin_receiver.stop()
            iptv_service.stop_worker()
            discovery_mdns.stop()
            stop_x11_overlay()
            stop_splash_screen()
            if workers_enabled:
                try:
                    stop_mpv(restart_splash=False)
                except Exception:
                    pass
                try:
                    stop_cec_monitor()
                except Exception:
                    pass
            postlive_relay.close_all(reason="server shutdown")
            plex_service.stop_all_transcodes()

    app = FastAPI(lifespan=_lifespan)
    app.state.testing = bool(testing)

    # Registered before the slow-request logger so the logger middleware sits
    # outermost and rejected writes still show up in request logging.
    @app.middleware("http")
    async def _api_token_guard(request: Request, call_next):
        auth_header = request.headers.get("authorization")
        client_host = request.client.host if request.client else ""
        has_valid_token = bool(
            api_auth.configured_api_token()
            and api_auth.bearer_token_from_header(auth_header)
            and api_auth.write_request_allowed(
                request.method,
                auth_header,
                path=request.url.path,
            )
        )
        if not has_valid_token and api_auth.cross_site_write_request(
            request.method,
            request.url.path,
            sec_fetch_site=request.headers.get("sec-fetch-site"),
            origin=request.headers.get("origin"),
            referer=request.headers.get("referer"),
            host=request.headers.get("host"),
        ):
            return JSONResponse(
                {"detail": "cross-site write request rejected"},
                status_code=403,
            )
        if api_auth.write_request_allowed(
            request.method,
            auth_header,
            path=request.url.path,
            overlay_token=request.headers.get(api_auth.OVERLAY_TOKEN_HEADER),
            client_host=client_host,
            testing=bool(getattr(app.state, "testing", False)),
        ):
            return await call_next(request)
        return JSONResponse(
            {"detail": "api token required"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.middleware("http")
    async def _log_slow_requests(request: Request, call_next):
        start_ts = time.monotonic()
        path = request.url.path or "/"
        try:
            response = await call_next(request)
        except Exception:
            latency_ms = int((time.monotonic() - start_ts) * 1000)
            if not skip_slow_request_logging(path):
                http_logger.exception(
                    "request_error method=%s path=%s latency_ms=%d",
                    request.method,
                    path,
                    latency_ms,
                )
            raise

        latency_ms = int((time.monotonic() - start_ts) * 1000)
        threshold_ms = slow_request_threshold_ms()
        if skip_slow_request_logging(path):
            return response
        if response.status_code >= 400 or latency_ms >= threshold_ms:
            level = logging.WARNING if response.status_code >= 500 else logging.INFO
            http_logger.log(
                level,
                "request method=%s path=%s status=%d latency_ms=%d",
                request.method,
                path,
                int(response.status_code),
                latency_ms,
            )
        return response

    app.include_router(router)

    return app


app = create_app()
