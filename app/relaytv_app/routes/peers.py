# SPDX-License-Identifier: GPL-3.0-only
"""Peer device endpoints: identity, registry CRUD, reachability, and send."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import device_identity, peers, state


router = APIRouter()


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
        # Populated once mDNS browsing lands; the shape is stable now so the
        # UI can render "found nearby" without a second round of changes.
        "discovered": [],
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


@router.post("/peers/{peer_id}/send")
def peers_send(peer_id: str, req: PeerSendReq) -> dict[str, object]:
    """Send the queue (or one queue item) to a peer device."""
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
        return peers.send_queue(peer_id, items=items, mode=req.mode)
    except peers.PeerError as exc:
        raise _peer_error(exc) from exc
