# SPDX-License-Identifier: GPL-3.0-only
import time
import uuid

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .. import player, playback_service, state


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


class ShareReq(BaseModel):
    """Payload for the share target's authenticated POST."""

    url: str | None = None
    link: str | None = None
    cec: bool = True


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


def _annotate_upload_item(item: object) -> object:
    from . import _annotate_upload_item as annotate_upload_item

    return annotate_upload_item(item)


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
    return playback_service.preserve_current_to_queue_front()


def _rollback_play_now_preserve(preserved: dict | None) -> None:
    rolled_back = playback_service.rollback_play_now_preserve(preserved)
    if rolled_back is None:
        return
    try:
        _ui_event_push_queue(
            "play_now_rollback",
            queue=rolled_back,
            queue_length=len(rolled_back),
            source="play_now",
        )
    except Exception:
        pass


def _discard_interrupted_playback_state(reason: str) -> None:
    from . import _discard_interrupted_playback_state as discard_interrupted_playback_state

    discard_interrupted_playback_state(reason)


def _stop_current_for_idle_or_desktop() -> bool:
    keep_qt_shell = bool(
        _idle_visual_surface_enabled_for_player()
        and getattr(player, "_qt_shell_backend_enabled", lambda: False)()
    )
    if keep_qt_shell:
        stopped_in_place = bool(playback_service.stop_keep_shell())
        if stopped_in_place:
            return True
    playback_service.stop_all(restart_splash=_idle_visual_surface_enabled_for_player())
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
    playback_service.suppress_auto_next(2.0)
    item = _smart_item_from_url(req.url or "")
    start_pos = item.get("resume_pos") if isinstance(item, dict) else None
    now = playback_service.play_now(
        item,
        use_resolver=req.use_ytdlp,
        cec=req.cec,
        clear_queue=True,
        mode="play",
        start_pos=(float(start_pos) if start_pos is not None else None),
    )
    return {"status": "playing", "now_playing": _annotate_upload_item(now)}


@router.post("/next")
def next_track():
    try:
        result = dict(playback_service.advance_queue(mode="next", prefer_playlist_next=True, poll_sleep=time.sleep))
    except playback_service.QueueAdvanceEmptyError:
        raise HTTPException(status_code=400, detail="Queue is empty")
    if result.get("method") == "dequeue_play_item":
        result.pop("method", None)
    if "now_playing" in result:
        result["now_playing"] = _annotate_upload_item(result.get("now_playing"))
    return result


