# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import os
import stat
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from relaytv_app import (
    devices,
    discovery_mdns,
    main as app_main,
    playback_service,
    player,
    postlive_relay,
    qt_shell_app,
    routes,
    state,
    x11_overlay,
)
from relaytv_app.config import runtime_config
from relaytv_app.resolver import ResolvedStreams


def _install_memory_playback_state(
    monkeypatch: pytest.MonkeyPatch,
    *,
    now_playing: dict | None,
    queue_items: list[dict] | None = None,
) -> dict[str, dict]:
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
    monkeypatch.setattr(state, "QUEUE", list(queue_items or []), raising=False)
    monkeypatch.setattr(state, "persist_queue", lambda: True)
    monkeypatch.setattr(state, "persist_queue_payload", lambda _p: True)
    monkeypatch.setattr(state, "persist_session", lambda: True)
    monkeypatch.setattr(
        state,
        "history_update",
        lambda hid, updates: history_records.setdefault(str(hid), {}).update(updates) is None
        or True,
    )
    monkeypatch.setattr(state, "history_contains", lambda hid: str(hid) in history_records)
    monkeypatch.setattr(
        state,
        "history_add",
        lambda entry: history_records.__setitem__(str(entry["history_id"]), dict(entry)),
    )
    return history_records


# --- 1. Archived /live/<id> VODs & Upgraded post_live Replays ----------------


@pytest.mark.parametrize("live_status", ["was_live", "not_live", "post_live", "is_upcoming"])
def test_archived_youtube_live_urls_are_classified_as_vod_not_live(
    monkeypatch: pytest.MonkeyPatch,
    live_status: str,
) -> None:
    archived_item = {
        "url": "https://www.youtube.com/live/abc123vod",
        "title": "Archived YouTube Live Replay",
        "provider": "youtube",
        "live_status": live_status,
        "duration_sec": 3600.0,
        "resume_pos": 3595.0,
        "started": 1_000,
        "_playback_started_pos": 0.0,
    }
    assert player._item_looks_like_live_stream(archived_item) is False

    # Explicit live flags and IPTV still classify as live.
    assert player._item_looks_like_live_stream(dict(archived_item, is_live=True)) is True
    assert player._item_looks_like_live_stream(dict(archived_item, provider="iptv")) is True


def test_archived_youtube_live_vod_and_upgraded_postlive_restore_seek_prefetch_and_qt_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ts = 5_000.0
    monkeypatch.setattr(player.time, "time", lambda: now_ts)
    monkeypatch.setattr(routes.time, "time", lambda: now_ts)
    player._reset_playback_seek_state()

    vod_item = {
        "url": "https://www.youtube.com/live/archived999",
        "title": "Archived Live Stream VOD",
        "provider": "youtube",
        "stream": "https://rr1---sn.googlevideo.com/videoplayback",
        "live_status": "was_live",
        "duration_sec": 600.0,
        "resume_pos": 595.0,
        "started": int(now_ts - 600.0),
        "_playback_started_pos": 0.0,
        "history_id": "hist-archived-live",
    }
    assert player._item_should_prefetch_stream(vod_item) is True

    # /seek and /seek_abs use VOD seeking rather than _seek_live_cache_result.
    mpv_cmds: list[list[object]] = []
    monkeypatch.setattr(state, "NOW_PLAYING", dict(vod_item), raising=False)
    monkeypatch.setattr(
        routes,
        "_seek_live_cache_result",
        lambda *_a, **_kw: pytest.fail("VOD seek routed to live DVR cache"),
    )
    monkeypatch.setattr(routes, "_qt_runtime_seek_via_time_pos", lambda _t: None)
    monkeypatch.setattr(
        player,
        "mpv_get_many",
        lambda _props, **_kw: {"time-pos": 120.0, "duration": 600.0},
    )
    monkeypatch.setattr(
        player,
        "mpv_command",
        lambda cmd: mpv_cmds.append(list(cmd)) or {"error": "success"},
    )

    rel_res = routes._seek_relative_result(30.0)
    abs_res = routes._seek_absolute_result(300.0)
    assert rel_res["error"] == "success"
    assert abs_res["error"] == "success"
    assert ["seek", 30.0, "relative"] in mpv_cmds
    assert ["seek", 300.0, "absolute"] in mpv_cmds

    # Qt runtime gap completion succeeds for both was_live VOD and upgraded post_live spool.
    player._reset_playback_seek_state()
    for candidate in (
        vod_item,
        dict(
            vod_item,
            stream="/tmp/relaytv-postlive-spool/final.mkv",
            live_status="post_live",
            history_id="hist-upgraded-postlive",
        ),
    ):
        monkeypatch.setattr(state, "NOW_PLAYING", dict(candidate), raising=False)
        monkeypatch.setattr(state, "SESSION_STATE", "playing", raising=False)
        monkeypatch.setattr(player, "playback_transitioning", lambda: False)
        monkeypatch.setattr(player, "auto_next_transitioning", lambda: False)
        monkeypatch.setattr(player, "_is_playing", lambda: False)
        monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: True)
        monkeypatch.setattr(player, "_qt_shell_running", lambda: True)
        monkeypatch.setattr(player, "native_qt_playback_explicitly_ended", lambda: False)
        monkeypatch.setattr(player, "_qt_shell_runtime_snapshot", lambda max_age_sec=3.0: None)
        monkeypatch.setenv("RELAYTV_PLAYBACK_RUNTIME_GAP_CONFIRM_SEC", "0.5")
        monkeypatch.setattr(player, "_PLAYBACK_IDLE_CANDIDATE_SINCE", 0.0, raising=False)

        assert player._playback_runtime_idle_or_ended() is False
        now_ts += 1.0
        assert player._playback_runtime_idle_or_ended() is True


# --- 2. Completion Plausibility: Seek Near EOF, Durationless/Short, Stall ----


