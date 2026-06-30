# SPDX-License-Identifier: GPL-3.0-only
import time

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
