# SPDX-License-Identifier: GPL-3.0-only
"""IPTV product service: source discovery, M3U refresh, catalog, and actions."""
from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import threading
import time
import uuid
import urllib.error
import urllib.request
from collections import Counter
from urllib.parse import urljoin, urlsplit

from fastapi import HTTPException

from .. import playback_service
from ..config import env_int, env_str, runtime_config
from ..debug import get_logger
from .iptv_store import IptvStore


logger = get_logger("iptv")

_STORE_LOCK = threading.Lock()
_STORE: IptvStore | None = None
_STORE_PATH = ""
_REFRESH_LOCKS: dict[str, threading.Lock] = {}
_WORKER_STOP = threading.Event()
_WORKER_THREAD: threading.Thread | None = None

_ATTR_RE = re.compile(r"([A-Za-z0-9_-]+)=(\"[^\"]*\"|'[^']*'|[^\s,]*)")
_HLS_SOURCE_TAGS = ("#EXT-X-TARGETDURATION", "#EXT-X-STREAM-INF", "#EXT-X-MEDIA-SEQUENCE")


DIRECTORY: tuple[dict[str, object], ...] = (
    {
        "id": "iptv-org-us",
        "name": "iptv-org · United States",
        "provider": "iptv-org",
        "description": "Publicly available channels broadcasting in the United States.",
        "country": "United States",
        "language": "",
        "category": "Country",
        "homepage": "https://github.com/iptv-org/iptv",
        "location": "https://iptv-org.github.io/iptv/countries/us.m3u",
        "provenance": "Community-maintained links to publicly available streams; regional restrictions may apply.",
    },
    {
        "id": "iptv-org-ca",
        "name": "iptv-org · Canada",
        "provider": "iptv-org",
        "description": "Publicly available channels broadcasting in Canada.",
        "country": "Canada",
        "language": "",
        "category": "Country",
        "homepage": "https://github.com/iptv-org/iptv",
        "location": "https://iptv-org.github.io/iptv/countries/ca.m3u",
        "provenance": "Community-maintained links to publicly available streams; regional restrictions may apply.",
    },
    {
        "id": "iptv-org-uk",
        "name": "iptv-org · United Kingdom",
        "provider": "iptv-org",
        "description": "Publicly available channels broadcasting in the United Kingdom.",
        "country": "United Kingdom",
        "language": "",
        "category": "Country",
        "homepage": "https://github.com/iptv-org/iptv",
        "location": "https://iptv-org.github.io/iptv/countries/uk.m3u",
        "provenance": "Community-maintained links to publicly available streams; regional restrictions may apply.",
    },
    {
        "id": "iptv-org-news",
        "name": "iptv-org · News",
        "provider": "iptv-org",
        "description": "Publicly available news channels from multiple countries.",
        "country": "Worldwide",
        "language": "Multiple",
        "category": "News",
        "homepage": "https://github.com/iptv-org/iptv",
        "location": "https://iptv-org.github.io/iptv/categories/news.m3u",
        "provenance": "Community-maintained links to publicly available streams; regional restrictions may apply.",
    },
    {
        "id": "iptv-org-sports",
        "name": "iptv-org · Sports",
        "provider": "iptv-org",
        "description": "Publicly available sports channels from multiple countries.",
        "country": "Worldwide",
        "language": "Multiple",
        "category": "Sports",
        "homepage": "https://github.com/iptv-org/iptv",
        "location": "https://iptv-org.github.io/iptv/categories/sports.m3u",
        "provenance": "Community-maintained links to publicly available streams; regional restrictions may apply.",
    },
    {
        "id": "iptv-org-english",
        "name": "iptv-org · English",
        "provider": "iptv-org",
        "description": "Publicly available English-language channels.",
        "country": "Worldwide",
        "language": "English",
        "category": "Language",
        "homepage": "https://github.com/iptv-org/iptv",
        "location": "https://iptv-org.github.io/iptv/languages/eng.m3u",
        "provenance": "Community-maintained links to publicly available streams; regional restrictions may apply.",
    },
    {
        "id": "iptv-org-spanish",
        "name": "iptv-org · Spanish",
        "provider": "iptv-org",
        "description": "Publicly available Spanish-language channels.",
        "country": "Worldwide",
        "language": "Spanish",
        "category": "Language",
        "homepage": "https://github.com/iptv-org/iptv",
        "location": "https://iptv-org.github.io/iptv/languages/spa.m3u",
        "provenance": "Community-maintained links to publicly available streams; regional restrictions may apply.",
    },
    {
        "id": "free-tv",
        "name": "Free-TV · Worldwide",
        "provider": "Free-TV/IPTV",
        "description": "A quality-focused playlist of officially free television channels.",
        "country": "Worldwide",
        "language": "Multiple",
        "category": "General",
        "homepage": "https://github.com/Free-TV/IPTV",
        "location": "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8",
        "provenance": "Project policy accepts only channels officially provided for free; regional restrictions may apply.",
    },
)