def test_seek_near_eof_marks_completed_and_advances_before_session_tracker_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ts = 10_000.0
    monkeypatch.setattr(player.time, "time", lambda: now_ts)
    player._reset_playback_seek_state()

    vod_now = {
        "url": "https://www.youtube.com/watch?v=seekeof123",
        "title": "Seek to EOF Video",
        "provider": "youtube",
        "started": int(now_ts - 30.0),
        "_playback_started_pos": 0.0,
        "resume_pos": 25.0,  # Stale pre-seek sample (< 2s before next tracker tick)
        "duration_sec": 1800.0,
        "history_id": "hist-seek-eof",
    }
    queued_next = {
        "url": "https://www.youtube.com/watch?v=queuedafterseek",
        "title": "Next Video",
        "provider": "youtube",
    }
    history_records = _install_memory_playback_state(
        monkeypatch, now_playing=vod_now, queue_items=[queued_next]
    )

    player.note_playback_seek(1799.0)
    now_ts += 1.2  # Finishes in 1.2s before 2s _session_tracker_tick runs

    assert player._history_item_completed(vod_now) is True
    assert player._runtime_gap_completion_plausible(vod_now) is True

    monkeypatch.setattr(player, "_attempt_playlist_next_handoff", lambda **_: None)
    monkeypatch.setattr(
        player,
        "play_item",
        lambda item, **_kw: {"url": item["url"], "title": item["title"], "started": int(now_ts)},
    )
    assert playback_service.natural_end() == "advanced"
    assert history_records["hist-seek-eof"]["completed"] is True
    assert state.QUEUE == []


def test_durationless_non_live_and_short_clip_complete_while_zero_progress_crash_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ts = 20_000.0
    monkeypatch.setattr(player.time, "time", lambda: now_ts)
    player._reset_playback_seek_state()

    # 1. Durationless non-live clip with >= 2s genuine playback progress completes.
    durationless_clip = {
        "url": "https://cdn.example.com/clip-no-content-length.mp4",
        "title": "Durationless Clip",
        "provider": "other",
        "started": int(now_ts - 15.0),
        "_playback_started_pos": 0.0,
        "resume_pos": 14.2,
        "duration_sec": None,
        "history_id": "hist-durationless-clip",
    }
    assert player._runtime_gap_completion_plausible(durationless_clip) is True
    assert player._natural_end_item_completed(durationless_clip) is True

    # 2. Sub-2s clip reaching explicit EOF before resume_pos is sampled completes.
    short_clip = {
        "url": "https://cdn.example.com/bumper.mp4",
        "title": "1.2s Bumper",
        "provider": "other",
        "started": int(now_ts - 1.3),
        "_playback_started_pos": 0.0,
        "resume_pos": None,
        "duration_sec": 1.2,
        "history_id": "hist-short-clip",
    }
    history_records = _install_memory_playback_state(
        monkeypatch,
        now_playing=short_clip,
        queue_items=[{"url": "https://cdn.example.com/main.mp4", "title": "Main Feature"}],
    )
    monkeypatch.setattr(player, "playback_transitioning", lambda: False)
    monkeypatch.setattr(player, "auto_next_transitioning", lambda: False)
    monkeypatch.setattr(player, "_is_playing", lambda: True)
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: True)
    monkeypatch.setattr(player, "native_qt_playback_explicitly_ended", lambda: True)
    monkeypatch.setattr(
        player,
        "_qt_shell_runtime_snapshot",
        lambda max_age_sec=3.0: {"mpv_runtime_eof_reached": True},
    )
    monkeypatch.setattr(player, "_attempt_playlist_next_handoff", lambda **_: None)
    monkeypatch.setattr(
        player,
        "play_item",
        lambda item, **_kw: {"url": item["url"], "title": item["title"], "started": int(now_ts)},
    )

    assert player._playback_runtime_idle_or_ended() is True
    assert playback_service.natural_end() == "advanced"
    assert history_records["hist-short-clip"]["completed"] is True

    # 3. Instant 0-progress crash (no EOF, resume_pos=None) never falsely completes.
    crashed_clip = {
        "url": "https://cdn.example.com/403.mp4",
        "title": "403 Crash",
        "provider": "other",
        "started": int(now_ts - 1.3),
        "_playback_started_pos": 0.0,
        "resume_pos": None,
        "duration_sec": None,
        "history_id": "hist-403",
    }
    player._reset_playback_seek_state()
    assert player._runtime_gap_completion_plausible(crashed_clip) is False
    assert player._natural_end_item_completed(crashed_clip) is False


def test_live_stream_stall_stop_completes_when_resume_pos_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ts = 30_000.0
    mono_ts = 500.0
    stop_cmds: list[list[object]] = []
    monkeypatch.setattr(player.time, "time", lambda: now_ts)
    monkeypatch.setattr(player.time, "monotonic", lambda: mono_ts)
    player._reset_live_stream_stall_state()
    player._reset_playback_seek_state()

    live_now = {
        "url": "https://www.youtube.com/watch?v=livestall99",
        "title": "Stalled Live Stream Without Sampled Resume",
        "provider": "youtube",
        "is_live": True,
        "live_status": "is_live",
        "started": int(now_ts - 120.0),
        "resume_pos": None,
        "history_id": "hist-live-stall-none",
    }
    initial_keys = set(live_now.keys())
    history_records = _install_memory_playback_state(
        monkeypatch,
        now_playing=live_now,
        queue_items=[{"url": "https://www.youtube.com/watch?v=nextafterlive", "title": "Next"}],
    )
    monkeypatch.setattr(player, "playback_transitioning", lambda: False)
    monkeypatch.setattr(player, "auto_next_transitioning", lambda: False)
    monkeypatch.setattr(player, "_is_playing", lambda: True)
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: True)
    monkeypatch.setattr(player, "native_qt_playback_explicitly_ended", lambda: False)
    monkeypatch.setattr(
        player,
        "_mpv_get_many_fresh",
        lambda _props: {
            "core-idle": True,
            "eof-reached": False,
            "pause": False,
            "path": live_now["url"],
            "time-pos": 33.5,
            "duration": None,
        },
    )
    monkeypatch.setattr(
        player,
        "mpv_command",
        lambda cmd: stop_cmds.append(list(cmd)) or {"error": "success"},
    )

    assert player._playback_runtime_idle_or_ended() is False
    mono_ts += 46.0
    now_ts += 46.0
    assert player._playback_runtime_idle_or_ended() is True
    assert stop_cmds == [["stop"]]
    assert set(live_now.keys()) == initial_keys
    assert live_now["resume_pos"] is None

    monkeypatch.setattr(player, "_attempt_playlist_next_handoff", lambda **_: None)
    monkeypatch.setattr(
        player,
        "play_item",
        lambda item, **_kw: {"url": item["url"], "title": item["title"], "started": int(now_ts)},
    )
    assert playback_service.natural_end() == "advanced"
    assert history_records["hist-live-stall-none"]["completed"] is True


