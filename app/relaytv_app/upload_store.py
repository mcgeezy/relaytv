# SPDX-License-Identifier: GPL-3.0-only
import json
import mimetypes
import os
import re
import shutil
import threading
import time
import uuid
from .config import env_float as _env_float
from pathlib import Path
from urllib.parse import unquote, urlparse

from fastapi import HTTPException

from .debug import get_logger


logger = get_logger("uploads")

_UPLOAD_LOCK = threading.Lock()
_ACTIVE_UPLOADS: set[str] = set()
_CLEANUP_WORKER_STARTED = False
_UPLOADS_ROOT = os.getenv("RELAYTV_UPLOADS_DIR", "/data/uploads").strip() or "/data/uploads"
_UPLOAD_URL_PREFIX = "/media/uploads/"
_DEFAULT_MAX_SIZE_GB = 5.0
_DEFAULT_RETENTION_HOURS = 24
_DEFAULT_PROGRESSIVE_MP4_READY_MB = 24.0
_DEFAULT_PROGRESSIVE_WEBM_READY_MB = 12.0
_DEFAULT_PROGRESSIVE_MAX_STALL_SEC = 2.0
_DEFAULT_PROGRESSIVE_MIN_THROUGHPUT_KBPS = 256.0
_ALLOWED_MIME_TYPES: dict[str, tuple[str, ...]] = {
    "application/octet-stream": (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".mp4", ".m4v", ".webm"),
    "application/ogg": (".ogg", ".opus"),
    "audio/aac": (".aac",),
    "audio/flac": (".flac",),
    "audio/m4a": (".m4a",),
    "audio/mpeg": (".mp3",),
    "audio/mp4": (".m4a", ".mp4"),
    "audio/ogg": (".ogg", ".opus"),
    "audio/opus": (".opus",),
    "audio/wav": (".wav",),
    "audio/wave": (".wav",),
    "audio/x-aac": (".aac",),
    "audio/x-flac": (".flac",),
    "audio/x-m4a": (".m4a",),
    "audio/x-wav": (".wav",),
    "video/mp4": (".mp4", ".m4v"),
    "video/webm": (".webm",),
}

_ALLOWED_UPLOAD_EXTENSIONS = tuple(sorted({ext for exts in _ALLOWED_MIME_TYPES.values() for ext in exts}))


def default_upload_settings() -> dict[str, float | int]:
    return {
        "max_size_gb": _DEFAULT_MAX_SIZE_GB,
        "retention_hours": _DEFAULT_RETENTION_HOURS,
    }


def normalize_upload_settings(value: object) -> dict[str, float | int]:
    out = default_upload_settings()
    if not isinstance(value, dict):
        return out
    try:
        max_size_gb = float(value.get("max_size_gb", out["max_size_gb"]))
    except Exception:
        max_size_gb = float(out["max_size_gb"])
    try:
        retention_hours = int(value.get("retention_hours", out["retention_hours"]))
    except Exception:
        retention_hours = int(out["retention_hours"])
    out["max_size_gb"] = max(0.25, min(500.0, round(max_size_gb, 2)))
    out["retention_hours"] = max(1, min(24 * 90, retention_hours))
    return out


def uploads_root() -> str:
    return _UPLOADS_ROOT


def register_active_upload(upload_id: str) -> None:
    """Protect an in-process upload from retention/capacity cleanup."""
    with _UPLOAD_LOCK:
        _ACTIVE_UPLOADS.add(str(upload_id or "").strip())


def unregister_active_upload(upload_id: str) -> None:
    with _UPLOAD_LOCK:
        _ACTIVE_UPLOADS.discard(str(upload_id or "").strip())


# Upload ids are generated, never supplied: new_upload_id() has produced
# "u_" + 20 hex characters since the first commit, so this is the whole format
# that has ever existed on disk and enforcing it orphans nothing.
_UPLOAD_ID_RE = re.compile(r"^u_[0-9a-f]{20}$")


