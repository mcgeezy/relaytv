# SPDX-License-Identifier: GPL-3.0-only
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import player, state


router = APIRouter()


class VolumeReq(BaseModel):
    delta: float | None = None
    set: float | None = None


class MuteReq(BaseModel):
    set: bool | None = None


class SeekReq(BaseModel):
    sec: float


class SeekAbsReq(BaseModel):
    sec: float


class PlayReq(BaseModel):
    url: str
    use_ytdlp: bool = True
    cec: bool = False


class PlayNowReq(BaseModel):
    """Play immediately, optionally preserving current playback."""

    url: str
    preserve_current: bool = True
    preserve_to: str = "queue_front"
    resume_current: bool = True
    reason: str | None = None
    title: str | None = None
    thumbnail: str | None = None
    resume_pos: float | None = None
    history_id: str | None = None
    resolved_source_url: str | None = None
    resolved_stream: str | None = None
    resolved_audio: str | None = None
    resolved_at: float | None = None


class PlayTemporaryReq(BaseModel):
    url: str
    resume: bool = True
    resume_mode: str = "auto"
    timeout_sec: float | None = 15.0
    volume_override: float | None = None


class PlayAtReq(BaseModel):
    url: str
    start_at: float


def _control_ack_payload(result: dict | None) -> dict[str, object]:
    from . import _control_ack_payload as control_ack_payload

    return control_ack_payload(result)


def _control_result_or_raise(result: dict | None, *, action: str) -> dict[str, object]:
    from . import _control_result_or_raise as control_result_or_raise

    return control_result_or_raise(result, action=action)


def _seek_transition_hold_sec() -> float:
    from . import _seek_transition_hold_sec as seek_transition_hold_sec

    return seek_transition_hold_sec()


def _seek_relative_result(delta_sec: float) -> dict[str, object]:
    from . import _seek_relative_result as seek_relative_result

    return seek_relative_result(delta_sec)


def _seek_absolute_result(target_sec: float) -> dict[str, object]:
    from . import _seek_absolute_result as seek_absolute_result

    return seek_absolute_result(target_sec)


def _playback_state_fast_snapshot() -> dict[str, object]:
    from . import _playback_state_fast_snapshot as playback_state_fast_snapshot

    return playback_state_fast_snapshot()


def _resume_paused_current_session_in_place(*, action: str = "resume") -> dict[str, object] | None:
    from . import _resume_paused_current_session_in_place as resume_paused_current_session_in_place

    return resume_paused_current_session_in_place(action=action)


def _playback_notification_display_sec() -> float:
    from . import _playback_notification_display_sec as playback_notification_display_sec

    return playback_notification_display_sec()


def _push_overlay_toast(**kwargs) -> None:
    from . import _push_overlay_toast as push_overlay_toast

    push_overlay_toast(**kwargs)


def _ui_event_push_queue(
    action: str,
    queue: list[object] | None = None,
    queue_length: int | None = None,
    source: str = "api",
) -> None:
    from . import _ui_event_push_queue as push_queue_event

    push_queue_event(action, queue=queue, queue_length=queue_length, source=source)