# --- 3. Single-Use /postlive/{token}.mkv, Qt Start Telemetry & Hardening -----


def test_preserve_current_to_queue_front_never_caches_postlive_relay_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay_now = {
        "url": "https://www.youtube.com/live/postlive123",
        "title": "Processing Replay",
        "provider": "youtube",
        "stream": "http://127.0.0.1:8787/postlive/singleusetoken.mkv",
        "live_status": "post_live",
        "history_id": "hist-preserve-postlive",
    }
    _install_memory_playback_state(monkeypatch, now_playing=relay_now)
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(player, "mpv_get", lambda prop: 42.0 if prop == "time-pos" else None)

    preserved = playback_service.preserve_current_to_queue_front(capture_only=True)
    assert preserved is not None
    assert "_resolved_stream" not in preserved
    assert "_resolved_source_url" not in preserved
    assert preserved["url"] == "https://www.youtube.com/live/postlive123"


def test_postlive_upgrade_and_qt_supervisor_restart_avoid_dead_relay_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spool_file = tmp_path / "upgraded-spool.mkv"
    spool_file.write_bytes(b"matroska")
    relay_url = "http://127.0.0.1:8787/postlive/tok_upgrade_1.mkv"

    active_now = {
        "url": "https://www.youtube.com/live/postlive_up",
        "title": "Post-Live Replay",
        "provider": "youtube",
        "stream": relay_url,
        "live_status": "post_live",
        "started": 1_500,
        "history_id": "hist-up-1",
    }
    _install_memory_playback_state(monkeypatch, now_playing=active_now)
    monkeypatch.setattr(
        player.postlive_relay,
        "spool_ready_path",
        lambda tok: str(spool_file) if tok == "tok_upgrade_1" else None,
    )
    monkeypatch.setattr(player, "mpv_get", lambda prop: relay_url if prop == "path" else 55.0)
    monkeypatch.setattr(player, "_load_stream_in_existing_mpv", lambda *_a, **_kw: True)
    monkeypatch.setattr(player, "_notify_post_live_seek_ready", lambda: None)

    verdict, misses = player._post_live_upgrade_step("tok_upgrade_1", relay_url, 0)
    assert verdict == "upgraded"
    assert misses == 0

    started_streams: list[str] = []
    monkeypatch.setattr(
        player,
        "start_mpv",
        lambda stream, **_kw: started_streams.append(stream),
    )
    monkeypatch.setattr(player, "_current_runtime_paused", lambda: False)
    monkeypatch.setattr(player, "_current_runtime_position", lambda: 55.0)

    assert player._restart_active_qt_shell_playback("active_audio_without_video") is True
    assert started_streams == [str(spool_file)]
    assert state.NOW_PLAYING["stream"] == str(spool_file)

    # Without an upgraded spool, supervisor restart re-resolves from now["url"] instead of 404 token.
    started_streams.clear()
    unupgraded_now = {
        "url": "https://www.youtube.com/live/postlive_raw",
        "title": "Unupgraded Replay",
        "provider": "youtube",
        "stream": "http://127.0.0.1:8787/postlive/tok_dead_99.mkv",
        "live_status": "post_live",
        "started": 1_600,
        "history_id": "hist-up-2",
    }
    monkeypatch.setattr(state, "NOW_PLAYING", dict(unupgraded_now), raising=False)
    monkeypatch.setattr(player.postlive_relay, "spool_ready_path", lambda _tok: None)
    monkeypatch.setattr(
        player,
        "resolve_streams",
        lambda _u: ResolvedStreams(
            "https://rr2---sn.googlevideo.com/fresh-vod",
            None,
            live_status="was_live",
        ),
    )
    assert player._restart_active_qt_shell_playback("active_audio_without_video") is True
    assert started_streams == ["https://rr2---sn.googlevideo.com/fresh-vod"]


def test_qt_playback_started_requires_decoded_time_pos_and_rejects_previous_track_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. _QtLibMpvPlayer.runtime_snapshot must NOT set mpv_runtime_playback_started when
    #    duration > 0.0 while time_pos is None.
    fake_mpv = qt_shell_app._QtLibMpvPlayer.__new__(qt_shell_app._QtLibMpvPlayer)
    fake_mpv._handle = 1
    fake_mpv._track_list_cache_ts = 0.0
    fake_mpv._track_list_cache_aid = None
    fake_mpv._track_list_cache_path = ""
    fake_mpv._track_list_cache = []
    props: dict[str, object] = {
        "pause": "no",
        "time-pos": None,
        "duration": "240.0",
        "volume": "100.0",
        "mute": "no",
        "core-idle": "no",
        "eof-reached": "no",
        "path": "https://example.com/stuck204.mp4",
        "current-vo": "libmpv",
        "current-ao": "pulse",
        "aid": "1",
        "playlist-pos": "0",
        "playlist-count": "1",
    }
    fake_mpv._get_property_string = lambda name: props.get(name)
    fake_mpv._get_property_node = lambda _name: []
    fake_mpv.set_property = lambda *_a: None

    snap_unstarted = fake_mpv.runtime_snapshot()
    assert snap_unstarted["mpv_runtime_playback_active"] is True
    assert snap_unstarted["mpv_runtime_playback_started"] is False

    props["time-pos"] = "0.0"
    snap_started_zero = fake_mpv.runtime_snapshot()
    assert snap_started_zero["mpv_runtime_playback_started"] is True

    # 2. player._playback_runtime_started rejects <5s-old telemetry from the previous track
    #    (older ts or mismatched mpv_runtime_path).
    now_ts = 50_000.5
    monkeypatch.setattr(player.time, "time", lambda: now_ts)
    player.note_playback_started(0.0)
    monkeypatch.setattr(
        state,
        "NOW_PLAYING",
        {
            "url": "https://example.com/track-b.mp4",
            "stream": "https://example.com/track-b.mp4",
            "started": int(now_ts),
            "history_id": "hist-track-b",
        },
        raising=False,
    )
    monkeypatch.setattr(player, "mpv_command", lambda _cmd: {"error": "unavailable"})

    # Previous play (even of the same URL or with empty runtime path) wrote telemetry 0.3s
    # earlier in the same integer second: must return False via the timestamp guard alone!
    monkeypatch.setattr(
        player,
        "qt_shell_runtime_telemetry",
        lambda **_kw: {
            "available": True,
            "ts": now_ts - 0.3,
            "age_sec": 0.3,
            "mpv_runtime_path": "https://example.com/track-b.mp4",
            "mpv_runtime_playback_started": True,
            "mpv_runtime_time_pos": 88.0,
        },
    )
    assert player._playback_runtime_started() is False

    # Newer timestamp but still reporting previous track's path: must return False!
    monkeypatch.setattr(
        player,
        "qt_shell_runtime_telemetry",
        lambda **_kw: {
            "available": True,
            "ts": now_ts + 0.2,
            "age_sec": 0.0,
            "mpv_runtime_path": "https://example.com/track-a.mp4",
            "mpv_runtime_playback_started": True,
            "mpv_runtime_time_pos": 88.3,
        },
    )
    assert player._playback_runtime_started() is False

    # Current track path + ts >= load start + decoded time_pos >= 0.0 -> True!
    monkeypatch.setattr(
        player,
        "qt_shell_runtime_telemetry",
        lambda **_kw: {
            "available": True,
            "ts": now_ts + 0.4,
            "age_sec": 0.0,
            "mpv_runtime_path": "https://example.com/track-b.mp4",
            "mpv_runtime_playback_started": True,
            "mpv_runtime_time_pos": 0.0,
        },
    )
    assert player._playback_runtime_started() is True


