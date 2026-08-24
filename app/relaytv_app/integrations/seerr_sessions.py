# SPDX-License-Identifier: GPL-3.0-only
"""Memory-only caller sessions for Seerr Jellyfin Quick Connect."""
from __future__ import annotations

from dataclasses import dataclass
from http.cookies import SimpleCookie
import re
import secrets
import threading
import time

from .seerr_client import SeerrClient, SeerrConfig, SeerrError

COOKIE_NAME = "relaytv_seerr_session"
FLOW_TTL_SEC = 10 * 60
SESSION_TTL_SEC = 12 * 60 * 60
MAX_FLOWS = 128
MAX_SESSIONS = 256
_OPAQUE_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_QUICK_SECRET_RE = re.compile(r"^[A-Fa-f0-9]{8,128}$")


@dataclass(frozen=True, slots=True)
class _Flow:
    secret: str
    server_url: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class CallerSession:
    cookie: str
    server_url: str
    identity: dict[str, object]
    expires_at: float


_LOCK = threading.RLock()
_FLOWS: dict[str, _Flow] = {}
_SESSIONS: dict[str, CallerSession] = {}


def initiate() -> dict[str, object]:
    config = _caller_config()
    with _LOCK:
        _prune_locked(time.monotonic())
        _require_capacity_locked(_FLOWS, MAX_FLOWS, "Quick Connect")
    response = SeerrClient(config).request_json_response(
        "POST",
        "/auth/jellyfin/quickconnect/initiate",
        auth=False,
    )
    data = response.data
    if not isinstance(data, dict):
        raise _invalid_upstream()
    code = str(data.get("code") or "").strip()
    secret = str(data.get("secret") or "").strip()
    if not code or len(code) > 32 or not _QUICK_SECRET_RE.fullmatch(secret):
        raise _invalid_upstream()
    flow_id = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _LOCK:
        _prune_locked(now)
        _require_capacity_locked(_FLOWS, MAX_FLOWS, "Quick Connect")
        _FLOWS[flow_id] = _Flow(
            secret=secret,
            server_url=config.server_url,
            expires_at=now + FLOW_TTL_SEC,
        )
    return {"flow_id": flow_id, "code": code, "expires_in": FLOW_TTL_SEC}


def complete(flow_id: str) -> tuple[str | None, dict[str, object]]:
    opaque = _opaque(flow_id, label="Quick Connect flow")
    config = _caller_config()
    now = time.monotonic()
    with _LOCK:
        _prune_locked(now)
        flow = _FLOWS.get(opaque)
    if flow is None or flow.server_url != config.server_url:
        raise SeerrError(
            "seerr_quick_connect_expired",
            "Quick Connect expired; start again",
            status_code=404,
        )
    client = SeerrClient(config)
    checked = client.get(
        "/auth/jellyfin/quickconnect/check",
        query={"secret": flow.secret},
        auth=False,
    )
    if not isinstance(checked, dict):
        raise _invalid_upstream()
    if not bool(checked.get("authenticated")):
        return None, {"connected": False, "pending": True}

    with _LOCK:
        claimed = _FLOWS.pop(opaque, None)
    if claimed is None:
        raise SeerrError(
            "seerr_quick_connect_expired",
            "Quick Connect expired; start again",
            status_code=404,
        )
    authenticated = client.request_json_response(
        "POST",
        "/auth/jellyfin/quickconnect/authenticate",
        body={"secret": claimed.secret},
        auth=False,
    )
    if not isinstance(authenticated.data, dict):
        raise _invalid_upstream()
    cookie = _cookie_header(authenticated.set_cookies)
    if not cookie:
        raise SeerrError(
            "seerr_session_missing",
            "Seerr did not create a caller session",
            status_code=502,
        )
    identity = _identity(authenticated.data)
    session_id = secrets.token_urlsafe(32)
    with _LOCK:
        _prune_locked(time.monotonic())
        _require_capacity_locked(_SESSIONS, MAX_SESSIONS, "caller session")
        _SESSIONS[session_id] = CallerSession(
            cookie=cookie,
            server_url=config.server_url,
            identity=identity,
            expires_at=time.monotonic() + SESSION_TTL_SEC,
        )
    return session_id, {
        "connected": True,
        "pending": False,
        "identity": identity,
        "expires_in": SESSION_TTL_SEC,
    }


