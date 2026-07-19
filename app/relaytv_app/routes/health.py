# SPDX-License-Identifier: GPL-3.0-only
from fastapi import APIRouter

from .. import api_auth


router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    # Keep the health payload intentionally minimal so basic liveness checks and
    # smoke tests can rely on a stable response contract.
    return {"ok": True}


@router.post("/auth/check")
def auth_check() -> dict[str, bool]:
    """Validate write authorization without changing server state."""
    return {"ok": True, "token_required": bool(api_auth.configured_api_token())}