def is_valid_upload_id(upload_id: object) -> bool:
    """Return True when ``upload_id`` has the generated shape."""
    return bool(_UPLOAD_ID_RE.match(str(upload_id or "").strip()))


def _is_contained(path: str, root: str) -> bool:
    """Return True when ``path`` resolves inside ``root``."""
    try:
        return os.path.commonpath([os.path.abspath(root), os.path.abspath(path)]) == os.path.abspath(root)
    except (ValueError, OSError):
        # Different drives, or a path that cannot be resolved at all.
        return False


def upload_dir(upload_id: str) -> str:
    """Return the directory holding an upload, or "" when the id is not one.

    This is the containment boundary for the whole store: metadata, session,
    media, and deletion paths are all derived from it. An id reaches here from
    a URL, where upload_ref_from_url used to decode %2F *after* splitting the
    path, so "..%2F..%2Fdata" arrived as a single component that unquoted into
    "../../data" and escaped the root. Only the filename was ever basename'd.

    "" rather than a raised exception, because every caller already treats a
    missing upload as expired (410) and a raise would turn a hostile URL into
    a 500. Derived paths must propagate the "" instead of joining onto it:
    os.path.join("", "meta.json") is a *relative* path, which would read from
    the working directory.
    """
    ident = str(upload_id or "").strip()
    if not is_valid_upload_id(ident):
        return ""
    path = os.path.join(uploads_root(), ident)
    # Belt and braces. The format check already makes traversal impossible;
    # this keeps that true if the format is ever widened.
    if not _is_contained(path, uploads_root()):
        return ""
    return path


def upload_public_path(upload_id: str, filename: str) -> str:
    return f"{_UPLOAD_URL_PREFIX}{upload_id}/{filename}"


def is_upload_url(url: object) -> bool:
    try:
        path = urlparse(str(url or "")).path or ""
    except Exception:
        return False
    return path.startswith(_UPLOAD_URL_PREFIX)


def upload_ref_from_url(url: object) -> tuple[str, str] | None:
    if not is_upload_url(url):
        return None
    try:
        path = urlparse(str(url or "")).path or ""
    except Exception:
        return None
    rel = path[len(_UPLOAD_URL_PREFIX):].strip("/")
    parts = [unquote(part) for part in rel.split("/") if part]
    if len(parts) != 2:
        return None
    upload_id, filename = parts
    if not filename:
        return None
    # unquote runs after the split, so an encoded separator survives inside a
    # single component: "..%2F..%2Fdata" decodes to "../../data" here.
    if not is_valid_upload_id(upload_id):
        return None
    return upload_id, os.path.basename(filename)


def sanitize_upload_filename(filename: object, *, content_type: str = "") -> str:
    original = os.path.basename(str(filename or "").strip()) or "upload"
    stem, ext = os.path.splitext(original)
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("._-") or "upload"
    safe_stem = safe_stem[:96]
    ext = (ext or "").strip().lower()
    allowed_exts = _ALLOWED_MIME_TYPES.get(str(content_type or "").strip().lower(), ())
    if allowed_exts:
        if ext not in allowed_exts:
            ext = allowed_exts[0]
    elif not ext:
        guessed = mimetypes.guess_extension(str(content_type or "").strip().lower()) or ""
        ext = guessed.lower()
    if ext and not ext.startswith("."):
        ext = f".{ext}"
    return f"{safe_stem}{ext}" if ext else safe_stem


def upload_limits(settings_payload: dict | None = None) -> dict[str, float | int]:
    src = settings_payload if isinstance(settings_payload, dict) else {}
    return normalize_upload_settings(src.get("uploads"))


def max_upload_bytes(settings_payload: dict | None = None) -> int:
    limits = upload_limits(settings_payload)
    return max(1, int(float(limits["max_size_gb"]) * 1024 * 1024 * 1024))


def retention_seconds(settings_payload: dict | None = None) -> int:
    limits = upload_limits(settings_payload)
    return max(3600, int(limits["retention_hours"]) * 3600)


