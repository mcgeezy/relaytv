# SPDX-License-Identifier: GPL-3.0-only
"""Unified multi-server media browse, latency tracking, and cross-source deduplication."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import base64
import copy
import json
import os
import re
import threading
import time
from typing import Any
from urllib import parse as _urlparse
from urllib import request as _urlrequest
import uuid

from .. import config, playback_service, public_media, state, url_boundary
from ..debug import get_logger
from ..thumb_cache import attach_local_thumbnail
from . import jellyfin_receiver, plex_auth, plex_service
from .plex_client import (
    MAX_BINARY_RESPONSE_BYTES, SAFE_IMAGE_TYPES,
    PlexBinaryResponse, PlexClient, PlexError, PlexStreamResponse,
)

logger = get_logger("browse_service")

MEDIA_SERVERS_FILE = "media_servers.json"
MEDIA_SERVERS_VERSION = 1
SERVER_TYPES = {"jellyfin", "emby", "plex"}
ITEM_ID_PREFIX = "ms~"

_STORE_LOCK = threading.RLock()
_LATENCY_LOCK = threading.RLock()
_SERIES_GROUP_LOCK = threading.RLock()
_CUSTOM_AUTH_LOCK = threading.RLock()

# server_id -> {"latency_ms": float | None, "connected": bool, "last_checked_ts": int | None, "last_error": str, "server_name": str}
_SERVER_METRICS: dict[str, dict[str, Any]] = {}

# canonical_series_id (winning item_id) -> list of (server_id, raw_series_id, title, year)
_SERIES_GROUPS: dict[str, list[tuple[str, str, str, str]]] = {}

# canonical_item_id (winning item_id) -> merged deduplication overlay fields
_DEDUPED_ITEMS: dict[str, dict[str, Any]] = {}
# dedupe_key -> every cached winning reference, including randomized Plex aliases.
_DEDUPE_KEY_TO_ITEM_IDS: dict[str, set[str]] = {}
MAX_DEDUPED_ITEMS = 512

# server_id -> {"access_token": str, "user_id": str, "authenticated_at": float}
_CUSTOM_JF_SESSIONS: dict[str, dict[str, Any]] = {}


def _servers_path() -> str:
    return state._state_path(MEDIA_SERVERS_FILE)


def _empty_store() -> dict[str, Any]:
    return {
        "version": MEDIA_SERVERS_VERSION,
        "servers": [],
        "enabled_plex_machine_ids": [],
    }


def load_servers_store() -> dict[str, Any]:
    with _STORE_LOCK:
        path = _servers_path()
        if not os.path.exists(path):
            return _empty_store()
        raw = state._load_json(path, None)
        if not isinstance(raw, dict):
            return _empty_store()
        servers = raw.get("servers") if isinstance(raw.get("servers"), list) else []
        plex_ids = (
            raw.get("enabled_plex_machine_ids")
            if isinstance(raw.get("enabled_plex_machine_ids"), list)
            else []
        )
        return {
            "version": MEDIA_SERVERS_VERSION,
            "servers": [ copy.deepcopy(s) for s in servers if isinstance(s, dict) ],
            "enabled_plex_machine_ids": [
                str(x).strip() for x in plex_ids if str(x or "").strip()
            ],
        }


def save_servers_store(payload: dict[str, Any]) -> None:
    with _STORE_LOCK:
        path = _servers_path()
        doc = {
            "version": MEDIA_SERVERS_VERSION,
            "servers": [
                copy.deepcopy(s)
                for s in (payload.get("servers") or [])
                if isinstance(s, dict)
            ],
            "enabled_plex_machine_ids": [
                str(x).strip()
                for x in (payload.get("enabled_plex_machine_ids") or [])
                if str(x or "").strip()
            ],
        }
        state._atomic_write_json(path, doc)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def encode_item_id(server_id: str, raw_id: str) -> str:
    sid = str(server_id or "").strip()
    rid = str(raw_id or "").strip()
    if not sid or not rid:
        return rid
    if rid.startswith((ITEM_ID_PREFIX, "ms2~")):
        return rid
    # Keep new IDs safe in a single URL path segment. Accept the original ms~
    # format when decoding so existing browser selections remain usable.
    payload = json.dumps([sid, rid], separators=(",", ":")).encode("utf-8")
    return "ms2~" + base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_item_id(item_id: str) -> tuple[str, str]:
    raw = str(item_id or "").strip()
    if raw.startswith("ms2~"):
        try:
            encoded = raw[4:]
            sid, rid = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if isinstance(sid, str) and isinstance(rid, str) and sid and rid:
                return sid, rid
        except (ValueError, TypeError, UnicodeError):
            pass
    if raw.startswith(ITEM_ID_PREFIX):
        rest = raw[len(ITEM_ID_PREFIX) :]
        parts = rest.split("~", 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            return parts[0].strip(), parts[1].strip()
    return "jf_primary", raw


def _record_latency(
    server_id: str,
    latency_ms: float | None,
    *,
    connected: bool = True,
    error: str = "",
    server_name: str = "",
) -> None:
    with _LATENCY_LOCK:
        prev = _SERVER_METRICS.get(server_id) or {}
        smoothed = latency_ms
        prev_lat = prev.get("latency_ms")
        if latency_ms is not None and isinstance(prev_lat, (int, float)) and prev_lat > 0:
            smoothed = round((float(prev_lat) * 0.35) + (float(latency_ms) * 0.65), 1)
        elif latency_ms is not None:
            smoothed = round(float(latency_ms), 1)
        else:
            smoothed = prev_lat if connected else None
        _SERVER_METRICS[server_id] = {
            "latency_ms": smoothed,
            "connected": bool(connected),
            "last_checked_ts": int(time.time()),
            "last_error": str(error or "").strip(),
            "server_name": str(server_name or prev.get("server_name") or "").strip(),
        }


def get_server_metrics(server_id: str) -> dict[str, Any]:
    with _LATENCY_LOCK:
        return dict(_SERVER_METRICS.get(server_id) or {})


def _normalize_title_key(title: object) -> str:
    raw = str(title or "").strip().lower()
    if not raw:
        return ""
    # Strip trailing year in parentheses if embedded in title
    raw = re.sub(r"\(\s*(?:19|20)\d{2}\s*\)\s*$", "", raw).strip()
    return re.sub(r"[^a-z0-9]+", "", raw)


def _safe_int(val: object) -> int | None:
    try:
        if val is None or val == "":
            return None
        return int(val)
    except (TypeError, ValueError):
        return None


def _safe_float(val: object) -> float | None:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _extract_year(val: object) -> str:
    if val is None:
        return ""
    txt = str(val).strip()
    m = re.search(r"\b((?:19|20)\d{2})\b", txt)
    return m.group(1) if m else ""


def _public_server_entry(entry: dict[str, Any]) -> dict[str, Any]:
    sid = str(entry.get("id") or "").strip()
    enabled = bool(entry.get("enabled", True))
    metrics = get_server_metrics(sid)
    lat = metrics.get("latency_ms")
    if lat is None and entry.get("latency_ms") is not None:
        lat = entry.get("latency_ms")
    if not enabled:
        connected = False
    elif sid in {"jf_primary", "plex_primary"}:
        # Primary servers have authoritative runtime status in entry["connected"];
        # only override with metrics if a probe explicitly recorded a failure while enabled.
        if not bool(entry.get("connected", False)):
            connected = False
        elif metrics.get("connected") is False:
            connected = False
        else:
            connected = True
    else:
        connected = metrics.get("connected")
        if connected is None:
            connected = bool(entry.get("connected", False))
    return {
        "id": sid,
        "name": str(entry.get("name") or metrics.get("server_name") or sid),
        "type": str(entry.get("type") or "jellyfin").lower(),
        "origin": str(entry.get("origin") or "custom"),
        "server_url": str(entry.get("server_url") or ""),
        "enabled": enabled,
        "connected": bool(connected),
        "latency_ms": round(float(lat), 1) if isinstance(lat, (int, float)) else None,
        "last_checked_ts": metrics.get("last_checked_ts"),
        "last_error": str(metrics.get("last_error") or entry.get("last_error") or ""),
        "username": str(entry.get("username") or ""),
        "user_id": str(entry.get("user_id") or ""),
        "machine_id": str(entry.get("machine_id") or ""),
        "has_password": bool(entry.get("password") or entry.get("has_password")),
        "has_api_key": bool(entry.get("api_key") or entry.get("has_api_key")),
        "has_token": bool(entry.get("token") or entry.get("has_token")),
    }


def _collect_configured_servers(*, include_disabled: bool = True) -> list[dict[str, Any]]:
    """Return all configured servers across primary settings, linked Plex, and custom store."""
    out: list[dict[str, Any]] = []
    settings = state.get_settings() if hasattr(state, "get_settings") else {}
    store = load_servers_store()

    # 1. Primary Jellyfin / Emby from jellyfin_receiver / settings
    try:
        jf_st = jellyfin_receiver.status()
    except Exception:
        jf_st = {}
    jf_url = str(
        jf_st.get("server_url") or settings.get("jellyfin_server_url") or ""
    ).strip()
    if "jellyfin_enabled" in settings:
        jf_enabled = bool(settings.get("jellyfin_enabled")) and bool(jf_st.get("enabled", True))
    else:
        jf_enabled = bool(jf_st.get("enabled"))
    jf_type = str(
        jf_st.get("server_type") or settings.get("jellyfin_server_type") or "jellyfin"
    ).strip().lower()
    if jf_type not in {"jellyfin", "emby"}:
        jf_type = "jellyfin"
    if jf_url or jf_enabled:
        jf_connected = bool(
            jf_enabled
            and jf_st.get("running")
            and (jf_st.get("connected") or jf_st.get("authenticated") or jf_st.get("catalog_ready"))
        )
        brand = "Emby" if jf_type == "emby" else "Jellyfin"
        product_name = str(jf_st.get("server_product_name") or "").strip()
        entry = {
            "id": "jf_primary",
            "name": product_name or f"{brand} (Primary)",
            "type": jf_type,
            "origin": "primary",
            "server_url": jf_url,
            "enabled": jf_enabled,
            "connected": jf_connected,
            "last_error": str(jf_st.get("last_error") or ""),
            "username": str(jf_st.get("auth_user") or settings.get("jellyfin_username") or ""),
            "user_id": str(jf_st.get("catalog_user_id") or settings.get("jellyfin_user_id") or ""),
            "has_password": bool(jf_st.get("auth_user_configured")),
            "has_api_key": bool(jf_st.get("api_key_configured")),
        }
        if include_disabled or jf_enabled:
            out.append(entry)

    # 2. Primary Plex server (and any additional enabled Plex servers from linked account)
    plex_enabled = bool(settings.get("plex_enabled", False))
    try:
        plex_st = plex_auth.auth_manager.status()
    except Exception:
        plex_st = {}
    plex_linked = bool(plex_st.get("linked"))
    selected_server = plex_st.get("server") if isinstance(plex_st.get("server"), dict) else {}
    primary_machine_id = str(selected_server.get("machine_id") or "").strip()
    if plex_linked and primary_machine_id:
        entry = {
            "id": "plex_primary",
            "name": f"{selected_server.get('name') or 'Plex'} (Primary)",
            "type": "plex",
            "origin": "primary",
            "server_url": str(selected_server.get("server_url") or ""),
            "machine_id": primary_machine_id,
            "enabled": plex_enabled,
            "connected": bool(plex_enabled and plex_st.get("server_selected")),
            "has_token": True,
        }
        if include_disabled or plex_enabled:
            out.append(entry)

    # Additional custom servers from media_servers.json
    for raw in store.get("servers") or []:
        if not isinstance(raw, dict):
            continue
        sid = str(raw.get("id") or "").strip()
        stype = str(raw.get("type") or "jellyfin").strip().lower()
        if not sid or stype not in SERVER_TYPES:
            continue
        enabled = bool(raw.get("enabled", True))
        if not include_disabled and not enabled:
            continue
        url = str(raw.get("server_url") or "").strip().rstrip("/")
        out.append(
            {
                "id": sid,
                "name": str(raw.get("name") or f"{stype.title()} Server").strip(),
                "type": stype,
                "origin": "custom",
                "server_url": url,
                "enabled": enabled,
                "connected": bool(get_server_metrics(sid).get("connected", bool(url))),
                "username": str(raw.get("username") or "").strip(),
                "password": str(raw.get("password") or ""),
                "api_key": str(raw.get("api_key") or "").strip(),
                "token": str(raw.get("token") or "").strip(),
                "user_id": str(raw.get("user_id") or "").strip(),
                "machine_id": str(raw.get("machine_id") or "").strip(),
            }
        )

    return out


def list_servers_public() -> dict[str, Any]:
    servers = [_public_server_entry(s) for s in _collect_configured_servers(include_disabled=True)]
    servers.sort(
        key=lambda s: (
            not bool(s.get("enabled")),
            not bool(s.get("connected")),
            float(s["latency_ms"]) if isinstance(s.get("latency_ms"), (int, float)) else 999999.0,
            str(s.get("name") or ""),
        )
    )
    return {
        "ok": True,
        "servers": servers,
        "connected_count": sum(1 for s in servers if s.get("enabled") and s.get("connected")),
        "enabled_count": sum(1 for s in servers if s.get("enabled")),
    }


def upsert_custom_server(data: dict[str, Any]) -> dict[str, Any]:
    stype = str(data.get("type") or "jellyfin").strip().lower()
    if stype not in SERVER_TYPES:
        raise ValueError("Server type must be jellyfin, emby, or plex")
    server_url = str(data.get("server_url") or "").strip().rstrip("/")
    if not server_url or not (
        server_url.startswith("http://") or server_url.startswith("https://")
    ):
        raise ValueError("Valid http:// or https:// server_url is required")
    name = str(data.get("name") or "").strip() or f"{stype.title()} ({server_url})"
    sid = str(data.get("id") or "").strip()
    enabled = bool(data.get("enabled", True))

    with _STORE_LOCK:
        store = load_servers_store()
        servers = list(store.get("servers") or [])
        existing_idx = next(
            (i for i, s in enumerate(servers) if str(s.get("id") or "") == sid),
            -1,
        )
        prev = servers[existing_idx] if existing_idx >= 0 else {}
        if not sid or sid in {"jf_primary", "plex_primary"}:
            sid = f"srv_{uuid.uuid4().hex[:8]}"

        password = str(data.get("password") or "")
        if not password and not bool(data.get("clear_password")):
            password = str(prev.get("password") or "")

        api_key = str(data.get("api_key") or "").strip()
        if not api_key and not bool(data.get("clear_api_key")):
            api_key = str(prev.get("api_key") or "").strip()

        token = str(data.get("token") or "").strip()
        if not token and not bool(data.get("clear_token")):
            token = str(prev.get("token") or "").strip()

        record = {
            "id": sid,
            "name": name[:80],
            "type": stype,
            "server_url": server_url,
            "enabled": enabled,
            "username": str(data.get("username") if "username" in data else prev.get("username") or "").strip(),
            "password": password,
            "api_key": api_key,
            "token": token,
            "user_id": str(data.get("user_id") if "user_id" in data else prev.get("user_id") or "").strip(),
            "machine_id": str(data.get("machine_id") if "machine_id" in data else prev.get("machine_id") or "").strip(),
        }
        if existing_idx >= 0:
            servers[existing_idx] = record
        else:
            servers.append(record)
        store["servers"] = servers
        save_servers_store(store)

    with _CUSTOM_AUTH_LOCK:
        _CUSTOM_JF_SESSIONS.pop(sid, None)

    probe_single_server(record)
    return _public_server_entry(record)


def delete_custom_server(server_id: str) -> bool:
    sid = str(server_id or "").strip()
    if not sid or sid in {"jf_primary", "plex_primary"}:
        return False
    with _STORE_LOCK:
        store = load_servers_store()
        before = len(store.get("servers") or [])
        store["servers"] = [
            s for s in (store.get("servers") or []) if str(s.get("id") or "") != sid
        ]
        if len(store["servers"]) == before:
            return False
        save_servers_store(store)
    with _LATENCY_LOCK:
        _SERVER_METRICS.pop(sid, None)
    with _CUSTOM_AUTH_LOCK:
        _CUSTOM_JF_SESSIONS.pop(sid, None)
    return True


def _custom_jf_headers(token: str = "") -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "X-Emby-Authorization": (
            'MediaBrowser Client="RelayTV", Device="RelayTV", '
            'DeviceId="relaytv-browse", Version="1.0"'
        ),
    }
    if token:
        headers["X-Emby-Token"] = token
        headers["X-MediaBrowser-Token"] = token
    return headers


def _custom_jf_get_json(url: str, *, token: str = "", timeout: float = 5.0) -> Any:
    req = _urlrequest.Request(url, method="GET")
    for k, v in _custom_jf_headers(token).items():
        req.add_header(k, v)
    with _urlrequest.urlopen(req, timeout=timeout) as resp:
        raw = (resp.read() or b"{}").decode("utf-8", "ignore")
    return json.loads(raw) if raw.strip() else {}


def _resolve_custom_jf_auth(server: dict[str, Any]) -> tuple[str, str, str]:
    """Return (base_url, token, user_id) for a custom Jellyfin/Emby server."""
    sid = str(server.get("id") or "")
    base = str(server.get("server_url") or "").strip().rstrip("/")
    api_key = str(server.get("api_key") or server.get("token") or "").strip()
    user_id = str(server.get("user_id") or "").strip()
    username = str(server.get("username") or "").strip()
    password = str(server.get("password") or "")

    with _CUSTOM_AUTH_LOCK:
        cached = _CUSTOM_JF_SESSIONS.get(sid)
        if isinstance(cached, dict) and cached.get("base") == base:
            tok = str(cached.get("token") or api_key).strip()
            uid = str(cached.get("user_id") or user_id).strip()
            if tok:
                return base, tok, uid

    token = api_key
    if username and password and base:
        auth_url = f"{base}/Users/AuthenticateByName"
        body = json.dumps({"Username": username, "Pw": password}).encode("utf-8")
        req = _urlrequest.Request(auth_url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        identity = (
            'MediaBrowser Client="RelayTV", Device="RelayTV", '
            'DeviceId="relaytv-browse", Version="1.0"'
        )
        req.add_header("Authorization", identity)
        req.add_header("X-Emby-Authorization", identity)
        try:
            with _urlrequest.urlopen(req, timeout=4.0) as resp:
                raw = (resp.read() or b"{}").decode("utf-8", "ignore")
            data = json.loads(raw) if raw.strip() else {}
            if isinstance(data, dict):
                token = str(data.get("AccessToken") or token).strip()
                user_obj = data.get("User") if isinstance(data.get("User"), dict) else {}
                user_id = str(user_obj.get("Id") or user_id).strip()
        except Exception as exc:
            logger.debug("custom_jf_auth_failed server=%s err=%s", sid, exc)

    if token and not user_id and base:
        try:
            users = _custom_jf_get_json(f"{base}/Users", token=token, timeout=3.5)
            if isinstance(users, list) and users and isinstance(users[0], dict):
                user_id = str(users[0].get("Id") or "").strip()
        except Exception:
            pass

    if token:
        with _CUSTOM_AUTH_LOCK:
            _CUSTOM_JF_SESSIONS[sid] = {
                "base": base,
                "token": token,
                "user_id": user_id,
                "ts": time.time(),
            }
    return base, token, user_id


def probe_single_server(server: dict[str, Any]) -> dict[str, Any]:
    sid = str(server.get("id") or "").strip()
    stype = str(server.get("type") or "jellyfin").strip().lower()
    base = str(server.get("server_url") or "").strip().rstrip("/")
    if not server.get("enabled", True):
        _record_latency(sid, None, connected=False, error="disabled")
        return _public_server_entry(server)

    t0 = time.perf_counter()
    try:
        if sid == "jf_primary":
            st = jellyfin_receiver.status()
            base_url = str(st.get("server_url") or base).strip().rstrip("/")
            if not base_url:
                raise RuntimeError("server_url not configured")
            _base, token, _uid = jellyfin_receiver._catalog_base_token_user()
            info = _custom_jf_get_json(
                f"{base_url}/System/Info/Public", token=token, timeout=3.5
            )
            dt = (time.perf_counter() - t0) * 1000.0
            sname = str(
                (info.get("ServerName") if isinstance(info, dict) else "")
                or server.get("name")
                or ""
            ).strip()
            _record_latency(sid, dt, connected=True, server_name=sname)
        elif sid == "plex_primary":
            session = plex_auth.auth_manager.selected_server_session()
            identity = session.client.get("/identity")
            dt = (time.perf_counter() - t0) * 1000.0
            container = (
                identity.get("MediaContainer") if isinstance(identity, dict) else {}
            )
            _record_latency(
                sid,
                dt,
                connected=True,
                server_name=str(server.get("name") or ""),
                error="" if isinstance(container, dict) else "",
            )
        elif stype in {"jellyfin", "emby"}:
            base_url, token, _uid = _resolve_custom_jf_auth(server)
            if not base_url:
                raise RuntimeError("server_url not configured")
            info = _custom_jf_get_json(
                f"{base_url}/System/Info/Public", token=token, timeout=3.5
            )
            dt = (time.perf_counter() - t0) * 1000.0
            sname = str(
                (info.get("ServerName") if isinstance(info, dict) else "")
                or server.get("name")
                or ""
            ).strip()
            _record_latency(sid, dt, connected=True, server_name=sname)
        elif stype == "plex":
            token = str(server.get("token") or server.get("api_key") or "").strip()
            client = PlexClient(base, token=token, identity=plex_auth.auth_manager.identity)
            client.get("/identity")
            dt = (time.perf_counter() - t0) * 1000.0
            _record_latency(sid, dt, connected=True, server_name=str(server.get("name") or ""))
    except Exception as exc:
        _record_latency(sid, None, connected=False, error=str(exc))
    return _public_server_entry(server)


def probe_all_servers(server_id: str = "") -> dict[str, Any]:
    servers = _collect_configured_servers(include_disabled=True)
    targets = [
        s
        for s in servers
        if (not server_id or str(s.get("id") or "") == server_id) and bool(s.get("enabled", True))
    ]
    if targets:
        with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
            futures = [pool.submit(probe_single_server, s) for s in targets]
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception:
                    pass
    summary = list_servers_public()
    target_ids = {str(server.get("id") or "") for server in targets}
    results = [server for server in summary["servers"] if server["id"] in target_ids]
    return {
        **summary,
        "results": results,
        "result": next((server for server in results if server["id"] == server_id), None),
    }


def _active_servers() -> list[dict[str, Any]]:
    """Return enabled servers that are configured for browsing, ordered by lowest latency."""
    servers = _collect_configured_servers(include_disabled=False)
    ready: list[dict[str, Any]] = []
    for s in servers:
        sid = str(s.get("id") or "")
        if sid == "jf_primary":
            st = jellyfin_receiver.status()
            if not (
                bool(st.get("enabled"))
                and bool(st.get("running"))
                and bool(str(st.get("server_url") or "").strip())
            ):
                continue
        elif sid == "plex_primary":
            st = plex_auth.auth_manager.status()
            if not (bool(st.get("enabled")) and bool(st.get("linked")) and bool(st.get("server_selected"))):
                continue
        else:
            if not str(s.get("server_url") or "").strip():
                continue
        metrics = get_server_metrics(sid)
        s_copy = dict(s)
        s_copy["latency_ms"] = metrics.get("latency_ms")
        s_copy["connected"] = metrics.get("connected", s.get("connected", True))
        ready.append(s_copy)

    ready.sort(
        key=lambda s: (
            float(s["latency_ms"]) if isinstance(s.get("latency_ms"), (int, float)) else 5000.0,
            0 if s.get("origin") == "primary" else 1,
            str(s.get("id") or ""),
        )
    )
    return ready


def status_summary() -> dict[str, Any]:
    all_info = list_servers_public()
    active = _active_servers()
    best_latency = None
    for s in active:
        lat = s.get("latency_ms")
        if isinstance(lat, (int, float)):
            best_latency = lat if best_latency is None else min(best_latency, lat)
    return {
        "ok": True,
        "enabled": len(active) > 0,
        "connected": len(active) > 0,
        "active_server_count": len(active),
        "best_latency_ms": best_latency,
        "servers": all_info["servers"],
    }


def _tag_item_source(
    item: dict[str, Any],
    server: dict[str, Any],
    *,
    latency_ms: float | None = None,
) -> dict[str, Any]:
    out = dict(item)
    sid = str(server.get("id") or "jf_primary").strip()
    stype = str(server.get("type") or "jellyfin").strip().lower()
    sname = str(server.get("name") or stype.title()).strip()
    raw_id = str(out.get("item_id") or out.get("id") or "").strip()
    if not raw_id:
        return out
    out["raw_item_id"] = raw_id
    out["item_id"] = encode_item_id(sid, raw_id)
    if out.get("series_id") and not str(out["series_id"]).startswith((ITEM_ID_PREFIX, "ms2~")):
        out["raw_series_id"] = str(out["series_id"])
        out["series_id"] = encode_item_id(sid, str(out["series_id"]))
    lat = latency_ms if latency_ms is not None else get_server_metrics(sid).get("latency_ms")
    rounded_lat = round(float(lat), 1) if isinstance(lat, (int, float)) else None
    out["source_id"] = sid
    out["server_id"] = sid
    out["source_type"] = stype
    out["server_type"] = stype
    out["source_name"] = sname
    out["server_name"] = sname
    out["source_latency_ms"] = rounded_lat
    out["latency_ms"] = rounded_lat
    base_lib = str(out.get("library_name") or "").strip()
    out["library_name"] = f"{sname} · {base_lib}" if base_lib else sname
    return out


def _adapt_plex_item(
    pitem: dict[str, Any],
    server: dict[str, Any],
    *,
    latency_ms: float | None = None,
) -> dict[str, Any]:
    raw_type = str(pitem.get("type") or "").strip().lower()
    item_type = "series" if raw_type == "show" else raw_type
    raw_id = str(pitem.get("id") or pitem.get("item_id") or "").strip()
    title = str(pitem.get("title") or "Untitled").strip()
    subtitle = str(pitem.get("subtitle") or "").strip()
    year = _extract_year(pitem.get("year")) or _extract_year(subtitle)
    series_name = ""
    season_num = _safe_int(pitem.get("parent_index"))
    episode_num = _safe_int(pitem.get("index"))

    if item_type == "episode":
        # Plex subtitle is "Series Name · S01 E02"
        parts = [p.strip() for p in subtitle.split("·") if p.strip()]
        if parts and not re.match(r"^S\d+\s*E\d+$", parts[0], re.I):
            series_name = parts[0]
        ep_code = ""
        if season_num is not None and episode_num is not None:
            ep_code = f"S{season_num:02d}E{episode_num:02d}"
        ep_title = title
        display_title = series_name or ep_title
        sub_parts = [p for p in (ep_code, ep_title if ep_title != display_title else "") if p]
        subtitle = " · ".join(sub_parts)
        title = display_title
    elif item_type == "movie":
        subtitle = f"Movie · {year}" if year else "Movie"
    elif item_type == "series":
        subtitle = f"Series · {year}" if year else "Series"

    duration_ms = _safe_int(pitem.get("duration_ms")) or 0
    offset_ms = _safe_int(pitem.get("view_offset_ms")) or 0
    runtime_sec = round(duration_ms / 1000.0, 1) if duration_ms > 0 else None
    resume_pos = round(offset_ms / 1000.0, 1) if offset_ms > 0 else None
    progress = _safe_float(pitem.get("progress")) or 0.0
    poster = str(pitem.get("poster_url") or pitem.get("thumbnail") or "").strip()
    backdrop = str(pitem.get("backdrop_url") or poster or "").strip()

    normalized: dict[str, Any] = {
        "item_id": raw_id,
        "title": title,
        "subtitle": subtitle,
        "type": item_type,
        "series_name": series_name or (title if item_type == "series" else ""),
        "series_id": str(pitem.get("series_id") or ""),
        "thumbnail": poster,
        "poster": poster,
        "backdrop": backdrop,
        "year": year,
        "runtime_sec": runtime_sec,
        "resume_pos": resume_pos,
        "progress_percent": runtime_sec and round(progress, 2) or round(progress, 2),
        "is_played": bool(pitem.get("watched")),
        "is_favorite": False,
        "overview": str(pitem.get("summary") or pitem.get("overview") or "").strip(),
        "season_number": season_num if item_type in {"episode", "season"} else None,
        "episode_number": episode_num if item_type == "episode" else None,
        "audio_streams": [],
        "subtitle_streams": [],
        "versions": pitem.get("versions") if isinstance(pitem.get("versions"), list) else [],
    }
    return _tag_item_source(normalized, server, latency_ms=latency_ms)


def _dedupe_key_for_item(item: dict[str, Any]) -> str:
    itype = str(item.get("type") or "").strip().lower()
    if itype == "show":
        itype = "series"
    tmdb_id = _safe_int(item.get("tmdb_id"))
    if itype in {"movie", "series"} and tmdb_id:
        return f"{itype}:tmdb:{tmdb_id}"
    if itype == "episode":
        s_name = _normalize_title_key(item.get("series_name") or item.get("title"))
        s_num = _safe_int(item.get("season_number"))
        e_num = _safe_int(item.get("episode_number"))
        if s_name and s_num is not None and e_num is not None:
            return f"episode:{s_name}:s{s_num}:e{e_num}"
    title_key = _normalize_title_key(item.get("title"))
    if not title_key:
        return f"id:{item.get('item_id')}"
    year = _extract_year(item.get("year")) or _extract_year(item.get("subtitle"))
    if itype in {"movie", "series"}:
        return f"{itype}:{title_key}:{year}" if year else f"{itype}:{title_key}"
    return f"{itype}:{title_key}:{year}"


def _resolve_item_latency_and_source(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    sid = str(out.get("source_id") or out.get("server_id") or "").strip()
    if not sid and str(out.get("item_id") or "").startswith((ITEM_ID_PREFIX, "ms2~")):
        sid, _ = decode_item_id(str(out.get("item_id") or ""))
    stype = str(out.get("source_type") or out.get("server_type") or "").strip().lower()
    sname = str(out.get("source_name") or out.get("server_name") or "").strip()
    lat = out.get("source_latency_ms")
    if not isinstance(lat, (int, float)):
        lat = out.get("latency_ms")
    if not isinstance(lat, (int, float)) and sid:
        lat = get_server_metrics(sid).get("latency_ms")
    rounded_lat = round(float(lat), 1) if isinstance(lat, (int, float)) else None
    if sid:
        out["source_id"] = sid
        out["server_id"] = sid
    if stype:
        out["source_type"] = stype
        out["server_type"] = stype
    if sname:
        out["source_name"] = sname
        out["server_name"] = sname
    out["source_latency_ms"] = rounded_lat
    out["latency_ms"] = rounded_lat
    return out


def deduplicate_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate items across servers, selecting the lowest-latency server's copy."""
    groups: dict[str, list[dict[str, Any]]] = {}
    order_keys: list[str] = []

    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        item = _resolve_item_latency_and_source(raw_item)
        itype = str(item.get("type") or "").strip().lower()
        t_norm = _normalize_title_key(item.get("title"))
        key = _dedupe_key_for_item(item)
        # Also match if an existing group has same type + title_norm when one copy lacks year or tmdb_id
        matched_key = key
        if key not in groups and itype in {"movie", "series"} and t_norm:
            year = _extract_year(item.get("year")) or _extract_year(item.get("subtitle"))
            alt_no_year = f"{itype}:{t_norm}"
            if alt_no_year in groups:
                matched_key = alt_no_year
            elif not year:
                for existing_k in order_keys:
                    if existing_k.startswith(f"{itype}:{t_norm}:"):
                        matched_key = existing_k
                        break
            else:
                # Check if any existing group has same title+year even if keyed by tmdb
                for existing_k, g_items in groups.items():
                    first = g_items[0]
                    if (
                        str(first.get("type") or "").lower() == itype
                        and _normalize_title_key(first.get("title")) == t_norm
                    ):
                        first_yr = _extract_year(first.get("year")) or _extract_year(
                            first.get("subtitle")
                        )
                        if not first_yr or first_yr == year:
                            matched_key = existing_k
                            break

        if matched_key not in groups:
            groups[matched_key] = []
            order_keys.append(matched_key)
        groups[matched_key].append(item)

    deduped: list[dict[str, Any]] = []
    for k in order_keys:
        candidates = groups[k]
        if len(candidates) == 1:
            single = dict(candidates[0])
            single["source_count"] = 1
            single["duplicate_count"] = 1
            if str(single.get("type") or "").lower() == "series":
                _register_series_group(single["item_id"], candidates)
            with _SERIES_GROUP_LOCK:
                _DEDUPED_ITEMS.pop(single["item_id"], None)
                previous_ids = _DEDUPE_KEY_TO_ITEM_IDS.pop(k, set())
                for previous_id in previous_ids:
                    _DEDUPED_ITEMS.pop(previous_id, None)
            deduped.append(single)
            continue

        # Sort candidates by lowest source_latency_ms
        sorted_cands = sorted(
            candidates,
            key=lambda c: (
                float(c["source_latency_ms"])
                if isinstance(c.get("source_latency_ms"), (int, float))
                else 999999.0,
                0 if str(c.get("source_id") or "").endswith("_primary") else 1,
            ),
        )
        winner = dict(sorted_cands[0])
        # Merge best resume_pos / progress across copies so user never loses watch progress
        best_resume = _safe_float(winner.get("resume_pos")) or 0.0
        best_progress = _safe_float(winner.get("progress_percent")) or 0.0
        for alt in sorted_cands[1:]:
            alt_res = _safe_float(alt.get("resume_pos")) or 0.0
            alt_prog = _safe_float(alt.get("progress_percent")) or 0.0
            if alt_res > best_resume:
                best_resume = alt_res
            if alt_prog > best_progress:
                best_progress = alt_prog
            for field in ("overview", "year", "runtime_sec", "backdrop", "poster", "thumbnail"):
                if not winner.get(field) and alt.get(field):
                    winner[field] = alt[field]
        if best_resume > 0:
            winner["resume_pos"] = best_resume
        if best_progress > 0:
            winner["progress_percent"] = round(best_progress, 2)

        unique_sources = []
        seen_sids: set[str] = set()
        for c in sorted_cands:
            sid = str(c.get("source_id") or "")
            if sid and sid not in seen_sids:
                seen_sids.add(sid)
                unique_sources.append(
                    {
                        "source_id": sid,
                        "source_name": c.get("source_name"),
                        "source_type": c.get("source_type"),
                        "latency_ms": c.get("source_latency_ms"),
                        "item_id": c.get("item_id"),
                    }
                )
        winner["source_count"] = len(unique_sources)
        winner["duplicate_count"] = len(unique_sources)
        winner["alternate_sources"] = unique_sources
        if len(unique_sources) > 1:
            winner["library_name"] = (
                f"{winner.get('source_name')} (+{len(unique_sources) - 1} more)"
            )
        if str(winner.get("type") or "").lower() == "series":
            _register_series_group(winner["item_id"], sorted_cands)
        _register_deduped_item(winner, dedupe_key=k)
        deduped.append(winner)

    return deduped


