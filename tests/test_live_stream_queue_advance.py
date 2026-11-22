# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import pytest

from relaytv_app import playback_service, player, routes, state
from relaytv_app.resolver import ResolvedStreams
from relaytv_app.routes import playback as playback_routes


def _install_memory_queue_and_history(
    monkeypatch: pytest.MonkeyPatch,
    *,
    now_playing: dict | None,
    queue_items: list[dict],
) -> tuple[list[dict], dict[str, dict]]:
    persisted_queues: list[dict] = []
    history_records: dict[str, dict] = {}
    if isinstance(now_playing, dict) and now_playing.get("history_id"):
        history_records[str(now_playing["history_id"])] = {
            "history_id": str(now_playing["history_id"]),
            "url": now_playing.get("url"),
            "title": now_playing.get("title"),
            "resume_pos": float(now_playing.get("resume_pos") or 0.0),
            "completed": False,
        }

    monkeypatch.setattr(state, "NOW_PLAYING", now_playing, raising=False)
    monkeypatch.setattr(state, "SESSION_STATE", "playing", raising=False)
    monkeypatch.setattr(state, "SESSION_POSITION", 0.0, raising=False)
    monkeypatch.setattr(state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0, raising=False)
    monkeypatch.setattr(state, "QUEUE", list(queue_items), raising=False)
    monkeypatch.setattr(state, "persist_queue", lambda: None)
    monkeypatch.setattr(
        state,
        "persist_queue_payload",
        lambda payload: persisted_queues.append(dict(payload)),
    )
    monkeypatch.setattr(state, "persist_session", lambda: None)
    monkeypatch.setattr(
        state,
        "history_update",
        lambda hid, updates: (
            history_records.setdefault(str(hid), {}).update(updates) is None
        ),
    )
    monkeypatch.setattr(state, "history_contains", lambda hid: str(hid) in history_records)
    monkeypatch.setattr(
        state,
        "history_add",
        lambda entry: history_records.__setitem__(str(entry["history_id"]), dict(entry)),
    )
    return persisted_queues, history_records


