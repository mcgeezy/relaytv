# SPDX-License-Identifier: GPL-3.0-only
from fastapi import APIRouter


router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    # Keep the health payload intentionally minimal so basic liveness checks and
    # smoke tests can rely on a stable response contract.
    return {"ok": True}
