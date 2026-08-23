# SPDX-License-Identifier: GPL-3.0-only
"""RelayTV-facing Seerr product behavior and sanitized integration status."""
from __future__ import annotations

import time

from ..debug import get_logger
from .seerr_client import SeerrClient, SeerrConfig, SeerrError

logger = get_logger("seerr")

_MEDIA_SERVER_TYPES = {
    1: "plex",
    2: "jellyfin",
    3: "emby",
    4: "not_configured",
}


def _config_status(config: SeerrConfig) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "configured": config.configured,
        "reachable": False,
        "server_url": config.server_url,
        "version": "",
        "application_title": "Seerr",
        "media_server_type": "unknown",
        "auth_mode": "shared_api_key" if config.api_key else "none",
        "shared_requests_enabled": config.shared_requests_enabled,
        "writes_allowed": bool(config.configured and config.shared_requests_enabled),
        "request_user_id": config.request_user_id,
    }


def integration_status(*, probe: bool = True) -> dict[str, object]:
    config = SeerrConfig.current()
    out = _config_status(config)
    if config.configuration_error:
        out["error"] = {
            "code": "seerr_invalid_configuration",
            "message": config.configuration_error,
        }
        return out
    if not config.enabled or not config.configured or not probe:
        return out
    started = time.monotonic()
    try:
        out.update(_probe(config))
        logger.info(
            "seerr_status operation=status host=%s latency_ms=%d result=ok",
            _safe_host(config.server_url),
            int((time.monotonic() - started) * 1000),
        )
        return out
    except SeerrError as exc:
        out["error"] = {"code": exc.code, "message": exc.message}
        logger.warning(
            "seerr_status operation=status host=%s latency_ms=%d result=%s",
            _safe_host(config.server_url),
            int((time.monotonic() - started) * 1000),
            exc.code,
        )
        return out


def test_connection() -> dict[str, object]:
    config = SeerrConfig.current()
    if not config.enabled:
        raise SeerrError("seerr_disabled", "Seerr integration is disabled", status_code=503)
    if config.configuration_error:
        raise SeerrError(
            "seerr_invalid_configuration",
            config.configuration_error,
            status_code=400,
        )
    if not config.configured:
        raise SeerrError(
            "seerr_not_configured",
            "Seerr server URL and API key are required",
            status_code=503,
        )
    status = _config_status(config)
    client = SeerrClient(config)
    status.update(_probe(config, client=client))
    identity = client.get("/auth/me")
    if not isinstance(identity, dict):
        raise SeerrError(
            "seerr_invalid_response",
            "Seerr returned an unexpected identity response",
            status_code=502,
        )
    status["identity"] = {
        "id": _safe_int(identity.get("id")),
        "display_name": str(identity.get("displayName") or "").strip(),
        "username": str(identity.get("username") or "").strip(),
    }
    status["ok"] = True
    return status


def _probe(config: SeerrConfig, *, client: SeerrClient | None = None) -> dict[str, object]:
    active_client = client or SeerrClient(config)
    status = active_client.get("/status", query={"checkUpdateAvailable": False}, auth=False)
    settings = active_client.get("/settings/main")
    if not isinstance(status, dict) or not isinstance(settings, dict):
        raise SeerrError(
            "seerr_invalid_response",
            "Seerr returned an unexpected response shape",
            status_code=502,
        )
    return {
        "reachable": True,
        "version": str(status.get("version") or "").strip(),
        "application_title": str(settings.get("applicationTitle") or "Seerr").strip()
        or "Seerr",
        "media_server_type": _MEDIA_SERVER_TYPES.get(
            _safe_int(settings.get("mediaServerType")), "unknown"
        ),
    }


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_host(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return str(urlsplit(url).hostname or "")
    except ValueError:
        return ""