def enabled() -> bool:
    return runtime_config.snapshot().flag("RELAYTV_IPTV_ENABLED", False)


def _database_path() -> str:
    configured = env_str("RELAYTV_IPTV_DB_PATH", "")
    return configured or "/data/iptv.sqlite3"


def store() -> IptvStore:
    global _STORE, _STORE_PATH
    path = _database_path()
    with _STORE_LOCK:
        if _STORE is None or _STORE_PATH != path:
            _STORE = IptvStore(path)
            _STORE_PATH = path
        return _STORE


def reset_store_for_tests() -> None:
    global _STORE, _STORE_PATH
    with _STORE_LOCK:
        _STORE = None
        _STORE_PATH = ""


def status() -> dict[str, object]:
    sources = store().list_sources()
    return {
        "enabled": enabled(),
        "source_count": len(sources),
        "channel_count": sum(int(source.get("channel_count") or 0) for source in sources),
        "sources": sources,
    }


def directory(query: str = "") -> list[dict[str, object]]:
    needle = str(query or "").strip().casefold()
    out: list[dict[str, object]] = []
    for preset in DIRECTORY:
        public = {key: value for key, value in preset.items() if key != "location"}
        if needle:
            haystack = " ".join(str(value or "") for value in public.values()).casefold()
            if needle not in haystack:
                continue
        out.append(public)
    return out


def _preset(preset_id: str) -> dict[str, object] | None:
    key = str(preset_id or "").strip()
    return next((dict(item) for item in DIRECTORY if item["id"] == key), None)


def _validate_http_url(value: str, *, field: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = urlsplit(text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid {field}") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail=f"{field} must be an HTTP or HTTPS URL")
    return text


def create_source(
    *,
    name: str,
    location: str = "",
    content: str = "",
    preset_id: str = "",
    refresh_interval_sec: int = 21600,
) -> dict[str, object]:
    preset = _preset(preset_id) if preset_id else None
    if preset_id and preset is None:
        raise HTTPException(status_code=404, detail="IPTV directory preset not found")
    if preset is not None:
        location = str(preset["location"])
        name = str(name or preset["name"])
    clean_name = str(name or "").strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="source name is required")
    clean_name = clean_name[:120]
    clean_content = str(content or "")
    if clean_content:
        kind = "upload"
        location = ""
        max_bytes = env_int("RELAYTV_IPTV_MAX_PLAYLIST_BYTES", 20 * 1024 * 1024, minimum=1024)
        if len(clean_content.encode("utf-8")) > max_bytes:
            raise HTTPException(status_code=413, detail="IPTV playlist exceeds configured size limit")
    else:
        kind = "url"
        location = _validate_http_url(location, field="playlist URL")
    interval = max(300, min(int(refresh_interval_sec or 21600), 7 * 24 * 3600))
    return store().create_source(
        source_id=str(uuid.uuid4()),
        name=clean_name,
        kind=kind,
        location=location,
        content=clean_content,
        preset_id=str(preset_id or ""),
        refresh_interval_sec=interval,
    )


def add_directory_source(preset_id: str) -> dict[str, object]:
    preset = _preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="IPTV directory preset not found")
    return create_source(name=str(preset["name"]), preset_id=preset_id)


