# SPDX-License-Identifier: GPL-3.0-only
"""Service-level Jellyfin behavior tests (docs/ARCHITECTURE_PHASE_4_ROADMAP.md M7).

These exercise integrations/jellyfin_service.py directly with fake receiver
and player adapters — no FastAPI route context. Route-level contract tests
for the same behavior live in tests/test_jellyfin_routes.py.
"""
import pytest

from relaytv_app import playback_service, player, state, video_profile
from relaytv_app.integrations import jellyfin_receiver, jellyfin_service


class FakeCommandReq:
    """Duck-typed stand-in for the routes JellyfinCommandReq model."""

    def __init__(self, action=None, url=None, start_pos=None, use_ytdlp=True, payload=None):
        self.action = action
        self.url = url
        self.start_pos = start_pos
        self.use_ytdlp = use_ytdlp
        self.payload = payload


def _noop_ui() -> dict:
    return {
        "toast": lambda **kw: None,
        "notification_display_sec": lambda: 4.0,
        "queue_event": lambda event, **kw: None,
        "jellyfin_event": lambda event, **kw: None,
    }


# --- command normalization -------------------------------------------------


def test_normalize_action_maps_receiver_aliases() -> None:
    assert jellyfin_service.normalize_action("PlayNow", None) == "play"
    assert jellyfin_service.normalize_action("NextTrack", None) == "next"
    assert jellyfin_service.normalize_action("Unpauseplayback", None) == "resume"
    assert jellyfin_service.normalize_action("SetVolume", None) == "set_volume"
    assert jellyfin_service.normalize_action(None, {"Command": "MuteAudio"}) == "mute"


def test_extract_seek_seconds_converts_ticks_and_ms() -> None:
    ticks = FakeCommandReq(payload={"SeekPositionTicks": 90_000_000})
    assert jellyfin_service.extract_seek_seconds(ticks) == pytest.approx(9.0)
    ms = FakeCommandReq(payload={"PositionMs": 5500})
    assert jellyfin_service.extract_seek_seconds(ms) == pytest.approx(5.5)
    explicit = FakeCommandReq(start_pos=12.5, payload={"PositionTicks": 90_000_000})
    assert jellyfin_service.extract_seek_seconds(explicit) == pytest.approx(12.5)


def test_extract_playlist_items_prefers_rich_entries() -> None:
    payload = {
        "Items": [
            {"Id": "a1", "Name": "Pilot", "MediaSourceId": "ms-1"},
            {"Id": "a2", "Name": "Episode 2"},
        ],
        "ItemIds": ["ignored"],
    }
    items = jellyfin_service.extract_playlist_items(payload)
    assert [it["id"] for it in items] == ["a1", "a2"]
    assert items[0]["media_source_id"] == "ms-1"


# --- stream selection policy -----------------------------------------------


def _detail(**overrides) -> dict:
    base = {
        "item_id": "item-1",
        "video_codec": "h264",
        "video_height": 1080,
        "video_bit_depth": 8,
        "video_bitrate": 8_000_000,
        "audio_streams": [],
        "subtitle_streams": [],
    }
    base.update(overrides)
    return base


def test_select_playback_url_direct_when_profile_is_healthy(monkeypatch) -> None:
    monkeypatch.setattr(jellyfin_receiver, "get_item_detail", lambda iid, refresh=False: _detail())
    monkeypatch.setattr(video_profile, "get_profile", lambda: {"decode_profile": "intel_amd64_qsv", "av1_allowed": True})
    monkeypatch.setattr(player, "native_qt_runtime_active", lambda: False)

    selected = jellyfin_service.select_playback_url(
        item_id="item-1",
        source_url="http://jf.local/Videos/item-1/stream?static=true",
        server_url="http://jf.local",
        api_key="tok",
        settings={"jellyfin_playback_mode": "auto"},
    )
    assert selected["mode"] == "direct"
    assert selected["reason"] == "direct_ok"
    assert selected["url"] == "http://jf.local/Videos/item-1/stream?static=true"