def _preserve_current_to_queue_front() -> dict | None:
    """If something is playing, capture it and insert at front of queue with resume_pos."""
    if not player.is_playing():
        return None
    now = state.NOW_PLAYING
    if not isinstance(now, dict):
        return None

    pos = None
    dur = None
    with player.MPV_LOCK:
        try:
            pos = player.mpv_get("time-pos")
        except Exception:
            pos = None
        try:
            dur = player.mpv_get("duration")
        except Exception:
            dur = None
    try:
        pos_f = float(pos) if pos is not None else None
    except Exception:
        pos_f = None

    url = now.get("url")
    if not isinstance(url, str) or not url.strip():
        return None

    preserved = {
        "url": url.strip(),
        "title": now.get("title") or url.strip(),
        "provider": now.get("provider"),
        "_relaytv_interrupt_preserved": True,
        "_relaytv_interrupt_preserved_at": int(time.time()),
    }
    if isinstance(now.get("channel"), str) and now.get("channel"):
        preserved["channel"] = now.get("channel")
    if isinstance(now.get("thumbnail"), str) and now.get("thumbnail"):
        preserved["thumbnail"] = now.get("thumbnail")
    if isinstance(now.get("thumbnail_local"), str) and now.get("thumbnail_local"):
        preserved["thumbnail_local"] = now.get("thumbnail_local")
    if isinstance(now.get("jellyfin_item_id"), str) and now.get("jellyfin_item_id"):
        preserved["jellyfin_item_id"] = now.get("jellyfin_item_id")
    if isinstance(now.get("jellyfin_media_source_id"), str) and now.get("jellyfin_media_source_id"):
        preserved["jellyfin_media_source_id"] = now.get("jellyfin_media_source_id")
    if isinstance(now.get("history_id"), str) and now.get("history_id"):
        preserved["history_id"] = now.get("history_id")
    resolved_stream = str(now.get("_resolved_stream") or "").strip()
    if not resolved_stream:
        now_stream = str(now.get("stream") or "").strip()
        if now_stream and now_stream != url.strip():
            resolved_stream = now_stream
    if resolved_stream:
        preserved["_resolved_source_url"] = url.strip()
        preserved["_resolved_stream"] = resolved_stream
        resolved_audio = str(now.get("_resolved_audio") or now.get("audio") or "").strip()
        if resolved_audio:
            preserved["_resolved_audio"] = resolved_audio
        try:
            preserved["_resolved_at"] = float(now.get("_resolved_at") or time.time())
        except Exception:
            preserved["_resolved_at"] = time.time()
    if pos_f is not None:
        preserved["resume_pos"] = pos_f
    player.update_history_progress(now, position_sec=pos_f, duration_sec=dur, force=True)

    with state.QUEUE_LOCK:
        state.QUEUE.insert(0, preserved)
        snapshot = {"queue": list(state.QUEUE), "saved_at": int(time.time())}
    try:
        state.persist_queue_payload(snapshot)
    except Exception:
        from . import logger

        logger.warning("queue_persist_failed route=play_now_preserve")
    return preserved


def _discard_interrupted_playback_state(reason: str) -> None:
    from . import _discard_interrupted_playback_state as discard_interrupted_playback_state

    discard_interrupted_playback_state(reason)


def _stop_current_for_idle_or_desktop() -> bool:
    keep_qt_shell = bool(
        _idle_visual_surface_enabled_for_player()
        and getattr(player, "_qt_shell_backend_enabled", lambda: False)()
    )
    if keep_qt_shell:
        stopped_in_place = bool(getattr(player, "stop_playback_keep_qt_shell", lambda: False)())
        if stopped_in_place:
            return True
    player.stop_mpv(restart_splash=_idle_visual_surface_enabled_for_player())
    _ensure_notification_surface(wait_for_subscriber=False)
    return False


def _can_preserve_closed_session() -> bool:
    from . import _can_preserve_closed_session as can_preserve_closed_session

    return can_preserve_closed_session()


def _idle_visual_surface_enabled_for_player() -> bool:
    from . import _idle_visual_surface_enabled_for_player as idle_visual_surface_enabled_for_player

    return idle_visual_surface_enabled_for_player()


def _ensure_notification_surface(*, wait_for_subscriber: bool = False) -> None:
    from . import _ensure_notification_surface as ensure_notification_surface

    ensure_notification_surface(wait_for_subscriber=wait_for_subscriber)


def _jellyfin_emit_stopped_hint(position_sec: float | None = None, duration_sec: float | None = None) -> None:
    from . import _jellyfin_emit_stopped_hint as jellyfin_emit_stopped_hint

    jellyfin_emit_stopped_hint(position_sec, duration_sec)


def _next_track() -> dict:
    return next_track()


def _temporary_playback_stack() -> list[dict]:
    from . import _TEMP_PLAYBACK_STACK

    return _TEMP_PLAYBACK_STACK


def _temporary_playback_lock():
    from . import _TEMP_PLAYBACK_LOCK

    return _TEMP_PLAYBACK_LOCK


