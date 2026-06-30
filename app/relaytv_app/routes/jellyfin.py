# SPDX-License-Identifier: GPL-3.0-only
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..integrations import jellyfin_receiver


router = APIRouter()


def _ui_event_push_jellyfin(event: str, **payload) -> None:
    from . import _ui_event_push_jellyfin as push_jellyfin_event

    push_jellyfin_event(event, **payload)


def _require_jellyfin_catalog_ready() -> dict[str, object]:
    st = jellyfin_receiver.status()
    if not bool(st.get("enabled")):
        raise HTTPException(status_code=503, detail="jellyfin integration disabled")
    if not bool(st.get("running")):
        raise HTTPException(status_code=503, detail="jellyfin integration not running")
    if not str(st.get("server_url") or "").strip():
        raise HTTPException(status_code=400, detail="jellyfin server_url not configured")
    return st


@router.get("/integrations/jellyfin/status")
def jellyfin_integration_status():
    """Jellyfin receiver integration status."""
    return jellyfin_receiver.status()


@router.post("/integrations/jellyfin/catalog/cache_clear")
def jellyfin_catalog_cache_clear():
    """Clear Jellyfin catalog cache for operator troubleshooting."""
    st = jellyfin_receiver.status()
    if not bool(st.get("enabled")):
        raise HTTPException(status_code=503, detail="jellyfin integration disabled")
    out = jellyfin_receiver.clear_catalog_cache(reason="manual_api")
    _ui_event_push_jellyfin(
        "catalog_cache_clear",
        refresh_active_tab=True,
        refresh_settings=True,
        refresh_status=True,
        reason="manual_api",
    )
    return {"ok": True, "status": out}


@router.get("/jellyfin/home")
def jellyfin_home(limit: int = 24, refresh: bool = False):
    st = _require_jellyfin_catalog_ready()
    lim = max(1, min(60, int(limit)))
    try:
        payload = jellyfin_receiver.get_home_rows(limit=lim, refresh=bool(refresh))
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin home rows")
    refreshed = jellyfin_receiver.status()
    return {
        "ok": True,
        "rows": payload.get("rows") if isinstance(payload, dict) else [],
        "generated_ts": payload.get("generated_ts") if isinstance(payload, dict) else None,
        "connected": bool(refreshed.get("connected")),
        "last_error": refreshed.get("last_error"),
        "device_name": st.get("device_name"),
    }


@router.get("/jellyfin/search")
def jellyfin_search(q: str = "", limit: int = 30, refresh: bool = False):
    st = _require_jellyfin_catalog_ready()
    query = str(q or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="q is required")
    lim = max(1, min(100, int(limit)))
    try:
        payload = jellyfin_receiver.search_catalog(query, limit=lim, refresh=bool(refresh))
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to search jellyfin catalog")
    refreshed = jellyfin_receiver.status()
    return {
        "ok": True,
        "query": payload.get("query") if isinstance(payload, dict) else query,
        "count": int(payload.get("count") or 0) if isinstance(payload, dict) else 0,
        "items": payload.get("items") if isinstance(payload, dict) else [],
        "connected": bool(refreshed.get("connected")),
        "last_error": refreshed.get("last_error"),
        "device_name": st.get("device_name"),
    }


@router.get("/jellyfin/movies")
def jellyfin_movies(
    sort: str = "added",
    limit: int = 60,
    start: int = 0,
    starts_with: str = "",
    refresh: bool = False,
):
    st = _require_jellyfin_catalog_ready()
    lim = max(1, min(5000, int(limit)))
    start_index = max(0, int(start))
    try:
        payload = jellyfin_receiver.list_movies(
            sort=str(sort or "added").strip().lower(),
            limit=lim,
            start_index=start_index,
            starts_with=str(starts_with or "").strip(),
            refresh=bool(refresh),
        )
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin movies")
    refreshed = jellyfin_receiver.status()
    return {
        "ok": True,
        "sort": payload.get("sort") if isinstance(payload, dict) else str(sort or "added").strip().lower(),
        "items": payload.get("items") if isinstance(payload, dict) else [],
        "count": int(payload.get("count") or 0) if isinstance(payload, dict) else 0,
        "start_index": int(payload.get("start_index") or start_index) if isinstance(payload, dict) else start_index,
        "limit": int(payload.get("limit") or lim) if isinstance(payload, dict) else lim,
        "next_start_index": payload.get("next_start_index") if isinstance(payload, dict) else None,
        "starts_with": str(payload.get("starts_with") or "") if isinstance(payload, dict) else str(starts_with or "").strip().upper(),
        "connected": bool(refreshed.get("connected")),
        "last_error": refreshed.get("last_error"),
        "device_name": st.get("device_name"),
    }


