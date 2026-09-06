# SPDX-License-Identifier: GPL-3.0-only
"""Public RelayTV route surface for Plex account and server setup."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from ..integrations import plex_auth
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