def _capture_current_playback_state() -> dict | None:
    from . import _capture_current_playback_state as capture_current_playback_state

    return capture_current_playback_state()


def _complete_temporary_playback(frame_id: str, reason: str) -> bool:
    from . import _complete_temporary_playback as complete_temporary_playback

    return complete_temporary_playback(frame_id, reason)


def _temporary_watchdog(frame_id: str, timeout_sec: float | None) -> None:
    from . import _temporary_watchdog as temporary_watchdog

    temporary_watchdog(frame_id, timeout_sec)


def _threading_module():
    from . import threading as threading_module

    return threading_module


def _logger():
    from . import logger

    return logger


def _smart_item_from_url(url: str, *, start_pos: float | None = None, lightweight: bool = False) -> dict:
    from . import _smart_item_from_url as smart_item_from_url

    if start_pos is not None:
        return smart_item_from_url(url, start_pos=start_pos, lightweight=lightweight)
    if lightweight:
        return smart_item_from_url(url, lightweight=True)
    return smart_item_from_url(url)


def _push_queue_added_toast_async(item: object, fallback_label: str) -> None:
    from . import _push_queue_added_toast_async as push_queue_added_toast_async

    push_queue_added_toast_async(item, fallback_label)


@router.post("/play")
def play(req: PlayReq):
    """Immediate play; clears queue."""
    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0
    item = _smart_item_from_url(req.url or "")
    start_pos = item.get("resume_pos") if isinstance(item, dict) else None
    now = player.play_item(
        item,
        use_resolver=req.use_ytdlp,
        cec=req.cec,
        clear_queue=True,
        mode="play",
        start_pos=(float(start_pos) if start_pos is not None else None),
    )
    return {"status": "playing", "now_playing": now}


@router.post("/next")
def next_track():
    try:
        result = dict(player.advance_queue_playback(mode="next", prefer_playlist_next=True, poll_sleep=time.sleep))
    except player.QueueAdvanceEmptyError:
        raise HTTPException(status_code=400, detail="Queue is empty")
    if result.get("method") == "dequeue_play_item":
        result.pop("method", None)
    return result


@router.post("/play_now")
def play_now(req: PlayNowReq):
    """Play immediately, optionally preserving the currently playing item."""
    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0

    preserved = None
    if req.preserve_current and req.preserve_to == "queue_front" and req.resume_current:
        preserved = _preserve_current_to_queue_front()

    if (
        req.title
        or req.thumbnail
        or req.resume_pos is not None
        or req.history_id
        or req.resolved_stream
    ):
        item = {"url": req.url}
        if req.title:
            item["title"] = req.title
        if req.thumbnail:
            item["thumbnail"] = req.thumbnail
        if req.history_id:
            item["history_id"] = req.history_id
        if req.resolved_stream:
            item["_resolved_source_url"] = (req.resolved_source_url or req.url or "").strip()
            item["_resolved_stream"] = req.resolved_stream.strip()
            if req.resolved_audio:
                item["_resolved_audio"] = req.resolved_audio.strip()
            if req.resolved_at is not None:
                item["_resolved_at"] = req.resolved_at
        now = player.play_item(
            item,
            use_resolver=True,
            cec=False,
            clear_queue=False,
            mode=(req.reason or "play_now"),
            start_pos=req.resume_pos,
        )
    else:
        now = player.play_item(req.url, use_resolver=True, cec=False, clear_queue=False, mode=(req.reason or "play_now"))
    try:
        title = now.get("title") if isinstance(now, dict) else None
        _push_overlay_toast(
            text=f"Playing now: {title or req.url}",
            duration=_playback_notification_display_sec(),
            level="success",
            icon="play",
            image_url=(now.get("thumbnail_local") or now.get("thumbnail")) if isinstance(now, dict) else None,
        )
    except Exception:
        pass
    with state.QUEUE_LOCK:
        qlen = len(state.QUEUE)
        queue_snapshot = list(state.QUEUE)
    if preserved is not None or qlen:
        _ui_event_push_queue("play_now", queue=queue_snapshot, queue_length=qlen, source="play_now")
    return {"ok": True, "action": "played", "now_playing": now, "preserved": preserved, "queue_length": qlen}


