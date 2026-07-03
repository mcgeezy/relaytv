# SPDX-License-Identifier: GPL-3.0-only
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import player, state, upload_store
from ..debug import get_logger


router = APIRouter()
logger = get_logger("routes.queue")


class EnqueueReq(BaseModel):
    url: str


class QueueRemoveReq(BaseModel):
    index: int


class QueueMoveReq(BaseModel):
    from_index: int
    to_index: int


class HistoryPlayReq(BaseModel):
    index: int


def _smart_item_from_url(url: str, *, lightweight: bool = False) -> dict:
    from . import _smart_item_from_url as build_item

    return build_item(url, lightweight=lightweight)


def _push_queue_added_toast_async(item: object, fallback_label: str) -> None:
    from . import _push_queue_added_toast_async as push_toast

    push_toast(item, fallback_label)


def _ui_event_push_queue(
    action: str,
    queue: list[object] | None = None,
    queue_length: int | None = None,
    source: str = "api",
) -> None:
    from . import _ui_event_push_queue as push_queue_event

    push_queue_event(action, queue=queue, queue_length=queue_length, source=source)


def _play_now_from_history(payload: dict[str, object]) -> dict:
    from . import PlayNowReq, play_now

    return play_now(PlayNowReq(**payload))


def _annotate_upload_item(item: object) -> object:
    return upload_store.annotate_item(item)


def _annotate_upload_items(items: list[object] | None) -> list[object]:
    return [_annotate_upload_item(item) for item in list(items or [])]


@router.post("/enqueue")
@router.post("/queue/add")
@router.post("/api/queue/add")
@router.post("/v1/queue/add")
def enqueue(req: EnqueueReq):
    try:
        item = _smart_item_from_url(req.url or "", lightweight=True)
    except TypeError:
        # Compatibility for tests/patches that mock _smart_item_from_url(url).
        from . import _smart_item_from_url as build_item

        item = build_item(req.url or "")
    with state.QUEUE_LOCK:
        state.QUEUE.append(item)
        qlen = len(state.QUEUE)
        queue_snapshot = list(state.QUEUE)
    state.persist_queue()
    try:
        player.prefetch_queue_item_stream(item)
    except Exception:
        pass
    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    try:
        _push_queue_added_toast_async(item, req.url or "item")
    except Exception:
        pass
    _ui_event_push_queue("add", queue=queue_snapshot, queue_length=qlen, source="enqueue")
    return {"status": "queued", "item": item, "queue_length": qlen, "now_playing": state.NOW_PLAYING}


@router.post("/clear")
def clear():
    with state.QUEUE_LOCK:
        state.QUEUE.clear()
        queue_snapshot: list[object] = []
    state.persist_queue()
    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    _ui_event_push_queue("clear", queue=queue_snapshot, queue_length=0, source="clear")
    return {"status": "cleared"}


@router.get("/queue")
def queue():
    with state.QUEUE_LOCK:
        q = list(state.QUEUE)
    return {
        "now_playing": _annotate_upload_item(state.NOW_PLAYING),
        "queue": _annotate_upload_items(q),
        "queue_length": len(q),
    }


@router.get("/history")
def history():
    with state.HISTORY_LOCK:
        h = list(state.HISTORY)
    return {
        "history": _annotate_upload_items(h),
        "history_length": len(h),
        "limit": state.HISTORY_LIMIT,
    }


@router.post("/history/clear")
def history_clear():
    with state.HISTORY_LOCK:
        state.HISTORY.clear()
    state.persist_history()
    return {"status": "cleared"}


@router.post("/history/play")
def history_play(req: HistoryPlayReq):
    """Play an item from history by index while preserving current playback."""
    idx = int(req.index)
    with state.HISTORY_LOCK:
        if idx < 0 or idx >= len(state.HISTORY):
            raise HTTPException(status_code=400, detail="index out of range")
        it = dict(state.HISTORY[idx])
    url = it.get("url")
    if not isinstance(url, str) or not url.strip():
        raise HTTPException(status_code=400, detail="history item missing url")

    resume_pos = None
    try:
        raw_resume = it.get("resume_pos")
        resume_pos = float(raw_resume) if raw_resume is not None else None
    except Exception:
        resume_pos = None
    try:
        resolved_at = float(it.get("_resolved_at")) if it.get("_resolved_at") is not None else None
    except Exception:
        resolved_at = None
    return _play_now_from_history(
        {
            "url": url.strip(),
            "preserve_current": True,
            "reason": "history",
            "title": str(it.get("title") or "").strip() or None,
            "thumbnail": str(it.get("thumbnail") or "").strip() or None,
            "resume_pos": resume_pos,
            "history_id": str(it.get("history_id") or "").strip() or None,
            "resolved_source_url": str(it.get("_resolved_source_url") or "").strip() or None,
            "resolved_stream": str(it.get("_resolved_stream") or "").strip() or None,
            "resolved_audio": str(it.get("_resolved_audio") or "").strip() or None,
            "resolved_at": resolved_at,
        }
    )


