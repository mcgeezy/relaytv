# SPDX-License-Identifier: GPL-3.0-only
"""Public discovery surface for RelayTV realtime transports."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..realtime import realtime_capabilities_payload


router = APIRouter()


@router.get("/realtime/capabilities")
def realtime_capabilities() -> JSONResponse:
    # WebSocket routes land in M2. Until then discovery truthfully selects the
    # existing SSE transport even though the versioned WS paths are reserved.
    return JSONResponse(
        realtime_capabilities_payload(websocket_enabled=False),
        headers={"Cache-Control": "no-store"},
    )