@router.post("/play_temporary")
def play_temporary(req: PlayTemporaryReq):
    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0
    snapshot = _capture_current_playback_state()
    frame_id = str(uuid.uuid4())
    frame = {
        "id": frame_id,
        "resume": bool(req.resume),
        "snapshot": snapshot,
        "started_at": time.time(),
    }

    if req.volume_override is not None:
        try:
            player.mpv_set("volume", max(0.0, min(200.0, float(req.volume_override) * 100.0)))
        except Exception:
            pass

    stack = _temporary_playback_stack()
    with _temporary_playback_lock():
        stack.append(frame)

    now = player.play_item(req.url, use_resolver=True, cec=False, clear_queue=False, mode="play_temporary")
    try:
        title = now.get("title") if isinstance(now, dict) else None
        _push_overlay_toast(
            text=f"Temporary playback: {title or req.url}",
            duration=_playback_notification_display_sec(),
            level="warn",
            icon="play",
            image_url=(now.get("thumbnail_local") or now.get("thumbnail")) if isinstance(now, dict) else None,
        )
    except Exception:
        pass
    timeout = float(req.timeout_sec) if req.timeout_sec is not None and req.timeout_sec > 0 else None
    _threading_module().Thread(target=_temporary_watchdog, args=(frame_id, timeout), daemon=True).start()
    return {"ok": True, "temporary_id": frame_id, "now_playing": now, "stack_depth": len(stack)}


@router.post("/play_temporary/cancel")
def play_temporary_cancel():
    stack = _temporary_playback_stack()
    with _temporary_playback_lock():
        if not stack:
            raise HTTPException(status_code=400, detail="No temporary playback in progress")
        frame_id = stack[-1].get("id")
    restored = _complete_temporary_playback(frame_id, reason="cancel")
    return {"ok": restored, "stack_depth": len(stack)}


@router.post("/play_at")
def play_at(req: PlayAtReq):
    def _delayed_play() -> None:
        delay = max(0.0, float(req.start_at) - time.time())
        if delay > 0:
            time.sleep(delay)
        try:
            player.play_item(req.url, use_resolver=True, cec=False, clear_queue=False, mode="play_at")
        except Exception as e:
            _logger().warning("play_at_failed start_at=%s error=%s", req.start_at, e)

    _threading_module().Thread(target=_delayed_play, daemon=True).start()
    return {"ok": True, "url": req.url, "start_at": req.start_at}


@router.post("/previous")
def previous():
    """Back button semantics."""
    if player.is_playing():
        try:
            with player.MPV_LOCK:
                pos = player.mpv_get("time-pos")
            if pos is not None and float(pos) > 5.0:
                player.mpv_command(["seek", 0.0, "absolute"])
                return {"ok": True, "action": "restart"}
        except Exception:
            pass

    cur_url = None
    if isinstance(state.NOW_PLAYING, dict):
        u = state.NOW_PLAYING.get("url")
        if isinstance(u, str) and u.strip():
            cur_url = u.strip()

    chosen = None
    with state.HISTORY_LOCK:
        for i, it in enumerate(state.HISTORY):
            if not isinstance(it, dict):
                continue
            u = it.get("url")
            if not isinstance(u, str) or not u.strip():
                continue
            u = u.strip()
            if cur_url and u == cur_url:
                continue
            chosen = dict(it)
            state.HISTORY.pop(i)
            break
    if chosen is None:
        raise HTTPException(status_code=400, detail="No previous history item")
    try:
        state.persist_history()
    except Exception:
        pass

    return play_now(PlayNowReq(url=chosen.get("url"), preserve_current=True, reason="previous"))


@router.get("/share")
def share(url: str | None = None, link: str | None = None, cec: bool = True):
    shared = (url or link or "").strip()
    if not shared:
        raise HTTPException(status_code=400, detail="Missing url or link query parameter")
    item = _smart_item_from_url(shared)
    start_pos = item.get("resume_pos") if isinstance(item, dict) else None
    now = player.play_item(
        item,
        use_resolver=True,
        cec=cec,
        clear_queue=True,
        mode="share",
        start_pos=(float(start_pos) if start_pos is not None else None),
    )
    return {"status": "playing", "now_playing": now, "source": "share_target"}


