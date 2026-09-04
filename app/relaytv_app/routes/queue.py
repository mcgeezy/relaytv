# SPDX-License-Identifier: GPL-3.0-only
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import player, playback_service, public_media, resolver, state, upload_store
from ..debug import get_logger


router = APIRouter()
logger = get_logger("routes.queue")

# Hints a peer may send alongside the URL. Everything else about the item is
# rebuilt locally, so a peer can never inject resolved streams or headers.
#
# Text hints outrank a lightweight local title: queue-time item building defers
# metadata lookups and falls back to a URL-derived placeholder, while the
# sender already has the real title. Artwork and duration only fill gaps —
# preferring a peer's thumbnail would make our artwork depend on that device
# staying reachable.
PEER_TEXT_HINTS = ("title", "channel")
PEER_FILL_HINTS = ("thumbnail", "duration")


class EnqueueReq(BaseModel):
    url: str


class QueueImportItem(BaseModel):
    url: str
    title: str = ""
    thumbnail: str = ""
    channel: str = ""
    duration: float | None = None
    provider: str = ""


class QueueImportSender(BaseModel):
    device_id: str = ""
    name: str = ""
    base_url: str = ""


class QueueImportReq(BaseModel):
    items: list[QueueImportItem] = []
    mode: str = "append"
    # ``from`` is a Python keyword, so the wire name arrives through an alias.
    from_device: QueueImportSender | None = Field(default=None, alias="from")

    model_config = {"populate_by_name": True}


class QueueHandoffReq(BaseModel):
    now_playing: QueueImportItem
    resume_pos: float | None = None
    items: list[QueueImportItem] = []
    from_device: QueueImportSender | None = Field(default=None, alias="from")

    model_config = {"populate_by_name": True}


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


def _play_now_queue_item(item: dict[str, object], *, resume_pos: float | None) -> dict:
    from . import _play_now_item

    url = str(item.get("url") or "").strip()
    return _play_now_item(
        item,
        request_url=url,
        preserve_current=True,
        reason="queue_play",
        title_hint=str(item.get("title") or "").strip() or None,
        resume_pos=resume_pos,
    )


def _annotate_upload_item(item: object) -> object:
    return public_media.public_media_item(upload_store.annotate_item(item))


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
    qlen, queue_snapshot = playback_service.queue_item(item)
    try:
        _push_queue_added_toast_async(item, req.url or "item")
    except Exception:
        pass
    _ui_event_push_queue("add", queue=queue_snapshot, queue_length=qlen, source="enqueue")
    return {
        "status": "queued",
        "item": _annotate_upload_item(item),
        "queue_length": qlen,
        "now_playing": _annotate_upload_item(state.NOW_PLAYING),
    }


def _push_overlay_toast(**kwargs) -> None:
    from . import _push_overlay_toast as push_toast

    push_toast(**kwargs)


def _peer_hosted_media_item(url: str, entry: QueueImportItem) -> dict:
    """Build an item for media the sending device hosts itself.

    A peer's upload URL is upload-shaped (``/media/uploads/...``), so the normal
    item build would resolve it against *this* device's upload store and report
    it as expired. The file lives on the peer, so treat it as ordinary remote
    media streamed over HTTP from that device instead. The sender declares the
    provider, which is why no host matching is needed here.
    """
    item: dict[str, object] = {
        "url": url,
        # "other" is what provider_from_url returns for any plain remote media
        # URL, which is exactly what this is once the upload shape is ignored.
        "provider": "other",
        "title": entry.title or url,
        # Keeps upload annotation off this item on every later read.
        "peer_hosted": True,
    }
    if entry.channel:
        item["channel"] = entry.channel
    if entry.thumbnail:
        item["thumbnail"] = entry.thumbnail
    if entry.duration:
        item["duration"] = entry.duration
    return item


