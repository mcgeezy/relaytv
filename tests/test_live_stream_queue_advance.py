# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import pytest

from relaytv_app import playback_service, player, routes, state
from relaytv_app.resolver import ResolvedStreams


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
            lambda props: {
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
            "resume_pos": None,
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


def test_unstarted_broken_vod_without_duration_remains_implausible_for_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ts = 5_000.0
    monkeypatch.setattr(player.time, "time", lambda: now_ts)
    broken_vod = {
        "url": "https://example.com/broken.mp4",
        "title": "Broken VOD",
        "provider": "other",
        "started": int(now_ts - 10.0),
        "_playback_started_pos": 0.0,
        "resume_pos": None,
        "duration_sec": None,
    }
    assert player._runtime_gap_completion_plausible(broken_vod) is False


def test_status_payload_demotes_explicitly_ended_live_stream_but_holds_buffering_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_now = {
        "url": "https://rumble.com/v7ipa8i-live.html",
        "title": "Ended live stream",
        "provider": "rumble",
        "is_live": True,
    }
    monkeypatch.setattr(state, "NOW_PLAYING", dict(live_now), raising=False)
    monkeypatch.setattr(state, "SESSION_STATE", "playing", raising=False)
    monkeypatch.setattr(state, "SESSION_POSITION", 6290.8, raising=False)
    monkeypatch.setattr(state, "QUEUE", [], raising=False)
    monkeypatch.setattr(state, "persist_session", lambda: None)
    monkeypatch.setattr(player, "is_playing", lambda: False)
    monkeypatch.setattr(player, "startup_session_restore_pending", lambda: False)
    monkeypatch.setattr(player, "_qt_runtime_active", lambda **_: False)
    monkeypatch.setattr(player, "playback_transitioning", lambda: False)
    monkeypatch.setattr(player, "auto_next_transitioning", lambda: False)

    # During a transient live buffering gap (not explicitly ended), session stays "playing".
    monkeypatch.setattr(player, "native_qt_playback_explicitly_ended", lambda: False)
    buffering_payload = routes._status_payload()
    assert buffering_payload["state"] == "playing"
    assert state.SESSION_STATE == "playing"

    # Once Qt explicitly reports EOF + core-idle, /status reconciles stale "playing" to "idle".
    monkeypatch.setattr(player, "native_qt_playback_explicitly_ended", lambda: True)
    ended_payload = routes._status_payload()
    assert ended_payload["state"] == "idle"
    assert state.SESSION_STATE == "idle"


def test_unclosed_hls_live_stream_stall_triggers_stop_and_ended_while_iptv_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ts = 10_000.0
    mono_ts = 500.0
    stop_commands: list[list[object]] = []

    monkeypatch.setattr(player.time, "time", lambda: now_ts)
    monkeypatch.setattr(player.time, "monotonic", lambda: mono_ts)
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
    monkeypatch.setattr(player, "native_qt_playback_explicitly_ended", lambda: False)
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
    monkeypatch.setattr(player, "mpv_get_many", lambda props: dict(stalled_props))

    # Initial observation and transient buffering (< 45s) do not end the live stream.
    assert player._playback_runtime_idle_or_ended() is False
    mono_ts += 20.0
    assert player._playback_runtime_idle_or_ended() is False
    assert stop_commands == []

    # Advancing time-pos resets the stall timer.
    stalled_props["time-pos"] = 1802.0
    mono_ts += 30.0
    assert player._playback_runtime_idle_or_ended() is False
    mono_ts += 30.0
    assert player._playback_runtime_idle_or_ended() is False
    assert stop_commands == []

    # Continuous unpaused core-idle with frozen time-pos >= 45s aborts hung demuxer and ends.
    mono_ts += 16.0
    assert player._playback_runtime_idle_or_ended() is True
    assert stop_commands == [["stop"]]

    # Linear 24/7 IPTV channels never trigger live-end stall termination.
    stop_commands.clear()
    iptv_now = dict(live_now, provider="iptv")
    monkeypatch.setattr(state, "NOW_PLAYING", iptv_now, raising=False)
    assert player._playback_runtime_idle_or_ended() is False
    mono_ts += 120.0
    assert player._playback_runtime_idle_or_ended() is False
    assert stop_commands == []


def test_forward_seek_updates_completion_baseline_for_vod_auto_next(
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
        "resume_pos": 5.0,
        "duration_sec": 7200.0,
    }
    monkeypatch.setattr(state, "NOW_PLAYING", vod_now, raising=False)
    monkeypatch.setattr(player, "_qt_shell_runtime_accepts_mpv_commands", lambda: True)
    monkeypatch.setattr(
        player,
        "mpv_get_many",
        lambda props: {"time-pos": 5.0, "duration": 7200.0},
    )
    monkeypatch.setattr(player, "mpv_set_result", lambda prop, val: {"error": "success"})

    # User seeks forward to the final 80 seconds of the 2-hour VOD 10s after starting.
    now_ts += 10.0
    seek_res = routes._seek_absolute_result(7120.0)
    assert seek_res["error"] == "success"
    assert vod_now["_playback_seek_pos"] == 7120.0

    # Watching the remaining 80s to EOF is now recognized as plausible completion.
    now_ts += 80.0
    vod_now["resume_pos"] = 7200.0
    assert player._runtime_gap_completion_plausible(vod_now) is True


def test_queue_prefetch_honors_cooldown_for_mpv_ytdl_and_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ts = 30_000.0
    monkeypatch.setattr(player.time, "time", lambda: now_ts)
    resolve_calls: list[str] = []

    post_live_item = {
        "url": "https://www.youtube.com/watch?v=pn3qVRCI1_E",
        "title": "Post-live replay",
        "provider": "youtube",
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
    player._prefetch_queue_item_worker(post_live_item, post_live_item["url"])
    assert len(resolve_calls) == 1
    assert "_resolved_stream" not in post_live_item
    assert player._item_should_prefetch_stream(post_live_item) is False

    # Still suppressed 60s later; allowed again once cooldown expires.
    now_ts += 60.0
    assert player._item_should_prefetch_stream(post_live_item) is False
    now_ts += float(getattr(player, "_QUEUE_PREFETCH_DEFER_COOLDOWN_SEC", 300.0))
    assert player._item_should_prefetch_stream(post_live_item) is True

    # Transient resolver failures also back off instead of retrying every 2s tick.
    failing_item = {
        "url": "https://www.youtube.com/watch?v=brokenitem99",
        "title": "Broken item",
        "provider": "youtube",
    }

    def raise_resolve(_url: str):
        resolve_calls.append(_url)
        raise RuntimeError("yt-dlp failed")

    monkeypatch.setattr(player, "resolve_streams", raise_resolve)
    player._prefetch_queue_item_worker(failing_item, failing_item["url"])
    assert player._item_should_prefetch_stream(failing_item) is False
    now_ts += float(getattr(player, "_QUEUE_PREFETCH_ERROR_COOLDOWN_SEC", 120.0)) + 1.0
    assert player._item_should_prefetch_stream(failing_item) is True


def test_seamless_replace_resets_sticky_ytdl_overrides_on_next_normal_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_commands: list[list[object]] = []
    monkeypatch.setenv("RELAYTV_MPV_SEAMLESS_REPLACE", "1")
    monkeypatch.setenv("RELAYTV_MPV_YTDL_RAW_OPTIONS", "")
    monkeypatch.setattr(player, "_ACTIVE_HTTP_HEADERS", {}, raising=False)
    monkeypatch.setattr(player, "_ACTIVE_YTDL_OVERRIDES", {}, raising=False)
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: False)
    monkeypatch.setattr(player, "_qt_shell_runtime_accepts_mpv_commands", lambda: False)
    monkeypatch.setattr(
        player,
        "MPV_PROC",
        type("_Proc", (), {"poll": staticmethod(lambda: None)})(),
    )
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

    # First load installs live-stream ytdl overrides on the running mpv instance.
    assert player._load_stream_in_existing_mpv(
        "https://www.youtube.com/watch?v=live123",
        ytdl_format_override="95/94/best",
        ytdl_raw_options_override="extractor-args=youtube:player_client=tv",
    )
    assert ["set_property", "ytdl-format", "95/94/best"] in captured_commands
    assert [
        "set_property",
        "ytdl-raw-options",
        "extractor-args=youtube:player_client=tv",
    ] in captured_commands

    # Next ordinary load restores default ytdl properties instead of inheriting live overrides.
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