def test_atomic_write_permissions_mpv_log_mode_qt_flag_equals_and_log_scrub(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # 1. qt_shell_app._atomic_write_json creates 0600 file via mkstemp.
    status_file = tmp_path / "qt-status.json"
    qt_shell_app._atomic_write_json(str(status_file), {"alive": True})
    assert stat.S_IMODE(status_file.stat().st_mode) == 0o600

    # 2. mpv log files created with 0600 in both player and qt_shell_app.
    mpv_log = tmp_path / "mpv-player.log"
    qt_mpv_log = tmp_path / "mpv-qt.log"
    monkeypatch.setenv("MPV_LOG_FILE", str(mpv_log))
    player._build_mpv_args("https://example.com/v.mp4", None, "x11")
    assert stat.S_IMODE(mpv_log.stat().st_mode) == 0o600

    monkeypatch.setenv("MPV_LOG_FILE", str(qt_mpv_log))
    qt_shell_app._build_mpv_args("https://example.com/v.mp4", wid=1)
    assert stat.S_IMODE(qt_mpv_log.stat().st_mode) == 0o600

    # 3. _start_qt_shell passes --flag=value so leading-dash values never crash argparse.
    spawned_args: list[list[str]] = []
    monkeypatch.setattr(player, "_stop_qt_shell", lambda: None)
    monkeypatch.setattr(player, "_effective_audio_device", lambda _s=None: "-custom-sink")
    monkeypatch.setattr(
        player.subprocess,
        "Popen",
        lambda args, **_kw: spawned_args.append(list(args)) or SimpleNamespace(poll=lambda: None),
    )
    player._start_qt_shell(
        "-weird-stream-id",
        http_headers={"User-Agent": "-DashAgent/1.0", "Referer": "-DashRef"},
        ytdl_format_override="-best",
        ytdl_raw_options_override="--extractor-args=youtube:player_client=tv",
    )
    assert len(spawned_args) == 1
    cli = spawned_args[0]
    for expected in (
        "--stream=-weird-stream-id",
        "--user-agent=-DashAgent/1.0",
        "--referrer=-DashRef",
        "--audio-device=-custom-sink",
        "--ytdl-format=-best",
        "--ytdl-raw-options=--extractor-args=youtube:player_client=tv",
    ):
        assert expected in cli

    # 4. get_mpv_log_tail scrubs URLs and query credentials.
    mpv_log.write_text(
        "[ffmpeg] Opening 'https://10.0.0.9:8096/Videos/1/stream?api_key=SECRET_KEY_123&Static=true'\n"
        "[ytdl_hook] Seg path /hls/seg1.ts?token=PRIV_TOK_999&sig=SIG_ABC failed\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MPV_LOG_FILE", str(mpv_log))
    tail = player.get_mpv_log_tail(lines=10)
    joined = "\n".join(tail)
    assert "SECRET_KEY_123" not in joined
    assert "PRIV_TOK_999" not in joined
    assert "SIG_ABC" not in joined
    assert "https://10.0.0.9" not in joined
    assert "<url>" in joined


# --- 4. _INTENT_LOCK Not Held Across Blocking CEC Subprocesses ---------------


def test_cec_takeover_runs_outside_intent_lock_and_honors_post_cec_retirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cec_entered = threading.Event()
    cec_release = threading.Event()
    started_mpv: list[str] = []

    def blocking_tv_on_and_switch() -> None:
        cec_entered.set()
        assert cec_release.wait(5.0)

    _install_memory_playback_state(
        monkeypatch,
        now_playing=None,
        queue_items=[{"url": "https://example.com/keep-in-queue.mp4", "title": "Keep"}],
    )
    monkeypatch.setattr(player, "cec_auto_on_switch", lambda cec: bool(cec))
    monkeypatch.setattr(player, "_our_phys_addr", lambda: "1000")
    monkeypatch.setattr(state, "get_tv_state", lambda: {"active_source_phys_addr": "2000"})
    monkeypatch.setattr(player, "tv_on_and_switch", blocking_tv_on_and_switch)
    monkeypatch.setattr(
        player,
        "start_mpv",
        lambda stream, **_kw: started_mpv.append(stream),
    )
    monkeypatch.setattr(player, "_load_stream_in_existing_mpv", lambda *_a, **_kw: False)

    play_thread = threading.Thread(
        target=player.play_item,
        kwargs={
            "item_or_text": {"url": "https://example.com/slow-cec.mp4", "title": "Slow CEC"},
            "use_resolver": False,
            "cec": True,
            "clear_queue": True,
            "mode": "play",
        },
        daemon=True,
    )
    play_thread.start()
    assert cec_entered.wait(2.0), "tv_on_and_switch was never entered"

    # While tv_on_and_switch is blocked in flight, retire_playback_intents must NOT block
    # on _INTENT_LOCK!
    retired_done = threading.Event()

    def retire_now() -> None:
        player.retire_playback_intents("concurrent_stop")
        retired_done.set()

    retire_thread = threading.Thread(target=retire_now, daemon=True)
    retire_thread.start()
    assert retired_done.wait(0.5), "_INTENT_LOCK was held across blocking tv_on_and_switch!"

    cec_release.set()
    play_thread.join(timeout=3.0)
    assert not play_thread.is_alive()
    assert started_mpv == []
    assert len(state.QUEUE) == 1


# --- 5. Playback Polish, Handoff, IPTV Restart, Reaping, mDNS, Thumb, Main ---


def test_jellyfin_stopped_payload_and_mpv_queued_next_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 5a. _jellyfin_stopped_payload_from_now reads duration_sec.
    payload = player._jellyfin_stopped_payload_from_now(
        {
            "jellyfin_item_id": "item-42",
            "jellyfin_media_source_id": "ms-42",
            "resume_pos": 99.0,
            "duration_sec": 100.0,
        }
    )
    assert payload is not None
    assert payload["RunTimeTicks"] == 1_000_000_000
    assert payload["PositionTicks"] == 1_000_000_000
    assert payload["PlayedPercentage"] == 100.0

    # 5b. _consume_mpv_queued_next_if_started resets started pos / seek baseline and carries
    #     duration_sec, live_status, is_live (guarded by live_status), and _playback_started_pos.
    prev_now = {
        "url": "https://www.youtube.com/watch?v=prev111",
        "title": "Previous",
        "provider": "youtube",
        "started": 1_000,
        "resume_pos": 118.0,
        "duration_sec": 120.0,
        "history_id": "hist-prev-111",
    }
    next_queued = {
        "url": "https://www.youtube.com/live/archived222",
        "title": "Queued Archived Live VOD",
        "provider": "youtube",
        "duration_sec": 900.0,
        "live_status": "was_live",
    }
    _install_memory_playback_state(monkeypatch, now_playing=prev_now, queue_items=[next_queued])
    player.note_playback_started(300.0)
    player.note_playback_seek(115.0)
    monkeypatch.setattr(player, "_is_playing", lambda: True)
    monkeypatch.setattr(player, "_prime_mpv_up_next_from_queue", lambda **_kw: None)
    monkeypatch.setattr(
        player, "_MPV_UPNEXT_ARMED_ID", player._queue_item_identity(next_queued), raising=False
    )
    monkeypatch.setattr(player, "_MPV_UPNEXT_ARMED_URL", next_queued["url"], raising=False)

    consumed = player._consume_mpv_queued_next_if_started(
        {"playlist-pos": 1, "time-pos": 0.4, "path": next_queued["url"]}
    )
    assert consumed is True
    assert player.playback_started_pos() == 0.0
    assert player.active_playback_seek_baseline(state.NOW_PLAYING) is None
    assert state.NOW_PLAYING["duration_sec"] == 900.0
    assert state.NOW_PLAYING["live_status"] == "was_live"
    assert "is_live" not in state.NOW_PLAYING
    assert state.NOW_PLAYING["_playback_started_pos"] == 0.0


def test_iptv_restart_current_and_restore_playback_state_preserve_dict_and_skip_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    iptv_now = {
        "url": "http://iptv.local:8080/live/stream.ts",
        "title": "Sports HD",
        "provider": "iptv",
        "iptv_source_id": "src-1",
        "iptv_channel_id": "chan-9",
        "http_headers": {"User-Agent": "RelayIPTV/1.0", "Referer": "http://iptv.local/"},
        "started": 2_000,
    }
    _install_memory_playback_state(monkeypatch, now_playing=dict(iptv_now))
    monkeypatch.setattr(player, "is_playing", lambda: False)
    monkeypatch.setattr(player, "stop_mpv", lambda **_kw: None)

    calls: list[tuple[object, bool]] = []

    def fake_play_item(target, *, use_resolver, **_kw):
        calls.append((target, use_resolver))
        return dict(target) if isinstance(target, dict) else {"url": str(target)}

    monkeypatch.setattr(player, "play_item", fake_play_item)

    restarted = player.restart_current()
    assert restarted is not None
    assert len(calls) == 1
    restart_target, restart_use_resolver = calls[0]
    assert isinstance(restart_target, dict)
    assert restart_use_resolver is False
    assert restart_target["provider"] == "iptv"
    assert restart_target["iptv_source_id"] == "src-1"
    assert restart_target["iptv_channel_id"] == "chan-9"
    assert restart_target["http_headers"] == iptv_now["http_headers"]
    assert restart_target["title"] == "Sports HD"

    calls.clear()
    playback_service.restore_playback_state(
        {"now_playing": dict(iptv_now), "position": None, "paused": False, "queue": []}
    )
    assert len(calls) == 1
    restore_target, restore_use_resolver = calls[0]
    assert isinstance(restore_target, dict)
    assert restore_use_resolver is False
    assert restore_target["provider"] == "iptv"
    assert restore_target["http_headers"] == iptv_now["http_headers"]


def test_process_kill_reaps_wait_and_x11_overlay_closes_log_handle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _StubbornProc:
        def __init__(self) -> None:
            self.killed = False
            self.wait_timeouts: list[float] = []

        def poll(self):
            return 0 if (self.killed and len(self.wait_timeouts) >= 2) else None

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float = 0.0) -> int:
            self.wait_timeouts.append(timeout)
            if not self.killed:
                raise subprocess.TimeoutExpired(cmd="stubborn", timeout=timeout)
            return -9

        def kill(self) -> None:
            self.killed = True

    # player.stop_mpv reaps after kill
    mpv_proc = _StubbornProc()
    monkeypatch.setattr(player, "MPV_PROC", mpv_proc)
    monkeypatch.setattr(player, "_persist_runtime_volume_before_stop", lambda: None)
    monkeypatch.setattr(player, "_stop_qt_shell", lambda: None)
    monkeypatch.setattr(player, "_cleanup_ipc_socket", lambda: None)
    player.stop_mpv(restart_splash=False)
    assert mpv_proc.killed is True
    assert mpv_proc.wait_timeouts == [3, 1]

    # x11_overlay.stop_overlay reaps after kill
    ov_proc = _StubbornProc()
    monkeypatch.setattr(x11_overlay, "_OVERLAY_PROC", ov_proc)
    x11_overlay.stop_overlay()
    assert ov_proc.killed is True
    assert ov_proc.wait_timeouts == [2, 1]

    # x11_overlay.start_overlay closes log_handle in finally: even if Popen raises, and sets 0600
    overlay_log = tmp_path / "overlay.log"
    opened_handles: list[object] = []
    real_fdopen = x11_overlay.os.fdopen

    def tracking_fdopen(fd: int, *args, **kwargs):
        fh = real_fdopen(fd, *args, **kwargs)
        opened_handles.append(fh)
        return fh

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("RELAYTV_X11_OVERLAY", "1")
    monkeypatch.setenv("RELAYTV_OVERLAY_LOG", str(overlay_log))
    monkeypatch.setattr(x11_overlay, "refresh_display_credentials", lambda env: dict(env))
    monkeypatch.setattr(x11_overlay.os, "fdopen", tracking_fdopen)
    monkeypatch.setattr(
        x11_overlay.subprocess,
        "Popen",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("spawn failed")),
    )
    x11_overlay.start_overlay()
    assert len(opened_handles) == 1
    assert opened_handles[0].closed is True
    assert stat.S_IMODE(overlay_log.stat().st_mode) == 0o600