def _register_deduped_item(winner: dict[str, Any], *, dedupe_key: str = "") -> None:
    winning_id = str(winner.get("item_id") or "").strip()
    if not winning_id:
        return
    overlay = {
        "_dedupe_key": dedupe_key,
        "resume_pos": winner.get("resume_pos"),
        "progress_percent": winner.get("progress_percent"),
        "source_count": winner.get("source_count", 1),
        "duplicate_count": winner.get("duplicate_count", 1),
        "alternate_sources": copy.deepcopy(winner.get("alternate_sources") or []),
        "library_name": winner.get("library_name"),
        "overview": winner.get("overview"),
        "year": winner.get("year"),
        "runtime_sec": winner.get("runtime_sec"),
        "backdrop": winner.get("backdrop"),
        "poster": winner.get("poster"),
        "thumbnail": winner.get("thumbnail"),
    }
    with _SERIES_GROUP_LOCK:
        if dedupe_key:
            _DEDUPE_KEY_TO_ITEM_IDS.setdefault(dedupe_key, set()).add(winning_id)
        _DEDUPED_ITEMS[winning_id] = overlay
        while len(_DEDUPED_ITEMS) > MAX_DEDUPED_ITEMS:
            oldest_id = next(iter(_DEDUPED_ITEMS))
            oldest = _DEDUPED_ITEMS.pop(oldest_id)
            oldest_key = str(oldest.get("_dedupe_key") or "")
            aliases = _DEDUPE_KEY_TO_ITEM_IDS.get(oldest_key)
            if aliases is not None:
                aliases.discard(oldest_id)
                if not aliases:
                    _DEDUPE_KEY_TO_ITEM_IDS.pop(oldest_key, None)


