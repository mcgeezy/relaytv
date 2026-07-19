# SPDX-License-Identifier: GPL-3.0-only
from relaytv_app.public_media import public_media_item, sanitize_public_url
from relaytv_app import routes


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