def test_mdns_start_browse_async_retired_by_stop_browse_never_leaks_zeroconf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery_mdns, "_enabled", lambda: True)
    monkeypatch.setattr(discovery_mdns, "_browse_enabled", lambda: True)
    discovery_mdns.stop_browse()

    # Interleaving A: stop_browse() runs BEFORE the async startup thread enters Zeroconf().
    thread_gate = threading.Event()
    worker_refs: list[threading.Thread] = []
    built_zc: list[object] = []
    real_thread = discovery_mdns.threading.Thread

    def gated_thread(*, target, args=(), daemon=True, name=None):
        def wrapped():
            assert thread_gate.wait(5.0)
            target(*args)

        t = real_thread(target=wrapped, daemon=daemon, name=name)
        worker_refs.append(t)
        return t

    class _FakeZC:
        def __init__(self) -> None:
            self.closed = False
            built_zc.append(self)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(discovery_mdns.threading, "Thread", gated_thread)
    monkeypatch.setattr(discovery_mdns, "Zeroconf", _FakeZC)
    monkeypatch.setattr(
        discovery_mdns,
        "ServiceBrowser",
        lambda *_a, **_kw: SimpleNamespace(cancel=lambda: None),
    )

    discovery_mdns.start_browse_async()
    assert len(worker_refs) == 1
    discovery_mdns.stop_browse()
    thread_gate.set()
    worker_refs[0].join(timeout=3.0)
    assert built_zc == []
    assert discovery_mdns._BROWSE_ZEROCONF is None

    # Interleaving B: stop_browse() runs WHILE Zeroconf() is initializing on the async thread.
    monkeypatch.setattr(discovery_mdns.threading, "Thread", real_thread)
    zc_entered = threading.Event()
    zc_release = threading.Event()

    def slow_zc() -> _FakeZC:
        zc = _FakeZC()
        zc_entered.set()
        assert zc_release.wait(5.0)
        return zc

    monkeypatch.setattr(discovery_mdns, "Zeroconf", slow_zc)
    discovery_mdns.start_browse_async()
    assert zc_entered.wait(2.0)
    discovery_mdns.stop_browse()
    zc_release.set()

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not built_zc[0].closed:
        time.sleep(0.02)
    assert built_zc[0].closed is True
    assert discovery_mdns._BROWSE_ZEROCONF is None
    assert discovery_mdns._BROWSE_SESSION is None


