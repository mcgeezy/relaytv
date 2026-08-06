# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import os
import threading
from ..config import env_bool as _env_bool
from ..config import runtime_config
import time
import json
import hashlib
import re
from urllib import request as _urlrequest
from urllib import error as _urlerror
from urllib import parse as _urlparse
import platform
from ..thumb_cache import attach_local_thumbnail

_LOCK = threading.Lock()
_THREAD_LOCK = threading.Lock()

_STATUS: dict[str, object] = {
    "enabled": False,
    "running": False,
    "server_url": "",
    "device_name": "RelayTV",
    "device_id": "",
    "client_name": "RelayTV",
    "client_version": "1.0",
    "heartbeat_sec": 5,
    "server_type": "jellyfin",
    "server_product_name": "",
    "last_detect_ts": None,
    "last_detect_ok": None,
    "last_detect_error": None,
    "api_key_configured": False,
    "connected": False,
    "last_heartbeat_ts": None,
    "last_command": None,
    "last_error": None,
    "last_register_ts": None,
    "last_register_ok": None,
    "last_register_error": None,
    "last_register_reason": None,
    "last_progress_ts": None,
    "last_progress_ok": None,
    "last_progress_error": None,
    "last_stopped_ts": None,
    "last_stopped_ok": None,
    "last_stopped_error": None,
    "last_playing_ts": None,
    "last_playing_ok": None,
    "last_playing_error": None,
    "register_retry_failures": 0,
    "next_register_retry_ts": None,
    "last_register_backoff_sec": 0.0,
    # None until a session readback has been attempted; see _verify_registration.
    "media_control_verified": None,
    "auth_user_configured": False,
    "authenticated": False,
    "auth_user": "",
    "auth_user_id": "",
    "auth_session_id": "",
    "catalog_user_id": "",
    "catalog_user_source": "none",
    "last_auth_ts": None,
    "last_auth_ok": None,
    "last_auth_error": None,
    "catalog_last_ok_ts": None,
    "catalog_last_error": None,
    "catalog_cache_hits": 0,
    "catalog_cache_misses": 0,
    "catalog_cache_clears": 0,
    "catalog_cache_last_cleared_ts": None,
    "catalog_cache_last_cleared_reason": None,
    "progress_success_count": 0,
    "progress_failure_count": 0,
    "stopped_success_count": 0,
    "stopped_failure_count": 0,
    "stopped_suppressed_count": 0,
    "last_progress_latency_ms": None,
    "last_stopped_latency_ms": None,
}

_API_KEY: str = ""
_AUTH_USERNAME: str = ""
_AUTH_PASSWORD: str = ""
_ACCESS_TOKEN: str = ""
_AUTH_USER_ID: str = ""
_AUTH_SESSION_ID: str = ""
_STOP_EVENT = threading.Event()
_THREAD: threading.Thread | None = None
_PROGRESS_PROVIDER = None
_COMMAND_SINK = None
_REGISTER_RETRY_FAILURES = 0
_NEXT_REGISTER_RETRY_TS = 0.0
_CATALOG_CACHE_LOCK = threading.Lock()
_CATALOG_CACHE: dict[str, tuple[float, object]] = {}
_LAST_STOPPED_SIGNATURE = ""
_LAST_STOPPED_TS = 0.0


def _catalog_ttl_sec(kind: str) -> float:
    if kind == "metadata":
        return max(
            0.0,
            float(
                os.getenv(
                    "RELAYTV_JELLYFIN_METADATA_TTL_SEC",
                    os.getenv("RELAYTV_JELLYFIN_DETAIL_TTL_SEC", "300"),
                )
                or "300"
            ),
        )
    if kind == "detail":
        return max(0.0, float(os.getenv("RELAYTV_JELLYFIN_DETAIL_TTL_SEC", "300") or "300"))
    if kind == "search":
        return max(0.0, float(os.getenv("RELAYTV_JELLYFIN_SEARCH_TTL_SEC", os.getenv("RELAYTV_JELLYFIN_CATALOG_TTL_SEC", "120")) or "120"))
    return max(0.0, float(os.getenv("RELAYTV_JELLYFIN_CATALOG_TTL_SEC", "120") or "120"))


def _catalog_cache_max_entries() -> int:
    try:
        return max(32, int(float(os.getenv("RELAYTV_JELLYFIN_CATALOG_MAX_ENTRIES", "96") or "96")))
    except Exception:
        return 96


def _catalog_cache_get(key: str) -> object | None:
    now = time.time()
    with _CATALOG_CACHE_LOCK:
        item = _CATALOG_CACHE.get(key)
        if not item:
            with _LOCK:
                _STATUS["catalog_cache_misses"] = int(_STATUS.get("catalog_cache_misses") or 0) + 1
            return None
        expires_at, payload = item
        if expires_at <= now:
            _CATALOG_CACHE.pop(key, None)
            with _LOCK:
                _STATUS["catalog_cache_misses"] = int(_STATUS.get("catalog_cache_misses") or 0) + 1
            return None
        with _LOCK:
            _STATUS["catalog_cache_hits"] = int(_STATUS.get("catalog_cache_hits") or 0) + 1
        return payload


def _catalog_cache_set(key: str, payload: object, *, ttl_sec: float) -> None:
    if ttl_sec <= 0:
        return
    with _CATALOG_CACHE_LOCK:
        _CATALOG_CACHE[key] = (time.time() + ttl_sec, payload)
        now = time.time()
        for k, (exp, _) in list(_CATALOG_CACHE.items()):
            if exp <= now:
                _CATALOG_CACHE.pop(k, None)
        max_entries = _catalog_cache_max_entries()
        if len(_CATALOG_CACHE) > max_entries:
            # Keep entries with latest expiry first.
            drop_keys = sorted(_CATALOG_CACHE.items(), key=lambda kv: kv[1][0])[: len(_CATALOG_CACHE) - max_entries]
            for k, _ in drop_keys:
                _CATALOG_CACHE.pop(k, None)


def _attach_thumb(item: dict[str, object]) -> dict[str, object]:
    try:
        attach_local_thumbnail(item)
    except Exception:
        pass
    return item


def _catalog_cache_clear() -> None:
    with _CATALOG_CACHE_LOCK:
        _CATALOG_CACHE.clear()
    with _LOCK:
        _STATUS["catalog_cache_hits"] = 0
        _STATUS["catalog_cache_misses"] = 0


def _mark_catalog_cache_cleared(*, reason: str) -> None:
    clean_reason = str(reason or "").strip() or "unknown"
    with _LOCK:
        _STATUS["catalog_cache_clears"] = int(_STATUS.get("catalog_cache_clears") or 0) + 1
        _STATUS["catalog_cache_last_cleared_ts"] = int(time.time())
        _STATUS["catalog_cache_last_cleared_reason"] = clean_reason


def _stopped_dedupe_sec() -> float:
    try:
        v = float(os.getenv("RELAYTV_JELLYFIN_STOPPED_DEDUPE_SEC", "2") or "2")
    except Exception:
        v = 2.0
    return max(0.0, v)


def _complete_ratio() -> float:
    try:
        ratio = float(os.getenv("RELAYTV_JELLYFIN_COMPLETE_RATIO", "0.98"))
    except Exception:
        ratio = 0.98
    return min(0.999, max(0.0, ratio))


def _complete_remaining_sec() -> float:
    try:
        sec = float(os.getenv("RELAYTV_JELLYFIN_COMPLETE_REMAINING_SEC", "0"))
    except Exception:
        sec = 0.0
    return max(0.0, sec)


def _stopped_signature(payload: dict[str, object]) -> str:
    try:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except Exception:
        raw = str(payload)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _stopped_duplicate_suppressed(payload: dict[str, object], now_ts: float) -> bool:
    window = _stopped_dedupe_sec()
    if window <= 0.0:
        return False
    sig = _stopped_signature(payload)
    global _LAST_STOPPED_SIGNATURE, _LAST_STOPPED_TS
    with _LOCK:
        is_dup = bool(_LAST_STOPPED_SIGNATURE) and (_LAST_STOPPED_SIGNATURE == sig) and ((now_ts - _LAST_STOPPED_TS) <= window)
        if is_dup:
            _STATUS["stopped_suppressed_count"] = int(_STATUS.get("stopped_suppressed_count") or 0) + 1
            return True
        _LAST_STOPPED_SIGNATURE = sig
        _LAST_STOPPED_TS = now_ts
    return False


def _mark_catalog_ok() -> None:
    with _LOCK:
        _STATUS["catalog_last_ok_ts"] = int(time.time())
        _STATUS["catalog_last_error"] = None


def _mark_catalog_error(msg: str) -> None:
    text = _sanitize_error_text(msg)
    with _LOCK:
        _STATUS["catalog_last_error"] = text or None


def _read_config() -> dict[str, object]:
    configured_name = ""
    configured_server_type = ""
    try:
        from .. import state as _state

        s = _state.get_settings() if hasattr(_state, "get_settings") else {}
        configured_name = str((s or {}).get("device_name") or "").strip()
        configured_server_type = str((s or {}).get("jellyfin_server_type") or "").strip().lower()
    except Exception:
        configured_name = ""
        configured_server_type = ""
    server_type = configured_server_type or (runtime_config.snapshot().raw("RELAYTV_JELLYFIN_SERVER_TYPE") or "").strip().lower()
    if server_type not in ("jellyfin", "emby"):
        server_type = "jellyfin"
    device_name = (
        configured_name
        or (runtime_config.snapshot().raw("RELAYTV_DEVICE_NAME") or "").strip()
        or (runtime_config.snapshot().raw("RELAYTV_JELLYFIN_DEVICE_NAME") or "RelayTV").strip()
        or "RelayTV"
    )
    if len(device_name) > 80:
        device_name = device_name[:80].strip() or "RelayTV"
    return {
        "enabled": runtime_config.snapshot().flag("RELAYTV_JELLYFIN_ENABLED", False),
        "server_url": (runtime_config.snapshot().raw("RELAYTV_JELLYFIN_SERVER_URL") or "").strip(),
        "device_name": device_name,
        "device_id": (os.getenv("RELAYTV_JELLYFIN_DEVICE_ID") or f"relaytv-{device_name.lower().replace(' ', '-')}-{platform.node() or 'host'}").strip(),
        "client_name": (runtime_config.snapshot().raw("RELAYTV_JELLYFIN_CLIENT_NAME") or device_name).strip() or device_name,
        "client_version": (os.getenv("RELAYTV_JELLYFIN_CLIENT_VERSION") or "1.0").strip() or "1.0",
        "heartbeat_sec": max(2, int(float(os.getenv("RELAYTV_JELLYFIN_HEARTBEAT_SEC") or "5"))),
        "server_type": server_type,
    }


def _preferred_catalog_user_id() -> str:
    try:
        from .. import state as _state

        cur = _state.get_settings() if hasattr(_state, "get_settings") else {}
        pref = str((cur or {}).get("jellyfin_user_id") or "").strip()
        if pref:
            return pref
    except Exception:
        pass
    return str(runtime_config.snapshot().raw("RELAYTV_JELLYFIN_USER_ID") or "").strip()


def _effective_catalog_user(st: dict[str, object]) -> tuple[str, str]:
    preferred = _preferred_catalog_user_id()
    if preferred:
        return preferred, "preferred"
    authenticated = str(st.get("auth_user_id") or "").strip()
    if authenticated:
        return authenticated, "authenticated"
    return "", "none"


def start() -> None:
    """Initialize Jellyfin receiver runtime state (network wiring added later)."""
    global _API_KEY, _AUTH_USERNAME, _AUTH_PASSWORD, _ACCESS_TOKEN, _AUTH_USER_ID, _AUTH_SESSION_ID
    cfg = _read_config()
    global _REGISTER_RETRY_FAILURES, _NEXT_REGISTER_RETRY_TS, _LAST_STOPPED_SIGNATURE, _LAST_STOPPED_TS
    with _LOCK:
        _STATUS["enabled"] = bool(cfg["enabled"])
        _STATUS["server_url"] = str(cfg["server_url"])
        _STATUS["device_name"] = str(cfg["device_name"])
        _STATUS["device_id"] = str(cfg["device_id"])
        _STATUS["client_name"] = str(cfg["client_name"])
        _STATUS["client_version"] = str(cfg["client_version"])
        _STATUS["heartbeat_sec"] = int(cfg["heartbeat_sec"])
        _STATUS["server_type"] = str(cfg["server_type"])
        _API_KEY = (runtime_config.snapshot().raw("RELAYTV_JELLYFIN_API_KEY") or "").strip()
        _AUTH_USERNAME = (runtime_config.snapshot().raw("RELAYTV_JELLYFIN_USERNAME") or "").strip()
        _AUTH_PASSWORD = (runtime_config.snapshot().raw("RELAYTV_JELLYFIN_PASSWORD") or "").strip()
        _ACCESS_TOKEN = ""
        _AUTH_USER_ID = ""
        _AUTH_SESSION_ID = ""
        _STATUS["api_key_configured"] = bool(_API_KEY)
        _STATUS["auth_user_configured"] = bool(_AUTH_USERNAME and _AUTH_PASSWORD)
        _STATUS["authenticated"] = False
        _STATUS["auth_user"] = _AUTH_USERNAME
        _STATUS["auth_user_id"] = ""
        _STATUS["auth_session_id"] = ""
        _STATUS["last_auth_ts"] = None
        _STATUS["last_auth_ok"] = None
        _STATUS["last_auth_error"] = None
        _STATUS["catalog_last_ok_ts"] = None
        _STATUS["catalog_last_error"] = None
        _STATUS["catalog_cache_hits"] = 0
        _STATUS["catalog_cache_misses"] = 0
        _STATUS["catalog_cache_clears"] = 0
        _STATUS["catalog_cache_last_cleared_ts"] = None
        _STATUS["catalog_cache_last_cleared_reason"] = None
        _STATUS["progress_success_count"] = 0
        _STATUS["progress_failure_count"] = 0
        _STATUS["stopped_success_count"] = 0
        _STATUS["stopped_failure_count"] = 0
        _STATUS["stopped_suppressed_count"] = 0
        _STATUS["last_progress_latency_ms"] = None
        _STATUS["last_stopped_latency_ms"] = None
        _STATUS["running"] = bool(cfg["enabled"])
        _STATUS["connected"] = False
        _STATUS["last_error"] = None
        _STATUS["last_command"] = None
        _STATUS["last_heartbeat_ts"] = int(time.time()) if bool(cfg["enabled"]) else None
        _STATUS["last_register_ts"] = None
        _STATUS["last_register_ok"] = None
        _STATUS["last_register_error"] = None
        _STATUS["last_progress_ts"] = None
        _STATUS["last_progress_ok"] = None
        _STATUS["last_progress_error"] = None
        _STATUS["last_stopped_ts"] = None
        _STATUS["last_stopped_ok"] = None
        _STATUS["last_stopped_error"] = None
        _STATUS["register_retry_failures"] = 0
        _STATUS["next_register_retry_ts"] = None
        _STATUS["last_register_backoff_sec"] = 0.0
        _STATUS["media_control_verified"] = None
        _REGISTER_RETRY_FAILURES = 0
        _NEXT_REGISTER_RETRY_TS = 0.0
        _LAST_STOPPED_SIGNATURE = ""
        _LAST_STOPPED_TS = 0.0
    _catalog_cache_clear()
    _start_worker()
    if _env_bool("RELAYTV_JELLYFIN_AUTO_REGISTER", False):
        try:
            register_receiver_once()
        except Exception:
            pass