def _peer_display_item(url: str, entry: QueueImportItem) -> dict:
    """Build a queue item from the sender's metadata, without re-fetching it.

    Queue-time item building normally reaches out for a title and artwork. The
    sending device already did that work and shipped the result, so repeating it
    per item is pure latency — enough of it on a Pi (~2.5s each) to time out the
    sender while the import lands anyway. The *stream* is still resolved locally
    at play time, so nothing about the trust boundary changes.
    """
    item: dict[str, object] = {
        "url": url,
        "provider": str(entry.provider or "").strip().lower() or resolver.provider_from_url(url),
        "title": entry.title,
    }
    if entry.channel:
        item["channel"] = entry.channel
    if entry.thumbnail:
        item["thumbnail"] = entry.thumbnail
    if entry.duration:
        item["duration"] = entry.duration
    return item


def _peer_import_item(entry: QueueImportItem, sender: QueueImportSender | None) -> dict:
    """Rebuild a playable item from a peer's display-safe payload.

    The sender's item is a reference, not a recipe: only the URL and a few
    display hints cross the wire, and this device re-resolves the URL with its
    own provider configuration, cookies, and quality policy. That keeps a
    peer's expiring stream URLs and provider tokens out of our queue.
    """
    url = resolver.validate_user_url(entry.url or "")
    provider = str(entry.provider or "").strip().lower()
    if provider == "upload":
        item = _peer_hosted_media_item(url, entry)
    elif entry.title and provider != "jellyfin":
        # Jellyfin is the exception: its item build resolves server-specific ids
        # and stream parameters from *this* device's configuration.
        item = _peer_display_item(url, entry)
    else:
        item = _smart_item_from_url(url, lightweight=True)
    if not isinstance(item, dict):
        raise ValueError("item could not be built")
    placeholder_metadata = bool(item.get("_metadata_lightweight"))
    for key in PEER_TEXT_HINTS:
        hint = getattr(entry, key, None)
        if hint and (placeholder_metadata or not item.get(key)):
            item[key] = hint
    for key in PEER_FILL_HINTS:
        hint = getattr(entry, key, None)
        if hint and not item.get(key):
            item[key] = hint
    if sender is not None and (sender.device_id or sender.name):
        item["peer_origin"] = {
            "device_id": str(sender.device_id or ""),
            "name": str(sender.name or ""),
        }
    return item


def _push_peer_import_toast(sender_name: str, count: int) -> None:
    label = str(sender_name or "").strip() or "Another RelayTV device"
    noun = "item" if count == 1 else "items"
    try:
        _push_overlay_toast(text=f"{label} sent {count} {noun}", level="info", icon="share")
    except Exception:
        pass


def _apply_peer_import(
    entries: list[QueueImportItem],
    *,
    mode: str,
    sender: QueueImportSender | None,
    announce: bool = True,
) -> dict:
    """Import peer items into the queue.

    ``announce`` is off for a handoff: that flow raises its own on-TV toast, and
    two notifications for one user action is noise.
    """
    results: list[dict] = []
    accepted_items: list[dict] = []
    for entry in entries:
        try:
            accepted_items.append(_peer_import_item(entry, sender))
            results.append({"url": entry.url, "title": entry.title, "accepted": True, "reason": ""})
        except HTTPException as exc:
            results.append(
                {
                    "url": entry.url,
                    "title": entry.title,
                    "accepted": False,
                    "reason": str(exc.detail or "rejected"),
                }
            )
        except Exception as exc:
            logger.warning("queue_import_item_failed error=%s", exc)
            results.append(
                {"url": entry.url, "title": entry.title, "accepted": False, "reason": "item could not be built"}
            )

    if mode == "replace":
        with state.QUEUE_LOCK:
            state.QUEUE.clear()
        state.persist_queue()

    qlen = 0
    queue_snapshot: list[object] = []
    if accepted_items:
        qlen, queue_snapshot = playback_service.queue_items(accepted_items)
    else:
        with state.QUEUE_LOCK:
            queue_snapshot = list(state.QUEUE)
            qlen = len(queue_snapshot)
        # queue_item() normally re-primes mpv's up-next; a replace that landed
        # nothing still changed the queue, so prime it here.
        try:
            player.prime_mpv_up_next_from_queue(force=True)
        except Exception:
            pass

    if accepted_items and announce:
        _push_peer_import_toast(str((sender.name if sender else "") or ""), len(accepted_items))
    _ui_event_push_queue(
        "replace" if mode == "replace" else "add",
        queue=queue_snapshot,
        queue_length=qlen,
        source="queue_import",
    )
    logger.info(
        "queue_import from=%s mode=%s received=%d accepted=%d",
        (sender.device_id if sender else "") or "unknown",
        mode,
        len(entries),
        len(accepted_items),
    )
    return {
        "status": "imported",
        "mode": mode,
        "received": len(entries),
        "accepted": len(accepted_items),
        "results": results,
        "queue_length": qlen,
        "queue": _annotate_upload_items(queue_snapshot),
        "now_playing": _annotate_upload_item(state.NOW_PLAYING),
    }


