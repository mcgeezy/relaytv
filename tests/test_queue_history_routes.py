# SPDX-License-Identifier: GPL-3.0-only
from fastapi import HTTPException
from fastapi.testclient import TestClient

from relaytv_app import routes
from relaytv_app.main import create_app


def _client_with_queue_patches(monkeypatch):
    events: list[dict[str, object]] = []
    prime_calls: list[bool] = []

    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)
    monkeypatch.setattr(routes.state, "HISTORY", [], raising=False)
    monkeypatch.setattr(routes.state, "NOW_PLAYING", None, raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: None)
    monkeypatch.setattr(routes.state, "persist_history", lambda: None)
    monkeypatch.setattr(routes.state, "persist_queue_payload", lambda payload: None)
    monkeypatch.setattr(
        routes,
        "_smart_item_from_url",
        lambda url, **kwargs: {"url": str(url), "title": str(url).rsplit("/", 1)[-1], "lightweight": bool(kwargs.get("lightweight"))},
    )
    monkeypatch.setattr(routes.player, "prefetch_queue_item_stream", lambda item: None)
    monkeypatch.setattr(routes.player, "prime_mpv_up_next_from_queue", lambda force=True: prime_calls.append(bool(force)))
    monkeypatch.setattr(routes, "_push_queue_added_toast_async", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        routes,
        "_ui_event_push_queue",
        lambda action, **payload: events.append({"action": action, **payload}),
    )
    return TestClient(create_app(testing=True)), events, prime_calls


def test_queue_add_aliases_are_equivalent(monkeypatch) -> None:
    client, events, prime_calls = _client_with_queue_patches(monkeypatch)
    aliases = ["/enqueue", "/queue/add", "/api/queue/add", "/v1/queue/add"]

    for index, path in enumerate(aliases, start=1):
        response = client.post(path, json={"url": f"https://example.com/{index}.mp4"})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "queued"
        assert body["queue_length"] == index
        assert body["item"]["url"] == f"https://example.com/{index}.mp4"
        assert body["item"]["lightweight"] is True

    assert [item["url"] for item in routes.state.QUEUE] == [
        "https://example.com/1.mp4",
        "https://example.com/2.mp4",
        "https://example.com/3.mp4",
        "https://example.com/4.mp4",
    ]
    assert [event["action"] for event in events] == ["add", "add", "add", "add"]
    assert prime_calls == [True, True, True, True]


def test_queue_remove_move_dedupe_and_clear(monkeypatch) -> None:
    client, events, prime_calls = _client_with_queue_patches(monkeypatch)
    routes.state.QUEUE[:] = [
        {"url": "https://example.com/a.mp4", "title": "A"},
        {"url": "https://example.com/b.mp4", "title": "B"},
        {"url": "https://example.com/c.mp4", "title": "C"},
    ]

    removed = client.post("/queue/remove", json={"index": 1})
    assert removed.status_code == 200
    assert removed.json()["removed"]["url"] == "https://example.com/b.mp4"
    assert [item["url"] for item in routes.state.QUEUE] == ["https://example.com/a.mp4", "https://example.com/c.mp4"]

    moved = client.post("/queue/move", json={"from_index": 1, "to_index": 0})
    assert moved.status_code == 200
    assert [item["url"] for item in moved.json()["queue"]] == ["https://example.com/c.mp4", "https://example.com/a.mp4"]

    routes.state.QUEUE.append({"url": "https://example.com/a.mp4", "title": "A duplicate"})
    deduped = client.post("/queue/dedupe")
    assert deduped.status_code == 200
    assert deduped.json()["changed"] is True
    assert deduped.json()["removed_count"] == 1
    assert [item["url"] for item in deduped.json()["queue"]] == ["https://example.com/c.mp4", "https://example.com/a.mp4"]

    cleared = client.post("/clear")
    assert cleared.status_code == 200
    assert cleared.json() == {"status": "cleared"}
    assert routes.state.QUEUE == []

    assert [event["action"] for event in events] == ["remove", "move", "dedupe", "clear"]
    assert prime_calls == [True, True, True, True]


