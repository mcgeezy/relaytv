# SPDX-License-Identifier: GPL-3.0-only
import os
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import player, state
from ..integrations import jellyfin_receiver


router = APIRouter()


class JellyfinAudioSelectReq(BaseModel):
    index: int


class JellyfinSubtitleSelectReq(BaseModel):
    index: int


class JellyfinItemActionReq(BaseModel):
    item_id: str
    command: str = "play_now"  # play_now|play_next|play_last|resume
    resume_pos: float | None = None


class JellyfinConnectReq(BaseModel):
    server_url: str
    api_key: str | None = None
    device_name: str | None = None
    heartbeat_sec: int | None = None
    register_now: bool = False


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


def _require_jellyfin_catalog_ready_for_playback() -> dict[str, object]:
    from . import _require_jellyfin_catalog_ready as require

    return require()


def _first_nonempty_str(values: list[str]) -> str:
    from . import _first_nonempty_str as helper

    return helper(values)


def _extract_jellyfin_item_id_from_url_raw(raw_url: str | None) -> str:
    from . import _extract_jellyfin_item_id_from_url_raw as helper

    return helper(raw_url)


def _extract_jellyfin_media_source_id_from_url(url: str) -> str:
    from . import _extract_jellyfin_media_source_id_from_url as helper

    return helper(url)


def _extract_jellyfin_audio_stream_index_from_url(url: str) -> str:
    from . import _extract_jellyfin_audio_stream_index_from_url as helper

    return helper(url)


def _extract_jellyfin_subtitle_stream_index_from_url(url: str) -> str:
    from . import _extract_jellyfin_subtitle_stream_index_from_url as helper

    return helper(url)


def _jellyfin_runtime_selected_audio_stream(audio_streams: list[dict[str, object]]) -> tuple[int | None, str]:
    from . import _jellyfin_runtime_selected_audio_stream as helper

    return helper(audio_streams)


def _jellyfin_runtime_selected_subtitle_stream(subtitle_streams: list[dict[str, object]]) -> tuple[int | None, str, bool]:
    from . import _jellyfin_runtime_selected_subtitle_stream as helper

    return helper(subtitle_streams)


def _normalize_lang_pref(raw: str) -> str:
    from . import _normalize_lang_pref as helper

    return helper(raw)


def _retarget_jellyfin_queue_stream_preferences() -> int:
    from . import _retarget_jellyfin_queue_stream_preferences as helper

    return helper()


def _jellyfin_try_set_mpv_audio_track(
    *,
    language: str = "",
    display: str = "",
    preferred_stream_index: int | None = None,
) -> bool:
    from . import _jellyfin_try_set_mpv_audio_track as helper

    if preferred_stream_index is None:
        return helper(language=language, display=display)
    return helper(language=language, display=display, preferred_stream_index=preferred_stream_index)


def _jellyfin_try_set_mpv_subtitle_track(
    *,
    language: str = "",
    display: str = "",
    preferred_stream_index: int | None = None,
    off: bool = False,
) -> bool:
    from . import _jellyfin_try_set_mpv_subtitle_track as helper

    return helper(language=language, display=display, preferred_stream_index=preferred_stream_index, off=off)


def _jellyfin_emit_progress_hint() -> None:
    from . import _jellyfin_emit_progress_hint as helper

    helper()


def _jellyfin_access_token() -> str:
    from . import _jellyfin_access_token as helper

    return helper()


def _build_jellyfin_item_stream_url(
    item_id: str,
    *,
    server_url: str,
    api_key: str,
    media_source_id: str = "",
    audio_stream_index: str = "",
    subtitle_stream_index: str = "",
) -> str:
    from . import _build_jellyfin_item_stream_url as helper

    return helper(
        item_id,
        server_url=server_url,
        api_key=api_key,
        media_source_id=media_source_id,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
    )


def _select_jellyfin_playback_url(
    *,
    item_id: str,
    source_url: str,
    server_url: str,
    api_key: str,
    media_source_id: str = "",
    audio_stream_index: str = "",
    subtitle_stream_index: str = "",
    settings: dict[str, object] | None = None,
) -> dict[str, object]:
    from . import _select_jellyfin_playback_url as helper

    return helper(
        item_id=item_id,
        source_url=source_url,
        server_url=server_url,
        api_key=api_key,
        media_source_id=media_source_id,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
        settings=settings,
    )