def update_source(source_id: str, patch: dict[str, object]) -> dict[str, object]:
    cleaned: dict[str, object] = {}
    if "name" in patch:
        name = str(patch.get("name") or "").strip()[:120]
        if not name:
            raise HTTPException(status_code=400, detail="source name is required")
        cleaned["name"] = name
    if "enabled" in patch:
        cleaned["enabled"] = 1 if bool(patch.get("enabled")) else 0
    if "location" in patch and str(patch.get("location") or "").strip():
        cleaned["location"] = _validate_http_url(
            str(patch.get("location") or ""), field="playlist URL"
        )
        # Point the source at the URL: clear any pasted content, flip a
        # previously uploaded source to URL mode, and drop stale conditional
        # validators so the new URL is fully fetched instead of 304-skipped.
        cleaned["content"] = ""
        cleaned["kind"] = "url"
        cleaned["etag"] = ""
        cleaned["last_modified"] = ""
    if "refresh_interval_sec" in patch:
        cleaned["refresh_interval_sec"] = max(
            300, min(int(patch.get("refresh_interval_sec") or 21600), 7 * 24 * 3600)
        )
    updated = store().update_source(source_id, cleaned)
    if updated is None:
        raise HTTPException(status_code=404, detail="IPTV source not found")
    return updated


def delete_source(source_id: str) -> None:
    if not store().delete_source(source_id):
        raise HTTPException(status_code=404, detail="IPTV source not found")


def _split_extinf(text: str) -> tuple[str, str]:
    quoted = False
    quote = ""
    for index, char in enumerate(text):
        if char in {'"', "'"}:
            if quoted and char == quote:
                quoted = False
                quote = ""
            elif not quoted:
                quoted = True
                quote = char
        elif char == "," and not quoted:
            return text[:index], text[index + 1 :].strip()
    return text, ""


def parse_m3u(text: str, *, base_url: str = "") -> list[dict[str, object]]:
    body = str(text or "").lstrip("\ufeff")
    if not body.strip().startswith("#EXTM3U"):
        raise ValueError("playlist is not extended M3U")
    lines = body.splitlines()
    has_catalog_entries = any(line.strip().startswith("#EXTINF") for line in lines)
    if not has_catalog_entries and any(tag in body for tag in _HLS_SOURCE_TAGS):
        raise ValueError("HLS media/master manifest cannot be used as an IPTV source")

    max_entries = env_int("RELAYTV_IPTV_MAX_CHANNELS", 100000, minimum=1, maximum=500000)
    entries: list[dict[str, object]] = []
    pending: dict[str, object] | None = None
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            info = line.split(":", 1)[1] if ":" in line else ""
            attrs_text, title = _split_extinf(info)
            attrs: dict[str, str] = {}
            for match in _ATTR_RE.finditer(attrs_text):
                value = match.group(2).strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                    value = value[1:-1]
                attrs[match.group(1).lower()] = value.strip()
            pending = {
                "name": title or attrs.get("tvg-name") or "Untitled channel",
                "tvg_id": attrs.get("tvg-id", ""),
                "tvg_name": attrs.get("tvg-name", ""),
                "logo_url": attrs.get("tvg-logo", ""),
                "group_title": attrs.get("group-title", ""),
                "user_agent": "",
                "referrer": "",
            }
            continue
        if pending is None:
            continue
        if line.startswith("#EXTGRP:"):
            pending["group_title"] = line.split(":", 1)[1].strip()
            continue
        if line.startswith("#EXTVLCOPT:") or line.startswith("#KODIPROP:"):
            option = line.split(":", 1)[1]
            key, _, value = option.partition("=")
            normalized = key.strip().lower()
            if normalized in {"http-user-agent", "user-agent"}:
                pending["user_agent"] = value.strip()
            elif normalized in {"http-referrer", "http-referer", "referrer", "referer"}:
                pending["referrer"] = value.strip()
            continue
        if line.startswith("#"):
            continue
        stream_url = urljoin(base_url, line) if base_url else line
        try:
            parsed = urlsplit(stream_url)
        except Exception:
            pending = None
            continue
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            pending = None
            continue
        pending["stream_url"] = stream_url
        pending["upstream_index"] = len(entries)
        entries.append(pending)
        pending = None
        if len(entries) > max_entries:
            raise ValueError("playlist exceeds configured channel limit")
    if not entries:
        raise ValueError("playlist contains no playable HTTP channels")
    return entries