def _play_now_item(
    item_or_text: dict[str, object] | str,
    *,
    request_url: str,
    preserve_current: bool = True,
    preserve_to: str = "queue_front",
    resume_current: bool = True,
    reason: str | None = None,
    title_hint: str | None = None,
    resume_pos: float | None = None,
) -> dict:
    """Run the play-now transition without flattening an existing item.

    Public ``PlayNowReq`` callers intentionally expose only generic media
    fields. Internal callers such as queue playback already own a trusted,
    server-built item and must retain provider metadata needed for play-time
    resolution (for example IPTV catalog references).
    """
    playback_service.suppress_auto_next(2.0)

    preserved = None
    if preserve_current and preserve_to == "queue_front" and resume_current:
        preserved = _preserve_current_to_queue_front()

    try:
        now = playback_service.play_now(
            item_or_text,
            use_resolver=True,
            cec=False,
            clear_queue=False,
            mode=(reason or "play_now"),
            start_pos=resume_pos,
        )
    except Exception as exc:
        if isinstance(exc, player.YouTubePostLiveProcessingError):
            try:
                handoff = playback_service.advance_queue(
                    mode="play_next",
                    prefer_playlist_next=False,
                )
                now = handoff.get("now_playing") if isinstance(handoff, dict) else None
            except playback_service.QueueAdvanceEmptyError:
                player._handle_playback_idle_no_queue()
                now = None
            with state.QUEUE_LOCK:
                qlen = len(state.QUEUE)
                queue_snapshot = list(state.QUEUE)
            _ui_event_push_queue(
                "play_now",
                queue=queue_snapshot,
                queue_length=qlen,
                source="post_live_skip",
            )
            return {
                "ok": True,
                "action": "skipped_post_live_processing",
                "now_playing": _annotate_upload_item(now),
                "preserved": _annotate_upload_item(preserved),
                "queue_length": qlen,
            }
        _rollback_play_now_preserve(preserved)
        if isinstance(exc, player.YouTubeBotCheckError):
            # The 400 detail only reaches the requesting client; the TV
            # otherwise drops back to idle with no explanation.
            try:
                _push_overlay_toast(
                    text=f"Can't play (YouTube bot check): {title_hint or request_url}",
                    duration=6.0,
                    level="warn",
                    icon="play",
                )
            except Exception:
                pass
        raise
    try:
        title = now.get("title") if isinstance(now, dict) else None
        _push_overlay_toast(
            text=f"Playing now: {title or title_hint or request_url}",
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
    return {
        "ok": True,
        "action": "played",
        "now_playing": _annotate_upload_item(now),
        "preserved": _annotate_upload_item(preserved),
        "queue_length": qlen,
    }


@router.post("/play_now")
def play_now(req: PlayNowReq):
    """Play immediately, optionally preserving the currently playing item."""
    item_or_text: dict[str, object] | str = req.url
    if (
        req.title
        or req.thumbnail
        or req.resume_pos is not None
        or req.history_id
        or req.resolved_stream
    ):
        item: dict[str, object] = {"url": req.url}
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
        item_or_text = item
    return _play_now_item(
        item_or_text,
        request_url=req.url,
        preserve_current=req.preserve_current,
        preserve_to=req.preserve_to,
        resume_current=req.resume_current,
        reason=req.reason,
        title_hint=req.title,
        resume_pos=req.resume_pos,
    )


@router.post("/play_temporary")
def play_temporary(req: PlayTemporaryReq):
    playback_service.suppress_auto_next(2.0)
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

    now = playback_service.play_now(req.url, use_resolver=True, cec=False, clear_queue=False, mode="play_temporary")
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
    return {"ok": True, "temporary_id": frame_id, "now_playing": _annotate_upload_item(now), "stack_depth": len(stack)}


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
            playback_service.play_now(req.url, use_resolver=True, cec=False, clear_queue=False, mode="play_at")
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


@router.post("/share")
def share(req: ShareReq):
    """Play a shared link immediately, clearing the queue.

    The share target itself is the GET below; this is the endpoint it hands
    off to, and the one API clients should call.
    """
    shared = (req.url or req.link or "").strip()
    if not shared:
        raise HTTPException(status_code=400, detail="Missing url or link")
    item = _smart_item_from_url(shared)
    start_pos = item.get("resume_pos") if isinstance(item, dict) else None
    now = playback_service.play_now(
        item,
        use_resolver=True,
        cec=req.cec,
        clear_queue=True,
        mode="share",
        start_pos=(float(start_pos) if start_pos is not None else None),
    )
    return {"status": "playing", "now_playing": _annotate_upload_item(now), "source": "share_target"}


@router.get("/share")
def share_target(url: str | None = None, link: str | None = None, cec: bool = True):
    """Hand a shared link to the UI instead of playing it.

    This is the PWA share target (``routes/assets.py``, web app manifest),
    which the Web Share Target API can only declare as a GET and which reaches
    us as a plain browser navigation carrying no ``Authorization`` header.

    It used to clear the queue and start playback directly. That made every
    install — token or not — drivable by any page the operator visited, since
    an ``<img src>`` or a prefetch issues the GET and the side effect lands
    even though the browser discards the response. Authenticating the route
    would not have helped: the share target cannot send a header, so it would
    simply have stopped working.

    Redirecting keeps the share target intact and moves the side effect onto
    the UI's authenticated JSON POST, which a cross-origin page cannot forge
    without a CORS preflight this app never answers. ``cec`` is accepted for
    URL compatibility; callers that need it should POST.
    """
    shared = (url or link or "").strip()
    if not shared:
        raise HTTPException(status_code=400, detail="Missing url or link query parameter")
    # 303 so the redirect is a GET regardless of how the share target arrived.
    return RedirectResponse(f"/ui?share={quote(shared, safe='')}", status_code=303)


@router.post("/smart")
def smart(req: PlayReq):
    playback_service.suppress_auto_next(2.0)
    if player.is_playing():
        item = _smart_item_from_url(req.url or "", lightweight=True)
        qlen, _queue_snapshot = playback_service.queue_item(item)
        try:
            _push_queue_added_toast_async(item, req.url or "item")
        except Exception:
            pass
        return {
            "status": "queued",
            "item": _annotate_upload_item(item),
            "queue_length": qlen,
            "now_playing": _annotate_upload_item(state.NOW_PLAYING),
        }

    item = _smart_item_from_url(req.url or "")
    start_pos = item.get("resume_pos") if isinstance(item, dict) else None
    now = playback_service.play_now(
        item,
        use_resolver=req.use_ytdlp,
        cec=req.cec,
        clear_queue=True,
        mode="smart_play",
        start_pos=(float(start_pos) if start_pos is not None else None),
    )
    return {"status": "playing", "now_playing": _annotate_upload_item(now)}


@router.post("/now_playing/clear")
def clear_now_playing():
    """Discard current now-playing item; advance queue or return to idle/desktop."""
    _discard_interrupted_playback_state("now_playing_clear")
    with state.QUEUE_LOCK:
        has_queue = bool(state.QUEUE)
    if has_queue:
        return _next_track()

    playback_service.suppress_auto_next(3600 * 24)
    with player.MPV_LOCK:
        stopped_in_place = _stop_current_for_idle_or_desktop()
    playback_service.clear_session()
    return {"status": "cleared", "resume_available": False, "kept_player_shell": bool(stopped_in_place)}


@router.post("/close")
def close():
    """Close the player but keep session resumable (queue preserved)."""
    result = playback_service.close_current(
        idle_surface_enabled=_idle_visual_surface_enabled_for_player(),
        keep_shell_allowed=bool(getattr(player, "_qt_shell_backend_enabled", lambda: False)()),
    )
    preserve_resume = bool(result["preserve_resume"])
    pos = result["position"]
    if not result["stopped_in_place"]:
        _ensure_notification_surface(wait_for_subscriber=False)
    if preserve_resume:
        _jellyfin_emit_stopped_hint(pos, result["duration"])
    return {
        "status": ("closed" if preserve_resume else "idle"),
        "resume_available": bool(preserve_resume and state.NOW_PLAYING),
        "position": pos,
        "kept_player_shell": bool(result["stopped_in_place"]),
    }


@router.post("/resume/clear")
def clear_resumable_session():
    """Clear retained now-playing/resume state and return to idle."""
    playback_service.suppress_auto_next(3600 * 24)
    _discard_interrupted_playback_state("resume_clear")
    with player.MPV_LOCK:
        playback_service.stop_all()
    playback_service.clear_session()
    return {"status": "cleared", "resume_available": False}


@router.post("/resume_session")
def resume_session():
    """Resume a previously closed session (best-effort)."""
    if getattr(state, "SESSION_STATE", "idle") != "closed":
        raise HTTPException(status_code=400, detail="No closed session to resume")
    if not state.NOW_PLAYING:
        raise HTTPException(status_code=400, detail="No item to resume")

    _resumed, resume_result = playback_service.resume_session()
    return {"status": "resumed", "now_playing": _annotate_upload_item(state.NOW_PLAYING), **_control_ack_payload(resume_result)}


@router.post("/stop")
def stop():
    """User stop with resume support; always return to idle visuals."""
    stop_hint_now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    emit_stopped_hint = isinstance(stop_hint_now, dict) and bool(stop_hint_now.get("jellyfin_item_id"))

    result = playback_service.stop_current()
    pos = result["position"]
    if result["preserve_resume"]:
        _jellyfin_emit_stopped_hint(pos, result["duration"])
        return {"status": "stopped", "resume_available": bool(state.NOW_PLAYING), "position": pos}

    if emit_stopped_hint:
        _jellyfin_emit_stopped_hint(pos, result["duration"])
    return {"status": ("stopped" if emit_stopped_hint else "idle"), "resume_available": False, "position": pos}


@router.post("/pause")
def pause():
    result = _control_result_or_raise(player.mpv_set_result("pause", True), action="pause")
    playback_service.mark_paused(True)
    return {"ok": True, "paused": True, **_control_ack_payload(result)}


@router.post("/resume")
def resume():
    result = _control_result_or_raise(player.mpv_set_result("pause", False), action="resume")
    playback_service.mark_paused(False)
    return {"ok": True, "paused": False, **_control_ack_payload(result)}


@router.post("/toggle_pause")
def toggle_pause():
    cur = bool(player.mpv_get("pause"))
    target = not cur
    result = _control_result_or_raise(player.mpv_set_result("pause", target), action="toggle_pause")
    playback_service.mark_paused(target)
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
        playback_service.mark_paused(target)
        return {
            "ok": True,
            "action": ("pause" if target else "resume"),
            "paused": target,
            "now_playing": _annotate_upload_item(state.NOW_PLAYING),
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
        playback_service.suppress_auto_next(2.0)

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
            playback_service.mark_resumed_now_playing(resumed)
            return {
                "ok": True,
                "action": "resume_session",
                "now_playing": _annotate_upload_item(state.NOW_PLAYING),
                **_control_ack_payload(resume_result),
            }

        # Fallback: re-resolve/play via play_item
        resumed = playback_service.play_now(
            now,
            use_resolver=True,
            cec=False,
            clear_queue=False,
            mode="resume",
            start_pos=(float(pos) if pos is not None else None),
        )
        resumed["closed"] = False
        playback_service.mark_resumed_now_playing(resumed)
        return {"ok": True, "action": "resume_session", "now_playing": _annotate_upload_item(state.NOW_PLAYING)}

    # Else: play next queue item.
    try:
        handoff = playback_service.advance_queue(mode="play_next", prefer_playlist_next=False)
    except playback_service.QueueAdvanceEmptyError:
        raise HTTPException(status_code=400, detail="Queue is empty")
    return {"ok": True, "action": "play_next", "now_playing": _annotate_upload_item(handoff.get("now_playing"))}


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
        playback_service.mark_paused(target)
        return {"ok": True, "action": "toggle_pause", "paused": target, **_control_ack_payload(result)}
    return playback_play()


@router.post("/seek")
def seek(req: SeekReq):
    hold_sec = _seek_transition_hold_sec()
    playback_service.suppress_auto_next(hold_sec, extend_only=True)
    try:
        player._mark_playback_transition(hold_sec)
    except Exception:
        pass
    result = _seek_relative_result(float(req.sec))
    return {"ok": True, "seeked": req.sec, **_control_ack_payload(result)}


@router.post("/seek_abs")
def seek_abs(req: SeekAbsReq):
    hold_sec = _seek_transition_hold_sec()
    playback_service.suppress_auto_next(hold_sec, extend_only=True)
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
