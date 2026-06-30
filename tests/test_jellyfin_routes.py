# SPDX-License-Identifier: GPL-3.0-only
from fastapi.testclient import TestClient

from relaytv_app import routes
from relaytv_app.main import create_app


READY_STATUS = {
    "enabled": True,
    "running": True,
    "server_url": "http://jellyfin.local",
    "connected": True,
    "last_error": "",
    "device_name": "RelayTV",
}


def test_jellyfin_status_route_returns_receiver_status(monkeypatch) -> None:
    monkeypatch.setattr(routes.jellyfin_receiver, "status", lambda: {"enabled": True, "running": True})

    client = TestClient(create_app(testing=True))
    response = client.get("/integrations/jellyfin/status")

    assert response.status_code == 200
    assert response.json() == {"enabled": True, "running": True}


def test_jellyfin_cache_clear_route_requires_enabled_receiver(monkeypatch) -> None:
    monkeypatch.setattr(routes.jellyfin_receiver, "status", lambda: {"enabled": False})

    client = TestClient(create_app(testing=True))
    response = client.post("/integrations/jellyfin/catalog/cache_clear")

    assert response.status_code == 503
    assert response.json()["detail"] == "jellyfin integration disabled"


def test_jellyfin_cache_clear_route_emits_ui_event(monkeypatch) -> None:
    events: list[dict[str, object]] = []

    monkeypatch.setattr(routes.jellyfin_receiver, "status", lambda: {"enabled": True})
    monkeypatch.setattr(routes.jellyfin_receiver, "clear_catalog_cache", lambda reason: {"cleared": True, "reason": reason})
    monkeypatch.setattr(routes, "_ui_event_push_jellyfin", lambda event, **payload: events.append({"event": event, **payload}))

    client = TestClient(create_app(testing=True))
    response = client.post("/integrations/jellyfin/catalog/cache_clear")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "status": {"cleared": True, "reason": "manual_api"}}
    assert events == [
        {
            "event": "catalog_cache_clear",
            "refresh_active_tab": True,
            "refresh_settings": True,
            "refresh_status": True,
            "reason": "manual_api",
        }
    ]