@router.post("/smart")
def smart(req: PlayReq):
    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0
    if player.is_playing():
        item = _smart_item_from_url(req.url or "", lightweight=True)
        with state.QUEUE_LOCK:
            state.QUEUE.append(item)
            qlen = len(state.QUEUE)
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
            _push_queue_added_toast_async(item, req.url or "item")
        except Exception:
            pass
        return {"status": "queued", "item": item, "queue_length": qlen, "now_playing": state.NOW_PLAYING}

    item = _smart_item_from_url(req.url or "")
    start_pos = item.get("resume_pos") if isinstance(item, dict) else None
    now = player.play_item(
        item,
        use_resolver=req.use_ytdlp,
        cec=req.cec,
        clear_queue=True,
        mode="smart_play",
        start_pos=(float(start_pos) if start_pos is not None else None),
    )
    return {"status": "playing", "now_playing": now}


@router.post("/now_playing/clear")
def clear_now_playing():
    """Discard current now-playing item; advance queue or return to idle/desktop."""
    _discard_interrupted_playback_state("now_playing_clear")
    with state.QUEUE_LOCK:
        has_queue = bool(state.QUEUE)
    if has_queue:
        return _next_track()

    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 3600 * 24
    with player.MPV_LOCK:
        stopped_in_place = _stop_current_for_idle_or_desktop()
    state.set_now_playing(None)
    state.set_session_position(None)
    state.set_session_state("idle")
    try:
        state.persist_queue()
    except Exception:
        pass
    return {"status": "cleared", "resume_available": False, "kept_player_shell": bool(stopped_in_place)}


@router.post("/close")
def close():
    """Close the player but keep session resumable (queue preserved)."""
    # Prevent the autoplay worker from immediately advancing.
    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 3600 * 24
    _discard_interrupted_playback_state("close")

    pos = None
    dur = None
    preserve_resume = _can_preserve_closed_session() or isinstance(state.NOW_PLAYING, dict)
    try:
        if bool(getattr(player, "native_qt_playback_explicitly_ended", lambda: False)()):
            preserve_resume = False
    except Exception:
        pass
    if preserve_resume:
        with player.MPV_LOCK:
            try:
                pos = player.mpv_get("time-pos")
            except Exception:
                pos = None
            try:
                dur = player.mpv_get("duration")
            except Exception:
                dur = None
        if pos is None and isinstance(state.NOW_PLAYING, dict):
            pos = state.NOW_PLAYING.get("resume_pos")
        try:
            state.set_session_position(float(pos) if pos is not None else None)
        except Exception:
            state.set_session_position(None)

        try:
            if isinstance(state.NOW_PLAYING, dict) and pos is not None:
                np = dict(state.NOW_PLAYING)
                np["resume_pos"] = float(pos)
                np["closed"] = True
                np["closed_at"] = int(time.time())
                state.set_now_playing(np)
        except Exception:
            pass
    elif getattr(state, "SESSION_STATE", "idle") != "closed":
        try:
            state.set_now_playing(None)
        except Exception:
            pass
        try:
            state.set_session_position(None)
        except Exception:
            pass

    state.set_session_state("closed" if preserve_resume else "idle")
    keep_qt_shell = bool(
        preserve_resume
        and _idle_visual_surface_enabled_for_player()
        and getattr(player, "_qt_shell_backend_enabled", lambda: False)()
    )
    stopped_in_place = False
    if keep_qt_shell:
        with player.MPV_LOCK:
            stopped_in_place = bool(getattr(player, "stop_playback_keep_qt_shell", lambda: False)())
    if not stopped_in_place:
        with player.MPV_LOCK:
            player.stop_mpv(restart_splash=_idle_visual_surface_enabled_for_player())
        _ensure_notification_surface(wait_for_subscriber=False)

    if preserve_resume:
        player.update_history_progress(
            state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None,
            position_sec=pos,
            duration_sec=dur,
            force=True,
        )
        _jellyfin_emit_stopped_hint(pos, dur)
    return {
        "status": ("closed" if preserve_resume else "idle"),
        "resume_available": bool(preserve_resume and state.NOW_PLAYING),
        "position": pos,
        "kept_player_shell": bool(stopped_in_place),
    }


