# SPDX-License-Identifier: GPL-3.0-only
"""Service-level Jellyfin behavior tests (docs/ARCHITECTURE.md).

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


def test_handle_command_playlist_enriches_queue_metadata(monkeypatch) -> None:
    jellyfin_service.reset_command_state()
    monkeypatch.setattr(jellyfin_receiver, "status", lambda: {"enabled": True, "server_url": "http://jf.local"})
    monkeypatch.setattr(jellyfin_receiver, "mark_command", lambda action: None)
    monkeypatch.setattr(jellyfin_receiver, "mark_heartbeat", lambda: None)
    monkeypatch.setattr(jellyfin_receiver, "get_item_detail", lambda iid, refresh=False: _detail())
    monkeypatch.setattr(jellyfin_receiver, "session_token", lambda: "tok")
    monkeypatch.setattr(jellyfin_receiver, "api_key", lambda: "")

    metadata_calls: list[str] = []

    def fake_metadata(iid, *, token_override="", server_url_override=""):
        metadata_calls.append(iid)
        return {
            "title": f"Episode {iid}",
            "channel": "Series · S01",
            "thumbnail": f"http://jf.local/Items/{iid}/Images/Primary",
        }

    monkeypatch.setattr(jellyfin_receiver, "get_item_metadata", fake_metadata)
    monkeypatch.setattr(video_profile, "get_profile", lambda: {"decode_profile": "intel_amd64_qsv", "av1_allowed": True})
    monkeypatch.setattr(player, "native_qt_runtime_active", lambda: False)
    monkeypatch.setattr(player, "is_playing", lambda: False)
    monkeypatch.setattr(player, "recent_jellyfin_stop_matches", lambda **kw: False)
    monkeypatch.setattr(player, "prime_mpv_up_next_from_queue", lambda force=False: None)
    monkeypatch.setattr(state, "get_settings", lambda: {"jellyfin_playback_mode": "direct"})
    monkeypatch.setattr(state, "QUEUE", [])
    monkeypatch.setattr(state, "NOW_PLAYING", {})
    monkeypatch.setattr(state, "persist_queue", lambda: None)
    monkeypatch.setattr(jellyfin_service, "emit_progress_hint", lambda: None)
    monkeypatch.setattr(
        jellyfin_service, "smart_item_from_url", lambda url, start_pos=None: {"url": url, "title": "Episode ep-1"}
    )
    monkeypatch.setattr(jellyfin_service, "preferred_stream_indices", lambda iid: ("", ""))
    monkeypatch.setattr(playback_service, "suppress_auto_next", lambda sec, **kw: None)
    monkeypatch.setattr(playback_service, "play_now", lambda item, **kw: dict(item))
    monkeypatch.setattr(playback_service, "update_now_playing", lambda now: None)

    out = jellyfin_service.handle_command(
        FakeCommandReq(action="Play", payload={"ItemIds": ["ep-1", "ep-2", "ep-3"], "PlayCommand": "PlayNow"}),
        controls={},
        ui=_noop_ui(),
    )

    assert out["ok"] is True and out["action"] == "play"
    assert metadata_calls == ["ep-2", "ep-3"]
    assert [q["jellyfin_item_id"] for q in state.QUEUE] == ["ep-2", "ep-3"]
    assert [q["title"] for q in state.QUEUE] == ["Episode ep-2", "Episode ep-3"]
    assert state.QUEUE[0]["channel"] == "Series · S01"
    assert state.QUEUE[0]["thumbnail"] == "http://jf.local/Items/ep-2/Images/Primary"
    jellyfin_service.reset_command_state()


# --- server-type detection (Emby support) ----------------------------------


class _FakeSystemInfoResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_system_info(monkeypatch, payload) -> None:
    import json as _json

    def fake_urlopen(req, timeout=None):
        if isinstance(payload, Exception):
            raise payload
        return _FakeSystemInfoResp(_json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(jellyfin_receiver._urlrequest, "urlopen", fake_urlopen)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"ProductName": "Jellyfin Server", "Version": "10.9.0", "Id": "j1"}, "jellyfin"),
        ({"ProductName": "Emby Server", "Version": "4.8.0", "Id": "e1"}, "emby"),
        # Emby 3.5.x omits ProductName from the public system info entirely.
        ({"Version": "3.5.3", "Id": "e2"}, "emby"),
    ],
)
def test_detect_server_type_classifies_products(monkeypatch, payload, expected) -> None:
    _fake_system_info(monkeypatch, payload)

    result = jellyfin_receiver.detect_server_type("http://media.local:8096/")

    assert result["ok"] is True
    assert result["server_type"] == expected


def test_detect_server_type_rejects_unrecognizable_and_unreachable(monkeypatch) -> None:
    _fake_system_info(monkeypatch, {"hello": "world"})
    result = jellyfin_receiver.detect_server_type("http://media.local:8096")
    assert result["ok"] is False and result["server_type"] == ""

    _fake_system_info(monkeypatch, OSError("connection refused"))
    result = jellyfin_receiver.detect_server_type("http://media.local:8096")
    assert result["ok"] is False and result["server_type"] == ""
    assert result["error"]

    result = jellyfin_receiver.detect_server_type("")
    assert result["ok"] is False and result["error"] == "no_server_url"


def test_run_detection_persists_detected_server_type(monkeypatch) -> None:
    updates: list[dict[str, object]] = []
    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_type", "jellyfin")
    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_product_name", "")
    monkeypatch.setitem(jellyfin_receiver._STATUS, "last_detect_ok", None)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "last_detect_ts", None)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "last_detect_error", None)
    monkeypatch.setattr(state, "update_settings", lambda patch: updates.append(dict(patch)) or dict(patch))
    _fake_system_info(monkeypatch, {"ProductName": "Emby Server", "Version": "4.8.0", "Id": "e1"})

    result = jellyfin_receiver._run_detection("http://emby.local:8096")

    assert result["ok"] is True
    status = jellyfin_receiver.status()
    assert status["server_type"] == "emby"
    assert status["server_product_name"] == "Emby Server"
    assert status["last_detect_ok"] is True
    assert updates == [{"jellyfin_server_type": "emby"}]


def test_run_detection_failure_keeps_existing_server_type(monkeypatch) -> None:
    updates: list[dict[str, object]] = []
    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_type", "jellyfin")
    monkeypatch.setitem(jellyfin_receiver._STATUS, "last_detect_ok", None)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "last_detect_ts", None)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "last_detect_error", None)
    monkeypatch.setattr(state, "update_settings", lambda patch: updates.append(dict(patch)) or dict(patch))
    _fake_system_info(monkeypatch, OSError("connection refused"))

    result = jellyfin_receiver._run_detection("http://emby.local:8096")

    assert result["ok"] is False
    status = jellyfin_receiver.status()
    assert status["server_type"] == "jellyfin"
    assert status["last_detect_ok"] is False
    assert status["last_detect_error"]
    assert updates == []


@pytest.mark.parametrize(
    ("server_type", "expected_row_ids"),
    [
        ("jellyfin", ["continue_watching", "next_up", "movies", "shows", "recently_added"]),
        ("emby", ["movies", "shows", "recently_added"]),
    ],
)
def test_home_rows_match_server_capabilities(monkeypatch, server_type, expected_row_ids) -> None:
    requested_urls: list[str] = []

    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_type", server_type)
    monkeypatch.setattr(
        jellyfin_receiver,
        "_catalog_base_token_user",
        lambda: ("http://media.local", "token", "user-1"),
    )

    def fake_get_json(url, *, timeout, token):
        requested_urls.append(url)
        return {"Items": [{"Id": "item-1"}]}

    monkeypatch.setattr(jellyfin_receiver, "_get_json", fake_get_json)
    monkeypatch.setattr(
        jellyfin_receiver,
        "_normalize_catalog_item",
        lambda item, *, base, token: {"item_id": item["Id"]},
    )

    payload = jellyfin_receiver.get_home_rows(limit=5, refresh=True)

    assert [row["id"] for row in payload["rows"]] == expected_row_ids
    if server_type == "emby":
        assert not any("/Items/Resume" in url or "/Shows/NextUp" in url for url in requested_urls)


def test_normalize_catalog_item_exposes_image_roles_and_progress(monkeypatch) -> None:
    def attach(item):
        item["thumbnail_local"] = "/thumbs/cached.jpg"
        return item

    monkeypatch.setattr(jellyfin_receiver, "_attach_thumb", attach)

    item = jellyfin_receiver._normalize_catalog_item(
        {
            "Id": "movie 1",
            "Name": "Movie",
            "Type": "Movie",
            "ImageTags": {"Primary": "primary-tag"},
            "BackdropImageTags": ["backdrop-tag"],
            "RunTimeTicks": 1_000_000_000,
            "UserData": {
                "PlaybackPositionTicks": 250_000_000,
                "Played": False,
                "IsFavorite": True,
            },
        },
        base="http://media.local",
        token="secret token",
    )

    assert item["poster"] == item["thumbnail"]
    assert item["poster_local"] == "/thumbs/cached.jpg"
    assert item["backdrop"] == (
        "http://media.local/Items/movie%201/Images/Backdrop/0?tag=backdrop-tag&api_key=secret+token"
    )
    assert item["progress_percent"] == 25.0
    assert item["is_played"] is False
    assert item["is_favorite"] is True


def test_persist_server_type_writes_only_on_change(monkeypatch) -> None:
    updates: list[dict[str, object]] = []
    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_type", "emby")
    monkeypatch.setattr(state, "update_settings", lambda patch: updates.append(dict(patch)) or dict(patch))

    jellyfin_receiver._persist_server_type("emby", "Emby Server")

    assert updates == []
    assert jellyfin_receiver._STATUS["server_product_name"] == "Emby Server"


def test_set_server_type_updates_live_status_and_clears_stale_product(monkeypatch) -> None:
    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_type", "jellyfin")
    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_product_name", "Jellyfin Server")
    monkeypatch.setattr(state, "update_settings", lambda patch: dict(patch))

    result = jellyfin_receiver.set_server_type(" EMBY ")

    assert result["server_type"] == "emby"
    assert result["server_product_name"] == ""


def test_looks_like_media_url_accepts_emby_hosts() -> None:
    assert jellyfin_service.looks_like_media_url("http://emby.home.lan:8096/web/index.html") is True
    assert jellyfin_service.looks_like_media_url("http://jellyfin.home.lan:8096/web/") is True
    assert jellyfin_service.looks_like_media_url("http://media.local/Items/abc/PlaybackInfo") is True
    assert jellyfin_service.looks_like_media_url("https://example.com/watch?v=abc") is False


def test_provider_display_name_reflects_server_type(monkeypatch) -> None:
    from relaytv_app import resolver

    monkeypatch.setattr(state, "get_settings", lambda: {"jellyfin_server_type": "emby"})
    assert resolver._provider_display_name("jellyfin") == "Emby"
    assert resolver._provider_display_name("youtube") == "YouTube"

    monkeypatch.setattr(state, "get_settings", lambda: {"jellyfin_server_type": "jellyfin"})
    assert resolver._provider_display_name("jellyfin") == "Jellyfin"

    monkeypatch.setattr(state, "get_settings", lambda: {})
    assert resolver._provider_display_name("jellyfin") == "Jellyfin"


# --- cast-target registration ----------------------------------------------

# Jellyfin's GeneralCommandType, as served by 10.11's OpenAPI schema. Pinned
# here because the bug this guards was advertising PlaystateCommand values in
# a GeneralCommandType field: the server answered 400, and the wrapped-body
# fallback that "succeeded" instead wrote empty capabilities.
GENERAL_COMMAND_TYPES = frozenset(
    {
        "MoveUp", "MoveDown", "MoveLeft", "MoveRight", "PageUp", "PageDown",
        "PreviousLetter", "NextLetter", "ToggleOsd", "ToggleContextMenu", "Select",
        "Back", "TakeScreenshot", "SendKey", "SendString", "GoHome", "GoToSettings",
        "VolumeUp", "VolumeDown", "Mute", "Unmute", "ToggleMute", "SetVolume",
        "SetAudioStreamIndex", "SetSubtitleStreamIndex", "ToggleFullscreen",
        "DisplayContent", "GoToSearch", "DisplayMessage", "SetRepeatMode",
        "ChannelUp", "ChannelDown", "Guide", "ToggleStats", "PlayMediaSource",
        "PlayTrailers", "SetShuffleQueue", "PlayState", "PlayNext", "ToggleOsdMenu",
        "Play", "SetMaxStreamingBitrate", "SetPlaybackOrder",
    }
)


def test_advertised_commands_are_all_general_command_types() -> None:
    unknown = set(jellyfin_receiver.CAPABILITY_COMMANDS) - GENERAL_COMMAND_TYPES
    assert unknown == set(), f"not GeneralCommandType members: {sorted(unknown)}"


def test_capabilities_body_is_not_wrapped() -> None:
    """A wrapped body binds as an all-default DTO and erases capabilities.

    The server still answers 204, so nothing downstream notices.
    """
    payload = jellyfin_receiver.capabilities_payload()
    assert "Capabilities" not in payload
    assert payload["SupportsMediaControl"] is True
    assert payload["PlayableMediaTypes"] == ["Video", "Audio"]
    assert "PlayState" in payload["SupportedCommands"]


def test_register_posts_the_unwrapped_body_and_verifies_it(monkeypatch) -> None:
    posts: list[tuple[str, dict]] = []
    monkeypatch.setitem(jellyfin_receiver._STATUS, "enabled", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "running", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_url", "http://jf.lan:8096")
    monkeypatch.setitem(jellyfin_receiver._STATUS, "device_id", "relaytv-den")
    monkeypatch.setattr(
        jellyfin_receiver, "_post_json", lambda url, payload, timeout=3.0: posts.append((url, payload))
    )
    monkeypatch.setattr(
        jellyfin_receiver,
        "read_session_capabilities",
        lambda device_id="", timeout=3.0: {"DeviceId": device_id, "SupportsMediaControl": True},
    )

    out = jellyfin_receiver.register_receiver_once()

    assert out["ok"] is True
    assert out["verified"] is True
    assert len(posts) == 1
    url, body = posts[0]
    assert url == "http://jf.lan:8096/Sessions/Capabilities/Full"
    assert body == jellyfin_receiver.capabilities_payload()
    assert jellyfin_receiver._STATUS["media_control_verified"] is True


def test_register_fails_when_the_server_did_not_record_media_control(monkeypatch) -> None:
    """The exact live failure: 204 accepted, capabilities silently empty."""
    monkeypatch.setitem(jellyfin_receiver._STATUS, "enabled", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "running", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_url", "http://jf.lan:8096")
    monkeypatch.setitem(jellyfin_receiver._STATUS, "device_id", "relaytv-den")
    monkeypatch.setattr(jellyfin_receiver, "_post_json", lambda url, payload, timeout=3.0: None)
    monkeypatch.setattr(jellyfin_receiver, "_post_no_body", lambda url, timeout=3.0: None)
    monkeypatch.setattr(
        jellyfin_receiver,
        "read_session_capabilities",
        lambda device_id="", timeout=3.0: {"DeviceId": device_id, "SupportsMediaControl": False},
    )

    out = jellyfin_receiver.register_receiver_once()

    assert out["ok"] is False
    assert "media control" in str(out["error"])
    assert jellyfin_receiver._STATUS["connected"] is False


def test_register_accepts_a_server_that_hides_its_session_list(monkeypatch) -> None:
    """A proxy or an Emby build may not answer the readback; that is not a failure."""
    monkeypatch.setitem(jellyfin_receiver._STATUS, "enabled", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "running", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_url", "http://jf.lan:8096")
    monkeypatch.setitem(jellyfin_receiver._STATUS, "device_id", "relaytv-den")
    monkeypatch.setattr(jellyfin_receiver, "_post_json", lambda url, payload, timeout=3.0: None)

    def _boom(device_id="", timeout=3.0):
        raise RuntimeError("404")

    monkeypatch.setattr(jellyfin_receiver, "read_session_capabilities", _boom)

    out = jellyfin_receiver.register_receiver_once()

    assert out["ok"] is True
    assert out["verified"] is None


def test_register_falls_back_to_the_query_form_for_emby(monkeypatch) -> None:
    attempts: list[str] = []
    monkeypatch.setitem(jellyfin_receiver._STATUS, "enabled", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "running", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_url", "http://emby.lan:8096")
    monkeypatch.setitem(jellyfin_receiver._STATUS, "device_id", "relaytv-den")

    def _reject_body(url, payload, timeout=3.0):
        attempts.append(url)
        raise RuntimeError("HTTP 400")

    def _accept_query(url, timeout=3.0):
        attempts.append(url)

    monkeypatch.setattr(jellyfin_receiver, "_post_json", _reject_body)
    monkeypatch.setattr(jellyfin_receiver, "_post_no_body", _accept_query)
    monkeypatch.setattr(jellyfin_receiver, "read_session_capabilities", lambda device_id="", timeout=3.0: {})

    out = jellyfin_receiver.register_receiver_once()

    assert out["ok"] is True
    assert out["method"] == "caps_query"
    assert "/Sessions/Capabilities?" in attempts[1]
    assert "supportedCommands=PlayState" in attempts[1]


# --- remote-friendly playback reporting ------------------------------------


def test_play_pause_toggles_against_current_state(monkeypatch) -> None:
    calls: list[str] = []
    controls = {
        "stop": lambda: None,
        "pause": lambda: calls.append("pause"),
        "resume": lambda: calls.append("resume"),
        "seek": lambda sec: None,
        "next": lambda: None,
        "previous": lambda: None,
        "set_volume": lambda vol: None,
        "mute": lambda muted: None,
    }
    monkeypatch.setitem(jellyfin_receiver._STATUS, "enabled", True)
    monkeypatch.setattr(jellyfin_service, "emit_progress_hint", lambda: None)

    monkeypatch.setattr(jellyfin_service, "playback_is_paused", lambda: False)
    out = jellyfin_service.handle_command(
        FakeCommandReq(payload={"Command": "PlayPause"}), controls=controls, ui=_noop_ui()
    )
    assert out["action"] == "pause"

    monkeypatch.setattr(jellyfin_service, "playback_is_paused", lambda: True)
    out = jellyfin_service.handle_command(
        FakeCommandReq(payload={"Command": "PlayPause"}), controls=controls, ui=_noop_ui()
    )
    assert out["action"] == "resume"

    assert calls == ["pause", "resume"]


def test_progress_payload_lets_the_remote_scrub(monkeypatch) -> None:
    """Without CanSeek the Jellyfin remote renders a read-only progress bar."""
    monkeypatch.setattr(state, "NOW_PLAYING", {"jellyfin_item_id": "abc", "url": "http://jf/x"})
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(player, "mpv_get_many", lambda keys: {"pause": False, "time-pos": 12.0, "duration": 100.0})

    payload = jellyfin_service.progress_snapshot()

    assert payload["CanSeek"] is True
    assert payload["PlayMethod"] == "DirectStream"


def test_play_method_follows_the_selected_stream_mode() -> None:
    assert jellyfin_service.play_method({"jellyfin_stream_mode": "transcode"}) == "Transcode"
    assert jellyfin_service.play_method({"jellyfin_stream_mode": "direct"}) == "DirectStream"
    assert jellyfin_service.play_method({}) == "DirectStream"


# Every message the server is authorized to send by CAPABILITY_COMMANDS, in the
# shape the control socket delivers it. Advertising PlayState authorizes the
# whole PlaystateCommand family, not just the ones the web remote happens to
# use today.
ADVERTISED_TRAFFIC = [
    ("PlayState", {"MessageType": "Playstate", "Data": {"Command": "Pause"}}),
    ("PlayState", {"MessageType": "Playstate", "Data": {"Command": "Unpause"}}),
    ("PlayState", {"MessageType": "Playstate", "Data": {"Command": "PlayPause"}}),
    ("PlayState", {"MessageType": "Playstate", "Data": {"Command": "Stop"}}),
    ("PlayState", {"MessageType": "Playstate", "Data": {"Command": "Seek", "SeekPositionTicks": 10_000_000}}),
    ("PlayState", {"MessageType": "Playstate", "Data": {"Command": "NextTrack"}}),
    ("PlayState", {"MessageType": "Playstate", "Data": {"Command": "PreviousTrack"}}),
    ("PlayState", {"MessageType": "Playstate", "Data": {"Command": "Rewind"}}),
    ("PlayState", {"MessageType": "Playstate", "Data": {"Command": "FastForward"}}),
    ("SetVolume", {"MessageType": "GeneralCommand", "Data": {"Name": "SetVolume", "Arguments": {"Volume": "30"}}}),
    ("Mute", {"MessageType": "GeneralCommand", "Data": {"Name": "Mute"}}),
    ("Unmute", {"MessageType": "GeneralCommand", "Data": {"Name": "Unmute"}}),
    ("ToggleMute", {"MessageType": "GeneralCommand", "Data": {"Name": "ToggleMute"}}),
]


@pytest.mark.parametrize("capability,message", ADVERTISED_TRAFFIC, ids=lambda v: v if isinstance(v, str) else "")
def test_every_advertised_capability_reaches_a_handler(capability, message, monkeypatch) -> None:
    """Advertising a command RelayTV cannot execute puts a dead button on the remote.

    ToggleMute shipped exactly that way once: accepted by the server, 400 at the
    ingress, silent on the TV.
    """
    from relaytv_app.integrations import jellyfin_ws

    assert capability in jellyfin_receiver.CAPABILITY_COMMANDS

    dispatched: list[str] = []
    controls = {
        name: (lambda *a, _n=name, **kw: dispatched.append(_n))
        for name in ("stop", "pause", "resume", "seek", "seek_relative", "next", "previous", "set_volume", "mute")
    }
    monkeypatch.setitem(jellyfin_receiver._STATUS, "enabled", True)
    monkeypatch.setattr(jellyfin_service, "emit_progress_hint", lambda: None)
    monkeypatch.setattr(jellyfin_service, "playback_is_paused", lambda: False)
    monkeypatch.setattr(jellyfin_service, "playback_is_muted", lambda: False)

    routed = jellyfin_ws.normalize_message(message)
    assert routed is not None, f"{capability}: socket dropped the message"
    action, payload = routed

    out = jellyfin_service.handle_command(
        FakeCommandReq(action=action or None, payload=payload), controls=controls, ui=_noop_ui()
    )
    assert out["ok"] is True
    assert dispatched, f"{capability}: handled but dispatched nothing"


def test_toggle_mute_flips_both_ways(monkeypatch) -> None:
    calls: list[bool] = []
    controls = {
        "stop": lambda: None, "pause": lambda: None, "resume": lambda: None,
        "seek": lambda sec: None, "seek_relative": lambda delta: None,
        "next": lambda: None, "previous": lambda: None, "set_volume": lambda vol: None,
        "mute": lambda muted: calls.append(muted),
    }
    monkeypatch.setitem(jellyfin_receiver._STATUS, "enabled", True)
    monkeypatch.setattr(jellyfin_service, "emit_progress_hint", lambda: None)

    monkeypatch.setattr(jellyfin_service, "playback_is_muted", lambda: False)
    assert jellyfin_service.handle_command(
        FakeCommandReq(payload={"Name": "ToggleMute"}), controls=controls, ui=_noop_ui()
    )["action"] == "mute"

    monkeypatch.setattr(jellyfin_service, "playback_is_muted", lambda: True)
    assert jellyfin_service.handle_command(
        FakeCommandReq(payload={"Name": "ToggleMute"}), controls=controls, ui=_noop_ui()
    )["action"] == "unmute"

    assert calls == [True, False]


def test_skip_commands_seek_by_jellyfin_default_amounts(monkeypatch) -> None:
    deltas: list[float] = []
    controls = {
        "stop": lambda: None, "pause": lambda: None, "resume": lambda: None,
        "seek": lambda sec: None, "seek_relative": lambda delta: deltas.append(delta),
        "next": lambda: None, "previous": lambda: None, "set_volume": lambda vol: None,
        "mute": lambda muted: None,
    }
    monkeypatch.setitem(jellyfin_receiver._STATUS, "enabled", True)
    monkeypatch.setattr(jellyfin_service, "emit_progress_hint", lambda: None)

    jellyfin_service.handle_command(FakeCommandReq(payload={"Command": "Rewind"}), controls=controls, ui=_noop_ui())
    jellyfin_service.handle_command(FakeCommandReq(payload={"Command": "FastForward"}), controls=controls, ui=_noop_ui())

    assert deltas == [-10.0, 30.0]


def test_progress_stops_when_playback_stops(monkeypatch) -> None:
    """A stopped session must not keep reporting progress.

    NOW_PLAYING is deliberately retained after a stop so RelayTV's own UI can
    resume it. Reporting that to Jellyfin re-created the session's
    NowPlayingItem seconds after Stop cleared it, so every remote showed a
    stale paused entry indefinitely.
    """
    monkeypatch.setattr(state, "NOW_PLAYING", {"jellyfin_item_id": "abc", "url": "http://jf/x"})
    monkeypatch.setattr(player, "is_playing", lambda: False)
    monkeypatch.setattr(player, "mpv_get_many", lambda keys: {})

    assert jellyfin_service.progress_snapshot() is None


def test_progress_still_reports_a_paused_item(monkeypatch) -> None:
    """Pause is not stop: a paused mpv still reports playing, and the remote
    needs the position to keep its scrubber honest."""
    monkeypatch.setattr(state, "NOW_PLAYING", {"jellyfin_item_id": "abc", "url": "http://jf/x"})
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(player, "mpv_get_many", lambda keys: {"pause": True, "time-pos": 42.0, "duration": 100.0})

    payload = jellyfin_service.progress_snapshot()

    assert payload is not None
    assert payload["IsPaused"] is True
    assert payload["PositionTicks"] == 420_000_000