@pytest.mark.parametrize("end_signal", ["native_qt_ended", "qt_eof_props", "runtime_dead"])
def test_dvr_live_stream_joined_mid_broadcast_advances_preserved_queue_on_eof(
    monkeypatch: pytest.MonkeyPatch,
    end_signal: str,
) -> None:
    """Reproduces a mid-broadcast DVR live stream ending with an interrupt-preserved queue head."""
    now_ts = 1_795_314_303.0
    monkeypatch.setattr(player.time, "time", lambda: now_ts)
    monkeypatch.setattr(
        player,
        "_LIVE_STREAM_STALL_STATE",
        {
            "identity": None,
            "time_pos": None,
            "since_monotonic": 0.0,
        },
        raising=False,
    )

    live_now = {
        "url": "https://rumble.com/v7ipa8i-live.html",
        "title": "Mid-broadcast DVR live stream",
        "provider": "rumble",
        "stream": "https://rumble.com/v7ipa8i-live.html",
        "started": int(now_ts - 1790.0),
        "_playback_started_pos": 0.0,
        "resume_pos": 6290.819,
        "duration_sec": 6290.819,
        "is_live": True,
        "live_status": "is_live",
        "history_id": "hist-live-rumble",
    }
    preserved_next = {
        "url": "https://www.youtube.com/watch?v=pn3qVRCI1_E",
        "title": "Preserved queued video",
        "provider": "youtube",
        "resume_pos": 140.0,
        "_relaytv_interrupt_preserved": True,
    }
    _persisted, history_records = _install_memory_queue_and_history(
        monkeypatch,
        now_playing=live_now,
        queue_items=[dict(preserved_next)],
    )
    monkeypatch.setattr(player, "playback_transitioning", lambda: False)
    monkeypatch.setattr(player, "auto_next_transitioning", lambda: False)
    monkeypatch.setattr(player, "_attempt_playlist_next_handoff", lambda **_: None)

    if end_signal == "native_qt_ended":
        monkeypatch.setattr(player, "_is_playing", lambda: True)
        monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: True)
        monkeypatch.setattr(player, "native_qt_playback_explicitly_ended", lambda: True)
        assert player._playback_runtime_idle_or_ended() is True
    elif end_signal == "qt_eof_props":
        monkeypatch.setattr(player, "_is_playing", lambda: True)
        monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: True)
        monkeypatch.setattr(player, "native_qt_playback_explicitly_ended", lambda: False)
        monkeypatch.setenv("RELAYTV_PLAYBACK_IDLE_CONFIRM_SEC", "0.5")
        monkeypatch.setattr(
            player,
            "mpv_get_many",
            lambda props, **_kw: {
                "core-idle": True,
                "eof-reached": True,
                "pause": False,
                "path": "",
                "time-pos": 6290.819,
                "duration": 6290.819,
            },
        )
        monkeypatch.setattr(player, "_PLAYBACK_IDLE_CANDIDATE_SINCE", 0.0, raising=False)
        assert player._playback_runtime_idle_or_ended() is False
        now_ts += 1.0
        assert player._playback_runtime_idle_or_ended() is True
    else:
        monkeypatch.setattr(player, "_is_playing", lambda: False)
        monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: False)
        monkeypatch.setenv("RELAYTV_PLAYBACK_RUNTIME_GAP_CONFIRM_SEC", "0.5")
        monkeypatch.setattr(player, "_PLAYBACK_IDLE_CANDIDATE_SINCE", 0.0, raising=False)
        assert player._playback_runtime_idle_or_ended() is False
        now_ts += 1.0
        assert player._playback_runtime_idle_or_ended() is True

    played_items: list[tuple[dict, float | None, str]] = []

    def fake_play_item(item, use_resolver, cec, clear_queue, mode, start_pos=None, **_kw):
        played_items.append((dict(item), start_pos, mode))
        next_now = {
            "url": item["url"],
            "title": item["title"],
            "provider": item["provider"],
            "started": int(now_ts),
            "mode": mode,
            "resume_pos": float(start_pos or 0.0),
        }
        state.update_session(
            now_playing=next_now,
            session_state="playing",
            pause_reason=None,
            session_position=float(start_pos or 0.0),
            persist=False,
        )
        return next_now

    monkeypatch.setattr(player, "play_item", fake_play_item)

    outcome = playback_service.natural_end()

    assert outcome == "advanced"
    assert len(played_items) == 1
    assert played_items[0][0]["url"] == preserved_next["url"]
    assert played_items[0][1] == 140.0
    assert played_items[0][2] == "auto_next"
    assert state.QUEUE == []
    assert history_records["hist-live-rumble"]["completed"] is True
    assert history_records["hist-live-rumble"]["resume_pos"] == 0.0


@pytest.mark.parametrize(
    "ended_item",
    [
        {
            "url": "https://www.twitch.tv/somecaster",
            "title": "Durationless Twitch live stream",
            "provider": "twitch",
            "stream": "https://www.twitch.tv/somecaster",
            "started": 1_000,
            "_playback_started_pos": 0.0,
            "resume_pos": 95.0,
            "duration_sec": None,
            "is_live": True,
            "history_id": "hist-durationless-live",
        },
        {
            "url": "https://www.youtube.com/watch?v=pn3qVRCI1_E",
            "title": "Post-live progressive relay",
            "provider": "youtube",
            "stream": "http://127.0.0.1:8787/postlive/tok123.mkv",
            "started": 1_000,
            "_playback_started_pos": 0.0,
            "resume_pos": 48.5,
            "duration_sec": None,
            "live_status": "post_live",
            "history_id": "hist-postlive-relay",
        },
    ],
)
def test_durationless_live_and_postlive_relay_complete_and_advance_queue(
    monkeypatch: pytest.MonkeyPatch,
    ended_item: dict,
) -> None:
    now_ts = 1_120.0
    monkeypatch.setattr(player.time, "time", lambda: now_ts)
    queued_next = {
        "url": "https://www.youtube.com/watch?v=nextitem123",
        "title": "Next queued item",
        "provider": "youtube",
    }
    _persisted, history_records = _install_memory_queue_and_history(
        monkeypatch,
        now_playing=dict(ended_item),
        queue_items=[dict(queued_next)],
    )
    monkeypatch.setattr(player, "playback_transitioning", lambda: False)
    monkeypatch.setattr(player, "auto_next_transitioning", lambda: False)
    monkeypatch.setattr(player, "_is_playing", lambda: True)
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: True)
    monkeypatch.setattr(player, "native_qt_playback_explicitly_ended", lambda: True)
    monkeypatch.setattr(player, "_attempt_playlist_next_handoff", lambda **_: None)
    monkeypatch.setattr(
        player,
        "play_item",
        lambda item, **_kw: {
            "url": item["url"],
            "title": item["title"],
            "provider": item["provider"],
            "started": int(now_ts),
        },
    )

    assert player._playback_runtime_idle_or_ended() is True
    assert playback_service.natural_end() == "advanced"
    assert state.QUEUE == []
    hid = str(ended_item["history_id"])
    assert history_records[hid]["completed"] is True
    assert history_records[hid]["resume_pos"] == 0.0


