# SPDX-License-Identifier: GPL-3.0-only
import os
import json
import threading
import time
import re
import platform
import tempfile
from .resolver import provider_from_url
from .debug import get_logger


logger = get_logger("state")

# =========================
# Persistent state (queue + history)
# =========================

STATE_DIR = os.getenv("RELAYTV_STATE_DIR") or os.getenv("BRAVECAST_STATE_DIR", "/data")
QUEUE_STATE_FILE = os.getenv("RELAYTV_QUEUE_FILE") or os.getenv("BRAVECAST_QUEUE_FILE", "queue.json")
HISTORY_STATE_FILE = os.getenv("RELAYTV_HISTORY_FILE") or os.getenv("BRAVECAST_HISTORY_FILE", "history.json")
SESSION_STATE_FILE = os.getenv("RELAYTV_SESSION_FILE") or os.getenv("BRAVECAST_SESSION_FILE", "session.json")
SETTINGS_STATE_FILE = os.getenv("RELAYTV_SETTINGS_FILE") or os.getenv("BRAVECAST_SETTINGS_FILE", "settings.json")
HISTORY_LIMIT = int(os.getenv("RELAYTV_HISTORY_LIMIT") or os.getenv("BRAVECAST_HISTORY_LIMIT", "200"))


def _default_ytdlp_format() -> str:
    """Default to provider auto-selection unless explicitly overridden."""
    env_fmt = (os.getenv("YTDLP_FORMAT") or "").strip()
    if env_fmt:
        return env_fmt

    # Raspberry Pi / arm64 defaults to the arm-safe AVC-first profile with a
    # 1080p cap unless explicitly overridden.
    arch = (platform.machine() or "").lower()
    if arch in ("aarch64", "arm64"):
        arm_cap = (os.getenv("RELAYTV_ARM_DEFAULT_QUALITY") or "1080").strip()
        if arm_cap in {"360", "480", "720", "1080"}:
            return f"best[height<={arm_cap}][fps<=30][vcodec^=avc1]/best[height<={arm_cap}][fps<=30]/best[height<={arm_cap}]/best"
        return "best[height<=1080][fps<=30][vcodec^=avc1]/best[height<=1080][fps<=30]/best[height<=1080]/best"

    # Universal default for unknown providers: avoid AV1-heavy picks while
    # still allowing split streams where that is the only good choice.
    return "bestvideo[vcodec!*=av01][height<=1080][fps<=60]+bestaudio/best[vcodec!*=av01]/best"


def _normalize_ytdlp_format(v: str | None) -> str:
    """Normalize UI shorthand values into concrete yt-dlp format expressions."""
    s = (v or "").strip()
    # Common operator error from env paste: "YTDLP_FORMAT=<expr>"
    if s.upper().startswith("YTDLP_FORMAT="):
        s = s.split("=", 1)[1].strip()
    # Trim accidental wrapping quotes.
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        s = s[1:-1].strip()

    raw = s.lower()
    if not raw:
        return _default_ytdlp_format()
    if raw == "worst":
        return "worst"
    if raw in {"360", "480", "720", "1080"}:
        # Prefer capped non-AV1 split streams with a progressive fallback.
        # This keeps resolution selections consistent with provider defaults.
        return f"bestvideo[vcodec!*=av01][height<={raw}][fps<=60]+bestaudio/best[height<={raw}][fps<=60]/best"
    # Ensure a broad fallback exists so providers with odd format catalogs still play.
    out = s
    if "/best" not in out:
        out = f"{out}/best"
    return out


def _normalize_quality_mode(v: str | None) -> str:
    s = str(v or "").strip().lower()
    if s in ("auto", "auto_profile", "profile"):
        return "auto_profile"
    if s == "manual":
        return "manual"
    auto_env = _env_bool("RELAYTV_AUTO_STREAM_PROFILE", True)
    return "auto_profile" if auto_env else "manual"


def _normalize_quality_cap(v: object) -> str:
    raw = str(v or "").strip().lower()
    if not raw or raw == "auto":
        return ""
    if raw == "worst":
        return ""
    try:
        cap = int(float(raw))
    except Exception:
        return ""
    if cap not in (360, 480, 720, 1080, 1440, 2160):
        return ""
    return str(cap)


def _normalize_jellyfin_playback_mode(v: object) -> str:
    s = str(v or "").strip().lower()
    if s in ("direct", "transcode", "auto"):
        return s
    return "auto"


def _normalize_invidious_base(v: object) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    s = s.rstrip("/")
    if not re.match(r"^https?://", s, flags=re.IGNORECASE):
        return ""
    return s


def _normalize_volume(v, default: float = 100.0) -> float:
    """Clamp persisted/default volume to mpv's expected 0-200 range."""
    try:
        out = float(v)
    except Exception:
        return float(default)
    if out < 0.0:
        return 0.0
    if out > 200.0:
        return 200.0
    return out


def _normalize_idle_qr_size(v: object, default: int = 168) -> int:
    """Clamp idle QR code size (CSS pixels)."""
    try:
        out = int(float(v))
    except Exception:
        out = int(default)
    if out < 96:
        return 96
    if out > 280:
        return 280
    return out


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _ensure_state_dir() -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
    except Exception:
        # If we can't create it, we'll fall back to in-memory only.
        pass


def _state_path(name: str) -> str:
    return os.path.join(STATE_DIR, name)


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _atomic_write_json(path: str, obj) -> None:
    """Best-effort atomic write (same filesystem)."""
    tmp = None
    try:
        _ensure_state_dir()
        # Use a per-write unique temp file so concurrent writers don't stomp one
        # another and race on a shared "*.tmp" path.
        fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=os.path.dirname(path) or None)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        logger.warning("state_write_failed path=%s error=%s", path, e)
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except Exception:
                pass