def _normalize_jellyfin_source_url(raw_url: str, *, server_url: str, api_key: str) -> str:
    from . import _normalize_jellyfin_source_url as helper

    return helper(raw_url, server_url=server_url, api_key=api_key)


def _apply_jellyfin_stream_params(url: str, *, audio_stream_index: str = "", subtitle_stream_index: str = "") -> str:
    from . import _apply_jellyfin_stream_params as helper

    return helper(url, audio_stream_index=audio_stream_index, subtitle_stream_index=subtitle_stream_index)


def _jellyfin_enrich_now_stream_metadata(
    now: dict[str, object],
    *,
    detail: dict[str, object],
    audio_stream_index: str = "",
    subtitle_stream_index: str = "",
) -> dict[str, object]:
    from . import _jellyfin_enrich_now_stream_metadata as helper

    return helper(
        now,
        detail=detail,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
    )


def _jellyfin_command_req(**kwargs):
    from . import JellyfinCommandReq

    return JellyfinCommandReq(**kwargs)


def _jellyfin_integration_command(req) -> dict[str, object]:
    from . import jellyfin_integration_command as command

    return command(req)


def _jellyfin_should_suppress_duplicate_ui_action(command: str, item_id: str, resume_pos: float | None) -> bool:
    from . import _jellyfin_should_suppress_duplicate_ui_action as helper

    return helper(command, item_id, resume_pos)


def _reset_jellyfin_command_state() -> None:
    from . import _reset_jellyfin_command_state as reset

    reset()


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


@router.post("/integrations/jellyfin/connect")
def jellyfin_integration_connect(req: JellyfinConnectReq):
    server_url = (req.server_url or "").strip()
    if not server_url:
        raise HTTPException(status_code=400, detail="server_url is required")
    settings_name = ""
    try:
        settings_name = str((state.get_settings() if hasattr(state, "get_settings") else {}).get("device_name") or "").strip()
    except Exception:
        settings_name = ""
    out = jellyfin_receiver.connect(
        server_url=server_url,
        api_key=req.api_key,
        device_name=(req.device_name or settings_name or None),
        heartbeat_sec=req.heartbeat_sec,
    )
    if bool(req.register_now):
        out = dict(out)
        out["register"] = jellyfin_receiver.register_receiver_once()
    _reset_jellyfin_command_state()
    _ui_event_push_jellyfin("connect", refresh_active_tab=True, refresh_settings=True, refresh_status=True)
    return out


@router.post("/integrations/jellyfin/disconnect")
def jellyfin_integration_disconnect():
    _reset_jellyfin_command_state()
    out = jellyfin_receiver.disconnect()
    _ui_event_push_jellyfin("disconnect", refresh_active_tab=True, refresh_settings=True, refresh_status=True)
    return out


@router.post("/integrations/jellyfin/register")
def jellyfin_integration_register():
    """Force a single Jellyfin receiver registration handshake."""
    st = jellyfin_receiver.status()
    if not bool(st.get("enabled")):
        raise HTTPException(status_code=503, detail="jellyfin integration disabled")
    out = jellyfin_receiver.register_receiver_once()
    _ui_event_push_jellyfin("register", refresh_active_tab=True, refresh_settings=True, refresh_status=True)
    if not bool(out.get("ok")):
        return JSONResponse(out, status_code=202)
    return out


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


@router.post("/jellyfin/tv/series/{series_id}/play_all")
def jellyfin_tv_series_play_all(series_id: str, refresh: bool = False):
    _require_jellyfin_catalog_ready_for_playback()
    sid = str(series_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="series_id is required")
    try:
        payload = jellyfin_receiver.list_series_episodes(sid, refresh=bool(refresh))
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin series episodes")
    episodes = payload.get("episodes") if isinstance(payload, dict) and isinstance(payload.get("episodes"), list) else []
    item_ids = [str(ep.get("item_id") or "").strip() for ep in episodes if isinstance(ep, dict) and str(ep.get("item_id") or "").strip()]
    if not item_ids:
        raise HTTPException(status_code=404, detail="no episodes available for series")
    first = next((ep for ep in episodes if isinstance(ep, dict) and str(ep.get("item_id") or "").strip() == item_ids[0]), {})
    out = _jellyfin_integration_command(
        _jellyfin_command_req(
            action="Play",
            payload={"ItemIds": item_ids, "PlayCommand": "PlayNow"},
            play_command="PlayNow",
            use_ytdlp=False,
        )
    )
    return {
        "ok": bool(out.get("ok", True)),
        "series_id": sid,
        "series_title": str(first.get("series_name") or first.get("title") or "").strip(),
        "queued_count": max(0, len(item_ids) - 1),
        "started_item_id": item_ids[0],
        "play_result": out,
    }