@router.post("/queue/import")
def queue_import(req: QueueImportReq):
    """Receive queue items from another RelayTV device.

    Per-item results are reported instead of failing the whole request: a peer
    can hold items this device cannot play (an unconfigured provider, a URL
    scheme we reject), and the sender needs to say so honestly rather than
    claim everything landed.
    """
    entries = list(req.items or [])
    if not entries:
        raise HTTPException(status_code=400, detail="no items to import")
    mode = str(req.mode or "append").strip().lower()
    if mode not in ("append", "replace"):
        raise HTTPException(status_code=400, detail="mode must be append or replace")
    return _apply_peer_import(entries, mode=mode, sender=req.from_device)


@router.post("/queue/handoff")
def queue_handoff(req: QueueHandoffReq):
    """Continue another device's playback here, taking its queue with it.

    The sending device stops only after this returns, so a handoff that fails
    mid-flight leaves the user watching what they were already watching. What
    was playing here is preserved to the front of the queue rather than
    discarded, so a handoff never destroys the receiver's own session.
    """
    sender = req.from_device
    try:
        item = _peer_import_item(req.now_playing, sender)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("queue_handoff_item_failed error=%s", exc)
        raise HTTPException(status_code=400, detail="item could not be built") from exc

    # Take over playback before importing the queue. If this device cannot
    # start playing, the error propagates and the sender keeps both playback and
    # its queue — importing first would leave the items stranded here as well.
    played = _play_now_from_history(
        {
            "url": str(item.get("url") or ""),
            "preserve_current": True,
            "preserve_to": "queue_front",
            "resume_current": True,
            "reason": "peer_handoff",
            "title": str(item.get("title") or "").strip() or None,
            "thumbnail": str(item.get("thumbnail") or "").strip() or None,
            "resume_pos": req.resume_pos,
        }
    )
    playing = bool(isinstance(played, dict) and played.get("status") not in ("error", "failed"))

    imported = None
    if req.items:
        imported = _apply_peer_import(list(req.items), mode="append", sender=sender, announce=False)
    if playing:
        label = str((sender.name if sender else "") or "").strip() or "Another RelayTV device"
        try:
            _push_overlay_toast(text=f"Continuing from {label}", level="info", icon="share")
        except Exception:
            pass
    with state.QUEUE_LOCK:
        queue_snapshot = list(state.QUEUE)
    logger.info(
        "queue_handoff from=%s resume_pos=%s items=%d playing=%s",
        (sender.device_id if sender else "") or "unknown",
        "none" if req.resume_pos is None else f"{float(req.resume_pos):.1f}",
        len(list(req.items or [])),
        playing,
    )
    return {
        "status": "handed_off",
        "playing": playing,
        "now_playing": _annotate_upload_item(state.NOW_PLAYING),
        "accepted": int((imported or {}).get("accepted") or 0),
        "results": list((imported or {}).get("results") or []),
        "queue_length": len(queue_snapshot),
        "queue": _annotate_upload_items(queue_snapshot),
    }


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


