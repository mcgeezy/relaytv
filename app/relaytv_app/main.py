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

        def _set(env, key, default="", is_bool=False, lower=False, true_val="1", false_val="0"):
            if is_bool:
                val = bool(s.get(key, default) if key in s else default)
                runtime_config.set_value(env, true_val if val else false_val)
            else:
                val = str(s.get(key) or default).strip()
                runtime_config.set_value(env, val.lower() if lower else val)

        _set("RELAYTV_YTDLP_COOKIES", "youtube_cookies_path")
        _set("RELAYTV_YTDLP_AUTO_UPDATE", "ytdlp_auto_update_enabled", is_bool=True)
        _set(
            "USE_INVIDIOUS",
            "youtube_use_invidious",
            is_bool=True,
            true_val="true",
            false_val="false",
        )
        _set("INVIDIOUS_BASE", "youtube_invidious_base")
        _set("RELAYTV_JELLYFIN_ENABLED", "jellyfin_enabled", is_bool=True)
        _set("RELAYTV_JELLYFIN_AUTH_ENABLED", "jellyfin_auth_enabled", is_bool=True, default=True)
        _set("RELAYTV_JELLYFIN_PLAYBACK_MODE", "jellyfin_playback_mode", default="auto", lower=True)
        _set("RELAYTV_IPTV_ENABLED", "iptv_enabled", is_bool=True)
        _set("RELAYTV_SEERR_ENABLED", "seerr_enabled", is_bool=True)
        _set("RELAYTV_SEERR_SHARED_REQUESTS_ENABLED", "seerr_shared_requests_enabled", is_bool=True)

        _set("RELAYTV_JELLYFIN_SERVER_URL", "jellyfin_server_url")
        _set("RELAYTV_JELLYFIN_API_KEY", "jellyfin_api_key")
        _set("RELAYTV_JELLYFIN_USERNAME", "jellyfin_username")
        _set("RELAYTV_JELLYFIN_PASSWORD", "jellyfin_password")
        _set("RELAYTV_JELLYFIN_USER_ID", "jellyfin_user_id")
        _set("RELAYTV_JELLYFIN_AUDIO_LANG", "jellyfin_audio_lang", lower=True)
        _set("RELAYTV_JELLYFIN_SUB_LANG", "jellyfin_sub_lang", lower=True)
        _set("RELAYTV_SEERR_SERVER_URL", "seerr_server_url")
        _set("RELAYTV_SEERR_API_KEY", "seerr_api_key")
        _set("RELAYTV_SEERR_REQUEST_USER_ID", "seerr_request_user_id")

        runtime_config.set_value(
            "RELAYTV_SEERR_REQUEST_MODE",
            normalize_seerr_request_mode(
                s.get("seerr_request_mode"),
                shared_requests_enabled=bool(s.get("seerr_shared_requests_enabled")),
            ),
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
            seerr_sessions.retire_all()
            await stop_realtime_runtime()
            jellyfin_receiver.stop()
            iptv_service.stop_worker()
            discovery_mdns.stop()
            stop_x11_overlay()
            stop_splash_screen()
            postlive_relay.close_all(reason="server shutdown")
            plex_service.stop_all_transcodes()

    app = FastAPI(lifespan=_lifespan)

    # Registered before the slow-request logger so the logger middleware sits
    # outermost and rejected writes still show up in request logging.
    @app.middleware("http")
    async def _api_token_guard(request: Request, call_next):
        if api_auth.cross_site_mutating_get(
            request.method,
            request.url.path,
            sec_fetch_site=request.headers.get("sec-fetch-site"),
            referer=request.headers.get("referer"),
            host=request.headers.get("host"),
        ):
            return JSONResponse(
                {"detail": "cross-site write request rejected"},
                status_code=403,
            )
        if api_auth.write_request_allowed(
            request.method,
            request.headers.get("authorization"),
            path=request.url.path,
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