def test_jellyfin_home_route_clamps_limit_and_reports_status(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(routes.jellyfin_receiver, "status", lambda: dict(READY_STATUS))
    monkeypatch.setattr(
        routes.jellyfin_receiver,
        "get_home_rows",
        lambda limit, refresh=False: calls.append({"limit": limit, "refresh": refresh})
        or {"rows": [{"title": "Continue"}], "generated_ts": 123.0},
    )

    client = TestClient(create_app(testing=True))
    response = client.get("/jellyfin/home", params={"limit": 999, "refresh": "true"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["rows"] == [{"title": "Continue"}]
    assert body["generated_ts"] == 123.0
    assert body["connected"] is True
    assert body["device_name"] == "RelayTV"
    assert calls == [{"limit": 60, "refresh": True}]


def test_jellyfin_search_route_requires_query_and_normalizes_limit(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(routes.jellyfin_receiver, "status", lambda: dict(READY_STATUS))
    monkeypatch.setattr(
        routes.jellyfin_receiver,
        "search_catalog",
        lambda query, limit, refresh=False: calls.append({"query": query, "limit": limit, "refresh": refresh})
        or {"query": query, "count": 1, "items": [{"title": "Movie"}]},
    )

    client = TestClient(create_app(testing=True))
    missing = client.get("/jellyfin/search", params={"q": "   "})
    response = client.get("/jellyfin/search", params={"q": " Movie ", "limit": 999})

    assert missing.status_code == 400
    assert missing.json()["detail"] == "q is required"
    assert response.status_code == 200
    assert response.json()["query"] == "Movie"
    assert response.json()["count"] == 1
    assert calls == [{"query": "Movie", "limit": 100, "refresh": False}]


def test_jellyfin_movies_and_series_routes_normalize_pagination(monkeypatch) -> None:
    movie_calls: list[dict[str, object]] = []
    series_calls: list[dict[str, object]] = []

    monkeypatch.setattr(routes.jellyfin_receiver, "status", lambda: dict(READY_STATUS))
    monkeypatch.setattr(
        routes.jellyfin_receiver,
        "list_movies",
        lambda **kwargs: movie_calls.append(dict(kwargs)) or {"sort": kwargs["sort"], "items": [], "count": 0},
    )
    monkeypatch.setattr(
        routes.jellyfin_receiver,
        "list_series",
        lambda **kwargs: series_calls.append(dict(kwargs)) or {"sort": kwargs["sort"], "items": [], "count": 0},
    )

    client = TestClient(create_app(testing=True))
    movies = client.get("/jellyfin/movies", params={"sort": "Title_Asc", "limit": 9999, "start": -5, "starts_with": " a "})
    series = client.get("/jellyfin/tv/series", params={"sort": "Added", "limit": 0, "start": -10})

    assert movies.status_code == 200
    assert series.status_code == 200
    assert movie_calls == [{"sort": "title_asc", "limit": 5000, "start_index": 0, "starts_with": "a", "refresh": False}]
    assert series_calls == [{"sort": "added", "limit": 1, "start_index": 0, "starts_with": "", "refresh": False}]


def test_jellyfin_item_detail_route_returns_not_found_payload(monkeypatch) -> None:
    monkeypatch.setattr(routes.jellyfin_receiver, "status", lambda: dict(READY_STATUS))
    monkeypatch.setattr(routes.jellyfin_receiver, "get_item_detail", lambda item_id, refresh=False: None)

    client = TestClient(create_app(testing=True))
    response = client.get("/jellyfin/item/item-1")

    assert response.status_code == 404
    assert response.json() == {
        "ok": False,
        "reason": "not_found",
        "item_id": "item-1",
        "connected": True,
        "last_error": "",
    }


def test_jellyfin_episode_routes_return_catalog_payloads(monkeypatch) -> None:
    monkeypatch.setattr(routes.jellyfin_receiver, "status", lambda: dict(READY_STATUS))
    monkeypatch.setattr(
        routes.jellyfin_receiver,
        "list_series_seasons",
        lambda series_id, refresh=False: {"seasons": [{"id": "season-1"}], "count": 1},
    )
    monkeypatch.setattr(
        routes.jellyfin_receiver,
        "list_series_episodes",
        lambda series_id, season_id="", season_number=None, refresh=False: {
            "season_id": season_id,
            "season_number": season_number,
            "episodes": [{"item_id": "ep-1"}],
            "count": 1,
        },
    )
    monkeypatch.setattr(
        routes.jellyfin_receiver,
        "get_adjacent_episodes",
        lambda item_id, refresh=False: {"prev": {"item_id": "prev"}, "next": {"item_id": "next"}},
    )

    client = TestClient(create_app(testing=True))
    seasons = client.get("/jellyfin/tv/series/series-1/seasons")
    episodes = client.get("/jellyfin/tv/series/series-1/episodes", params={"season_id": "season-1", "season_number": 2})
    adjacent = client.get("/jellyfin/item/item-1/adjacent")

    assert seasons.status_code == 200
    assert seasons.json()["seasons"] == [{"id": "season-1"}]
    assert episodes.status_code == 200
    assert episodes.json()["episodes"] == [{"item_id": "ep-1"}]
    assert episodes.json()["season_number"] == 2
    assert adjacent.status_code == 200
    assert adjacent.json()["prev"] == {"item_id": "prev"}
    assert adjacent.json()["next"] == {"item_id": "next"}


def test_jellyfin_audio_options_reports_runtime_selection(monkeypatch) -> None:
    monkeypatch.setattr(routes, "_require_jellyfin_catalog_ready", lambda: dict(READY_STATUS))
    monkeypatch.setattr(
        routes.state,
        "NOW_PLAYING",
        {
            "provider": "jellyfin",
            "jellyfin_item_id": "item-1",
            "url": "http://jellyfin.local/Videos/item-1/stream?audioStreamIndex=0",
        },
    )
    monkeypatch.setattr(
        routes.jellyfin_receiver,
        "get_item_detail",
        lambda item_id, refresh=False: {
            "item_id": item_id,
            "audio_streams": [
                {"index": 0, "language": "eng", "display": "English", "is_default": True},
                {"index": 1, "language": "jpn", "display": "Japanese", "is_default": False},
            ],
        },
    )
    monkeypatch.setattr(
        routes.player,
        "mpv_get",
        lambda prop: [
            {"type": "audio", "ff-index": 1, "lang": "jpn", "title": "Japanese", "selected": True},
        ]
        if prop == "track-list"
        else None,
    )

    client = TestClient(create_app(testing=True))
    response = client.get("/jellyfin/audio/options")

    assert response.status_code == 200
    body = response.json()
    assert body["current_audio_stream_index"] == 1
    assert body["current_audio_language"] == "jpn"
    assert body["options"][1]["is_current"] is True


def test_jellyfin_audio_select_switches_runtime_track_in_place(monkeypatch) -> None:
    settings_updates: list[dict[str, object]] = []
    emitted: list[bool] = []
    now_playing: list[dict[str, object]] = []

    monkeypatch.setattr(routes, "_require_jellyfin_catalog_ready", lambda: dict(READY_STATUS))
    monkeypatch.setattr(
        routes.state,
        "NOW_PLAYING",
        {
            "provider": "jellyfin",
            "title": "Movie",
            "jellyfin_item_id": "item-1",
            "jellyfin_audio_stream_index": "0",
            "url": "http://jellyfin.local/Videos/item-1/stream?audioStreamIndex=0",
        },
    )
    monkeypatch.setattr(
        routes.jellyfin_receiver,
        "get_item_detail",
        lambda item_id, refresh=False: {
            "item_id": item_id,
            "audio_streams": [
                {"index": 0, "language": "eng", "display": "English", "is_default": True},
                {"index": 1, "language": "jpn", "display": "Japanese", "is_default": False},
            ],
            "subtitle_streams": [],
        },
    )
    monkeypatch.setattr(routes.state, "update_settings", lambda data: settings_updates.append(dict(data)))
    monkeypatch.setattr(routes, "_retarget_jellyfin_queue_stream_preferences", lambda: 2)
    monkeypatch.setattr(routes.player, "is_playing", lambda: True)
    monkeypatch.setattr(routes.player, "mpv_get_many", lambda props: {"time-pos": 45.5, "pause": False})
    monkeypatch.setattr(routes, "_jellyfin_try_set_mpv_audio_track", lambda language="", display="": True)
    monkeypatch.setattr(routes.state, "set_now_playing", lambda data: now_playing.append(dict(data)))
    monkeypatch.setattr(routes, "_jellyfin_emit_progress_hint", lambda: emitted.append(True))

    client = TestClient(create_app(testing=True))
    response = client.post("/jellyfin/audio/select", json={"index": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "mpv_runtime_aid"
    assert body["current_audio_stream_index"] == 1
    assert body["current_audio_language"] == "jpn"
    assert body["queued_items_retargeted"] == 2
    assert settings_updates == [{"jellyfin_audio_lang": "jpn"}]
    assert emitted == [True]
    assert now_playing[-1]["jellyfin_audio_stream_index"] == "1"


def test_jellyfin_subtitle_select_rejects_unavailable_index(monkeypatch) -> None:
    monkeypatch.setattr(routes, "_require_jellyfin_catalog_ready", lambda: dict(READY_STATUS))
    monkeypatch.setattr(
        routes.state,
        "NOW_PLAYING",
        {
            "provider": "jellyfin",
            "jellyfin_item_id": "item-1",
            "url": "http://jellyfin.local/Videos/item-1/stream",
        },
    )
    monkeypatch.setattr(
        routes.jellyfin_receiver,
        "get_item_detail",
        lambda item_id, refresh=False: {
            "item_id": item_id,
            "subtitle_streams": [
                {"index": 2, "language": "eng", "display": "English", "is_default": True},
            ],
        },
    )

    client = TestClient(create_app(testing=True))
    response = client.post("/jellyfin/subtitle/select", json={"index": 9})

    assert response.status_code == 400
    assert response.json()["detail"] == "requested subtitle stream index is unavailable"


def test_jellyfin_series_play_all_builds_play_command(monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    monkeypatch.setattr(routes, "_require_jellyfin_catalog_ready", lambda: dict(READY_STATUS))
    monkeypatch.setattr(
        routes.jellyfin_receiver,
        "list_series_episodes",
        lambda series_id, refresh=False: {
            "episodes": [
                {"item_id": "ep-1", "series_name": "Series", "title": "Episode 1"},
                {"item_id": "ep-2", "series_name": "Series", "title": "Episode 2"},
            ],
        },
    )

    def fake_command(req):
        captured.append({"action": req.action, "payload": req.payload, "use_ytdlp": req.use_ytdlp})
        return {"ok": True, "started": True}

    monkeypatch.setattr(routes, "jellyfin_integration_command", fake_command)

    client = TestClient(create_app(testing=True))
    response = client.post("/jellyfin/tv/series/series-1/play_all", params={"refresh": "true"})

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "series_id": "series-1",
        "series_title": "Series",
        "queued_count": 1,
        "started_item_id": "ep-1",
        "play_result": {"ok": True, "started": True},
    }
    assert captured == [
        {
            "action": "Play",
            "payload": {"ItemIds": ["ep-1", "ep-2"], "PlayCommand": "PlayNow"},
            "use_ytdlp": False,
        }
    ]


def test_jellyfin_series_play_all_requires_episode_ids(monkeypatch) -> None:
    monkeypatch.setattr(routes, "_require_jellyfin_catalog_ready", lambda: dict(READY_STATUS))
    monkeypatch.setattr(routes.jellyfin_receiver, "list_series_episodes", lambda series_id, refresh=False: {"episodes": []})

    client = TestClient(create_app(testing=True))
    response = client.post("/jellyfin/tv/series/series-1/play_all")

    assert response.status_code == 404
    assert response.json()["detail"] == "no episodes available for series"


def test_jellyfin_item_action_maps_play_next_command(monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    monkeypatch.setattr(routes, "_require_jellyfin_catalog_ready", lambda: dict(READY_STATUS))
    monkeypatch.setattr(routes, "_jellyfin_should_suppress_duplicate_ui_action", lambda command, item_id, resume_pos: False)

    def fake_command(req):
        captured.append({"action": req.action, "payload": req.payload, "start_pos": req.start_pos, "use_ytdlp": req.use_ytdlp})
        return {"ok": True, "queued": True}

    monkeypatch.setattr(routes, "jellyfin_integration_command", fake_command)

    client = TestClient(create_app(testing=True))
    response = client.post("/jellyfin/action", json={"item_id": "item-1", "command": "play_next"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["ui_command"] == "play_next"
    assert body["item_id"] == "item-1"
    assert body["resolved_resume_pos"] is None
    assert captured == [
        {
            "action": "Play",
            "payload": {"ItemId": "item-1", "PlayCommand": "PlayNext"},
            "start_pos": None,
            "use_ytdlp": True,
        }
    ]


def test_jellyfin_item_action_resume_uses_item_detail_position(monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    monkeypatch.setattr(routes, "_require_jellyfin_catalog_ready", lambda: dict(READY_STATUS))
    monkeypatch.setattr(routes, "_jellyfin_should_suppress_duplicate_ui_action", lambda command, item_id, resume_pos: False)
    monkeypatch.setattr(routes.jellyfin_receiver, "get_item_detail", lambda item_id: {"resume_pos": 37.5})

    def fake_command(req):
        captured.append({"payload": req.payload, "start_pos": req.start_pos})
        return {"ok": True, "playing": True}

    monkeypatch.setattr(routes, "jellyfin_integration_command", fake_command)

    client = TestClient(create_app(testing=True))
    response = client.post("/jellyfin/action", json={"item_id": "item-1", "command": "resume"})

    assert response.status_code == 200
    body = response.json()
    assert body["resolved_resume_pos"] == 37.5
    assert captured == [{"payload": {"ItemId": "item-1", "PlayCommand": "PlayNow"}, "start_pos": 37.5}]


def test_jellyfin_item_action_suppresses_duplicate_ui_action(monkeypatch) -> None:
    commands: list[object] = []

    monkeypatch.setattr(routes, "_require_jellyfin_catalog_ready", lambda: dict(READY_STATUS))
    monkeypatch.setattr(routes, "_jellyfin_should_suppress_duplicate_ui_action", lambda command, item_id, resume_pos: True)
    monkeypatch.setattr(routes, "jellyfin_integration_command", lambda req: commands.append(req))

    client = TestClient(create_app(testing=True))
    response = client.post("/jellyfin/action", json={"item_id": "item-1", "command": "resume", "resume_pos": 12.0})

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "action": "ui_suppressed",
        "suppressed_duplicate_ui_action": True,
        "ui_command": "resume",
        "item_id": "item-1",
        "resolved_resume_pos": 12.0,
    }
    assert commands == []


def test_jellyfin_connect_uses_settings_device_name_and_registers(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    resets: list[bool] = []

    monkeypatch.setattr(routes.state, "get_settings", lambda: {"device_name": "Living Room TV"})

    def fake_connect(*, server_url, api_key=None, device_name=None, heartbeat_sec=None):
        calls.append(
            {
                "server_url": server_url,
                "api_key": api_key,
                "device_name": device_name,
                "heartbeat_sec": heartbeat_sec,
            }
        )
        return {"ok": True, "connected": True}

    monkeypatch.setattr(routes.jellyfin_receiver, "connect", fake_connect)
    monkeypatch.setattr(routes.jellyfin_receiver, "register_receiver_once", lambda: {"ok": True, "registered": True})
    monkeypatch.setattr(routes, "_reset_jellyfin_command_state", lambda: resets.append(True))
    monkeypatch.setattr(routes, "_ui_event_push_jellyfin", lambda event, **payload: events.append({"event": event, **payload}))

    client = TestClient(create_app(testing=True))
    response = client.post(
        "/integrations/jellyfin/connect",
        json={
            "server_url": " http://jellyfin.local/ ",
            "api_key": "secret",
            "heartbeat_sec": 15,
            "register_now": True,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "connected": True, "register": {"ok": True, "registered": True}}
    assert calls == [
        {
            "server_url": "http://jellyfin.local/",
            "api_key": "secret",
            "device_name": "Living Room TV",
            "heartbeat_sec": 15,
        }
    ]
    assert resets == [True]
    assert events == [
        {
            "event": "connect",
            "refresh_active_tab": True,
            "refresh_settings": True,
            "refresh_status": True,
        }
    ]


def test_jellyfin_connect_requires_server_url(monkeypatch) -> None:
    calls: list[object] = []

    monkeypatch.setattr(routes.jellyfin_receiver, "connect", lambda **kwargs: calls.append(kwargs))

    client = TestClient(create_app(testing=True))
    response = client.post("/integrations/jellyfin/connect", json={"server_url": "   "})

    assert response.status_code == 400
    assert response.json()["detail"] == "server_url is required"
    assert calls == []


def test_jellyfin_disconnect_resets_and_emits_event(monkeypatch) -> None:
    events: list[dict[str, object]] = []
    resets: list[bool] = []

    monkeypatch.setattr(routes, "_reset_jellyfin_command_state", lambda: resets.append(True))
    monkeypatch.setattr(routes.jellyfin_receiver, "disconnect", lambda: {"ok": True, "enabled": False})
    monkeypatch.setattr(routes, "_ui_event_push_jellyfin", lambda event, **payload: events.append({"event": event, **payload}))

    client = TestClient(create_app(testing=True))
    response = client.post("/integrations/jellyfin/disconnect")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "enabled": False}
    assert resets == [True]
    assert events == [
        {
            "event": "disconnect",
            "refresh_active_tab": True,
            "refresh_settings": True,
            "refresh_status": True,
        }
    ]


def test_jellyfin_register_requires_enabled_receiver(monkeypatch) -> None:
    calls: list[bool] = []

    monkeypatch.setattr(routes.jellyfin_receiver, "status", lambda: {"enabled": False})
    monkeypatch.setattr(routes.jellyfin_receiver, "register_receiver_once", lambda: calls.append(True))

    client = TestClient(create_app(testing=True))
    response = client.post("/integrations/jellyfin/register")

    assert response.status_code == 503
    assert response.json()["detail"] == "jellyfin integration disabled"
    assert calls == []


def test_jellyfin_register_returns_accepted_for_pending_handshake(monkeypatch) -> None:
    events: list[dict[str, object]] = []

    monkeypatch.setattr(routes.jellyfin_receiver, "status", lambda: {"enabled": True})
    monkeypatch.setattr(routes.jellyfin_receiver, "register_receiver_once", lambda: {"ok": False, "pending": True})
    monkeypatch.setattr(routes, "_ui_event_push_jellyfin", lambda event, **payload: events.append({"event": event, **payload}))

    client = TestClient(create_app(testing=True))
    response = client.post("/integrations/jellyfin/register")

    assert response.status_code == 202
    assert response.json() == {"ok": False, "pending": True}
    assert events == [
        {
            "event": "register",
            "refresh_active_tab": True,
            "refresh_settings": True,
            "refresh_status": True,
        }
    ]


def test_jellyfin_push_route_is_deprecated() -> None:
    client = TestClient(create_app(testing=True))
    response = client.post("/integrations/jellyfin/push")

    assert response.status_code == 410
    assert "deprecated" in response.json()["detail"]


def test_jellyfin_command_pause_dispatches_playback_control(monkeypatch) -> None:
    receiver_events: list[tuple[str, object]] = []
    progress_hints: list[bool] = []
    pauses: list[bool] = []

    monkeypatch.setattr(routes.jellyfin_receiver, "status", lambda: {"enabled": True})
    monkeypatch.setattr(routes.jellyfin_receiver, "mark_command", lambda action: receiver_events.append(("command", action)))
    monkeypatch.setattr(routes.jellyfin_receiver, "mark_heartbeat", lambda: receiver_events.append(("heartbeat", True)))
    monkeypatch.setattr(routes, "pause", lambda: pauses.append(True) or {"paused": True})
    monkeypatch.setattr(routes, "_jellyfin_emit_progress_hint", lambda: progress_hints.append(True))

    client = TestClient(create_app(testing=True))
    response = client.post("/integrations/jellyfin/command", json={"action": "Pause"})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "action": "pause", "result": {"paused": True}}
    assert receiver_events == [("command", "pause"), ("heartbeat", True)]
    assert pauses == [True]
    assert progress_hints == [True]


def test_jellyfin_command_requires_enabled_receiver(monkeypatch) -> None:
    monkeypatch.setattr(routes.jellyfin_receiver, "status", lambda: {"enabled": False})

    client = TestClient(create_app(testing=True))
    response = client.post("/integrations/jellyfin/command", json={"action": "Pause"})

    assert response.status_code == 503
    assert response.json()["detail"] == "jellyfin integration disabled"


def test_jellyfin_heartbeat_returns_accepted_for_pending_push(monkeypatch) -> None:
    monkeypatch.setattr(routes.jellyfin_receiver, "status", lambda: {"enabled": True})
    monkeypatch.setattr(routes.jellyfin_receiver, "send_progress_once", lambda: {"ok": False, "pending": True})

    client = TestClient(create_app(testing=True))
    response = client.post("/integrations/jellyfin/heartbeat")

    assert response.status_code == 202
    assert response.json() == {"ok": False, "pending": True}


def test_jellyfin_progress_snapshot_returns_payload(monkeypatch) -> None:
    monkeypatch.setattr(routes.jellyfin_receiver, "status", lambda: {"enabled": True})
    monkeypatch.setattr(routes, "_jellyfin_progress_snapshot", lambda: {"ItemId": "item-1", "PositionTicks": 120})

    client = TestClient(create_app(testing=True))
    response = client.get("/integrations/jellyfin/progress_snapshot")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "payload": {"ItemId": "item-1", "PositionTicks": 120}}


def test_jellyfin_progress_snapshot_returns_accepted_without_payload(monkeypatch) -> None:
    monkeypatch.setattr(routes.jellyfin_receiver, "status", lambda: {"enabled": True})
    monkeypatch.setattr(routes, "_jellyfin_progress_snapshot", lambda: None)

    client = TestClient(create_app(testing=True))
    response = client.get("/integrations/jellyfin/progress_snapshot")

    assert response.status_code == 202
    assert response.json() == {"ok": False, "reason": "no_payload"}


def test_jellyfin_stopped_route_sends_snapshot(monkeypatch) -> None:
    sent: list[dict[str, object]] = []

    monkeypatch.setattr(routes.jellyfin_receiver, "status", lambda: {"enabled": True})
    monkeypatch.setattr(routes, "_jellyfin_stopped_snapshot", lambda: {"ItemId": "item-1", "PositionTicks": 250})
    monkeypatch.setattr(
        routes.jellyfin_receiver,
        "send_playback_stopped_once",
        lambda payload: sent.append(dict(payload)) or {"ok": True, "sent": True},
    )

    client = TestClient(create_app(testing=True))
    response = client.post("/integrations/jellyfin/stopped")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "payload": {"ItemId": "item-1", "PositionTicks": 250},
        "result": {"ok": True, "sent": True},
    }
    assert sent == [{"ItemId": "item-1", "PositionTicks": 250}]


def test_jellyfin_stopped_snapshot_returns_accepted_without_payload(monkeypatch) -> None:
    monkeypatch.setattr(routes.jellyfin_receiver, "status", lambda: {"enabled": True})
    monkeypatch.setattr(routes, "_jellyfin_stopped_snapshot", lambda: None)

    client = TestClient(create_app(testing=True))
    response = client.get("/integrations/jellyfin/stopped_snapshot")

    assert response.status_code == 202
    assert response.json() == {"ok": False, "reason": "no_payload"}