@router.post("/jellyfin/action")
def jellyfin_item_action(req: JellyfinItemActionReq):
    _require_jellyfin_catalog_ready_for_playback()
    item_id = str(req.item_id or "").strip()
    if not item_id:
        raise HTTPException(status_code=400, detail="item_id is required")
    command = str(req.command or "play_now").strip().lower()
    if command not in {"play_now", "play_next", "play_last", "resume"}:
        raise HTTPException(status_code=400, detail="unsupported command")

    payload: dict[str, object] = {"ItemId": item_id, "PlayCommand": "PlayNow"}
    if command == "play_next":
        payload["PlayCommand"] = "PlayNext"
    elif command == "play_last":
        payload["PlayCommand"] = "PlayLast"

    start_pos: float | None = None
    if command == "resume":
        if req.resume_pos is not None:
            try:
                rp = float(req.resume_pos)
                if rp > 0:
                    start_pos = rp
            except Exception:
                start_pos = None
        if start_pos is None:
            try:
                meta = jellyfin_receiver.get_item_detail(item_id)
                rp2 = float(meta.get("resume_pos")) if isinstance(meta, dict) and meta.get("resume_pos") is not None else None
                if rp2 is not None and rp2 > 0:
                    start_pos = rp2
            except Exception:
                start_pos = None

    if _jellyfin_should_suppress_duplicate_ui_action(command, item_id, start_pos):
        return {
            "ok": True,
            "action": "ui_suppressed",
            "suppressed_duplicate_ui_action": True,
            "ui_command": command,
            "item_id": item_id,
            "resolved_resume_pos": start_pos,
        }

    out = _jellyfin_integration_command(
        _jellyfin_command_req(
            action="Play",
            start_pos=start_pos,
            use_ytdlp=True,
            payload=payload,
        )
    )
    if isinstance(out, dict):
        out = dict(out)
        out["ui_command"] = command
        out["item_id"] = item_id
        out["resolved_resume_pos"] = start_pos
    return out


@router.get("/jellyfin/audio/options")
def jellyfin_audio_options(refresh: bool = False):
    _require_jellyfin_catalog_ready_for_playback()
    now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    if not isinstance(now, dict) or not now:
        raise HTTPException(status_code=409, detail="no active now_playing item")
    provider = str(now.get("provider") or "").strip().lower()
    item_id = str(now.get("jellyfin_item_id") or "").strip()
    if not item_id:
        item_id = _extract_jellyfin_item_id_from_url_raw(str(now.get("url") or ""))
    if provider != "jellyfin" and not item_id:
        raise HTTPException(status_code=409, detail="now_playing is not a jellyfin item")
    if not item_id:
        raise HTTPException(status_code=409, detail="missing jellyfin item_id for current playback")
    try:
        detail = jellyfin_receiver.get_item_detail(item_id, refresh=bool(refresh))
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin stream options")

    audio_streams = detail.get("audio_streams") if isinstance(detail, dict) and isinstance(detail.get("audio_streams"), list) else []
    current_idx = _first_nonempty_str(
        [
            str(now.get("jellyfin_audio_stream_index") or "").strip(),
            _extract_jellyfin_audio_stream_index_from_url(str(now.get("url") or "")),
        ]
    )
    runtime_idx, runtime_lang = _jellyfin_runtime_selected_audio_stream(audio_streams)
    if runtime_idx is not None:
        current_idx = str(runtime_idx)
    selected_numeric: int | None = None
    try:
        if current_idx != "":
            selected_numeric = int(current_idx)
    except Exception:
        selected_numeric = None

    options: list[dict[str, object]] = []
    current_opt: dict[str, object] | None = None
    fallback_default: dict[str, object] | None = None
    fallback_first: dict[str, object] | None = None
    for row in audio_streams:
        if not isinstance(row, dict):
            continue
        try:
            idx_int = int(row.get("index"))
        except Exception:
            continue
        opt = {
            "index": idx_int,
            "language": str(row.get("language") or "").strip(),
            "display": str(row.get("display") or "").strip(),
            "is_default": bool(row.get("is_default")),
            "is_current": False,
        }
        if fallback_first is None:
            fallback_first = opt
        if bool(opt["is_default"]) and fallback_default is None:
            fallback_default = opt
        if selected_numeric is not None and idx_int == selected_numeric:
            opt["is_current"] = True
            current_opt = opt
        options.append(opt)

    if current_opt is None and fallback_default is not None:
        for opt in options:
            if int(opt.get("index")) == int(fallback_default.get("index")):
                opt["is_current"] = True
                current_opt = opt
                break
    if current_opt is None and fallback_first is not None:
        for opt in options:
            if int(opt.get("index")) == int(fallback_first.get("index")):
                opt["is_current"] = True
                current_opt = opt
                break

    current_lang = _first_nonempty_str(
        [
            str((current_opt or {}).get("language") or "").strip(),
            runtime_lang,
            str(now.get("jellyfin_audio_language") or "").strip(),
            str(now.get("audio_language") or "").strip(),
            str(detail.get("audio_language") if isinstance(detail, dict) else "").strip(),
        ]
    )
    return {
        "ok": True,
        "item_id": item_id,
        "current_audio_stream_index": (current_opt or {}).get("index"),
        "current_audio_language": current_lang,
        "options": options,
    }