def test_select_playback_url_transcodes_av1_when_not_allowed(monkeypatch) -> None:
    monkeypatch.setattr(jellyfin_receiver, "get_item_detail", lambda iid, refresh=False: _detail(video_codec="av1"))
    monkeypatch.setattr(video_profile, "get_profile", lambda: {"decode_profile": "intel_amd64_qsv", "av1_allowed": False})
    monkeypatch.setattr(player, "native_qt_runtime_active", lambda: False)
    resolved: dict[str, object] = {}

    def fake_resolve(iid, **kwargs):
        resolved.update({"item_id": iid, **kwargs})
        return {"url": "http://jf.local/Videos/item-1/master.m3u8?api_key=tok", "method": "server", "media_source_id": "ms-9"}

    monkeypatch.setattr(jellyfin_receiver, "resolve_playback_url", fake_resolve)

    selected = jellyfin_service.select_playback_url(
        item_id="item-1",
        source_url="",
        server_url="http://jf.local",
        api_key="tok",
        settings={"jellyfin_playback_mode": "auto"},
    )
    assert selected["mode"] == "transcode"
    assert selected["reason"] == "av1_not_allowed"
    assert selected["media_source_id"] == "ms-9"
    assert resolved["prefer_transcode"] is True


def test_select_playback_url_builds_fallback_master_url(monkeypatch) -> None:
    monkeypatch.setattr(jellyfin_receiver, "get_item_detail", lambda iid, refresh=False: {})
    monkeypatch.setattr(video_profile, "get_profile", lambda: {})
    monkeypatch.setattr(player, "native_qt_runtime_active", lambda: False)
    monkeypatch.setattr(jellyfin_receiver, "resolve_playback_url", lambda iid, **kw: {})

    selected = jellyfin_service.select_playback_url(
        item_id="item-1",
        source_url="",
        server_url="http://jf.local",
        api_key="tok",
        settings={"jellyfin_playback_mode": "auto"},
    )
    # Detail lookup failed -> compatibility-first transcode via built master URL.
    assert selected["mode"] == "transcode"
    assert selected["reason"] == "auto_no_detail"
    assert selected["method"] == "fallback_master"
    assert "/Videos/item-1/master.m3u8" in selected["url"]


def test_forced_transcode_mode_overrides_healthy_direct(monkeypatch) -> None:
    monkeypatch.setattr(jellyfin_receiver, "get_item_detail", lambda iid, refresh=False: _detail())
    monkeypatch.setattr(video_profile, "get_profile", lambda: {"decode_profile": "intel_amd64_qsv", "av1_allowed": True})
    monkeypatch.setattr(player, "native_qt_runtime_active", lambda: False)
    monkeypatch.setattr(
        jellyfin_receiver,
        "resolve_playback_url",
        lambda iid, **kw: {"url": "http://jf.local/t.m3u8", "method": "server", "media_source_id": ""},
    )

    selected = jellyfin_service.select_playback_url(
        item_id="item-1",
        source_url="http://jf.local/Videos/item-1/stream?static=true",
        server_url="http://jf.local",
        api_key="tok",
        settings={"jellyfin_playback_mode": "transcode"},
    )
    assert selected["mode"] == "transcode"
    assert selected["reason"] == "forced_transcode_mode"


# --- track preference ------------------------------------------------------


def test_preferred_stream_indices_match_language_settings(monkeypatch) -> None:
    monkeypatch.setattr(state, "get_settings", lambda: {"jellyfin_audio_lang": "jpn", "jellyfin_sub_lang": "en"})
    monkeypatch.setattr(
        jellyfin_receiver,
        "get_item_detail",
        lambda iid, refresh=False: {
            "audio_streams": [
                {"index": 0, "language": "eng"},
                {"index": 1, "language": "jpn"},
            ],
            "subtitle_streams": [
                {"index": 2, "language": "eng"},
            ],
        },
    )
    audio_idx, sub_idx = jellyfin_service.preferred_stream_indices("item-1")
    assert audio_idx == "1"
    assert sub_idx == "2"