def test_sanitize_thumb_ref_allows_browse_artwork_and_blocks_unsafe_paths() -> None:
    valid_encoded = "/browse/artwork/srv-1?path=%2Flibrary%2Fmetadata%2F42%2Fthumb%2F1700"
    valid_jf = "/browse/artwork/jf_main?path=%2FItems%2Fabc%2FImages%2FPrimary%3Ftag%3Dxyz"
    assert state._sanitize_thumb_ref(valid_encoded) == valid_encoded
    assert state._sanitize_thumb_ref(valid_jf) == valid_jf
    assert state._sanitize_thumb_ref("/browse/artwork/simple_id") == "/browse/artwork/simple_id"

    assert state._sanitize_thumb_ref("/browse/artwork/") is None
    assert state._sanitize_thumb_ref("/browse/artwork/../etc/passwd") is None
    assert state._sanitize_thumb_ref("/browse/artwork/srv?path=<script>") is None
    assert state._sanitize_thumb_ref("/browse/artwork/srv?path=/ok#frag") is None


def test_main_startup_sync_preserves_env_audio_device_and_lifespan_stops_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_env = dict(os.environ)
    try:
        monkeypatch.setenv("MPV_AUDIO_DEVICE", "alsa/hdmi:CARD=PCH,DEV=1")
        persisted = state._default_settings()
        persisted.update(
            {
                "device_name": "Bedroom TV",
                "audio_device": "",  # Default empty -> must NOT wipe env MPV_AUDIO_DEVICE!
                "video_mode": "drm",
                "drm_connector": "HDMI-A-2",
                "sub_lang": "en,ja",
                "cec_enabled": "1",
                "idle_dashboard_enabled": False,
                "idle_notifications_enabled": True,
                "idle_qr_enabled": False,
                "idle_qr_size": 200,
                "jellyfin_server_type": "emby",
                "quality_mode": "manual",
                "ytdlp_format": "bv*[height<=720]+ba/b",
            }
        )
        monkeypatch.setattr(app_main, "load_state_from_disk", lambda: None)
        monkeypatch.setattr(app_main, "get_settings", lambda: dict(persisted))
        monkeypatch.setattr(app_main.upload_store, "cleanup_uploads", lambda _s: None)

        with TestClient(app_main.create_app(testing=True)):
            snap = runtime_config.snapshot()
            assert snap.raw("RELAYTV_DEVICE_NAME") == "Bedroom TV"
            assert snap.raw("RELAYTV_JELLYFIN_DEVICE_NAME") == "Bedroom TV"
            assert snap.raw("RELAYTV_JELLYFIN_CLIENT_NAME") == "Bedroom TV"
            assert snap.raw("MPV_AUDIO_DEVICE") == "alsa/hdmi:CARD=PCH,DEV=1"
            assert snap.raw("RELAYTV_VIDEO_MODE") == "drm"
            assert snap.raw("RELAYTV_DRM_CONNECTOR") == "HDMI-A-2"
            assert snap.raw("RELAYTV_SUB_LANG") == "en,ja"
            assert snap.raw("RELAYTV_CEC") == "1"
            assert snap.raw("RELAYTV_CEC_ENABLED") == "1"
            assert snap.raw("RELAYTV_IDLE_DASHBOARD_ENABLED") == "0"
            assert snap.raw("RELAYTV_IDLE_NOTIFICATIONS_ENABLED") == "1"
            assert snap.raw("RELAYTV_IDLE_QR_ENABLED") == "0"
            assert snap.raw("RELAYTV_IDLE_QR_SIZE") == "200"
            assert snap.raw("RELAYTV_JELLYFIN_SERVER_TYPE") == "emby"
            assert snap.raw("YTDLP_FORMAT") == "bv*[height<=720]+ba/b"

        # Non-empty persisted audio_device overrides operator env in runtime_config.
        persisted["audio_device"] = "alsa/plughw:CARD=PCH,DEV=0"
        with TestClient(app_main.create_app(testing=True)):
            assert runtime_config.snapshot().raw("MPV_AUDIO_DEVICE") == "alsa/plughw:CARD=PCH,DEV=0"

        # Lifespan with workers_enabled=True terminates mpv and cec_monitor on shutdown.
        shutdown_events: list[str] = []
        monkeypatch.setenv("RELAYTV_DISABLE_WORKERS", "0")
        monkeypatch.setattr(app_main.video_profile, "warm_profile", lambda: None)
        monkeypatch.setattr(app_main, "start_autoplay_worker", lambda: None)
        monkeypatch.setattr(app_main, "start_session_tracker_worker", lambda: None)
        monkeypatch.setattr(app_main, "start_qt_audio_watchdog_worker", lambda: None)
        monkeypatch.setattr(app_main, "start_qt_shell_supervisor_worker", lambda: None)
        monkeypatch.setattr(app_main, "start_cec_monitor", lambda: None)
        monkeypatch.setattr(app_main, "start_thumb_worker", lambda: None)
        monkeypatch.setattr(app_main.upload_store, "start_cleanup_worker", lambda: None)
        monkeypatch.setattr(app_main.ytdlp_update, "start_worker", lambda: None)
        monkeypatch.setattr(app_main, "start_x11_overlay", lambda: None)
        monkeypatch.setattr(app_main.jellyfin_receiver, "start", lambda: None)
        monkeypatch.setattr(app_main.iptv_service, "start_worker", lambda: None)
        monkeypatch.setattr(app_main.discovery_mdns, "start_async", lambda: {})
        monkeypatch.setattr(app_main.discovery_mdns, "start_browse_async", lambda: {})
        monkeypatch.setattr(app_main, "qt_shell_backend_enabled", lambda: True)
        monkeypatch.setattr(app_main, "ensure_qt_shell_idle", lambda: None)
        monkeypatch.setattr(
            app_main,
            "stop_mpv",
            lambda *, restart_splash=True: shutdown_events.append(f"stop_mpv:{restart_splash}"),
        )
        monkeypatch.setattr(
            app_main,
            "stop_cec_monitor",
            lambda: shutdown_events.append("stop_cec_monitor"),
        )

        with TestClient(app_main.create_app(testing=False)):
            pass
        assert "stop_mpv:False" in shutdown_events
        assert "stop_cec_monitor" in shutdown_events
    finally:
        for k in set(os.environ) - set(snapshot_env):
            del os.environ[k]
        for k, v in snapshot_env.items():
            os.environ[k] = v
        runtime_config.refresh_from_env()


