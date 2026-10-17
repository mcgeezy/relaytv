# SPDX-License-Identifier: GPL-3.0-only
"""Public route surface for unified multi-server Browse."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..integrations import browse_service
from ..integrations.plex_client import PlexError

router = APIRouter()


class BrowseServerUpsertReq(BaseModel):
    id: str | None = None
    name: str | None = None
    type: str = "jellyfin"
    server_url: str
    enabled: bool = True
    username: str | None = None
    password: str | None = None
    clear_password: bool = False
    api_key: str | None = None
    clear_api_key: bool = False
    token: str | None = None
    clear_token: bool = False
    user_id: str | None = None
    machine_id: str | None = None


class BrowseProbeReq(BaseModel):
    server_id: str | None = None


class BrowseItemActionReq(BaseModel):
    item_id: str
    command: str = "play_now"
    resume_pos: float | None = None
    version_id: str = ""
    audio_id: str = ""
    subtitle_id: str = ""


@router.get("/browse/status")
def browse_status():
    return browse_service.status_summary()


@router.get("/browse/servers")
def browse_servers_list():
    return browse_service.list_servers_public()


@router.post("/browse/servers")
def browse_servers_upsert(req: BrowseServerUpsertReq):
    try:
        server = browse_service.upsert_custom_server(req.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {
        "ok": True,
        "server": server,
        "servers": browse_service.list_servers_public()["servers"],
    }


@router.delete("/browse/servers/{server_id}")
def browse_servers_delete(server_id: str):
    removed = browse_service.delete_custom_server(server_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Media server not found or not removable")
    return {
        "ok": True,
        "deleted": server_id,
        "servers": browse_service.list_servers_public()["servers"],
    }


@router.post("/browse/servers/probe")
def browse_servers_probe(req: BrowseProbeReq | None = None):
    sid = str((req.server_id if req else "") or "").strip()
    return browse_service.probe_all_servers(server_id=sid)


@router.get("/browse/home")
def browse_home(limit: int = 24, refresh: bool = False):
    lim = max(1, min(60, int(limit)))
    return browse_service.get_unified_home(limit=lim, refresh=bool(refresh))


@router.get("/browse/movies")
def browse_movies(
    sort: str = "added",
    limit: int = 60,
    start: int = 0,
    starts_with: str = "",
    refresh: bool = False,
):
    return browse_service.list_unified_movies(
        sort=sort,
        limit=limit,
        start=start,
        starts_with=starts_with,
        refresh=bool(refresh),
    )


@router.get("/browse/tv/series")
def browse_tv_series(
    sort: str = "title_asc",
    limit: int = 60,
    start: int = 0,
    starts_with: str = "",
    refresh: bool = False,
):
    return browse_service.list_unified_series(
        sort=sort,
        limit=limit,
        start=start,
        starts_with=starts_with,
        refresh=bool(refresh),
    )


@router.get("/browse/tv/series/{series_id}/seasons")
def browse_tv_series_seasons(series_id: str, refresh: bool = False):
    sid = str(series_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="series_id is required")
    return browse_service.list_unified_series_seasons(sid, refresh=bool(refresh))


@router.get("/browse/tv/series/{series_id}/episodes")
def browse_tv_series_episodes(
    series_id: str,
    season_id: str = "",
    season_number: int | None = None,
    refresh: bool = False,
):
    sid = str(series_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="series_id is required")
    return browse_service.list_unified_series_episodes(
        sid,
        season_id=season_id,
        season_number=season_number,
        refresh=bool(refresh),
    )


@router.post("/browse/tv/series/{series_id}/play_all")
def browse_tv_series_play_all(series_id: str, refresh: bool = False):
    sid = str(series_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="series_id is required")
    try:
        return browse_service.play_all_unified_series(sid, refresh=bool(refresh))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.get("/browse/search")
def browse_search(q: str = "", limit: int = 30, refresh: bool = False):
    query = str(q or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="q is required")
    return browse_service.search_unified_catalog(query, limit=limit, refresh=bool(refresh))


@router.get("/browse/item/{item_id}")
def browse_item_detail(item_id: str, refresh: bool = False):
    iid = str(item_id or "").strip()
    if not iid:
        raise HTTPException(status_code=400, detail="item_id is required")
    try:
        item = browse_service.get_unified_item_detail(iid, refresh=bool(refresh))
    except PlexError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    st = browse_service.status_summary()
    if not item:
        return JSONResponse(
            {
                "ok": False,
                "reason": "not_found",
                "item_id": iid,
                "connected": st["connected"],
            },
            status_code=404,
        )
    return {
        "ok": True,
        "item": item,
        "connected": st["connected"],
    }


@router.get("/browse/item/{item_id}/adjacent")
def browse_item_adjacent(item_id: str, refresh: bool = False):
    iid = str(item_id or "").strip()
    if not iid:
        raise HTTPException(status_code=400, detail="item_id is required")
    nav = browse_service.get_unified_item_adjacent(iid, refresh=bool(refresh))
    st = browse_service.status_summary()
    return {
        "ok": True,
        "item_id": iid,
        "prev": nav.get("prev"),
        "next": nav.get("next"),
        "connected": st["connected"],
    }


@router.post("/browse/action")
def browse_item_action(req: BrowseItemActionReq):
    iid = str(req.item_id or "").strip()
    if not iid:
        raise HTTPException(status_code=400, detail="item_id is required")
    try:
        return browse_service.perform_unified_action(
            iid,
            req.command,
            resume_pos=req.resume_pos,
            version_id=req.version_id,
            audio_id=req.audio_id,
            subtitle_id=req.subtitle_id,
        )
    except HTTPException:
        raise
    except PlexError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None


@router.get("/browse/artwork/{server_id}")
def browse_artwork(server_id: str, path: str = ""):
    try:
        artwork = browse_service.fetch_custom_plex_artwork(server_id, path)
    except PlexError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    return Response(
        content=artwork.body,
        media_type=artwork.content_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/browse/stream/{server_id}")
def browse_stream(server_id: str, request: Request, path: str = ""):
    try:
        stream = browse_service.open_custom_plex_stream(
            server_id,
            path,
            range_header=str(request.headers.get("range") or ""),
        )
    except PlexError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    headers = dict(stream.headers)
    headers["Cache-Control"] = "private, no-store"
    headers["X-Content-Type-Options"] = "nosniff"
    return StreamingResponse(
        stream.iter_bytes(),
        status_code=stream.status_code,
        headers=headers,
    )
