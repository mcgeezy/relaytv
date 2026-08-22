# SPDX-License-Identifier: GPL-3.0-only
"""Public discovery surface for RelayTV realtime transports."""

from fastapi import APIRouter, WebSocket
from fastapi.responses import JSONResponse

from ..realtime import realtime_capabilities_payload


router = APIRouter()


@router.get("/realtime/capabilities")
def realtime_capabilities() -> JSONResponse:
    return JSONResponse(
        realtime_capabilities_payload(websocket_enabled=True),
        headers={"Cache-Control": "no-store"},
    )


@router.websocket("/ui/ws")
async def ui_websocket(websocket: WebSocket) -> None:
    # The aggregate still owns status construction. Keep this import deferred
    # so this transport route does not create an import cycle at module load.
    from . import _ui_websocket_session

    await _ui_websocket_session(websocket)