@router.get("/jellyfin/subtitle/options")
def jellyfin_subtitle_options(refresh: bool = False):
    _require_jellyfin_catalog_ready_for_playback()
    now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    if not isinstance(now, dict) or not now:
        raise HTTPException(status_code=409, detail="no active now_playing item")
    provider = str(now.get("provider") or "").strip().lower()
    item_id = str(now.get("jellyfin_item_id") or "").strip()
    if not item_id:
        item_id = _extract_jellyfin_item_id_from_url_raw(str(now.get("url") or ""))
    if provider != "jellyfin" and not item_id:
        raise HTTPException(status_code=409, detail="now_playing is not a jellyfin item")
    if not item_id:
        raise HTTPException(status_code=409, detail="missing jellyfin item_id for current playback")
    try:
        detail = jellyfin_receiver.get_item_detail(item_id, refresh=bool(refresh))
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin subtitle options")

    subtitle_streams = detail.get("subtitle_streams") if isinstance(detail, dict) and isinstance(detail.get("subtitle_streams"), list) else []
    current_idx = _first_nonempty_str(
        [
            str(now.get("jellyfin_subtitle_stream_index") or "").strip(),
            _extract_jellyfin_subtitle_stream_index_from_url(str(now.get("url") or "")),
        ]
    )
    runtime_idx, runtime_lang, runtime_off = _jellyfin_runtime_selected_subtitle_stream(subtitle_streams)
    if runtime_off:
        current_idx = "-1"
    elif runtime_idx is not None:
        current_idx = str(runtime_idx)
    current_is_off = current_idx == "-1"
    selected_numeric: int | None = None
    try:
        if not current_is_off and current_idx != "":
            selected_numeric = int(current_idx)
    except Exception:
        selected_numeric = None

    options: list[dict[str, object]] = [{
        "index": -1,
        "language": "",
        "display": "Off",
        "is_default": False,
        "is_current": current_is_off,
        "is_off": True,
    }]
    current_opt: dict[str, object] | None = options[0] if current_is_off else None
    fallback_default: dict[str, object] | None = None
    fallback_first: dict[str, object] | None = None
    for row in subtitle_streams:
        if not isinstance(row, dict):
            continue
        try:
            idx_int = int(row.get("index"))
        except Exception:
            continue
        opt = {
            "index": idx_int,
            "language": str(row.get("language") or "").strip(),
            "display": str(row.get("display") or "").strip(),
            "is_default": bool(row.get("is_default")),
            "is_current": False,
            "is_off": False,
        }
        if fallback_first is None:
            fallback_first = opt
        if bool(opt["is_default"]) and fallback_default is None:
            fallback_default = opt
        if selected_numeric is not None and idx_int == selected_numeric:
            opt["is_current"] = True
            current_opt = opt
        options.append(opt)

    if current_opt is None and fallback_default is not None:
        for opt in options:
            if int(opt.get("index")) == int(fallback_default.get("index")):
                opt["is_current"] = True
                current_opt = opt
                break
    if current_opt is None and fallback_first is not None:
        for opt in options:
            if int(opt.get("index")) == int(fallback_first.get("index")):
                opt["is_current"] = True
                current_opt = opt
                break

    current_lang = "off" if current_is_off else _first_nonempty_str(
        [
            str((current_opt or {}).get("language") or "").strip(),
            runtime_lang,
            str(now.get("jellyfin_subtitle_language") or "").strip(),
            str(now.get("subtitle_language") or "").strip(),
            str(detail.get("subtitle_language") if isinstance(detail, dict) else "").strip(),
        ]
    )
    return {
        "ok": True,
        "item_id": item_id,
        "current_subtitle_stream_index": -1 if current_is_off else (current_opt or {}).get("index"),
        "current_subtitle_language": current_lang,
        "current_subtitle_off": current_is_off,
        "options": options,
    }


