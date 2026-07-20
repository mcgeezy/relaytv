# SPDX-License-Identifier: GPL-3.0-only
import json

from fastapi.testclient import TestClient

from relaytv_app.public_media import public_media_item, sanitize_public_url
from relaytv_app import routes
from relaytv_app.main import create_app


def test_sanitize_public_url_removes_credentials_and_signature() -> None:
    url = (
        "http://user:password@media.example/Videos/abc/stream"
        "?static=true&mediaSourceId=abc&api_key=secret&X-Amz-Signature=signed"
        "#private-fragment"
    )

    result = sanitize_public_url(url)

    assert result == "http://media.example/Videos/abc/stream?static=true&mediaSourceId=abc"
    assert "secret" not in result
    assert "password" not in result
    assert "signed" not in result


def test_sanitize_public_url_preserves_ipv6_hosts() -> None:
    url = "http://[fd00::a1]:8096/Videos/abc/stream?api_key=secret&static=true"

    result = sanitize_public_url(url)

    assert result == "http://[fd00::a1]:8096/Videos/abc/stream?static=true"


def test_public_media_item_prefers_safe_source_and_drops_private_runtime_fields() -> None:
    item = {
        "title": "Movie",
        "url": "https://media.example/direct.m3u8?token=secret",
        "_resolved_source_url": "https://media.example/watch/123?api_key=secret&lang=en",
        "_resolved_stream": "https://cdn.example/video?Policy=secret",
        "stream": "https://cdn.example/video?token=secret",
        "audio": "https://cdn.example/audio?token=secret",
        "headers": {"Authorization": "Bearer secret"},
        "thumbnail": "https://media.example/image/123?api_key=secret&width=400",
        "resume_pos": 42.5,
    }

    result = public_media_item(item)

    assert result == {
        "title": "Movie",
        "url": "https://media.example/watch/123?lang=en",
        "thumbnail": "https://media.example/image/123?width=400",
        "resume_pos": 42.5,
    }
    assert item["_resolved_stream"] == "https://cdn.example/video?Policy=secret"


def test_status_payload_redacts_runtime_media_without_mutating_state(monkeypatch) -> None:
    item = {
        "title": "Movie",
        "url": "https://media.example/Videos/123/stream?api_key=secret&static=true",
        "_resolved_stream": "https://cdn.example/video?token=secret",
    }
    monkeypatch.setattr(routes.state, "NOW_PLAYING", item, raising=False)
    monkeypatch.setattr(routes.state, "QUEUE", [dict(item)], raising=False)
    monkeypatch.setattr(routes.state, "SESSION_STATE", "closed", raising=False)
    monkeypatch.setattr(routes.player, "is_playing", lambda: False)

    payload = routes._status_payload()

    assert payload["now_playing"] == {
        "title": "Movie",
        "url": "https://media.example/Videos/123/stream?static=true",
    }
    assert payload["queue"] == [payload["now_playing"]]
    assert item["url"].endswith("api_key=secret&static=true")
    assert item["_resolved_stream"].endswith("token=secret")


def _secret_item() -> dict:
    return {
        "title": "Movie",
        "url": "https://media.example/Videos/123/stream?api_key=secret&static=true",
        "_resolved_stream": "https://cdn.example/video?token=secret",
    }


_SAFE_ITEM = {
    "title": "Movie",
    "url": "https://media.example/Videos/123/stream?static=true",
}


def test_enqueue_response_redacts_item_and_now_playing(monkeypatch) -> None:
    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)
    monkeypatch.setattr(routes.state, "NOW_PLAYING", _secret_item(), raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: None)
    monkeypatch.setattr(routes.state, "persist_queue_payload", lambda payload: None)
    monkeypatch.setattr(routes, "_smart_item_from_url", lambda url, **kwargs: _secret_item())
    monkeypatch.setattr(routes.player, "prefetch_queue_item_stream", lambda item: None)
    monkeypatch.setattr(routes.player, "prime_mpv_up_next_from_queue", lambda force=True: None)
    monkeypatch.setattr(routes, "_push_queue_added_toast_async", lambda *args, **kwargs: None)
    monkeypatch.setattr(routes, "_ui_event_push_queue", lambda action, **payload: None)
    client = TestClient(create_app(testing=True))

    response = client.post("/enqueue", json={"url": "https://media.example/watch"})

    assert response.status_code == 200
    body = response.json()
    assert body["item"] == _SAFE_ITEM
    assert body["now_playing"] == _SAFE_ITEM
    assert "secret" not in json.dumps(body)


def test_queue_remove_response_redacts_items_without_mutating_state(monkeypatch) -> None:
    monkeypatch.setattr(routes.state, "QUEUE", [_secret_item(), _secret_item()], raising=False)
    monkeypatch.setattr(routes.state, "persist_queue_payload", lambda payload: None)
    monkeypatch.setattr(routes.player, "prime_mpv_up_next_from_queue", lambda force=True: None)
    monkeypatch.setattr(routes, "_ui_event_push_queue", lambda action, **payload: None)
    client = TestClient(create_app(testing=True))

    response = client.post("/queue/remove", json={"index": 0})

    assert response.status_code == 200
    body = response.json()
    assert body["removed"] == _SAFE_ITEM
    assert body["queue"] == [_SAFE_ITEM]
    assert "secret" not in json.dumps(body)
    assert routes.state.QUEUE[0]["_resolved_stream"].endswith("token=secret")


def test_play_response_redacts_now_playing(monkeypatch) -> None:
    monkeypatch.setattr(routes, "_smart_item_from_url", lambda url, **kwargs: {"url": str(url)})
    monkeypatch.setattr(routes.playback_service, "suppress_auto_next", lambda sec: None)
    monkeypatch.setattr(routes.playback_service, "play_now", lambda *args, **kwargs: _secret_item())
    client = TestClient(create_app(testing=True))

    response = client.post("/play", json={"url": "https://media.example/watch"})

    assert response.status_code == 200
    assert response.json()["now_playing"] == _SAFE_ITEM