def test_queue_routes_publish_only_after_releasing_queue_lock(monkeypatch) -> None:
    client, _, _ = _client_with_queue_patches(monkeypatch)
    observed: list[bool] = []

    def _persist(_payload):
        acquired = routes.state.QUEUE_LOCK.acquire(blocking=False)
        observed.append(acquired)
        if acquired:
            routes.state.QUEUE_LOCK.release()

    monkeypatch.setattr(routes.state, "persist_queue_payload", _persist)
    routes.state.QUEUE[:] = [
        {"url": "https://example.com/a.mp4", "title": "A"},
        {"url": "https://example.com/b.mp4", "title": "B"},
    ]

    assert client.post("/queue/move", json={"from_index": 1, "to_index": 0}).status_code == 200
    routes.state.QUEUE.append({"url": "https://example.com/a.mp4", "title": "duplicate"})
    assert client.post("/queue/dedupe").status_code == 200

    assert observed == [True, True]


def test_queue_and_history_read_shapes(monkeypatch) -> None:
    client, _, _ = _client_with_queue_patches(monkeypatch)
    routes.state.NOW_PLAYING = {"url": "https://example.com/now.mp4", "title": "Now"}
    routes.state.QUEUE[:] = [{"url": "https://example.com/queued.mp4", "title": "Queued"}]
    routes.state.HISTORY[:] = [{"url": "https://example.com/history.mp4", "title": "History"}]

    queue_response = client.get("/queue")
    assert queue_response.status_code == 200
    assert queue_response.json() == {
        "now_playing": {"url": "https://example.com/now.mp4", "title": "Now"},
        "queue": [{"url": "https://example.com/queued.mp4", "title": "Queued"}],
        "queue_length": 1,
    }

    history_response = client.get("/history")
    assert history_response.status_code == 200
    history_body = history_response.json()
    assert history_body["history"] == [{"url": "https://example.com/history.mp4", "title": "History"}]
    assert history_body["history_length"] == 1
    assert history_body["limit"] == routes.state.HISTORY_LIMIT


def test_history_clear_and_play_delegate_to_play_now(monkeypatch) -> None:
    client, _, _ = _client_with_queue_patches(monkeypatch)
    play_now_requests: list[object] = []
    routes.state.HISTORY[:] = [
        {
            "url": "https://example.com/history.mp4",
            "title": "History Item",
            "thumbnail": "https://example.com/history.jpg",
            "resume_pos": 12.5,
            "history_id": "hist-1",
            "_resolved_source_url": "https://example.com/watch",
            "_resolved_stream": "https://cdn.example.com/history.mp4",
            "_resolved_audio": "https://cdn.example.com/history.m4a",
            "_resolved_at": 1234.5,
        }
    ]
    monkeypatch.setattr(
        routes,
        "play_now",
        lambda req: play_now_requests.append(req) or {"ok": True, "action": "played", "url": req.url, "resume_pos": req.resume_pos},
    )

    played = client.post("/history/play", json={"index": 0})
    assert played.status_code == 200
    assert played.json() == {"ok": True, "action": "played", "url": "https://example.com/history.mp4", "resume_pos": 12.5}
    assert len(play_now_requests) == 1
    req = play_now_requests[0]
    assert req.url == "https://example.com/history.mp4"
    assert req.preserve_current is True
    assert req.reason == "history"
    assert req.title == "History Item"
    assert req.thumbnail == "https://example.com/history.jpg"
    assert req.resume_pos == 12.5
    assert req.history_id == "hist-1"
    assert req.resolved_source_url == "https://example.com/watch"
    assert req.resolved_stream == "https://cdn.example.com/history.mp4"
    assert req.resolved_audio == "https://cdn.example.com/history.m4a"
    assert req.resolved_at == 1234.5

    cleared = client.post("/history/clear")
    assert cleared.status_code == 200
    assert cleared.json() == {"status": "cleared"}


def _seed_playable_queue() -> list[dict[str, object]]:
    items: list[dict[str, object]] = [
        {"url": "https://example.com/a.mp4", "title": "A"},
        {
            "url": "https://example.com/b.mp4",
            "title": "B",
            "thumbnail": "https://example.com/b.jpg",
            "resume_pos": 8.5,
            "history_id": "hist-b",
            "_resolved_source_url": "https://example.com/watch-b",
            "_resolved_stream": "https://cdn.example.com/b.mp4",
            "_resolved_audio": "https://cdn.example.com/b.m4a",
            "_resolved_at": 99.5,
        },
        {"url": "https://example.com/c.mp4", "title": "C"},
    ]
    routes.state.QUEUE[:] = items
    return items