@router.post("/resume/clear")
def clear_resumable_session():
    """Clear retained now-playing/resume state and return to idle."""
    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 3600 * 24
    _discard_interrupted_playback_state("resume_clear")
    with player.MPV_LOCK:
        player.stop_mpv()
    state.set_now_playing(None)
    state.set_session_position(None)
    state.set_session_state("idle")
    try:
        state.persist_queue()
    except Exception:
        pass
    return {"status": "cleared", "resume_available": False}


@router.post("/resume_session")
def resume_session():
    """Resume a previously closed session (best-effort)."""
    if getattr(state, "SESSION_STATE", "idle") != "closed":
        raise HTTPException(status_code=400, detail="No closed session to resume")

    now = state.NOW_PLAYING
    if not now:
        raise HTTPException(status_code=400, detail="No item to resume")

    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0

    stream = now.get("stream")
    audio = now.get("audio")
    pos = now.get("resume_pos")
    if pos is None:
        pos = getattr(state, "SESSION_POSITION", None)
    start_pos = None
    try:
        start_pos = player._normalize_start_pos(float(pos)) if pos is not None else None
    except Exception:
        start_pos = None

    if not isinstance(stream, str) or not stream.strip():
        resumed = player.play_item(
            now,
            use_resolver=True,
            cec=False,
            clear_queue=False,
            mode="resume",
            start_pos=start_pos,
        )
    else:
        with player.MPV_LOCK:
            stream_url = stream.strip()
            audio_url = audio.strip() if isinstance(audio, str) and audio.strip() else None
            if not player._load_stream_in_existing_mpv(stream_url, audio_url=audio_url, start_pos=start_pos):
                player.start_mpv(stream_url, audio_url=audio_url, start_pos=start_pos)
        resumed = dict(now)
        resumed["started"] = int(time.time())
        resumed["mode"] = "resume"
        resumed["closed"] = False
        state.set_now_playing(resumed)
        state.set_session_state("playing")

    resume_result: dict[str, object] | None = None
    if start_pos is not None:
        try:
            resume_result = _control_result_or_raise(player.mpv_set_result("pause", False), action="resume_session")
        except Exception:
            resume_result = None

    return {"status": "resumed", "now_playing": state.NOW_PLAYING, **_control_ack_payload(resume_result)}


@router.post("/stop")
def stop():
    """User stop with resume support; always return to idle visuals."""
    state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 3600 * 24
    _discard_interrupted_playback_state("stop")

    pos = None
    dur = None
    stop_hint_now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    emit_stopped_hint = isinstance(stop_hint_now, dict) and bool(stop_hint_now.get("jellyfin_item_id"))
    preserve_resume = _can_preserve_closed_session()
    if preserve_resume:
        with player.MPV_LOCK:
            pos = player.mpv_get("time-pos")
            dur = player.mpv_get("duration")
        try:
            state.set_session_position(float(pos) if pos is not None else None)
        except Exception:
            state.set_session_position(None)

    if preserve_resume:
        try:
            if isinstance(state.NOW_PLAYING, dict):
                np = dict(state.NOW_PLAYING)
                if pos is not None:
                    np["resume_pos"] = float(pos)
                np["closed"] = True
                np["closed_at"] = int(time.time())
                state.set_now_playing(np)
        except Exception:
            pass
    elif getattr(state, "SESSION_STATE", "idle") != "closed":
        try:
            state.set_now_playing(None)
        except Exception:
            pass
        try:
            state.set_session_position(None)
        except Exception:
            pass

    if preserve_resume:
        state.set_session_state("closed")
        with player.MPV_LOCK:
            player.stop_mpv()
        player.update_history_progress(
            state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None,
            position_sec=pos,
            duration_sec=dur,
            force=True,
        )
        _jellyfin_emit_stopped_hint(pos, dur)
        return {"status": "stopped", "resume_available": bool(state.NOW_PLAYING), "position": pos}

    if emit_stopped_hint:
        _jellyfin_emit_stopped_hint(pos, dur)
    if getattr(state, "SESSION_STATE", "idle") != "closed":
        try:
            state.set_now_playing(None)
        except Exception:
            pass
        try:
            state.set_session_position(None)
        except Exception:
            pass
    state.set_session_state("idle")
    with player.MPV_LOCK:
        player.stop_mpv()
    return {"status": ("stopped" if emit_stopped_hint else "idle"), "resume_available": False, "position": pos}