@router.post("/jellyfin/audio/select")
def jellyfin_audio_select(req: JellyfinAudioSelectReq):
    st = _require_jellyfin_catalog_ready_for_playback()
    now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    if not isinstance(now, dict) or not now:
        raise HTTPException(status_code=409, detail="no active now_playing item")
    provider = str(now.get("provider") or "").strip().lower()
    item_id = str(now.get("jellyfin_item_id") or "").strip()
    if not item_id:
        item_id = _extract_jellyfin_item_id_from_url_raw(str(now.get("url") or ""))
    if provider != "jellyfin" and not item_id:
        raise HTTPException(status_code=409, detail="now_playing is not a jellyfin item")
    if not item_id:
        raise HTTPException(status_code=409, detail="missing jellyfin item_id for current playback")

    try:
        detail = jellyfin_receiver.get_item_detail(item_id)
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin item detail")

    audio_streams = detail.get("audio_streams") if isinstance(detail, dict) and isinstance(detail.get("audio_streams"), list) else []
    try:
        requested_idx = int(req.index)
    except Exception:
        raise HTTPException(status_code=400, detail="audio index must be an integer")
    if requested_idx < 0:
        raise HTTPException(status_code=400, detail="audio index must be non-negative")
    requested_audio_language = ""
    requested_audio_display = ""
    if audio_streams:
        valid = False
        for row in audio_streams:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("index")) == requested_idx:
                    valid = True
                    requested_audio_language = str(row.get("language") or "").strip()
                    requested_audio_display = str(row.get("display") or "").strip()
                    break
            except Exception:
                continue
        if not valid:
            raise HTTPException(status_code=400, detail="requested audio stream index is unavailable")
    preferred_audio_lang = _normalize_lang_pref(requested_audio_language)
    queue_retargeted = 0
    if preferred_audio_lang:
        try:
            state.update_settings({"jellyfin_audio_lang": preferred_audio_lang})
        except Exception:
            pass
        try:
            os.environ["RELAYTV_JELLYFIN_AUDIO_LANG"] = preferred_audio_lang
        except Exception:
            pass
        try:
            queue_retargeted = int(_retarget_jellyfin_queue_stream_preferences())
        except Exception:
            queue_retargeted = 0

    was_playing = bool(player.is_playing())
    try:
        props = player.mpv_get_many(["time-pos", "pause"]) if was_playing else {}
    except Exception:
        props = {}
    start_pos: float | None = None
    if isinstance(props, dict):
        try:
            raw_pos = props.get("time-pos")
            if raw_pos is not None:
                start_pos = float(raw_pos)
        except Exception:
            start_pos = None
    if start_pos is None:
        try:
            raw_resume = now.get("resume_pos")
            if raw_resume is not None:
                start_pos = float(raw_resume)
        except Exception:
            start_pos = None
    was_paused = bool((props or {}).get("pause")) or str(getattr(state, "SESSION_STATE", "") or "").strip().lower() == "paused"
    pause_reason = state.get_pause_reason() if hasattr(state, "get_pause_reason") else None

    media_source_id = _first_nonempty_str(
        [
            str(now.get("jellyfin_media_source_id") or "").strip(),
            _extract_jellyfin_media_source_id_from_url(str(now.get("url") or "")),
            str(detail.get("media_source_id") if isinstance(detail, dict) else "").strip(),
        ]
    )
    subtitle_stream_index = _first_nonempty_str(
        [
            str(now.get("jellyfin_subtitle_stream_index") or "").strip(),
            _extract_jellyfin_subtitle_stream_index_from_url(str(now.get("url") or "")),
        ]
    )
    audio_stream_index = str(requested_idx)

    if _jellyfin_try_set_mpv_audio_track(language=requested_audio_language, display=requested_audio_display):
        now_out = _jellyfin_enrich_now_stream_metadata(
            dict(now),
            detail=detail if isinstance(detail, dict) else {},
            audio_stream_index=audio_stream_index,
            subtitle_stream_index=subtitle_stream_index,
        )
        state.set_now_playing(now_out)
        _jellyfin_emit_progress_hint()
        return {
            "ok": True,
            "method": "mpv_runtime_aid",
            "item_id": item_id,
            "current_audio_stream_index": requested_idx,
            "current_audio_language": str(now_out.get("jellyfin_audio_language") or now_out.get("audio_language") or "").strip(),
            "queued_items_retargeted": queue_retargeted,
            "now_playing": now_out,
        }

    try:
        settings_snapshot = state.get_settings()
    except Exception:
        settings_snapshot = {}
    auth_token = _jellyfin_access_token()

    source_url = _build_jellyfin_item_stream_url(
        item_id,
        server_url=str(st.get("server_url") or ""),
        api_key=auth_token,
        media_source_id=media_source_id,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
    )
    selected_stream = _select_jellyfin_playback_url(
        item_id=item_id,
        source_url=source_url,
        server_url=str(st.get("server_url") or ""),
        api_key=auth_token,
        media_source_id=media_source_id,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
        settings=settings_snapshot if isinstance(settings_snapshot, dict) else {},
    )
    source_url = _normalize_jellyfin_source_url(
        str(selected_stream.get("url") or source_url),
        server_url=str(st.get("server_url") or ""),
        api_key=auth_token,
    )
    source_url = _apply_jellyfin_stream_params(
        source_url,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
    )
    media_source_id = _first_nonempty_str(
        [
            str(selected_stream.get("media_source_id") or "").strip(),
            _extract_jellyfin_media_source_id_from_url(source_url),
            media_source_id,
        ]
    )
    if not source_url:
        raise HTTPException(status_code=502, detail="unable to build jellyfin stream url")

    play_payload = {
        "url": source_url,
        "provider": "jellyfin",
        "title": str(now.get("title") or "").strip() or f"Jellyfin item {item_id}",
        **({"channel": now.get("channel")} if now.get("channel") else {}),
        **({"thumbnail": now.get("thumbnail")} if now.get("thumbnail") else {}),
        **({"thumbnail_local": now.get("thumbnail_local")} if now.get("thumbnail_local") else {}),
        "jellyfin_item_id": item_id,
        **({"jellyfin_media_source_id": media_source_id} if media_source_id else {}),
    }

    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0
    switched = player.play_item(
        play_payload,
        use_resolver=False,
        cec=False,
        clear_queue=False,
        mode="jellyfin_audio_switch",
        start_pos=start_pos,
    )
    now_out = switched if isinstance(switched, dict) else dict(play_payload)
    now_out["jellyfin_item_id"] = item_id
    if media_source_id:
        now_out["jellyfin_media_source_id"] = media_source_id
    now_out["jellyfin_stream_mode"] = str(selected_stream.get("mode") or "direct")
    now_out["jellyfin_stream_reason"] = str(selected_stream.get("reason") or "")
    now_out = _jellyfin_enrich_now_stream_metadata(
        now_out,
        detail=detail if isinstance(detail, dict) else {},
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
    )
    _jellyfin_try_set_mpv_audio_track(language=requested_audio_language, display=requested_audio_display)
    state.set_now_playing(now_out)
    if was_paused:
        try:
            player.mpv_set("pause", True)
        except Exception:
            pass
        state.set_session_state("paused")
        state.set_pause_reason(pause_reason if pause_reason is not None else "user")
    _jellyfin_emit_progress_hint()
    return {
        "ok": True,
        "item_id": item_id,
        "current_audio_stream_index": requested_idx,
        "current_audio_language": str(now_out.get("jellyfin_audio_language") or now_out.get("audio_language") or "").strip(),
        "queued_items_retargeted": queue_retargeted,
        "now_playing": now_out,
    }