def _is_safe_thumb_filename(fn: str) -> bool:
    if not fn:
        return False
    if "/" in fn or "\\" in fn or ".." in fn:
        return False
    if len(fn) > 200:
        return False
    return re.fullmatch(r"[A-Za-z0-9_.-]+", fn) is not None

def _sanitize_thumb_for_persist(item: dict):
    """Return a safe thumbnail reference to persist (or None)."""
    try:
        th = (item or {}).get("thumbnail")
    except Exception:
        return None
    return _sanitize_thumb_ref(th)


def _sanitize_thumb_ref(th: object) -> str | None:
    """Return a safe thumbnail URL/path reference for persisted state."""
    if not th:
        return None
    th = str(th)

    if th.startswith("http://") or th.startswith("https://"):
        return th

    if th.startswith("/thumbs/"):
        fn = th[len("/thumbs/"):]
        return f"/thumbs/{fn}" if _is_safe_thumb_filename(fn) else None

    # bare filename -> normalize
    return f"/thumbs/{th}" if _is_safe_thumb_filename(th) else None


def _persistable_queue_item(item: dict) -> dict | None:
    """Normalize a queue entry for disk persistence."""
    if not isinstance(item, dict):
        return None
    u = item.get("url")
    if not isinstance(u, str) or not u.strip():
        return None
    url = u.strip()
    out: dict[str, object] = {
        "url": url,
        "title": (
            item.get("title")
            if isinstance(item.get("title"), str) and str(item.get("title")).strip()
            else url
        ),
        "provider": (
            item.get("provider")
            if isinstance(item.get("provider"), str) and str(item.get("provider")).strip()
            else provider_from_url(url)
        ),
    }

    for key in (
        "channel",
        "subtitle",
        "jellyfin_item_id",
        "jellyfin_media_source_id",
        "series_name",
        "type",
        "audio_language",
        "subtitle_language",
    ):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()

    for key in ("season_number", "episode_number", "year"):
        val = item.get(key)
        if val is None:
            continue
        try:
            out[key] = int(val)
        except Exception:
            pass

    rp = item.get("resume_pos")
    if rp is not None:
        try:
            rp_f = float(rp)
            if rp_f >= 0:
                out["resume_pos"] = rp_f
        except Exception:
            pass

    thumb = _sanitize_thumb_for_persist(item)
    if thumb:
        out["thumbnail"] = thumb
    thumb_local = _sanitize_thumb_ref(item.get("thumbnail_local"))
    if thumb_local:
        out["thumbnail_local"] = thumb_local

    return out


def _load_persisted_queue_item(item: dict) -> dict | None:
    """Normalize a persisted queue entry from disk (legacy + rich formats)."""
    out = _persistable_queue_item(item)
    if not isinstance(out, dict):
        return None
    return out


def _persistable_history_item(item: dict) -> dict | None:
    """Normalize a history entry for disk persistence."""
    base = _persistable_queue_item(item)
    if not isinstance(base, dict):
        return None

    out = dict(base)
    ts = item.get("ts")
    if ts is not None:
        try:
            out["ts"] = int(ts)
        except Exception:
            pass
    mode = item.get("mode")
    if isinstance(mode, str) and mode.strip():
        out["mode"] = mode.strip()
    return out


def _load_persisted_history_item(item: dict) -> dict | None:
    """Normalize a persisted history entry from disk."""
    out = _persistable_history_item(item)
    if not isinstance(out, dict):
        return None
    return out


# =========================
# Queue + Smart behavior
# =========================

QUEUE: list[dict] = []  # each item: {"url":..., "title":..., "provider":...}
QUEUE_LOCK = threading.Lock()
NOW_PLAYING: dict | None = None  # {"input","url","title","provider","stream","audio","started","mode"}

# Lightweight session state (Phase 1 UX)
SESSION_STATE: str = "idle"  # idle|playing|paused|closed
SESSION_POSITION: float | None = None  # last known position (sec) for resume
SESSION_PAUSE_REASON: str | None = None  # user|tv_standby|input_changed

# Explicit playback runtime-state diagnostics.
PLAYBACK_RUNTIME_STATE: str = "idle"  # idle|buffering|playing|paused|closed|degraded
PLAYBACK_RUNTIME_STATE_REASON: str = "startup_default"
PLAYBACK_RUNTIME_PREVIOUS_STATE: str = ""
PLAYBACK_RUNTIME_PREVIOUS_REASON: str = ""
PLAYBACK_RUNTIME_STATE_SINCE_UNIX: float = time.time()
PLAYBACK_RUNTIME_LAST_TRANSITION_UNIX: float = PLAYBACK_RUNTIME_STATE_SINCE_UNIX
PLAYBACK_RUNTIME_LAST_FAILURE_CLASS: str = ""
PLAYBACK_RUNTIME_LAST_FAILURE_UNIX: float = 0.0
PLAYBACK_RUNTIME_LAST_RECOVERY_ACTION: str = ""
PLAYBACK_RUNTIME_LAST_RECOVERY_UNIX: float = 0.0

