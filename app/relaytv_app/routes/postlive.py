# SPDX-License-Identifier: GPL-3.0-only
"""Serve post-live relay sessions to mpv as progressive matroska.

Loopback-internal: the player hands mpv a ``/postlive/{token}.mkv`` URL
after :mod:`relaytv_app.postlive_relay` spawns the yt-dlp → ffmpeg
pipeline behind it. Tokens are single-use capabilities minted per
session; the route is a read (GET), so the optional ``RELAYTV_API_TOKEN``
write guard never applies — mpv sends no credentials, by design.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import postlive_relay

router = APIRouter()

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}


def _require_loopback_client(request: Request) -> None:
    host = str(getattr(request.client, "host", "") or "").strip().lower()
    if host in _LOOPBACK_HOSTS:
        return
    if host == "testclient" and getattr(request.app.state, "testing", False):
        return
    raise HTTPException(status_code=403, detail="Post-live relay stream is local-only")


@router.get("/postlive/{token}.mkv")
def postlive_stream(token: str, request: Request):
    _require_loopback_client(request)
    stream = postlive_relay.iter_stream(token)
    if stream is None:
        # Unknown token, closed session, or a reconnect: the relay is
        # progressive-only, so a stream can only ever be served once.
        return JSONResponse({"detail": "no such relay session"}, status_code=404)
    return StreamingResponse(stream, media_type="video/x-matroska")
