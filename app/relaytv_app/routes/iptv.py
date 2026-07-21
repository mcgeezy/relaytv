# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..integrations import iptv_service


router = APIRouter()


class IptvSourceCreateReq(BaseModel):
    name: str = ""
    location: str = ""
    content: str = ""
    preset_id: str = ""
    refresh_interval_sec: int = 21600
    refresh_now: bool = True


class IptvSourcePatchReq(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    location: str | None = None
    refresh_interval_sec: int | None = None


class IptvChannelPatchReq(BaseModel):
    source_id: str
    hidden: bool | None = None
    favorite: bool | None = None


class IptvVisibilityReq(BaseModel):
    source_id: str
    group: str
    hidden: bool


class IptvReorderReq(BaseModel):
    source_id: str
    channel_id: str
    before_channel_id: str = ""
    after_channel_id: str = ""


class IptvChannelActionReq(BaseModel):
    source_id: str
    command: str = "play_now"


def _require_enabled() -> None:
    if not iptv_service.enabled():
        raise HTTPException(status_code=503, detail="IPTV is disabled in settings")


@router.get("/integrations/iptv/status")
def iptv_status():
    return iptv_service.status()


@router.get("/iptv/directory")
def iptv_directory(q: str = ""):
    return {"items": iptv_service.directory(q), "query": str(q or "").strip()}


@router.post("/iptv/directory/{preset_id}/add")
def iptv_directory_add(preset_id: str):
    source = iptv_service.add_directory_source(preset_id)
    return {"ok": True, "source": source}


@router.get("/iptv/sources")
def iptv_sources():
    return {"items": iptv_service.store().list_sources()}


@router.post("/iptv/sources")
def iptv_source_create(req: IptvSourceCreateReq):
    source = iptv_service.create_source(
        name=req.name,
        location=req.location,
        content=req.content,
        preset_id=req.preset_id,
        refresh_interval_sec=req.refresh_interval_sec,
    )
    refresh = None
    if req.refresh_now:
        refresh = iptv_service.refresh_source(str(source["id"]))
        source = iptv_service.store().get_source(str(source["id"]), redacted=True) or source
    return {"ok": True, "source": source, "refresh": refresh}


@router.patch("/iptv/sources/{source_id}")
def iptv_source_update(source_id: str, req: IptvSourcePatchReq):
    patch = req.model_dump(exclude_none=True)
    return {"ok": True, "source": iptv_service.update_source(source_id, patch)}


@router.delete("/iptv/sources/{source_id}")
def iptv_source_delete(source_id: str):
    iptv_service.delete_source(source_id)
    return {"ok": True}


@router.post("/iptv/sources/{source_id}/refresh")
def iptv_source_refresh(source_id: str):
    return iptv_service.refresh_source(source_id)


@router.get("/iptv/channels")
def iptv_channels(
    source_id: str = "",
    q: str = "",
    group: str = "",
    visibility: str = "visible",
    favorites: bool = False,
    availability: str = "",
    sort: str = "manual",
    offset: int = 0,
    limit: int = 100,
):
    _require_enabled()
    visibility = str(visibility or "visible").strip().lower()
    if visibility not in {"visible", "hidden", "all"}:
        raise HTTPException(status_code=400, detail="unsupported visibility filter")
    sort = str(sort or "manual").strip().lower()
    if sort not in {"manual", "playlist", "name", "group"}:
        raise HTTPException(status_code=400, detail="unsupported IPTV sort")
    availability = str(availability or "").strip().lower()
    if availability not in {"", "unknown", "available", "suspect", "unavailable", "geo_blocked"}:
        raise HTTPException(status_code=400, detail="unsupported availability filter")
    return iptv_service.list_channels(
        source_id=str(source_id or "").strip(),
        query=str(q or "").strip()[:200],
        group=str(group or "").strip()[:200],
        visibility=visibility,
        favorites_only=bool(favorites),
        availability=availability,
        sort=sort,
        offset=max(0, int(offset)),
        limit=max(1, min(int(limit), 500)),
    )


@router.patch("/iptv/channels/{channel_id}")
def iptv_channel_update(channel_id: str, req: IptvChannelPatchReq):
    patch = req.model_dump(exclude={"source_id"}, exclude_none=True)
    return {
        "ok": True,
        "channel": iptv_service.update_channel(req.source_id, channel_id, patch),
    }


@router.post("/iptv/channels/visibility")
def iptv_channel_visibility(req: IptvVisibilityReq):
    if not str(req.group or "").strip():
        raise HTTPException(status_code=400, detail="group is required")
    updated = iptv_service.set_group_hidden(req.source_id, req.group, req.hidden)
    return {"ok": True, "updated": updated}


@router.post("/iptv/channels/reorder")
def iptv_channel_reorder(req: IptvReorderReq):
    iptv_service.reorder_channel(
        req.source_id,
        req.channel_id,
        before_channel_id=req.before_channel_id,
        after_channel_id=req.after_channel_id,
    )
    return {"ok": True}


@router.post("/iptv/channels/{channel_id}/check")
def iptv_channel_check(channel_id: str, req: IptvChannelActionReq):
    _require_enabled()
    return {"ok": True, "channel": iptv_service.check_channel(req.source_id, channel_id)}


@router.post("/iptv/channels/{channel_id}/action")
def iptv_channel_action(channel_id: str, req: IptvChannelActionReq):
    _require_enabled()
    return iptv_service.channel_action(req.source_id, channel_id, req.command)