@pytest.mark.parametrize(
    "incomplete_item",
    [
        {
            "url": "https://example.com/broken.mp4",
            "title": "Broken unstarted VOD",
            "provider": "other",
            "started": 4_990,
            "_playback_started_pos": 0.0,
            "resume_pos": None,
            "duration_sec": None,
        },
        {
            "url": "https://www.twitch.tv/offlinecaster",
            "title": "Offline unstarted live stream",
            "provider": "twitch",
            "started": 4_950,
            "_playback_started_pos": 0.0,
            "resume_pos": None,
            "duration_sec": None,
            "is_live": True,
        },
        {
            "url": "https://plex.invalid/item",
            "title": "Crashed Plex Matroska transcode",
            "provider": "plex",
            "plex_item_id": "99",
            "plex_stream_mode": "transcode",
            "started": 4_980,
            "_playback_started_pos": 0.0,
            "resume_pos": 14.0,
            "duration_sec": None,
        },
        {
            "url": "https://www.youtube.com/live/archive123",
            "title": "Archived YouTube /live/ replay",
            "provider": "youtube",
            "started": 4_980,
            "_playback_started_pos": 0.0,
            "resume_pos": 20.0,
            "duration_sec": 7200.0,
            "is_live": True,
            "live_status": "was_live",
        },
        {
            "url": "https://iptv.invalid/src/ch1",
            "title": "24/7 IPTV channel drop",
            "provider": "iptv",
            "started": 4_800,
            "_playback_started_pos": 0.0,
            "resume_pos": 45.0,
            "duration_sec": None,
        },
    ],
)
def test_incomplete_or_non_completable_streams_never_falsely_report_completion(
    monkeypatch: pytest.MonkeyPatch,
    incomplete_item: dict,
) -> None:
    monkeypatch.setattr(player.time, "time", lambda: 5_000.0)
    assert player._runtime_gap_completion_plausible(incomplete_item) is False
    if hasattr(player, "_natural_end_item_completed"):
        assert player._natural_end_item_completed(incomplete_item) is False


