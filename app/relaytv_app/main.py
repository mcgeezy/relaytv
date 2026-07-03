# SPDX-License-Identifier: GPL-3.0-only
import os
import time
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.requests import Request

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
from .routes import router
from .state import get_settings, load_state_from_disk
from .thumb_cache import THUMB_DIR, start_worker as start_thumb_worker
from .integrations import jellyfin_receiver
from . import discovery_mdns
from . import video_profile
from . import upload_store
from .debug import configure_logging, get_logger, slow_request_threshold_ms, skip_slow_request_logging


def create_app(*, testing: bool = False) -> FastAPI:
    configure_logging()
    http_logger = get_logger("http")

    def _sync_jellyfin_env_from_settings() -> None:
        """Keep runtime env aligned with persisted settings."""
        try:
            s = get_settings() if callable(get_settings) else {}
        except Exception:
            s = {}
        if not isinstance(s, dict):
            return
        os.environ["RELAYTV_YTDLP_COOKIES"] = str(s.get("youtube_cookies_path") or "").strip()
        os.environ["USE_INVIDIOUS"] = "true" if bool(s.get("youtube_use_invidious")) else "false"
        os.environ["INVIDIOUS_BASE"] = str(s.get("youtube_invidious_base") or "").strip()
        os.environ["RELAYTV_JELLYFIN_ENABLED"] = "1" if bool(s.get("jellyfin_enabled")) else "0"
        os.environ["RELAYTV_JELLYFIN_SERVER_URL"] = str(s.get("jellyfin_server_url") or "").strip()
        os.environ["RELAYTV_JELLYFIN_API_KEY"] = str(s.get("jellyfin_api_key") or "").strip()
        os.environ["RELAYTV_JELLYFIN_AUTH_ENABLED"] = "1" if bool(s.get("jellyfin_auth_enabled", True)) else "0"
        os.environ["RELAYTV_JELLYFIN_USERNAME"] = str(s.get("jellyfin_username") or "").strip()
        os.environ["RELAYTV_JELLYFIN_PASSWORD"] = str(s.get("jellyfin_password") or "").strip()
        os.environ["RELAYTV_JELLYFIN_USER_ID"] = str(s.get("jellyfin_user_id") or "").strip()
        os.environ["RELAYTV_JELLYFIN_AUDIO_LANG"] = str(s.get("jellyfin_audio_lang") or "").strip().lower()
        os.environ["RELAYTV_JELLYFIN_SUB_LANG"] = str(s.get("jellyfin_sub_lang") or "").strip().lower()
        os.environ["RELAYTV_JELLYFIN_PLAYBACK_MODE"] = str(s.get("jellyfin_playback_mode") or "auto").strip().lower()
        uploads = s.get("uploads") if isinstance(s.get("uploads"), dict) else {}
        os.environ["RELAYTV_UPLOAD_MAX_SIZE_GB"] = str(uploads.get("max_size_gb") or 5.0)
        os.environ["RELAYTV_UPLOAD_RETENTION_HOURS"] = str(uploads.get("retention_hours") or 24)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        # Serve normalized thumbnails (persisted via ./data:/data)
        try:
            os.makedirs(THUMB_DIR, exist_ok=True)
        except Exception:
            pass
        load_state_from_disk()
        _sync_jellyfin_env_from_settings()
        upload_store.cleanup_uploads(get_settings() if callable(get_settings) else {})
        if not testing:
            video_profile.warm_profile()
        workers_enabled = not (
            testing or os.getenv("RELAYTV_DISABLE_WORKERS", "0").strip() in ("1", "true", "yes", "on")
        )
        if workers_enabled:
            start_autoplay_worker()
            start_session_tracker_worker()
            start_qt_audio_watchdog_worker()
            start_qt_shell_supervisor_worker()
            start_cec_monitor()
            start_thumb_worker()
            upload_store.start_cleanup_worker()
            start_x11_overlay()
            jellyfin_receiver.start()
            discovery_mdns.start_async()
            if qt_shell_backend_enabled():
                ensure_qt_shell_idle()
            else:
                start_splash_screen()
        try:
            yield
        finally:
            jellyfin_receiver.stop()
            discovery_mdns.stop()
            stop_x11_overlay()
            stop_splash_screen()

    app = FastAPI(lifespan=_lifespan)

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