def _apply_deduped_item_overlay(item: dict[str, Any]) -> dict[str, Any]:
    winning_id = str(item.get("item_id") or "").strip()
    if not winning_id:
        return item
    with _SERIES_GROUP_LOCK:
        overlay = _DEDUPED_ITEMS.get(winning_id)
    if not isinstance(overlay, dict):
        return item
    out = dict(item)
    cur_res = _safe_float(out.get("resume_pos")) or 0.0
    ov_res = _safe_float(overlay.get("resume_pos")) or 0.0
    if ov_res > cur_res:
        out["resume_pos"] = ov_res
    cur_prog = _safe_float(out.get("progress_percent")) or 0.0
    ov_prog = _safe_float(overlay.get("progress_percent")) or 0.0
    if ov_prog > cur_prog:
        out["progress_percent"] = ov_prog
    if int(overlay.get("duplicate_count") or 1) > 1:
        out["source_count"] = int(overlay["source_count"])
        out["duplicate_count"] = int(overlay["duplicate_count"])
        out["alternate_sources"] = copy.deepcopy(overlay.get("alternate_sources") or [])
        if overlay.get("library_name"):
            out["library_name"] = overlay["library_name"]
    for field in ("overview", "year", "runtime_sec", "backdrop", "poster", "thumbnail"):
        if not out.get(field) and overlay.get(field):
            out[field] = overlay[field]
    return out


