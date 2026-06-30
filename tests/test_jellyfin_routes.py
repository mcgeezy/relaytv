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