def test_queue_play_delegates_and_consumes_item(monkeypatch) -> None:
    client, events, _ = _client_with_queue_patches(monkeypatch)
    _seed_playable_queue()
    play_now_calls: list[tuple[dict[str, object], dict[str, object]]] = []
    monkeypatch.setattr(
        routes,
        "_play_now_item",
        lambda item, **kwargs: play_now_calls.append((item, kwargs))
        or {"ok": True, "action": "played", "url": item["url"]},
    )

    played = client.post("/queue/play", json={"index": 1})

    assert played.status_code == 200
    assert played.json()["ok"] is True
    assert [item["url"] for item in played.json()["queue"]] == [
        "https://example.com/a.mp4",
        "https://example.com/c.mp4",
    ]
    assert played.json()["queue_length"] == 2
    assert [item["url"] for item in routes.state.QUEUE] == [
        "https://example.com/a.mp4",
        "https://example.com/c.mp4",
    ]
    assert len(play_now_calls) == 1
    item, kwargs = play_now_calls[0]
    assert item == {
        "url": "https://example.com/b.mp4",
        "title": "B",
        "thumbnail": "https://example.com/b.jpg",
        "resume_pos": 8.5,
        "history_id": "hist-b",
        "_resolved_source_url": "https://example.com/watch-b",
        "_resolved_stream": "https://cdn.example.com/b.mp4",
        "_resolved_audio": "https://cdn.example.com/b.m4a",
        "_resolved_at": 99.5,
    }
    assert kwargs == {
        "request_url": "https://example.com/b.mp4",
        "preserve_current": True,
        "reason": "queue_play",
        "title_hint": "B",
        "resume_pos": 8.5,
    }
    assert [event["action"] for event in events] == ["remove"]
    assert events[0]["queue_length"] == 2


def test_queue_play_preserves_opaque_provider_metadata(monkeypatch) -> None:
    client, _, _ = _client_with_queue_patches(monkeypatch)
    queued = {
        "url": "https://iptv.invalid/source-1/channel-1",
        "title": "News",
        "provider": "iptv",
        "iptv_source_id": "source-1",
        "iptv_channel_id": "channel-1",
    }
    routes.state.QUEUE[:] = [queued]
    played: list[dict[str, object]] = []

    monkeypatch.setattr(routes.playback_service, "suppress_auto_next", lambda _seconds: None)
    monkeypatch.setattr(routes.playback_service, "preserve_current_to_queue_front", lambda: None)
    monkeypatch.setattr(
        routes.playback_service,
        "play_now",
        lambda item, **_kwargs: played.append(item) or {"url": item["url"], "title": item["title"]},
    )
    monkeypatch.setattr(routes, "_push_overlay_toast", lambda **_kwargs: None)

    response = client.post("/queue/play", json={"index": 0})

    assert response.status_code == 200
    assert played == [queued]
    assert played[0]["provider"] == "iptv"
    assert played[0]["iptv_source_id"] == "source-1"
    assert played[0]["iptv_channel_id"] == "channel-1"
    assert routes.state.QUEUE == []


def test_queue_play_restores_item_when_play_fails(monkeypatch) -> None:
    client, events, _ = _client_with_queue_patches(monkeypatch)
    _seed_playable_queue()

    def _failing_play_now(_item, **_kwargs):
        raise HTTPException(status_code=400, detail="cannot resolve media")

    monkeypatch.setattr(routes, "_play_now_item", _failing_play_now)

    response = client.post("/queue/play", json={"index": 1})

    assert response.status_code == 400
    assert response.json()["detail"] == "cannot resolve media"
    # The failed play must not consume the item: original order is restored.
    assert [item["url"] for item in routes.state.QUEUE] == [
        "https://example.com/a.mp4",
        "https://example.com/b.mp4",
        "https://example.com/c.mp4",
    ]
    assert [event["action"] for event in events] == ["add"]
    assert events[0]["queue_length"] == 3


def test_queue_play_validates_index_and_item(monkeypatch) -> None:
    client, events, _ = _client_with_queue_patches(monkeypatch)
    routes.state.QUEUE[:] = [
        {"url": "https://example.com/a.mp4", "title": "A"},
        {"title": "no url"},
    ]

    assert client.post("/queue/play", json={"index": 5}).status_code == 400
    assert client.post("/queue/play", json={"index": -1}).status_code == 400
    missing_url = client.post("/queue/play", json={"index": 1})
    assert missing_url.status_code == 400
    assert missing_url.json()["detail"] == "queue item missing url"
    # A rejected item must stay where it was.
    assert len(routes.state.QUEUE) == 2
    assert routes.state.QUEUE[1] == {"title": "no url"}
    assert events == []
    assert routes.state.HISTORY == []