# Explicit overlay delivery-state diagnostics (runtime-only; not persisted).
OVERLAY_DELIVERY_STATE: str = "disconnected"  # headless|disconnected|connected|displaying|stale|retrying|draining
OVERLAY_DELIVERY_STATE_REASON: str = "startup_default"
OVERLAY_DELIVERY_PREVIOUS_STATE: str = ""
OVERLAY_DELIVERY_PREVIOUS_REASON: str = ""
OVERLAY_DELIVERY_STATE_SINCE_UNIX: float = time.time()
OVERLAY_DELIVERY_LAST_TRANSITION_UNIX: float = OVERLAY_DELIVERY_STATE_SINCE_UNIX
OVERLAY_DELIVERY_LAST_FAILURE_CLASS: str = ""
OVERLAY_DELIVERY_LAST_FAILURE_UNIX: float = 0.0
OVERLAY_DELIVERY_LAST_RECOVERY_ACTION: str = ""
OVERLAY_DELIVERY_LAST_RECOVERY_UNIX: float = 0.0
OVERLAY_DELIVERY_LAST_CLIENT_EVENT: str = ""
OVERLAY_DELIVERY_LAST_CLIENT_REASON: str = ""
OVERLAY_DELIVERY_LAST_CLIENT_EVENT_UNIX: float = 0.0

TV_STATE_LOCK = threading.Lock()
TV_STATE: dict = {
    "active_source_phys_addr": None,
    "tv_power_status": None,
    "last_event_ts": 0,
    "last_event": None,
    "control_method": "cec-client",
    "confidence": {
        "active_source_phys_addr": 0.0,
        "tv_power_status": 0.0,
    },
}


# History entries (most recent first)
HISTORY: list[dict] = []
HISTORY_LOCK = threading.Lock()

ADVANCE_LOCK = threading.Lock()
AUTO_NEXT_SUPPRESS_UNTIL = 0.0  # epoch seconds; auto-next ignored until this time


def _persist_queue_payload(payload: dict) -> None:
    path = _state_path(QUEUE_STATE_FILE)
    _atomic_write_json(path, payload)


def _persist_history_payload(payload: dict) -> None:
    path = _state_path(HISTORY_STATE_FILE)
    _atomic_write_json(path, payload)


def _persist_session_payload(payload: dict) -> None:
    path = _state_path(SESSION_STATE_FILE)
    _atomic_write_json(path, payload)

def persist_session() -> None:
    payload = {
        "session_state": SESSION_STATE,
        "pause_reason": SESSION_PAUSE_REASON,
        "session_position": SESSION_POSITION,
        "now_playing": NOW_PLAYING,
        "playback_runtime": {
            "state": PLAYBACK_RUNTIME_STATE,
            "reason": PLAYBACK_RUNTIME_STATE_REASON,
            "previous_state": PLAYBACK_RUNTIME_PREVIOUS_STATE,
            "previous_reason": PLAYBACK_RUNTIME_PREVIOUS_REASON,
            "state_since_unix": PLAYBACK_RUNTIME_STATE_SINCE_UNIX,
            "last_transition_unix": PLAYBACK_RUNTIME_LAST_TRANSITION_UNIX,
            "last_failure_class": PLAYBACK_RUNTIME_LAST_FAILURE_CLASS,
            "last_failure_unix": PLAYBACK_RUNTIME_LAST_FAILURE_UNIX,
            "last_recovery_action": PLAYBACK_RUNTIME_LAST_RECOVERY_ACTION,
            "last_recovery_unix": PLAYBACK_RUNTIME_LAST_RECOVERY_UNIX,
        },
        "saved_at": int(time.time()),
    }
    _persist_session_payload(payload)


def _persist_queue() -> None:
    """Persist current queue to disk (safe to call anywhere)."""
    with QUEUE_LOCK:
        queue = []
        for it in list(QUEUE):
            normalized = _persistable_queue_item(it)
            if isinstance(normalized, dict):
                queue.append(normalized)
        payload = {"queue": queue, "saved_at": int(time.time())}
    _persist_queue_payload(payload)


def _persist_history() -> None:
    """Persist current history to disk (safe to call anywhere)."""
    with HISTORY_LOCK:
        history = []
        for it in list(HISTORY):
            normalized = _persistable_history_item(it)
            if isinstance(normalized, dict):
                history.append(normalized)
        payload = {"history": history, "saved_at": int(time.time())}
    _persist_history_payload(payload)

def _history_add(entry: dict) -> None:
    with HISTORY_LOCK:
        HISTORY.insert(0, entry)
        if HISTORY_LIMIT > 0 and len(HISTORY) > HISTORY_LIMIT:
            del HISTORY[HISTORY_LIMIT:]
    _persist_history()


def _load_persisted_state() -> None:
    """Load queue/history from disk (best-effort)."""
    qpath = _state_path(QUEUE_STATE_FILE)
    hpath = _state_path(HISTORY_STATE_FILE)

    qdata = _load_json(qpath, {})
    if isinstance(qdata, dict) and isinstance(qdata.get("queue"), list):
        with QUEUE_LOCK:
            loaded = []
            for it in qdata.get("queue"):
                if not isinstance(it, dict):
                    continue
                normalized = _load_persisted_queue_item(it)
                if isinstance(normalized, dict):
                    loaded.append(normalized)
            QUEUE[:] = loaded

    hdata = _load_json(hpath, {})
    if isinstance(hdata, dict) and isinstance(hdata.get("history"), list):
        with HISTORY_LOCK:
            loaded_h = []
            for it in hdata.get("history"):
                if not isinstance(it, dict):
                    continue
                normalized = _load_persisted_history_item(it)
                if isinstance(normalized, dict):
                    loaded_h.append(normalized)
            HISTORY[:] = loaded_h[: max(0, HISTORY_LIMIT) or len(loaded_h)]


