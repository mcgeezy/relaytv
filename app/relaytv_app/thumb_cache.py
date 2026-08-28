# SPDX-License-Identifier: GPL-3.0-only
import os
import hashlib
import threading
import tempfile
import subprocess
import queue
import time
import shutil
import urllib.request
from urllib.parse import urlparse


def _read_max_total_bytes() -> int:
    raw_mb = (os.getenv("RELAYTV_THUMB_MAX_TOTAL_MB") or "").strip()
    if raw_mb:
        try:
            return max(0, int(float(raw_mb) * 1024 * 1024))
        except Exception:
            pass
    raw_bytes = (os.getenv("RELAYTV_THUMB_MAX_TOTAL_BYTES") or "").strip()
    if raw_bytes:
        try:
            return max(0, int(float(raw_bytes)))
        except Exception:
            pass
    return 100 * 1024 * 1024


# Where to store normalized thumbnails (persisted via ./data:/data)
THUMB_DIR = os.getenv("RELAYTV_THUMB_DIR") or os.getenv("BRAVECAST_THUMB_DIR", "/data/thumbs")
THUMB_WIDTH = int(os.getenv("RELAYTV_THUMB_WIDTH") or os.getenv("BRAVECAST_THUMB_WIDTH", "480"))
THUMB_JPEG_Q = int(os.getenv("RELAYTV_THUMB_JPEG_Q") or os.getenv("BRAVECAST_THUMB_JPEG_Q", "5"))  # ffmpeg qscale (2=best, 31=worst)
THUMB_MAX_BYTES = int(os.getenv("RELAYTV_THUMB_MAX_BYTES") or os.getenv("BRAVECAST_THUMB_MAX_BYTES", str(2 * 1024 * 1024)))  # raw download cap
THUMB_MAX_FILES = max(1, int(os.getenv("RELAYTV_THUMB_MAX_FILES") or "2000"))
THUMB_MAX_TOTAL_BYTES = _read_max_total_bytes()
THUMB_RETENTION_SEC = max(0, int(os.getenv("RELAYTV_THUMB_RETENTION_SEC") or str(14 * 24 * 3600)))
THUMB_PRUNE_INTERVAL_SEC = max(0, int(os.getenv("RELAYTV_THUMB_PRUNE_INTERVAL_SEC") or "120"))
THUMB_SRC_MAP_MAX = max(1, int(os.getenv("RELAYTV_THUMB_SRC_MAP_MAX") or "4096"))

# The work queue was unbounded and had no de-duplication, so every /status
# poll re-enqueued the same ids for any thumbnail that had not landed yet: a
# history page of items whose provider was down grew the queue without limit
# and re-requested the same failing URLs forever.
THUMB_QUEUE_MAX = max(16, int(os.getenv("RELAYTV_THUMB_QUEUE_MAX") or "512"))
# How long a failed thumbnail is left alone before it may be retried.
THUMB_FAILURE_BACKOFF_SEC = max(0, int(os.getenv("RELAYTV_THUMB_FAILURE_BACKOFF_SEC") or "900"))
THUMB_FAILURE_MAP_MAX = max(1, int(os.getenv("RELAYTV_THUMB_FAILURE_MAP_MAX") or "2048"))

_Q: "queue.Queue[tuple[str, str]]" = queue.Queue(maxsize=THUMB_QUEUE_MAX)
_STARTED = False
_LOCK = threading.Lock()
_PRUNE_LOCK = threading.Lock()
_SRC_BY_ID: dict[str, str] = {}
_LAST_PRUNE_TS = 0.0
# Ids queued or being fetched right now, and ids whose last fetch failed.
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: set[str] = set()
_FAILED_AT: dict[str, float] = {}


def _claim_thumb(tid: str) -> bool:
    """Return True when this caller should enqueue ``tid``.

    False when it is already queued or in flight, or when its last attempt
    failed recently enough that retrying would just hammer a dead provider.
    """
    now = time.time()
    with _INFLIGHT_LOCK:
        if tid in _INFLIGHT:
            return False
        failed_at = _FAILED_AT.get(tid)
        if failed_at is not None and (now - failed_at) < THUMB_FAILURE_BACKOFF_SEC:
            return False
        _INFLIGHT.add(tid)
        return True


def _release_thumb(tid: str, *, failed: bool) -> None:
    now = time.time()
    with _INFLIGHT_LOCK:
        _INFLIGHT.discard(tid)
        if failed:
            if len(_FAILED_AT) >= THUMB_FAILURE_MAP_MAX:
                # Drop the oldest failures rather than growing without bound.
                for stale, _ts in sorted(_FAILED_AT.items(), key=lambda kv: kv[1])[:64]:
                    _FAILED_AT.pop(stale, None)
            _FAILED_AT[tid] = now
        else:
            _FAILED_AT.pop(tid, None)


