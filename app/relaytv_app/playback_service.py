# SPDX-License-Identifier: GPL-3.0-only
"""Playback transition service (docs/ARCHITECTURE.md).

Explicit command vocabulary for playback transitions (architecture review
Finding 4). Facade-first: each command delegates 1:1 to the current
implementation so behavior is provably unchanged; later milestones move the
policy itself behind these commands (M3 auto-next suppression, M4 temporary
playback stack, M5 close/resume semantics, M6 queue advancement policy).

Review command mapping:

- ``play_now``          -> ``player.play_item`` (play / play-now / smart /
                           share / resume start paths)
- ``queue_item``        -> queue append + persist + prefetch/prime
- ``advance_queue``     -> ``player.advance_queue_playback``
- ``stop_all``          -> ``player.stop_mpv``
- ``stop_keep_shell``   -> ``player.stop_playback_keep_qt_shell``
- ``suppress_auto_next`` / ``clear_auto_next_suppression`` -> the
  ``state.AUTO_NEXT_SUPPRESS_UNTIL`` write API (all writers migrate in M3)
- ``close_current``, ``resume_session``, ``natural_end`` arrive with M5/M6
  when their implementations move out of the routes package and player.

The Phase 3 end state is that this module is the only writer of the playback
transition globals outside ``state.py`` (see
docs/TRANSITION_INVENTORY.md). Delegation goes through
module attributes (``player.play_item``) on purpose: tests monkeypatch those
attributes and must keep intercepting the calls.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from . import player, state
from .debug import get_logger

logger = get_logger("playback")

# The queue-advance outcome vocabulary routes catch; defined by the player's
# advance mechanics, re-exported so callers need only the service.
QueueAdvanceEmptyError = player.QueueAdvanceEmptyError
QueueAdvanceSuppressedError = player.QueueAdvanceSuppressedError


def play_now(
    item_or_text,
    use_resolver: bool,
    cec: bool,
    clear_queue: bool,
    mode: str,
    start_pos: float | None = None,
):
    """Start playing an item or raw URL immediately.

    ``start_pos`` is forwarded only when set: ``None`` is ``play_item``'s own
    default, and existing test doubles for ``player.play_item`` don't all
    accept the parameter.
    """
    kwargs: dict[str, Any] = {
        "use_resolver": use_resolver,
        "cec": cec,
        "clear_queue": clear_queue,
        "mode": mode,
    }
    if start_pos is not None:
        kwargs["start_pos"] = start_pos
    return player.play_item(item_or_text, **kwargs)


def queue_item(item: dict) -> tuple[int, list[dict]]:
    """Append an item to the queue, persist, and warm the handoff caches.

    Returns ``(queue_length, queue_snapshot)`` for the caller's response and
    UI events; presentation (toasts, UI event push) stays with the caller.
    """
    with state.QUEUE_LOCK:
        state.QUEUE.append(item)
        qlen = len(state.QUEUE)
        snapshot = list(state.QUEUE)
    state.persist_queue()
    try:
        player.prefetch_queue_item_stream(item)
    except Exception:
        pass
    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    return qlen, snapshot


def queue_items(items: list[dict]) -> tuple[int, list[dict]]:
    """Append several items at once, persisting and warming caches one time.

    Bulk transfers (a peer sending its queue) would otherwise rewrite
    ``queue.json`` and re-prime mpv's up-next per item, which is slow enough on
    a Pi to time out the sender while the import actually succeeds.
    """
    if not items:
        with state.QUEUE_LOCK:
            return len(state.QUEUE), list(state.QUEUE)
    with state.QUEUE_LOCK:
        state.QUEUE.extend(items)
        qlen = len(state.QUEUE)
        snapshot = list(state.QUEUE)
    state.persist_queue()
    # Only the head can become the next handoff, so prefetching the rest here
    # buys nothing; the queue worker warms them as they approach the front.
    try:
        player.prefetch_queue_item_stream(snapshot[0] if snapshot else items[0])
    except Exception:
        pass
    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    return qlen, snapshot


def queue_item_next(item: dict) -> tuple[int, list[dict]]:
    """Insert an item at the front of the queue and warm the handoff caches."""
    with state.QUEUE_LOCK:
        state.QUEUE.insert(0, item)
        qlen = len(state.QUEUE)
        snapshot = list(state.QUEUE)
    state.persist_queue()
    try:
        player.prefetch_queue_item_stream(item)
    except Exception:
        pass
    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    return qlen, snapshot


def advance_queue(
    *,
    mode: str,
    prefer_playlist_next: bool = False,
    poll_sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Advance playback to the next queue item.

    ``poll_sleep`` is forwarded only when set (``None`` is the callee default
    and existing test doubles assert the exact kwargs they receive).
    """
    kwargs: dict[str, Any] = {"mode": mode, "prefer_playlist_next": prefer_playlist_next}
    if poll_sleep is not None:
        kwargs["poll_sleep"] = poll_sleep
    return player.advance_queue_playback(**kwargs)