def resolve(session_id: str | None) -> CallerSession | None:
    if not session_id or not _OPAQUE_RE.fullmatch(str(session_id)):
        return None
    config = SeerrConfig.current()
    if not config.enabled or config.request_mode != "caller_session":
        return None
    now = time.monotonic()
    with _LOCK:
        _prune_locked(now)
        session = _SESSIONS.get(str(session_id))
        if session is None or session.server_url != config.server_url:
            if session is not None:
                _SESSIONS.pop(str(session_id), None)
            return None
        return session


def status(session_id: str | None) -> dict[str, object]:
    session = resolve(session_id)
    if session is None:
        return {"connected": False}
    return {
        "connected": True,
        "identity": dict(session.identity),
        "expires_in": max(0, int(session.expires_at - time.monotonic())),
    }


def retire(session_id: str | None) -> None:
    if not session_id:
        return
    with _LOCK:
        _SESSIONS.pop(str(session_id), None)


def retire_all() -> None:
    with _LOCK:
        _FLOWS.clear()
        _SESSIONS.clear()


def _caller_config() -> SeerrConfig:
    config = SeerrConfig.current()
    if not config.enabled:
        raise SeerrError("seerr_disabled", "Seerr integration is disabled", status_code=503)
    if config.configuration_error:
        raise SeerrError(
            "seerr_invalid_configuration",
            config.configuration_error,
            status_code=400,
        )
    if not config.server_url:
        raise SeerrError(
            "seerr_not_configured",
            "Seerr server URL is not configured",
            status_code=503,
        )
    if config.request_mode != "caller_session":
        raise SeerrError(
            "seerr_caller_mode_disabled",
            "Caller-specific Seerr sign-in is not enabled",
            status_code=403,
        )
    return config


def _opaque(value: object, *, label: str) -> str:
    text = str(value or "").strip()
    if not _OPAQUE_RE.fullmatch(text):
        raise SeerrError(
            "seerr_invalid_request",
            f"Invalid {label}",
            status_code=400,
        )
    return text


def _cookie_header(values: tuple[str, ...]) -> str:
    pairs = []
    for value in values[:10]:
        if len(value) > 8192 or "\r" in value or "\n" in value:
            continue
        jar = SimpleCookie()
        try:
            jar.load(value)
        except Exception:
            continue
        for name, morsel in jar.items():
            if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name):
                pairs.append(f"{name}={morsel.value}")
    cookie = "; ".join(pairs)
    return cookie if len(cookie) <= 16_384 else ""


def _identity(raw: dict) -> dict[str, object]:
    try:
        user_id = int(raw.get("id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    if user_id <= 0:
        raise _invalid_upstream()
    return {
        "id": user_id,
        "display_name": str(raw.get("displayName") or "").strip()[:150],
        "username": str(raw.get("username") or "").strip()[:150],
    }


def _invalid_upstream() -> SeerrError:
    return SeerrError(
        "seerr_invalid_response",
        "Seerr returned an unexpected authentication response",
        status_code=502,
    )


def _prune_locked(now: float) -> None:
    for key, flow in list(_FLOWS.items()):
        if flow.expires_at <= now:
            _FLOWS.pop(key, None)
    for key, session in list(_SESSIONS.items()):
        if session.expires_at <= now:
            _SESSIONS.pop(key, None)


def _require_capacity_locked(values: dict, maximum: int, label: str) -> None:
    if len(values) >= maximum:
        raise SeerrError(
            "seerr_session_capacity",
            f"Too many active Seerr {label} records; try again later",
            status_code=503,
        )