def stop() -> None:
    _stop_worker()
    _stop_control_socket()
    with _LOCK:
        _STATUS["running"] = False
        _STATUS["connected"] = False


def mark_command(name: str) -> None:
    with _LOCK:
        _STATUS["last_command"] = str(name or "").strip() or None


def mark_error(msg: str) -> None:
    with _LOCK:
        _STATUS["last_error"] = _sanitize_error_text(msg) or None


def mark_heartbeat() -> None:
    with _LOCK:
        _STATUS["last_heartbeat_ts"] = int(time.time())
        _STATUS["connected"] = bool(_STATUS.get("running"))


def _status_with_sync_health(raw: dict[str, object]) -> dict[str, object]:
    out = dict(raw)
    now_ts = int(time.time())
    catalog_user_id, catalog_user_source = _effective_catalog_user(raw)
    out["catalog_user_id"] = catalog_user_id
    out["catalog_user_source"] = catalog_user_source
    with _CATALOG_CACHE_LOCK:
        out["catalog_cache_entries"] = len(_CATALOG_CACHE)
    out["catalog_cache_max_entries"] = _catalog_cache_max_entries()
    out["catalog_ttl_home_sec"] = _catalog_ttl_sec("home")
    out["catalog_ttl_search_sec"] = _catalog_ttl_sec("search")
    out["catalog_ttl_detail_sec"] = _catalog_ttl_sec("detail")
    out["catalog_ttl_metadata_sec"] = _catalog_ttl_sec("metadata")
    out["catalog_cache_clears"] = int(out.get("catalog_cache_clears") or 0)
    out["catalog_cache_last_cleared_ts"] = out.get("catalog_cache_last_cleared_ts")
    out["catalog_cache_last_cleared_reason"] = str(out.get("catalog_cache_last_cleared_reason") or "")
    out["stopped_dedupe_window_sec"] = _stopped_dedupe_sec()
    out["stopped_dedupe_enabled"] = bool(_stopped_dedupe_sec() > 0.0)
    out["complete_ratio"] = _complete_ratio()
    out["complete_remaining_sec"] = _complete_remaining_sec()
    progress_ts = int(out.get("last_progress_ts") or 0)
    stopped_ts = int(out.get("last_stopped_ts") or 0)
    register_ts = int(out.get("last_register_ts") or 0)
    auth_ts = int(out.get("last_auth_ts") or 0)
    last_sync_ts = max(progress_ts, stopped_ts)
    out["last_sync_ts"] = last_sync_ts or None
    out["last_sync_age_sec"] = (now_ts - last_sync_ts) if last_sync_ts > 0 else None

    if not bool(out.get("enabled")) or not bool(out.get("running")):
        out["sync_health"] = "disabled"
        out["sync_health_reason"] = "integration_disabled"
        return out

    if bool(out.get("connected")) and bool(out.get("last_register_ok")):
        out["sync_health"] = "ok"
        out["sync_health_reason"] = "connected"
        return out

    if bool(out.get("last_progress_ok")) is False:
        out["sync_health"] = "error"
        out["sync_health_reason"] = "progress_failed"
        return out
    if bool(out.get("last_stopped_ok")) is False:
        out["sync_health"] = "error"
        out["sync_health_reason"] = "stopped_failed"
        return out
    if bool(out.get("last_register_ok")) is False:
        out["sync_health"] = "error"
        out["sync_health_reason"] = "register_failed"
        return out
    if bool(out.get("auth_user_configured")) and bool(out.get("last_auth_ok")) is False:
        out["sync_health"] = "error"
        out["sync_health_reason"] = "auth_failed"
        return out

    if auth_ts == 0 and bool(out.get("auth_user_configured")):
        out["sync_health"] = "degraded"
        out["sync_health_reason"] = "awaiting_auth"
        return out
    if register_ts == 0 and bool(out.get("api_key_configured") or out.get("authenticated")):
        out["sync_health"] = "degraded"
        out["sync_health_reason"] = "awaiting_register"
        return out
    if last_sync_ts == 0:
        out["sync_health"] = "degraded"
        out["sync_health_reason"] = "awaiting_sync"
        return out

    out["sync_health"] = "degraded"
    out["sync_health_reason"] = "disconnected"
    return out


def status() -> dict[str, object]:
    with _LOCK:
        out = _status_with_sync_health(_STATUS)
    # Merged outside the lock: the socket module reads this status, so holding
    # ours while calling into it would invite a deadlock later.
    return _with_control_socket_status(out)


def _with_control_socket_status(out: dict[str, object]) -> dict[str, object]:
    ws = _control_socket_status()
    out["ws_enabled"] = bool(ws.get("enabled")) if ws else False
    out["ws_available"] = bool(ws.get("available")) if ws else False
    out["ws_connected"] = bool(ws.get("connected")) if ws else False
    out["ws_last_connect_ts"] = ws.get("last_connect_ts") if ws else None
    out["ws_last_error"] = ws.get("last_error") if ws else None
    out["ws_reconnects"] = int(ws.get("reconnects") or 0) if ws else 0
    out["ws_keepalive_sec"] = ws.get("keepalive_sec") if ws else None
    out["ws_commands_received"] = int(ws.get("commands_received") or 0) if ws else 0
    out["ws_commands_dropped"] = int(ws.get("commands_dropped") or 0) if ws else 0
    # The one field that answers "can I cast to this device?": the server only
    # offers a session that advertises media control *and* holds a live socket.
    out["cast_target_ready"] = bool(out.get("media_control_verified")) and bool(out.get("ws_connected"))
    return out


def detect_server_type(server_url: str, *, timeout_sec: float = 3.0) -> dict[str, object]:
    """Classify a media server as jellyfin or emby via /System/Info/Public.

    The endpoint is unauthenticated on both products. Jellyfin reports
    ProductName "Jellyfin Server" and modern Emby reports "Emby Server";
    Emby 3.5.x omits the field entirely, so a valid system-info payload
    without a recognizable ProductName counts as Emby.
    """
    base = str(server_url or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "server_type": "", "product_name": "", "version": "", "error": "no_server_url"}
    req = _urlrequest.Request(f"{base}/System/Info/Public", method="GET")
    try:
        with _urlrequest.urlopen(req, timeout=max(0.5, float(timeout_sec))) as resp:
            raw = (resp.read() or b"{}").decode("utf-8", "ignore")
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            raise RuntimeError("system info response is not an object")
        product = str(data.get("ProductName") or "").strip()
        version = str(data.get("Version") or "").strip()
        lowered = product.lower()
        if "jellyfin" in lowered:
            server_type = "jellyfin"
        elif "emby" in lowered:
            server_type = "emby"
        elif not product and (version or str(data.get("Id") or "").strip()):
            server_type = "emby"
        else:
            raise RuntimeError("system info response has no recognizable product")
        return {"ok": True, "server_type": server_type, "product_name": product, "version": version, "error": None}
    except Exception as e:
        return {"ok": False, "server_type": "", "product_name": "", "version": "", "error": _format_http_error(e)}


def _persist_server_type(server_type: str, product_name: str = "") -> None:
    st = str(server_type or "").strip().lower()
    if st not in ("jellyfin", "emby"):
        return
    with _LOCK:
        changed = _STATUS.get("server_type") != st
        _STATUS["server_type"] = st
        if product_name:
            _STATUS["server_product_name"] = str(product_name)
        elif changed:
            _STATUS["server_product_name"] = ""
    if not changed:
        return
    try:
        runtime_config.set_value("RELAYTV_JELLYFIN_SERVER_TYPE", st)
    except Exception:
        pass
    try:
        from .. import state as _state

        if hasattr(_state, "update_settings"):
            _state.update_settings({"jellyfin_server_type": st})
    except Exception:
        pass


def set_server_type(server_type: str) -> dict[str, object]:
    """Apply an operator-selected server type to the live receiver."""
    st = str(server_type or "").strip().lower()
    if st not in ("jellyfin", "emby"):
        raise ValueError("server_type must be jellyfin or emby")
    _persist_server_type(st)
    return status()


def _run_detection(server_url: str) -> dict[str, object]:
    result = detect_server_type(server_url)
    with _LOCK:
        _STATUS["last_detect_ts"] = int(time.time())
        _STATUS["last_detect_ok"] = bool(result.get("ok"))
        _STATUS["last_detect_error"] = result.get("error")
    if result.get("ok"):
        _persist_server_type(str(result.get("server_type") or ""), str(result.get("product_name") or ""))
    return result


def _maybe_retry_detection() -> None:
    """Re-probe from the worker when detection hasn't succeeded yet.

    Only once the server is provably reachable (auth or register worked),
    and at most every 300s, so an endpoint hidden by a proxy doesn't get
    hammered on every heartbeat.
    """
    st = status()
    if st.get("last_detect_ok") is True:
        return
    base = str(st.get("server_url") or "").strip()
    if not base:
        return
    if not (bool(st.get("authenticated")) or bool(st.get("last_register_ok"))):
        return
    last_ts = st.get("last_detect_ts")
    if last_ts and (time.time() - float(last_ts)) < 300.0:
        return
    _run_detection(base)


def connect(*, server_url: str, api_key: str | None = None, device_name: str | None = None, heartbeat_sec: int | None = None) -> dict[str, object]:
    """Configure and enable Jellyfin receiver runtime."""
    global _API_KEY, _AUTH_USERNAME, _AUTH_PASSWORD, _ACCESS_TOKEN, _AUTH_USER_ID, _AUTH_SESSION_ID
    global _REGISTER_RETRY_FAILURES, _NEXT_REGISTER_RETRY_TS, _LAST_STOPPED_SIGNATURE, _LAST_STOPPED_TS
    with _LOCK:
        _STATUS["enabled"] = True
        _STATUS["running"] = True
        _STATUS["server_url"] = str(server_url or "").strip()
        if device_name is not None:
            _STATUS["device_name"] = str(device_name or "").strip() or "RelayTV"
            _STATUS["client_name"] = str(_STATUS["device_name"])
            _STATUS["device_id"] = f"relaytv-{_STATUS['device_name'].lower().replace(' ', '-')}"
        if heartbeat_sec is not None:
            _STATUS["heartbeat_sec"] = max(2, int(heartbeat_sec))
        if api_key is not None:
            _API_KEY = str(api_key or "").strip()
        _AUTH_USERNAME = (runtime_config.snapshot().raw("RELAYTV_JELLYFIN_USERNAME") or "").strip()
        _AUTH_PASSWORD = (runtime_config.snapshot().raw("RELAYTV_JELLYFIN_PASSWORD") or "").strip()
        _ACCESS_TOKEN = ""
        _AUTH_USER_ID = ""
        _AUTH_SESSION_ID = ""
        _STATUS["api_key_configured"] = bool(_API_KEY)
        _STATUS["auth_user_configured"] = bool(_AUTH_USERNAME and _AUTH_PASSWORD)
        _STATUS["authenticated"] = False
        _STATUS["auth_user"] = _AUTH_USERNAME
        _STATUS["auth_user_id"] = ""
        _STATUS["auth_session_id"] = ""
        _STATUS["last_auth_ts"] = None
        _STATUS["last_auth_ok"] = None
        _STATUS["last_auth_error"] = None
        _STATUS["catalog_last_ok_ts"] = None
        _STATUS["catalog_last_error"] = None
        _STATUS["catalog_cache_hits"] = 0
        _STATUS["catalog_cache_misses"] = 0
        _STATUS["catalog_cache_clears"] = 0
        _STATUS["catalog_cache_last_cleared_ts"] = None
        _STATUS["catalog_cache_last_cleared_reason"] = None
        _STATUS["progress_success_count"] = 0
        _STATUS["progress_failure_count"] = 0
        _STATUS["stopped_success_count"] = 0
        _STATUS["stopped_failure_count"] = 0
        _STATUS["stopped_suppressed_count"] = 0
        _STATUS["last_progress_latency_ms"] = None
        _STATUS["last_stopped_latency_ms"] = None
        _STATUS["connected"] = bool(_STATUS["server_url"])
        _STATUS["last_error"] = None
        _STATUS["last_heartbeat_ts"] = int(time.time())
        _STATUS["register_retry_failures"] = 0
        _STATUS["next_register_retry_ts"] = None
        _STATUS["last_register_backoff_sec"] = 0.0
        _STATUS["media_control_verified"] = None
        _STATUS["last_stopped_ts"] = None
        _STATUS["last_stopped_ok"] = None
        _STATUS["last_stopped_error"] = None
        _REGISTER_RETRY_FAILURES = 0
        _NEXT_REGISTER_RETRY_TS = 0.0
        _LAST_STOPPED_SIGNATURE = ""
        _LAST_STOPPED_TS = 0.0
    _catalog_cache_clear()
    if str(server_url or "").strip():
        try:
            _run_detection(str(server_url or "").strip())
        except Exception:
            pass
    with _LOCK:
        out = dict(_STATUS)
    _start_worker()
    if _env_bool("RELAYTV_JELLYFIN_AUTO_REGISTER", False):
        try:
            register_receiver_once()
        except Exception:
            pass
    return out


