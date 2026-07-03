# SPDX-License-Identifier: GPL-3.0-only
import os
import tempfile
import time

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from .. import player, state, upload_store
from ..debug import get_logger


router = APIRouter()
logger = get_logger("routes.uploads")


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


def _playback_notification_display_sec() -> float:
    from . import _playback_notification_display_sec as playback_notification_display_sec

    return playback_notification_display_sec()


def _push_overlay_toast(**kwargs) -> None:
    from . import _push_overlay_toast as push_overlay_toast

    push_overlay_toast(**kwargs)


def _smart_item_from_url(url: str, *, lightweight: bool = False) -> dict:
    from . import _smart_item_from_url as smart_item_from_url

    if lightweight:
        return smart_item_from_url(url, lightweight=True)
    return smart_item_from_url(url)


def _push_queue_added_toast_async(item: object, fallback_label: str) -> None:
    from . import _push_queue_added_toast_async as push_queue_added_toast_async

    push_queue_added_toast_async(item, fallback_label)


def _ui_event_push_queue(
    action: str,
    queue: list[object] | None = None,
    queue_length: int | None = None,
    source: str = "api",
) -> None:
    from . import _ui_event_push_queue as push_queue_event

    push_queue_event(action, queue=queue, queue_length=queue_length, source=source)


def _uploaded_media_title(title: str | None, filename: str, public_name: str) -> str:
    return str(title or "").strip() or os.path.basename(filename or public_name) or public_name


def _uploaded_media_meta(upload_id: str, *, filename: str, public_name: str, title: str, content_type: str, size_bytes: int = 0) -> dict:
    return {
        "id": upload_id,
        "filename": os.path.basename(filename or public_name),
        "public_name": public_name,
        "stored_name": public_name,
        "title": title,
        "mime_type": content_type,
        "size_bytes": int(size_bytes),
        "created_unix": float(time.time()),
    }


def _progressive_fallback_toast() -> None:
    try:
        _push_overlay_toast(
            text="Upload still in progress. Waiting for full file for reliable playback.",
            duration=_playback_notification_display_sec(),
            level="warn",
            icon="clock",
        )
    except Exception:
        pass


async def _play_uploaded_item(item: dict, *, mode: str) -> dict:
    return await run_in_threadpool(player.play_item, item, False, False, False, mode)


def _enqueue_uploaded_media_url(url: str) -> dict:
    try:
        item = _smart_item_from_url(url or "", lightweight=True)
    except TypeError:
        item = _smart_item_from_url(url or "")
    with state.QUEUE_LOCK:
        state.QUEUE.append(item)
        qlen = len(state.QUEUE)
        queue_snapshot = list(state.QUEUE)
    state.persist_queue()
    try:
        player.prefetch_queue_item_stream(item)
    except Exception:
        pass
    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    try:
        _push_queue_added_toast_async(item, url or "item")
    except Exception:
        pass
    _ui_event_push_queue("add", queue=queue_snapshot, queue_length=qlen, source="ingest_media_enqueue")
    return {"status": "queued", "item": item, "queue_length": qlen, "now_playing": state.NOW_PLAYING}


