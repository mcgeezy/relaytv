# SPDX-License-Identifier: GPL-3.0-only
"""Frame capture endpoints.

Capture used to report success the moment the mpv command was dispatched:
the result was discarded and the response named an ``image_url`` before any
file existed. Clients got ``ok: true`` followed by a 404, or a URL that stayed
empty forever when mpv had rejected the command outright. Both endpoints now
wait, briefly and with a bound, for a frame that is actually on disk.
"""

import os
import threading
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from .. import player


router = APIRouter()

# How long to wait for mpv to land the frame. Writing a screenshot is fast on
# the NUC and slower on the Pi's SD card, so this is an operator knob rather
# than a constant; the point is that it is bounded, not that it is generous.
_DEFAULT_SNAPSHOT_TIMEOUT_SEC = 2.0
_SNAPSHOT_POLL_SEC = 0.05
_DEFAULT_SNAPSHOT_MAX_FILES = 50
_DEFAULT_SNAPSHOT_MAX_BYTES = 100 * 1024 * 1024
_SNAPSHOT_LOCK = threading.Lock()
_LAST_SNAPSHOT_MS = 0


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


def _snapshot_max_files() -> int:
    raw = os.getenv("RELAYTV_SNAPSHOT_MAX_FILES", "")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_SNAPSHOT_MAX_FILES
    return value if value > 0 else _DEFAULT_SNAPSHOT_MAX_FILES


def _snapshot_max_bytes() -> int:
    raw = os.getenv("RELAYTV_SNAPSHOT_MAX_BYTES", "")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_SNAPSHOT_MAX_BYTES
    return value if value > 0 else _DEFAULT_SNAPSHOT_MAX_BYTES


def _next_snapshot_name() -> str:
    global _LAST_SNAPSHOT_MS
    with _SNAPSHOT_LOCK:
        now_ms = int(time.time() * 1000)
        if now_ms <= _LAST_SNAPSHOT_MS:
            now_ms = _LAST_SNAPSHOT_MS + 1
        _LAST_SNAPSHOT_MS = now_ms
    return f"snapshot-{now_ms}.jpg"


def _prune_snapshots(snap_dir: str, *, preserve_path: str) -> None:
    """Evict oldest ``snapshot-*.jpg`` files exceeding file-count or byte budgets."""
    max_files = _snapshot_max_files()
    max_bytes = _snapshot_max_bytes()
    preserve_abs = os.path.abspath(preserve_path)
    with _SNAPSHOT_LOCK:
        try:
            candidates: list[tuple[int, str, str, int]] = []
            preserved_bytes = 0
            preserved_count = 0
            with os.scandir(snap_dir) as entries:
                for entry in entries:
                    if not (entry.name.startswith("snapshot-") and entry.name.endswith(".jpg")):
                        continue
                    try:
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    entry_abs = os.path.abspath(entry.path)
                    size = max(0, int(st.st_size))
                    if entry_abs == preserve_abs:
                        preserved_count = 1
                        preserved_bytes = size
                        continue
                    candidates.append((int(st.st_mtime_ns), entry.name, entry.path, size))
        except OSError:
            return

        candidates.sort(key=lambda item: (item[0], item[1]))
        total_files = preserved_count + len(candidates)
        total_bytes = preserved_bytes + sum(item[3] for item in candidates)
        for _mtime_ns, _name, candidate_path, size in candidates:
            if total_files <= max_files and total_bytes <= max_bytes:
                break
            try:
                os.unlink(candidate_path)
            except OSError:
                pass
            total_files -= 1
            total_bytes -= size


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
    if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename or ".." in filename:
        return Response(status_code=400)
    snap_dir = os.getenv("RELAYTV_SNAPSHOT_DIR", "/data/snapshots")
    path = os.path.join(snap_dir, filename)
    if not await run_in_threadpool(os.path.isfile, path):
        return Response(status_code=404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.post("/snapshot")
@router.get("/snapshot")
def snapshot():
    if not player.is_playing():
        raise HTTPException(status_code=409, detail="No active playback for snapshot")
    snap_dir = os.getenv("RELAYTV_SNAPSHOT_DIR", "/data/snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    name = _next_snapshot_name()
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

    _prune_snapshots(snap_dir, preserve_path=path)
    return {"ok": True, "image_url": f"/snapshots/{name}"}