def _normalize_identity(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _assign_channel_ids(
    source_id: str,
    entries: list[dict[str, object]],
    existing: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    tvg_counts = Counter(_normalize_identity(entry.get("tvg_id")) for entry in entries)
    name_keys = [
        f"{_normalize_identity(entry.get('tvg_name') or entry.get('name'))}|"
        f"{_normalize_identity(entry.get('group_title'))}"
        for entry in entries
    ]
    name_counts = Counter(name_keys)
    # Which name_key currently owns each tvg: identity, so a newly-appeared
    # duplicate tvg-id does not evict the incumbent and detach its user state.
    incumbent_tvg: dict[str, str] = {}
    for row in existing or []:
        ik = str(row.get("identity_key") or "")
        if ik.startswith("tvg:"):
            nk = (
                f"{_normalize_identity(row.get('tvg_name') or row.get('name'))}|"
                f"{_normalize_identity(row.get('group_title'))}"
            )
            incumbent_tvg.setdefault(ik[len("tvg:"):], nk)
    out: list[dict[str, object]] = []
    for entry, name_key in zip(entries, name_keys):
        tvg = _normalize_identity(entry.get("tvg_id"))
        if tvg and tvg_counts[tvg] == 1:
            identity_key = f"tvg:{tvg}"
        elif tvg and tvg_counts[tvg] > 1 and incumbent_tvg.get(tvg) == name_key:
            identity_key = f"tvg:{tvg}"
        elif name_key.strip("|") and name_counts[name_key] == 1:
            identity_key = f"name:{name_key}"
        else:
            identity_key = f"url:{name_key}|{str(entry.get('stream_url') or '').strip()}"
        digest = hashlib.sha256(f"{source_id}|{identity_key}".encode("utf-8")).hexdigest()[:24]
        item = dict(entry)
        item["identity_key"] = identity_key
        item["channel_id"] = digest
        out.append(item)
    # Collapse entries that resolve to the same channel_id (identical name,
    # group, and stream URL). replace_catalog upserts them into one row, so
    # keeping duplicates would inflate channel_count past the browsable total.
    seen: set[str] = set()
    deduped: list[dict[str, object]] = []
    for item in out:
        cid = str(item.get("channel_id") or "")
        if cid in seen:
            continue
        seen.add(cid)
        deduped.append(item)
    return deduped


def _fetch_target_host_is_public(host: str, port: int) -> bool:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    checked = False
    for info in infos:
        checked = True
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return checked


def _assert_fetch_target_allowed(url: str) -> None:
    """SSRF guard: on a token-less (open-LAN) deployment, only allow playlist
    hosts that resolve to public addresses. A configured ``RELAYTV_API_TOKEN``
    gates source management to the operator, who may then target private hosts.
    """
    from ..api_auth import configured_api_token

    if configured_api_token():
        return
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("playlist URL must be http(s)")
    host = parts.hostname
    if not host:
        raise ValueError("playlist URL has no host")
    port = parts.port or (443 if scheme == "https" else 80)
    if not _fetch_target_host_is_public(host, port):
        raise ValueError("playlist host is not a permitted public address")


class _PublicOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate every redirect hop so a public URL cannot bounce to a
    private/loopback/link-local host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_fetch_target_allowed(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_FETCH_OPENER = urllib.request.build_opener(_PublicOnlyRedirectHandler())


def _fetch_source(source: dict[str, object]) -> tuple[str, str, str, bool]:
    if str(source.get("kind") or "") == "upload":
        return str(source.get("content") or ""), "", "", False
    location = str(source.get("location") or "")
    _assert_fetch_target_allowed(location)
    headers = {"User-Agent": "RelayTV/1.0 IPTV catalog"}
    if source.get("etag"):
        headers["If-None-Match"] = str(source["etag"])
    if source.get("last_modified"):
        headers["If-Modified-Since"] = str(source["last_modified"])
    request = urllib.request.Request(location, headers=headers)
    timeout = env_int("RELAYTV_IPTV_FETCH_TIMEOUT_SEC", 15, minimum=2, maximum=120)
    max_bytes = env_int("RELAYTV_IPTV_MAX_PLAYLIST_BYTES", 20 * 1024 * 1024, minimum=1024)
    try:
        with _FETCH_OPENER.open(request, timeout=timeout) as response:
            if int(getattr(response, "status", 200) or 200) == 304:
                return "", str(source.get("etag") or ""), str(source.get("last_modified") or ""), True
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise ValueError("playlist exceeds configured size limit")
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset)
            except (LookupError, UnicodeDecodeError):
                text = payload.decode("utf-8", errors="replace")
            return (
                text,
                str(response.headers.get("ETag") or ""),
                str(response.headers.get("Last-Modified") or ""),
                False,
            )
    except urllib.error.HTTPError as exc:
        if int(exc.code) == 304:
            return "", str(source.get("etag") or ""), str(source.get("last_modified") or ""), True
        raise ValueError(f"playlist fetch failed with HTTP {int(exc.code)}") from exc
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"playlist fetch failed ({type(exc).__name__})") from exc


def refresh_source(source_id: str) -> dict[str, object]:
    source = store().get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="IPTV source not found")
    if not bool(source.get("enabled")):
        raise HTTPException(status_code=409, detail="IPTV source is disabled")
    with _STORE_LOCK:
        source_lock = _REFRESH_LOCKS.setdefault(source_id, threading.Lock())
    if not source_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="IPTV source refresh already running")
    try:
        store().mark_refresh_attempt(source_id)
        text, etag, last_modified, not_modified = _fetch_source(source)
        if not_modified:
            return {"ok": True, "source_id": source_id, "not_modified": True}
        base_url = str(source.get("location") or "")
        entries = parse_m3u(text, base_url=base_url)
        channels = _assign_channel_ids(source_id, entries, store().channel_identities(source_id))
        result = store().replace_catalog(
            source_id, channels, etag=etag, last_modified=last_modified
        )
        return {"ok": True, "source_id": source_id, "not_modified": False, **result}
    except HTTPException:
        raise
    except Exception as exc:
        if isinstance(exc, ValueError):
            message = str(exc or "invalid IPTV playlist")[:500]
        else:
            message = f"playlist refresh failed ({type(exc).__name__})"
        store().mark_refresh_error(source_id, message)
        logger.warning("iptv_refresh_failed source_id=%s error_type=%s", source_id, type(exc).__name__)
        raise HTTPException(status_code=502, detail=message) from exc
    finally:
        source_lock.release()


