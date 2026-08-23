# SPDX-License-Identifier: GPL-3.0-only
"""Public RelayTV route surface for the Seerr integration."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

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
