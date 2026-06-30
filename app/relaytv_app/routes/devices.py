# SPDX-License-Identifier: GPL-3.0-only
from fastapi import APIRouter

from .. import devices


router = APIRouter()


@router.get("/devices")
def get_devices():
    return devices.discover()