def natural_end() -> str:
    """Handle a confirmed natural end of playback (Phase 3 M6).

    Called by the player's autoplay monitor once its telemetry confirms
    playback ended without user intent: advance the queue if there is one,
    otherwise transition to the idle surface. Detection (idle confirmation,
    transition guards, suppression window) stays with the monitor; this is
    the decision policy. Returns the action taken, for logging/tests.
    """
    # Record why this item ended before deciding what happens next. The
    # queue-empty handler below also does it, but that branch is only reached
    # with an empty queue — so playlist and queue users, the common case, got no
    # diagnostic at all, and the successor's play_item then cleared it.
    try:
        player.note_playback_failure_if_no_progress(
            state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
        )
    except Exception:
        pass

    with state.QUEUE_LOCK:
        has_queue = bool(state.QUEUE)
    if not has_queue:
        # Avoid flashing idle above video during native handoff gaps.
        if player.playback_transitioning() or player.auto_next_transitioning():
            return "hold_transition"
        if player._session_already_idle_without_queue():
            return "already_idle"
        player._handle_playback_idle_no_queue()
        return "idle"

    player._set_auto_next_transition(True)
    try:
        advance_queue(mode="auto_next", prefer_playlist_next=True)
        return "advanced"
    except QueueAdvanceEmptyError:
        player._handle_playback_idle_no_queue()
        return "idle"
    except QueueAdvanceSuppressedError:
        return "suppressed"
    except Exception as exc:
        logger.warning("auto_next_failed error=%s", exc)
        suppress_auto_next(3.0)
        return "failed"
    finally:
        player._set_auto_next_transition(False)


def stop_all(*, restart_splash: bool | None = None) -> None:
    """Stop the playback process (optionally restoring the splash/idle surface).

    ``restart_splash`` is forwarded only when the caller specifies it, both to
    keep ``player.stop_mpv``'s own default authoritative and because existing
    test doubles for it don't all accept the parameter.
    """
    if restart_splash is None:
        player.stop_mpv()
    else:
        player.stop_mpv(restart_splash=restart_splash)


def stop_keep_shell() -> bool:
    """Stop playback while keeping the Qt shell alive for the idle surface."""
    return player.stop_playback_keep_qt_shell()


def suppress_auto_next(seconds: float, *, extend_only: bool = False) -> None:
    """Suppress queue auto-advance for ``seconds`` from now.

    ``extend_only`` keeps a longer existing suppression window instead of
    shortening it (the advance-handoff guard semantics in player.py).
    """
    until = time.time() + float(seconds)
    if extend_only:
        until = max(float(state.AUTO_NEXT_SUPPRESS_UNTIL or 0.0), until)
    state.AUTO_NEXT_SUPPRESS_UNTIL = until


def clear_auto_next_suppression() -> None:
    """Allow queue auto-advance again immediately."""
    state.AUTO_NEXT_SUPPRESS_UNTIL = 0.0


# --- close / resume semantics (Phase 3 M5) ---------------------------------
#
# Transition cores moved from routes; HTTP guards, response shaping
# (_control_ack_payload), UI events, and the Jellyfin stopped hint stay with
# the route handlers.


