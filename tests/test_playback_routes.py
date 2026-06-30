# SPDX-License-Identifier: GPL-3.0-only
from fastapi.testclient import TestClient

from relaytv_app import routes
from relaytv_app.main import create_app


def test_play_now_route_preserves_current_and_uses_resolved_resume(monkeypatch) -> None:
    persisted: list[dict] = []
    queue_events: list[dict[str, object]] = []
    play_calls: list[dict[str, object]] = []

    monkeypatch.setattr(routes.player, "is_playing", lambda: True)
    monkeypatch.setattr(routes.player, "mpv_get", lambda prop: 37.0 if prop == "time-pos" else 120.0)
    monkeypatch.setattr(routes.player, "update_history_progress", lambda *args, **kwargs: None)
    monkeypatch.setattr(routes.state, "NOW_PLAYING", {"url": "https://example.com/current.mp4", "title": "Current"}, raising=False)
    monkeypatch.setattr(routes.state, "QUEUE", [{"url": "https://example.com/queued.mp4", "title": "Queued"}], raising=False)
    monkeypatch.setattr(routes.state, "persist_queue_payload", lambda payload: persisted.append(dict(payload)))
    monkeypatch.setattr(routes, "_push_overlay_toast", lambda **kwargs: None)
    monkeypatch.setattr(routes, "_ui_event_push_queue", lambda action, **payload: queue_events.append({"action": action, **payload}))

    def fake_play_item(item, use_resolver=True, cec=False, clear_queue=False, mode="play_now", start_pos=None):
        play_calls.append(
            {
                "item": dict(item) if isinstance(item, dict) else item,
                "use_resolver": use_resolver,
                "cec": cec,
                "clear_queue": clear_queue,
                "mode": mode,
                "start_pos": start_pos,
            }
        )
        return {"url": item["url"] if isinstance(item, dict) else item, "title": "History Item"}

    monkeypatch.setattr(routes.player, "play_item", fake_play_item)

    client = TestClient(create_app(testing=True))
    response = client.post(
        "/play_now",
        json={
            "url": "https://example.com/history.mp4",
            "title": "History Item",
            "resume_pos": 12.5,
            "history_id": "hist-1",
            "resolved_source_url": "https://example.com/watch",
            "resolved_stream": "https://cdn.example.com/history.mp4",
            "resolved_audio": "https://cdn.example.com/history.m4a",
            "resolved_at": 1234.5,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["preserved"]["url"] == "https://example.com/current.mp4"
    assert body["preserved"]["resume_pos"] == 37.0
    assert routes.state.QUEUE[0]["_relaytv_interrupt_preserved"] is True
    assert persisted[-1]["queue"][0]["url"] == "https://example.com/current.mp4"
    assert play_calls == [
        {
            "item": {
                "url": "https://example.com/history.mp4",
                "title": "History Item",
                "history_id": "hist-1",
                "_resolved_source_url": "https://example.com/watch",
                "_resolved_stream": "https://cdn.example.com/history.mp4",
                "_resolved_audio": "https://cdn.example.com/history.m4a",
                "_resolved_at": 1234.5,
            },
            "use_resolver": True,
            "cec": False,
            "clear_queue": False,
            "mode": "play_now",
            "start_pos": 12.5,
        }
    ]
    assert queue_events[-1]["action"] == "play_now"


def test_close_route_preserves_queue_and_returns_closed_session(monkeypatch) -> None:
    now_values: list[object] = []
    session_values: list[str] = []
    stop_shell_calls: list[bool] = []
    stop_mpv_calls: list[bool] = []
    queue = [{"url": "https://example.com/next.mp4", "title": "Next"}]

    monkeypatch.setattr(routes.state, "QUEUE", queue, raising=False)
    monkeypatch.setattr(routes.state, "NOW_PLAYING", {"url": "https://example.com/current.mp4", "title": "Current"}, raising=False)
    monkeypatch.setattr(routes.state, "set_now_playing", lambda value: now_values.append(value) or setattr(routes.state, "NOW_PLAYING", value))
    monkeypatch.setattr(routes.state, "set_session_position", lambda value: setattr(routes.state, "SESSION_POSITION", value))
    monkeypatch.setattr(routes.state, "set_session_state", lambda value: session_values.append(value) or setattr(routes.state, "SESSION_STATE", value))
    monkeypatch.setattr(routes.player, "native_qt_playback_explicitly_ended", lambda: False)
    monkeypatch.setattr(routes.player, "_idle_dashboard_enabled", lambda: True)
    monkeypatch.setattr(routes.player, "_qt_shell_backend_enabled", lambda: True)
    monkeypatch.setattr(routes.player, "mpv_get", lambda prop: 42.0 if prop == "time-pos" else 100.0)
    monkeypatch.setattr(routes.player, "stop_playback_keep_qt_shell", lambda: stop_shell_calls.append(True) or True)
    monkeypatch.setattr(routes.player, "stop_mpv", lambda restart_splash=True: stop_mpv_calls.append(bool(restart_splash)))
    monkeypatch.setattr(routes.player, "update_history_progress", lambda *args, **kwargs: None)
    monkeypatch.setattr(routes, "_jellyfin_emit_stopped_hint", lambda *args, **kwargs: None)

    client = TestClient(create_app(testing=True))
    response = client.post("/close")

    assert response.status_code == 200
    assert response.json()["status"] == "closed"
    assert response.json()["resume_available"] is True
    assert response.json()["kept_player_shell"] is True
    assert routes.state.QUEUE == queue
    assert now_values[-1]["closed"] is True
    assert now_values[-1]["resume_pos"] == 42.0
    assert session_values[-1] == "closed"
    assert stop_shell_calls == [True]
    assert stop_mpv_calls == []


def test_clear_now_playing_route_advances_queue_and_discards_temporary_state(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []
    routes._TEMP_PLAYBACK_STACK.clear()
    routes._TEMP_PLAYBACK_STACK.append(
        {
            "id": "frame-1",
            "resume": True,
            "snapshot": {"now_playing": {"url": "https://example.com/interrupted.mp4"}},
        }
    )

    monkeypatch.setattr(routes.state, "QUEUE", [{"url": "https://example.com/next.mp4"}], raising=False)
    monkeypatch.setattr(
        routes.player,
        "advance_queue_playback",
        lambda mode, prefer_playlist_next=True, poll_sleep=None: calls.append((mode, bool(prefer_playlist_next)))
        or {
            "status": "playing_next",
            "now_playing": {"title": "Next"},
            "method": "dequeue_play_item",
        },
    )

    try:
        client = TestClient(create_app(testing=True))
        response = client.post("/now_playing/clear")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "playing_next"
        assert body["now_playing"]["title"] == "Next"
        assert "method" not in body
        assert calls == [("next", True)]
        assert routes._TEMP_PLAYBACK_STACK == []
    finally:
        routes._TEMP_PLAYBACK_STACK.clear()


def test_clear_resumable_session_route_stops_and_clears_state(monkeypatch) -> None:
    now_values: list[object] = []
    session_positions: list[object] = []
    session_states: list[str] = []
    stop_calls: list[bool] = []
    persist_calls: list[bool] = []

    monkeypatch.setattr(routes.state, "NOW_PLAYING", {"url": "https://example.com/current.mp4"}, raising=False)
    monkeypatch.setattr(routes.state, "set_now_playing", lambda value: now_values.append(value) or setattr(routes.state, "NOW_PLAYING", value))
    monkeypatch.setattr(routes.state, "set_session_position", lambda value: session_positions.append(value))
    monkeypatch.setattr(routes.state, "set_session_state", lambda value: session_states.append(value) or setattr(routes.state, "SESSION_STATE", value))
    monkeypatch.setattr(routes.state, "persist_queue", lambda: persist_calls.append(True))
    monkeypatch.setattr(routes.player, "stop_mpv", lambda *args, **kwargs: stop_calls.append(True))

    client = TestClient(create_app(testing=True))
    response = client.post("/resume/clear")

    assert response.status_code == 200
    assert response.json() == {"status": "cleared", "resume_available": False}
    assert stop_calls == [True]
    assert now_values == [None]
    assert session_positions == [None]
    assert session_states == ["idle"]
    assert persist_calls == [True]


def test_stop_route_preserves_resumable_current_item(monkeypatch) -> None:
    now_values: list[object] = []
    session_positions: list[object] = []
    session_states: list[str] = []
    stop_calls: list[bool] = []
    history_calls: list[dict[str, object]] = []
    jellyfin_calls: list[tuple[object, object]] = []

    monkeypatch.setattr(routes.player, "is_playing", lambda: True)
    monkeypatch.setattr(routes.player, "native_qt_playback_explicitly_ended", lambda: False)
    monkeypatch.setattr(routes.player, "mpv_get", lambda prop: 31.5 if prop == "time-pos" else 120.0)
    monkeypatch.setattr(routes.player, "stop_mpv", lambda *args, **kwargs: stop_calls.append(True))
    monkeypatch.setattr(
        routes.player,
        "update_history_progress",
        lambda item, position_sec=None, duration_sec=None, force=False: history_calls.append(
            {"item": item, "position_sec": position_sec, "duration_sec": duration_sec, "force": force}
        ),
    )
    monkeypatch.setattr(routes, "_jellyfin_emit_stopped_hint", lambda pos, dur: jellyfin_calls.append((pos, dur)))
    monkeypatch.setattr(
        routes.state,
        "NOW_PLAYING",
        {"url": "https://example.com/current.mp4", "title": "Current", "jellyfin_item_id": "jf-1"},
        raising=False,
    )
    monkeypatch.setattr(routes.state, "set_now_playing", lambda value: now_values.append(value) or setattr(routes.state, "NOW_PLAYING", value))
    monkeypatch.setattr(routes.state, "set_session_position", lambda value: session_positions.append(value))
    monkeypatch.setattr(routes.state, "set_session_state", lambda value: session_states.append(value) or setattr(routes.state, "SESSION_STATE", value))

    client = TestClient(create_app(testing=True))
    response = client.post("/stop")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stopped"
    assert body["resume_available"] is True
    assert body["position"] == 31.5
    assert now_values[-1]["closed"] is True
    assert now_values[-1]["resume_pos"] == 31.5
    assert session_positions == [31.5]
    assert session_states == ["closed"]
    assert stop_calls == [True]
    assert history_calls[-1]["position_sec"] == 31.5
    assert history_calls[-1]["duration_sec"] == 120.0
    assert jellyfin_calls == [(31.5, 120.0)]


def test_resume_session_route_uses_resolved_stream_without_relookup(monkeypatch) -> None:
    load_calls: list[dict[str, object]] = []
    start_calls: list[dict[str, object]] = []

    monkeypatch.setattr(routes.state, "SESSION_STATE", "closed", raising=False)
    monkeypatch.setattr(routes.state, "SESSION_POSITION", 42.5, raising=False)
    monkeypatch.setattr(
        routes.state,
        "NOW_PLAYING",
        {
            "url": "https://youtube.com/watch?v=abc",
            "stream": "https://video.example/resolved.mp4",
            "audio": "https://audio.example/resolved.m4a",
            "resume_pos": 42.5,
        },
        raising=False,
    )
    monkeypatch.setattr(routes.state, "set_now_playing", lambda value: setattr(routes.state, "NOW_PLAYING", value))
    monkeypatch.setattr(routes.state, "set_session_state", lambda value: setattr(routes.state, "SESSION_STATE", value))
    monkeypatch.setattr(
        routes.player,
        "_load_stream_in_existing_mpv",
        lambda stream_url, audio_url=None, start_pos=None: load_calls.append(
            {"stream": stream_url, "audio": audio_url, "start_pos": start_pos}
        )
        or False,
    )
    monkeypatch.setattr(
        routes.player,
        "start_mpv",
        lambda stream_url, audio_url=None, start_pos=None: start_calls.append(
            {"stream": stream_url, "audio": audio_url, "start_pos": start_pos}
        ),
    )
    monkeypatch.setattr(routes.player, "mpv_set_result", lambda prop, value: {"error": "success", "request_id": "resume-ok"})
    monkeypatch.setattr(
        routes.player,
        "play_item",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("resume_session should reuse resolved stream")),
    )

    client = TestClient(create_app(testing=True))
    response = client.post("/resume_session")

    assert response.status_code == 200
    assert response.json()["status"] == "resumed"
    assert load_calls == [{"stream": "https://video.example/resolved.mp4", "audio": "https://audio.example/resolved.m4a", "start_pos": 42.5}]
    assert start_calls == [{"stream": "https://video.example/resolved.mp4", "audio": "https://audio.example/resolved.m4a", "start_pos": 42.5}]


def test_seek_volume_mute_routes_return_control_ack(monkeypatch) -> None:
    commands: list[list[object]] = []
    set_calls: list[tuple[str, object]] = []
    settings: list[dict[str, object]] = []

    monkeypatch.setattr(routes.state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0, raising=False)
    monkeypatch.setattr(routes.player, "_mark_playback_transition", lambda sec=None: None)
    monkeypatch.setattr(routes.player, "_qt_shell_runtime_accepts_mpv_commands", lambda: False)
    monkeypatch.setattr(routes.player, "mpv_get", lambda prop: 20.0 if prop == "volume" else False)
    monkeypatch.setattr(routes.player, "mpv_command", lambda cmd: commands.append(list(cmd)) or {"error": "success", "request_id": "cmd-ok"})
    monkeypatch.setattr(
        routes.player,
        "mpv_set_result",
        lambda prop, value: set_calls.append((prop, value)) or {"error": "success", "request_id": f"{prop}-ok"},
    )
    monkeypatch.setattr(routes.state, "update_settings", lambda patch: settings.append(dict(patch)) or patch)

    client = TestClient(create_app(testing=True))

    seek_response = client.post("/seek", json={"sec": 15})
    seek_abs_response = client.post("/seek_abs", json={"sec": 90})
    volume_response = client.post("/volume", json={"delta": 5})
    mute_response = client.post("/mute", json={"set": True})

    assert seek_response.status_code == 200
    assert seek_abs_response.status_code == 200
    assert volume_response.status_code == 200
    assert mute_response.status_code == 200
    assert commands == [["seek", 15.0, "relative"], ["seek", 90.0, "absolute"]]
    assert ("volume", 25.0) in set_calls
    assert ("mute", True) in set_calls
    assert settings[-1] == {"volume": 25.0}


def test_playback_state_route_reports_closed_stop_hold(monkeypatch) -> None:
    monkeypatch.setattr(routes.state, "SESSION_STATE", "closed", raising=False)
    monkeypatch.setattr(routes.state, "NOW_PLAYING", {"title": "Closed"}, raising=False)
    monkeypatch.setattr(routes.state, "QUEUE", [{"url": "https://example.com/queued.mp4"}], raising=False)
    monkeypatch.setattr(routes.state, "AUTO_NEXT_SUPPRESS_UNTIL", routes.time.time() + 3600.0, raising=False)
    monkeypatch.setattr(routes.player, "playback_transitioning", lambda: False)
    monkeypatch.setattr(routes.player, "auto_next_transitioning", lambda: False)
    monkeypatch.setattr(routes.player, "natural_idle_reset_holding", lambda: False)
    monkeypatch.setattr(
        routes.player,
        "qt_shell_runtime_telemetry",
        lambda **kwargs: {"selected": True, "available": True, "freshness": "fresh", "mpv_runtime_playback_active": True},
    )
    monkeypatch.setattr(routes.state, "update_playback_runtime_state", lambda next_state, reason="": {"playback_runtime_state": next_state, "playback_runtime_state_reason": reason})

    client = TestClient(create_app(testing=True))
    response = client.get("/playback/state")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "closed"
    assert body["playing"] is False
    assert body["transition_in_progress"] is False
    assert body["queue_length"] == 1