@router.post("/pause")
def pause():
    result = _control_result_or_raise(player.mpv_set_result("pause", True), action="pause")
    state.set_session_state("paused")
    state.set_pause_reason("user")
    return {"ok": True, "paused": True, **_control_ack_payload(result)}


@router.post("/resume")
def resume():
    result = _control_result_or_raise(player.mpv_set_result("pause", False), action="resume")
    state.set_session_state("playing")
    state.set_pause_reason(None)
    return {"ok": True, "paused": False, **_control_ack_payload(result)}


@router.post("/toggle_pause")
def toggle_pause():
    cur = bool(player.mpv_get("pause"))
    target = not cur
    result = _control_result_or_raise(player.mpv_set_result("pause", target), action="toggle_pause")
    state.set_session_state("paused" if target else "playing")
    state.set_pause_reason("user" if target else None)
    return {"ok": True, "paused": target, **_control_ack_payload(result)}


@router.post("/playback/play")
def playback_play():
    """
    User-facing Play semantics:
      - If mpv is running: toggle pause/resume
      - Else if current session is resumable and NOW_PLAYING exists: resume at saved position
      - Else: play next item from queue (if any)
    """
    # If already playing, behave as play/pause for stale clients that still call
    # /playback/play instead of /playback/toggle.
    if player.is_playing():
        cur = bool(player.mpv_get("pause"))
        target = not cur
        result = _control_result_or_raise(player.mpv_set_result("pause", target), action="playback_play")
        state.set_session_state("paused" if target else "playing")
        state.set_pause_reason("user" if target else None)
        return {
            "ok": True,
            "action": ("pause" if target else "resume"),
            "paused": target,
            "now_playing": state.NOW_PLAYING,
            **_control_ack_payload(result),
        }

    paused_resume = _resume_paused_current_session_in_place(action="resume")
    if paused_resume is not None:
        return paused_resume

    # If runtime dropped out but app state still has a resumable current item,
    # prefer resuming that item over consuming the queue.
    sess = str(getattr(state, "SESSION_STATE", "idle") or "idle").strip().lower()
    if sess in {"closed", "paused", "playing"} and state.NOW_PLAYING:
        now = state.NOW_PLAYING
        # Reuse resolved stream/audio where possible.
        stream = now.get("stream")
        audio = now.get("audio")
        pos = now.get("resume_pos")
        if pos is None:
            pos = getattr(state, "SESSION_POSITION", None)
        start_pos = None
        try:
            start_pos = player._normalize_start_pos(float(pos)) if pos is not None else None
        except Exception:
            start_pos = None
        state.AUTO_NEXT_SUPPRESS_UNTIL = time.time() + 2.0

        if isinstance(stream, str) and stream.strip():
            resume_result: dict[str, object] | None = None
            with player.MPV_LOCK:
                stream_url = stream.strip()
                audio_url = audio.strip() if isinstance(audio, str) and audio.strip() else None
                if not player._load_stream_in_existing_mpv(stream_url, audio_url=audio_url, start_pos=start_pos):
                    player.start_mpv(stream_url, audio_url=audio_url, start_pos=start_pos)
            try:
                resume_result = _control_result_or_raise(player.mpv_set_result("pause", False), action="resume_session")
            except Exception:
                resume_result = None
            resumed = dict(now)
            resumed["started"] = int(time.time())
            resumed["mode"] = "resume"
            resumed["closed"] = False
            state.set_now_playing(resumed)
            state.set_session_state("playing")
            state.set_pause_reason(None)
            return {"ok": True, "action": "resume_session", "now_playing": state.NOW_PLAYING, **_control_ack_payload(resume_result)}

        # Fallback: re-resolve/play via play_item
        resumed = player.play_item(
            now,
            use_resolver=True,
            cec=False,
            clear_queue=False,
            mode="resume",
            start_pos=(float(pos) if pos is not None else None),
        )
        resumed["closed"] = False
        state.set_now_playing(resumed)
        state.set_session_state("playing")
        state.set_pause_reason(None)
        return {"ok": True, "action": "resume_session", "now_playing": state.NOW_PLAYING}

    # Else: play next queue item.
    try:
        handoff = player.advance_queue_playback(mode="play_next", prefer_playlist_next=False)
    except player.QueueAdvanceEmptyError:
        raise HTTPException(status_code=400, detail="Queue is empty")
    return {"ok": True, "action": "play_next", "now_playing": handoff.get("now_playing")}


