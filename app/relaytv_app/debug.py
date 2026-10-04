# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import logging
import os
import time


_LOGGING_CONFIGURED = False
_HOT_ACCESS_FILTER_NAME = "_relaytv_hot_access_filter"
_HOT_ACCESS_PATHS = (
    "/status",
    "/playback/state",
    "/ui/events",
    "/integrations/jellyfin/status",
)
_SLOW_REQUEST_SKIP_PREFIXES = (
    "/thumbs/",
    "/pwa/",
    "/assets/",
    "/snapshots/",
    "/favicon.ico",
    "/manifest.json",
    "/sw.js",
    # Post-live relay streams stay open for the length of the replay; that
    # is the feature working, not a slow request.
    "/postlive/",
)
_SLOW_REQUEST_SKIP_EXACT = (
    "/health",
    "/ui/events",
    "/x11/overlay/events",
)


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def _env_log_level(name: str, default: str = "INFO") -> int:
    raw = str(os.getenv(name) or default).strip().upper()
    return getattr(logging, raw, logging.INFO)


def access_logging_enabled() -> bool:
    raw = os.getenv("RELAYTV_ACCESS_LOG")
    if raw is None:
        return True
    return _truthy(raw)


def _hot_access_paths() -> tuple[str, ...]:
    raw = str(os.getenv("RELAYTV_ACCESS_LOG_HOT_PATHS") or "").strip()
    if not raw:
        return _HOT_ACCESS_PATHS
    parts = tuple(part.strip() for part in raw.split(",") if part.strip())
    return parts or _HOT_ACCESS_PATHS


def slow_request_threshold_ms() -> float:
    try:
        return max(0.0, float(os.getenv("RELAYTV_SLOW_REQUEST_MS", "250")))
    except Exception:
        return 250.0


def skip_slow_request_logging(path: str) -> bool:
    norm = str(path or "").split("?", 1)[0]
    if norm in _SLOW_REQUEST_SKIP_EXACT:
        return True
    return any(norm.startswith(prefix) for prefix in _SLOW_REQUEST_SKIP_PREFIXES)


def configure_logging() -> None:
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    log_level = _env_log_level("RELAYTV_LOG_LEVEL", "INFO")
    access_level = _env_log_level("RELAYTV_ACCESS_LOG_LEVEL", "INFO")
    resolver_level = _env_log_level("RELAYTV_RESOLVER_LOG_LEVEL", os.getenv("RELAYTV_LOG_LEVEL", "INFO"))

    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )
    else:
        root.setLevel(log_level)

    logging.getLogger("relaytv").setLevel(log_level)
    logging.getLogger("relaytv.resolver").setLevel(resolver_level)
    logging.getLogger("uvicorn.error").setLevel(log_level)

    access_logger = logging.getLogger("uvicorn.access")
    if access_logging_enabled():
        access_logger.setLevel(access_level)
        if not any(isinstance(flt, _RelaytvHotAccessFilter) for flt in access_logger.filters):
            access_logger.addFilter(_RelaytvHotAccessFilter())
        for handler in access_logger.handlers:
            if not getattr(handler, _HOT_ACCESS_FILTER_NAME, False):
                handler.addFilter(_RelaytvHotAccessFilter())
                setattr(handler, _HOT_ACCESS_FILTER_NAME, True)
    else:
        access_logger.disabled = True

    _LOGGING_CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    normalized = str(name or "app").strip().replace(" ", "_")
    if normalized.startswith("relaytv."):
        return logging.getLogger(normalized)
    return logging.getLogger(f"relaytv.{normalized}")


class _RelaytvHotAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not access_logging_enabled():
            return False
        try:
            args = tuple(record.args or ())
            if len(args) < 5:
                return True
            method = str(args[1] or "")
            path = str(args[2] or "").split("?", 1)[0]
            status_code = int(args[4])
        except Exception:
            return True
        if method not in ("GET", "HEAD"):
            return True
        if status_code >= 400:
            return True
        return path not in _hot_access_paths()


def debug_enabled(scope: str | None = None) -> bool:
    """Enable RelayTV debug logging via RELAYTV_DEBUG.

    Supported values:
      - 1/true/yes/on/all/*: enable all debug logs
      - comma-separated scopes: e.g. "youtube,resolver,player"
    """
    raw = (os.getenv("RELAYTV_DEBUG") or "").strip()
    if not raw:
        return False

    low = raw.lower()
    if _truthy(low) or low in ("all", "*"):
        return True

    wanted = {part.strip().lower() for part in low.split(",") if part.strip()}
    if not wanted:
        return False
    if scope is None:
        return True
    return scope.strip().lower() in wanted


def debug_log(scope: str, message: str) -> None:
    if not debug_enabled(scope):
        return
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    get_logger(f"debug.{scope}").debug("[%s] %s", ts, message)