@router.get("/jellyfin/tv/series")
def jellyfin_tv_series(
    sort: str = "title_asc",
    limit: int = 60,
    start: int = 0,
    starts_with: str = "",
    refresh: bool = False,
):
    st = _require_jellyfin_catalog_ready()
    lim = max(1, min(5000, int(limit)))
    start_index = max(0, int(start))
    try:
        payload = jellyfin_receiver.list_series(
            sort=str(sort or "title_asc").strip().lower(),
            limit=lim,
            start_index=start_index,
            starts_with=str(starts_with or "").strip(),
            refresh=bool(refresh),
        )
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin series")
    refreshed = jellyfin_receiver.status()
    return {
        "ok": True,
        "sort": payload.get("sort") if isinstance(payload, dict) else str(sort or "title_asc").strip().lower(),
        "items": payload.get("items") if isinstance(payload, dict) else [],
        "count": int(payload.get("count") or 0) if isinstance(payload, dict) else 0,
        "start_index": int(payload.get("start_index") or start_index) if isinstance(payload, dict) else start_index,
        "limit": int(payload.get("limit") or lim) if isinstance(payload, dict) else lim,
        "next_start_index": payload.get("next_start_index") if isinstance(payload, dict) else None,
        "starts_with": str(payload.get("starts_with") or "") if isinstance(payload, dict) else str(starts_with or "").strip().upper(),
        "connected": bool(refreshed.get("connected")),
        "last_error": refreshed.get("last_error"),
        "device_name": st.get("device_name"),
    }


@router.get("/jellyfin/tv/series/{series_id}/seasons")
def jellyfin_tv_series_seasons(series_id: str, refresh: bool = False):
    _require_jellyfin_catalog_ready()
    sid = str(series_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="series_id is required")
    try:
        payload = jellyfin_receiver.list_series_seasons(sid, refresh=bool(refresh))
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin seasons")
    refreshed = jellyfin_receiver.status()
    return {
        "ok": True,
        "series_id": sid,
        "seasons": payload.get("seasons") if isinstance(payload, dict) else [],
        "count": int(payload.get("count") or 0) if isinstance(payload, dict) else 0,
        "connected": bool(refreshed.get("connected")),
        "last_error": refreshed.get("last_error"),
    }


@router.get("/jellyfin/tv/series/{series_id}/episodes")
def jellyfin_tv_series_episodes(
    series_id: str,
    season_id: str = "",
    season_number: int | None = None,
    refresh: bool = False,
):
    _require_jellyfin_catalog_ready()
    sid = str(series_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="series_id is required")
    try:
        payload = jellyfin_receiver.list_series_episodes(
            sid,
            season_id=str(season_id or "").strip(),
            season_number=season_number,
            refresh=bool(refresh),
        )
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin episodes")
    refreshed = jellyfin_receiver.status()
    return {
        "ok": True,
        "series_id": sid,
        "season_id": payload.get("season_id") if isinstance(payload, dict) else str(season_id or "").strip(),
        "season_number": payload.get("season_number") if isinstance(payload, dict) else season_number,
        "episodes": payload.get("episodes") if isinstance(payload, dict) else [],
        "count": int(payload.get("count") or 0) if isinstance(payload, dict) else 0,
        "connected": bool(refreshed.get("connected")),
        "last_error": refreshed.get("last_error"),
    }


@router.get("/jellyfin/item/{item_id}")
def jellyfin_item_detail(item_id: str, refresh: bool = False):
    _require_jellyfin_catalog_ready()
    iid = str(item_id or "").strip()
    if not iid:
        raise HTTPException(status_code=400, detail="item_id is required")
    try:
        item = jellyfin_receiver.get_item_detail(iid, refresh=bool(refresh))
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin item detail")
    refreshed = jellyfin_receiver.status()
    if not item:
        return JSONResponse(
            {
                "ok": False,
                "reason": "not_found",
                "item_id": iid,
                "connected": bool(refreshed.get("connected")),
                "last_error": refreshed.get("last_error"),
            },
            status_code=404,
        )
    return {
        "ok": True,
        "item": item,
        "connected": bool(refreshed.get("connected")),
        "last_error": refreshed.get("last_error"),
    }


@router.get("/jellyfin/item/{item_id}/adjacent")
def jellyfin_item_adjacent(item_id: str, refresh: bool = False):
    _require_jellyfin_catalog_ready()
    iid = str(item_id or "").strip()
    if not iid:
        raise HTTPException(status_code=400, detail="item_id is required")
    try:
        nav = jellyfin_receiver.get_adjacent_episodes(iid, refresh=bool(refresh))
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin adjacent episodes")
    refreshed = jellyfin_receiver.status()
    prev = nav.get("prev") if isinstance(nav, dict) and isinstance(nav.get("prev"), dict) else None
    nxt = nav.get("next") if isinstance(nav, dict) and isinstance(nav.get("next"), dict) else None
    return {
        "ok": True,
        "item_id": iid,
        "prev": prev,
        "next": nxt,
        "connected": bool(refreshed.get("connected")),
        "last_error": refreshed.get("last_error"),
    }
