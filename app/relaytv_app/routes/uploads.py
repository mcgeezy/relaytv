# SPDX-License-Identifier: GPL-3.0-only
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import upload_store


router = APIRouter()


@router.get("/media/uploads/{upload_id}/{filename}", name="get_uploaded_media")
def get_uploaded_media(upload_id: str, filename: str):
    meta = upload_store.load_metadata(upload_id)
    if not isinstance(meta, dict):
        raise HTTPException(status_code=410, detail="Uploaded media expired or removed")
    expected_name = os.path.basename(str(meta.get("public_name") or meta.get("filename") or "").strip())
    if os.path.basename(filename) != expected_name:
        raise HTTPException(status_code=404, detail="Uploaded media not found")
    path = upload_store.stored_file_path(meta)
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=410, detail="Uploaded media expired or removed")
    return FileResponse(
        path,
        media_type=str(meta.get("mime_type") or "application/octet-stream"),
        filename=str(meta.get("filename") or expected_name),
        headers={"Cache-Control": "private, max-age=60"},
    )