def disconnect() -> dict[str, object]:
    global _REGISTER_RETRY_FAILURES, _NEXT_REGISTER_RETRY_TS, _ACCESS_TOKEN, _AUTH_USER_ID, _AUTH_SESSION_ID
    global _LAST_STOPPED_SIGNATURE, _LAST_STOPPED_TS
    _stop_worker()
    _stop_control_socket()
    with _LOCK:
        _STATUS["running"] = False
        _STATUS["connected"] = False
        _STATUS["last_command"] = None
        _STATUS["last_error"] = None
        _STATUS["authenticated"] = False
        _STATUS["auth_user_id"] = ""
        _STATUS["auth_session_id"] = ""
        _STATUS["register_retry_failures"] = 0
        _STATUS["next_register_retry_ts"] = None
        _STATUS["last_register_backoff_sec"] = 0.0
        _STATUS["media_control_verified"] = None
        _ACCESS_TOKEN = ""
        _AUTH_USER_ID = ""
        _AUTH_SESSION_ID = ""
        _REGISTER_RETRY_FAILURES = 0
        _NEXT_REGISTER_RETRY_TS = 0.0
        _LAST_STOPPED_SIGNATURE = ""
        _LAST_STOPPED_TS = 0.0
        out = dict(_STATUS)
    _catalog_cache_clear()
    return out


def set_device_identity(name: str) -> dict[str, object]:
    """Update runtime device/client display name for Jellyfin presence."""
    clean = str(name or "").strip() or "RelayTV"
    if len(clean) > 80:
        clean = clean[:80].strip() or "RelayTV"
    with _LOCK:
        _STATUS["device_name"] = clean
        _STATUS["client_name"] = clean
        _STATUS["device_id"] = f"relaytv-{clean.lower().replace(' ', '-')}"
        # Identity changed, drop session token and force fresh auth/register.
        global _ACCESS_TOKEN, _AUTH_USER_ID, _AUTH_SESSION_ID
        _ACCESS_TOKEN = ""
        _AUTH_USER_ID = ""
        _AUTH_SESSION_ID = ""
        _STATUS["authenticated"] = False
        _STATUS["auth_user_id"] = ""
        _STATUS["auth_session_id"] = ""
        _STATUS["connected"] = False
        _STATUS["last_auth_ok"] = None
        _STATUS["last_register_ok"] = None
    _catalog_cache_clear()
    _clear_register_retry_state()
    return status()


def refresh_catalog_profile() -> dict[str, object]:
    """Clear cached catalog rows/details after profile preference changes."""
    _catalog_cache_clear()
    _mark_catalog_cache_cleared(reason="profile_refresh")
    return status()


def clear_catalog_cache(*, reason: str = "manual") -> dict[str, object]:
    """Clear cached catalog rows/details for operator troubleshooting."""
    _catalog_cache_clear()
    _mark_catalog_cache_cleared(reason=reason)
    return status()


def api_key() -> str:
    """Internal accessor for integration command helpers."""
    with _LOCK:
        return str(_API_KEY or "")


def _active_token() -> str:
    with _LOCK:
        if _ACCESS_TOKEN:
            return str(_ACCESS_TOKEN)
        return str(_API_KEY or "")


def active_token() -> str:
    """Public accessor used by route helpers."""
    return _active_token()


def session_token() -> str:
    """Return the authenticated login-session token only."""
    with _LOCK:
        return str(_ACCESS_TOKEN or "")


def extract_item_id_from_url(url: str | None) -> str:
    u = str(url or "").strip()
    if not u:
        return ""
    try:
        parts = _urlparse.urlsplit(u)
        q = dict(_urlparse.parse_qsl(parts.query, keep_blank_values=True))
        for key in ("itemId", "ItemId", "item_id", "id", "Id"):
            val = str(q.get(key) or "").strip()
            if val:
                return val
        segs = [s for s in (parts.path or "").split("/") if s]
        for idx, seg in enumerate(segs):
            low = seg.lower()
            if low == "videos" and (idx + 1) < len(segs):
                return str(segs[idx + 1] or "").strip()
            if low == "items" and (idx + 1) < len(segs):
                return str(segs[idx + 1] or "").strip()
    except Exception:
        return ""
    return ""


def _build_emby_headers(*, token: str = "") -> dict[str, str]:
    st = status()
    client_name = str(st.get("client_name") or "RelayTV")
    device_name = str(st.get("device_name") or "RelayTV")
    device_id = str(st.get("device_id") or "relaytv")
    client_version = str(st.get("client_version") or "1.0")
    out: dict[str, str] = {}
    tok = str(token or "").strip()
    if tok:
        out["X-Emby-Token"] = tok
        out["Authorization"] = f'MediaBrowser Token="{tok}"'
    auth = (
        f'MediaBrowser Client="{client_name}", '
        f'Device="{device_name}", '
        f'DeviceId="{device_id}", '
        f'Version="{client_version}"'
    )
    if tok:
        auth = f'{auth}, Token="{tok}"'
    out["X-Emby-Authorization"] = auth
    return out


def get_item_metadata(item_id: str, *, token_override: str = "", server_url_override: str = "") -> dict[str, object]:
    iid = str(item_id or "").strip()
    st = status()
    base = str(server_url_override or st.get("server_url") or "").strip().rstrip("/")
    if not iid or not base:
        return {}
    token = str(token_override or _active_token() or "").strip()
    user_id = str(st.get("catalog_user_id") or st.get("auth_user_id") or "").strip()
    token_key = hashlib.sha1(token.encode("utf-8", "ignore")).hexdigest()[:12] if token else "-"
    cache_key = f"meta:{base}:{user_id}:{iid}:{token_key}"
    cached = _catalog_cache_get(cache_key)
    if isinstance(cached, dict):
        _mark_catalog_ok()
        return _attach_thumb(dict(cached))

    client_name = str(st.get("client_name") or "RelayTV")
    device_name = str(st.get("device_name") or "RelayTV")
    device_id = str(st.get("device_id") or "relaytv")
    client_version = str(st.get("client_version") or "1.0")

    def _headers() -> dict[str, str]:
        out: dict[str, str] = {}
        if token:
            out["X-Emby-Token"] = token
            out["Authorization"] = f'MediaBrowser Token="{token}"'
        auth = (
            f'MediaBrowser Client="{client_name}", '
            f'Device="{device_name}", '
            f'DeviceId="{device_id}", '
            f'Version="{client_version}"'
        )
        if token:
            auth = f'{auth}, Token="{token}"'
        out["X-Emby-Authorization"] = auth
        return out

    urls: list[str] = []
    if user_id:
        urls.append(f"{base}/Users/{_urlparse.quote(user_id)}/Items/{_urlparse.quote(iid)}")
    urls.append(f"{base}/Items/{_urlparse.quote(iid)}")
    last_err = ""
    for url in urls:
        req = _urlrequest.Request(url, method="GET")
        for k, v in _headers().items():
            req.add_header(k, v)
        try:
            with _urlrequest.urlopen(req, timeout=float(os.getenv("RELAYTV_JELLYFIN_ITEM_TIMEOUT_SEC", "5"))) as resp:
                raw = (resp.read() or b"{}").decode("utf-8", "ignore")
            data = json.loads(raw) if raw.strip() else {}
            if not isinstance(data, dict):
                continue
            name = str(data.get("Name") or "").strip()
            item_type = str(data.get("Type") or "").strip().lower()
            series_name = str(data.get("SeriesName") or "").strip()
            season_name = str(data.get("SeasonName") or "").strip()
            try:
                season_num = int(data.get("ParentIndexNumber")) if data.get("ParentIndexNumber") is not None else None
            except Exception:
                season_num = None
            try:
                episode_num = int(data.get("IndexNumber")) if data.get("IndexNumber") is not None else None
            except Exception:
                episode_num = None
            year = ""
            try:
                py = data.get("ProductionYear")
                if py is not None:
                    year = str(int(py))
            except Exception:
                year = ""
            if not year:
                premiere = str(data.get("PremiereDate") or "").strip()
                if len(premiere) >= 4 and premiere[:4].isdigit():
                    year = premiere[:4]

            display_title = name
            display_channel = ""
            if item_type == "episode":
                display_title = series_name or name or "Episode"
                ep_parts: list[str] = []
                if season_num is not None and episode_num is not None:
                    ep_parts.append(f"S{season_num:02d}E{episode_num:02d}")
                elif episode_num is not None:
                    ep_parts.append(f"E{episode_num}")
                if name and name != display_title:
                    ep_parts.append(name)
                elif season_name and season_name != display_title:
                    ep_parts.append(season_name)
                display_channel = " · ".join([p for p in ep_parts if p])
            elif item_type == "movie":
                display_title = name or "Movie"
                display_channel = f"Movie · {year}" if year else "Movie"
            elif item_type:
                display_title = name or item_type.title()
                display_channel = item_type.title()

            image_tags = data.get("ImageTags") if isinstance(data.get("ImageTags"), dict) else {}
            primary_tag = str(image_tags.get("Primary") or "").strip()
            thumb = f"{base}/Items/{_urlparse.quote(iid)}/Images/Primary"
            q: dict[str, str] = {}
            if primary_tag:
                q["tag"] = primary_tag
            if token:
                q["api_key"] = token
            if q:
                thumb = f"{thumb}?{_urlparse.urlencode(q)}"
            user_data = data.get("UserData") if isinstance(data.get("UserData"), dict) else {}
            pos_ticks = user_data.get("PlaybackPositionTicks")
            try:
                resume = float(pos_ticks) / 10_000_000.0 if pos_ticks is not None else None
            except Exception:
                resume = None
            run_ticks = data.get("RunTimeTicks")
            try:
                duration = float(run_ticks) / 10_000_000.0 if run_ticks is not None else None
            except Exception:
                duration = None
            _audio_streams, _subtitle_streams, audio_language, subtitle_language = _extract_stream_languages(data)
            out = {
                "item_id": iid,
                "title": display_title,
                "channel": display_channel,
                "thumbnail": thumb,
                "resume_pos": resume,
                "duration": duration,
                "type": item_type,
                "year": year,
                "audio_language": audio_language,
                "subtitle_language": subtitle_language,
            }
            _catalog_cache_set(cache_key, dict(out), ttl_sec=_catalog_ttl_sec("metadata"))
            _mark_catalog_ok()
            return _attach_thumb(out)
        except Exception as e:
            last_err = _format_http_error(e)
            _mark_catalog_error(last_err)
            continue
    if last_err:
        mark_error(last_err)
    return {}


def _get_json(url: str, *, timeout: float = 5.0, token: str = "") -> object:
    req = _urlrequest.Request(url, method="GET")
    for k, v in _build_emby_headers(token=token).items():
        req.add_header(k, v)
    with _urlrequest.urlopen(req, timeout=timeout) as resp:
        raw = (resp.read() or b"{}").decode("utf-8", "ignore")
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _ticks_to_seconds(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value) / 10_000_000.0
    except Exception:
        return None


def _safe_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _stream_language(value: object) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _extract_stream_languages(data: dict[str, object]) -> tuple[list[dict[str, object]], list[dict[str, object]], str, str]:
    raw = data.get("MediaStreams")
    streams = raw if isinstance(raw, list) else []
    default_audio_idx = _safe_int(data.get("DefaultAudioStreamIndex"))
    default_sub_idx = _safe_int(data.get("DefaultSubtitleStreamIndex"))

    audio_streams: list[dict[str, object]] = []
    subtitle_streams: list[dict[str, object]] = []
    for s in streams:
        if not isinstance(s, dict):
            continue
        idx = _safe_int(s.get("Index"))
        if idx is None:
            continue
        stype = str(s.get("Type") or "").strip().lower()
        if stype not in ("audio", "subtitle"):
            continue
        entry: dict[str, object] = {
            "index": idx,
            "language": _stream_language(s.get("Language")),
            "display": str(s.get("DisplayTitle") or s.get("Title") or "").strip(),
            "is_default": bool(s.get("IsDefault")),
        }
        if stype == "audio":
            if default_audio_idx is not None and idx == default_audio_idx:
                entry["is_default"] = True
            audio_streams.append(entry)
        else:
            if default_sub_idx is not None and idx == default_sub_idx:
                entry["is_default"] = True
            entry["is_forced"] = bool(s.get("IsForced"))
            entry["is_external"] = bool(s.get("IsExternal"))
            subtitle_streams.append(entry)

    def _selected_lang(rows: list[dict[str, object]]) -> str:
        for row in rows:
            if bool(row.get("is_default")) and str(row.get("language") or "").strip():
                return str(row.get("language") or "").strip()
        for row in rows:
            if str(row.get("language") or "").strip():
                return str(row.get("language") or "").strip()
        return ""

    return audio_streams, subtitle_streams, _selected_lang(audio_streams), _selected_lang(subtitle_streams)


def _item_year(data: dict[str, object]) -> str:
    year = ""
    try:
        py = data.get("ProductionYear")
        if py is not None:
            year = str(int(py))
    except Exception:
        year = ""
    if year:
        return year
    premiere = str(data.get("PremiereDate") or "").strip()
    if len(premiere) >= 4 and premiere[:4].isdigit():
        return premiere[:4]
    return ""


def _extract_media_source_id(data: dict[str, object]) -> str:
    direct = str(
        data.get("MediaSourceId")
        or data.get("mediaSourceId")
        or data.get("PrimaryMediaSourceId")
        or data.get("primaryMediaSourceId")
        or ""
    ).strip()
    if direct:
        return direct
    media_sources = data.get("MediaSources")
    if isinstance(media_sources, list):
        for ms in media_sources:
            if not isinstance(ms, dict):
                continue
            mid = str(ms.get("Id") or ms.get("id") or ms.get("MediaSourceId") or ms.get("mediaSourceId") or "").strip()
            if mid:
                return mid
    return ""


