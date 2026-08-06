# SPDX-License-Identifier: GPL-3.0-only
"""Peer device endpoints: identity, registry CRUD, reachability, and send."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import device_identity, discovery_mdns, peers, playback_service, state
from ..debug import get_logger


router = APIRouter()
logger = get_logger("routes.peers")


class PeerCreateReq(BaseModel):
    base_url: str
    name: str = ""
    token: str = ""
    verify: bool = True


class PeerPatchReq(BaseModel):
    name: str | None = None
    base_url: str | None = None
    token: str | None = None


class PeerSendReq(BaseModel):
    mode: str = "append"
    # ``index`` predates per-item selection and is kept for companion apps.
    index: int | None = None
    indexes: list[int] | None = None


class PeerHandoffReq(BaseModel):
    indexes: list[int] | None = None
    # A handoff that keeps the local session is the "Copy" gesture: the peer
    # starts playing, this device carries on. Defaulting to False keeps the
    # original meaning of the endpoint for callers that send no body.
    keep_local: bool = False


def _peer_error(exc: peers.PeerError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/peers/identity")
def peers_identity() -> dict[str, str]:
    """Identify this device to other RelayTV devices.

    Deliberately anonymous and read-only: a peer must be able to confirm what
    it is talking to before any token is exchanged. Carries no secrets.
    """
    return device_identity.identity_payload()


@router.get("/peers")
def peers_list() -> dict[str, object]:
    return {
        "device": device_identity.identity_payload(),
        "peers": peers.list_peers(),
        "discovered": peers.discovered_candidates(),
        "discovery": discovery_mdns.browse_status(),
    }


@router.post("/peers")
def peers_add(req: PeerCreateReq) -> dict[str, object]:
    try:
        peer = peers.add_peer(
            base_url=req.base_url,
            name=req.name,
            token=req.token,
            source="manual",
            verify=bool(req.verify),
        )
    except peers.PeerError as exc:
        raise _peer_error(exc) from exc
    return {"status": "added", "peer": peer}


@router.post("/peers/probe")
def peers_probe(req: PeerCreateReq) -> dict[str, object]:
    """Test an address before saving it, so bad entries never persist."""
    try:
        identity = peers.probe_identity(req.base_url, token=req.token)
    except peers.PeerError as exc:
        return {"online": False, "error": exc.message}
    return {
        "online": True,
        "error": "",
        "device_id": identity["device_id"],
        "device_name": identity["device_name"],
        "version": identity["version"],
        "is_self": identity["device_id"] == device_identity.device_id(),
    }


@router.patch("/peers/{peer_id}")
def peers_update(peer_id: str, req: PeerPatchReq) -> dict[str, object]:
    try:
        peer = peers.update_peer(peer_id, name=req.name, base_url=req.base_url, token=req.token)
    except peers.PeerError as exc:
        raise _peer_error(exc) from exc
    return {"status": "updated", "peer": peer}


@router.delete("/peers/{peer_id}")
def peers_remove(peer_id: str) -> dict[str, object]:
    if not peers.remove_peer(peer_id):
        raise HTTPException(status_code=404, detail="unknown device")
    return {"status": "removed", "peers": peers.list_peers()}


@router.post("/peers/{peer_id}/probe")
def peers_probe_saved(peer_id: str) -> dict[str, object]:
    try:
        return peers.probe_peer(peer_id)
    except peers.PeerError as exc:
        raise _peer_error(exc) from exc


def _clear_local_queue() -> int:
    """Drop the local queue after a confirmed transfer.

    Delegates to the queue routes rather than touching ``state.QUEUE`` here:
    queue writers are pinned by tests/test_transition_inventory.py, and those
    functions already persist, re-prime mpv's up-next, and emit the UI event.
    """
    from .queue import clear as clear_queue

    clear_queue()
    with state.QUEUE_LOCK:
        return len(state.QUEUE)


def _queue_index_of(target: object) -> int | None:
    """Locate an item in the live queue. Call with ``state.QUEUE_LOCK`` held."""
    for index, item in enumerate(state.QUEUE):
        if item is target:
            return index
    # Persisting and reloading the queue replaces the objects, so identity can
    # legitimately miss; the URL is the next best handle on the same item.
    url = str(target.get("url") or "") if isinstance(target, dict) else ""
    if not url:
        return None
    for index, item in enumerate(state.QUEUE):
        if isinstance(item, dict) and str(item.get("url") or "") == url:
            return index
    return None


def _remove_local_queue_items(items: list[object]) -> int:
    """Drop exactly the items the peer took, as the queue stands right now.

    Positions from before the send are deliberately not reused. A send can take
    tens of seconds, and auto-next or another client may have shifted the queue
    in the meantime — reusing an index would delete whichever item had moved
    into that slot, destroying something that was never sent. Matching the item
    itself also leaves behind anything the peer rejected and anything that
    could not travel at all, so giving up ownership never loses more than it
    handed over.
    """
    targets = list(items or [])
    if not targets:
        with state.QUEUE_LOCK:
            return len(state.QUEUE)

    with state.QUEUE_LOCK:
        current = list(state.QUEUE)
    # The whole queue going at once is the common case; clearing keeps it to a
    # single persist, re-prime, and UI event instead of one per item.
    if current and len(targets) >= len(current):
        if all(any(item is target for target in targets) for item in current):
            return _clear_local_queue()

    from .queue import QueueRemoveReq, queue_remove

    for target in targets:
        with state.QUEUE_LOCK:
            index = _queue_index_of(target)
        if index is None:
            # Already gone: played out, or removed by someone else mid-transfer.
            continue
        try:
            queue_remove(QueueRemoveReq(index=index))
        except HTTPException:
            continue
    with state.QUEUE_LOCK:
        return len(state.QUEUE)


def _selected_indexes(queue_length: int, *, index: int | None, indexes: list[int] | None) -> list[int] | None:
    """Resolve a selection to sorted queue indexes, or None for the whole queue.

    An explicit empty list is a real answer ("send nothing from the queue"),
    which is why it is distinguished from the absent selection that means all.
    """
    if indexes is None and index is None:
        return None
    chosen = list(indexes) if indexes is not None else [int(index or 0)]
    resolved: list[int] = []
    for value in chosen:
        position = int(value)
        if position < 0 or position >= queue_length:
            raise HTTPException(status_code=400, detail="index out of range")
        resolved.append(position)
    return sorted(set(resolved))


@router.post("/peers/{peer_id}/send")
def peers_send(peer_id: str, req: PeerSendReq) -> dict[str, object]:
    """Send the queue (or one queue item) to a peer device.

    ``move`` gives up local ownership, so it is deliberately two-phase: the
    local queue is only touched after the peer confirms the import. A transfer
    that fails in transit must not lose the queue.
    """
    mode = str(req.mode or "append").strip().lower()
    move = mode == "move"
    if move:
        mode = "append"

    with state.QUEUE_LOCK:
        queue = list(state.QUEUE)
    selection = _selected_indexes(len(queue), index=req.index, indexes=req.indexes)
    items: list[object] = queue if selection is None else [queue[i] for i in selection]
    if not items:
        raise HTTPException(status_code=400, detail="nothing selected to send")
    try:
        result, accepted = peers.send_queue(peer_id, items=items, mode=mode)
    except peers.PeerError as exc:
        raise _peer_error(exc) from exc

    if move:
        result["local_queue_length"] = _remove_local_queue_items(accepted)
        result["moved"] = True
    return result


@router.post("/peers/{peer_id}/handoff")
def peers_handoff(peer_id: str, req: PeerHandoffReq | None = None) -> dict[str, object]:
    """Continue what is playing here on a peer device, then stop here.

    Ordering matters: playback stops locally only once the peer reports it has
    taken over, so a failed handoff leaves this device playing.

    With ``keep_local`` the teardown is skipped entirely and both devices play
    the same thing. That is a deliberate user gesture ("Copy"), not the default:
    an unasked-for second room playing along would be a surprise, so the plain
    handoff still moves the session rather than duplicating it.
    """
    request = req or PeerHandoffReq()
    snapshot = playback_service.handoff_snapshot()
    if snapshot is None:
        raise HTTPException(status_code=409, detail="nothing is playing to hand off")

    with state.QUEUE_LOCK:
        queue = list(state.QUEUE)
    selection = _selected_indexes(len(queue), index=None, indexes=request.indexes)
    items: list[object] = queue if selection is None else [queue[i] for i in selection]
    try:
        result, accepted = peers.handoff_playback(peer_id, snapshot=snapshot, items=items)
    except peers.PeerError as exc:
        raise _peer_error(exc) from exc

    result["kept_local"] = bool(request.keep_local)
    if request.keep_local:
        with state.QUEUE_LOCK:
            result["local_queue_length"] = len(state.QUEUE)
        result["local_stopped"] = False
        return result

    # The queue goes first: clearing now-playing with items still queued would
    # advance into the next one instead of going idle. With a partial selection
    # that is exactly right — this device continues into whatever it kept.
    result["local_queue_length"] = _remove_local_queue_items(accepted)

    from .playback import clear_now_playing

    stopped = False
    try:
        # Not /close: that keeps a resumable session, which after a handoff
        # would leave this device showing the item it just gave away and
        # offering to resume it — playing the same thing in two rooms. The
        # session moved, so it is cleared here.
        clear_now_playing()
        # A handoff leaves this device available rather than deliberately
        # parked, so the long auto-next hold that clearing applies is dropped.
        playback_service.clear_auto_next_suppression()
        stopped = True
    except Exception as exc:  # pragma: no cover - local teardown is best effort
        logger.warning("peer_handoff_local_stop_failed error=%s", exc)
    result["local_stopped"] = stopped
    return result