def _register_series_group(winning_id: str, candidates: list[dict[str, Any]]) -> None:
    members: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for c in candidates:
        sid = str(c.get("source_id") or "jf_primary").strip()
        rid = str(c.get("raw_item_id") or decode_item_id(str(c.get("item_id") or ""))[1]).strip()
        title = str(c.get("title") or "").strip()
        year = str(c.get("year") or "").strip()
        if sid and rid and (sid, rid) not in seen:
            seen.add((sid, rid))
            members.append((sid, rid, title, year))
    if members:
        with _SERIES_GROUP_LOCK:
            _SERIES_GROUPS[winning_id] = members


def _fetch_server_home_buckets(
    server: dict[str, Any],
    *,
    limit: int,
    refresh: bool,
) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {
        "continue_watching_movies": [],
        "continue_watching_tv": [],
        "recently_added_movies": [],
        "recently_added_tv": [],
    }
    sid = str(server.get("id") or "")
    stype = str(server.get("type") or "jellyfin").lower()
    t0 = time.perf_counter()

    try:
        if sid == "jf_primary":
            payload = jellyfin_receiver.get_home_rows(limit=limit, refresh=refresh)
            dt = (time.perf_counter() - t0) * 1000.0
            _record_latency(sid, dt, connected=True, server_name=str(server.get("name") or ""))
            rows = payload.get("rows") if isinstance(payload, dict) else []
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                rid = str(row.get("id") or "").strip().lower()
                raw_items = row.get("items") if isinstance(row.get("items"), list) else []
                tagged = [
                    _tag_item_source(it, server, latency_ms=dt)
                    for it in raw_items
                    if isinstance(it, dict)
                ]
                if rid == "continue_watching":
                    for it in tagged:
                        t = str(it.get("type") or "").lower()
                        if t == "movie":
                            buckets["continue_watching_movies"].append(it)
                        elif t in {"episode", "series"}:
                            buckets["continue_watching_tv"].append(it)
                elif rid == "next_up":
                    for it in tagged:
                        buckets["continue_watching_tv"].append(it)
                elif rid == "movies":
                    for it in tagged:
                        buckets["recently_added_movies"].append(it)
                        if (_safe_float(it.get("resume_pos")) or 0.0) > 0 and not it.get("is_played"):
                            buckets["continue_watching_movies"].append(it)
                elif rid == "shows":
                    for it in tagged:
                        buckets["recently_added_tv"].append(it)
                        if (_safe_float(it.get("resume_pos")) or 0.0) > 0 and not it.get("is_played"):
                            buckets["continue_watching_tv"].append(it)
                elif rid == "recently_added":
                    for it in tagged:
                        t = str(it.get("type") or "").lower()
                        if t == "movie":
                            buckets["recently_added_movies"].append(it)
                        elif t in {"series", "episode"}:
                            buckets["recently_added_tv"].append(it)
            return buckets

        if sid == "plex_primary" or stype == "plex":
            if sid == "plex_primary":
                home_data = plex_service.catalog_service.home(limit=limit)
                dt = (time.perf_counter() - t0) * 1000.0
                _record_latency(sid, dt, connected=True, server_name=str(server.get("name") or ""))
                rows = home_data.get("rows") if isinstance(home_data, dict) else []
            else:
                rows, dt = _custom_plex_home(server, limit=limit)
                _record_latency(sid, dt, connected=True, server_name=str(server.get("name") or ""))

            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                rtitle = str(row.get("title") or "").lower()
                rid = str(row.get("id") or "").lower()
                raw_items = row.get("items") if isinstance(row.get("items"), list) else []
                adapted = [
                    _adapt_plex_item(it, server, latency_ms=dt)
                    for it in raw_items
                    if isinstance(it, dict)
                ]
                is_continue = any(
                    tok in rid or tok in rtitle
                    for tok in ("continue", "ondeck", "on deck", "inprogress", "in progress")
                )
                is_recent = any(
                    tok in rid or tok in rtitle
                    for tok in ("recent", "added", "latest", "new")
                )
                for it in adapted:
                    itype = str(it.get("type") or "").lower()
                    has_resume = (_safe_float(it.get("resume_pos")) or 0.0) > 0
                    if is_continue or has_resume:
                        if itype == "movie":
                            buckets["continue_watching_movies"].append(it)
                        elif itype in {"episode", "series"}:
                            buckets["continue_watching_tv"].append(it)
                    if is_recent or not is_continue:
                        if itype == "movie":
                            buckets["recently_added_movies"].append(it)
                        elif itype in {"series", "episode"}:
                            buckets["recently_added_tv"].append(it)
            return buckets

        if stype in {"jellyfin", "emby"}:
            base, token, uid = _resolve_custom_jf_auth(server)
            if not base:
                return buckets
            fields = (
                "Overview,ImageTags,BackdropImageTags,ProductionYear,PremiereDate,"
                "RunTimeTicks,UserData,SeriesName,SeriesId,ParentIndexNumber,IndexNumber,ProviderIds"
            )
            q_fields = _urlparse.quote(fields)
            # Fetch Resume, Recent Movies, Recent TV
            if uid:
                res_url = f"{base}/Users/{_urlparse.quote(uid)}/Items/Resume?Limit={limit}&Fields={q_fields}"
                raw_res = jellyfin_receiver._extract_items(
                    _custom_jf_get_json(res_url, token=token, timeout=4.0)
                )
            else:
                raw_res = []
            mov_url = (
                f"{base}/Items?IncludeItemTypes=Movie&Recursive=true"
                f"&SortBy=DateCreated&SortOrder=Descending&Limit={limit}&Fields={q_fields}"
            )
            tv_url = (
                f"{base}/Items?IncludeItemTypes=Series,Episode&Recursive=true"
                f"&SortBy=DateCreated&SortOrder=Descending&Limit={limit}&Fields={q_fields}"
            )
            raw_mov = jellyfin_receiver._extract_items(
                _custom_jf_get_json(mov_url, token=token, timeout=4.0)
            )
            raw_tv = jellyfin_receiver._extract_items(
                _custom_jf_get_json(tv_url, token=token, timeout=4.0)
            )
            dt = (time.perf_counter() - t0) * 1000.0
            _record_latency(sid, dt, connected=True, server_name=str(server.get("name") or ""))

            for raw in raw_res:
                norm = jellyfin_receiver._normalize_catalog_item(raw, base=base, token=token)
                tagged = _tag_item_source(norm, server, latency_ms=dt)
                if str(tagged.get("type") or "").lower() == "movie":
                    buckets["continue_watching_movies"].append(tagged)
                else:
                    buckets["continue_watching_tv"].append(tagged)
            for raw in raw_mov:
                norm = jellyfin_receiver._normalize_catalog_item(raw, base=base, token=token)
                buckets["recently_added_movies"].append(
                    _tag_item_source(norm, server, latency_ms=dt)
                )
            for raw in raw_tv:
                norm = jellyfin_receiver._normalize_catalog_item(raw, base=base, token=token)
                buckets["recently_added_tv"].append(
                    _tag_item_source(norm, server, latency_ms=dt)
                )
            return buckets
    except Exception as exc:
        _record_latency(sid, None, connected=False, error=str(exc))
        logger.warning("browse_home_server_failed server=%s err=%s", sid, exc)
    return buckets


def _custom_plex_client(server: dict[str, Any]) -> PlexClient:
    base = str(server.get("server_url") or "").strip().rstrip("/")
    token = str(server.get("token") or server.get("api_key") or "").strip()
    return PlexClient(base, token=token, identity=plex_auth.auth_manager.identity)


def _custom_plex_raw_to_dict(
    server: dict[str, Any],
    raw: dict[str, Any],
    *,
    detail: bool = False,
) -> dict[str, Any] | None:
    sid = str(server.get("id") or "")
    mtype = str(raw.get("type") or "").strip().lower()
    key = str(raw.get("key") or "").strip()
    if mtype not in {"movie", "show", "season", "episode"} or not key.startswith("/"):
        return None
    if mtype in {"show", "season"}:
        key = key.removesuffix("/children")
    duration = int(raw.get("duration") or 0)
    offset = int(raw.get("viewOffset") or 0)
    progress = round(min(100.0, (offset / duration) * 100.0), 1) if duration > 0 else 0.0
    thumb_path = str(raw.get("thumb") or "").strip()
    art_path = str(raw.get("art") or "").strip()
    poster_url = (
        f"/browse/artwork/{_urlparse.quote(sid)}?path={_urlparse.quote(thumb_path)}"
        if thumb_path.startswith("/")
        else ""
    )
    backdrop_url = (
        f"/browse/artwork/{_urlparse.quote(sid)}?path={_urlparse.quote(art_path)}"
        if art_path.startswith("/")
        else ""
    )
    subtitle_parts: list[str] = []
    if mtype == "episode":
        if raw.get("grandparentTitle"):
            subtitle_parts.append(str(raw["grandparentTitle"]))
        s_idx = _safe_int(raw.get("parentIndex"))
        e_idx = _safe_int(raw.get("index"))
        if s_idx is not None and e_idx is not None:
            subtitle_parts.append(f"S{s_idx:02d} E{e_idx:02d}")
    elif raw.get("year"):
        subtitle_parts.append(str(raw.get("year")))

    out: dict[str, Any] = {
        "id": key,
        "type": mtype,
        "title": str(raw.get("title") or "Untitled"),
        "subtitle": " · ".join(subtitle_parts),
        "series_id": str(raw.get("grandparentKey") or "") if mtype == "episode" else "",
        "year": _safe_int(raw.get("year")),
        "duration_ms": duration or None,
        "view_offset_ms": offset,
        "progress": progress,
        "watched": int(raw.get("viewCount") or 0) > 0,
        "index": _safe_int(raw.get("index")),
        "parent_index": _safe_int(raw.get("parentIndex")),
        "poster_url": poster_url,
        "backdrop_url": backdrop_url,
        "summary": str(raw.get("summary") or ""),
    }
    if detail:
        out["overview"] = str(raw.get("summary") or "")
    return out


def _custom_plex_home(
    server: dict[str, Any], *, limit: int = 24
) -> tuple[list[dict[str, Any]], float]:
    t0 = time.perf_counter()
    client = _custom_plex_client(server)
    payload = client.get(
        "/hubs",
        query={"includeGuids": 1, "includeCollections": 0, "includeExternalMedia": 0},
    )
    dt = (time.perf_counter() - t0) * 1000.0
    container = payload.get("MediaContainer") if isinstance(payload, dict) else {}
    hubs = container.get("Hub") if isinstance(container, dict) else []
    rows: list[dict[str, Any]] = []
    for hub in hubs if isinstance(hubs, list) else []:
        if not isinstance(hub, dict):
            continue
        records = hub.get("Metadata") if isinstance(hub.get("Metadata"), list) else []
        items = [
            it
            for r in records
            if isinstance(r, dict) and (it := _custom_plex_raw_to_dict(server, r)) is not None
        ][:limit]
        if items:
            rows.append(
                {
                    "id": str(hub.get("hubIdentifier") or hub.get("key") or ""),
                    "title": str(hub.get("title") or "Plex"),
                    "items": items,
                }
            )
    return rows, dt