def list_channels(**kwargs) -> dict[str, object]:
    return store().query_channels(**kwargs)


def update_channel(source_id: str, channel_id: str, patch: dict[str, object]) -> dict[str, object]:
    updated = store().update_channel(source_id, channel_id, patch)
    if updated is None:
        raise HTTPException(status_code=404, detail="IPTV channel not found")
    return updated


def set_group_hidden(source_id: str, group: str, hidden: bool) -> int:
    return store().set_group_hidden(source_id, group, hidden)


def reorder_channel(
    source_id: str,
    channel_id: str,
    *,
    before_channel_id: str = "",
    after_channel_id: str = "",
) -> None:
    if bool(before_channel_id) == bool(after_channel_id):
        raise HTTPException(status_code=400, detail="provide exactly one reorder anchor")
    if not store().reorder_channel(
        source_id,
        channel_id,
        before_channel_id=before_channel_id,
        after_channel_id=after_channel_id,
    ):
        raise HTTPException(status_code=404, detail="IPTV channel or reorder anchor not found")


def _playable_item(source_id: str, channel_id: str) -> dict[str, object]:
    channel = store().get_channel(source_id, channel_id)
    if channel is None or not bool(channel.get("active")):
        raise HTTPException(status_code=404, detail="IPTV channel is unavailable")
    headers: dict[str, str] = {}
    if channel.get("user_agent"):
        headers["User-Agent"] = str(channel["user_agent"])
    if channel.get("referrer"):
        headers["Referer"] = str(channel["referrer"])
    return {
        "provider": "iptv",
        "title": str(channel.get("name") or "IPTV channel"),
        "channel": str(channel.get("group_title") or "IPTV"),
        "thumbnail": str(channel.get("logo_url") or ""),
        "url": str(channel.get("stream_url") or ""),
        "iptv_source_id": source_id,
        "iptv_channel_id": channel_id,
        **({"http_headers": headers} if headers else {}),
    }