def can_preserve_closed_session() -> bool:
    """Return True only when user stop/close should keep a resumable item."""
    try:
        if bool(getattr(player, "native_qt_playback_explicitly_ended", lambda: False)()):
            return False
    except Exception:
        pass
    try:
        return bool(player.is_playing()) and isinstance(state.NOW_PLAYING, dict)
    except Exception:
        return False


def preserve_current_to_queue_front() -> dict | None:
    """If something is playing, capture it and insert at front of queue with resume_pos."""
    if not player.is_playing():
        return None
    now = state.NOW_PLAYING
    if not isinstance(now, dict):
        return None
    with state.QUEUE_LOCK:
        queue_head = state.QUEUE[0] if state.QUEUE else None
    if isinstance(queue_head, dict) and queue_head.get("_relaytv_interrupt_preserved") is True:
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

    iptv_sid = str(now.get("iptv_source_id") or "").strip()
    iptv_cid = str(now.get("iptv_channel_id") or "").strip()
    is_iptv = str(now.get("provider") or "").strip().lower() == "iptv" and bool(iptv_sid) and bool(iptv_cid)

    preserved = {
        # IPTV keeps only the opaque catalog reference; the credential-bearing
        # stream URL and headers are re-resolved from the catalog at replay time.
        "url": f"https://iptv.invalid/{iptv_sid}/{iptv_cid}" if is_iptv else url.strip(),
        "title": now.get("title") or ("IPTV channel" if is_iptv else url.strip()),
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
    if is_iptv:
        # Carry the opaque references so replay re-resolves the stream + headers.
        preserved["iptv_source_id"] = iptv_sid
        preserved["iptv_channel_id"] = iptv_cid
    else:
        # Cache the resolved stream for non-IPTV so resume avoids re-resolving.
        # (Skipped for IPTV so no credential-bearing stream is stored on disk.)
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
        logger.warning("queue_persist_failed route=play_now_preserve")
    return preserved


def handoff_snapshot() -> dict[str, Any] | None:
    """Capture what is playing, for handing playback to another device.

    Read-only: it does not touch playback state. Returns ``None`` when there is
    nothing to hand off. IPTV sessions are excluded deliberately — their stream
    URLs are re-resolved from a local catalog the other device does not have.
    """
    if not player.is_playing():
        return None
    now = state.NOW_PLAYING
    if not isinstance(now, dict):
        return None
    url = now.get("url")
    if not isinstance(url, str) or not url.strip():
        return None
    if str(now.get("provider") or "").strip().lower() == "iptv":
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
        position = float(pos) if pos is not None else None
    except Exception:
        position = None
    try:
        duration = float(dur) if dur is not None else None
    except Exception:
        duration = None
    return {"item": dict(now), "position": position, "duration": duration}


def rollback_play_now_preserve(preserved: dict | None) -> list[dict] | None:
    """Remove a preserved queue-front item after a failed play-now.

    Returns the post-rollback queue snapshot when something was removed (the
    caller pushes the UI event), else None.
    """
    if preserved is None:
        return None
    removed = False
    with state.QUEUE_LOCK:
        if state.QUEUE and state.QUEUE[0] is preserved:
            state.QUEUE.pop(0)
            removed = True
        elif state.QUEUE and state.QUEUE[0] == preserved:
            state.QUEUE.pop(0)
            removed = True
        if not removed:
            return None
        snapshot = {"queue": list(state.QUEUE), "saved_at": int(time.time())}
    try:
        state.persist_queue_payload(snapshot)
    except Exception:
        logger.warning("queue_persist_failed route=play_now_preserve_rollback")
    return list(snapshot["queue"])


def resume_paused_in_place() -> dict[str, Any] | None:
    """Unpause the current paused session in place.

    Returns the successful mpv control result (for the caller's ack payload),
    or None when there is nothing to resume or the unpause failed.
    """
    sess = str(getattr(state, "SESSION_STATE", "idle") or "idle").strip().lower()
    now = getattr(state, "NOW_PLAYING", None)
    if sess != "paused" or not isinstance(now, dict):
        return None

    try:
        result = player.mpv_set_result("pause", False)
    except Exception:
        return None
    if not isinstance(result, dict) or result.get("error") != "success":
        return None

    resumed = dict(now)
    resumed["closed"] = False
    suppress_auto_next(2.0)
    state.set_now_playing(resumed)
    state.set_session_state("playing")
    state.set_pause_reason(None)
    return result


def close_current(*, idle_surface_enabled: bool, keep_shell_allowed: bool) -> dict[str, Any]:
    """Close the player, retaining a resumable session when possible.

    ``idle_surface_enabled`` and ``keep_shell_allowed`` are the caller's
    visual-surface decisions (idle dashboard/notification config and Qt shell
    backend availability) — surface policy stays out of the service. Returns
    the transition outcome; response shaping and notification/Jellyfin side
    effects stay with the caller.
    """
    # Prevent the autoplay worker from immediately advancing.
    suppress_auto_next(3600 * 24)
    discard_interrupted_playback_state("close")

    pos = None
    dur = None
    preserve_resume = can_preserve_closed_session() or isinstance(state.NOW_PLAYING, dict)
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
    keep_qt_shell = bool(preserve_resume and idle_surface_enabled and keep_shell_allowed)
    stopped_in_place = False
    if keep_qt_shell:
        with player.MPV_LOCK:
            stopped_in_place = bool(stop_keep_shell())
    if not stopped_in_place:
        with player.MPV_LOCK:
            stop_all(restart_splash=idle_surface_enabled)

    if preserve_resume:
        player.update_history_progress(
            state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None,
            position_sec=pos,
            duration_sec=dur,
            force=True,
        )
    return {
        "preserve_resume": preserve_resume,
        "position": pos,
        "duration": dur,
        "stopped_in_place": stopped_in_place,
    }


def resume_session() -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Resume the closed session (best-effort).

    The caller validates that a closed session with an item exists. Returns
    ``(resumed_now_playing, resume_control_result_or_None)``.
    """
    now = state.NOW_PLAYING
    suppress_auto_next(2.0)

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
        resumed = play_now(
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
            http_headers = player._item_http_headers(now)
            header_kwargs = {"http_headers": http_headers} if http_headers else {}
            if not player._load_stream_in_existing_mpv(
                stream_url,
                audio_url=audio_url,
                start_pos=start_pos,
                **header_kwargs,
            ):
                player.start_mpv(
                    stream_url,
                    audio_url=audio_url,
                    start_pos=start_pos,
                    **header_kwargs,
                )
        resumed = dict(now)
        resumed["started"] = int(time.time())
        resumed["mode"] = "resume"
        resumed["closed"] = False
        state.set_now_playing(resumed)
        state.set_session_state("playing")

    resume_result: dict[str, Any] | None = None
    if start_pos is not None:
        try:
            result = player.mpv_set_result("pause", False)
            if isinstance(result, dict) and result.get("error") == "success":
                resume_result = dict(result)
        except Exception:
            resume_result = None

    return resumed, resume_result


def clear_session() -> None:
    """Reset the session to idle: no current item, no resume position."""
    state.set_now_playing(None)
    state.set_session_position(None)
    state.set_session_state("idle")
    try:
        state.persist_queue()
    except Exception:
        pass


def mark_paused(paused: bool, *, reason: str | None = None) -> None:
    """Record a user pause/unpause of the current session.

    ``reason`` preserves a pre-existing pause reason across an in-place
    restart (Jellyfin track switches); it defaults to a user pause.
    """
    state.set_session_state("paused" if paused else "playing")
    state.set_pause_reason((reason if reason is not None else "user") if paused else None)


def update_now_playing(now: dict) -> None:
    """Refresh the current item's metadata in place — not a transition.

    Used after in-place stream/track changes so NOW_PLAYING writes stay
    behind service commands.
    """
    state.set_now_playing(now)


def mark_resumed_now_playing(resumed: dict) -> None:
    """Record a successfully resumed item as the playing session."""
    state.set_now_playing(resumed)
    state.set_session_state("playing")
    state.set_pause_reason(None)


def stop_current() -> dict[str, Any]:
    """User stop with resume support; always returns to idle visuals.

    Mirrors ``close_current``'s split: the transition core lives here, the
    caller owns response shaping and the Jellyfin stopped hint.
    """
    suppress_auto_next(3600 * 24)
    discard_interrupted_playback_state("stop")

    pos = None
    dur = None
    preserve_resume = can_preserve_closed_session()
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
            stop_all()
        player.update_history_progress(
            state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None,
            position_sec=pos,
            duration_sec=dur,
            force=True,
        )
        return {"preserve_resume": True, "position": pos, "duration": dur}

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
        stop_all()
    return {"preserve_resume": False, "position": pos, "duration": dur}


# --- temporary playback (picture-in-place interruptions with restore) ------
#
# Moved from routes/__init__.py in Phase 3 M4. The stack holds restore frames
# for /play_temporary; completing (or timing out / ending) the top frame
# restores the captured session. routes/__init__.py keeps aliases to the same
# objects for compatibility with existing callers and tests.

_TEMP_PLAYBACK_LOCK = threading.Lock()
_TEMP_PLAYBACK_STACK: list[dict] = []


def discard_temporary_playback(reason: str) -> int:
    """Discard temporary restore frames after an explicit user stop/close."""
    with _TEMP_PLAYBACK_LOCK:
        count = len(_TEMP_PLAYBACK_STACK)
        _TEMP_PLAYBACK_STACK.clear()
    if count:
        try:
            logger.info("temporary_playback_discarded reason=%s count=%s", reason, count)
        except Exception:
            pass
    return count


def discard_interrupted_playback_state(reason: str) -> None:
    discard_temporary_playback(reason)


def capture_current_playback_state() -> dict | None:
    if not player.is_playing() or not isinstance(state.NOW_PLAYING, dict):
        return None

    with player.MPV_LOCK:
        props = player.mpv_get_many(["time-pos", "pause"])
    pos = props.get("time-pos")
    paused = bool(props.get("pause"))
    try:
        pos_f = float(pos) if pos is not None else None
    except Exception:
        pos_f = None

    with state.QUEUE_LOCK:
        queue_snapshot = list(state.QUEUE)

    return {
        "now_playing": dict(state.NOW_PLAYING),
        "position": pos_f,
        "paused": paused,
        "queue": queue_snapshot,
    }


def restore_playback_state(snapshot: dict) -> None:
    now = snapshot.get("now_playing") if isinstance(snapshot, dict) else None
    if not isinstance(now, dict):
        return

    with state.QUEUE_LOCK:
        state.QUEUE[:] = list(snapshot.get("queue") or [])
    state.persist_queue()

    start_pos = snapshot.get("position")
    resumed = play_now(
        now,
        use_resolver=True,
        cec=False,
        clear_queue=False,
        mode="temporary_resume",
        start_pos=(float(start_pos) if start_pos is not None else None),
    )
    if snapshot.get("paused"):
        try:
            player.mpv_set("pause", True)
            state.set_session_state("paused")
            state.set_pause_reason("temporary")
        except Exception:
            pass
    else:
        state.set_session_state("playing")
        state.set_pause_reason(None)
    state.set_now_playing(resumed)


def complete_temporary_playback(frame_id: str, reason: str) -> bool:
    with _TEMP_PLAYBACK_LOCK:
        if not _TEMP_PLAYBACK_STACK or _TEMP_PLAYBACK_STACK[-1].get("id") != frame_id:
            return False
        frame = _TEMP_PLAYBACK_STACK.pop()

    if frame.get("resume") and isinstance(frame.get("snapshot"), dict):
        try:
            restore_playback_state(frame["snapshot"])
        except Exception as e:
            logger.warning("temporary_restore_failed frame_id=%s error=%s", frame_id, e)
            return False
    return True


def temporary_watchdog(frame_id: str, timeout_sec: float | None) -> None:
    started = time.time()
    while True:
        time.sleep(0.25)
        with _TEMP_PLAYBACK_LOCK:
            is_top = bool(_TEMP_PLAYBACK_STACK) and _TEMP_PLAYBACK_STACK[-1].get("id") == frame_id
        if not is_top:
            return
        if timeout_sec is not None and (time.time() - started) >= float(timeout_sec):
            complete_temporary_playback(frame_id, reason="timeout")
            return
        if not player.is_playing():
            complete_temporary_playback(frame_id, reason="ended")
            return
