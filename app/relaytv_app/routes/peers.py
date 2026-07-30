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
    index: int | None = None


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


def _remove_local_queue_index(index: int) -> int:
    from .queue import QueueRemoveReq, queue_remove

    try:
        result = queue_remove(QueueRemoveReq(index=int(index)))
    except HTTPException:
        # The item moved or vanished between send and cleanup; the transfer
        # already succeeded, so report the current length instead of failing.
        with state.QUEUE_LOCK:
            return len(state.QUEUE)
    return int(result.get("queue_length") or 0)


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
    if req.index is None:
        items: list[object] = queue
    else:
        index = int(req.index)
        if index < 0 or index >= len(queue):
            raise HTTPException(status_code=400, detail="index out of range")
        items = [queue[index]]
    try:
        result = peers.send_queue(peer_id, items=items, mode=mode)
    except peers.PeerError as exc:
        raise _peer_error(exc) from exc

    if move:
        if req.index is None:
            result["local_queue_length"] = _clear_local_queue()
        else:
            result["local_queue_length"] = _remove_local_queue_index(int(req.index))
        result["moved"] = True
    return result


@router.post("/peers/{peer_id}/handoff")
def peers_handoff(peer_id: str) -> dict[str, object]:
    """Continue what is playing here on a peer device, then stop here.

    Ordering matters: playback stops locally only once the peer reports it has
    taken over, so a failed handoff leaves this device playing.
    """
    snapshot = playback_service.handoff_snapshot()
    if snapshot is None:
        raise HTTPException(status_code=409, detail="nothing is playing to hand off")

    with state.QUEUE_LOCK:
        queue = list(state.QUEUE)
    try:
        result = peers.handoff_playback(peer_id, snapshot=snapshot, items=queue)
    except peers.PeerError as exc:
        raise _peer_error(exc) from exc

    # The queue goes first: clearing now-playing with items still queued would
    # advance into the next one instead of going idle.
    result["local_queue_length"] = _clear_local_queue()

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
