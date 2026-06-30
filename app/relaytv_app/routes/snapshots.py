# SPDX-License-Identifier: GPL-3.0-only
import os
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from .. import player


router = APIRouter()


@router.get("/snapshots/{filename}")
async def get_snapshot(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        return Response(status_code=400)
    snap_dir = os.getenv("RELAYTV_SNAPSHOT_DIR", "/data/snapshots")
    path = os.path.join(snap_dir, filename)
    if not os.path.exists(path):
        return Response(status_code=404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.post("/snapshot")
@router.get("/snapshot")
def snapshot():
    if not player.is_playing():
        raise HTTPException(status_code=409, detail="No active playback for snapshot")
    snap_dir = os.getenv("RELAYTV_SNAPSHOT_DIR", "/data/snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    name = f"snapshot-{int(time.time() * 1000)}.jpg"
    path = os.path.join(snap_dir, name)
    player.mpv_command(["screenshot-to-file", path, "video"])
    return {"ok": True, "image_url": f"/snapshots/{name}"}
