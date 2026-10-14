# SPDX-License-Identifier: GPL-3.0-only
"""Seekable, bounded-range transport for resolved YouTube media.

Some googlevideo servers throttle open-ended requests below the media bitrate.
The FFmpeg shipped in our image cannot split those requests itself. Keep the
original signed URL in playback state; only hand mpv a process-local capability.
No download workers or files survive a request.
"""

from collections import OrderedDict
from dataclasses import dataclass, field
import http.client
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

from . import config


CHUNK_BYTES = 1024 * 1024
MAX_SOURCES = 128
SOURCE_TTL = 6 * 60 * 60


@dataclass(frozen=True)
class StreamSource:
    url: str = field(repr=False)
    size: int
    media_type: str
    expires_at: float


_LOCK = threading.Lock()
_SOURCES: OrderedDict[str, StreamSource] = OrderedDict()


def proxy_url(url: str | None) -> str | None:
    """Wrap finite YouTube media only; leave all other transports untouched."""
    if not url:
        return url
    try:
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        (media_type,) = query.get("mime", [])
        (length,) = query.get("clen", [])
        if (
            parsed.scheme != "https"
            or not (parsed.hostname or "").endswith(".googlevideo.com")
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != "/videoplayback"
            or media_type not in ("audio/webm", "audio/mp4", "video/webm", "video/mp4")
            or not length.isdecimal()
            or not 0 < int(length) <= 2**40
        ):
            return url
    except (ValueError, TypeError):
        return url
    now = time.monotonic()
    with _LOCK:
        for token, source in list(_SOURCES.items()):
            if source.expires_at <= now:
                del _SOURCES[token]
        for token, source in list(_SOURCES.items()):
            if source.url == url:
                _SOURCES.move_to_end(token)
                break
        else:
            token = secrets.token_urlsafe(24)
            _SOURCES[token] = StreamSource(url, int(length), media_type, now + SOURCE_TTL)
            while len(_SOURCES) > MAX_SOURCES:
                _SOURCES.popitem(last=False)
    return f"http://127.0.0.1:{config.server_port()}/youtube-stream/{token}"


def get_source(token: str) -> StreamSource | None:
    with _LOCK:
        source = _SOURCES.get(token)
        if source is not None and source.expires_at <= time.monotonic():
            del _SOURCES[token]
            return None
        return source


def byte_range(header: str | None, size: int) -> tuple[int, int]:
    """Resolve one HTTP byte range, including suffix and open-ended seeks."""
    if header is None:
        return 0, size - 1
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or not any(match.groups()):
        raise ValueError("invalid byte range")
    first, last = match.groups()
    if not first:
        suffix = int(last)
        if suffix <= 0:
            raise ValueError("invalid suffix range")
        return max(0, size - suffix), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        raise ValueError("unsatisfiable byte range")
    return start, end


class StreamProxyError(Exception):
    """Public-safe failure: never include the signed source URL."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A minted capability must not become a proxy to another destination.
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


def fetch_chunk(source: StreamSource, start: int, end: int) -> bytes:
    """Fetch and validate one bounded response before exposing its bytes."""
    end = min(end, start + CHUNK_BYTES - 1)
    request = urllib.request.Request(
        source.url,
        headers={
            "Range": f"bytes={start}-{end}",
            "Accept-Encoding": "identity",
        },
    )
    try:
        with _OPENER.open(request, timeout=10) as response:
            if (
                response.status != 206
                or response.headers.get("Content-Range") != f"bytes {start}-{end}/{source.size}"
                or response.headers.get("Content-Encoding", "identity") != "identity"
            ):
                raise StreamProxyError("invalid upstream media range")
            data = response.read(end - start + 2)
            if len(data) != end - start + 1:
                raise StreamProxyError("incomplete upstream media range")
            return data
    except urllib.error.HTTPError as exc:
        exc.close()
        raise StreamProxyError("upstream media download failed") from None
    except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
        raise StreamProxyError("upstream media download failed") from None


def iter_stream(source: StreamSource, start: int, end: int, first: bytes):
    yield first
    start += len(first)
    while start <= end:
        data = fetch_chunk(source, start, end)
        yield data
        start += len(data)
