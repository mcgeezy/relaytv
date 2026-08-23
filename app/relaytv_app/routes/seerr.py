# SPDX-License-Identifier: GPL-3.0-only
"""Public RelayTV route surface for the Seerr integration."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from ..integrations import seerr_service
from ..integrations.seerr_client import SeerrError

router = APIRouter()


def _http_error(exc: SeerrError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


@router.get("/integrations/seerr/status")
def seerr_integration_status():
    return seerr_service.integration_status()


@router.post("/integrations/seerr/test")
def seerr_integration_test():
    try:
        return seerr_service.test_connection()
    except SeerrError as exc:
        raise _http_error(exc) from None


@router.get("/seerr/discover")
def seerr_discover(section: str = "trending", page: int = 1):
    try:
        return seerr_service.discover(section, page)
    except SeerrError as exc:
        raise _http_error(exc) from None


@router.get("/seerr/search")
def seerr_search(query: str, page: int = 1):
    try:
        return seerr_service.search(query, page)
    except SeerrError as exc:
        raise _http_error(exc) from None


@router.get("/seerr/item/{media_type}/{media_id}")
def seerr_item_detail(media_type: str, media_id: int):
    try:
        return seerr_service.item_detail(media_type, media_id)
    except SeerrError as exc:
        raise _http_error(exc) from None


@router.get("/seerr/requests")
def seerr_requests(take: int = 20, skip: int = 0, filter: str = "all"):
    try:
        return seerr_service.list_requests(take=take, skip=skip, status_filter=filter)
    except SeerrError as exc:
        raise _http_error(exc) from None


@router.get("/seerr/image/{size}/{image_path:path}")
def seerr_image(size: str, image_path: str):
    try:
        upstream = seerr_service.image(size, image_path)
    except SeerrError as exc:
        raise _http_error(exc) from None
    headers = {"Cache-Control": upstream.cache_control or "public, max-age=3600"}
    if upstream.etag:
        headers["ETag"] = upstream.etag
    if upstream.last_modified:
        headers["Last-Modified"] = upstream.last_modified
    return Response(
        content=upstream.content,
        media_type=upstream.content_type,
        headers=headers,
    )
