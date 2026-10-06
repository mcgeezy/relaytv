# SPDX-License-Identifier: GPL-3.0-only
"""Public RelayTV route surface for the Seerr integration."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from ..integrations import seerr_service
from ..integrations import seerr_sessions
from ..integrations.seerr_client import SeerrError

router = APIRouter()


class SeerrRequestCreateReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_type: str
    media_id: int
    seasons: list[int] | str | None = None
    is_4k: bool = False


class SeerrQuickConnectCompleteReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flow_id: str


class SeerrPlaybackReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_type: str
    media_id: int
    command: str = "play_now"


def _http_error(exc: SeerrError, session_id: str | None = None) -> HTTPException:
    if exc.code == "seerr_session_expired":
        seerr_sessions.retire(session_id)
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


@router.get("/integrations/seerr/status")
def seerr_integration_status(request: Request):
    return seerr_service.integration_status(session_id=_session_id(request))


@router.post("/integrations/seerr/test")
def seerr_integration_test():
    try:
        return seerr_service.test_connection()
    except SeerrError as exc:
        raise _http_error(exc) from None


@router.get("/seerr/discover")
def seerr_discover(request: Request, section: str = "trending", page: int = 1):
    session_id = _session_id(request)
    try:
        return seerr_service.discover(section, page, session_id=session_id)
    except SeerrError as exc:
        raise _http_error(exc, session_id) from None


@router.get("/seerr/search")
def seerr_search(request: Request, query: str, page: int = 1):
    session_id = _session_id(request)
    try:
        return seerr_service.search(query, page, session_id=session_id)
    except SeerrError as exc:
        raise _http_error(exc, session_id) from None


@router.get("/seerr/item/{media_type}/{media_id}")
def seerr_item_detail(request: Request, media_type: str, media_id: int):
    session_id = _session_id(request)
    try:
        return seerr_service.item_detail(media_type, media_id, session_id=session_id)
    except SeerrError as exc:
        raise _http_error(exc, session_id) from None


@router.get("/seerr/requests")
def seerr_requests(
    request: Request, take: int = 20, skip: int = 0, filter: str = "all"
):
    session_id = _session_id(request)
    try:
        return seerr_service.list_requests(
            take=take,
            skip=skip,
            status_filter=filter,
            session_id=session_id,
        )
    except SeerrError as exc:
        raise _http_error(exc, session_id) from None


@router.post("/seerr/requests")
def seerr_request_create(request: Request, req: SeerrRequestCreateReq):
    session_id = _session_id(request)
    try:
        return seerr_service.create_request(
            media_type=req.media_type,
            media_id=req.media_id,
            seasons=req.seasons,
            is_4k=req.is_4k,
            session_id=session_id,
        )
    except SeerrError as exc:
        raise _http_error(exc, session_id) from None


@router.post("/seerr/playback")
def seerr_playback(request: Request, req: SeerrPlaybackReq):
    session_id = _session_id(request)
    try:
        return seerr_service.playback_action(
            media_type=req.media_type,
            media_id=req.media_id,
            command=req.command,
            session_id=session_id,
        )
    except SeerrError as exc:
        raise _http_error(exc, session_id) from None


@router.post("/integrations/seerr/session/quick-connect")
def seerr_quick_connect_start():
    try:
        return seerr_sessions.initiate()
    except SeerrError as exc:
        raise _http_error(exc) from None


@router.post("/integrations/seerr/session/quick-connect/complete")
def seerr_quick_connect_complete(
    req: SeerrQuickConnectCompleteReq,
    request: Request,
    response: Response,
):
    try:
        session_id, result = seerr_sessions.complete(req.flow_id)
    except SeerrError as exc:
        raise _http_error(exc) from None
    if session_id:
        response.set_cookie(
            key=seerr_sessions.COOKIE_NAME,
            value=session_id,
            max_age=seerr_sessions.SESSION_TTL_SEC,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
    return result


@router.get("/integrations/seerr/session")
def seerr_caller_session_status(request: Request):
    return seerr_sessions.status(_session_id(request))


@router.post("/integrations/seerr/session/logout")
def seerr_caller_session_logout(request: Request, response: Response):
    seerr_sessions.retire(_session_id(request))
    response.delete_cookie(
        seerr_sessions.COOKIE_NAME,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/",
    )
    return {"connected": False}


@router.get("/integrations/seerr/users")
def seerr_integration_users():
    try:
        return {"users": seerr_service.list_users()}
    except SeerrError as exc:
        raise _http_error(exc) from None


@router.get("/seerr/image/{size}/{image_path:path}")
def seerr_image(size: str, image_path: str):
    try:
        upstream = seerr_service.image(size, image_path)
    except SeerrError as exc:
        raise _http_error(exc) from None
    headers = {
        "Cache-Control": upstream.cache_control or "public, max-age=3600",
        "X-Content-Type-Options": "nosniff",
    }
    if upstream.etag:
        headers["ETag"] = upstream.etag
    if upstream.last_modified:
        headers["Last-Modified"] = upstream.last_modified
    return Response(
        content=upstream.content,
        media_type=upstream.content_type,
        headers=headers,
    )


def _session_id(request: Request) -> str:
    return str(request.cookies.get(seerr_sessions.COOKIE_NAME) or "")