def is_allowed_upload(content_type: object, filename: object) -> bool:
    mime = str(content_type or "").split(";", 1)[0].strip().lower()
    name = str(filename or "").strip().lower()
    if mime in _ALLOWED_MIME_TYPES:
        if not name:
            return True
        return any(name.endswith(ext) for ext in _ALLOWED_MIME_TYPES[mime])
    if name:
        return any(name.endswith(ext) for ext in _ALLOWED_UPLOAD_EXTENSIONS)
    return False


def new_upload_id() -> str:
    return f"u_{uuid.uuid4().hex[:20]}"


def metadata_path(upload_id: str) -> str:
    base = upload_dir(upload_id)
    return os.path.join(base, "meta.json") if base else ""


def session_path(upload_id: str) -> str:
    base = upload_dir(upload_id)
    return os.path.join(base, "session.json") if base else ""


def load_metadata(upload_id: str) -> dict | None:
    path = metadata_path(upload_id)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def write_metadata(upload_id: str, meta: dict) -> None:
    path = metadata_path(upload_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_session(upload_id: str) -> dict | None:
    path = session_path(upload_id)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def write_session(upload_id: str, session: dict) -> None:
    path = session_path(upload_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(session, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def progressive_ready_threshold_bytes(content_type: object) -> int | None:
    mime = str(content_type or "").split(";", 1)[0].strip().lower()
    if mime == "video/mp4":
        mb = _env_float(
            "RELAYTV_UPLOAD_PROGRESSIVE_MP4_READY_MB",
            _DEFAULT_PROGRESSIVE_MP4_READY_MB,
            minimum=1.0,
            maximum=1024.0,
        )
        return int(mb * 1024 * 1024)
    if mime == "video/webm":
        mb = _env_float(
            "RELAYTV_UPLOAD_PROGRESSIVE_WEBM_READY_MB",
            _DEFAULT_PROGRESSIVE_WEBM_READY_MB,
            minimum=1.0,
            maximum=1024.0,
        )
        return int(mb * 1024 * 1024)
    return None


def progressive_max_stall_sec() -> float:
    return _env_float(
        "RELAYTV_UPLOAD_PROGRESSIVE_MAX_STALL_SEC",
        _DEFAULT_PROGRESSIVE_MAX_STALL_SEC,
        minimum=0.25,
        maximum=30.0,
    )


def progressive_min_throughput_bps() -> float:
    kbps = _env_float(
        "RELAYTV_UPLOAD_PROGRESSIVE_MIN_THROUGHPUT_KBPS",
        _DEFAULT_PROGRESSIVE_MIN_THROUGHPUT_KBPS,
        minimum=32.0,
        maximum=1024.0 * 32.0,
    )
    return kbps * 1024.0


def new_play_session(meta: dict) -> dict:
    mime_type = str(meta.get("mime_type") or "").strip().lower()
    now = time.time()
    return {
        "id": str(meta.get("id") or "").strip(),
        "mode": "ingest_play",
        "status": "uploading",
        "mime_type": mime_type,
        "filename": str(meta.get("filename") or "").strip(),
        "public_name": str(meta.get("public_name") or "").strip(),
        "created_unix": float(meta.get("created_unix") or now),
        "last_updated_unix": now,
        "last_chunk_unix": 0.0,
        "bytes_received": 0,
        "size_bytes": 0,
        "chunk_count": 0,
        "throughput_bps": 0.0,
        "ready_threshold_bytes": int(progressive_ready_threshold_bytes(mime_type) or 0),
        "progressive_eligible": bool(progressive_ready_threshold_bytes(mime_type)),
        "progressive_probe_ok": False,
        "progressive_started": False,
        "progressive_started_unix": 0.0,
        "fallback_to_full_upload": False,
        "fallback_reason": "",
        "upload_complete": False,
        "playback_started": False,
        "playback_mode": "",
        "path": "",
    }


def mark_session_progress(
    session: dict,
    *,
    bytes_received: int,
    chunk_size: int,
    chunk_started_unix: float,
    chunk_finished_unix: float,
    path: str,
) -> dict:
    out = dict(session or {})
    previous_bytes = int(out.get("bytes_received") or 0)
    delta_bytes = max(0, int(bytes_received) - previous_bytes)
    duration = max(0.001, float(chunk_finished_unix) - float(chunk_started_unix))
    current_bps = float(delta_bytes or chunk_size or 0) / duration
    prior_bps = float(out.get("throughput_bps") or 0.0)
    out["bytes_received"] = max(0, int(bytes_received))
    out["size_bytes"] = max(0, int(bytes_received))
    out["chunk_count"] = max(0, int(out.get("chunk_count") or 0) + (1 if chunk_size > 0 else 0))
    out["last_chunk_unix"] = float(chunk_finished_unix)
    out["last_updated_unix"] = float(chunk_finished_unix)
    out["throughput_bps"] = current_bps if prior_bps <= 0.0 else ((prior_bps * 0.6) + (current_bps * 0.4))
    out["path"] = str(path or "").strip()
    return out


def mark_session_fallback(session: dict, reason: str) -> dict:
    out = dict(session or {})
    out["status"] = "fallback_full_upload"
    out["fallback_to_full_upload"] = True
    out["fallback_reason"] = str(reason or "").strip()
    out["last_updated_unix"] = time.time()
    return out


def mark_session_progressive_started(session: dict) -> dict:
    out = dict(session or {})
    now = time.time()
    out["status"] = "progressive_started"
    out["progressive_started"] = True
    out["progressive_started_unix"] = now
    out["playback_started"] = True
    out["playback_mode"] = "progressive"
    out["last_updated_unix"] = now
    return out


def mark_session_complete(session: dict) -> dict:
    out = dict(session or {})
    now = time.time()
    out["upload_complete"] = True
    if out.get("playback_started") is True:
        out["status"] = "completed"
    elif out.get("fallback_to_full_upload") is True:
        out["status"] = "fallback_full_upload"
    else:
        out["status"] = "uploaded"
    out["last_updated_unix"] = now
    return out


def mark_session_completed_playback(session: dict, *, mode: str) -> dict:
    out = dict(session or {})
    now = time.time()
    out["status"] = "playing"
    out["playback_started"] = True
    out["playback_mode"] = str(mode or "").strip() or "full_upload"
    out["last_updated_unix"] = now
    return out


def progressive_upload_health(session: dict, *, now: float | None = None) -> tuple[bool, str]:
    current = float(time.time() if now is None else now)
    last_chunk = float(session.get("last_chunk_unix") or 0.0)
    if last_chunk <= 0.0:
        return False, "waiting_for_upload"
    if (current - last_chunk) > progressive_max_stall_sec():
        return False, "upload_stalled"
    throughput_bps = float(session.get("throughput_bps") or 0.0)
    if throughput_bps > 0.0 and throughput_bps < progressive_min_throughput_bps():
        return False, "upload_slow"
    if int(session.get("chunk_count") or 0) < 2:
        return False, "warming_up"
    return True, ""


def progressive_probe_ready(path: str, *, content_type: str, size_bytes: int | None = None) -> bool:
    mime = str(content_type or "").split(";", 1)[0].strip().lower()
    try:
        with open(path, "rb") as fh:
            head = fh.read(4 * 1024 * 1024)
    except Exception:
        return False
    if not head:
        return False
    if mime == "video/mp4":
        if b"ftyp" not in head[:128]:
            return False
        return b"moov" in head
    if mime == "video/webm":
        return head.startswith(b"\x1A\x45\xDF\xA3")
    return False


def progressive_start_ready(meta: dict, session: dict) -> tuple[bool, str]:
    if not isinstance(meta, dict) or not isinstance(session, dict):
        return False, "missing_session"
    if session.get("progressive_started") is True:
        return False, "already_started"
    if session.get("fallback_to_full_upload") is True:
        return False, str(session.get("fallback_reason") or "fallback_full_upload")
    threshold = int(session.get("ready_threshold_bytes") or 0)
    if threshold <= 0:
        return False, "mime_not_supported"
    size_bytes = int(session.get("bytes_received") or 0)
    if size_bytes < threshold:
        return False, "buffering"
    healthy, reason = progressive_upload_health(session)
    if not healthy:
        return False, reason
    path = str(session.get("path") or stored_file_path(meta) or "").strip()
    if not path or not os.path.exists(path):
        return False, "file_missing"
    if not progressive_probe_ready(path, content_type=str(meta.get("mime_type") or ""), size_bytes=size_bytes):
        return False, "probe_failed"
    return True, ""


def stored_file_path(meta: dict) -> str | None:
    upload_id = str(meta.get("id") or "").strip()
    stored_name = os.path.basename(str(meta.get("stored_name") or "").strip())
    if not upload_id or not stored_name:
        return None
    base = upload_dir(upload_id)
    if not base:
        return None
    return os.path.join(base, stored_name)


def media_exists(upload_id: str) -> bool:
    meta = load_metadata(upload_id)
    if not isinstance(meta, dict):
        return False
    path = stored_file_path(meta)
    return bool(path) and os.path.exists(path)


def build_item(meta: dict, *, absolute_url: str | None = None) -> dict:
    upload_id = str(meta.get("id") or "").strip()
    public_name = os.path.basename(str(meta.get("public_name") or meta.get("filename") or "").strip())
    url = str(absolute_url or upload_public_path(upload_id, public_name))
    path = stored_file_path(meta)
    available = bool(path) and os.path.exists(path)
    item = {
        "url": url,
        "provider": "upload",
        "title": str(meta.get("title") or meta.get("filename") or public_name or url).strip() or url,
        "upload_id": upload_id,
        "upload_filename": str(meta.get("filename") or public_name or "").strip(),
        "mime_type": str(meta.get("mime_type") or "").strip(),
        "size_bytes": int(meta.get("size_bytes") or 0),
        "available": available,
    }
    return item


def item_from_url(url: str) -> dict:
    ref = upload_ref_from_url(url)
    if ref is None:
        raise HTTPException(status_code=400, detail="invalid upload url")
    upload_id, _ = ref
    meta = load_metadata(upload_id)
    if not isinstance(meta, dict):
        raise HTTPException(status_code=410, detail="Uploaded media expired or removed")
    item = build_item(meta, absolute_url=url)
    if not item.get("available"):
        raise HTTPException(status_code=410, detail="Uploaded media expired or removed")
    return item


def annotate_item(item: object) -> object:
    if not isinstance(item, dict):
        return item
    if item.get("peer_hosted"):
        # Media hosted by another RelayTV device: the URL is upload-shaped but
        # the file lives on that device, so resolving it against this device's
        # upload store would wrongly report it as expired.
        return item
    url = str(item.get("url") or "").strip()
    upload_id = str(item.get("upload_id") or "").strip()
    if not upload_id and not is_upload_url(url):
        return item
    meta = None
    if upload_id:
        meta = load_metadata(upload_id)
    elif is_upload_url(url):
        ref = upload_ref_from_url(url)
        if ref is not None:
            meta = load_metadata(ref[0])
    if not isinstance(meta, dict):
        out = dict(item)
        out["provider"] = "upload"
        out["available"] = False
        return out
    out = build_item(meta, absolute_url=url or None)
    for key, value in item.items():
        if key not in out:
            out[key] = value
    return out


def list_upload_metadata() -> list[dict]:
    root = Path(uploads_root())
    if not root.exists():
        return []
    out: list[dict] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        meta = load_metadata(child.name)
        if not isinstance(meta, dict):
            continue
        file_path = stored_file_path(meta)
        size_bytes = 0
        if file_path and os.path.exists(file_path):
            try:
                size_bytes = int(os.path.getsize(file_path))
            except Exception:
                size_bytes = int(meta.get("size_bytes") or 0)
        else:
            size_bytes = int(meta.get("size_bytes") or 0)
        created_unix = float(meta.get("created_unix") or 0.0)
        out.append({
            **meta,
            "size_bytes": size_bytes,
            "created_unix": created_unix,
            "available": bool(file_path) and os.path.exists(file_path),
        })
    out.sort(key=lambda m: float(m.get("created_unix") or 0.0))
    return out


def delete_upload(upload_id: str) -> None:
    path = upload_dir(upload_id)
    if not path:
        # Never hand an unvalidated id to rmtree. Reached today only with
        # server-generated ids and ids read back from meta.json, but the
        # operation is recursive deletion and the guard costs nothing.
        logger.warning("upload_delete_rejected id=%r", str(upload_id)[:64])
        return
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        return
    except Exception as exc:
        logger.warning("upload_delete_failed id=%s error=%s", upload_id, exc)


def _prune_missing_upload_refs() -> dict[str, int]:
    try:
        from . import state
    except Exception:
        return {"queue_removed": 0}
    removed = 0
    snapshot = None
    with state.QUEUE_LOCK:
        updated: list[dict] = []
        for item in list(state.QUEUE):
            annotated = annotate_item(item)
            if isinstance(annotated, dict) and annotated.get("provider") == "upload" and (annotated.get("available") is False):
                removed += 1
                continue
            updated.append(item)
        if removed:
            state.QUEUE[:] = updated
            snapshot = {"queue": list(state.QUEUE), "saved_at": int(time.time())}
    if snapshot is not None:
        try:
            state.persist_queue_payload(snapshot)
        except Exception as exc:
            logger.warning("upload_queue_prune_persist_failed removed=%d error=%s", removed, exc)
    return {"queue_removed": removed}


def cleanup_uploads(settings_payload: dict | None = None) -> dict[str, int]:
    with _UPLOAD_LOCK:
        os.makedirs(uploads_root(), exist_ok=True)
        max_bytes = max_upload_bytes(settings_payload)
        expire_before = time.time() - retention_seconds(settings_payload)
        metas = list_upload_metadata()
        deleted = 0
        total_bytes = sum(int(meta.get("size_bytes") or 0) for meta in metas if meta.get("available"))
        for meta in list(metas):
            upload_id = str(meta.get("id") or "").strip()
            if upload_id in _ACTIVE_UPLOADS:
                continue
            created_unix = float(meta.get("created_unix") or 0.0)
            available = bool(meta.get("available"))
            expired = (created_unix > 0.0) and (created_unix < expire_before)
            if (not available) or expired:
                delete_upload(upload_id)
                deleted += 1
                if available:
                    total_bytes -= int(meta.get("size_bytes") or 0)
                metas.remove(meta)
        if total_bytes > max_bytes:
            for meta in list(metas):
                if total_bytes <= max_bytes:
                    break
                upload_id = str(meta.get("id") or "").strip()
                if not upload_id:
                    continue
                if upload_id in _ACTIVE_UPLOADS:
                    continue
                delete_upload(upload_id)
                deleted += 1
                total_bytes -= int(meta.get("size_bytes") or 0)
                metas.remove(meta)
        pruned = _prune_missing_upload_refs()
    return {
        "deleted_uploads": deleted,
        "queue_removed": int(pruned.get("queue_removed") or 0),
    }


def start_cleanup_worker() -> None:
    global _CLEANUP_WORKER_STARTED
    with _UPLOAD_LOCK:
        if _CLEANUP_WORKER_STARTED:
            return
        _CLEANUP_WORKER_STARTED = True

    def _loop() -> None:
        while True:
            try:
                from . import state
                settings_snapshot = state.get_settings() if hasattr(state, "get_settings") else {}
                cleanup_uploads(settings_snapshot)
            except Exception as exc:
                logger.warning("upload_cleanup_worker_failed error=%s", exc)
            time.sleep(900)

    thread = threading.Thread(target=_loop, name="relaytv-upload-cleanup", daemon=True)
    thread.start()
