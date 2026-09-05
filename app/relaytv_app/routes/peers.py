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
    queue_id: str | None = None
    queue_ids: list[str] | None = None


class PeerHandoffReq(BaseModel):
    indexes: list[int] | None = None
    queue_ids: list[str] | None = None
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


def _remove_local_queue_items(items: list[object]) -> int:
    """Drop accepted queue instances atomically, ignoring any already gone."""
    from .queue import _ui_event_push_queue

    accepted_ids = {state.queue_item_id(item) for item in items if state.queue_item_id(item)}
    removed, snapshot = playback_service.remove_queue_items_by_id(accepted_ids)
    if removed:
        _ui_event_push_queue("remove", queue=snapshot, queue_length=len(snapshot), source="peer_transfer")
    return len(snapshot)


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


def _selected_queue_items(
    queue: list[object],
    *,
    index: int | None,
    indexes: list[int] | None,
    queue_id: str | None = None,
    queue_ids: list[str] | None = None,
) -> list[object]:
    """Select stable queue instances, with indexes retained for old callers."""
    if queue_ids is not None or queue_id:
        chosen = list(queue_ids) if queue_ids is not None else [str(queue_id or "")]
        wanted = [str(value or "").strip().lower() for value in chosen if str(value or "").strip()]
        by_id = {state.queue_item_id(item): item for item in queue if state.queue_item_id(item)}
        if any(item_id not in by_id for item_id in wanted):
            raise HTTPException(status_code=409, detail="queue changed; refresh and retry")
        return [by_id[item_id] for item_id in dict.fromkeys(wanted)]
    selection = _selected_indexes(len(queue), index=index, indexes=indexes)
    return queue if selection is None else [queue[position] for position in selection]


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
        state.ensure_queue_item_ids(state.QUEUE)
        queue = list(state.QUEUE)
    items = _selected_queue_items(
        queue,
        index=req.index,
        indexes=req.indexes,
        queue_id=req.queue_id,
        queue_ids=req.queue_ids,
    )
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
        state.ensure_queue_item_ids(state.QUEUE)
        queue = list(state.QUEUE)
    items = _selected_queue_items(
        queue,
        index=None,
        indexes=request.indexes,
        queue_ids=request.queue_ids,
    )
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

    from .playback import _idle_visual_surface_enabled_for_player

    stopped = False
    try:
        # Not /close: that keeps a resumable session, which after a handoff
        # would leave this device showing the item it just gave away and
        # offering to resume it — playing the same thing in two rooms. The
        # session moved, so it is cleared here.
        stopped = playback_service.complete_peer_handoff(
            snapshot, idle_surface_enabled=_idle_visual_surface_enabled_for_player(),
        )
    except Exception as exc:  # pragma: no cover - local teardown is best effort
        logger.warning("peer_handoff_local_stop_failed error=%s", exc)
    result["local_stopped"] = stopped
    return result
