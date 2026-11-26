# SPDX-License-Identifier: GPL-3.0-only
"""Safe serialization helpers for media objects exposed by public APIs."""

from __future__ import annotations

import re
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
    "plex_token",
    "resolved_audio",
    "resolved_source_url",
    "resolved_stream",
    "stream",
    "token",
    "x-plex-token",
    "x_plex_token",
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
    "pass",
    "passwd",
    "password",
    "policy",
    "pwd",
    "sig",
    "signature",
    "token",
    "user",
    "username",
    "usr",
    "x-emby-token",
    "x-jellyfin-token",
    "x-plex-token",
}

_URL_FIELDS = {"art", "image", "input", "poster", "thumbnail", "thumbnail_local", "url"}
_PLEX_PLACEHOLDER_URL = "https://plex.invalid/item"
_NETWORK_MEDIA_SCHEMES = frozenset({"http", "https", "rtsp", "rtsps"})
_EMBEDDED_MEDIA_URL_RE = re.compile(r"(?:https?|rtsps?)://[^\s<>'\"`]+", re.IGNORECASE)
_PUBLIC_URL_PREFIXES = (
    "http://",
    "https://",
    "rtsp://",
    "rtsps://",
    "/plex/stream",
    "/browse/stream",
    "/postlive",
)


def _is_sensitive_query_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    return normalized in _SENSITIVE_QUERY_KEYS or normalized.startswith("x-amz-")


def _redacted_capability_stream_url(path: str) -> str | None:
    if not path or not path.startswith("/"):
        return None
    collapsed = "/" + path.lstrip("/")
    lowered = collapsed.lower()
    if lowered == "/plex/stream" or lowered.startswith("/plex/stream/"):
        return _PLEX_PLACEHOLDER_URL
    if lowered == "/browse/stream" or lowered.startswith("/browse/stream/"):
        return _PLEX_PLACEHOLDER_URL
    if lowered == "/postlive" or lowered.startswith("/postlive/"):
        return ""
    return None


def sanitize_public_url(value: object, *, _allow_embedded: bool = True) -> str:
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
    redacted_stream = _redacted_capability_stream_url(parsed.path)
    if redacted_stream is not None:
        return redacted_stream
    if parsed.scheme == "data":
        return raw
    # A network media URL with an empty authority (for example `rtsp:///stream1`
    # or `http:///path`) is malformed; omit it rather than echoing or recursing.
    if parsed.scheme in _NETWORK_MEDIA_SCHEMES and not parsed.raw_netloc:
        return ""
    # Preserved from the urlsplit version: a value with no scheme or no
    # authority is a relative reference or a non-network URL (file:, data:),
    # and is passed through untouched — unless it carries an embedded media URL
    # (for example prose-prefixed or <...>-wrapped share text).
    if not parsed.scheme or not parsed.raw_netloc:
        if _allow_embedded and _EMBEDDED_MEDIA_URL_RE.search(raw):
            return sanitize_embedded_urls(raw)
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
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), ""))


def sanitize_embedded_urls(text: object) -> str:
    """Sanitize any embedded http(s)/rtsp(s) URLs inside arbitrary text."""
    raw = str(text or "")
    if not raw:
        return ""
    stripped = raw.strip()
    if stripped.lower().startswith("data:"):
        return raw
    if stripped.lower().startswith(_PUBLIC_URL_PREFIXES) and " " not in stripped and "\t" not in stripped:
        if not any(ch in stripped for ch in "<>'\"`"):
            return sanitize_public_url(stripped, _allow_embedded=False)

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        suffix = ""
        while token and token[-1] in ",.;!?":
            suffix = token[-1] + suffix
            token = token[:-1]
        for close, open_ in ((")", "("), ("]", "["), ("}", "{")):
            while token.endswith(close) and token.count(close) > token.count(open_):
                suffix = close + suffix
                token = token[:-1]
        sanitized = sanitize_public_url(token, _allow_embedded=False)
        return f"{sanitized}{suffix}" if sanitized else suffix

    return _EMBEDDED_MEDIA_URL_RE.sub(_replace, raw)


def url_has_credentials(value: object) -> bool:
    """Return True when ``value`` embeds userinfo or sensitive auth query keys."""
    parsed = url_boundary.parse_url(value)
    if parsed is None or not parsed.scheme or not parsed.raw_netloc:
        return False
    if parsed.username or parsed.password:
        return True
    return any(
        _is_sensitive_query_key(key)
        for key, _val in parse_qsl(parsed.query, keep_blank_values=True)
    )


def rtsp_item_has_credentials(item: object) -> bool:
    """Return True when an RTSP item carries inline credentials."""
    if not isinstance(item, dict):
        return False
    raw_url = str(item.get("url") or item.get("input") or "").strip()
    provider = str(item.get("provider") or "").strip().lower()
    parsed = url_boundary.parse_url(raw_url)
    is_rtsp = provider == "rtsp" or (parsed is not None and parsed.scheme in ("rtsp", "rtsps"))
    if not is_rtsp:
        return False
    return url_has_credentials(raw_url)


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
        if isinstance(value, str) and value.strip().lower().startswith(_PUBLIC_URL_PREFIXES):
            # A failed metadata lookup can leave the signed stream URL in a
            # display field such as title. Public redaction is about the value,
            # not only the expected schema key.
            safe_value = sanitize_public_url(value)
            if safe_value:
                result[key] = safe_value
            continue
        if (
            isinstance(value, str)
            and not value.strip().lower().startswith("data:")
            and _EMBEDDED_MEDIA_URL_RE.search(value)
        ):
            safe_value = sanitize_embedded_urls(value).strip()
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
