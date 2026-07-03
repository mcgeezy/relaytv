# SPDX-License-Identifier: GPL-3.0-only
from fastapi import APIRouter

from .. import discovery_mdns, player, state


router = APIRouter()


@router.get("/discovery/status")
def discovery_status():
    return {"mdns": discovery_mdns.status()}


@router.get("/tv/status")
def tv_status():
    tv = state.get_tv_state() if hasattr(state, "get_tv_state") else {}
    cec_controller = player.cec_controller_status() if hasattr(player, "cec_controller_status") else {}
    return {
        "tv_power_state": tv.get("tv_power_status"),
        "active_source_phys_addr": tv.get("active_source_phys_addr"),
        "last_cec_event_ts": tv.get("last_event_ts"),
        "last_cec_event": tv.get("last_event"),
        "tv_control_method": tv.get("control_method", "cec-client"),
        "cec_controller": cec_controller,
        "confidence": tv.get("confidence", {}),
    }