def get_unified_home(*, limit: int = 24, refresh: bool = False) -> dict[str, Any]:
    lim = max(1, min(60, int(limit)))
    servers = _active_servers()
    merged: dict[str, list[dict[str, Any]]] = {
        "continue_watching_movies": [],
        "continue_watching_tv": [],
        "recently_added_movies": [],
        "recently_added_tv": [],
    }
    if servers:
        with ThreadPoolExecutor(max_workers=min(8, len(servers))) as pool:
            futs = [
                pool.submit(_fetch_server_home_buckets, s, limit=lim, refresh=refresh)
                for s in servers
            ]
            for fut in as_completed(futs):
                try:
                    res = fut.result()
                    for key in merged:
                        merged[key].extend(res.get(key) or [])
                except Exception:
                    pass

    # If a server didn't populate recently_added_movies or recently_added_tv from home hubs,
    # backfill from unified movies / series when empty.
    if servers and not merged["recently_added_movies"]:
        try:
            mov = list_unified_movies(sort="added", limit=lim, start=0, refresh=refresh)
            merged["recently_added_movies"] = list(mov.get("items") or [])[:lim]
        except Exception:
            pass
    if servers and not merged["recently_added_tv"]:
        try:
            tv = list_unified_series(sort="added", limit=lim, start=0, refresh=refresh)
            merged["recently_added_tv"] = list(tv.get("items") or [])[:lim]
        except Exception:
            pass

    row_defs = [
        ("recently_added_movies", "Recently Added Movies"),
        ("recently_added_tv", "Recently Added TV"),
        ("continue_watching_movies", "Continue Watching Movies"),
        ("continue_watching_tv", "Continue Watching TV"),
    ]
    rows: list[dict[str, Any]] = []
    for row_id, row_title in row_defs:
        deduped = deduplicate_items(merged[row_id])[:lim]
        if row_id.startswith("continue_watching") and not deduped:
            continue
        rows.append({"id": row_id, "title": row_title, "items": deduped})

    st = status_summary()
    return {
        "ok": True,
        "rows": rows,
        "generated_ts": int(time.time()),
        "connected": st["connected"],
        "active_server_count": st["active_server_count"],
        "best_latency_ms": st["best_latency_ms"],
        "servers": st["servers"],
    }


def _plex_sort_for_unified(sort_key: str) -> str:
    s = str(sort_key or "").strip().lower()
    if s == "added":
        return "added"
    if s == "title_desc":
        return "title_desc"
    if s == "year_asc":
        return "year_asc"
    if s == "year_desc":
        return "year"
    return "title"


def _fetch_server_catalog_items(
    server: dict[str, Any],
    *,
    kind: str,  # "movie" | "series"
    sort: str,
    limit: int,
    start: int,
    starts_with: str,
    refresh: bool,
) -> tuple[list[dict[str, Any]], int]:
    sid = str(server.get("id") or "")
    stype = str(server.get("type") or "jellyfin").lower()
    t0 = time.perf_counter()
    fetch_count = max(limit + start + 1, 120)

    try:
        if sid == "jf_primary":
            fetch_page = (
                jellyfin_receiver.list_movies if kind == "movie" else jellyfin_receiver.list_series
            )
            raw_items: list[dict[str, Any]] = []
            total = 0
            while len(raw_items) < fetch_count:
                payload = fetch_page(
                    sort=sort,
                    limit=min(5000, fetch_count - len(raw_items)),
                    start_index=len(raw_items),
                    starts_with=starts_with,
                    refresh=refresh,
                )
                page = payload.get("items") or []
                total = int(payload.get("count") or len(raw_items) + len(page))
                raw_items.extend(page)
                if not page or len(raw_items) >= total:
                    break
            dt = (time.perf_counter() - t0) * 1000.0
            _record_latency(sid, dt, connected=True, server_name=str(server.get("name") or ""))
            tagged = [
                _tag_item_source(it, server, latency_ms=dt)
                for it in (raw_items if isinstance(raw_items, list) else [])
                if isinstance(it, dict)
            ]
            return tagged, total

        if sid == "plex_primary":
            if starts_with:
                fetch_count = float("inf")
            libs_payload = plex_service.catalog_service.libraries()
            libs = libs_payload.get("libraries") if isinstance(libs_payload, dict) else []
            wanted_section_type = "movie" if kind == "movie" else "show"
            plex_sort = _plex_sort_for_unified(sort)
            items_out: list[dict[str, Any]] = []
            total_count = 0
            for lib in libs if isinstance(libs, list) else []:
                if not isinstance(lib, dict):
                    continue
                if str(lib.get("type") or "").lower() != wanted_section_type:
                    continue
                lib_id = str(lib.get("id") or "").strip()
                if not lib_id:
                    continue
                offset = 0
                while offset < fetch_count:
                    sec_res = plex_service.catalog_service.library_items(
                        lib_id,
                        start=offset,
                        limit=min(100, fetch_count - offset),
                        sort=plex_sort,
                    )
                    sec_items = sec_res.get("items") or []
                    for pit in sec_items:
                        if isinstance(pit, dict):
                            items_out.append(pit)
                    next_offset = sec_res.get("next_start")
                    if offset == 0:
                        total_count += int(sec_res.get("count") or len(sec_items))
                    if next_offset is None or int(next_offset) <= offset:
                        break
                    offset = int(next_offset)
            dt = (time.perf_counter() - t0) * 1000.0
            _record_latency(sid, dt, connected=True, server_name=str(server.get("name") or ""))
            adapted = [_adapt_plex_item(it, server, latency_ms=dt) for it in items_out]
            if starts_with:
                letter = starts_with[0].upper()
                adapted = [
                    it
                    for it in adapted
                    if str(it.get("title") or "").strip().upper().startswith(letter)
                ]
            return adapted, len(adapted) if starts_with else max(total_count, len(adapted))

        if stype in {"jellyfin", "emby"}:
            base, token, uid = _resolve_custom_jf_auth(server)
            if not base:
                return [], 0
            sort_map: dict[str, tuple[str, str]] = {
                "added": ("DateCreated", "Descending"),
                "title_asc": ("SortName", "Ascending"),
                "title_desc": ("SortName", "Descending"),
                "year_desc": ("ProductionYear,SortName", "Descending"),
                "year_asc": ("ProductionYear,SortName", "Ascending"),
            }
            sort_by, sort_order = sort_map.get(sort, sort_map["title_asc"])
            fields = (
                "Overview,ImageTags,BackdropImageTags,ProductionYear,PremiereDate,"
                "RunTimeTicks,UserData,SeriesName,SeriesId,ParentIndexNumber,IndexNumber,ProviderIds"
            )
            rows: list[dict[str, Any]] = []
            total = 0
            while len(rows) < fetch_count:
                params: dict[str, str] = {
                    "IncludeItemTypes": "Movie" if kind == "movie" else "Series",
                    "Recursive": "true",
                    "SortBy": sort_by,
                    "SortOrder": sort_order,
                    "StartIndex": str(len(rows)),
                    "Limit": str(min(5000, fetch_count - len(rows))),
                    "Fields": fields,
                }
                if starts_with:
                    params["NameStartsWithOrGreater"] = starts_with[0].upper()
                q = _urlparse.urlencode(params)
                url = (
                    f"{base}/Users/{_urlparse.quote(uid, safe='')}/Items?{q}"
                    if uid
                    else f"{base}/Items?{q}"
                )
                payload = _custom_jf_get_json(url, token=token, timeout=5.0)
                page_rows = jellyfin_receiver._extract_items(payload)
                total = jellyfin_receiver._extract_total_count(payload, len(rows) + len(page_rows))
                rows.extend(page_rows)
                if not page_rows or len(rows) >= total:
                    break
            dt = (time.perf_counter() - t0) * 1000.0
            _record_latency(sid, dt, connected=True, server_name=str(server.get("name") or ""))
            items = [
                _tag_item_source(
                    jellyfin_receiver._normalize_catalog_item(r, base=base, token=token),
                    server,
                    latency_ms=dt,
                )
                for r in rows
            ]
            return items, total

        if stype == "plex":
            if starts_with:
                fetch_count = float("inf")
            client = _custom_plex_client(server)
            sec_payload = client.get("/library/sections")
            container = (
                sec_payload.get("MediaContainer") if isinstance(sec_payload, dict) else {}
            )
            dirs = container.get("Directory") if isinstance(container, dict) else []
            wanted_type = "movie" if kind == "movie" else "show"
            plex_sort = plex_service.SORTS.get(_plex_sort_for_unified(sort), "titleSort:asc")
            raw_list: list[dict[str, Any]] = []
            total_count = 0
            for d in dirs if isinstance(dirs, list) else []:
                if not isinstance(d, dict):
                    continue
                if str(d.get("type") or "").lower() != wanted_type:
                    continue
                skey = str(d.get("key") or "").strip()
                if not skey:
                    continue
                offset = 0
                while offset < fetch_count:
                    p = client.get(
                        f"/library/sections/{_urlparse.quote(skey, safe='')}/all",
                        query={
                            "sort": plex_sort,
                            "includeGuids": 1,
                            "X-Plex-Container-Start": offset,
                            "X-Plex-Container-Size": min(100, fetch_count - offset),
                        },
                    )
                    c = p.get("MediaContainer") or {}
                    recs = c.get("Metadata") or []
                    section_total = int(c.get("totalSize") or len(recs))
                    if offset == 0:
                        total_count += section_total
                    for r in recs:
                        if isinstance(r, dict):
                            conv = _custom_plex_raw_to_dict(server, r)
                            if conv:
                                raw_list.append(conv)
                    offset += len(recs)
                    if not recs or offset >= section_total:
                        break
            dt = (time.perf_counter() - t0) * 1000.0
            _record_latency(sid, dt, connected=True, server_name=str(server.get("name") or ""))
            adapted = [_adapt_plex_item(it, server, latency_ms=dt) for it in raw_list]
            if starts_with:
                letter = starts_with[0].upper()
                adapted = [
                    it
                    for it in adapted
                    if str(it.get("title") or "").strip().upper().startswith(letter)
                ]
            return adapted, len(adapted) if starts_with else max(total_count, len(adapted))
    except Exception as exc:
        _record_latency(sid, None, connected=False, error=str(exc))
        logger.warning("browse_catalog_failed server=%s kind=%s err=%s", sid, kind, exc)
    return [], 0


def _sort_unified_items(items: list[dict[str, Any]], sort_key: str) -> list[dict[str, Any]]:
    s = str(sort_key or "title_asc").strip().lower()
    if s == "title_asc":
        return sorted(items, key=lambda x: str(x.get("title") or "").lower())
    if s == "title_desc":
        return sorted(items, key=lambda x: str(x.get("title") or "").lower(), reverse=True)
    if s == "year_desc":
        return sorted(
            items,
            key=lambda x: (
                _safe_int(_extract_year(x.get("year"))) or 0,
                str(x.get("title") or "").lower(),
            ),
            reverse=True,
        )
    if s == "year_asc":
        return sorted(
            items,
            key=lambda x: (
                _safe_int(_extract_year(x.get("year"))) or 9999,
                str(x.get("title") or "").lower(),
            ),
        )
    # For "added", preserve the interleaved recency order from servers
    return items


def list_unified_movies(
    *,
    sort: str = "added",
    limit: int = 60,
    start: int = 0,
    starts_with: str = "",
    refresh: bool = False,
) -> dict[str, Any]:
    return _list_unified_catalog(
        kind="movie",
        sort=sort,
        limit=limit,
        start=start,
        starts_with=starts_with,
        refresh=refresh,
    )


def list_unified_series(
    *,
    sort: str = "title_asc",
    limit: int = 60,
    start: int = 0,
    starts_with: str = "",
    refresh: bool = False,
) -> dict[str, Any]:
    return _list_unified_catalog(
        kind="series",
        sort=sort,
        limit=limit,
        start=start,
        starts_with=starts_with,
        refresh=refresh,
    )