def _normalize_catalog_item(data: dict[str, object], *, base: str, token: str) -> dict[str, object]:
    iid = str(data.get("Id") or "").strip()
    item_type = str(data.get("Type") or "").strip().lower()
    name = str(data.get("Name") or "").strip()
    series_name = str(data.get("SeriesName") or "").strip()
    try:
        season_num = int(data.get("ParentIndexNumber")) if data.get("ParentIndexNumber") is not None else None
    except Exception:
        season_num = None
    try:
        episode_num = int(data.get("IndexNumber")) if data.get("IndexNumber") is not None else None
    except Exception:
        episode_num = None

    title = name or "Item"
    subtitle = ""
    year = _item_year(data)
    if item_type == "episode":
        title = series_name or name or "Episode"
        parts: list[str] = []
        if season_num is not None and episode_num is not None:
            parts.append(f"S{season_num:02d}E{episode_num:02d}")
        elif episode_num is not None:
            parts.append(f"E{episode_num}")
        if name and name != title:
            parts.append(name)
        subtitle = " · ".join([p for p in parts if p])
    elif item_type == "movie":
        title = name or "Movie"
        subtitle = f"Movie · {year}" if year else "Movie"
    elif item_type == "series":
        title = name or "Series"
        subtitle = f"Series · {year}" if year else "Series"
    elif item_type:
        subtitle = item_type.title()

    tags = data.get("ImageTags") if isinstance(data.get("ImageTags"), dict) else {}
    primary_tag = str(tags.get("Primary") or data.get("PrimaryImageTag") or "").strip()
    backdrop_tags = data.get("BackdropImageTags") if isinstance(data.get("BackdropImageTags"), list) else []
    backdrop_tag = str(backdrop_tags[0] if backdrop_tags else "").strip()
    thumb = ""
    backdrop = ""
    if iid and base:
        thumb = f"{base}/Items/{_urlparse.quote(iid)}/Images/Primary"
        q: dict[str, str] = {}
        if primary_tag:
            q["tag"] = primary_tag
        if token:
            q["api_key"] = token
        if q:
            thumb = f"{thumb}?{_urlparse.urlencode(q)}"
        if backdrop_tag:
            backdrop = f"{base}/Items/{_urlparse.quote(iid)}/Images/Backdrop/0"
            backdrop_q: dict[str, str] = {"tag": backdrop_tag}
            if token:
                backdrop_q["api_key"] = token
            backdrop = f"{backdrop}?{_urlparse.urlencode(backdrop_q)}"

    user_data = data.get("UserData") if isinstance(data.get("UserData"), dict) else {}
    resume_pos = _ticks_to_seconds(user_data.get("PlaybackPositionTicks"))
    runtime_sec = _ticks_to_seconds(data.get("RunTimeTicks"))
    progress_percent = 0.0
    try:
        reported_progress = user_data.get("PlayedPercentage")
        if reported_progress is not None:
            progress_percent = max(0.0, min(100.0, float(reported_progress)))
        elif resume_pos is not None and runtime_sec is not None and runtime_sec > 0:
            progress_percent = max(0.0, min(100.0, (resume_pos / runtime_sec) * 100.0))
    except Exception:
        progress_percent = 0.0
    audio_streams, subtitle_streams, audio_language, subtitle_language = _extract_stream_languages(data)
    video_codec = ""
    video_profile = ""
    video_width: int | None = None
    video_height: int | None = None
    video_bit_depth: int | None = None
    video_fps: float | None = None
    video_bitrate: int | None = None
    media_streams = data.get("MediaStreams")
    if isinstance(media_streams, list):
        for stream in media_streams:
            if not isinstance(stream, dict):
                continue
            if str(stream.get("Type") or "").strip().lower() != "video":
                continue
            video_codec = str(stream.get("Codec") or "").strip().lower()
            video_profile = str(stream.get("Profile") or "").strip()
            video_width = _safe_int(stream.get("Width"))
            video_height = _safe_int(stream.get("Height"))
            video_bit_depth = _safe_int(stream.get("BitDepth"))
            try:
                rf = stream.get("RealFrameRate")
                if rf is not None:
                    video_fps = float(rf)
            except Exception:
                video_fps = None
            video_bitrate = _safe_int(stream.get("BitRate"))
            break
    media_source_id = _extract_media_source_id(data)

    out = {
        "item_id": iid,
        "title": title,
        "subtitle": subtitle,
        "type": item_type,
        "series_name": series_name,
        "series_id": str(data.get("SeriesId") or "").strip(),
        "thumbnail": thumb,
        "poster": thumb,
        "backdrop": backdrop,
        "year": year,
        "runtime_sec": runtime_sec,
        "resume_pos": resume_pos,
        "progress_percent": round(progress_percent, 2),
        "is_played": bool(user_data.get("Played")),
        "is_favorite": bool(user_data.get("IsFavorite")),
        "media_source_id": media_source_id,
        "overview": str(data.get("Overview") or "").strip(),
        "season_number": season_num,
        "episode_number": episode_num,
        "audio_streams": audio_streams,
        "subtitle_streams": subtitle_streams,
        "audio_language": audio_language,
        "subtitle_language": subtitle_language,
        "video_codec": video_codec,
        "video_profile": video_profile,
        "video_width": video_width,
        "video_height": video_height,
        "video_bit_depth": video_bit_depth,
        "video_fps": video_fps,
        "video_bitrate": video_bitrate,
    }
    _attach_thumb(out)
    if out.get("thumbnail_local"):
        out["poster_local"] = out["thumbnail_local"]
    return out


