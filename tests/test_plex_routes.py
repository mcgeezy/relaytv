# SPDX-License-Identifier: GPL-3.0-only
from fastapi.testclient import TestClient

from relaytv_app.integrations import plex_auth, plex_service
from relaytv_app.integrations.plex_client import PlexBinaryResponse, PlexStreamResponse
from relaytv_app.main import create_app


def test_link_routes_bind_flow_to_http_only_cookie(monkeypatch) -> None:
    calls = []

    def start(secret):
        calls.append(("start", secret))
        return {
            "flow_id": "flow-1",
            "link_url": "https://app.plex.tv/auth#?code=abc",
            "expires_in": 1800,
        }

    def poll(flow_id, secret):
        calls.append(("poll", flow_id, secret))
        return {"linked": False, "pending": True, "expires_in": 1700}

    monkeypatch.setattr(plex_auth.auth_manager, "start_link", start)
    monkeypatch.setattr(plex_auth.auth_manager, "poll_link", poll)
    client = TestClient(create_app(testing=True))

    started = client.post("/integrations/plex/auth/start")
    polled = client.post(
        "/integrations/plex/auth/poll",
        json={"flow_id": "flow-1"},
    )

    assert started.status_code == 200
    assert "httponly" in started.headers["set-cookie"].lower()
    assert "samesite=strict" in started.headers["set-cookie"].lower()
    assert started.headers["cache-control"] == "no-store"
    assert polled.status_code == 200
    assert polled.headers["cache-control"] == "no-store"
    assert calls[0][0] == "start"
    assert calls[1] == ("poll", "flow-1", calls[0][1])


def test_plex_status_and_servers_never_add_cacheable_credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        plex_auth.auth_manager,
        "status",
        lambda: {
            "enabled": True,
            "linked": True,
            "account": {"username": "mark"},
            "server": {"machine_id": "server-1", "name": "Home Plex"},
        },
    )
    monkeypatch.setattr(
        plex_auth.auth_manager,
        "list_servers",
        lambda: [{"machine_id": "server-1", "name": "Home Plex"}],
    )
    client = TestClient(create_app(testing=True))

    status = client.get("/integrations/plex/status")
    servers = client.get("/plex/servers")

    assert status.status_code == 200
    assert servers.status_code == 200
    assert status.headers["cache-control"] == "no-store"
    assert servers.headers["cache-control"] == "no-store"
    assert "token" not in status.text.lower()
    assert "token" not in servers.text.lower()


def test_plex_errors_keep_upstream_secrets_out_of_route_response(monkeypatch) -> None:
    def fail():
        raise plex_auth.PlexAuthError(
            "plex_auth_expired",
            "Plex authentication has expired; link the account again",
            status_code=401,
            upstream_status=498,
        )

    monkeypatch.setattr(plex_auth.auth_manager, "test_selected_server", fail)
    response = TestClient(create_app(testing=True)).post("/integrations/plex/test")

    assert response.status_code == 401
    assert response.json()["detail"] == {
        "code": "plex_auth_expired",
        "message": "Plex authentication has expired; link the account again",
    }
    assert "498" not in response.text


def test_plex_catalog_routes_are_bounded_and_non_cacheable(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        plex_service.catalog_service,
        "home",
        lambda *, limit: calls.append(("home", limit)) or {"rows": []},
    )
    monkeypatch.setattr(
        plex_service.catalog_service,
        "libraries",
        lambda: calls.append(("libraries",)) or {"libraries": []},
    )
    monkeypatch.setattr(
        plex_service.catalog_service,
        "library_items",
        lambda library_id, **kwargs: calls.append(("items", library_id, kwargs))
        or {"items": []},
    )
    monkeypatch.setattr(
        plex_service.catalog_service,
        "search",
        lambda query, *, limit: calls.append(("search", query, limit))
        or {"items": []},
    )
    monkeypatch.setattr(
        plex_service.catalog_service,
        "item_detail",
        lambda item_id: calls.append(("detail", item_id)) or {"item": {}},
    )
    monkeypatch.setattr(
        plex_service.catalog_service,
        "children",
        lambda item_id, **kwargs: calls.append(("children", item_id, kwargs))
        or {"items": []},
    )
    client = TestClient(create_app(testing=True))

    responses = [
        client.get("/plex/home?limit=999"),
        client.get("/plex/libraries"),
        client.get("/plex/libraries/library-ref/items?start=-2&limit=999&sort=added"),
        client.get("/plex/search?q=general&limit=999"),
        client.get("/plex/items/item-ref"),
        client.get("/plex/items/show-ref/children?start=-1&limit=999"),
    ]

    assert all(response.status_code == 200 for response in responses)
    assert all(response.headers["cache-control"] == "no-store" for response in responses)
    assert calls == [
        ("home", 50),
        ("libraries",),
        ("items", "library-ref", {"start": 0, "limit": 100, "sort": "added"}),
        ("search", "general", 100),
        ("detail", "item-ref"),
        ("children", "show-ref", {"start": 0, "limit": 100}),
    ]


def test_plex_artwork_route_returns_private_nosniff_image(monkeypatch) -> None:
    monkeypatch.setattr(
        plex_service.catalog_service,
        "artwork",
        lambda asset_id: PlexBinaryResponse(
            body=f"image:{asset_id}".encode(),
            content_type="image/jpeg",
        ),
    )

    response = TestClient(create_app(testing=True)).get("/plex/artwork/asset-ref")

    assert response.status_code == 200
    assert response.content == b"image:asset-ref"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_plex_stream_forwards_range_and_private_response_headers(monkeypatch) -> None:
    class _Response:
        def __init__(self):
            self.chunks = [b"video", b""]

        def read(self, _size):
            return self.chunks.pop(0)

        def close(self):
            pass

    calls = []
    monkeypatch.setattr(
        plex_service.catalog_service,
        "media_stream",
        lambda stream_id, *, range_header: calls.append((stream_id, range_header))
        or PlexStreamResponse(
            status_code=206,
            headers={
                "Content-Type": "video/mp4",
                "Content-Length": "5",
                "Content-Range": "bytes 0-4/100",
            },
            _response=_Response(),
        ),
    )

    response = TestClient(create_app(testing=True)).get(
        "/plex/stream/stream-ref",
        headers={"Range": "bytes=0-4"},
    )

    assert response.status_code == 206
    assert response.content == b"video"
    assert response.headers["content-range"] == "bytes 0-4/100"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert calls == [("stream-ref", "bytes=0-4")]


def test_plex_action_route_dispatches_opaque_item(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        plex_service.catalog_service,
        "action",
        lambda item_id, command, *, version_id="", audio_id="", subtitle_id="": calls.append(
            (item_id, command, version_id, audio_id, subtitle_id)
        )
        or {"ok": True, "action": command},
    )

    response = TestClient(create_app(testing=True)).post(
        "/plex/items/action",
        json={
            "item_id": "opaque-item",
            "command": "play_next",
            "version_id": "opaque-part",
            "audio_id": "opaque-audio",
            "subtitle_id": "opaque-subtitle",
        },
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"ok": True, "action": "play_next"}
    assert calls == [
        (
            "opaque-item",
            "play_next",
            "opaque-part",
            "opaque-audio",
            "opaque-subtitle",
        )
    ]