def reset_thumb_tracking_for_tests() -> None:
    with _INFLIGHT_LOCK:
        _INFLIGHT.clear()
        _FAILED_AT.clear()

def _ensure_dir() -> None:
    try:
        os.makedirs(THUMB_DIR, exist_ok=True)
    except Exception:
        pass


def _safe_rm(path: str) -> None:
    try:
        os.remove(path)
    except Exception:
        pass


def _touch(path: str) -> None:
    try:
        os.utime(path, None)
    except Exception:
        pass


def _remember_src(tid: str, src: str) -> None:
    _SRC_BY_ID[tid] = src
    overflow = len(_SRC_BY_ID) - THUMB_SRC_MAP_MAX
    if overflow > 0:
        # dict preserves insertion order; trim oldest ids first.
        for k in list(_SRC_BY_ID.keys())[:overflow]:
            _SRC_BY_ID.pop(k, None)


def _commit_file(src: str, dst: str) -> bool:
    try:
        os.replace(src, dst)
        return os.path.exists(dst) and os.path.getsize(dst) > 0
    except Exception:
        pass
    try:
        shutil.copyfile(src, dst)
        try:
            os.remove(src)
        except Exception:
            pass
        return os.path.exists(dst) and os.path.getsize(dst) > 0
    except Exception:
        return False


def _prune_thumb_dir(*, force: bool = False) -> None:
    global _LAST_PRUNE_TS
    now = time.time()
    with _PRUNE_LOCK:
        if not force and THUMB_PRUNE_INTERVAL_SEC > 0 and (now - _LAST_PRUNE_TS) < THUMB_PRUNE_INTERVAL_SEC:
            return
        _LAST_PRUNE_TS = now

    _ensure_dir()
    try:
        entries: list[tuple[str, float, int]] = []
        with os.scandir(THUMB_DIR) as it:
            for ent in it:
                if not ent.is_file():
                    continue
                if not ent.name.endswith(".jpg"):
                    continue
                try:
                    st = ent.stat()
                except Exception:
                    continue
                entries.append((ent.path, float(st.st_mtime), int(st.st_size)))
    except Exception:
        return

    if THUMB_RETENTION_SEC > 0:
        cutoff = now - THUMB_RETENTION_SEC
        for path, mtime, _size in entries:
            if mtime < cutoff:
                _safe_rm(path)
        entries = [(p, m, s) for (p, m, s) in entries if m >= cutoff and os.path.exists(p)]

    if THUMB_MAX_FILES > 0 and len(entries) > THUMB_MAX_FILES:
        entries.sort(key=lambda x: x[1], reverse=True)  # newest first
        for path, _mtime, _size in entries[THUMB_MAX_FILES:]:
            _safe_rm(path)
        entries = entries[:THUMB_MAX_FILES]

    if THUMB_MAX_TOTAL_BYTES > 0 and entries:
        # `st_mtime` is touched on local use, so this approximates oldest-accessed-first eviction.
        entries.sort(key=lambda x: x[1])  # oldest first for eviction
        total = sum(max(0, int(s)) for _p, _m, s in entries)
        if total > THUMB_MAX_TOTAL_BYTES:
            for path, _mtime, size in entries:
                _safe_rm(path)
                total -= max(0, int(size))
                if total <= THUMB_MAX_TOTAL_BYTES:
                    break

def thumb_id(url: str) -> str:
    h = hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()
    return h[:20]

def local_rel_path(tid: str) -> str:
    return f"/thumbs/{tid}.jpg"

def local_abs_path(tid: str) -> str:
    return os.path.join(THUMB_DIR, f"{tid}.jpg")

def _headers_for(url: str) -> dict[str, str]:
    # Some CDNs behave better with a browser-y UA and a referer.
    headers: dict[str, str] = {
        "User-Agent": "Mozilla/5.0 (compatible; RelayTV/1.0)",
        "Accept": "image/*,*/*;q=0.8",
    }
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("ytimg.com"):
        headers["Referer"] = "https://www.youtube.com/"
    elif host.endswith("bitchute.com"):
        headers["Referer"] = "https://www.bitchute.com/"
    elif host.endswith("rumble.com") or host.endswith("rumblecdn.com") or host.endswith("1a-1791.com"):
        headers["Referer"] = "https://rumble.com/"
    return headers

def _download_to(url: str, fp: str) -> bool:
    req = urllib.request.Request(url, headers=_headers_for(url))
    with urllib.request.urlopen(req, timeout=15) as r:
        total = 0
        with open(fp, "wb") as f:
            while True:
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > THUMB_MAX_BYTES:
                    return False
                f.write(chunk)
    return True

