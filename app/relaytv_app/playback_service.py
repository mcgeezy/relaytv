# SPDX-License-Identifier: GPL-3.0-only
"""Playback transition service (Phase 3, docs/ARCHITECTURE_PHASE_3_ROADMAP.md).

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
docs/ARCHITECTURE_PHASE_3_TRANSITION_INVENTORY.md). Delegation goes through
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


def advance_queue(
    *,
    mode: str,
    prefer_playlist_next: bool = False,
    poll_sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Advance playback to the next queue item."""
    return player.advance_queue_playback(
        mode=mode,
        prefer_playlist_next=prefer_playlist_next,
        poll_sleep=poll_sleep,
    )


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