def test_status_payload_demotes_explicitly_ended_live_stream_without_bypassing_autoplay_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ts = 6_000.0
    monkeypatch.setattr(player.time, "time", lambda: now_ts)
    live_now = {
        "url": "https://rumble.com/v7ipa8i-live.html",
        "title": "Ended live stream",
        "provider": "rumble",
        "is_live": True,
        "started": int(now_ts - 600.0),
        "resume_pos": 6290.8,
    }
    monkeypatch.setattr(state, "NOW_PLAYING", dict(live_now), raising=False)
    monkeypatch.setattr(state, "SESSION_STATE", "playing", raising=False)
    monkeypatch.setattr(state, "SESSION_POSITION", 6290.8, raising=False)
    monkeypatch.setattr(state, "QUEUE", [], raising=False)
    monkeypatch.setattr(state, "persist_session", lambda: None)
    monkeypatch.setattr(player, "is_playing", lambda: False)
    monkeypatch.setattr(player, "_is_playing", lambda: False)
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: True)
    monkeypatch.setattr(player, "startup_session_restore_pending", lambda: False)
    monkeypatch.setattr(player, "_qt_runtime_active", lambda **_: False)
    monkeypatch.setattr(player, "playback_transitioning", lambda: False)
    monkeypatch.setattr(player, "auto_next_transitioning", lambda: False)

    # During a transient live buffering gap (not explicitly ended), session stays "playing".
    monkeypatch.setattr(player, "native_qt_playback_explicitly_ended", lambda: False)
    buffering_payload = routes._status_payload()
    assert buffering_payload["state"] == "playing"
    assert state.SESSION_STATE == "playing"

    # Once Qt explicitly reports EOF + core-idle on a played live stream, /status reconciles to "idle".
    monkeypatch.setattr(player, "native_qt_playback_explicitly_ended", lambda: True)
    ended_payload = routes._status_payload()
    assert ended_payload["state"] == "idle"
    assert state.SESSION_STATE == "idle"

    # Conversely, if /status already demoted SESSION_STATE="idle" on an incomplete crashed VOD,
    # _playback_runtime_idle_or_ended() must still enforce _runtime_gap_completion_plausible().
    crashed_vod = {
        "url": "https://example.com/movie.mp4",
        "title": "Crashed VOD",
        "provider": "other",
        "started": int(now_ts - 20.0),
        "resume_pos": 15.0,
        "duration_sec": 3600.0,
    }
    monkeypatch.setattr(state, "NOW_PLAYING", crashed_vod, raising=False)
    monkeypatch.setattr(state, "SESSION_STATE", "idle", raising=False)
    assert player._playback_runtime_idle_or_ended() is False