@router.post("/history/requeue")
def history_requeue(req: HistoryPlayReq):
    """Queue an item from history by index using the server-stored URL.

    Public history payloads carry display-safe URLs with credentials
    stripped, so clients requeue by index and the server rebuilds the
    item from its unredacted copy.
    """
    idx = int(req.index)
    with state.HISTORY_LOCK:
        if idx < 0 or idx >= len(state.HISTORY):
            raise HTTPException(status_code=400, detail="index out of range")
        it = dict(state.HISTORY[idx])
    url = it.get("url")
    if not isinstance(url, str) or not url.strip():
        raise HTTPException(status_code=400, detail="history item missing url")

    item = _smart_item_from_url(url.strip(), lightweight=True)
    if isinstance(item, dict):
        for key in ("title", "thumbnail", "thumbnail_local", "history_id"):
            val = it.get(key)
            if val and not item.get(key):
                item[key] = val
    qlen, queue_snapshot = playback_service.queue_item(item)
    try:
        _push_queue_added_toast_async(item, str(it.get("title") or url or "item"))
    except Exception:
        pass
    _ui_event_push_queue("add", queue=queue_snapshot, queue_length=qlen, source="history_requeue")
    return {
        "status": "queued",
        "item": _annotate_upload_item(item),
        "queue_length": qlen,
        "now_playing": _annotate_upload_item(state.NOW_PLAYING),
    }


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

    return {
        "status": "removed",
        "removed": _annotate_upload_item(removed),
        "queue": _annotate_upload_items(snapshot["queue"]),
        "queue_length": len(snapshot["queue"]),
    }


@router.post("/queue/play")
def queue_play(req: QueueRemoveReq):
    """Play a queued item immediately by index, preserving current playback.

    Like ``history_play``, the index refers to the server's unredacted copy:
    public queue payloads carry display-safe URLs, so a client cannot simply
    repost what it rendered. The item leaves the queue only for a play the
    server accepts — a failed play restores it at its original position,
    matching the rollback pattern of ``advance_queue_playback``.
    """
    idx = int(req.index)
    with state.QUEUE_LOCK:
        if idx < 0 or idx >= len(state.QUEUE):
            raise HTTPException(status_code=400, detail="index out of range")
        item = state.QUEUE.pop(idx)
        snapshot = {"queue": list(state.QUEUE), "saved_at": int(time.time())}
    try:
        state.persist_queue_payload(snapshot)
    except Exception as e:
        logger.warning("queue_persist_failed route=queue_play error=%s", e)

    def _restore() -> list[object]:
        with state.QUEUE_LOCK:
            state.QUEUE.insert(min(idx, len(state.QUEUE)), item)
            rollback = {"queue": list(state.QUEUE), "saved_at": int(time.time())}
        try:
            state.persist_queue_payload(rollback)
        except Exception as e:
            logger.warning("queue_persist_failed route=queue_play_restore error=%s", e)
        return list(rollback["queue"])

    if not isinstance(item, dict):
        _restore()
        raise HTTPException(status_code=400, detail="queue item is not playable")
    url = item.get("url")
    if not isinstance(url, str) or not url.strip():
        _restore()
        raise HTTPException(status_code=400, detail="queue item missing url")

    resume_pos = None
    try:
        raw_resume = item.get("resume_pos")
        resume_pos = float(raw_resume) if raw_resume is not None else None
    except Exception:
        resume_pos = None
    try:
        result = _play_now_queue_item(item, resume_pos=resume_pos)
    except Exception:
        restored = _restore()
        _ui_event_push_queue("add", queue=restored, queue_length=len(restored), source="queue_play_restore")
        raise

    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    with state.QUEUE_LOCK:
        queue_snapshot = list(state.QUEUE)
    # play_now only pushes its queue event when something was preserved or the
    # queue is non-empty; the pop must be announced either way.
    _ui_event_push_queue("remove", queue=queue_snapshot, queue_length=len(queue_snapshot), source="queue_play")
    if isinstance(result, dict):
        return {**result, "queue": _annotate_upload_items(queue_snapshot), "queue_length": len(queue_snapshot)}
    return {"status": "playing", "queue": _annotate_upload_items(queue_snapshot), "queue_length": len(queue_snapshot)}


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
    if changed:
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
        "queue": _annotate_upload_items(state.QUEUE),
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

    return {"status": "moved", "queue": _annotate_upload_items(snapshot["queue"]), "queue_length": len(snapshot["queue"])}