def _extract_items(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        items = payload.get("Items")
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
    return []


def _extract_total_count(payload: object, default_count: int = 0) -> int:
    if isinstance(payload, dict):
        try:
            total = int(payload.get("TotalRecordCount"))
            if total >= 0:
                return total
        except Exception:
            pass
    return max(0, int(default_count))


def _catalog_base_token_user() -> tuple[str, str, str]:
    st = status()
    base = str(st.get("server_url") or "").strip().rstrip("/")
    token = _active_token()
    user_id = str(st.get("catalog_user_id") or st.get("auth_user_id") or "").strip()
    return base, token, user_id


def get_item_detail(item_id: str, *, refresh: bool = False) -> dict[str, object]:
    iid = str(item_id or "").strip()
    base, token, user_id = _catalog_base_token_user()
    if not iid or not base:
        return {}
    cache_key = f"detail:{base}:{user_id}:{iid}"
    if not refresh:
        cached = _catalog_cache_get(cache_key)
        if isinstance(cached, dict):
            _mark_catalog_ok()
            return _attach_thumb(dict(cached))
    quoted = _urlparse.quote(iid)
    fields = (
        "Overview,ImageTags,BackdropImageTags,ProductionYear,PremiereDate,RunTimeTicks,UserData,SeriesId,"
        "SeriesName,ParentIndexNumber,IndexNumber,MediaStreams,DefaultAudioStreamIndex,DefaultSubtitleStreamIndex"
    )
    candidates: list[str] = []
    if user_id:
        candidates.append(f"{base}/Users/{_urlparse.quote(user_id)}/Items/{quoted}?Fields={fields}")
    # Some Jellyfin deployments require UserId context for /Items/{id};
    # include it explicitly to avoid server-side Guid-empty errors.
    if user_id:
        candidates.append(f"{base}/Items/{quoted}?UserId={_urlparse.quote(user_id)}&Fields={fields}")
    else:
        candidates.append(f"{base}/Items/{quoted}?Fields={fields}")
    timeout = float(os.getenv("RELAYTV_JELLYFIN_ITEM_TIMEOUT_SEC", "5"))
    for url in candidates:
        try:
            payload = _get_json(url, timeout=timeout, token=token)
            if isinstance(payload, dict) and payload:
                item = _normalize_catalog_item(payload, base=base, token=token)
                if str(item.get("item_id") or "").strip():
                    _catalog_cache_set(cache_key, dict(item), ttl_sec=_catalog_ttl_sec("detail"))
                    _mark_catalog_ok()
                    return _attach_thumb(item)
        except Exception as e:
            _mark_catalog_error(str(e))
            continue
    return {}


def resolve_playback_url(
    item_id: str,
    *,
    prefer_transcode: bool = False,
    media_source_id: str = "",
    audio_stream_index: str = "",
    subtitle_stream_index: str = "",
    max_height: int | None = None,
    max_streaming_bitrate: int | None = None,
) -> dict[str, object]:
    """
    Ask Jellyfin for a playable URL for an item, preferring transcode when requested.
    Returns: {"url": str, "method": str, "media_source_id": str}
    """
    iid = str(item_id or "").strip()
    base, token, user_id = _catalog_base_token_user()
    if not iid or not base:
        return {"url": "", "method": "none", "media_source_id": ""}
    mid = str(media_source_id or "").strip()
    aidx = str(audio_stream_index or "").strip()
    sidx = str(subtitle_stream_index or "").strip()
    h = int(max_height or 0) if max_height else 0
    bps = int(max_streaming_bitrate or 0) if max_streaming_bitrate else 0
    cache_key = f"playback:{base}:{user_id}:{iid}:{int(bool(prefer_transcode))}:{mid}:{aidx}:{sidx}:{h}:{bps}"
    cached = _catalog_cache_get(cache_key)
    if isinstance(cached, dict):
        out = dict(cached)
        if isinstance(out.get("url"), str) and out.get("url"):
            return out

    path = f"/Items/{_urlparse.quote(iid)}/PlaybackInfo"
    endpoint = f"{base}{path}"
    timeout = float(os.getenv("RELAYTV_JELLYFIN_ITEM_TIMEOUT_SEC", "5"))

    payload: dict[str, object] = {
        "UserId": user_id or None,
        "EnableDirectPlay": not bool(prefer_transcode),
        "EnableDirectStream": not bool(prefer_transcode),
        "EnableTranscoding": True,
    }
    if mid:
        payload["MediaSourceId"] = mid
    if aidx != "":
        try:
            payload["AudioStreamIndex"] = int(aidx)
        except Exception:
            pass
    if sidx != "":
        try:
            payload["SubtitleStreamIndex"] = int(sidx)
        except Exception:
            pass
    if h > 0:
        payload["MaxHeight"] = h
    if bps > 0:
        payload["MaxStreamingBitrate"] = bps

    def _request(method: str) -> object:
        req_url = endpoint
        if user_id:
            sep = "&" if "?" in req_url else "?"
            req_url = f"{req_url}{sep}UserId={_urlparse.quote(user_id)}"
        if method == "POST":
            req = _urlrequest.Request(req_url, data=json.dumps(payload).encode("utf-8"), method="POST")
            req.add_header("Content-Type", "application/json")
        else:
            req = _urlrequest.Request(req_url, method="GET")
        for k, v in _build_emby_headers(token=token).items():
            req.add_header(k, v)
        with _urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = (resp.read() or b"{}").decode("utf-8", "ignore")
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {}

    data: dict[str, object] = {}
    last_err = ""
    for method in ("POST", "GET"):
        try:
            raw = _request(method)
            if isinstance(raw, dict):
                data = raw
                if data:
                    break
        except Exception as e:
            last_err = _format_http_error(e)
            _mark_catalog_error(last_err)
            continue

    media_sources = data.get("MediaSources") if isinstance(data, dict) and isinstance(data.get("MediaSources"), list) else []
    selected = media_sources[0] if media_sources and isinstance(media_sources[0], dict) else {}

    if prefer_transcode:
        for source in media_sources:
            if not isinstance(source, dict):
                continue
            if source.get("TranscodingUrl"):
                selected = source
                break

    rel_url = ""
    method = "none"
    if isinstance(selected, dict):
        if prefer_transcode:
            rel_url = str(selected.get("TranscodingUrl") or "").strip()
            if rel_url:
                method = "transcode"
        elif not rel_url:
            rel_url = str(selected.get("DirectStreamUrl") or "").strip()
            if rel_url:
                method = "direct"

    if rel_url and rel_url.startswith("/"):
        rel_url = f"{base}{rel_url}"

    out_mid = str(
        (selected.get("Id") if isinstance(selected, dict) else "")
        or (selected.get("MediaSourceId") if isinstance(selected, dict) else "")
        or mid
        or ""
    ).strip()
    out = {"url": rel_url, "method": method, "media_source_id": out_mid}
    if rel_url:
        _catalog_cache_set(cache_key, dict(out), ttl_sec=_catalog_ttl_sec("detail"))
        _mark_catalog_ok()
    elif last_err:
        mark_error(last_err)
    return out


def _episode_rank(season_num: int | None, episode_num: int | None) -> int:
    if season_num is None or episode_num is None:
        return -1
    return (int(season_num) * 100000) + int(episode_num)


def _adjacent_from_episodes(
    episodes: list[dict[str, object]],
    *,
    cur_id: str,
    cur_season: int,
    cur_episode: int,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    cur_rank = _episode_rank(cur_season, cur_episode)
    cur_idx = -1
    if cur_id:
        for idx, ep in enumerate(episodes):
            if str(ep.get("item_id") or "").strip() == cur_id:
                cur_idx = idx
                break

    prev: dict[str, object] | None = None
    nxt: dict[str, object] | None = None
    if cur_idx >= 0:
        if cur_idx > 0:
            prev = episodes[cur_idx - 1]
        if cur_idx + 1 < len(episodes):
            nxt = episodes[cur_idx + 1]
        return prev, nxt

    for ep in episodes:
        rank = _episode_rank(_safe_int(ep.get("season_number")), _safe_int(ep.get("episode_number")))
        if rank < 0:
            continue
        if rank < cur_rank:
            prev = ep
        elif nxt is None and rank > cur_rank:
            nxt = ep
    return prev, nxt


def _season_number_from_row(row: dict[str, object]) -> int | None:
    for key in ("IndexNumber", "ParentIndexNumber", "SeasonNumber"):
        n = _safe_int(row.get(key))
        if n is not None:
            return int(n)
    name = str(row.get("Name") or "").strip()
    if not name:
        return None
    m = re.search(r"(?:season|s)\s*([0-9]{1,4})", name, re.IGNORECASE)
    if m:
        return _safe_int(m.group(1))
    m = re.search(r"\b([0-9]{1,4})\b", name)
    if m:
        return _safe_int(m.group(1))
    return None


def _fetch_series_seasons(
    *,
    base: str,
    series_id: str,
    user_id: str,
    timeout: float,
    token: str,
) -> list[dict[str, object]]:
    sid = _urlparse.quote(series_id)
    uid = _urlparse.quote(user_id) if user_id else ""
    candidates: list[str] = []
    if uid:
        candidates.append(f"{base}/Shows/{sid}/Seasons?UserId={uid}")
    candidates.append(f"{base}/Shows/{sid}/Seasons")
    q = _urlparse.urlencode(
        {
            "ParentId": series_id,
            "IncludeItemTypes": "Season",
            "Recursive": "true",
            "Limit": "300",
            "SortBy": "SortName",
            "SortOrder": "Ascending",
            "Fields": "IndexNumber,ParentIndexNumber",
        }
    )
    if uid:
        candidates.append(f"{base}/Users/{uid}/Items?{q}")
    candidates.append(f"{base}/Items?{q}")

    by_num: dict[int, dict[str, object]] = {}
    for url in candidates:
        try:
            rows = _extract_items(_get_json(url, timeout=timeout, token=token))
        except Exception as e:
            _mark_catalog_error(str(e))
            continue
        for row in rows:
            season_num = _season_number_from_row(row)
            if season_num is None:
                continue
            season_id = str(row.get("Id") or row.get("id") or "").strip()
            cur = by_num.get(season_num)
            if not isinstance(cur, dict):
                by_num[season_num] = {"season_number": season_num, "season_id": season_id}
            elif not str(cur.get("season_id") or "").strip() and season_id:
                cur["season_id"] = season_id
    return [by_num[k] for k in sorted(by_num.keys())]


def _fetch_series_episodes_for_season(
    *,
    base: str,
    series_id: str,
    season_id: str,
    user_id: str,
    fields: str,
    timeout: float,
    token: str,
) -> list[dict[str, object]]:
    sid = _urlparse.quote(series_id)
    seas = _urlparse.quote(season_id)
    uid = _urlparse.quote(user_id) if user_id else ""
    fields_q = _urlparse.quote(fields)
    candidates: list[str] = []
    if uid:
        candidates.append(f"{base}/Shows/{sid}/Episodes?SeasonId={seas}&UserId={uid}&Limit=5000&Fields={fields_q}")
    candidates.append(f"{base}/Shows/{sid}/Episodes?SeasonId={seas}&Limit=5000&Fields={fields_q}")
    q = _urlparse.urlencode(
        {
            "ParentId": season_id,
            "IncludeItemTypes": "Episode",
            "Recursive": "true",
            "SortBy": "ParentIndexNumber,IndexNumber",
            "SortOrder": "Ascending",
            "Limit": "5000",
            "Fields": fields,
        }
    )
    if uid:
        candidates.append(f"{base}/Users/{uid}/Items?{q}")
    candidates.append(f"{base}/Items?{q}")

    rows_by_id: dict[str, dict[str, object]] = {}
    for url in candidates:
        try:
            rows = _extract_items(_get_json(url, timeout=timeout, token=token))
        except Exception as e:
            _mark_catalog_error(str(e))
            continue
        for row in rows:
            rid = str(row.get("Id") or row.get("id") or "").strip()
            if rid and rid not in rows_by_id:
                rows_by_id[rid] = row
        if rows_by_id:
            break
    return list(rows_by_id.values())


def _fetch_series_episodes_for_season_number(
    *,
    base: str,
    series_id: str,
    season_number: int,
    user_id: str,
    fields: str,
    timeout: float,
    token: str,
) -> list[dict[str, object]]:
    uid = _urlparse.quote(user_id) if user_id else ""
    q = _urlparse.urlencode(
        {
            "IncludeItemTypes": "Episode",
            "Recursive": "true",
            "SeriesId": series_id,
            "ParentIndexNumber": str(int(season_number)),
            "SortBy": "ParentIndexNumber,IndexNumber",
            "SortOrder": "Ascending",
            "Limit": "5000",
            "Fields": fields,
        }
    )
    candidates: list[str] = []
    if uid:
        candidates.append(f"{base}/Users/{uid}/Items?{q}")
    candidates.append(f"{base}/Items?{q}")

    rows_by_id: dict[str, dict[str, object]] = {}
    for url in candidates:
        try:
            rows = _extract_items(_get_json(url, timeout=timeout, token=token))
        except Exception as e:
            _mark_catalog_error(str(e))
            continue
        for row in rows:
            rid = str(row.get("Id") or row.get("id") or "").strip()
            if rid and rid not in rows_by_id:
                rows_by_id[rid] = row
        if rows_by_id:
            break
    return list(rows_by_id.values())


def get_adjacent_episodes(item_id: str, *, refresh: bool = False) -> dict[str, object]:
    iid = str(item_id or "").strip()
    base, token, user_id = _catalog_base_token_user()
    if not iid or not base:
        return {"prev": None, "next": None}
    cache_key = f"adjacent:{base}:{user_id}:{iid}"
    if not refresh:
        cached = _catalog_cache_get(cache_key)
        if isinstance(cached, dict):
            prev = cached.get("prev") if isinstance(cached.get("prev"), dict) else None
            nxt = cached.get("next") if isinstance(cached.get("next"), dict) else None
            _mark_catalog_ok()
            return {
                "prev": _attach_thumb(dict(prev)) if isinstance(prev, dict) else None,
                "next": _attach_thumb(dict(nxt)) if isinstance(nxt, dict) else None,
            }

    detail = get_item_detail(iid, refresh=refresh)
    if not isinstance(detail, dict):
        return {"prev": None, "next": None}
    if str(detail.get("type") or "").strip().lower() != "episode":
        payload = {"prev": None, "next": None}
        _catalog_cache_set(cache_key, payload, ttl_sec=_catalog_ttl_sec("detail"))
        return payload

    series_id = str(detail.get("series_id") or "").strip()
    series_name = str(detail.get("series_name") or detail.get("title") or "").strip()
    cur_season = _safe_int(detail.get("season_number"))
    cur_episode = _safe_int(detail.get("episode_number"))
    if cur_season is None or cur_episode is None:
        payload = {"prev": None, "next": None}
        _catalog_cache_set(cache_key, payload, ttl_sec=_catalog_ttl_sec("detail"))
        return payload

    fields = (
        "Overview,ImageTags,BackdropImageTags,ProductionYear,PremiereDate,RunTimeTicks,UserData,SeriesId,"
        "SeriesName,ParentIndexNumber,IndexNumber"
    )
    candidates: list[str] = []
    # Primary fetch path: series-scoped episode listing.
    if series_id:
        if user_id:
            candidates.append(
                f"{base}/Shows/{_urlparse.quote(series_id)}/Episodes?"
                f"UserId={_urlparse.quote(user_id)}&Limit=5000&Fields={_urlparse.quote(fields)}"
            )
        candidates.append(
            f"{base}/Shows/{_urlparse.quote(series_id)}/Episodes?"
            f"Limit=5000&Fields={_urlparse.quote(fields)}"
        )
        # Fallback fetch path: items query with explicit SeriesId.
        q_series = _urlparse.urlencode(
            {
                "IncludeItemTypes": "Episode",
                "Recursive": "true",
                "SeriesId": series_id,
                "SortBy": "ParentIndexNumber,IndexNumber",
                "SortOrder": "Ascending",
                "Limit": "5000",
                "Fields": fields,
            }
        )
        if user_id:
            candidates.append(f"{base}/Users/{_urlparse.quote(user_id)}/Items?{q_series}")
        candidates.append(f"{base}/Items?{q_series}")

    # Last-resort fallback when SeriesId is missing/unstable on some libraries:
    # search episode items by series name and filter to exact series match later.
    if series_name:
        q_name = _urlparse.urlencode(
            {
                "IncludeItemTypes": "Episode",
                "Recursive": "true",
                "SearchTerm": series_name,
                "SortBy": "ParentIndexNumber,IndexNumber",
                "SortOrder": "Ascending",
                "Limit": "5000",
                "Fields": fields,
            }
        )
        if user_id:
            candidates.append(f"{base}/Users/{_urlparse.quote(user_id)}/Items?{q_name}")
        candidates.append(f"{base}/Items?{q_name}")

    timeout = float(os.getenv("RELAYTV_JELLYFIN_ITEM_TIMEOUT_SEC", "5"))
    rows_by_id: dict[str, dict[str, object]] = {}
    for url in candidates:
        try:
            payload = _get_json(url, timeout=timeout, token=token)
            rows = _extract_items(payload)
            for row in rows:
                rid = str(row.get("Id") or row.get("id") or "").strip()
                if rid and rid not in rows_by_id:
                    rows_by_id[rid] = row
        except Exception as e:
            _mark_catalog_error(str(e))
            continue

    rows = list(rows_by_id.values())
    raw_episodes = [_normalize_catalog_item(row, base=base, token=token) for row in rows]
    wanted_series_name = series_name.lower().strip()
    def _episode_matches(ep: dict[str, object]) -> bool:
        if not str(ep.get("item_id") or "").strip():
            return False
        if str(ep.get("type") or "").strip().lower() != "episode":
            return False
        if _safe_int(ep.get("season_number")) is None or _safe_int(ep.get("episode_number")) is None:
            return False
        if series_id and str(ep.get("series_id") or "").strip() == series_id:
            return True
        if wanted_series_name and str(ep.get("series_name") or "").strip().lower() == wanted_series_name:
            return True
        return False

    def _episodes_sorted(rows_in: list[dict[str, object]]) -> list[dict[str, object]]:
        out = [_normalize_catalog_item(row, base=base, token=token) for row in rows_in]
        out = [ep for ep in out if _episode_matches(ep)]
        out.sort(
            key=lambda ep: (
                int(_safe_int(ep.get("season_number")) or 0),
                int(_safe_int(ep.get("episode_number")) or 0),
                str(ep.get("item_id") or ""),
            )
        )
        return out

    episodes = [ep for ep in raw_episodes if _episode_matches(ep)]
    episodes.sort(
        key=lambda ep: (
            int(_safe_int(ep.get("season_number")) or 0),
            int(_safe_int(ep.get("episode_number")) or 0),
            str(ep.get("item_id") or ""),
        )
    )
    if not episodes:
        payload = {"prev": None, "next": None}
        _catalog_cache_set(cache_key, payload, ttl_sec=_catalog_ttl_sec("detail"))
        return payload

    cur_id = str(detail.get("item_id") or "").strip()
    prev, nxt = _adjacent_from_episodes(episodes, cur_id=cur_id, cur_season=int(cur_season), cur_episode=int(cur_episode))

    # Some Jellyfin libraries return only the current season on generic episode listings.
    # If we're at a season boundary and a side is missing, probe adjacent seasons explicitly.
    if series_id and (prev is None or nxt is None):
        seasons = _fetch_series_seasons(
            base=base,
            series_id=series_id,
            user_id=user_id,
            timeout=timeout,
            token=token,
        )
        if not seasons:
            probe = max(2, min(24, int(float(os.getenv("RELAYTV_JELLYFIN_ADJACENT_SEASON_PROBE_MAX", "8") or "8"))))
            lo = max(1, int(cur_season) - probe)
            hi = int(cur_season) + probe
            seasons = [{"season_number": sn, "season_id": ""} for sn in range(lo, hi + 1) if sn != int(cur_season)]

        fetched_seasons: set[str] = set()
        for need_prev in (True, False):
            if need_prev and prev is not None:
                continue
            if (not need_prev) and nxt is not None:
                continue
            season_iter = reversed(seasons) if need_prev else seasons
            for season in season_iter:
                season_num = _safe_int(season.get("season_number"))
                season_id = str(season.get("season_id") or "").strip()
                if season_num is None:
                    continue
                if need_prev and season_num >= int(cur_season):
                    continue
                if (not need_prev) and season_num <= int(cur_season):
                    continue
                cache_key_season = f"{season_num}:{season_id or '#'}"
                if cache_key_season in fetched_seasons:
                    continue
                fetched_seasons.add(cache_key_season)
                if season_id:
                    extra_rows = _fetch_series_episodes_for_season(
                        base=base,
                        series_id=series_id,
                        season_id=season_id,
                        user_id=user_id,
                        fields=fields,
                        timeout=timeout,
                        token=token,
                    )
                else:
                    extra_rows = _fetch_series_episodes_for_season_number(
                        base=base,
                        series_id=series_id,
                        season_number=int(season_num),
                        user_id=user_id,
                        fields=fields,
                        timeout=timeout,
                        token=token,
                    )
                changed = False
                for row in extra_rows:
                    rid = str(row.get("Id") or row.get("id") or "").strip()
                    if rid and rid not in rows_by_id:
                        rows_by_id[rid] = row
                        changed = True
                if changed:
                    episodes = _episodes_sorted(list(rows_by_id.values()))
                    prev, nxt = _adjacent_from_episodes(
                        episodes, cur_id=cur_id, cur_season=int(cur_season), cur_episode=int(cur_episode)
                    )
                if need_prev and prev is not None:
                    break
                if (not need_prev) and nxt is not None:
                    break

    payload = {
        "prev": dict(prev) if isinstance(prev, dict) else None,
        "next": dict(nxt) if isinstance(nxt, dict) else None,
    }
    _catalog_cache_set(cache_key, payload, ttl_sec=_catalog_ttl_sec("detail"))
    _mark_catalog_ok()
    return {
        "prev": _attach_thumb(dict(prev)) if isinstance(prev, dict) else None,
        "next": _attach_thumb(dict(nxt)) if isinstance(nxt, dict) else None,
    }


def search_catalog(query: str, *, limit: int = 30, refresh: bool = False) -> dict[str, object]:
    q = str(query or "").strip()
    if not q:
        return {"query": "", "items": [], "count": 0}
    lim = max(1, min(100, int(limit)))
    base, token, user_id = _catalog_base_token_user()
    if not base:
        return {"query": q, "items": [], "count": 0}
    cache_key = f"search:{base}:{user_id}:{lim}:{q.lower()}"
    if not refresh:
        cached = _catalog_cache_get(cache_key)
        if isinstance(cached, dict):
            _mark_catalog_ok()
            items = cached.get("items") if isinstance(cached.get("items"), list) else []
            norm_items = [_attach_thumb(dict(it)) for it in items if isinstance(it, dict)]
            return {
                "query": str(cached.get("query") or q),
                "items": norm_items,
                "count": int(cached.get("count") or len(norm_items)),
            }
    params = _urlparse.urlencode(
        {
            "SearchTerm": q,
            "Recursive": "true",
            "Limit": str(lim),
            "IncludeItemTypes": "Movie,Episode,Series",
            "Fields": "Overview,ImageTags,BackdropImageTags,ProductionYear,PremiereDate,RunTimeTicks,UserData,SeriesName,ParentIndexNumber,IndexNumber",
        }
    )
    candidates: list[str] = []
    if user_id:
        candidates.append(f"{base}/Users/{_urlparse.quote(user_id)}/Items?{params}")
    candidates.append(f"{base}/Items?{params}")
    timeout = float(os.getenv("RELAYTV_JELLYFIN_ITEM_TIMEOUT_SEC", "5"))
    items: list[dict[str, object]] = []
    for url in candidates:
        try:
            payload = _get_json(url, timeout=timeout, token=token)
            items = _extract_items(payload)
            if items:
                break
        except Exception as e:
            _mark_catalog_error(str(e))
            continue
    out = [_normalize_catalog_item(it, base=base, token=token) for it in items]
    out = [x for x in out if str(x.get("item_id") or "").strip()]
    payload = {"query": q, "items": out, "count": len(out)}
    _catalog_cache_set(cache_key, payload, ttl_sec=_catalog_ttl_sec("search"))
    _mark_catalog_ok()
    return payload


def get_home_rows(*, limit: int = 24, refresh: bool = False) -> dict[str, object]:
    lim = max(1, min(60, int(limit)))
    base, token, user_id = _catalog_base_token_user()
    if not base:
        return {"rows": [], "generated_ts": int(time.time())}
    server_type = str(status().get("server_type") or "jellyfin").strip().lower()
    cache_key = f"home:{server_type}:{base}:{user_id}:{lim}"
    if not refresh:
        cached = _catalog_cache_get(cache_key)
        if isinstance(cached, dict):
            _mark_catalog_ok()
            rows = list(cached.get("rows") or [])
            norm_rows: list[dict[str, object]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                items = row.get("items") if isinstance(row.get("items"), list) else []
                norm_items = [_attach_thumb(dict(it)) for it in items if isinstance(it, dict)]
                nr = dict(row)
                nr["items"] = norm_items
                norm_rows.append(nr)
            return {
                "rows": norm_rows,
                "generated_ts": int(cached.get("generated_ts") or int(time.time())),
            }
    fields = "Overview,ImageTags,BackdropImageTags,ProductionYear,PremiereDate,RunTimeTicks,UserData,SeriesName,ParentIndexNumber,IndexNumber"

    def _first_items(urls: list[str]) -> list[dict[str, object]]:
        timeout = float(os.getenv("RELAYTV_JELLYFIN_ITEM_TIMEOUT_SEC", "5"))
        for u in urls:
            try:
                payload = _get_json(u, timeout=timeout, token=token)
                items = _extract_items(payload)
                if items:
                    return items
            except Exception as e:
                _mark_catalog_error(str(e))
                continue
        return []

    rows: list[dict[str, object]] = []

    continue_urls: list[str] = []
    if user_id:
        continue_urls.append(
            f"{base}/Users/{_urlparse.quote(user_id)}/Items/Resume?Limit={lim}&Fields={_urlparse.quote(fields)}"
        )
    next_up_urls: list[str] = []
    if user_id:
        next_up_urls.append(
            f"{base}/Shows/NextUp?UserId={_urlparse.quote(user_id)}&Limit={lim}&Fields={_urlparse.quote(fields)}"
        )
    movies_urls: list[str] = []
    if user_id:
        movies_urls.append(
            f"{base}/Users/{_urlparse.quote(user_id)}/Items?"
            f"IncludeItemTypes=Movie&Recursive=true&SortBy=DateCreated&SortOrder=Descending&Limit={lim}&Fields={_urlparse.quote(fields)}"
        )
    movies_urls.append(
        f"{base}/Items?"
        f"IncludeItemTypes=Movie&Recursive=true&SortBy=DateCreated&SortOrder=Descending&Limit={lim}&Fields={_urlparse.quote(fields)}"
    )
    shows_urls: list[str] = []
    if user_id:
        shows_urls.append(
            f"{base}/Users/{_urlparse.quote(user_id)}/Items?"
            f"IncludeItemTypes=Episode&Recursive=true&SortBy=DateCreated&SortOrder=Descending&Limit={lim}&Fields={_urlparse.quote(fields)}"
        )
    shows_urls.append(
        f"{base}/Items?"
        f"IncludeItemTypes=Episode&Recursive=true&SortBy=DateCreated&SortOrder=Descending&Limit={lim}&Fields={_urlparse.quote(fields)}"
    )
    latest_urls: list[str] = []
    if user_id:
        latest_urls.append(f"{base}/Users/{_urlparse.quote(user_id)}/Items/Latest?Limit={lim}")
    latest_urls.append(f"{base}/Items/Latest?Limit={lim}")

    specs = []
    if server_type != "emby":
        specs.extend(
            [
                ("continue_watching", "Continue Watching", continue_urls),
                ("next_up", "Next Up", next_up_urls),
            ]
        )
    specs.extend(
        [
            ("movies", "Movies", movies_urls),
            ("shows", "Shows", shows_urls),
            ("recently_added", "Recently Added", latest_urls),
        ]
    )
    for row_id, title, urls in specs:
        items = _first_items(urls)
        norm = [_normalize_catalog_item(it, base=base, token=token) for it in items]
        norm = [x for x in norm if str(x.get("item_id") or "").strip()]
        rows.append({"id": row_id, "title": title, "items": norm})
    payload = {"rows": rows, "generated_ts": int(time.time())}
    _catalog_cache_set(cache_key, payload, ttl_sec=_catalog_ttl_sec("home"))
    _mark_catalog_ok()
    return payload


def list_movies(
    *,
    sort: str = "added",
    limit: int = 60,
    start_index: int = 0,
    starts_with: str = "",
    refresh: bool = False,
) -> dict[str, object]:
    lim = max(1, min(5000, int(limit)))
    start = max(0, int(start_index))
    base, token, user_id = _catalog_base_token_user()
    if not base:
        return {
            "items": [],
            "count": 0,
            "sort": str(sort or "added"),
            "start_index": start,
            "limit": lim,
            "next_start_index": None,
            "starts_with": str(starts_with or "").strip().upper(),
        }

    sort_key = str(sort or "added").strip().lower()
    sort_map: dict[str, tuple[str, str]] = {
        "added": ("DateCreated", "Descending"),
        "title_asc": ("SortName", "Ascending"),
        "title_desc": ("SortName", "Descending"),
        "year_desc": ("ProductionYear,SortName", "Descending"),
        "year_asc": ("ProductionYear,SortName", "Ascending"),
    }
    sort_by, sort_order = sort_map.get(sort_key, sort_map["added"])
    letter = str(starts_with or "").strip()
    if letter:
        letter = letter[0].upper()

    cache_key = f"movies:{base}:{user_id}:{sort_key}:{lim}:{start}:{letter}"
    if not refresh:
        cached = _catalog_cache_get(cache_key)
        if isinstance(cached, dict):
            _mark_catalog_ok()
            items = cached.get("items") if isinstance(cached.get("items"), list) else []
            out_items = [_attach_thumb(dict(it)) for it in items if isinstance(it, dict)]
            return {
                "items": out_items,
                "count": int(cached.get("count") or len(out_items)),
                "sort": str(cached.get("sort") or sort_key),
                "start_index": int(cached.get("start_index") or start),
                "limit": int(cached.get("limit") or lim),
                "next_start_index": cached.get("next_start_index"),
                "starts_with": str(cached.get("starts_with") or letter),
            }

    fields = "Overview,ImageTags,BackdropImageTags,ProductionYear,PremiereDate,RunTimeTicks,UserData,SeriesName,ParentIndexNumber,IndexNumber"
    params: dict[str, str] = {
        "IncludeItemTypes": "Movie",
        "Recursive": "true",
        "SortBy": sort_by,
        "SortOrder": sort_order,
        "StartIndex": str(start),
        "Limit": str(lim),
        "Fields": fields,
    }
    if letter:
        params["NameStartsWithOrGreater"] = letter
    q = _urlparse.urlencode(params)

    candidates: list[str] = []
    if user_id:
        candidates.append(f"{base}/Users/{_urlparse.quote(user_id)}/Items?{q}")
    candidates.append(f"{base}/Items?{q}")

    timeout = float(os.getenv("RELAYTV_JELLYFIN_ITEM_TIMEOUT_SEC", "5"))
    rows: list[dict[str, object]] = []
    total = 0
    for url in candidates:
        try:
            payload = _get_json(url, timeout=timeout, token=token)
            rows = _extract_items(payload)
            total = _extract_total_count(payload, len(rows))
            if rows or total:
                break
        except Exception as e:
            _mark_catalog_error(str(e))
            continue

    items = [_normalize_catalog_item(it, base=base, token=token) for it in rows]
    items = [x for x in items if str(x.get("item_id") or "").strip()]
    next_start: int | None = None
    if start + len(items) < total:
        next_start = start + len(items)

    out = {
        "items": items,
        "count": total if total > 0 else len(items),
        "sort": sort_key,
        "start_index": start,
        "limit": lim,
        "next_start_index": next_start,
        "starts_with": letter,
    }
    _catalog_cache_set(cache_key, out, ttl_sec=_catalog_ttl_sec("search"))
    _mark_catalog_ok()
    return out


def list_series(
    *,
    sort: str = "title_asc",
    limit: int = 60,
    start_index: int = 0,
    starts_with: str = "",
    refresh: bool = False,
) -> dict[str, object]:
    lim = max(1, min(5000, int(limit)))
    start = max(0, int(start_index))
    base, token, user_id = _catalog_base_token_user()
    if not base:
        return {
            "items": [],
            "count": 0,
            "sort": str(sort or "title_asc"),
            "start_index": start,
            "limit": lim,
            "next_start_index": None,
            "starts_with": str(starts_with or "").strip().upper(),
        }

    sort_key = str(sort or "title_asc").strip().lower()
    sort_map: dict[str, tuple[str, str]] = {
        "title_asc": ("SortName", "Ascending"),
        "title_desc": ("SortName", "Descending"),
        "added": ("DateCreated", "Descending"),
        "year_desc": ("ProductionYear,SortName", "Descending"),
        "year_asc": ("ProductionYear,SortName", "Ascending"),
    }
    sort_by, sort_order = sort_map.get(sort_key, sort_map["title_asc"])
    letter = str(starts_with or "").strip()
    if letter:
        letter = letter[0].upper()

    cache_key = f"series:{base}:{user_id}:{sort_key}:{lim}:{start}:{letter}"
    if not refresh:
        cached = _catalog_cache_get(cache_key)
        if isinstance(cached, dict):
            _mark_catalog_ok()
            items = cached.get("items") if isinstance(cached.get("items"), list) else []
            out_items = [_attach_thumb(dict(it)) for it in items if isinstance(it, dict)]
            return {
                "items": out_items,
                "count": int(cached.get("count") or len(out_items)),
                "sort": str(cached.get("sort") or sort_key),
                "start_index": int(cached.get("start_index") or start),
                "limit": int(cached.get("limit") or lim),
                "next_start_index": cached.get("next_start_index"),
                "starts_with": str(cached.get("starts_with") or letter),
            }

    fields = "Overview,ImageTags,BackdropImageTags,ProductionYear,PremiereDate,RunTimeTicks,UserData,SeriesName,ParentIndexNumber,IndexNumber"
    params: dict[str, str] = {
        "IncludeItemTypes": "Series",
        "Recursive": "true",
        "SortBy": sort_by,
        "SortOrder": sort_order,
        "StartIndex": str(start),
        "Limit": str(lim),
        "Fields": fields,
    }
    if letter:
        params["NameStartsWithOrGreater"] = letter
    q = _urlparse.urlencode(params)

    candidates: list[str] = []
    if user_id:
        candidates.append(f"{base}/Users/{_urlparse.quote(user_id)}/Items?{q}")
    candidates.append(f"{base}/Items?{q}")

    timeout = float(os.getenv("RELAYTV_JELLYFIN_ITEM_TIMEOUT_SEC", "5"))
    rows: list[dict[str, object]] = []
    total = 0
    for url in candidates:
        try:
            payload = _get_json(url, timeout=timeout, token=token)
            rows = _extract_items(payload)
            total = _extract_total_count(payload, len(rows))
            if rows or total:
                break
        except Exception as e:
            _mark_catalog_error(str(e))
            continue

    items = [_normalize_catalog_item(it, base=base, token=token) for it in rows]
    items = [x for x in items if str(x.get("item_id") or "").strip()]
    next_start: int | None = None
    if start + len(items) < total:
        next_start = start + len(items)

    out = {
        "items": items,
        "count": total if total > 0 else len(items),
        "sort": sort_key,
        "start_index": start,
        "limit": lim,
        "next_start_index": next_start,
        "starts_with": letter,
    }
    _catalog_cache_set(cache_key, out, ttl_sec=_catalog_ttl_sec("search"))
    _mark_catalog_ok()
    return out


def list_series_seasons(series_id: str, *, refresh: bool = False) -> dict[str, object]:
    sid = str(series_id or "").strip()
    base, token, user_id = _catalog_base_token_user()
    if not sid or not base:
        return {"series_id": sid, "seasons": [], "count": 0}

    cache_key = f"series_seasons:{base}:{user_id}:{sid}"
    if not refresh:
        cached = _catalog_cache_get(cache_key)
        if isinstance(cached, dict):
            _mark_catalog_ok()
            seasons = cached.get("seasons") if isinstance(cached.get("seasons"), list) else []
            return {
                "series_id": str(cached.get("series_id") or sid),
                "seasons": [_attach_thumb(dict(x)) for x in seasons if isinstance(x, dict)],
                "count": int(cached.get("count") or len(seasons)),
            }

    timeout = float(os.getenv("RELAYTV_JELLYFIN_ITEM_TIMEOUT_SEC", "5"))
    seasons_raw = _fetch_series_seasons(
        base=base,
        series_id=sid,
        user_id=user_id,
        timeout=timeout,
        token=token,
    )
    seasons: list[dict[str, object]] = []
    for row in seasons_raw:
        if not isinstance(row, dict):
            continue
        num = _safe_int(row.get("season_number"))
        season_id_out = str(row.get("season_id") or "").strip()
        title = f"Season {num}" if num is not None else "Season"
        thumb_target = season_id_out or sid
        thumb = ""
        if thumb_target and base:
            thumb = f"{base}/Items/{_urlparse.quote(thumb_target)}/Images/Primary"
            q: dict[str, str] = {}
            if token:
                q["api_key"] = token
            if q:
                thumb = f"{thumb}?{_urlparse.urlencode(q)}"
        seasons.append(_attach_thumb({
            "series_id": sid,
            "season_id": season_id_out,
            "season_number": num,
            "title": title,
            "subtitle": f"{title}",
            "thumbnail": thumb,
        }))
    seasons.sort(key=lambda x: (_safe_int(x.get("season_number")) or 0))
    out = {"series_id": sid, "seasons": seasons, "count": len(seasons)}
    _catalog_cache_set(cache_key, out, ttl_sec=_catalog_ttl_sec("detail"))
    _mark_catalog_ok()
    return out


def list_series_episodes(
    series_id: str,
    *,
    season_id: str = "",
    season_number: int | None = None,
    refresh: bool = False,
) -> dict[str, object]:
    sid = str(series_id or "").strip()
    base, token, user_id = _catalog_base_token_user()
    if not sid or not base:
        return {"series_id": sid, "season_id": str(season_id or "").strip(), "season_number": season_number, "episodes": [], "count": 0}

    seas_id = str(season_id or "").strip()
    seas_num = _safe_int(season_number)
    explicit_season_filter = bool(seas_id) or (seas_num is not None)
    cache_key = f"series_eps:{base}:{user_id}:{sid}:{seas_id}:{seas_num if seas_num is not None else ''}"
    if not refresh:
        cached = _catalog_cache_get(cache_key)
        if isinstance(cached, dict):
            _mark_catalog_ok()
            eps = cached.get("episodes") if isinstance(cached.get("episodes"), list) else []
            return {
                "series_id": str(cached.get("series_id") or sid),
                "season_id": str(cached.get("season_id") or seas_id),
                "season_number": _safe_int(cached.get("season_number")),
                "episodes": [_attach_thumb(dict(x)) for x in eps if isinstance(x, dict)],
                "count": int(cached.get("count") or len(eps)),
            }

    fields = "Overview,ImageTags,BackdropImageTags,ProductionYear,PremiereDate,RunTimeTicks,UserData,SeriesName,ParentIndexNumber,IndexNumber,MediaStreams,DefaultAudioStreamIndex,DefaultSubtitleStreamIndex"
    timeout = float(os.getenv("RELAYTV_JELLYFIN_ITEM_TIMEOUT_SEC", "5"))

    rows: list[dict[str, object]] = []
    if seas_id:
        rows = _fetch_series_episodes_for_season(
            base=base,
            series_id=sid,
            season_id=seas_id,
            user_id=user_id,
            fields=fields,
            timeout=timeout,
            token=token,
        )
    elif seas_num is not None:
        rows = _fetch_series_episodes_for_season_number(
            base=base,
            series_id=sid,
            season_number=int(seas_num),
            user_id=user_id,
            fields=fields,
            timeout=timeout,
            token=token,
        )
    else:
        uid = _urlparse.quote(user_id) if user_id else ""
        fields_q = _urlparse.quote(fields)
        candidates: list[str] = []
        if uid:
            candidates.append(f"{base}/Shows/{_urlparse.quote(sid)}/Episodes?UserId={uid}&Limit=5000&Fields={fields_q}")
        candidates.append(f"{base}/Shows/{_urlparse.quote(sid)}/Episodes?Limit=5000&Fields={fields_q}")
        for u in candidates:
            try:
                rows = _extract_items(_get_json(u, timeout=timeout, token=token))
                if rows:
                    break
            except Exception as e:
                _mark_catalog_error(str(e))
                continue

    episodes = [_normalize_catalog_item(it, base=base, token=token) for it in rows]
    episodes = [x for x in episodes if str(x.get("item_id") or "").strip()]
    episodes = [
        ep
        for ep in episodes
        if (not str(ep.get("series_id") or "").strip()) or str(ep.get("series_id") or "").strip() == sid
    ]
    episodes.sort(
        key=lambda x: (
            _safe_int(x.get("season_number")) if _safe_int(x.get("season_number")) is not None else 10_000,
            _safe_int(x.get("episode_number")) if _safe_int(x.get("episode_number")) is not None else 10_000,
            str(x.get("title") or "").lower(),
        )
    )

    if explicit_season_filter and seas_num is not None:
        episodes = [ep for ep in episodes if _safe_int(ep.get("season_number")) == int(seas_num)]

    out = {
        "series_id": sid,
        "season_id": seas_id,
        "season_number": seas_num if explicit_season_filter else None,
        "episodes": episodes,
        "count": len(episodes),
    }
    _catalog_cache_set(cache_key, out, ttl_sec=_catalog_ttl_sec("detail"))
    _mark_catalog_ok()
    return out


def register_progress_provider(fn) -> None:
    global _PROGRESS_PROVIDER
    _PROGRESS_PROVIDER = fn


def register_command_sink(fn) -> None:
    """Register the callable that executes an inbound Jellyfin command.

    Same seam as ``register_progress_provider``: this module stays transport
    and never reaches into the routes package, so the socket hands normalized
    commands back out to whoever owns playback control.
    """
    global _COMMAND_SINK
    _COMMAND_SINK = fn


def dispatch_command(action: str, payload: dict[str, object]) -> object:
    """Run a socket-sourced command through the registered ingress."""
    sink = _COMMAND_SINK
    if sink is None:
        raise RuntimeError("no jellyfin command sink registered")
    return sink(action, payload)


def command_sink_registered() -> bool:
    return _COMMAND_SINK is not None


def _build_url(path: str) -> str:
    st = status()
    base = str(st.get("server_url") or "").strip().rstrip("/")
    p = (path or "").strip()
    if not p:
        return base
    if p.startswith("http://") or p.startswith("https://"):
        return p
    if not base:
        return p
    if not p.startswith("/"):
        p = f"/{p}"
    return f"{base}{p}"


def _post_json(url: str, payload: dict, *, timeout: float = 3.0) -> None:
    body = json.dumps(payload).encode("utf-8")
    req = _urlrequest.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    st = status()
    token = _active_token()
    if token:
        req.add_header("X-Emby-Token", token)
        req.add_header("Authorization", f'MediaBrowser Token="{token}"')
    auth = (
        f'MediaBrowser Client="{st.get("client_name")}", '
        f'Device="{st.get("device_name")}", '
        f'DeviceId="{st.get("device_id")}", '
        f'Version="{st.get("client_version")}"'
    )
    if token:
        auth = f'{auth}, Token="{token}"'
    req.add_header("X-Emby-Authorization", auth)
    with _urlrequest.urlopen(req, timeout=timeout):
        return


def _post_no_body(url: str, *, timeout: float = 3.0) -> None:
    req = _urlrequest.Request(url, data=b"", method="POST")
    st = status()
    token = _active_token()
    if token:
        req.add_header("X-Emby-Token", token)
        req.add_header("Authorization", f'MediaBrowser Token="{token}"')
    auth = (
        f'MediaBrowser Client="{st.get("client_name")}", '
        f'Device="{st.get("device_name")}", '
        f'DeviceId="{st.get("device_id")}", '
        f'Version="{st.get("client_version")}"'
    )
    if token:
        auth = f'{auth}, Token="{token}"'
    req.add_header("X-Emby-Authorization", auth)
    with _urlrequest.urlopen(req, timeout=timeout):
        return


def _sanitize_error_text(msg: object) -> str:
    text = str(msg or "").strip()
    if not text:
        return ""
    # Query-parameter secrets.
    text = re.sub(r"(?i)(api[_-]?key=)[^&\s]+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(access[_-]?token=)[^&\s]+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(refresh[_-]?token=)[^&\s]+", r"\1<redacted>", text)
    # Header-like/token literals.
    text = re.sub(r'(?i)(token\s*=\s*")[^"]+(")', r"\1<redacted>\2", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1<redacted>", text)
    text = re.sub(r'(?i)("?(?:accesstoken|authtoken|refreshtoken)"?\s*[:=]\s*")([^"]+)(")', r"\1<redacted>\3", text)
    text = re.sub(r'(?i)("?(?:accesstoken|authtoken|refreshtoken)"?\s*[:=]\s*)([A-Za-z0-9._~+/=-]{6,})', r"\1<redacted>", text)
    return text


def _format_http_error(exc: Exception) -> str:
    if isinstance(exc, _urlerror.HTTPError):
        body = ""
        try:
            body = (exc.read() or b"").decode("utf-8", "ignore").strip()
        except Exception:
            body = ""
        if body:
            return _sanitize_error_text(f"HTTP {exc.code}: {body[:500]}")
        return _sanitize_error_text(f"HTTP {exc.code}: {exc.reason}")
    return _sanitize_error_text(str(exc))


def authenticate_once() -> dict[str, object]:
    st = status()
    if not bool(st.get("enabled")) or not bool(st.get("running")):
        return {"ok": False, "reason": "disabled"}
    base = str(st.get("server_url") or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "reason": "no_server_url"}
    with _LOCK:
        username = str(_AUTH_USERNAME or "")
        password = str(_AUTH_PASSWORD or "")
        device_name = str(_STATUS.get("device_name") or "RelayTV")
        device_id = str(_STATUS.get("device_id") or "relaytv")
        client_name = str(_STATUS.get("client_name") or "RelayTV")
        client_version = str(_STATUS.get("client_version") or "1.0")
    if not username or not password:
        return {"ok": False, "reason": "no_credentials"}

    url = f"{base}/Users/AuthenticateByName"
    payload = {"Username": username, "Pw": password}
    req = _urlrequest.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header(
        "X-Emby-Authorization",
        (
            f'MediaBrowser Client="{client_name}", '
            f'Device="{device_name}", '
            f'DeviceId="{device_id}", '
            f'Version="{client_version}"'
        ),
    )
    timeout = float(os.getenv("RELAYTV_JELLYFIN_AUTH_TIMEOUT_SEC", "5"))
    try:
        with _urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = (resp.read() or b"{}").decode("utf-8", "ignore")
        data = json.loads(raw) if raw.strip() else {}
        access_token = str(data.get("AccessToken") or "").strip()
        user = data.get("User") if isinstance(data.get("User"), dict) else {}
        session = data.get("SessionInfo") if isinstance(data.get("SessionInfo"), dict) else {}
        user_id = str(user.get("Id") or "").strip()
        sess_id = str(session.get("Id") or "").strip()
        if not access_token:
            raise RuntimeError("authenticate response missing AccessToken")
        global _ACCESS_TOKEN, _AUTH_USER_ID, _AUTH_SESSION_ID
        with _LOCK:
            _ACCESS_TOKEN = access_token
            _AUTH_USER_ID = user_id
            _AUTH_SESSION_ID = sess_id
            _STATUS["authenticated"] = True
            _STATUS["auth_user_id"] = user_id
            _STATUS["auth_session_id"] = sess_id
            _STATUS["last_auth_ts"] = int(time.time())
            _STATUS["last_auth_ok"] = True
            _STATUS["last_auth_error"] = None
            _STATUS["last_error"] = None
        _catalog_cache_clear()
        return {"ok": True, "user_id": user_id, "session_id": sess_id}
    except Exception as e:
        msg = _format_http_error(e)
        with _LOCK:
            _STATUS["authenticated"] = False
            _STATUS["auth_user_id"] = ""
            _STATUS["auth_session_id"] = ""
            _STATUS["last_auth_ts"] = int(time.time())
            _STATUS["last_auth_ok"] = False
            _STATUS["last_auth_error"] = msg
            _STATUS["last_error"] = msg
        return {"ok": False, "reason": "auth_failed", "error": msg}


"""Commands advertised to the server, as ``GeneralCommandType`` members.

Jellyfin has two disjoint command enums and rejects a body that mixes them.
``Stop``/``Pause``/``Unpause``/``Seek``/``NextTrack``/``PreviousTrack`` are
``PlaystateCommand`` values and arrive over the socket as a single ``Playstate``
message, which ``PlayState`` here covers; listing them individually is a 400.

Only commands RelayTV actually executes belong here. Advertising one without a
handler puts a button on every Jellyfin remote in the house that does nothing.
"""
CAPABILITY_COMMANDS = (
    "PlayState",
    "Play",
    "PlayNext",
    "SetVolume",
    "Mute",
    "Unmute",
    "ToggleMute",
)

CAPABILITY_MEDIA_TYPES = ("Video", "Audio")


def capabilities_payload() -> dict[str, object]:
    """The ``ClientCapabilitiesDto`` body, posted unwrapped.

    Wrapping it as ``{"Capabilities": {...}}`` is accepted with a 204 and then
    bound as an all-default DTO, which silently *erases* the session's
    capabilities. Registration looked healthy for as long as that was the first
    shape tried.
    """
    return {
        "PlayableMediaTypes": list(CAPABILITY_MEDIA_TYPES),
        "SupportedCommands": list(CAPABILITY_COMMANDS),
        "SupportsMediaControl": True,
        "SupportsPersistentIdentifier": True,
    }


def _capabilities_query(device_id: str) -> str:
    q: list[tuple[str, str]] = [("id", device_id)]
    q.extend(("playableMediaTypes", value) for value in CAPABILITY_MEDIA_TYPES)
    q.extend(("supportedCommands", value) for value in CAPABILITY_COMMANDS)
    q.append(("supportsMediaControl", "true"))
    q.append(("supportsPersistentIdentifier", "true"))
    return _urlparse.urlencode(q, doseq=True)


def read_session_capabilities(device_id: str = "", *, timeout: float = 3.0) -> dict[str, object]:
    """Ask the server what it recorded for this device's session.

    A 204 from the capabilities POST is not evidence: the server returns one
    whether or not the body bound to anything. Only the session readback tells
    us media control is really advertised, so registration asserts on this.
    """
    did = str(device_id or status().get("device_id") or "").strip()
    if not did:
        return {}
    url = _build_url(f"/Sessions?deviceId={_urlparse.quote(did)}")
    if not url:
        return {}
    rows = _get_json(url, timeout=timeout, token=_active_token())
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if isinstance(row, dict) and str(row.get("DeviceId") or "").strip() == did:
            return row
    return {}


def register_receiver_once() -> dict[str, object]:
    st = status()
    if not bool(st.get("enabled")) or not bool(st.get("running")):
        return {"ok": False, "reason": "disabled"}
    base = str(st.get("server_url") or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "reason": "no_server_url"}
    did = str(st.get("device_id") or "").strip()
    payload = capabilities_payload()
    # The query-string form is what older Emby builds accept; it stays as a
    # fallback so an Emby server that rejects the DTO body still registers.
    candidates: list[tuple[str, str, dict | None]] = [
        ("full", f"{base}/Sessions/Capabilities/Full", payload),
        ("caps_query", f"{base}/Sessions/Capabilities?{_capabilities_query(did)}", None),
    ]
    timeout = float(os.getenv("RELAYTV_JELLYFIN_REGISTER_TIMEOUT_SEC", "3"))
    last_err = "register_failed"
    last_name = ""
    last_url = ""
    try:
        for name, url, body in candidates:
            try:
                if body is None:
                    _post_no_body(url, timeout=timeout)
                else:
                    _post_json(url, body, timeout=timeout)
            except Exception as e:
                last_err = _format_http_error(e)
                last_name = name
                last_url = url
                continue

            verified = _verify_registration(did, timeout=timeout)
            if verified is False:
                last_err = "server did not record media control for this session"
                last_name = name
                last_url = url
                continue

            with _LOCK:
                _STATUS["connected"] = True
                _STATUS["last_register_ts"] = int(time.time())
                _STATUS["last_register_ok"] = True
                _STATUS["last_register_error"] = None
                _STATUS["last_error"] = None
                _STATUS["media_control_verified"] = verified
            return {"ok": True, "url": url, "method": name, "verified": verified}
        with _LOCK:
            _STATUS["connected"] = False
            _STATUS["last_register_ts"] = int(time.time())
            _STATUS["last_register_ok"] = False
            _STATUS["last_register_error"] = f"{last_name}: {last_err}"
            _STATUS["last_error"] = f"{last_name}: {last_err}"
            _STATUS["media_control_verified"] = False
        return {"ok": False, "reason": "register_failed", "error": f"{last_name}: {last_err}", "url": last_url}
    except Exception as e:
        msg = _format_http_error(e)
        with _LOCK:
            _STATUS["connected"] = False
            _STATUS["last_register_ts"] = int(time.time())
            _STATUS["last_register_ok"] = False
            _STATUS["last_register_error"] = msg
            _STATUS["last_error"] = msg
        return {"ok": False, "reason": "register_failed", "error": msg}


def _verify_registration(device_id: str, *, timeout: float) -> bool | None:
    """True if the server advertises media control, False if it does not.

    ``None`` means the readback itself failed — a server that hides ``/Sessions``
    behind a proxy or an Emby build shaped differently should not be treated as
    a registration failure, so the caller accepts the POST on its own terms.
    """
    try:
        session = read_session_capabilities(device_id, timeout=timeout)
    except Exception:
        return None
    if not session:
        return None
    return bool(session.get("SupportsMediaControl"))


def invalidate_registration(reason: str = "") -> None:
    """Force the next heartbeat to re-post and re-verify capabilities.

    Capabilities live in the server's in-memory session state, so a Jellyfin
    restart drops them while this device's view still says registered. The
    control socket reconnecting is the signal that the session is new: without
    this, ``_ensure_registration`` short-circuits on the stale success and the
    device reports itself castable forever while the server offers nothing.
    """
    global _REGISTER_RETRY_FAILURES, _NEXT_REGISTER_RETRY_TS
    with _LOCK:
        _STATUS["connected"] = False
        _STATUS["last_register_ok"] = None
        _STATUS["media_control_verified"] = None
        _REGISTER_RETRY_FAILURES = 0
        _NEXT_REGISTER_RETRY_TS = 0.0
        _STATUS["register_retry_failures"] = 0
        _STATUS["next_register_retry_ts"] = None
        _STATUS["last_register_reason"] = str(reason or "") or None


def _register_retry_enabled() -> bool:
    return _env_bool("RELAYTV_JELLYFIN_REGISTER_RETRY", True)


def _register_backoff_sec(failures: int) -> float:
    base = max(0.5, float(os.getenv("RELAYTV_JELLYFIN_REGISTER_RETRY_BASE_SEC", "3")))
    cap = max(base, float(os.getenv("RELAYTV_JELLYFIN_REGISTER_RETRY_MAX_SEC", "60")))
    f = max(1, int(failures))
    return min(cap, base * (2 ** (f - 1)))


def _schedule_register_retry(now_ts: float, failures: int, delay_sec: float) -> None:
    global _REGISTER_RETRY_FAILURES, _NEXT_REGISTER_RETRY_TS
    _REGISTER_RETRY_FAILURES = max(0, int(failures))
    _NEXT_REGISTER_RETRY_TS = max(0.0, float(now_ts) + max(0.0, float(delay_sec)))
    with _LOCK:
        _STATUS["register_retry_failures"] = _REGISTER_RETRY_FAILURES
        _STATUS["next_register_retry_ts"] = int(_NEXT_REGISTER_RETRY_TS)
        _STATUS["last_register_backoff_sec"] = float(delay_sec)


def _clear_register_retry_state() -> None:
    global _REGISTER_RETRY_FAILURES, _NEXT_REGISTER_RETRY_TS
    _REGISTER_RETRY_FAILURES = 0
    _NEXT_REGISTER_RETRY_TS = 0.0
    with _LOCK:
        _STATUS["register_retry_failures"] = 0
        _STATUS["next_register_retry_ts"] = None
        _STATUS["last_register_backoff_sec"] = 0.0


def _ensure_registration(now_ts: float | None = None) -> None:
    if not _register_retry_enabled():
        return
    st = status()
    if not bool(st.get("enabled")) or not bool(st.get("running")):
        return
    if not str(st.get("server_url") or "").strip():
        return
    if not bool(st.get("api_key_configured")) and not bool(st.get("authenticated")):
        return

    now_val = float(now_ts if now_ts is not None else time.time())
    if _NEXT_REGISTER_RETRY_TS and now_val < float(_NEXT_REGISTER_RETRY_TS):
        return
    if bool(st.get("connected")) and bool(st.get("last_register_ok")):
        return

    out = register_receiver_once()
    if bool(out.get("ok")):
        _clear_register_retry_state()
        return

    failures = _REGISTER_RETRY_FAILURES + 1
    delay = _register_backoff_sec(failures)
    _schedule_register_retry(now_val, failures, delay)


def _ensure_control_socket() -> None:
    """Keep the cast-target socket up. Imported late to avoid an import cycle."""
    try:
        from . import jellyfin_ws
    except Exception:
        return
    try:
        jellyfin_ws.ensure_running()
    except Exception:
        pass


def _stop_control_socket() -> None:
    try:
        from . import jellyfin_ws
    except Exception:
        return
    try:
        jellyfin_ws.stop()
    except Exception:
        pass


def _control_socket_status() -> dict[str, object]:
    try:
        from . import jellyfin_ws

        return jellyfin_ws.status()
    except Exception:
        return {}


def _ensure_authentication() -> None:
    if not runtime_config.snapshot().flag("RELAYTV_JELLYFIN_AUTH_ENABLED", True):
        return
    st = status()
    if not bool(st.get("enabled")) or not bool(st.get("running")):
        return
    if bool(st.get("authenticated")):
        return
    if not bool(st.get("auth_user_configured")):
        return
    authenticate_once()


def send_playback_start_once(payload: dict | None = None) -> dict[str, object]:
    """Report playback start to ``/Sessions/Playing``.

    Progress alone eventually populates the session, but only on the next
    heartbeat tick. Announcing the start means the phone that just cast shows
    the item as playing straight away instead of an empty remote for a few
    seconds.
    """
    st = status()
    if not bool(st.get("enabled")) or not bool(st.get("running")):
        return {"ok": False, "reason": "disabled"}
    body = payload if isinstance(payload, dict) else {}
    if not body:
        return {"ok": False, "reason": "no_payload"}
    url = _build_url(os.getenv("RELAYTV_JELLYFIN_PLAYING_PATH", "/Sessions/Playing"))
    if not url:
        return {"ok": False, "reason": "no_server_url"}
    try:
        _post_json(url, body, timeout=float(os.getenv("RELAYTV_JELLYFIN_PROGRESS_TIMEOUT_SEC", "3")))
    except Exception as e:
        msg = _format_http_error(e)
        with _LOCK:
            _STATUS["last_playing_ts"] = int(time.time())
            _STATUS["last_playing_ok"] = False
            _STATUS["last_playing_error"] = msg
        return {"ok": False, "reason": "playing_failed", "error": msg}
    with _LOCK:
        _STATUS["last_playing_ts"] = int(time.time())
        _STATUS["last_playing_ok"] = True
        _STATUS["last_playing_error"] = None
    return {"ok": True}


def send_progress_payload_once(payload: dict | None = None) -> dict[str, object]:
    st = status()
    if not bool(st.get("enabled")) or not bool(st.get("running")):
        return {"ok": False, "reason": "disabled"}
    body = payload if isinstance(payload, dict) else {}
    if not body:
        return {"ok": False, "reason": "no_payload"}
    url = _build_url(os.getenv("RELAYTV_JELLYFIN_PROGRESS_PATH", "/Sessions/Playing/Progress"))
    if not url:
        return {"ok": False, "reason": "no_server_url"}
    t0 = time.monotonic()
    try:
        _post_json(url, body, timeout=float(os.getenv("RELAYTV_JELLYFIN_PROGRESS_TIMEOUT_SEC", "3")))
        latency_ms = max(0, int((time.monotonic() - t0) * 1000))
        with _LOCK:
            _STATUS["connected"] = True
            _STATUS["last_progress_ts"] = int(time.time())
            _STATUS["last_progress_ok"] = True
            _STATUS["last_progress_error"] = None
            _STATUS["progress_success_count"] = int(_STATUS.get("progress_success_count") or 0) + 1
            _STATUS["last_progress_latency_ms"] = latency_ms
            _STATUS["last_error"] = None
        return {"ok": True, "url": url, "latency_ms": latency_ms}
    except Exception as e:
        err = _format_http_error(e)
        latency_ms = max(0, int((time.monotonic() - t0) * 1000))
        with _LOCK:
            _STATUS["connected"] = False
            _STATUS["last_progress_ts"] = int(time.time())
            _STATUS["last_progress_ok"] = False
            _STATUS["last_progress_error"] = err
            _STATUS["progress_failure_count"] = int(_STATUS.get("progress_failure_count") or 0) + 1
            _STATUS["last_progress_latency_ms"] = latency_ms
            _STATUS["last_error"] = err
        return {"ok": False, "reason": "post_failed", "error": err, "latency_ms": latency_ms}


def send_progress_once() -> dict[str, object]:
    st = status()
    if not bool(st.get("enabled")) or not bool(st.get("running")):
        return {"ok": False, "reason": "disabled"}
    if _PROGRESS_PROVIDER is None:
        return {"ok": False, "reason": "no_provider"}
    payload = _PROGRESS_PROVIDER()
    return send_progress_payload_once(payload if isinstance(payload, dict) else None)


def send_playback_stopped_once(payload: dict | None = None) -> dict[str, object]:
    st = status()
    if not bool(st.get("enabled")) or not bool(st.get("running")):
        return {"ok": False, "reason": "disabled"}
    body = payload if isinstance(payload, dict) else {}
    if not body:
        return {"ok": False, "reason": "no_payload"}
    now_ts = time.time()
    if _stopped_duplicate_suppressed(body, now_ts):
        return {
            "ok": True,
            "reason": "duplicate_suppressed",
            "suppressed_duplicate_stopped": True,
            "window_sec": _stopped_dedupe_sec(),
        }
    url = _build_url(os.getenv("RELAYTV_JELLYFIN_STOPPED_PATH", "/Sessions/Playing/Stopped"))
    if not url:
        return {"ok": False, "reason": "no_server_url"}
    t0 = time.monotonic()
    try:
        _post_json(url, body, timeout=float(os.getenv("RELAYTV_JELLYFIN_STOPPED_TIMEOUT_SEC", "3")))
        latency_ms = max(0, int((time.monotonic() - t0) * 1000))
        with _LOCK:
            _STATUS["connected"] = True
            _STATUS["last_stopped_ts"] = int(time.time())
            _STATUS["last_stopped_ok"] = True
            _STATUS["last_stopped_error"] = None
            _STATUS["stopped_success_count"] = int(_STATUS.get("stopped_success_count") or 0) + 1
            _STATUS["last_stopped_latency_ms"] = latency_ms
            _STATUS["last_error"] = None
        return {"ok": True, "url": url, "latency_ms": latency_ms}
    except Exception as e:
        err = _format_http_error(e)
        latency_ms = max(0, int((time.monotonic() - t0) * 1000))
        with _LOCK:
            _STATUS["connected"] = False
            _STATUS["last_stopped_ts"] = int(time.time())
            _STATUS["last_stopped_ok"] = False
            _STATUS["last_stopped_error"] = err
            _STATUS["stopped_failure_count"] = int(_STATUS.get("stopped_failure_count") or 0) + 1
            _STATUS["last_stopped_latency_ms"] = latency_ms
            _STATUS["last_error"] = err
        return {"ok": False, "reason": "post_failed", "error": err, "latency_ms": latency_ms}


def _heartbeat_worker() -> None:
    while not _STOP_EVENT.is_set():
        try:
            _ensure_authentication()
            send_progress_once()
            _ensure_registration()
            _ensure_control_socket()
            _maybe_retry_detection()
        except Exception:
            pass
        hb = max(2, int(float(status().get("heartbeat_sec") or 5)))
        _STOP_EVENT.wait(hb)


def _start_worker() -> None:
    global _THREAD
    with _THREAD_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        if not bool(status().get("enabled")):
            return
        _STOP_EVENT.clear()
        _THREAD = threading.Thread(target=_heartbeat_worker, daemon=True, name="relaytv-jellyfin-heartbeat")
        _THREAD.start()


def _stop_worker() -> None:
    global _THREAD
    with _THREAD_LOCK:
        _STOP_EVENT.set()
        t = _THREAD
        _THREAD = None
    if t is not None and t.is_alive():
        t.join(timeout=1.0)