@router.post("/playback/toggle")
def playback_toggle():
    """
    Single button behavior:
      - If playing: toggle pause
      - If not playing: behave like /playback/play
    """
    if player.is_playing():
        cur = bool(player.mpv_get("pause"))
        target = not cur
        result = _control_result_or_raise(player.mpv_set_result("pause", target), action="toggle_pause")
        state.set_session_state("paused" if target else "playing")
        state.set_pause_reason("user" if target else None)
        return {"ok": True, "action": "toggle_pause", "paused": target, **_control_ack_payload(result)}
    return playback_play()


@router.post("/seek")
def seek(req: SeekReq):
    hold_sec = _seek_transition_hold_sec()
    state.AUTO_NEXT_SUPPRESS_UNTIL = max(float(getattr(state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0) or 0.0), time.time() + hold_sec)
    try:
        player._mark_playback_transition(hold_sec)
    except Exception:
        pass
    result = _seek_relative_result(float(req.sec))
    return {"ok": True, "seeked": req.sec, **_control_ack_payload(result)}


@router.post("/seek_abs")
def seek_abs(req: SeekAbsReq):
    hold_sec = _seek_transition_hold_sec()
    state.AUTO_NEXT_SUPPRESS_UNTIL = max(float(getattr(state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0) or 0.0), time.time() + hold_sec)
    try:
        player._mark_playback_transition(hold_sec)
    except Exception:
        pass
    result = _seek_absolute_result(float(req.sec))
    return {"ok": True, "seeked_to": req.sec, **_control_ack_payload(result)}


@router.post("/volume")
def volume(req: VolumeReq):
    if req.set is not None:
        v = max(0.0, min(200.0, float(req.set)))
        result = _control_result_or_raise(player.mpv_set_result("volume", v), action="volume")
        state.update_settings({"volume": v})
        return {"ok": True, "volume": v, **_control_ack_payload(result)}
    if req.delta is not None:
        cur = float(player.mpv_get("volume") or 0.0)
        v = max(0.0, min(200.0, cur + float(req.delta)))
        result = _control_result_or_raise(player.mpv_set_result("volume", v), action="volume")
        state.update_settings({"volume": v})
        return {"ok": True, "volume": v, **_control_ack_payload(result)}
    raise HTTPException(status_code=400, detail="Provide delta or set")


@router.post("/mute")
def mute(req: MuteReq):
    """Toggle mute or explicitly set it using mpv's native mute property."""
    try:
        cur = bool(player.mpv_get("mute"))
    except Exception:
        cur = False
    target = (not cur) if req.set is None else bool(req.set)
    try:
        result = _control_result_or_raise(player.mpv_set_result("mute", target), action="mute")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"mute failed: {e}") from e
    return {"ok": True, "mute": target, **_control_ack_payload(result)}


@router.get("/playback/state")
def playback_state():
    """Lightweight playback state for overlay/browser polling."""
    return _playback_state_fast_snapshot()
