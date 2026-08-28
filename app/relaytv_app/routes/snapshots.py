# SPDX-License-Identifier: GPL-3.0-only
"""Frame capture endpoints.

Capture used to report success the moment the mpv command was dispatched:
the result was discarded and the response named an ``image_url`` before any
file existed. Clients got ``ok: true`` followed by a 404, or a URL that stayed
empty forever when mpv had rejected the command outright. Both endpoints now
wait, briefly and with a bound, for a frame that is actually on disk.
"""
import os
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from .. import player


router = APIRouter()

# How long to wait for mpv to land the frame. Writing a screenshot is fast on
# the NUC and slower on the Pi's SD card, so this is an operator knob rather
# than a constant; the point is that it is bounded, not that it is generous.
_DEFAULT_SNAPSHOT_TIMEOUT_SEC = 2.0
_SNAPSHOT_POLL_SEC = 0.05


def _snapshot_timeout_sec() -> float:
    # A startup knob, not a settings-bus variable: read from the environment
    # directly, the same way RELAYTV_SNAPSHOT_DIR is read below.
    raw = os.getenv("RELAYTV_SNAPSHOT_TIMEOUT_SEC", "")
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_SNAPSHOT_TIMEOUT_SEC
    if value <= 0:
        return _DEFAULT_SNAPSHOT_TIMEOUT_SEC
    return min(value, 30.0)


def _mpv_command_succeeded(result: object) -> bool:
    """Interpret ``player.mpv_command``'s heterogeneous result.

    The IPC path returns mpv's own reply dict, the Qt runtime path returns a
    bool, and a dropped command returns ``None``. Only an explicit success
    counts; everything else is a failure we must not report as a capture.
    """
    if isinstance(result, bool):
        return result
    if isinstance(result, dict):
        return str(result.get("error") or "").strip().lower() == "success"
    return False


def _wait_for_frame(path: str, timeout_sec: float) -> bool:
    """Return True once ``path`` holds a non-empty file, else False on timeout."""
    deadline = time.monotonic() + timeout_sec
    while True:
        try:
            if os.path.getsize(path) > 0:
                return True
        except OSError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(_SNAPSHOT_POLL_SEC)


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

    result = player.mpv_command(["screenshot-to-file", path, "video"])
    if not _mpv_command_succeeded(result):
        raise HTTPException(status_code=502, detail="Player rejected the snapshot command")

    if not _wait_for_frame(path, _snapshot_timeout_sec()):
        # Nothing usable landed. Clear the stub so /snapshots does not serve a
        # zero-byte image, and tell the caller rather than handing them a URL
        # that will 404.
        try:
            os.unlink(path)
        except OSError:
            pass
        raise HTTPException(status_code=504, detail="Snapshot did not complete in time")

    return {"ok": True, "image_url": f"/snapshots/{name}"}