def _load_persisted_session() -> None:
    """Load persisted close/resume session state (best-effort)."""
    global SESSION_STATE, SESSION_POSITION, NOW_PLAYING, SESSION_PAUSE_REASON
    global PLAYBACK_RUNTIME_STATE, PLAYBACK_RUNTIME_STATE_REASON
    global PLAYBACK_RUNTIME_PREVIOUS_STATE, PLAYBACK_RUNTIME_PREVIOUS_REASON
    global PLAYBACK_RUNTIME_STATE_SINCE_UNIX, PLAYBACK_RUNTIME_LAST_TRANSITION_UNIX
    global PLAYBACK_RUNTIME_LAST_FAILURE_CLASS, PLAYBACK_RUNTIME_LAST_FAILURE_UNIX
    global PLAYBACK_RUNTIME_LAST_RECOVERY_ACTION, PLAYBACK_RUNTIME_LAST_RECOVERY_UNIX
    spath = _state_path(SESSION_STATE_FILE)
    sdata = _load_json(spath, {})
    if not isinstance(sdata, dict):
        return
    try:
        ss = sdata.get("session_state")
        if isinstance(ss, str) and ss:
            SESSION_STATE = ss
        pr = sdata.get("pause_reason")
        SESSION_PAUSE_REASON = pr if isinstance(pr, str) and pr else None
        sp = sdata.get("session_position")
        SESSION_POSITION = float(sp) if sp is not None else None
        np = sdata.get("now_playing")
        NOW_PLAYING = np if isinstance(np, dict) else None
        pr = sdata.get("playback_runtime")
        if isinstance(pr, dict):
            state_val = pr.get("state")
            if isinstance(state_val, str) and state_val.strip():
                PLAYBACK_RUNTIME_STATE = state_val.strip()
            reason_val = pr.get("reason")
            if isinstance(reason_val, str):
                PLAYBACK_RUNTIME_STATE_REASON = reason_val.strip()
            prev_state_val = pr.get("previous_state")
            if isinstance(prev_state_val, str):
                PLAYBACK_RUNTIME_PREVIOUS_STATE = prev_state_val.strip()
            prev_reason_val = pr.get("previous_reason")
            if isinstance(prev_reason_val, str):
                PLAYBACK_RUNTIME_PREVIOUS_REASON = prev_reason_val.strip()
            try:
                PLAYBACK_RUNTIME_STATE_SINCE_UNIX = float(pr.get("state_since_unix") or PLAYBACK_RUNTIME_STATE_SINCE_UNIX)
            except Exception:
                pass
            try:
                PLAYBACK_RUNTIME_LAST_TRANSITION_UNIX = float(pr.get("last_transition_unix") or PLAYBACK_RUNTIME_LAST_TRANSITION_UNIX)
            except Exception:
                pass
            failure_class_val = pr.get("last_failure_class")
            if isinstance(failure_class_val, str):
                PLAYBACK_RUNTIME_LAST_FAILURE_CLASS = failure_class_val.strip()
            try:
                PLAYBACK_RUNTIME_LAST_FAILURE_UNIX = float(pr.get("last_failure_unix") or PLAYBACK_RUNTIME_LAST_FAILURE_UNIX)
            except Exception:
                pass
            recovery_action_val = pr.get("last_recovery_action")
            if isinstance(recovery_action_val, str):
                PLAYBACK_RUNTIME_LAST_RECOVERY_ACTION = recovery_action_val.strip()
            try:
                PLAYBACK_RUNTIME_LAST_RECOVERY_UNIX = float(pr.get("last_recovery_unix") or PLAYBACK_RUNTIME_LAST_RECOVERY_UNIX)
            except Exception:
                pass
    except Exception:
        pass


def persist_queue_payload(payload: dict) -> None:
    """Public wrapper (modules shouldn't rely on underscored helpers)."""
    return _persist_queue_payload(payload)

def persist_history_payload(payload: dict) -> None:
    return _persist_history_payload(payload)

def persist_queue() -> None:
    return _persist_queue()

def persist_history() -> None:
    return _persist_history()


def set_session_state(val: str) -> None:
    global SESSION_STATE
    SESSION_STATE = val
    persist_session()


def set_pause_reason(reason: str | None) -> None:
    global SESSION_PAUSE_REASON
    SESSION_PAUSE_REASON = reason
    persist_session()


def get_pause_reason() -> str | None:
    return SESSION_PAUSE_REASON


def set_session_position(pos: float | None) -> None:
    global SESSION_POSITION
    SESSION_POSITION = pos
    persist_session()


def set_now_playing(now: dict | None) -> None:
    global NOW_PLAYING
    NOW_PLAYING = now
    persist_session()


def get_now_playing() -> dict | None:
    return NOW_PLAYING


def update_playback_runtime_state(next_state: str, reason: str = "") -> dict:
    global PLAYBACK_RUNTIME_STATE, PLAYBACK_RUNTIME_STATE_REASON
    global PLAYBACK_RUNTIME_PREVIOUS_STATE, PLAYBACK_RUNTIME_PREVIOUS_REASON
    global PLAYBACK_RUNTIME_STATE_SINCE_UNIX, PLAYBACK_RUNTIME_LAST_TRANSITION_UNIX
    global PLAYBACK_RUNTIME_LAST_FAILURE_CLASS, PLAYBACK_RUNTIME_LAST_FAILURE_UNIX
    global PLAYBACK_RUNTIME_LAST_RECOVERY_ACTION, PLAYBACK_RUNTIME_LAST_RECOVERY_UNIX

    state_val = str(next_state or "idle").strip().lower() or "idle"
    reason_val = str(reason or "").strip().lower()
    now_ts = time.time()
    previous_state = PLAYBACK_RUNTIME_STATE
    changed = (state_val != PLAYBACK_RUNTIME_STATE or reason_val != PLAYBACK_RUNTIME_STATE_REASON)
    if changed:
        PLAYBACK_RUNTIME_PREVIOUS_STATE = PLAYBACK_RUNTIME_STATE
        PLAYBACK_RUNTIME_PREVIOUS_REASON = PLAYBACK_RUNTIME_STATE_REASON
        PLAYBACK_RUNTIME_STATE = state_val
        PLAYBACK_RUNTIME_STATE_REASON = reason_val
        PLAYBACK_RUNTIME_STATE_SINCE_UNIX = now_ts
        PLAYBACK_RUNTIME_LAST_TRANSITION_UNIX = now_ts
        if state_val == "degraded":
            PLAYBACK_RUNTIME_LAST_FAILURE_CLASS = reason_val or "degraded"
            PLAYBACK_RUNTIME_LAST_FAILURE_UNIX = now_ts
        elif previous_state == "degraded" and state_val != "degraded":
            PLAYBACK_RUNTIME_LAST_RECOVERY_ACTION = reason_val or f"recovered_to_{state_val}"
            PLAYBACK_RUNTIME_LAST_RECOVERY_UNIX = now_ts
        elif previous_state == "buffering" and state_val in ("playing", "paused"):
            PLAYBACK_RUNTIME_LAST_RECOVERY_ACTION = reason_val or f"buffering_to_{state_val}"
            PLAYBACK_RUNTIME_LAST_RECOVERY_UNIX = now_ts
        persist_session()
    return get_playback_runtime_state_info(now_ts=now_ts)