# --- 6. Audio Device Curation & Normalization --------------------------------


def test_normalize_audio_device_drivers_and_bare_alsa_pcms() -> None:
    for drv in (
        "alsa",
        "pulse",
        "pipewire",
        "jack",
        "sndio",
        "null",
        "auto",
        "oss",
        "coreaudio",
        "wasapi",
        "sdl",
        "openal",
    ):
        assert devices.normalize_audio_device_for_mpv(drv) == drv

    for pcm in (
        "default",
        "sysdefault",
        "front",
        "rear",
        "center_lfe",
        "side",
        "surround21",
        "surround40",
        "surround41",
        "surround50",
        "surround51",
        "surround71",
        "iec958",
        "spdif",
        "hdmi",
        "dmix",
        "dsnoop",
        "asym",
        "dshare",
        "usbstream",
    ):
        assert devices.normalize_audio_device_for_mpv(pcm) == f"alsa/{pcm}"


def test_mpv_audio_device_auto_runs_detect_and_keeps_watchdog_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prev = runtime_config.snapshot().raw("MPV_AUDIO_DEVICE")
    try:
        monkeypatch.setattr(
            devices, "detect_audio_device", lambda _conn="": "alsa/hdmi:CARD=NVidia,DEV=1"
        )
        runtime_config.set_value("MPV_AUDIO_DEVICE", "AUTO")
        assert player._audio_device_explicitly_configured({"audio_device": ""}) is False
        assert player._effective_audio_device({"audio_device": ""}) == "alsa/hdmi:CARD=NVidia,DEV=1"

        runtime_config.set_value("MPV_AUDIO_DEVICE", "alsa/plughw:CARD=PCH,DEV=0")
        assert player._audio_device_explicitly_configured({"audio_device": "auto"}) is False
        assert (
            player._effective_audio_device({"audio_device": "auto"})
            == "alsa/hdmi:CARD=NVidia,DEV=1"
        )
    finally:
        runtime_config.set_value("MPV_AUDIO_DEVICE", prev or "")


