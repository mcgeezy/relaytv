# SPDX-License-Identifier: GPL-3.0-only
"""Public RelayTV route surface for Plex account and server setup."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from ..integrations import plex_auth, plex_service
from ..integrations.plex_client import PlexError


router = APIRouter()
LINK_COOKIE = "relaytv_plex_link"


class PlexLinkFlowReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flow_id: str


class PlexServerSelectReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    machine_id: str


def _http_error(exc: PlexError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get("/integrations/plex/status")
def plex_integration_status(response: Response):
    _no_store(response)
    try:
        return plex_auth.auth_manager.status()
    except PlexError as exc:
        raise _http_error(exc) from None


@router.post("/integrations/plex/auth/start")
def plex_auth_start(request: Request, response: Response):
    _no_store(response)
    browser_secret = secrets.token_urlsafe(32)
    try:
        result = plex_auth.auth_manager.start_link(browser_secret)
    except PlexError as exc:
        raise _http_error(exc) from None
    response.set_cookie(
        key=LINK_COOKIE,
        value=browser_secret,
        max_age=plex_auth.FLOW_MAX_AGE_SEC,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/integrations/plex/auth",
    )
    return result


@router.post("/integrations/plex/auth/poll")
def plex_auth_poll(req: PlexLinkFlowReq, request: Request, response: Response):
    _no_store(response)
    try:
        result = plex_auth.auth_manager.poll_link(
            req.flow_id,
            str(request.cookies.get(LINK_COOKIE) or ""),
        )
    except PlexError as exc:
        raise _http_error(exc) from None
    if bool(result.get("linked")):
        response.delete_cookie(
            LINK_COOKIE,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/integrations/plex/auth",
        )
    return result


@router.post("/integrations/plex/auth/cancel")
def plex_auth_cancel(req: PlexLinkFlowReq, request: Request, response: Response):
    _no_store(response)
    try:
        result = plex_auth.auth_manager.cancel_link(
            req.flow_id,
            str(request.cookies.get(LINK_COOKIE) or ""),
        )
    except PlexError as exc:
        raise _http_error(exc) from None
    response.delete_cookie(
        LINK_COOKIE,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/integrations/plex/auth",
    )
    return result


@router.post("/integrations/plex/disconnect")
def plex_disconnect(request: Request, response: Response):
    _no_store(response)
    try:
        result = plex_auth.auth_manager.disconnect()
    except PlexError as exc:
        raise _http_error(exc) from None
    response.delete_cookie(
        LINK_COOKIE,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/integrations/plex/auth",
    )
    return result


@router.get("/plex/servers")
def plex_servers(response: Response):
    _no_store(response)
    try:
        return {"servers": plex_auth.auth_manager.list_servers()}
    except PlexError as exc:
        raise _http_error(exc) from None


@router.get("/plex/home")
def plex_home(response: Response, limit: int = 20):
    _no_store(response)
    try:
        return plex_service.catalog_service.home(limit=max(1, min(50, int(limit))))
    except PlexError as exc:
        raise _http_error(exc) from None


@router.get("/plex/libraries")
def plex_libraries(response: Response):
    _no_store(response)
    try:
        return plex_service.catalog_service.libraries()
    except PlexError as exc:
        raise _http_error(exc) from None


@router.get("/plex/libraries/{library_id}/items")
def plex_library_items(
    library_id: str,
    response: Response,
    start: int = 0,
    limit: int = 60,
    sort: str = "title",
):
    _no_store(response)
    try:
        return plex_service.catalog_service.library_items(
            library_id,
            start=max(0, int(start)),
            limit=max(1, min(100, int(limit))),
            sort=str(sort or "title"),
        )
    except PlexError as exc:
        raise _http_error(exc) from None


@router.get("/plex/search")
def plex_search(response: Response, q: str = "", limit: int = 40):
    _no_store(response)
    query = str(q or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="q is required")
    if len(query) > 200:
        raise HTTPException(status_code=400, detail="q is too long")
    try:
        return plex_service.catalog_service.search(
            query,
            limit=max(1, min(100, int(limit))),
        )
    except PlexError as exc:
        raise _http_error(exc) from None


@router.get("/plex/items/{item_id}")
def plex_item_detail(item_id: str, response: Response):
    _no_store(response)
    try:
        return plex_service.catalog_service.item_detail(item_id)
    except PlexError as exc:
        raise _http_error(exc) from None


@router.get("/plex/items/{item_id}/children")
def plex_item_children(
    item_id: str,
    response: Response,
    start: int = 0,
    limit: int = 60,
):
    _no_store(response)
    try:
        return plex_service.catalog_service.children(
            item_id,
            start=max(0, int(start)),
            limit=max(1, min(100, int(limit))),
        )
    except PlexError as exc:
        raise _http_error(exc) from None


@router.get("/plex/artwork/{asset_id}")
def plex_artwork(asset_id: str):
    try:
        artwork = plex_service.catalog_service.artwork(asset_id)
    except PlexError as exc:
        raise _http_error(exc) from None
    return Response(
        content=artwork.body,
        media_type=artwork.content_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/integrations/plex/server")
def plex_server_select(req: PlexServerSelectReq, response: Response):
    _no_store(response)
    try:
        return {"server": plex_auth.auth_manager.select_server(req.machine_id)}
    except PlexError as exc:
        raise _http_error(exc) from None


@router.post("/integrations/plex/test")
def plex_integration_test(response: Response):
    _no_store(response)
    try:
        return plex_auth.auth_manager.test_selected_server()
    except PlexError as exc:
        raise _http_error(exc) from None