def get_playback_runtime_state_info(*, now_ts: float | None = None) -> dict:
    now_val = float(now_ts if now_ts is not None else time.time())
    since = float(PLAYBACK_RUNTIME_STATE_SINCE_UNIX or 0.0)
    last_transition = float(PLAYBACK_RUNTIME_LAST_TRANSITION_UNIX or since or now_val)
    return {
        "playback_runtime_state": PLAYBACK_RUNTIME_STATE,
        "playback_runtime_state_reason": PLAYBACK_RUNTIME_STATE_REASON,
        "playback_runtime_previous_state": PLAYBACK_RUNTIME_PREVIOUS_STATE,
        "playback_runtime_previous_reason": PLAYBACK_RUNTIME_PREVIOUS_REASON,
        "playback_runtime_state_since_unix": since,
        "playback_runtime_last_transition_unix": last_transition,
        "playback_runtime_time_in_state_sec": max(0.0, round(now_val - since, 3)),
        "playback_runtime_last_failure_class": PLAYBACK_RUNTIME_LAST_FAILURE_CLASS,
        "playback_runtime_last_failure_unix": float(PLAYBACK_RUNTIME_LAST_FAILURE_UNIX or 0.0),
        "playback_runtime_last_recovery_action": PLAYBACK_RUNTIME_LAST_RECOVERY_ACTION,
        "playback_runtime_last_recovery_unix": float(PLAYBACK_RUNTIME_LAST_RECOVERY_UNIX or 0.0),
    }


def update_overlay_delivery_state(
    next_state: str,
    reason: str = "",
    *,
    client_event: str | None = None,
    client_reason: str | None = None,
) -> dict:
    global OVERLAY_DELIVERY_STATE, OVERLAY_DELIVERY_STATE_REASON
    global OVERLAY_DELIVERY_PREVIOUS_STATE, OVERLAY_DELIVERY_PREVIOUS_REASON
    global OVERLAY_DELIVERY_STATE_SINCE_UNIX, OVERLAY_DELIVERY_LAST_TRANSITION_UNIX
    global OVERLAY_DELIVERY_LAST_FAILURE_CLASS, OVERLAY_DELIVERY_LAST_FAILURE_UNIX
    global OVERLAY_DELIVERY_LAST_RECOVERY_ACTION, OVERLAY_DELIVERY_LAST_RECOVERY_UNIX
    global OVERLAY_DELIVERY_LAST_CLIENT_EVENT, OVERLAY_DELIVERY_LAST_CLIENT_REASON
    global OVERLAY_DELIVERY_LAST_CLIENT_EVENT_UNIX

    state_val = str(next_state or "disconnected").strip().lower() or "disconnected"
    reason_val = str(reason or "").strip().lower()
    event_val = str(client_event or "").strip().lower()
    client_reason_val = str(client_reason or reason_val).strip().lower()
    now_ts = time.time()
    previous_state = OVERLAY_DELIVERY_STATE
    changed = (state_val != OVERLAY_DELIVERY_STATE or reason_val != OVERLAY_DELIVERY_STATE_REASON)
    if changed:
        OVERLAY_DELIVERY_PREVIOUS_STATE = OVERLAY_DELIVERY_STATE
        OVERLAY_DELIVERY_PREVIOUS_REASON = OVERLAY_DELIVERY_STATE_REASON
        OVERLAY_DELIVERY_STATE = state_val
        OVERLAY_DELIVERY_STATE_REASON = reason_val
        OVERLAY_DELIVERY_STATE_SINCE_UNIX = now_ts
        OVERLAY_DELIVERY_LAST_TRANSITION_UNIX = now_ts
        if state_val in ("headless", "disconnected", "stale", "retrying"):
            OVERLAY_DELIVERY_LAST_FAILURE_CLASS = reason_val or state_val
            OVERLAY_DELIVERY_LAST_FAILURE_UNIX = now_ts
        elif previous_state in ("headless", "disconnected", "stale", "retrying") and state_val in ("connected", "displaying", "draining"):
            OVERLAY_DELIVERY_LAST_RECOVERY_ACTION = reason_val or f"overlay_recovered_to_{state_val}"
            OVERLAY_DELIVERY_LAST_RECOVERY_UNIX = now_ts
    if event_val or client_reason is not None:
        OVERLAY_DELIVERY_LAST_CLIENT_EVENT = event_val
        OVERLAY_DELIVERY_LAST_CLIENT_REASON = client_reason_val
        OVERLAY_DELIVERY_LAST_CLIENT_EVENT_UNIX = now_ts
    return get_overlay_delivery_state_info(now_ts=now_ts)


