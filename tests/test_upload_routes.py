# SPDX-License-Identifier: GPL-3.0-only
from fastapi.testclient import TestClient

from relaytv_app import upload_store
from relaytv_app import routes
from relaytv_app.main import create_app


def test_ingest_media_enqueue_route_uploads_and_queues(monkeypatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    queue_events: list[dict[str, object]] = []
    prefetch_calls: list[dict[str, object]] = []
    prime_calls: list[bool] = []
    toast_calls: list[tuple[dict[str, object], str]] = []
    persist_calls: list[bool] = []

    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)
    monkeypatch.setattr(routes.state, "NOW_PLAYING", {"url": "https://example.com/current.mp4"}, raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: persist_calls.append(True))
    monkeypatch.setattr(routes.player, "prefetch_queue_item_stream", lambda item: prefetch_calls.append(dict(item)))
    monkeypatch.setattr(routes.player, "prime_mpv_up_next_from_queue", lambda force=False: prime_calls.append(bool(force)))
    monkeypatch.setattr(routes, "_push_queue_added_toast_async", lambda item, label: toast_calls.append((dict(item), label)))
    monkeypatch.setattr(routes, "_ui_event_push_queue", lambda action, **payload: queue_events.append({"action": action, **payload}))

    client = TestClient(create_app(testing=True))
    response = client.post(
        "/ingest/media/enqueue",
        data={"title": "Queued Upload"},
        files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "enqueue"
    assert body["item"]["provider"] == "upload"
    assert body["result"]["status"] == "queued"
    assert body["result"]["queue_length"] == 1
    assert routes.state.QUEUE[0]["provider"] == "upload"
    assert persist_calls == [True]
    assert prefetch_calls[0]["provider"] == "upload"
    assert prime_calls == [True]
    assert toast_calls[0][0]["provider"] == "upload"
    assert queue_events[-1]["action"] == "add"
    assert queue_events[-1]["source"] == "ingest_media_enqueue"


def test_ingest_media_play_route_stores_and_plays_full_upload(monkeypatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    play_calls: list[dict[str, object]] = []

    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(upload_store, "progressive_start_ready", lambda meta, session: (False, "waiting_for_upload"))
    monkeypatch.setattr(
        routes.player,
        "play_item",
        lambda item, use_resolver=True, cec=False, clear_queue=False, mode="play_now": play_calls.append(
            {
                "item": dict(item),
                "use_resolver": use_resolver,
                "cec": cec,
                "clear_queue": clear_queue,
                "mode": mode,
            }
        )
        or {"url": item["url"], "provider": "upload", "title": item["title"]},
    )

    client = TestClient(create_app(testing=True))
    response = client.post(
        "/ingest/media/play",
        data={"title": "Played Upload"},
        files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["playback_mode"] == "full_upload"
    assert body["now_playing"]["provider"] == "upload"
    assert body["item"]["title"] == "Played Upload"
    assert len(play_calls) == 1
    assert play_calls[0]["item"]["title"] == "Played Upload"
    assert play_calls[0]["item"]["provider"] == "upload"
    assert play_calls[0]["use_resolver"] is False
    assert play_calls[0]["cec"] is False
    assert play_calls[0]["clear_queue"] is False
    assert play_calls[0]["mode"] == "ingest_media_play"
    assert str(play_calls[0]["item"]["_local_stream_path"]).endswith(".mp4")