def resolve_queue_item(item: dict[str, object]) -> dict[str, object]:
    """Resolve a persisted IPTV queue/history reference to its current stream."""
    source_id = str(item.get("iptv_source_id") or "").strip()
    channel_id = str(item.get("iptv_channel_id") or "").strip()
    if not source_id or not channel_id:
        raise HTTPException(status_code=400, detail="invalid IPTV catalog reference")
    resolved = _playable_item(source_id, channel_id)
    for key in ("history_id", "resume_pos"):
        if item.get(key) is not None:
            resolved[key] = item[key]
    return resolved


def channel_action(source_id: str, channel_id: str, command: str) -> dict[str, object]:
    item = _playable_item(source_id, channel_id)
    action = str(command or "play_now").strip().lower()
    try:
        if action == "play_now":
            now = playback_service.play_now(
                item,
                use_resolver=False,
                cec=False,
                clear_queue=False,
                mode="iptv",
            )
            # Launching mpv is not proof the stream plays; leave the recorded
            # availability to real probes (scheduled + the explicit Check action)
            # so a dead or geo-blocked channel keeps its warning.
            return {"ok": True, "action": action, "now_playing": now}
        if action == "play_next":
            qlen, _snapshot = playback_service.queue_item_next(item)
            return {"ok": True, "action": action, "queue_length": qlen}
        if action == "play_last":
            qlen, _snapshot = playback_service.queue_item(item)
            return {"ok": True, "action": action, "queue_length": qlen}
        raise HTTPException(status_code=400, detail="unsupported IPTV channel action")
    except HTTPException:
        raise
    except Exception:
        # Display/runtime startup errors are not proof that a remote channel
        # failed. Availability failures come from bounded channel checks.
        raise


def check_channel(source_id: str, channel_id: str) -> dict[str, object]:
    channel = store().get_channel(source_id, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="IPTV channel not found")
    url = str(channel.get("stream_url") or "")
    headers = {"User-Agent": str(channel.get("user_agent") or "RelayTV/1.0")}
    if channel.get("referrer"):
        headers["Referer"] = str(channel["referrer"])
    request = urllib.request.Request(url, headers=headers)
    timeout = env_int("RELAYTV_IPTV_PROBE_TIMEOUT_SEC", 8, minimum=2, maximum=30)
    available = False
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            sample = response.read(64 * 1024)
            available = 200 <= status_code < 400 and bool(sample)
    except Exception:
        available = False
    updated = store().mark_channel_check(source_id, channel_id, available=available)
    assert updated is not None
    return updated


def remove_unavailable(*, source_id: str = "") -> int:
    if source_id and store().get_source(source_id) is None:
        raise HTTPException(status_code=404, detail="IPTV source not found")
    return store().remove_unavailable(source_id=source_id)


def _check_due_favorites() -> None:
    interval = env_int("RELAYTV_IPTV_CHECK_INTERVAL_SEC", 21600, minimum=300, maximum=604800)
    batch = env_int("RELAYTV_IPTV_CHECK_BATCH", 3, minimum=1, maximum=20)
    due = store().channels_due_for_check(before=time.time() - interval, limit=batch)
    for channel in due:
        try:
            check_channel(str(channel["source_id"]), str(channel["channel_id"]))
        except Exception:
            pass


def _refresh_due_sources() -> None:
    if not enabled():
        return
    now = time.time()
    for source in store().list_sources():
        if not bool(source.get("enabled")):
            continue
        last = float(source.get("last_attempt_at") or 0.0)
        interval = max(300, int(source.get("refresh_interval_sec") or 21600))
        # Deterministic per-source jitter avoids synchronized fetch bursts;
        # a failed source backs off for twice its normal interval.
        jitter = 0.9 + (int(hashlib.sha256(str(source["id"]).encode()).hexdigest()[:2], 16) / 2550.0)
        interval = int(interval * jitter * (2 if source.get("last_error") else 1))
        if last and (now - last) < interval:
            continue
        try:
            refresh_source(str(source["id"]))
        except Exception:
            pass


def start_worker() -> None:
    global _WORKER_THREAD
    if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
        return
    _WORKER_STOP.clear()

    def _run() -> None:
        while not _WORKER_STOP.wait(60.0):
            _refresh_due_sources()
            if enabled():
                _check_due_favorites()

    _WORKER_THREAD = threading.Thread(target=_run, name="relaytv-iptv-refresh", daemon=True)
    _WORKER_THREAD.start()


def stop_worker() -> None:
    _WORKER_STOP.set()