def test_curate_audio_devices_skips_usb_phantom_iec958_and_keeps_tegra_analog() -> None:
    entries = [
        ("auto", "Autoselect device"),
        (
            "alsa/plughw:CARD=Headset,DEV=0",
            "Logitech USB Headset, USB Audio/Hardware device with all software conversions",
        ),
        (
            "alsa/iec958:CARD=Headset,DEV=0",
            "Logitech USB Headset, USB Audio/IEC958 (S/PDIF) Digital Audio Output",
        ),
        (
            "alsa/plughw:CARD=PCH,DEV=0",
            "HDA Intel PCH, ALC897 Analog/Hardware device with all software conversions",
        ),
        (
            "alsa/iec958:CARD=PCH,DEV=0",
            "HDA Intel PCH, ALC897 Digital/IEC958 (S/PDIF) Digital Audio Output",
        ),
        (
            "alsa/plughw:CARD=APE,DEV=0",
            "NVIDIA Jetson Orin Nano APE, Tegra Analog/Hardware device with all software conversions",
        ),
        (
            "alsa/plughw:CARD=NVidia,DEV=3",
            "HDA NVidia, 27G2G8/Hardware device with all software conversions",
        ),
    ]
    curated = devices._curate_audio_devices(entries)
    ids = [d["id"] for d in curated]
    assert "alsa/plughw:CARD=Headset,DEV=0" in ids
    assert "alsa/iec958:CARD=Headset,DEV=0" not in ids
    assert "alsa/iec958:CARD=PCH,DEV=0" in ids
    assert "alsa/plughw:CARD=APE,DEV=0" in ids
    assert "alsa/plughw:CARD=NVidia,DEV=3" not in ids


def test_list_audio_devices_does_not_fallback_to_aplay_when_mpv_probed_is_nonempty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        devices,
        "list_alsa_devices",
        lambda: pytest.fail("aplay -L should not run when mpv probe succeeded"),
    )
    probed = [
        ("auto", "Autoselect device"),
        ("pulse/auto_null", "Dummy Output"),
        ("alsa/hdmi:CARD=vc4hdmi0,DEV=0", "vc4-hdmi-0, MAI PCM i2s-hifi-0/HDMI Audio Output"),
    ]
    disconnected = [{"connector": "HDMI-A-1", "status": "disconnected"}]
    assert devices.list_audio_devices(drm_connectors=disconnected, mpv_entries=probed) == []


def test_detect_audio_device_prefers_confirmed_eld_hdmi_over_explicit_connector_dev_index() -> None:
    x86_entries = [
        ("alsa/hdmi:CARD=NVidia,DEV=0", "HDA NVidia, HDMI 0/HDMI Audio Output"),
        ("alsa/hdmi:CARD=NVidia,DEV=1", "HDA NVidia, LG OLED TV/HDMI Audio Output"),
    ]
    connectors = [{"connector": "HDMI-A-1", "status": "connected"}]
    assert (
        devices.detect_audio_device(
            "HDMI-A-1",
            drm_connectors=connectors,
            mpv_entries=x86_entries,
        )
        == "alsa/hdmi:CARD=NVidia,DEV=1"
    )


def _install_fake_postlive_popen(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    poll_rc: int | None = None,
) -> None:
    class _FakePipe:
        def __init__(self, fd: int) -> None:
            self._fd = fd

        def fileno(self) -> int:
            return self._fd

        def readline(self) -> bytes:
            return b""

        def close(self) -> None:
            return None

    class _FakeProc:
        def __init__(self, fd: int) -> None:
            self.stdout = _FakePipe(fd)
            self.stderr = _FakePipe(fd + 100)
            self._rc = poll_rc

        def poll(self) -> int | None:
            return self._rc

        def terminate(self) -> None:
            self._rc = 1

        def wait(self, timeout: float | None = None) -> int:
            if self._rc is None:
                self._rc = 1
            return self._rc

        def kill(self) -> None:
            self._rc = -9

    seq = iter(range(10, 100))

    def _fake_popen(*_args, **_kwargs):
        return _FakeProc(next(seq))

    monkeypatch.setattr(state, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(postlive_relay.subprocess, "Popen", _fake_popen)


def test_postlive_close_all_stops_reaper_and_does_not_pollute_time_sleep(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_postlive_popen(monkeypatch, tmp_path, poll_rc=None)
    monkeypatch.setattr(postlive_relay, "_REAPER_INTERVAL_SEC", 0.02)

    try:
        postlive_relay.create_session(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            ytdl_format="best",
        )
        assert any(
            t.is_alive() and t.name == "relaytv-postlive-reaper"
            for t in threading.enumerate()
        )

        postlive_relay.close_all(reason="test cleanup")

        assert not any(
            t.is_alive() and t.name == "relaytv-postlive-reaper"
            for t in threading.enumerate()
        )

        events: list[tuple[str, float]] = []
        monkeypatch.setattr(player.time, "time", lambda: 1000.4)
        monkeypatch.setattr(
            player.time,
            "sleep",
            lambda seconds: events.append(("sleep", seconds)),
        )
        threading.Event().wait(0.08)
        player._wait_for_resolved_media_availability({"_playback_available_at": 1004.0})
        assert events == [("sleep", pytest.approx(4.2))]
    finally:
        postlive_relay.close_all(reason="test cleanup")


def test_postlive_reaper_exits_when_idle_and_restarts_on_next_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_postlive_popen(monkeypatch, tmp_path, poll_rc=1)
    monkeypatch.setattr(postlive_relay, "_REAPER_INTERVAL_SEC", 0.02)
    monkeypatch.setattr(postlive_relay, "_READERLESS_GRACE_SEC", 0.0)

    try:
        postlive_relay.create_session(
            "https://www.youtube.com/watch?v=first000001",
            ytdl_format="best",
        )
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if not any(
                t.is_alive() and t.name == "relaytv-postlive-reaper"
                for t in threading.enumerate()
            ):
                break
            threading.Event().wait(0.02)
        assert not any(
            t.is_alive() and t.name == "relaytv-postlive-reaper"
            for t in threading.enumerate()
        )

        monkeypatch.setattr(postlive_relay, "_READERLESS_GRACE_SEC", 60.0)
        _install_fake_postlive_popen(monkeypatch, tmp_path, poll_rc=None)
        postlive_relay.create_session(
            "https://www.youtube.com/watch?v=second00002",
            ytdl_format="best",
        )
        assert any(
            t.is_alive() and t.name == "relaytv-postlive-reaper"
            for t in threading.enumerate()
        )
    finally:
        postlive_relay.close_all(reason="test cleanup")