def get_overlay_delivery_state_info(*, now_ts: float | None = None) -> dict:
    now_val = float(now_ts if now_ts is not None else time.time())
    since = float(OVERLAY_DELIVERY_STATE_SINCE_UNIX or 0.0)
    last_transition = float(OVERLAY_DELIVERY_LAST_TRANSITION_UNIX or since or now_val)
    last_client_event_unix = float(OVERLAY_DELIVERY_LAST_CLIENT_EVENT_UNIX or 0.0)
    return {
        "overlay_delivery_state": OVERLAY_DELIVERY_STATE,
        "overlay_delivery_reason": OVERLAY_DELIVERY_STATE_REASON,
        "overlay_delivery_previous_state": OVERLAY_DELIVERY_PREVIOUS_STATE,
        "overlay_delivery_previous_reason": OVERLAY_DELIVERY_PREVIOUS_REASON,
        "overlay_delivery_state_since_unix": since,
        "overlay_delivery_last_transition_unix": last_transition,
        "overlay_delivery_time_in_state_sec": max(0.0, round(now_val - since, 3)),
        "overlay_delivery_last_failure_class": OVERLAY_DELIVERY_LAST_FAILURE_CLASS,
        "overlay_delivery_last_failure_unix": float(OVERLAY_DELIVERY_LAST_FAILURE_UNIX or 0.0),
        "overlay_delivery_last_recovery_action": OVERLAY_DELIVERY_LAST_RECOVERY_ACTION,
        "overlay_delivery_last_recovery_unix": float(OVERLAY_DELIVERY_LAST_RECOVERY_UNIX or 0.0),
        "overlay_delivery_last_client_event": OVERLAY_DELIVERY_LAST_CLIENT_EVENT,
        "overlay_delivery_last_client_reason": OVERLAY_DELIVERY_LAST_CLIENT_REASON,
        "overlay_delivery_last_client_event_unix": last_client_event_unix,
        "overlay_delivery_last_client_event_age_sec": (
            max(0.0, round(now_val - last_client_event_unix, 3)) if last_client_event_unix > 0.0 else None
        ),
    }


def update_tv_state(**patch) -> dict:
    with TV_STATE_LOCK:
        TV_STATE.update({k: v for k, v in patch.items() if v is not None})
        if patch.get("active_source_phys_addr") is not None:
            TV_STATE["confidence"]["active_source_phys_addr"] = 0.9
        if patch.get("tv_power_status") is not None:
            TV_STATE["confidence"]["tv_power_status"] = 0.7
        if patch.get("last_event"):
            TV_STATE["last_event_ts"] = int(time.time())
        return dict(TV_STATE)


def get_tv_state() -> dict:
    with TV_STATE_LOCK:
        out = dict(TV_STATE)
        out["confidence"] = dict(TV_STATE.get("confidence") or {})
        return out


def history_add(entry: dict) -> None:
    _history_add(entry)





# =========================
# Settings (persisted)
# =========================

SETTINGS_LOCK = threading.Lock()
SETTINGS: dict = {}


def _default_idle_panels() -> dict:
    """Default idle dashboard panel configuration.

    Only currently implemented idle dashboard panels are exposed.
    """
    return {
        "weather": {"enabled": True, "layout": "split"},
    }


def _normalize_idle_panels(value: object) -> dict:
    defaults = _default_idle_panels()
    if not isinstance(value, dict):
        return defaults
    out = _default_idle_panels()
    for key, panel in value.items():
        if key not in out or not isinstance(panel, dict):
            continue
        enabled = panel.get("enabled")
        layout = panel.get("layout")
        out[key] = {
            "enabled": bool(enabled) if enabled is not None else bool(out[key].get("enabled")),
            "layout": str(layout or out[key].get("layout") or "default").strip() or "default",
        }
    return out


def _default_weather_settings() -> dict:
    return {
        "latitude": 40.7128,
        "longitude": -74.0060,
        "location_name": "New York, NY",
        "units": "imperial",  # imperial|metric
        "forecast_days": 7,  # 1|3|7
    }


def _normalize_weather_settings(value: object) -> dict:
    out = _default_weather_settings()
    if not isinstance(value, dict):
        return out
    try:
        lat = float(value.get("latitude", out["latitude"]))
    except Exception:
        lat = out["latitude"]
    try:
        lon = float(value.get("longitude", out["longitude"]))
    except Exception:
        lon = out["longitude"]
    units = str(value.get("units", out["units"]) or out["units"]).strip().lower()
    if units not in ("imperial", "metric"):
        units = out["units"]
    location_name = str(value.get("location_name", out["location_name"]) or out["location_name"]).strip()
    if len(location_name) > 120:
        location_name = location_name[:120].strip()
    if not location_name:
        location_name = out["location_name"]
    try:
        days = int(value.get("forecast_days", out["forecast_days"]))
    except Exception:
        days = out["forecast_days"]
    if days not in (1, 3, 7):
        days = out["forecast_days"]
    out.update({
        "latitude": max(-90.0, min(90.0, lat)),
        "longitude": max(-180.0, min(180.0, lon)),
        "location_name": location_name,
        "units": units,
        "forecast_days": days,
    })
    return out


def _default_upload_settings() -> dict:
    return {
        "max_size_gb": 5.0,
        "retention_hours": 24,
    }


