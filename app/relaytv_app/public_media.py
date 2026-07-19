# SPDX-License-Identifier: GPL-3.0-only
"""Safe serialization helpers for media objects exposed by public APIs."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_PRIVATE_ITEM_KEYS = {
    "access_token",
    "api_key",
    "audio",
    "cookie",
    "cookies",
    "headers",
    "http_headers",
    "resolved_audio",
    "resolved_source_url",
    "resolved_stream",
    "stream",
    "token",
}

_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "apikey",
    "api_key",
    "authorization",
    "auth_token",
    "cookie",
    "expires",
    "hdnea",
    "hdnts",
    "key-pair-id",
    "policy",
    "sig",
    "signature",
    "token",
}

_URL_FIELDS = {"art", "image", "input", "poster", "thumbnail", "thumbnail_local", "url"}


def _is_sensitive_query_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    return normalized in _SENSITIVE_QUERY_KEYS or normalized.startswith("x-amz-")


def sanitize_public_url(value: object) -> str:
    """Remove credentials and transient signing parameters from a public URL."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except Exception:
        return ""
    if not parsed.scheme or not parsed.netloc:
        return raw

    hostname = parsed.hostname or ""
    if not hostname:
        return ""
    netloc = hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"

    query = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_sensitive_query_key(key)
    ]
    return urlunsplit((parsed.scheme, netloc, parsed.path, urlencode(query, doseq=True), ""))


def public_media_item(item: object) -> object:
    """Return a display-safe copy of a queue/history/now-playing item."""
    if not isinstance(item, dict):
        return item

    source_url = item.get("_resolved_source_url") or item.get("resolved_source_url")
    result: dict[str, object] = {}
    for key, value in item.items():
        normalized = str(key or "").strip().lower()
        if key.startswith("_") or normalized in _PRIVATE_ITEM_KEYS:
            continue
        if normalized in _URL_FIELDS and isinstance(value, str):
            safe_url = sanitize_public_url(value)
            if safe_url:
                result[key] = safe_url
            continue
        result[key] = value

    if source_url:
        safe_source = sanitize_public_url(source_url)
        if safe_source:
            result["url"] = safe_source
    return result


def public_media_items(items: list[object] | None) -> list[object]:
    """Return display-safe copies of media items."""
    return [public_media_item(item) for item in list(items or [])]