def _list_unified_catalog(
    *,
    kind: str,
    sort: str,
    limit: int,
    start: int,
    starts_with: str,
    refresh: bool,
) -> dict[str, Any]:
    lim = max(1, min(5000, int(limit)))
    start_idx = max(0, int(start))
    sort_key = str(sort or ("added" if kind == "movie" else "title_asc")).strip().lower()
    letter = str(starts_with or "").strip()[:1].upper()
    servers = sorted(_active_servers(), key=lambda server: str(server.get("id") or ""))
    per_server_items: list[list[dict[str, Any]]] = []
    unseen_count = 0

    if servers:
        with ThreadPoolExecutor(max_workers=min(8, len(servers))) as pool:
            futs = [
                pool.submit(
                    _fetch_server_catalog_items,
                    s,
                    kind=kind,
                    sort=sort_key,
                    limit=lim,
                    start=start_idx,
                    starts_with=letter,
                    refresh=refresh,
                )
                for s in servers
            ]
            # Preserve server order across pages, regardless of response timing.
            for fut in futs:
                try:
                    items, count = fut.result()
                    unseen_count += max(0, count - len(items))
                    if items:
                        per_server_items.append(items)
                except Exception:
                    pass

    # Interleave items round-robin across servers before deduplicating so "added" sort stays fair
    combined: list[dict[str, Any]] = []
    max_len = max((len(lst) for lst in per_server_items), default=0)
    for i in range(max_len):
        for lst in per_server_items:
            if i < len(lst):
                combined.append(lst[i])

    deduped = deduplicate_items(combined)
    sorted_items = _sort_unified_items(deduped, sort_key)
    # Until every source is exhausted, count is an upper bound: unseen
    # records may still duplicate another server's copy. Keep paging enabled.
    total = len(sorted_items) + unseen_count
    sliced = sorted_items[start_idx : start_idx + lim]
    next_start = (start_idx + len(sliced)) if (start_idx + len(sliced)) < total else None
    st = status_summary()
    return {
        "ok": True,
        "sort": sort_key,
        "items": sliced,
        "count": total,
        "start_index": start_idx,
        "limit": lim,
        "next_start_index": next_start,
        "starts_with": letter,
        "connected": bool(st.get("connected")),
        "active_server_count": int(st.get("active_server_count") or 0),
        "best_latency_ms": st.get("best_latency_ms"),
        "servers": st.get("servers") or [],
    }


def _find_server_by_id(server_id: str) -> dict[str, Any] | None:
    sid = str(server_id or "jf_primary").strip()
    for s in _collect_configured_servers(include_disabled=True):
        if str(s.get("id") or "") == sid:
            return s
    return None


def _series_member_sources(series_id: str) -> list[tuple[dict[str, Any], str]]:
    """Return all (server, raw_series_id) pairs for a potentially deduplicated series."""
    with _SERIES_GROUP_LOCK:
        grouped = list(_SERIES_GROUPS.get(series_id) or [])
    out: list[tuple[dict[str, Any], str]] = []
    seen: set[tuple[str, str]] = set()
    if grouped:
        for sid, rid, _title, _year in grouped:
            srv = _find_server_by_id(sid)
            if srv and (sid, rid) not in seen:
                seen.add((sid, rid))
                out.append((srv, rid))
    sid, rid = decode_item_id(series_id)
    srv = _find_server_by_id(sid)
    if srv and (sid, rid) not in seen:
        out.insert(0, (srv, rid))
    return out


def list_unified_series_seasons(series_id: str, *, refresh: bool = False) -> dict[str, Any]:
    members = _series_member_seasons(series_id, refresh=refresh)
    by_num: dict[int, dict[str, Any]] = {}
    for season in members:
        num = _safe_int(season.get("season_number"))
        if num is None:
            continue
        existing = by_num.get(num)
        if existing is None:
            by_num[num] = season
        else:
            # Prefer lowest latency
            cur_lat = _safe_float(existing.get("source_latency_ms")) or 999999.0
            new_lat = _safe_float(season.get("source_latency_ms")) or 999999.0
            if new_lat < cur_lat:
                by_num[num] = season
    seasons = [by_num[k] for k in sorted(by_num.keys())]
    st = status_summary()
    return {
        "ok": True,
        "series_id": series_id,
        "seasons": seasons,
        "count": len(seasons),
        "connected": st["connected"],
    }


