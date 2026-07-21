# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from relaytv_app.config import runtime_config
from relaytv_app.integrations import iptv_service
from relaytv_app.main import create_app


PLAYLIST = """#EXTM3U
#EXTINF:-1 tvg-id="one.example" group-title="News",Channel One
https://stream.example/one.m3u8
#EXTINF:-1 tvg-id="two.example" group-title="News",Channel Two
https://stream.example/two.m3u8
"""


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("RELAYTV_IPTV_DB_PATH", str(tmp_path / "iptv.sqlite3"))
    monkeypatch.setenv("RELAYTV_IPTV_ENABLED", "1")
    runtime_config.refresh_from_env()
    iptv_service.reset_store_for_tests()
    yield TestClient(create_app(testing=True))
    iptv_service.reset_store_for_tests()


def test_iptv_source_catalog_favorite_visibility_and_reorder_routes(client) -> None:
    created = client.post(
        "/iptv/sources",
        json={"name": "Local list", "content": PLAYLIST, "refresh_now": True},
    )
    assert created.status_code == 200
    source = created.json()["source"]
    assert "location" not in source

    channels = client.get("/iptv/channels", params={"source_id": source["id"]})
    assert channels.status_code == 200
    items = channels.json()["items"]
    assert [item["name"] for item in items] == ["Channel One", "Channel Two"]
    assert all("stream_url" not in item for item in items)

    favorite = client.patch(
        f"/iptv/channels/{items[0]['channel_id']}",
        json={"source_id": source["id"], "favorite": True},
    )
    assert favorite.status_code == 200
    assert favorite.json()["channel"]["favorite"] is True

    favorites = client.get("/iptv/channels", params={"favorites": True})
    assert favorites.json()["total"] == 1

    hidden = client.post(
        "/iptv/channels/visibility",
        json={"source_id": source["id"], "group": "News", "hidden": True},
    )
    assert hidden.status_code == 200
    assert hidden.json()["updated"] == 2
    assert client.get("/iptv/channels", params={"source_id": source["id"]}).json()["total"] == 0

    all_items = client.get(
        "/iptv/channels", params={"source_id": source["id"], "visibility": "all"}
    ).json()["items"]
    moved = client.post(
        "/iptv/channels/reorder",
        json={
            "source_id": source["id"],
            "channel_id": all_items[1]["channel_id"],
            "before_channel_id": all_items[0]["channel_id"],
        },
    )
    assert moved.status_code == 200
    reordered = client.get(
        "/iptv/channels",
        params={"source_id": source["id"], "visibility": "all", "sort": "manual"},
    ).json()["items"]
    assert [item["name"] for item in reordered] == ["Channel Two", "Channel One"]


def test_iptv_channels_require_enabled_setting(client) -> None:
    runtime_config.set_value("RELAYTV_IPTV_ENABLED", "0")

    response = client.get("/iptv/channels")

    assert response.status_code == 503
    assert response.json()["detail"] == "IPTV is disabled in settings"


def test_iptv_action_response_never_exposes_stream_or_headers(client, monkeypatch) -> None:
    created = client.post(
        "/iptv/sources",
        json={"name": "Local list", "content": PLAYLIST, "refresh_now": True},
    ).json()
    source_id = created["source"]["id"]
    channel_id = client.get("/iptv/channels", params={"source_id": source_id}).json()["items"][0][
        "channel_id"
    ]
    monkeypatch.setattr(
        iptv_service.playback_service,
        "play_now",
        lambda item, **kwargs: dict(item),
    )

    response = client.post(
        f"/iptv/channels/{channel_id}/action",
        json={"source_id": source_id, "command": "play_now"},
    )

    assert response.status_code == 200
    now = response.json()["now_playing"]
    assert now["provider"] == "iptv"
    assert now["iptv_source_id"] == source_id
    assert "url" not in now
    assert "http_headers" not in now


def test_unavailable_is_hidden_by_default_and_can_be_explicitly_removed(client) -> None:
    created = client.post(
        "/iptv/sources",
        json={"name": "Local list", "content": PLAYLIST, "refresh_now": True},
    ).json()
    source_id = created["source"]["id"]
    channel = client.get("/iptv/channels", params={"source_id": source_id}).json()["items"][0]
    for _ in range(3):
        iptv_service.store().mark_channel_check(
            source_id, str(channel["channel_id"]), available=False
        )

    assert client.get("/iptv/channels", params={"source_id": source_id}).json()["total"] == 1
    included = client.get(
        "/iptv/channels",
        params={"source_id": source_id, "include_unavailable": True},
    ).json()
    assert included["total"] == 2

    removed = client.post(
        "/iptv/channels/remove-unavailable", json={"source_id": source_id}
    )
    assert removed.status_code == 200
    assert removed.json()["removed"] == 1
    assert client.get(
        "/iptv/channels",
        params={"source_id": source_id, "visibility": "all"},
    ).json()["total"] == 1


def test_iptv_directory_search_and_add_is_opt_in(client) -> None:
    directory = client.get("/iptv/directory", params={"q": "Free-TV"})

    assert directory.status_code == 200
    assert directory.json()["items"][0]["id"] == "free-tv"
    assert client.get("/iptv/sources").json()["items"] == []

    added = client.post("/iptv/directory/free-tv/add")
    assert added.status_code == 200
    assert added.json()["source"]["preset_id"] == "free-tv"
    assert len(client.get("/iptv/sources").json()["items"]) == 1
