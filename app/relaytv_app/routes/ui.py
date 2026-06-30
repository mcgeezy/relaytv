# SPDX-License-Identifier: GPL-3.0-only
from fastapi import APIRouter
from fastapi.responses import RedirectResponse


router = APIRouter()


@router.get("/")
def root():
    return RedirectResponse(url="/ui")