def _all_primary_plex_children(item_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    start = 0
    while True:
        page = plex_service.catalog_service.children(item_id, start=start, limit=100)
        items.extend(item for item in (page.get("items") or []) if isinstance(item, dict))
        next_start = _safe_int(page.get("next_start"))
        if next_start is None or next_start <= start:
            return items
        start = next_start


def _all_custom_plex_children(client: PlexClient, raw_path: str) -> list[dict[str, Any]]:
    safe_path = plex_service._safe_upstream_path(raw_path)
    if not safe_path or not safe_path.startswith("/library/metadata/"):
        return []
    base_path = safe_path.removesuffix("/children").rstrip("/")
    children_path = f"{base_path}/children"
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = (
            {"X-Plex-Container-Start": offset, "X-Plex-Container-Size": 100}
            if offset > 0
            else None
        )
        p = client.get(children_path, query=query) if query else client.get(children_path)
        c = p.get("MediaContainer") if isinstance(p, dict) else {}
        recs = (c.get("Metadata") or []) if isinstance(c, dict) else []
        for r in recs:
            if isinstance(r, dict):
                items.append(r)
        total = _safe_int(c.get("totalSize") if isinstance(c, dict) else None)
        offset += len(recs)
        if not recs or total is None or offset >= total:
            return items


def _series_member_seasons(
    series_id: str, *, refresh: bool = False
) -> list[dict[str, Any]]:
    members = _series_member_sources(series_id)
    out: list[dict[str, Any]] = []
    for server, raw_sid in members:
        sid = str(server.get("id") or "")
        stype = str(server.get("type") or "jellyfin").lower()
        lat = get_server_metrics(sid).get("latency_ms")
        try:
            if sid == "jf_primary":
                res = jellyfin_receiver.list_series_seasons(raw_sid, refresh=refresh)
                for s in res.get("seasons") or []:
                    if isinstance(s, dict):
                        sc = dict(s)
                        sc["series_id"] = series_id
                        sc["source_id"] = sid
                        sc["source_latency_ms"] = lat
                        out.append(sc)
            elif sid == "plex_primary":
                for s in _all_primary_plex_children(raw_sid):
                    if isinstance(s, dict) and str(s.get("type") or "").lower() == "season":
                        num = _safe_int(s.get("index"))
                        if num is None:
                            continue
                        out.append(
                            {
                                "series_id": series_id,
                                "season_id": encode_item_id(sid, str(s.get("id") or "")),
                                "season_number": num,
                                "title": str(s.get("title") or f"Season {num}"),
                                "subtitle": str(s.get("title") or f"Season {num}"),
                                "thumbnail": str(s.get("poster_url") or ""),
                                "source_id": sid,
                                "source_latency_ms": lat,
                            }
                        )
            elif stype in {"jellyfin", "emby"}:
                base, token, uid = _resolve_custom_jf_auth(server)
                if not base:
                    continue
                seasons_raw = jellyfin_receiver._fetch_series_seasons(
                    base=base, series_id=raw_sid, user_id=uid, timeout=4.5, token=token
                )
                for row in seasons_raw:
                    num = _safe_int(row.get("season_number"))
                    if num is None:
                        continue
                    s_id = str(row.get("season_id") or "")
                    thumb = f"{base}/Items/{_urlparse.quote(s_id or raw_sid, safe='')}/Images/Primary"
                    if token:
                        thumb = f"{thumb}?api_key={_urlparse.quote(token, safe='')}"
                    out.append(
                        attach_local_thumbnail(
                            {
                                "series_id": series_id,
                                "season_id": encode_item_id(sid, s_id) if s_id else "",
                                "season_number": num,
                                "title": f"Season {num}",
                                "subtitle": f"Season {num}",
                                "thumbnail": thumb,
                                "source_id": sid,
                                "source_latency_ms": lat,
                            }
                        )
                    )
            elif stype == "plex":
                client = _custom_plex_client(server)
                for r in _all_custom_plex_children(client, raw_sid):
                    num = _safe_int(r.get("index"))
                    if num is None:
                        continue
                    conv = _custom_plex_raw_to_dict(server, r)
                    out.append(
                        {
                            "series_id": series_id,
                            "season_id": encode_item_id(sid, str((conv or {}).get("id") or "")),
                            "season_number": num,
                            "title": str(r.get("title") or f"Season {num}"),
                            "subtitle": str(r.get("title") or f"Season {num}"),
                            "thumbnail": str((conv or {}).get("poster_url") or ""),
                            "source_id": sid,
                            "source_latency_ms": lat,
                        }
                    )
        except Exception as exc:
            logger.debug("browse_seasons_failed server=%s err=%s", sid, exc)
    return out


def list_unified_series_episodes(
    series_id: str,
    *,
    season_id: str = "",
    season_number: int | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    members = _series_member_sources(series_id)
    all_episodes: list[dict[str, Any]] = []
    wanted_season = _safe_int(season_number)
    wanted_season_srv, wanted_season_raw = decode_item_id(season_id) if season_id else ("", "")

    for server, raw_sid in members:
        sid = str(server.get("id") or "")
        stype = str(server.get("type") or "jellyfin").lower()
        lat = get_server_metrics(sid).get("latency_ms")
        raw_seas = wanted_season_raw if (season_id and wanted_season_srv == sid) else ""
        try:
            if sid == "jf_primary":
                res = jellyfin_receiver.list_series_episodes(
                    raw_sid,
                    season_id=raw_seas,
                    season_number=wanted_season,
                    refresh=refresh,
                )
                for ep in res.get("episodes") or []:
                    if isinstance(ep, dict):
                        tagged = _tag_item_source(ep, server, latency_ms=lat)
                        tagged["series_id"] = series_id
                        all_episodes.append(tagged)
            elif sid == "plex_primary":
                seasons_list = _all_primary_plex_children(raw_sid)
                wanted_path = ""
                if wanted_season is None and raw_seas:
                    catalog = plex_service.catalog_service
                    session = catalog.auth_manager.selected_server_session()
                    wanted_path = catalog._resolve(session, raw_seas, expected_kind="item")["path"]
                for s in seasons_list:
                    if not isinstance(s, dict) or str(s.get("type") or "").lower() != "season":
                        continue
                    s_num = _safe_int(s.get("index"))
                    s_ref = str(s.get("id") or "")
                    if wanted_season is not None and s_num != wanted_season:
                        continue
                    if wanted_path:
                        path = catalog._resolve(session, s_ref, expected_kind="item")["path"]
                        if path != wanted_path:
                            continue
                    if not s_ref:
                        continue
                    for ep in _all_primary_plex_children(s_ref):
                        if isinstance(ep, dict) and str(ep.get("type") or "").lower() == "episode":
                            adapted = _adapt_plex_item(ep, server, latency_ms=lat)
                            adapted["series_id"] = series_id
                            if adapted.get("season_number") is None:
                                adapted["season_number"] = s_num
                            all_episodes.append(adapted)
            elif stype in {"jellyfin", "emby"}:
                base, token, uid = _resolve_custom_jf_auth(server)
                if not base:
                    continue
                fields = (
                    "Overview,ImageTags,BackdropImageTags,ProductionYear,PremiereDate,"
                    "RunTimeTicks,UserData,SeriesName,SeriesId,ParentIndexNumber,IndexNumber,"
                    "MediaStreams,DefaultAudioStreamIndex,DefaultSubtitleStreamIndex"
                )
                if wanted_season is not None:
                    rows = jellyfin_receiver._fetch_series_episodes_for_season_number(
                        base=base,
                        series_id=raw_sid,
                        season_number=int(wanted_season),
                        user_id=uid,
                        fields=fields,
                        timeout=5.0,
                        token=token,
                    )
                else:
                    q_f = _urlparse.quote(fields)
                    u = f"{base}/Shows/{_urlparse.quote(raw_sid, safe='')}/Episodes?Limit=5000&Fields={q_f}"
                    if raw_seas:
                        u += f"&SeasonId={_urlparse.quote(raw_seas, safe='')}"
                    if uid:
                        u += f"&UserId={_urlparse.quote(uid, safe='')}"
                    rows = jellyfin_receiver._extract_items(
                        _custom_jf_get_json(u, token=token, timeout=5.0)
                    )
                for r in rows:
                    norm = jellyfin_receiver._normalize_catalog_item(r, base=base, token=token)
                    tagged = _tag_item_source(norm, server, latency_ms=lat)
                    tagged["series_id"] = series_id
                    all_episodes.append(tagged)
            elif stype == "plex":
                client = _custom_plex_client(server)
                seasons_list = _all_custom_plex_children(client, raw_sid)
                for s in seasons_list:
                    if not isinstance(s, dict):
                        continue
                    s_num = _safe_int(s.get("index"))
                    if wanted_season is not None and s_num != wanted_season:
                        continue
                    s_key = plex_service._safe_upstream_path(s.get("key")).removesuffix("/children").rstrip("/")
                    if wanted_season is None and raw_seas and s_key != raw_seas.removesuffix("/children").rstrip("/"):
                        continue
                    if not s_key.startswith("/library/metadata/"):
                        continue
                    for r in _all_custom_plex_children(client, s_key):
                        conv = _custom_plex_raw_to_dict(server, r)
                        if conv:
                            adapted = _adapt_plex_item(conv, server, latency_ms=lat)
                            adapted["series_id"] = series_id
                            all_episodes.append(adapted)
        except Exception as exc:
            logger.debug("browse_episodes_failed server=%s err=%s", sid, exc)

    deduped = deduplicate_items(all_episodes)
    deduped.sort(
        key=lambda x: (
            _safe_int(x.get("season_number")) if _safe_int(x.get("season_number")) is not None else 10000,
            _safe_int(x.get("episode_number")) if _safe_int(x.get("episode_number")) is not None else 10000,
            str(x.get("title") or "").lower(),
        )
    )
    st = status_summary()
    return {
        "ok": True,
        "series_id": series_id,
        "season_id": season_id,
        "season_number": wanted_season,
        "episodes": deduped,
        "count": len(deduped),
        "connected": st["connected"],
    }


def search_unified_catalog(
    query: str, *, limit: int = 30, refresh: bool = False
) -> dict[str, Any]:
    q = str(query or "").strip()
    if not q:
        return {"ok": True, "query": "", "items": [], "count": 0, "connected": True}
    lim = max(1, min(100, int(limit)))
    servers = _active_servers()
    results_per_server: list[list[dict[str, Any]]] = []

    def _search_one(server: dict[str, Any]) -> list[dict[str, Any]]:
        sid = str(server.get("id") or "")
        stype = str(server.get("type") or "jellyfin").lower()
        t0 = time.perf_counter()
        try:
            if sid == "jf_primary":
                res = jellyfin_receiver.search_catalog(q, limit=lim, refresh=refresh)
                dt = (time.perf_counter() - t0) * 1000.0
                _record_latency(sid, dt, connected=True, server_name=str(server.get("name") or ""))
                return [
                    _tag_item_source(it, server, latency_ms=dt)
                    for it in (res.get("items") or [])
                    if isinstance(it, dict)
                ]
            if sid == "plex_primary":
                res = plex_service.catalog_service.search(q, limit=lim)
                dt = (time.perf_counter() - t0) * 1000.0
                _record_latency(sid, dt, connected=True, server_name=str(server.get("name") or ""))
                return [
                    _adapt_plex_item(it, server, latency_ms=dt)
                    for it in (res.get("items") or [])
                    if isinstance(it, dict) and str(it.get("type") or "").lower() != "season"
                ]
            if stype in {"jellyfin", "emby"}:
                base, token, uid = _resolve_custom_jf_auth(server)
                if not base:
                    return []
                params = _urlparse.urlencode(
                    {
                        "SearchTerm": q,
                        "Recursive": "true",
                        "Limit": str(lim),
                        "IncludeItemTypes": "Movie,Episode,Series",
                        "Fields": "Overview,ImageTags,BackdropImageTags,ProductionYear,PremiereDate,RunTimeTicks,UserData,SeriesName,SeriesId,ParentIndexNumber,IndexNumber,ProviderIds",
                    }
                )
                url = (
                    f"{base}/Users/{_urlparse.quote(uid)}/Items?{params}"
                    if uid
                    else f"{base}/Items?{params}"
                )
                payload = _custom_jf_get_json(url, token=token, timeout=5.0)
                dt = (time.perf_counter() - t0) * 1000.0
                _record_latency(sid, dt, connected=True, server_name=str(server.get("name") or ""))
                rows = jellyfin_receiver._extract_items(payload)
                return [
                    _tag_item_source(
                        jellyfin_receiver._normalize_catalog_item(r, base=base, token=token),
                        server,
                        latency_ms=dt,
                    )
                    for r in rows
                ]
            if stype == "plex":
                client = _custom_plex_client(server)
                payload = client.get(
                    "/hubs/search", query={"query": q, "limit": lim, "includeGuids": 1}
                )
                dt = (time.perf_counter() - t0) * 1000.0
                _record_latency(sid, dt, connected=True, server_name=str(server.get("name") or ""))
                c = payload.get("MediaContainer") if isinstance(payload, dict) else {}
                out: list[dict[str, Any]] = []
                for hub in (c.get("Hub") or []) if isinstance(c, dict) else []:
                    if not isinstance(hub, dict):
                        continue
                    for r in hub.get("Metadata") or []:
                        if isinstance(r, dict):
                            conv = _custom_plex_raw_to_dict(server, r)
                            if conv and str(conv.get("type") or "").lower() != "season":
                                out.append(_adapt_plex_item(conv, server, latency_ms=dt))
                return out
        except Exception as exc:
            _record_latency(sid, None, connected=False, error=str(exc))
            logger.debug("browse_search_failed server=%s err=%s", sid, exc)
        return []

    if servers:
        with ThreadPoolExecutor(max_workers=min(8, len(servers))) as pool:
            futs = [pool.submit(_search_one, s) for s in servers]
            for fut in as_completed(futs):
                try:
                    res = fut.result()
                    if res:
                        results_per_server.append(res)
                except Exception:
                    pass

    combined: list[dict[str, Any]] = []
    max_len = max((len(lst) for lst in results_per_server), default=0)
    for i in range(max_len):
        for lst in results_per_server:
            if i < len(lst):
                combined.append(lst[i])

    deduped = deduplicate_items(combined)[:lim]
    st = status_summary()
    return {
        "ok": True,
        "query": q,
        "items": deduped,
        "count": len(deduped),
        "connected": bool(st.get("connected")),
        "active_server_count": int(st.get("active_server_count") or 0),
        "best_latency_ms": st.get("best_latency_ms"),
        "servers": st.get("servers") or [],
    }


def get_unified_item_detail(item_id: str, *, refresh: bool = False) -> dict[str, Any] | None:
    sid, raw_id = decode_item_id(item_id)
    server = _find_server_by_id(sid)
    if not server or not raw_id:
        return None
    stype = str(server.get("type") or "jellyfin").lower()
    lat = get_server_metrics(sid).get("latency_ms")
    tagged: dict[str, Any] | None = None

    if sid == "jf_primary":
        item = jellyfin_receiver.get_item_detail(raw_id, refresh=refresh)
        if not item:
            return None
        tagged = _tag_item_source(item, server, latency_ms=lat)
    elif sid == "plex_primary":
        res = plex_service.catalog_service.item_detail(raw_id)
        pitem = res.get("item") if isinstance(res, dict) else None
        if not isinstance(pitem, dict):
            return None
        # Plex encrypts a fresh reference for each response. Retain the requested
        # reference so detail, cached overlays, and later actions share an identity.
        pitem = {**pitem, "id": raw_id}
        tagged = _adapt_plex_item(pitem, server, latency_ms=lat)
    elif stype in {"jellyfin", "emby"}:
        base, token, uid = _resolve_custom_jf_auth(server)
        if not base:
            return None
        quoted = _urlparse.quote(raw_id, safe="")
        fields = (
            "Overview,ImageTags,BackdropImageTags,ProductionYear,PremiereDate,RunTimeTicks,"
            "UserData,SeriesId,SeriesName,ParentIndexNumber,IndexNumber,MediaStreams,"
            "DefaultAudioStreamIndex,DefaultSubtitleStreamIndex,ProviderIds"
        )
        url = (
            f"{base}/Users/{_urlparse.quote(uid, safe='')}/Items/{quoted}?Fields={fields}"
            if uid
            else f"{base}/Items/{quoted}?Fields={fields}"
        )
        payload = _custom_jf_get_json(url, token=token, timeout=5.0)
        if not isinstance(payload, dict) or not payload:
            return None
        norm = jellyfin_receiver._normalize_catalog_item(payload, base=base, token=token)
        tagged = _tag_item_source(norm, server, latency_ms=lat)
    elif stype == "plex":
        safe_path = plex_service._safe_upstream_path(raw_id)
        if not safe_path or not safe_path.startswith("/library/metadata/"):
            return None
        client = _custom_plex_client(server)
        payload = client.get(safe_path, query={"includeGuids": 1, "includeExtras": 0})
        c = payload.get("MediaContainer") if isinstance(payload, dict) else {}
        recs = (c.get("Metadata") or []) if isinstance(c, dict) else []
        if not recs or not isinstance(recs[0], dict):
            return None
        conv = _custom_plex_raw_to_dict(server, recs[0], detail=True)
        tagged = _adapt_plex_item(conv, server, latency_ms=lat) if conv else None

    if not tagged:
        return None
    return _apply_deduped_item_overlay(tagged)


def get_unified_item_adjacent(item_id: str, *, refresh: bool = False) -> dict[str, Any]:
    sid, raw_id = decode_item_id(item_id)
    server = _find_server_by_id(sid)
    if not server or not raw_id:
        return {"prev": None, "next": None}
    lat = get_server_metrics(sid).get("latency_ms")
    if sid == "jf_primary":
        nav = jellyfin_receiver.get_adjacent_episodes(raw_id, refresh=refresh)
        prev = nav.get("prev") if isinstance(nav, dict) else None
        nxt = nav.get("next") if isinstance(nav, dict) else None
        if isinstance(prev, dict) or isinstance(nxt, dict):
            return {
                "prev": _tag_item_source(prev, server, latency_ms=lat) if isinstance(prev, dict) else None,
                "next": _tag_item_source(nxt, server, latency_ms=lat) if isinstance(nxt, dict) else None,
            }
    try:
        detail = get_unified_item_detail(item_id, refresh=refresh)
        if not isinstance(detail, dict) or str(detail.get("type") or "").lower() != "episode":
            return {"prev": None, "next": None}
        series_id = str(detail.get("series_id") or "").strip()
        if not series_id:
            return {"prev": None, "next": None}
        ep_res = list_unified_series_episodes(series_id, refresh=refresh)
        episodes = ep_res.get("episodes") if isinstance(ep_res, dict) else []
        if not isinstance(episodes, list) or not episodes:
            return {"prev": None, "next": None}
        cur_s = _safe_int(detail.get("season_number"))
        cur_e = _safe_int(detail.get("episode_number"))
        idx = next(
            (
                i
                for i, ep in enumerate(episodes)
                if isinstance(ep, dict)
                and (
                    str(ep.get("item_id") or "") == str(detail.get("item_id") or item_id)
                    or (
                        cur_s is not None
                        and cur_e is not None
                        and _safe_int(ep.get("season_number")) == cur_s
                        and _safe_int(ep.get("episode_number")) == cur_e
                    )
                )
            ),
            -1,
        )
        if idx < 0:
            return {"prev": None, "next": None}
        return {
            "prev": episodes[idx - 1] if idx > 0 else None,
            "next": episodes[idx + 1] if idx + 1 < len(episodes) else None,
        }
    except Exception:
        return {"prev": None, "next": None}


def perform_unified_action(
    item_id: str,
    command: str = "play_now",
    *,
    resume_pos: float | None = None,
    version_id: str = "",
    audio_id: str = "",
    subtitle_id: str = "",
) -> dict[str, Any]:
    sid, raw_id = decode_item_id(item_id)
    server = _find_server_by_id(sid)
    if not server or not raw_id:
        raise ValueError("Item or media server not found")
    stype = str(server.get("type") or "jellyfin").lower()
    cmd = str(command or "play_now").strip().lower()
    if cmd not in {"play_now", "play_next", "play_last", "resume"}:
        raise ValueError("unsupported command")

    canonical_id = encode_item_id(sid, raw_id)
    if cmd == "resume" and resume_pos is None:
        with _SERIES_GROUP_LOCK:
            overlay = _DEDUPED_ITEMS.get(canonical_id) or _DEDUPED_ITEMS.get(item_id)
        if isinstance(overlay, dict) and (_safe_float(overlay.get("resume_pos")) or 0.0) > 0:
            resume_pos = float(overlay["resume_pos"])

    if sid == "jf_primary":
        from ..routes import jellyfin as jf_routes

        res = jf_routes.jellyfin_item_action(
            jf_routes.JellyfinItemActionReq(
                item_id=raw_id,
                command=cmd,
                resume_pos=resume_pos,
            )
        )
        if isinstance(res, dict):
            return res
        return {"ok": True}

    if sid == "plex_primary":
        res = plex_service.catalog_service.action(
            raw_id,
            cmd,
            version_id=version_id,
            audio_id=audio_id,
            subtitle_id=subtitle_id,
            resume_pos=resume_pos,
        )
        # Format response so jellyfin.js understands both queue and play actions cleanly
        if cmd in {"play_next", "play_last"}:
            return {
                "ok": True,
                "action": "queue_only",
                "queued": 1,
                "queue_length": res.get("queue_length", 1),
                "item": res.get("item"),
            }
        return {
            "ok": True,
            "action": "play",
            "now_playing": res.get("now_playing"),
        }

    # Custom Jellyfin/Emby or custom Plex server
    detail = get_unified_item_detail(item_id, refresh=False) or {}
    title = str(detail.get("title") or "Media item").strip()
    sub = str(detail.get("subtitle") or "").strip()
    display_title = f"{title} — {sub}" if sub and detail.get("type") == "episode" else title
    thumb = str(detail.get("poster") or detail.get("thumbnail") or "").strip()

    if stype in {"jellyfin", "emby"}:
        base, token, _uid = _resolve_custom_jf_auth(server)
        if not base:
            raise ValueError("Server URL not configured")
        stream_url = f"{base}/Videos/{_urlparse.quote(raw_id, safe='')}/stream?static=true"
        if token:
            stream_url += f"&api_key={_urlparse.quote(token, safe='')}"
        queue_entry = {
            "url": stream_url,
            "title": display_title,
            "provider": stype,
            "thumbnail": thumb,
        }
    else:
        # Keep a server-aware durable reference so the normal player lifecycle
        # can resolve this source again and send its watch-state reports.
        queue_entry = {
            "url": "https://plex.invalid/item",
            "title": display_title,
            "provider": "plex",
            "thumbnail": thumb,
            "plex_item_id": encode_item_id(sid, raw_id),
        }
        if detail.get("runtime_sec"):
            queue_entry["duration_sec"] = detail["runtime_sec"]

    if cmd == "play_next":
        qlen, _snap = playback_service.queue_item_next(queue_entry)
        return {"ok": True, "action": "queue_only", "queued": 1, "queue_length": qlen}
    if cmd == "play_last":
        qlen, _snap = playback_service.queue_item(queue_entry)
        return {"ok": True, "action": "queue_only", "queued": 1, "queue_length": qlen}

    start_pos = (
        float(resume_pos)
        if cmd == "resume" and resume_pos is not None
        else (float(detail["resume_pos"]) if cmd == "resume" and detail.get("resume_pos") else None)
    )
    now = playback_service.play_now(
        queue_entry,
        use_resolver=False,
        cec=False,
        clear_queue=True,
        mode="browse_play",
        start_pos=start_pos,
    )
    return {
        "ok": True,
        "action": "play",
        "now_playing": now,
        "resolved_resume_pos": start_pos,
    }


def play_all_unified_series(series_id: str, *, refresh: bool = False) -> dict[str, Any]:
    with playback_service.queue_import():
        return _play_all_unified_series(series_id, refresh=refresh)


def _play_all_unified_series(series_id: str, *, refresh: bool = False) -> dict[str, Any]:
    ep_res = list_unified_series_episodes(series_id, refresh=refresh)
    episodes = ep_res.get("episodes") if isinstance(ep_res, dict) else []
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("No playable episodes found for series")
    first = episodes[0]
    first_id = str(first.get("item_id") or "").strip()
    with playback_service.queue_commit():
        pass
    play_res = perform_unified_action(first_id, "play_now")
    queued = 0
    cancelled = False
    for ep in episodes[1:]:
        ep_id = str(ep.get("item_id") or "").strip()
        if not ep_id:
            continue
        try:
            with playback_service.queue_commit():
                pass
            perform_unified_action(ep_id, "play_last")
            queued += 1
        except playback_service.QueueImportCancelled:
            cancelled = True
            break
        except Exception:
            continue
    return {
        "ok": True,
        "series_id": series_id,
        "series_title": str(first.get("series_name") or first.get("title") or "Series"),
        "queued_count": queued,
        "cancelled": cancelled,
        "now_playing": play_res.get("now_playing"),
    }


def _custom_plex_item_source(item_id: str) -> tuple[dict[str, Any], str]:
    sid, raw_path = decode_item_id(item_id)
    server = _find_server_by_id(sid)
    path = plex_service._safe_upstream_path(raw_path)
    if (
        not server or sid in {"jf_primary", "plex_primary"}
        or server.get("type") != "plex" or not server.get("enabled", True)
        or not re.fullmatch(r"/library/metadata/[^/?]+", path)
    ):
        raise PlexError("plex_invalid_reference", "Custom Plex item is unavailable", status_code=404)
    return server, path


def resolve_custom_plex_playback_item(item: dict[str, Any]) -> dict[str, Any]:
    server, path = _custom_plex_item_source(str(item.get("plex_item_id") or ""))
    client = _custom_plex_client(server)
    payload = client.get(path, query={"includeGuids": 1, "includeExtras": 0})
    records = (payload.get("MediaContainer") or {}).get("Metadata") or []
    if not records or records[0].get("type") not in {"movie", "episode"}:
        raise PlexError("plex_not_found", "Custom Plex media item not found", status_code=404)
    part, _media, _mi, _pi = plex_service.PlexCatalogService._select_playback_part(records[0])
    part_path = plex_service._safe_upstream_path(part.get("key"))
    if not part_path.startswith("/library/parts/"):
        raise PlexError("plex_media_unavailable", "Plex media part is unavailable", status_code=502)
    sid = str(server["id"])
    url = (
        f"http://127.0.0.1:{config.server_port()}/browse/stream/{_urlparse.quote(sid, safe='')}"
        f"?path={_urlparse.quote(part_path, safe='')}"
    )
    resolved = {**item, "url": url, "plex_stream_mode": "direct"}
    duration = _safe_float(records[0].get("duration"))
    if duration and duration > 0:
        resolved["duration_sec"] = duration / 1000.0
    return resolved


def report_custom_plex_timeline(
    now: dict[str, Any], *, playback_state: str,
    position_sec: float | None = None, duration_sec: float | None = None,
) -> bool:
    server, path = _custom_plex_item_source(str(now.get("plex_item_id") or ""))
    client = _custom_plex_client(server)
    position = _safe_float(position_sec if position_sec is not None else now.get("resume_pos")) or 0
    duration = _safe_float(duration_sec if duration_sec is not None else now.get("duration_sec")) or 0
    client.request_no_content("POST", "/:/timeline", query={
        "ratingKey": path.rsplit("/", 1)[1], "key": path,
        "identifier": "com.plexapp.plugins.library", "state": playback_state,
        "time": max(0, int(position * 1000)), "duration": max(0, int(duration * 1000)),
    })
    return True


_JF_IMAGE_PATH = re.compile(r"/Items/[A-Za-z0-9_-]+/Images/(?:Primary|Backdrop/[0-9]+)\Z")
_ARTWORK_FIELDS = {"thumbnail", "poster", "backdrop", "image", "art"}


def public_browse_payload(payload: Any) -> Any:
    """Keep credentials internal while preserving browser access to artwork."""
    servers = _collect_configured_servers(include_disabled=True)

    def artwork_url(raw: str) -> str:
        for server in servers:
            if server.get("type") not in {"jellyfin", "emby"}:
                continue
            base = str(server.get("server_url") or "").rstrip("/")
            if not base or not raw.startswith(base + "/Items/"):
                continue
            parts = _urlparse.urlsplit(raw[len(base):])
            if not _JF_IMAGE_PATH.fullmatch(parts.path):
                break
            query = [(key, value) for key, value in _urlparse.parse_qsl(parts.query) if key == "tag"]
            path = parts.path + ("?" + _urlparse.urlencode(query) if query else "")
            return f"/browse/artwork/{_urlparse.quote(str(server['id']), safe='')}?path={_urlparse.quote(path, safe='')}"
        return public_media.sanitize_public_url(raw)

    def visit(value):
        if isinstance(value, list):
            return [visit(entry) for entry in value]
        if not isinstance(value, dict):
            return value
        safe = public_media.public_media_item(value)
        for key in safe:
            raw = value.get(key)
            safe[key] = artwork_url(raw) if key in _ARTWORK_FIELDS and isinstance(raw, str) else visit(safe[key])
        return safe

    return visit(payload)


class _NoArtworkRedirect(_urlrequest.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_server_artwork(server_id: str, path: str) -> PlexBinaryResponse:
    server = _find_server_by_id(server_id)
    if not server:
        raise PlexError("browse_not_found", "Media server not found", status_code=404)
    if server.get("type") == "plex":
        return fetch_custom_plex_artwork(server_id, path)
    parts = url_boundary.parse_url(path)
    if (
        parts is None or server.get("type") not in {"jellyfin", "emby"}
        or parts.scheme or parts.raw_netloc or parts.fragment
        or not _JF_IMAGE_PATH.fullmatch(parts.path)
        or any(key != "tag" for key, _ in _urlparse.parse_qsl(parts.query, keep_blank_values=True))
    ):
        raise PlexError("browse_invalid_artwork", "Invalid artwork path", status_code=400)
    try:
        if server_id == "jf_primary":
            base, token, _uid = jellyfin_receiver._catalog_base_token_user()
        else:
            base, token, _uid = _resolve_custom_jf_auth(server)
        req = _urlrequest.Request(base.rstrip("/") + path, headers=_custom_jf_headers(token))
        opener = _urlrequest.build_opener(_NoArtworkRedirect())
        with opener.open(req, timeout=5.0) as response:
            body = response.read(MAX_BINARY_RESPONSE_BYTES + 1)
            media_type = str(response.headers.get("Content-Type") or "").partition(";")[0].strip().lower()
        if not body or len(body) > MAX_BINARY_RESPONSE_BYTES or media_type not in SAFE_IMAGE_TYPES:
            raise ValueError("Invalid image response")
    except Exception:
        raise PlexError("browse_artwork_unavailable", "Media artwork is unavailable", status_code=502) from None
    return PlexBinaryResponse(body=body, content_type=media_type)


def fetch_custom_plex_artwork(server_id: str, path: str) -> PlexBinaryResponse:
    server = _find_server_by_id(server_id)
    if not server or str(server.get("type") or "").lower() != "plex":
        raise PlexError("plex_not_found", "Plex server not found", status_code=404)
    safe_path = plex_service._safe_upstream_path(path)
    if not safe_path:
        raise PlexError("plex_invalid_reference", "Invalid artwork path", status_code=400)
    client = _custom_plex_client(server)
    return client.get_bytes(
        safe_path, query={"width": 1000, "height": 1500, "minSize": 1, "upscale": 0}
    )


def open_custom_plex_stream(
    server_id: str, path: str, *, range_header: str = ""
) -> PlexStreamResponse:
    server = _find_server_by_id(server_id)
    if not server or str(server.get("type") or "").lower() != "plex":
        raise PlexError("plex_not_found", "Plex server not found", status_code=404)
    safe_path = plex_service._safe_upstream_path(path)
    if not safe_path or not safe_path.startswith("/library/parts/"):
        raise PlexError("plex_invalid_reference", "Invalid stream path", status_code=400)
    client = _custom_plex_client(server)
    return client.open_stream(safe_path, range_header=range_header)