def _normalize_upload_settings(value: object) -> dict:
    out = _default_upload_settings()
    if not isinstance(value, dict):
        return out
    try:
        max_size_gb = float(value.get("max_size_gb", out["max_size_gb"]))
    except Exception:
        max_size_gb = out["max_size_gb"]
    try:
        retention_hours = int(value.get("retention_hours", out["retention_hours"]))
    except Exception:
        retention_hours = out["retention_hours"]
    out["max_size_gb"] = max(0.25, min(500.0, round(float(max_size_gb), 2)))
    out["retention_hours"] = max(1, min(24 * 90, int(retention_hours)))
    return out

def _default_settings() -> dict:
    default_volume = _normalize_volume(os.getenv("RELAYTV_DEFAULT_VOLUME", "100"))
    device_name = (os.getenv("RELAYTV_DEVICE_NAME") or "RelayTV").strip() or "RelayTV"
    if len(device_name) > 80:
        device_name = device_name[:80].strip() or "RelayTV"
    quality_mode = _normalize_quality_mode(os.getenv("RELAYTV_QUALITY_MODE"))
    quality_cap = _normalize_quality_cap(os.getenv("RELAYTV_QUALITY_CAP"))
    return {
        "device_name": device_name,
        "video_mode": (os.getenv("RELAYTV_VIDEO_MODE", "auto") or "auto").strip().lower(),
        "drm_connector": (os.getenv("RELAYTV_DRM_CONNECTOR") or "").strip(),
        # Empty means "auto-detect" at playback time.
        "audio_device": "",
        "quality_mode": quality_mode,
        "quality_cap": quality_cap,
        "ytdlp_format": _default_ytdlp_format(),
        "youtube_cookies_path": (
            os.getenv("RELAYTV_YTDLP_COOKIES")
            or os.getenv("YTDLP_COOKIES")
            or ""
        ).strip(),
        "youtube_use_invidious": _env_bool("USE_INVIDIOUS", False),
        "youtube_invidious_base": _normalize_invidious_base(os.getenv("INVIDIOUS_BASE")),
        "sub_lang": (os.getenv("RELAYTV_SUB_LANG") or "").strip(),
        "cec_enabled": (os.getenv("RELAYTV_CEC", "0") or "0").strip(),
        "tv_takeover_enabled": "1",
        "tv_pause_on_input_change": "1",
        "tv_auto_resume_on_return": "0",
        "volume": default_volume,
        "idle_dashboard_enabled": _env_bool("RELAYTV_IDLE_DASHBOARD_ENABLED", True),
        "idle_qr_enabled": _env_bool("RELAYTV_IDLE_QR_ENABLED", True),
        "idle_qr_size": _normalize_idle_qr_size(os.getenv("RELAYTV_IDLE_QR_SIZE", "168"), 168),
        "idle_panels": _default_idle_panels(),
        "weather": _default_weather_settings(),
        "uploads": _normalize_upload_settings(
            {
                "max_size_gb": os.getenv("RELAYTV_UPLOAD_MAX_SIZE_GB", "5"),
                "retention_hours": os.getenv("RELAYTV_UPLOAD_RETENTION_HOURS", "24"),
            }
        ),
        "jellyfin_enabled": _env_bool("RELAYTV_JELLYFIN_ENABLED", False),
        "jellyfin_server_url": (os.getenv("RELAYTV_JELLYFIN_SERVER_URL") or "").strip(),
        "jellyfin_api_key": (os.getenv("RELAYTV_JELLYFIN_API_KEY") or "").strip(),
        "jellyfin_auth_enabled": _env_bool("RELAYTV_JELLYFIN_AUTH_ENABLED", True),
        "jellyfin_username": (os.getenv("RELAYTV_JELLYFIN_USERNAME") or "").strip(),
        "jellyfin_password": (os.getenv("RELAYTV_JELLYFIN_PASSWORD") or "").strip(),
        "jellyfin_user_id": (os.getenv("RELAYTV_JELLYFIN_USER_ID") or "").strip(),
        "jellyfin_audio_lang": (os.getenv("RELAYTV_JELLYFIN_AUDIO_LANG") or "").strip(),
        "jellyfin_sub_lang": (os.getenv("RELAYTV_JELLYFIN_SUB_LANG") or os.getenv("RELAYTV_SUB_LANG") or "").strip(),
        "jellyfin_playback_mode": _normalize_jellyfin_playback_mode(os.getenv("RELAYTV_JELLYFIN_PLAYBACK_MODE") or "auto"),
    }

def load_settings() -> None:
    global SETTINGS
    path = _state_path(SETTINGS_STATE_FILE)
    defaults = _default_settings()
    data = _load_json(path, {})
    if isinstance(data, dict):
        defaults.update({k: v for k, v in data.items() if v is not None})
    defaults["ytdlp_format"] = _normalize_ytdlp_format(defaults.get("ytdlp_format"))
    defaults["quality_mode"] = _normalize_quality_mode(defaults.get("quality_mode"))
    defaults["quality_cap"] = _normalize_quality_cap(defaults.get("quality_cap"))
    defaults["youtube_cookies_path"] = str(defaults.get("youtube_cookies_path") or "").strip()
    defaults["youtube_use_invidious"] = bool(defaults.get("youtube_use_invidious"))
    defaults["youtube_invidious_base"] = _normalize_invidious_base(defaults.get("youtube_invidious_base"))
    defaults["volume"] = _normalize_volume(defaults.get("volume"), defaults.get("volume", 100.0))
    defaults["idle_dashboard_enabled"] = bool(defaults.get("idle_dashboard_enabled"))
    defaults["idle_qr_size"] = _normalize_idle_qr_size(defaults.get("idle_qr_size"), 168)
    defaults["idle_panels"] = _normalize_idle_panels(defaults.get("idle_panels"))
    defaults["weather"] = _normalize_weather_settings(defaults.get("weather"))
    defaults["uploads"] = _normalize_upload_settings(defaults.get("uploads"))
    defaults["jellyfin_playback_mode"] = _normalize_jellyfin_playback_mode(defaults.get("jellyfin_playback_mode"))
    with SETTINGS_LOCK:
        SETTINGS = defaults