@router.post("/ingest/media")
async def ingest_media(request: Request, file: UploadFile = File(...), title: str | None = Form(None)):
    settings_snapshot = state.get_settings() if hasattr(state, "get_settings") else {}
    upload_store.cleanup_uploads(settings_snapshot)
    filename = str(getattr(file, "filename", "") or "").strip()
    content_type = str(getattr(file, "content_type", "") or "").split(";", 1)[0].strip().lower()
    if not upload_store.is_allowed_upload(content_type, filename):
        raise HTTPException(
            status_code=400,
            detail="Unsupported media type; upload video/mp4, video/webm, audio/mpeg, or audio/mp4",
        )

    max_bytes = upload_store.max_upload_bytes(settings_snapshot)
    upload_id = upload_store.new_upload_id()
    public_name = upload_store.sanitize_upload_filename(filename, content_type=content_type)
    target_dir = upload_store.upload_dir(upload_id)
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="relaytv-upload-", suffix=".part", dir=target_dir)
    os.close(fd)
    final_path = os.path.join(target_dir, public_name)
    size_bytes = 0
    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise HTTPException(status_code=413, detail="Uploaded media exceeds configured storage size limit")
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if size_bytes <= 0:
            raise HTTPException(status_code=400, detail="Uploaded media is empty")
        os.replace(tmp_path, final_path)
        media_title = str(title or "").strip() or os.path.basename(filename or public_name) or public_name
        meta = {
            "id": upload_id,
            "filename": os.path.basename(filename or public_name),
            "public_name": public_name,
            "stored_name": public_name,
            "title": media_title,
            "mime_type": content_type,
            "size_bytes": int(size_bytes),
            "created_unix": float(time.time()),
        }
        upload_store.write_metadata(upload_id, meta)
        media_url = str(request.url_for("get_uploaded_media", upload_id=upload_id, filename=public_name))
        item = upload_store.build_item(meta, absolute_url=media_url)
        cleanup_result = upload_store.cleanup_uploads(settings_snapshot)
        return {
            "ok": True,
            "media_id": upload_id,
            "media_path": upload_store.upload_public_path(upload_id, public_name),
            "url": media_url,
            "item": item,
            "cleanup": cleanup_result,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("media_ingest_failed filename=%s error=%s", filename, exc)
        raise HTTPException(status_code=500, detail="Failed storing uploaded media") from exc
    finally:
        try:
            await file.close()
        except Exception:
            pass
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


@router.post("/ingest/media/enqueue")
async def ingest_media_enqueue(request: Request, file: UploadFile = File(...), title: str | None = Form(None)):
    created = await ingest_media(request, file=file, title=title)
    media_url = str(created.get("url") or "").strip() if isinstance(created, dict) else ""
    if not media_url:
        raise HTTPException(status_code=500, detail="Uploaded media missing playback URL")
    result = _enqueue_uploaded_media_url(media_url)
    out = dict(created)
    out.update({"action": "enqueue", "result": result})
    return out


@router.post("/ingest/media/play")
async def ingest_media_play(request: Request, file: UploadFile = File(...), title: str | None = Form(None)):
    settings_snapshot = state.get_settings() if hasattr(state, "get_settings") else {}
    upload_store.cleanup_uploads(settings_snapshot)
    filename = str(getattr(file, "filename", "") or "").strip()
    content_type = str(getattr(file, "content_type", "") or "").split(";", 1)[0].strip().lower()
    if not upload_store.is_allowed_upload(content_type, filename):
        raise HTTPException(
            status_code=400,
            detail="Unsupported media type; upload video/mp4, video/webm, audio/mpeg, or audio/mp4",
        )

    max_bytes = upload_store.max_upload_bytes(settings_snapshot)
    upload_id = upload_store.new_upload_id()
    public_name = upload_store.sanitize_upload_filename(filename, content_type=content_type)
    media_title = _uploaded_media_title(title, filename, public_name)
    meta = _uploaded_media_meta(
        upload_id,
        filename=filename,
        public_name=public_name,
        title=media_title,
        content_type=content_type,
        size_bytes=0,
    )
    upload_store.write_metadata(upload_id, meta)

    media_url = str(request.url_for("get_uploaded_media", upload_id=upload_id, filename=public_name))
    target_dir = upload_store.upload_dir(upload_id)
    os.makedirs(target_dir, exist_ok=True)
    final_path = os.path.join(target_dir, public_name)
    size_bytes = 0
    progressive_started = False
    fallback_reason = ""
    fallback_toast_sent = False
    playback_mode = "full_upload"
    now_playing = None
    stored_ok = False
    session = upload_store.new_play_session(meta)
    session["path"] = final_path
    upload_store.write_session(upload_id, session)
    try:
        with open(final_path, "wb") as out:
            while True:
                chunk_started = time.time()
                chunk = await file.read(1024 * 1024)
                chunk_finished = time.time()
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise HTTPException(status_code=413, detail="Uploaded media exceeds configured storage size limit")
                out.write(chunk)
                out.flush()
                os.fsync(out.fileno())

                session = upload_store.mark_session_progress(
                    session,
                    bytes_received=size_bytes,
                    chunk_size=len(chunk),
                    chunk_started_unix=chunk_started,
                    chunk_finished_unix=chunk_finished,
                    path=final_path,
                )
                upload_store.write_session(upload_id, session)

                if progressive_started or session.get("fallback_to_full_upload") is True:
                    continue

                ready, reason = upload_store.progressive_start_ready(meta, session)
                if ready:
                    item = upload_store.build_item(meta, absolute_url=media_url)
                    item["_local_stream_path"] = final_path
                    try:
                        now_playing = await _play_uploaded_item(item, mode="ingest_media_play")
                        progressive_started = True
                        playback_mode = "progressive"
                        session = upload_store.mark_session_progressive_started(session)
                        upload_store.write_session(upload_id, session)
                    except Exception as exc:
                        logger.warning("progressive_upload_start_failed id=%s error=%s", upload_id, exc)
                        fallback_reason = "start_failed"
                        session = upload_store.mark_session_fallback(session, fallback_reason)
                        upload_store.write_session(upload_id, session)
                elif reason not in ("buffering", "warming_up", "waiting_for_upload"):
                    fallback_reason = str(reason or "").strip() or "fallback_full_upload"
                    session = upload_store.mark_session_fallback(session, fallback_reason)
                    upload_store.write_session(upload_id, session)
                    _progressive_fallback_toast()
                    fallback_toast_sent = True

            out.flush()
            os.fsync(out.fileno())

        if size_bytes <= 0:
            raise HTTPException(status_code=400, detail="Uploaded media is empty")

        meta["size_bytes"] = int(size_bytes)
        upload_store.write_metadata(upload_id, meta)
        session = upload_store.mark_session_complete(session)
        upload_store.write_session(upload_id, session)

        item = upload_store.build_item(meta, absolute_url=media_url)
        if not progressive_started:
            if session.get("fallback_to_full_upload") is True and not fallback_toast_sent:
                _progressive_fallback_toast()
                fallback_toast_sent = True
            item["_local_stream_path"] = final_path
            now_playing = await _play_uploaded_item(item, mode="ingest_media_play")
            playback_mode = "full_upload"
            session = upload_store.mark_session_completed_playback(session, mode=playback_mode)
            upload_store.write_session(upload_id, session)

        cleanup_result = upload_store.cleanup_uploads(settings_snapshot)
        stored_ok = True
        return {
            "ok": True,
            "media_id": upload_id,
            "media_path": upload_store.upload_public_path(upload_id, public_name),
            "url": media_url,
            "item": upload_store.build_item(meta, absolute_url=media_url),
            "now_playing": now_playing,
            "playback_mode": playback_mode,
            "fallback_reason": fallback_reason,
            "cleanup": cleanup_result,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("media_ingest_play_failed filename=%s error=%s", filename, exc)
        raise HTTPException(status_code=500, detail="Failed storing uploaded media for playback") from exc
    finally:
        try:
            await file.close()
        except Exception:
            pass
        if not stored_ok:
            try:
                upload_store.delete_upload(upload_id)
            except Exception:
                pass