def _normalize_to_jpg(src_fp: str, dst_fp: str) -> bool:
    # Use ffmpeg to normalize any input image type to a consistent jpg size.
    # -vf scale=WIDTH:-2 preserves aspect ratio and ensures even height.
    w = max(64, int(THUMB_WIDTH))
    q = max(2, min(31, int(THUMB_JPEG_Q)))
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", src_fp,
        "-vf", f"scale={w}:-2",
        "-q:v", str(q),
        dst_fp
    ]
    p = subprocess.run(cmd, capture_output=True)
    return p.returncode == 0 and os.path.exists(dst_fp) and os.path.getsize(dst_fp) > 0

def _worker() -> None:
    _ensure_dir()
    while True:
        url, tid = _Q.get()
        failed = True
        try:
            dst = local_abs_path(tid)
            if os.path.exists(dst) and os.path.getsize(dst) > 0:
                _touch(dst)
                _prune_thumb_dir()
                failed = False
                continue
            with tempfile.TemporaryDirectory() as td:
                raw_fp = os.path.join(td, "in")
                ok = _download_to(url, raw_fp)
                if not ok:
                    # Stays failed: the backoff is what stops a dead provider
                    # from being re-requested on every status poll.
                    continue
                tmp_out = os.path.join(td, "out.jpg")
                if _normalize_to_jpg(raw_fp, tmp_out):
                    committed = _commit_file(tmp_out, dst)
                else:
                    # Fallback: persist original bytes (may not be jpg but better than dropping).
                    committed = _commit_file(raw_fp, dst)
                if not committed:
                    # A full/read-only disk is a failed thumbnail attempt too;
                    # clearing backoff here hammers storage on every status poll.
                    continue
                _touch(dst)
                _prune_thumb_dir()
                failed = False
        except Exception:
            pass
        finally:
            _release_thumb(tid, failed=failed)
            try:
                _Q.task_done()
            except Exception:
                pass

def start_worker() -> None:
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
    _prune_thumb_dir(force=True)

def attach_local_thumbnail(item: dict) -> dict:
    """Best-effort local thumbnail caching.

    - Always enqueues generation when a remote thumbnail is present.
    - Only exposes `thumbnail_local` once the normalized file exists, so callers won't
      hammer /thumbs/*.jpg with 404s.
    """
    try:
        thumb = item.get("thumbnail")
        if not isinstance(thumb, str) or not thumb.strip():
            return item

        start_worker()
        tid = thumb_id(thumb.strip())
        dst = local_abs_path(tid)
        # Remember source URL for best-effort synchronous materialization fallback.
        _remember_src(tid, thumb.strip())

        # Enqueue generation if missing, once.
        if not (os.path.exists(dst) and os.path.getsize(dst) > 0):
            if _claim_thumb(tid):
                try:
                    _Q.put_nowait((thumb.strip(), tid))
                except queue.Full:
                    # Saturated: drop this request rather than grow. The next
                    # poll re-offers it, so nothing is permanently lost.
                    _release_thumb(tid, failed=False)
                except Exception:
                    _release_thumb(tid, failed=False)
            return item  # don't advertise local until ready

        _touch(dst)
        item["thumbnail_local"] = local_rel_path(tid)
    except Exception:
        pass
    return item


def get_thumb_src(thumb_id: str) -> str | None:
    """Return the original source URL for a cached thumbnail id, if known."""
    return _SRC_BY_ID.get(thumb_id)

def thumb_path_for_id(thumb_id: str) -> str:
    return os.path.join(THUMB_DIR, f"{thumb_id}.jpg")

def ensure_cached_sync(thumb_id: str, timeout_s: float = 3.0) -> bool:
    """Best-effort: ensure the given thumbnail exists on disk.

    If it's already present, returns True. If we know the source URL, we download/normalize it
    synchronously (used by the /thumbs endpoint when HA requests a file that's not ready yet).
    """
    p = thumb_path_for_id(thumb_id)
    if os.path.exists(p):
        _touch(p)
        _prune_thumb_dir()
        return True
    src = get_thumb_src(thumb_id)
    if not src:
        return False
    # Try a direct, synchronous download + normalize (no queue) so first request can succeed.
    try:
        _ensure_dir()
        with tempfile.TemporaryDirectory() as td:
            raw_fp = os.path.join(td, "in")
            if not _download_to(src, raw_fp):
                return False
            out_fp = os.path.join(td, "out.jpg")
            if _normalize_to_jpg(raw_fp, out_fp):
                _commit_file(out_fp, p)
            else:
                _commit_file(raw_fp, p)
        _touch(p)
        _prune_thumb_dir()
        return os.path.exists(p) and os.path.getsize(p) > 0
    except Exception:
        return False