def persist_settings() -> None:
    with SETTINGS_LOCK:
        payload = dict(SETTINGS)
    _atomic_write_json(_state_path(SETTINGS_STATE_FILE), payload)

def get_settings() -> dict:
    with SETTINGS_LOCK:
        return dict(SETTINGS)

def update_settings(patch: dict) -> dict:
    """Update settings with patch dict, persist, return updated."""
    allowed = {
        "device_name",
        "video_mode",
        "drm_connector",
        "audio_device",
        "quality_mode",
        "quality_cap",
        "ytdlp_format",
        "youtube_cookies_path",
        "youtube_use_invidious",
        "youtube_invidious_base",
        "sub_lang",
        "cec_enabled",
        "tv_takeover_enabled",
        "tv_pause_on_input_change",
        "tv_auto_resume_on_return",
        "volume",
        "idle_dashboard_enabled",
        "idle_qr_enabled",
        "idle_qr_size",
        "idle_panels",
        "weather",
        "uploads",
        "jellyfin_enabled",
        "jellyfin_server_url",
        "jellyfin_api_key",
        "jellyfin_auth_enabled",
        "jellyfin_username",
        "jellyfin_password",
        "jellyfin_user_id",
        "jellyfin_audio_lang",
        "jellyfin_sub_lang",
        "jellyfin_playback_mode",
    }
    clean = {k: v for k, v in (patch or {}).items() if k in allowed}
    if "device_name" in clean:
        name = str(clean.get("device_name") or "").strip() or "RelayTV"
        if len(name) > 80:
            name = name[:80].strip() or "RelayTV"
        clean["device_name"] = name
    if "ytdlp_format" in clean:
        clean["ytdlp_format"] = _normalize_ytdlp_format(clean.get("ytdlp_format"))
    if "quality_mode" in clean:
        clean["quality_mode"] = _normalize_quality_mode(clean.get("quality_mode"))
    if "quality_cap" in clean:
        clean["quality_cap"] = _normalize_quality_cap(clean.get("quality_cap"))
    if "youtube_cookies_path" in clean:
        clean["youtube_cookies_path"] = str(clean.get("youtube_cookies_path") or "").strip()
    if "youtube_use_invidious" in clean:
        clean["youtube_use_invidious"] = bool(clean.get("youtube_use_invidious"))
    if "youtube_invidious_base" in clean:
        clean["youtube_invidious_base"] = _normalize_invidious_base(clean.get("youtube_invidious_base"))
    if "volume" in clean:
        clean["volume"] = _normalize_volume(clean.get("volume"), 100.0)
    if "idle_dashboard_enabled" in clean:
        clean["idle_dashboard_enabled"] = bool(clean.get("idle_dashboard_enabled"))
    if "idle_qr_enabled" in clean:
        clean["idle_qr_enabled"] = bool(clean.get("idle_qr_enabled"))
    if "idle_qr_size" in clean:
        clean["idle_qr_size"] = _normalize_idle_qr_size(clean.get("idle_qr_size"), 168)
    if "idle_panels" in clean:
        clean["idle_panels"] = _normalize_idle_panels(clean.get("idle_panels"))
    if "weather" in clean:
        clean["weather"] = _normalize_weather_settings(clean.get("weather"))
    if "uploads" in clean:
        clean["uploads"] = _normalize_upload_settings(clean.get("uploads"))
    if "jellyfin_enabled" in clean:
        clean["jellyfin_enabled"] = bool(clean.get("jellyfin_enabled"))
    if "jellyfin_auth_enabled" in clean:
        clean["jellyfin_auth_enabled"] = bool(clean.get("jellyfin_auth_enabled"))
    if "jellyfin_server_url" in clean:
        clean["jellyfin_server_url"] = str(clean.get("jellyfin_server_url") or "").strip()
    if "jellyfin_api_key" in clean:
        clean["jellyfin_api_key"] = str(clean.get("jellyfin_api_key") or "").strip()
    if "jellyfin_username" in clean:
        clean["jellyfin_username"] = str(clean.get("jellyfin_username") or "").strip()
    if "jellyfin_password" in clean:
        clean["jellyfin_password"] = str(clean.get("jellyfin_password") or "").strip()
    if "jellyfin_user_id" in clean:
        clean["jellyfin_user_id"] = str(clean.get("jellyfin_user_id") or "").strip()
    if "jellyfin_audio_lang" in clean:
        clean["jellyfin_audio_lang"] = str(clean.get("jellyfin_audio_lang") or "").strip().lower()
    if "jellyfin_sub_lang" in clean:
        clean["jellyfin_sub_lang"] = str(clean.get("jellyfin_sub_lang") or "").strip().lower()
    if "jellyfin_playback_mode" in clean:
        clean["jellyfin_playback_mode"] = _normalize_jellyfin_playback_mode(clean.get("jellyfin_playback_mode"))
    with SETTINGS_LOCK:
        SETTINGS.update(clean)
        payload = dict(SETTINGS)
    try:
        _atomic_write_json(_state_path(SETTINGS_STATE_FILE), payload)
    except Exception:
        pass
    return payload

def _load_runtime_state() -> None:
    """Load startup state in a deterministic order."""
    _load_persisted_state()
    _load_persisted_session()
    load_settings()


def load_state_from_disk() -> None:
    """Load persisted queue/history/session/settings into memory (best-effort)."""
    # Keep load order explicit: queue/history, resumable session, then runtime settings.
    _load_runtime_state()