def test_preferred_stream_indices_subtitles_off(monkeypatch) -> None:
    monkeypatch.setattr(state, "get_settings", lambda: {"jellyfin_audio_lang": "", "jellyfin_sub_lang": "off"})
    monkeypatch.setattr(jellyfin_receiver, "get_item_detail", lambda iid, refresh=False: {"audio_streams": [], "subtitle_streams": []})
    audio_idx, sub_idx = jellyfin_service.preferred_stream_indices("item-1")
    assert audio_idx == ""
    assert sub_idx == "-1"


def test_try_set_mpv_audio_track_picks_language_match(monkeypatch) -> None:
    track_list = [
        {"type": "audio", "id": 1, "lang": "eng", "ff-index": 0},
        {"type": "audio", "id": 2, "lang": "jpn", "ff-index": 1, "selected": True},
    ]
    sets: list[tuple[str, object]] = []
    monkeypatch.setattr(player, "mpv_get", lambda prop: list(track_list))
    monkeypatch.setattr(player, "mpv_set", lambda prop, val: sets.append((prop, val)))

    assert jellyfin_service.try_set_mpv_audio_track(language="jpn") is True
    assert ("aid", 2) in sets


# --- stopped/progress payloads ----------------------------------------------


def test_stopped_snapshot_from_now_snaps_near_complete_to_runtime() -> None:
    now = {
        "jellyfin_item_id": "item-1",
        "jellyfin_media_source_id": "ms-1",
        "url": "http://jf.local/Videos/item-1/stream",
    }
    payload = jellyfin_service.stopped_snapshot_from_now(now, position_sec=995.0, duration_sec=1000.0)
    assert payload["ItemId"] == "item-1"
    assert payload["MediaSourceId"] == "ms-1"
    # 99.5% > default 98% complete ratio -> snapped to full runtime.
    assert payload["PositionTicks"] == payload["RunTimeTicks"]
    assert payload["PlayedPercentage"] == 100.0


def test_progress_snapshot_reads_player_props(monkeypatch) -> None:
    monkeypatch.setattr(
        state, "NOW_PLAYING", {"jellyfin_item_id": "item-1", "url": "http://jf.local/v"}, raising=False
    )
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(
        player,
        "mpv_get_many",
        lambda props: {"pause": True, "time-pos": 30.0, "duration": 300.0, "mute": False, "volume": 80.0},
    )
    payload = jellyfin_service.progress_snapshot()
    assert payload["ItemId"] == "item-1"
    assert payload["IsPaused"] is True
    assert payload["PositionTicks"] == 300_000_000
    assert payload["RunTimeTicks"] == 3_000_000_000
    assert payload["VolumeLevel"] == 80


# --- command ingress with fake adapters --------------------------------------


def test_handle_command_dispatches_pause_through_controls(monkeypatch) -> None:
    jellyfin_service.reset_command_state()
    monkeypatch.setattr(jellyfin_receiver, "status", lambda: {"enabled": True})
    marks: list[str] = []
    monkeypatch.setattr(jellyfin_receiver, "mark_command", lambda action: marks.append(action))
    monkeypatch.setattr(jellyfin_receiver, "mark_heartbeat", lambda: None)
    hints: list[bool] = []
    monkeypatch.setattr(jellyfin_service, "emit_progress_hint", lambda: hints.append(True))
    controls = {"pause": lambda: {"paused": True}}

    out = jellyfin_service.handle_command(FakeCommandReq(action="Pause"), controls=controls, ui=_noop_ui())

    assert out == {"ok": True, "action": "pause", "result": {"paused": True}}
    assert marks == ["pause"]
    assert hints == [True]


