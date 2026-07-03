# SPDX-License-Identifier: GPL-3.0-only
from fastapi import APIRouter


router = APIRouter()


def _notification_capabilities() -> dict:
    from . import _notification_capabilities as notification_capabilities

    return notification_capabilities()


def _runtime_capabilities() -> dict:
    from . import _runtime_capabilities as runtime_capabilities

    return runtime_capabilities()


@router.get("/notifications/capabilities")
def notifications_capabilities():
    return _notification_capabilities()


@router.get("/runtime/capabilities")
def runtime_capabilities():
    return _runtime_capabilities()