@router.post("/jellyfin/subtitle/select")
def jellyfin_subtitle_select(req: JellyfinSubtitleSelectReq):
    st = _require_jellyfin_catalog_ready_for_playback()
    now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    if not isinstance(now, dict) or not now:
        raise HTTPException(status_code=409, detail="no active now_playing item")
    provider = str(now.get("provider") or "").strip().lower()
    item_id = str(now.get("jellyfin_item_id") or "").strip()
    if not item_id:
        item_id = _extract_jellyfin_item_id_from_url_raw(str(now.get("url") or ""))
    if provider != "jellyfin" and not item_id:
        raise HTTPException(status_code=409, detail="now_playing is not a jellyfin item")
    if not item_id:
        raise HTTPException(status_code=409, detail="missing jellyfin item_id for current playback")

    try:
        detail = jellyfin_receiver.get_item_detail(item_id)
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin item detail")

    subtitle_streams = detail.get("subtitle_streams") if isinstance(detail, dict) and isinstance(detail.get("subtitle_streams"), list) else []
    try:
        requested_idx = int(req.index)
    except Exception:
        raise HTTPException(status_code=400, detail="subtitle index must be an integer")
    if requested_idx < -1:
        raise HTTPException(status_code=400, detail="subtitle index must be -1 or non-negative")
    requested_subtitle_language = ""
    requested_subtitle_display = ""
    if requested_idx >= 0 and subtitle_streams:
        valid = False
        for row in subtitle_streams:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("index")) == requested_idx:
                    valid = True
                    requested_subtitle_language = str(row.get("language") or "").strip()
                    requested_subtitle_display = str(row.get("display") or "").strip()
                    break
            except Exception:
                continue
        if not valid:
            raise HTTPException(status_code=400, detail="requested subtitle stream index is unavailable")
    preferred_subtitle_lang = "off" if requested_idx < 0 else _normalize_lang_pref(requested_subtitle_language)
    queue_retargeted = 0
    try:
        state.update_settings({"jellyfin_sub_lang": preferred_subtitle_lang})
    except Exception:
        pass
    try:
        os.environ["RELAYTV_JELLYFIN_SUB_LANG"] = preferred_subtitle_lang
    except Exception:
        pass
    try:
        queue_retargeted = int(_retarget_jellyfin_queue_stream_preferences())
    except Exception:
        queue_retargeted = 0

    was_playing = bool(player.is_playing())
    try:
        props = player.mpv_get_many(["time-pos", "pause"]) if was_playing else {}
    except Exception:
        props = {}
    start_pos: float | None = None
    if isinstance(props, dict):
        try:
            raw_pos = props.get("time-pos")
            if raw_pos is not None:
                start_pos = float(raw_pos)
        except Exception:
            start_pos = None
    if start_pos is None:
        try:
            raw_resume = now.get("resume_pos")
            if raw_resume is not None:
                start_pos = float(raw_resume)
        except Exception:
            start_pos = None
    was_paused = bool((props or {}).get("pause")) or str(getattr(state, "SESSION_STATE", "") or "").strip().lower() == "paused"
    pause_reason = state.get_pause_reason() if hasattr(state, "get_pause_reason") else None

    media_source_id = _first_nonempty_str(
        [
            str(now.get("jellyfin_media_source_id") or "").strip(),
            _extract_jellyfin_media_source_id_from_url(str(now.get("url") or "")),
            str(detail.get("media_source_id") if isinstance(detail, dict) else "").strip(),
        ]
    )
    audio_stream_index = _first_nonempty_str(
        [
            str(now.get("jellyfin_audio_stream_index") or "").strip(),
            _extract_jellyfin_audio_stream_index_from_url(str(now.get("url") or "")),
        ]
    )
    subtitle_stream_index = "-1" if requested_idx < 0 else str(requested_idx)

    if _jellyfin_try_set_mpv_subtitle_track(
        language=requested_subtitle_language,
        display=requested_subtitle_display,
        preferred_stream_index=(requested_idx if requested_idx >= 0 else None),
        off=(requested_idx < 0),
    ):
        now_out = _jellyfin_enrich_now_stream_metadata(
            dict(now),
            detail=detail if isinstance(detail, dict) else {},
            audio_stream_index=audio_stream_index,
            subtitle_stream_index=subtitle_stream_index,
        )
        state.set_now_playing(now_out)
        _jellyfin_emit_progress_hint()
        return {
            "ok": True,
            "method": "mpv_runtime_sid",
            "item_id": item_id,
            "current_subtitle_stream_index": requested_idx,
            "current_subtitle_language": str(now_out.get("jellyfin_subtitle_language") or now_out.get("subtitle_language") or "").strip(),
            "current_subtitle_off": requested_idx < 0,
            "queued_items_retargeted": queue_retargeted,
            "now_playing": now_out,
        }

    try:
        settings_snapshot = state.get_settings()
    except Exception:
        settings_snapshot = {}
    auth_token = _jellyfin_access_token()

    source_url = _build_jellyfin_item_stream_url(
        item_id,
        server_url=str(st.get("server_url") or ""),
        api_key=auth_token,
        media_source_id=media_source_id,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
    )
    selected_stream = _select_jellyfin_playback_url(
        item_id=item_id,
        source_url=source_url,
        server_url=str(st.get("server_url") or ""),
        api_key=auth_token,
        media_source_id=media_source_id,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
        settings=settings_snapshot if isinstance(settings_snapshot, dict) else {},
    )
    source_url = _normalize_jellyfin_source_url(
        str(selected_stream.get("url") or source_url),
        server_url=str(st.get("server_url") or ""),
        api_key=auth_token,
    )
    source_url = _apply_jellyfin_stream_params(
        source_url,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
    )
    media_source_id = _first_nonempty_str(
        [
            str(selected_stream.get("media_source_id") or "").strip(),
            _extract_jellyfin_media_source_id_from_url(source_url),
            media_source_id,
        ]
    )
    if not source_url:
        raise HTTPException(status_code=502, detail="unable to build jellyfin stream url")

    play_payload = {
        "url": source_url,
        "provider": "jellyfin",
        "title": str(now.get("title") or "").strip() or f"Jellyfin item {item_id}",
        **({"channel": now.get("channel")} if now.get("channel") else {}),
        **({"thumbnail": now.get("thumbnail")} if now.get("thumbnail") else {}),
        **({"thumbnail_local": now.get("thumbnail_local")} if now.get("thumbnail_local") else {}),
        "jellyfin_item_id": item_id,
        **({"jellyfin_media_source_id": media_source_id} if media_source_id else {}),
    }

    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0
    switched = player.play_item(
        play_payload,
        use_resolver=False,
        cec=False,
        clear_queue=False,
        mode="jellyfin_subtitle_switch",
        start_pos=start_pos,
    )
    now_out = switched if isinstance(switched, dict) else dict(play_payload)
    now_out["jellyfin_item_id"] = item_id
    if media_source_id:
        now_out["jellyfin_media_source_id"] = media_source_id
    now_out["jellyfin_stream_mode"] = str(selected_stream.get("mode") or "direct")
    now_out["jellyfin_stream_reason"] = str(selected_stream.get("reason") or "")
    now_out = _jellyfin_enrich_now_stream_metadata(
        now_out,
        detail=detail if isinstance(detail, dict) else {},
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
    )
    _jellyfin_try_set_mpv_subtitle_track(
        language=requested_subtitle_language,
        display=requested_subtitle_display,
        preferred_stream_index=(requested_idx if requested_idx >= 0 else None),
        off=(requested_idx < 0),
    )
    state.set_now_playing(now_out)
    if was_paused:
        try:
            player.mpv_set("pause", True)
        except Exception:
            pass
        state.set_session_state("paused")
        state.set_pause_reason(pause_reason if pause_reason is not None else "user")
    _jellyfin_emit_progress_hint()
    return {
        "ok": True,
        "item_id": item_id,
        "current_subtitle_stream_index": requested_idx,
        "current_subtitle_language": str(now_out.get("jellyfin_subtitle_language") or now_out.get("subtitle_language") or "").strip(),
        "current_subtitle_off": requested_idx < 0,
        "queued_items_retargeted": queue_retargeted,
        "now_playing": now_out,
    }
