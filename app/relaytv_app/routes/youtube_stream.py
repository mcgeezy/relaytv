# SPDX-License-Identifier: GPL-3.0-only
"""Local mpv transport; callers cannot supply an upstream URL."""

import ipaddress

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .. import youtube_stream


router = APIRouter()


@router.api_route("/youtube-stream/{token}", methods=["GET", "HEAD"])
def youtube_stream_proxy(token: str, request: Request):
    try:
        local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    source = youtube_stream.get_source(token) if local else None
    if source is None:
        return JSONResponse({"detail": "no such media stream"}, status_code=404)
    range_header = request.headers.get("range")
    try:
        start, end = youtube_stream.byte_range(range_header, source.size)
    except ValueError:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{source.size}"})
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "no-store",
    }
    status = 206 if range_header is not None else 200
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{source.size}"
    if request.method == "HEAD":
        return Response(status_code=status, headers=headers, media_type=source.media_type)
    try:
        # This is a sync route: slow upstream I/O runs off the ASGI event loop.
        first = youtube_stream.fetch_chunk(source, start, end)
    except youtube_stream.StreamProxyError:
        return JSONResponse({"detail": "upstream media unavailable"}, status_code=502)
    return StreamingResponse(
        youtube_stream.iter_stream(source, start, end, first),
        status_code=status,
        headers=headers,
        media_type=source.media_type,
    )