@router.post("/queue/remove")
def queue_remove(req: QueueRemoveReq):
    with state.QUEUE_LOCK:
        idx = int(req.index)
        if idx < 0 or idx >= len(state.QUEUE):
            raise HTTPException(status_code=400, detail="index out of range")
        removed = state.QUEUE.pop(idx)
        snapshot = {"queue": list(state.QUEUE), "saved_at": int(time.time())}

    try:
        state.persist_queue_payload(snapshot)
    except Exception as e:
        logger.warning("queue_persist_failed route=queue_remove error=%s", e)
    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    _ui_event_push_queue("remove", queue=snapshot["queue"], queue_length=len(snapshot["queue"]), source="queue_remove")

    return {"status": "removed", "removed": removed, "queue": snapshot["queue"], "queue_length": len(snapshot["queue"])}


def _queue_item_dedupe_key(item: object) -> tuple[str, str]:
    if not isinstance(item, dict):
        return ("raw", str(item))
    provider = str(item.get("provider") or "").strip().lower()
    url = str(item.get("url") or "").strip()
    if provider == "jellyfin":
        from . import _canonical_jellyfin_item_id, _canonical_jellyfin_url_key

        iid = _canonical_jellyfin_item_id(item.get("jellyfin_item_id"))
        if iid:
            return ("jellyfin_id", iid)
        ukey = _canonical_jellyfin_url_key(url)
        if ukey:
            return ("jellyfin_url", ukey)
    if url:
        return ("url", url)
    title = str(item.get("title") or "").strip()
    return ("title", f"{provider}|{title}")


@router.post("/queue/dedupe")
def queue_dedupe():
    with state.QUEUE_LOCK:
        original = list(state.QUEUE)
        seen: set[tuple[str, str]] = set()
        deduped: list[object] = []
        removed = 0
        for entry in original:
            key = _queue_item_dedupe_key(entry)
            if key in seen:
                removed += 1
                continue
            seen.add(key)
            deduped.append(entry)
        changed = len(deduped) != len(original)
        if changed:
            state.QUEUE[:] = deduped
            snapshot = {"queue": list(state.QUEUE), "saved_at": int(time.time())}
            try:
                state.persist_queue_payload(snapshot)
            except Exception as e:
                logger.warning("queue_persist_failed route=queue_dedupe error=%s", e)
    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    if changed:
        _ui_event_push_queue("dedupe", queue=list(state.QUEUE), queue_length=len(state.QUEUE), source="queue_dedupe")
    return {
        "status": "deduped",
        "changed": changed,
        "removed_count": removed,
        "queue_length": len(state.QUEUE),
        "queue": list(state.QUEUE),
    }


@router.post("/queue/move")
def queue_move(req: QueueMoveReq):
    frm = int(req.from_index)
    to = int(req.to_index)
    with state.QUEUE_LOCK:
        n = len(state.QUEUE)
        if n == 0:
            raise HTTPException(status_code=400, detail="queue is empty")
        if frm < 0 or frm >= n or to < 0 or to >= n:
            raise HTTPException(status_code=400, detail="index out of range")
        item = state.QUEUE.pop(frm)
        state.QUEUE.insert(to, item)
        snapshot = {"queue": list(state.QUEUE), "saved_at": int(time.time())}

        try:
            state.persist_queue_payload(snapshot)
        except Exception as e:
            logger.warning("queue_persist_failed route=queue_move error=%s", e)
    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    _ui_event_push_queue("move", queue=snapshot["queue"], queue_length=len(snapshot["queue"]), source="queue_move")

    return {"status": "moved", "queue": snapshot["queue"], "queue_length": len(snapshot["queue"])}