def test_unclosed_hls_live_stream_stall_survives_mpv_cache_and_guards_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drives real mpv_get_many caching on ARM64 TTL so cache projection cannot mask stalls."""
    now_ts = 10_000.0
    mono_ts = 500.0
    stop_commands: list[list[object]] = []
    ipc_calls = 0

    monkeypatch.setattr(player.time, "time", lambda: now_ts)
    monkeypatch.setattr(player.time, "monotonic", lambda: mono_ts)
    monkeypatch.setenv("RELAYTV_MPV_POLL_CACHE_SEC", "1.0")
    monkeypatch.setattr(player, "_MPV_PROP_CACHE", {}, raising=False)
    monkeypatch.setattr(player, "_MPV_PROP_CACHE_TS", 0.0, raising=False)
    if hasattr(player, "_reset_live_stream_stall_state"):
        player._reset_live_stream_stall_state()

    live_now = {
        "url": "https://rumble.com/v7ipa8i-live.html",
        "title": "Unclosed HLS live stream",
        "provider": "rumble",
        "is_live": True,
        "started": int(now_ts - 600.0),
        "resume_pos": 1800.0,
        "duration_sec": 3600.0,
        "history_id": "hist-stalled-live",
    }
    monkeypatch.setattr(state, "SESSION_STATE", "playing", raising=False)
    monkeypatch.setattr(state, "NOW_PLAYING", dict(live_now), raising=False)
    monkeypatch.setattr(player, "playback_transitioning", lambda: False)
    monkeypatch.setattr(player, "auto_next_transitioning", lambda: False)
    monkeypatch.setattr(player, "_is_playing", lambda: True)
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: True)
    monkeypatch.setattr(player, "_qt_shell_runtime_preferred", lambda: False)
    monkeypatch.setattr(player, "_qt_shell_runtime_requires_live_mpv_ipc", lambda _p=None: False)
    monkeypatch.setattr(player, "native_qt_playback_explicitly_ended", lambda: False)
    monkeypatch.setattr(player, "_MPV_UPNEXT_ARMED_ID", "stale-upnext-id", raising=False)
    monkeypatch.setattr(
        player,
        "mpv_command",
        lambda cmd: stop_commands.append(list(cmd)) or {"error": "success"},
    )

    stalled_props = {
        "core-idle": True,
        "eof-reached": False,
        "pause": False,
        "path": "https://rumble.com/v7ipa8i-live.html",
        "time-pos": 1800.0,
        "duration": 3600.0,
    }

    def fake_ipc_many(payloads, timeout=1.5):
        nonlocal ipc_calls
        ipc_calls += 1
        return [
            {"error": "success", "data": stalled_props.get(p["command"][1])}
            for p in payloads
        ]

    monkeypatch.setattr(player, "_mpv_ipc_request_many", fake_ipc_many)

    # Even if another caller populates _MPV_PROP_CACHE, cached reads do not project time-pos
    # while core-idle=True, and stall polling bypasses cache projection.
    cached_sample = player.mpv_get_many(["core-idle", "pause", "time-pos", "duration"])
    assert cached_sample["time-pos"] == 1800.0
    now_ts += 0.4
    mono_ts += 0.4
    cached_sample_after = player.mpv_get_many(["core-idle", "pause", "time-pos", "duration"])
    assert cached_sample_after["time-pos"] == 1800.0

    # Tick at 0.25s cadence across cache TTL boundaries; transient buffering (< 45s) holds.
    for _ in range(80):  # 20.0s
        assert player._playback_runtime_idle_or_ended() is False
        now_ts += 0.25
        mono_ts += 0.25
    assert stop_commands == []

    # Advancing time-pos resets the stall timer.
    stalled_props["time-pos"] = 1802.0
    for _ in range(120):  # 30.0s after movement (< 45s)
        assert player._playback_runtime_idle_or_ended() is False
        now_ts += 0.25
        mono_ts += 0.25
    assert stop_commands == []

    # Reaching >= 45s continuous unpaused core-idle stops mpv and clears stale up-next state.
    for _ in range(61):  # +15.25s => 45.25s total stalled at 1802.0
        ended = player._playback_runtime_idle_or_ended()
        if ended:
            break
        now_ts += 0.25
        mono_ts += 0.25
    assert ended is True
    assert stop_commands == [["stop"]]
    assert player._MPV_UPNEXT_ARMED_ID == ""

    # Linear 24/7 IPTV channels never trigger live-end stall termination.
    stop_commands.clear()
    iptv_now = dict(live_now, provider="iptv")
    monkeypatch.setattr(state, "NOW_PLAYING", iptv_now, raising=False)
    for _ in range(200):
        assert player._playback_runtime_idle_or_ended() is False
        now_ts += 0.5
        mono_ts += 0.5
    assert stop_commands == []


def test_forward_seek_and_previous_restart_update_baseline_without_mutating_now_playing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ts = 20_000.0
    monkeypatch.setattr(player.time, "time", lambda: now_ts)

    vod_now = {
        "url": "https://www.youtube.com/watch?v=longmovie123",
        "title": "Two hour VOD",
        "provider": "youtube",
        "started": int(now_ts),
        "_playback_started_pos": 0.0,
        "resume_pos": 100.0,
        "duration_sec": 7200.0,
        "history_id": "hist-vod-seek",
    }
    initial_keys = set(vod_now.keys())
    monkeypatch.setattr(state, "NOW_PLAYING", vod_now, raising=False)
    monkeypatch.setattr(player, "_qt_shell_runtime_accepts_mpv_commands", lambda: True)
    monkeypatch.setattr(
        player,
        "mpv_get_many",
        lambda props, **_kw: {"time-pos": 100.0, "duration": 7200.0},
    )
    monkeypatch.setattr(player, "mpv_set_result", lambda prop, val: {"error": "success"})

    # Rapid +30s relative seeks accumulate from the active seek baseline rather than stale 100s.
    now_ts += 10.0
    for _ in range(4):
        res = routes._seek_relative_result(1750.0)
        assert res["error"] == "success"
    assert set(vod_now.keys()) == initial_keys  # No in-place dict key mutation on NOW_PLAYING!

    # Watching the remaining 100s (from 7100s to 7200s) is recognized as plausible completion.
    now_ts += 105.0
    vod_now["resume_pos"] = 7200.0
    assert player._runtime_gap_completion_plausible(vod_now) is True

    # Pressing /previous when pos > 5.0 seeks back to 0.0 and resets the seek baseline to 0.0.
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(player, "mpv_get", lambda prop: 7150.0 if prop == "time-pos" else None)
    monkeypatch.setattr(player, "mpv_command", lambda cmd: {"error": "success"})
    prev_res = playback_routes.previous()
    assert prev_res == {"ok": True, "action": "restart"}
    now_ts += 5.0
    assert player._runtime_gap_completion_plausible(vod_now) is False


def test_queue_prefetch_cooldown_suppresses_resolves_without_making_post_live_mpv_up_next_eligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ts = 30_000.0
    monkeypatch.setattr(player.time, "time", lambda: now_ts)
    resolve_calls: list[str] = []

    post_live_item = {
        "url": "https://www.youtube.com/watch?v=pn3qVRCI1_E",
        "title": "Post-live replay",
        "provider": "youtube",
        "channel": "Creator",
        "thumbnail": "https://i.ytimg.com/vi/pn3qVRCI1_E/hqdefault.jpg",
    }
    monkeypatch.setattr(
        player,
        "resolve_streams",
        lambda url: (
            resolve_calls.append(url)
            or ResolvedStreams(
                url,
                None,
                transport="mpv_ytdl",
                ytdl_format="best",
                live_status="post_live",
            )
        ),
    )

    assert player._item_should_prefetch_stream(post_live_item) is True
    assert player._mpv_up_next_eligible_item(post_live_item) is False
    player._prefetch_queue_item_worker(post_live_item, post_live_item["url"])
    assert len(resolve_calls) == 1
    assert "_resolved_stream" not in post_live_item

    # Structural policy stays True so mpv NEVER appends raw post_live URLs to its playlist,
    # while prefetch_queue_item_stream honors the cooldown and spawns no background worker.
    assert player._item_should_prefetch_stream(post_live_item) is True
    assert player._mpv_up_next_eligible_item(post_live_item) is False
    assert player._mpv_up_next_load_target(post_live_item) is None
    assert player.prefetch_queue_item_stream(post_live_item) is False
    assert len(resolve_calls) == 1

    # Transient resolver errors also back off without flipping _mpv_up_next_eligible_item to True.
    failing_item = {
        "url": "https://www.youtube.com/watch?v=brokenitem99",
        "title": "Broken item",
        "provider": "youtube",
        "channel": "Creator",
        "thumbnail": "https://i.ytimg.com/vi/brokenitem99/hqdefault.jpg",
    }

    def raise_resolve(_url: str):
        resolve_calls.append(_url)
        raise RuntimeError("yt-dlp failed")

    monkeypatch.setattr(player, "resolve_streams", raise_resolve)
    player._prefetch_queue_item_worker(failing_item, failing_item["url"])
    assert player._mpv_up_next_eligible_item(failing_item) is False
    assert player.prefetch_queue_item_stream(failing_item) is False
    now_ts += float(getattr(player, "_QUEUE_PREFETCH_ERROR_COOLDOWN_SEC", 120.0)) + 1.0
    if hasattr(player, "_item_prefetch_cooling_down"):
        assert player._item_prefetch_cooling_down(failing_item) is False


def test_seamless_replace_and_start_mpv_fallback_preserve_and_reset_ytdl_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_commands: list[list[object]] = []
    monkeypatch.setenv("RELAYTV_MPV_SEAMLESS_REPLACE", "1")
    monkeypatch.setenv("RELAYTV_MPV_YTDL_RAW_OPTIONS", "")
    monkeypatch.setattr(player, "_ACTIVE_HTTP_HEADERS", {}, raising=False)
    monkeypatch.setattr(player, "_ACTIVE_YTDL_OVERRIDES", {}, raising=False)
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: False)
    monkeypatch.setattr(player, "_qt_shell_runtime_accepts_mpv_commands", lambda: False)
    class _FakeProc:
        @staticmethod
        def poll():
            return None

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float = 3.0) -> int:
            return 0

    monkeypatch.setattr(player, "MPV_PROC", _FakeProc())
    monkeypatch.setattr(player, "_stop_qt_shell", lambda: player._ACTIVE_YTDL_OVERRIDES.clear())
    monkeypatch.setattr(player, "_persist_runtime_volume_before_stop", lambda: None)
    monkeypatch.setattr(player.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(player.youtube_stream, "proxy_url", lambda u: u)
    monkeypatch.setattr(
        player,
        "_effective_ytdl_format",
        lambda settings=None, provider=None: "bestvideo+bestaudio/best",
    )
    monkeypatch.setattr(
        player,
        "mpv_command",
        lambda cmd: captured_commands.append(list(cmd)) or {"error": "success"},
    )

    # Simulate start_mpv DRM -> X11 fallback so stop_mpv(restart_splash=False) runs mid-startup.
    monkeypatch.setattr(player.state, "get_settings", lambda: {"video_mode": "drm"})
    monkeypatch.setattr(player, "stop_splash_screen", lambda: None)
    monkeypatch.setattr(player, "_cleanup_ipc_socket", lambda: None)
    monkeypatch.setattr(player, "wait_for_ipc_ready", lambda timeout=5.0: True)
    monkeypatch.setattr(player, "_video_output_healthy", lambda timeout=2.0: False)
    monkeypatch.setattr(player, "_drm_no_video_fallback_allowed", lambda: True)
    monkeypatch.setattr(player, "_has_x11_display", lambda: True)
    monkeypatch.setattr(player, "_apply_startup_mpv_runtime_settings", lambda *_a: None)
    monkeypatch.setattr(player, "_recover_audio_output_if_needed", lambda *_a: None)
    monkeypatch.setattr(player.subprocess, "Popen", lambda *_a, **_kw: _FakeProc())

    player.start_mpv(
        "https://www.youtube.com/watch?v=live123",
        ytdl_format_override="95/94/best",
        ytdl_raw_options_override="extractor-args=youtube:player_client=tv",
    )
    assert player._ACTIVE_YTDL_OVERRIDES == {
        "ytdl-format": "95/94/best",
        "ytdl-raw-options": "extractor-args=youtube:player_client=tv",
    }

    # Next ordinary load restores default ytdl properties despite the earlier X11 fallback.
    captured_commands.clear()
    assert player._load_stream_in_existing_mpv("https://www.youtube.com/watch?v=vod456")
    assert ["set_property", "ytdl-format", "bestvideo+bestaudio/best"] in captured_commands
    assert ["set_property", "ytdl-raw-options", ""] in captured_commands

    # Subsequent ordinary load emits no redundant ytdl property resets.
    captured_commands.clear()
    assert player._load_stream_in_existing_mpv("https://www.youtube.com/watch?v=vod789")
    assert captured_commands == [
        ["loadfile", "https://www.youtube.com/watch?v=vod789", "replace"]
    ]


def test_resume_session_updates_playback_started_pos_for_completion_plausibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ts = 40_000.0
    monkeypatch.setattr(playback_service.time, "time", lambda: now_ts)
    monkeypatch.setattr(player.time, "time", lambda: now_ts)

    closed_vod = {
        "url": "https://cdn.example.com/movie.mp4",
        "title": "Closed two-hour movie",
        "provider": "other",
        "stream": "https://cdn.example.com/movie.mp4",
        "started": int(now_ts - 5000.0),
        "_playback_started_pos": 0.0,
        "resume_pos": 7120.0,
        "duration_sec": 7200.0,
        "closed": True,
        "history_id": "hist-closed-vod",
    }
    monkeypatch.setattr(state, "NOW_PLAYING", dict(closed_vod), raising=False)
    monkeypatch.setattr(state, "SESSION_STATE", "closed", raising=False)
    monkeypatch.setattr(state, "SESSION_POSITION", 7120.0, raising=False)
    monkeypatch.setattr(state, "persist_session", lambda: None)
    monkeypatch.setattr(
        player,
        "_load_stream_in_existing_mpv",
        lambda *_a, **_kw: True,
    )
    monkeypatch.setattr(player, "_arm_playback_start_watchdog", lambda *_a: None)
    monkeypatch.setattr(player, "mpv_set_result", lambda *_a: {"error": "success"})

    resumed, _ack = playback_service.resume_session()
    assert resumed["_playback_started_pos"] == 7120.0

    # Watching the remaining 80s to EOF passes completion plausibility.
    now_ts += 80.0
    resumed_done = dict(resumed, resume_pos=7200.0)
    assert player._runtime_gap_completion_plausible(resumed_done) is True


def test_live_stream_stall_aborts_stop_when_playback_intent_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ts = 50_000.0
    mono_ts = 1_000.0
    stop_commands: list[list[object]] = []

    monkeypatch.setattr(player.time, "time", lambda: now_ts)
    monkeypatch.setattr(player.time, "monotonic", lambda: mono_ts)
    player._reset_live_stream_stall_state()

    live_now = {
        "url": "https://rumble.com/v7ipa8i-live.html",
        "title": "Stalled live",
        "provider": "rumble",
        "is_live": True,
        "started": int(now_ts - 600.0),
        "resume_pos": 500.0,
        "history_id": "hist-stall-race",
    }
    monkeypatch.setattr(state, "NOW_PLAYING", dict(live_now), raising=False)
    monkeypatch.setattr(player, "playback_transitioning", lambda: False)
    monkeypatch.setattr(player, "auto_next_transitioning", lambda: False)
    monkeypatch.setattr(
        player,
        "mpv_command",
        lambda cmd: stop_commands.append(list(cmd)) or {"error": "success"},
    )
    props = {
        "core-idle": True,
        "eof-reached": False,
        "pause": False,
        "path": "https://rumble.com/v7ipa8i-live.html",
        "time-pos": 500.0,
    }

    def call_stall() -> bool:
        try:
            return player._live_stream_end_stalled(
                live_now, props, expected_intent=stale_intent
            )
        except TypeError:
            return player._live_stream_end_stalled(live_now, props)

    stale_intent = player.current_playback_intent()
    assert call_stall() is False

    # Simulate a concurrent Play claiming a newer intent right before the 45s threshold fires.
    mono_ts += 46.0
    player.claim_playback_intent()
    assert call_stall() is False
    assert stop_commands == []


def test_idle_uninitialized_subprocess_qt_shell_skips_ytdl_set_property_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_prop_calls: list[list[object]] = []
    load_calls: list[str] = []

    monkeypatch.setenv("RELAYTV_MPV_SEAMLESS_REPLACE", "1")
    monkeypatch.setattr(player, "_ACTIVE_HTTP_HEADERS", {}, raising=False)
    monkeypatch.setattr(
        player,
        "_ACTIVE_YTDL_OVERRIDES",
        {"ytdl-format": "95/94/best", "ytdl-raw-options": "a=b"},
        raising=False,
    )
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: True)
    monkeypatch.setattr(player, "_qt_runtime_uses_external_mpv", lambda: False)
    monkeypatch.setattr(player, "_idle_dashboard_enabled", lambda: True)
    monkeypatch.setattr(player, "_qt_shell_running", lambda: True)
    monkeypatch.setattr(player.os.path, "exists", lambda _p: False)
    monkeypatch.setattr(player.youtube_stream, "proxy_url", lambda u: u)
    monkeypatch.setattr(
        player,
        "_qt_shell_runtime_snapshot",
        lambda max_age_sec=3.0: {
            "alive": True,
            "control_file": "/tmp/relaytv-qt-runtime-control.json",
            "mpv_runtime_initialized": None,
            "mpv_runtime_playback_active": False,
            "mpv_runtime_stream_loaded": False,
            "mpv_runtime_playback_started": False,
        },
    )
    monkeypatch.setattr(player, "_qt_runtime_active", lambda **_: False)
    monkeypatch.setattr(
        player,
        "mpv_command",
        lambda cmd: set_prop_calls.append(list(cmd)) or {"error": "timeout_or_unavailable"},
    )
    monkeypatch.setattr(
        player,
        "_qt_shell_runtime_load_stream",
        lambda stream_url, audio_url=None, **_kw: load_calls.append(stream_url) or {"error": "success"},
    )

    # Ordinary stream load clears stale _ACTIVE_YTDL_OVERRIDES and loads without timing out on set_property.
    assert player._load_stream_in_existing_mpv("https://cdn.example.com/video.mp4") is True
    assert set_prop_calls == []
    assert load_calls == ["https://cdn.example.com/video.mp4"]
    assert player._ACTIVE_YTDL_OVERRIDES == {}

    # Override-bearing load declines seamless replace immediately so start_mpv passes CLI flags.
    assert (
        player._load_stream_in_existing_mpv(
            "https://www.youtube.com/watch?v=live123",
            ytdl_format_override="best",
        )
        is False
    )
    assert set_prop_calls == []