def test_handle_command_suppresses_duplicate_command_ids(monkeypatch) -> None:
    jellyfin_service.reset_command_state()
    monkeypatch.setattr(jellyfin_receiver, "status", lambda: {"enabled": True})
    monkeypatch.setattr(jellyfin_receiver, "mark_command", lambda action: None)
    monkeypatch.setattr(jellyfin_receiver, "mark_heartbeat", lambda: None)
    monkeypatch.setattr(jellyfin_service, "emit_progress_hint", lambda: None)
    pauses: list[bool] = []
    controls = {"pause": lambda: pauses.append(True) or {"paused": True}}
    req = FakeCommandReq(action="Pause", payload={"CommandId": "cmd-1"})

    first = jellyfin_service.handle_command(req, controls=controls, ui=_noop_ui())
    second = jellyfin_service.handle_command(req, controls=controls, ui=_noop_ui())

    assert first["ok"] is True and "suppressed_duplicate_command" not in first
    assert second == {"ok": True, "action": "pause", "suppressed_duplicate_command": True}
    assert pauses == [True]
    jellyfin_service.reset_command_state()


def test_handle_command_play_uses_playback_service(monkeypatch) -> None:
    jellyfin_service.reset_command_state()
    monkeypatch.setattr(jellyfin_receiver, "status", lambda: {"enabled": True, "server_url": "http://jf.local"})
    monkeypatch.setattr(jellyfin_receiver, "mark_command", lambda action: None)
    monkeypatch.setattr(jellyfin_receiver, "mark_heartbeat", lambda: None)
    monkeypatch.setattr(jellyfin_receiver, "get_item_detail", lambda iid, refresh=False: _detail())
    monkeypatch.setattr(jellyfin_receiver, "session_token", lambda: "tok")
    monkeypatch.setattr(jellyfin_receiver, "api_key", lambda: "")
    monkeypatch.setattr(video_profile, "get_profile", lambda: {"decode_profile": "intel_amd64_qsv", "av1_allowed": True})
    monkeypatch.setattr(player, "native_qt_runtime_active", lambda: False)
    monkeypatch.setattr(player, "is_playing", lambda: False)
    monkeypatch.setattr(player, "recent_jellyfin_stop_matches", lambda **kw: False)
    monkeypatch.setattr(state, "get_settings", lambda: {"jellyfin_playback_mode": "direct"})
    monkeypatch.setattr(jellyfin_service, "emit_progress_hint", lambda: None)
    monkeypatch.setattr(
        jellyfin_service, "smart_item_from_url", lambda url, start_pos=None: {"url": url, "title": "Movie"}
    )
    monkeypatch.setattr(jellyfin_service, "preferred_stream_indices", lambda iid: ("", ""))

    suppressed: list[float] = []
    played: list[dict] = []
    now_updates: list[dict] = []
    monkeypatch.setattr(playback_service, "suppress_auto_next", lambda sec, **kw: suppressed.append(sec))
    monkeypatch.setattr(
        playback_service,
        "play_now",
        lambda item, **kw: played.append({"item": item, **kw}) or dict(item),
    )
    monkeypatch.setattr(playback_service, "update_now_playing", lambda now: now_updates.append(dict(now)))

    events: list[str] = []
    ui = _noop_ui()
    ui["jellyfin_event"] = lambda event, **kw: events.append(event)

    out = jellyfin_service.handle_command(
        FakeCommandReq(action="Play", payload={"ItemId": "item-1"}),
        controls={},
        ui=ui,
    )

    assert out["ok"] is True and out["action"] == "play"
    assert suppressed == [2.0]
    assert len(played) == 1
    assert played[0]["mode"] == "jellyfin_play"
    assert played[0]["clear_queue"] is False
    assert now_updates and now_updates[-1]["jellyfin_item_id"] == "item-1"
    assert events == ["play"]
    jellyfin_service.reset_command_state()
