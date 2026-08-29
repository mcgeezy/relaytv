# SPDX-License-Identifier: GPL-3.0-only
"""Safe serialization helpers for media objects exposed by public APIs."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlunsplit

from . import url_boundary


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
    "auth",
    "authorization",
    "auth_token",
    "cookie",
    "exp",
    "expires",
    "hdnea",
    "hdnts",
    "jwt",
    "key-pair-id",
    "policy",
    "sig",
    "signature",
    "token",
    "x-emby-token",
    "x-jellyfin-token",
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
    # Shares its parser with input validation, so a value this cannot represent
    # can no longer be accepted in the first place. Values already on disk from
    # before that guard still reach here, and must not raise: a single poisoned
    # item used to break /queue, /status, /history, and realtime for everyone,
    # on every request, until someone hand-edited the JSON.
    parsed = url_boundary.parse_url(raw)
    if parsed is None:
        return ""
    # Preserved from the urlsplit version: a value with no scheme or no
    # authority is a relative reference or a non-network URL (file:, data:),
    # and is passed through untouched.
    if not parsed.scheme or not parsed.raw_netloc:
        return raw
    # An authority that parsed but yielded no hostname is malformed. Omit it
    # rather than echoing it back: the raw form can still carry credentials,
    # which is the one thing this function exists to remove.
    if not parsed.hostname:
        return ""

    query = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_sensitive_query_key(key)
    ]
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), "")
    )


def public_media_item(item: object) -> object:
    """Return a display-safe copy of a queue/history/now-playing item."""
    if not isinstance(item, dict):
        return item

    source_url = item.get("_resolved_source_url") or item.get("resolved_source_url")
    provider = str(item.get("provider") or "").strip().lower()
    result: dict[str, object] = {}
    for key, value in item.items():
        normalized = str(key or "").strip().lower()
        if key.startswith("_") or normalized in _PRIVATE_ITEM_KEYS:
            continue
        if provider == "iptv" and normalized in {"input", "url"}:
            # IPTV stream and playlist URLs may carry credentials anywhere in
            # the path. Opaque catalog IDs are sufficient for public clients.
            continue
        if normalized in _URL_FIELDS and isinstance(value, str):
            safe_url = sanitize_public_url(value)
            if safe_url:
                result[key] = safe_url
            continue
        if isinstance(value, str) and value.strip().lower().startswith(("http://", "https://")):
            # A failed metadata lookup can leave the signed stream URL in a
            # display field such as title. Public redaction is about the value,
            # not only the expected schema key.
            safe_value = sanitize_public_url(value)
            if safe_value:
                result[key] = safe_value
            continue
        result[key] = value

    if source_url and provider != "iptv":
        safe_source = sanitize_public_url(source_url)
        if safe_source:
            result["url"] = safe_source
    return result


def public_media_items(items: list[object] | None) -> list[object]:
    """Return display-safe copies of media items."""
    return [public_media_item(item) for item in list(items or [])]
