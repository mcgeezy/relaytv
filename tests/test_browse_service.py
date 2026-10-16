# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from relaytv_app import state
from relaytv_app.integrations import browse_service
from relaytv_app.main import create_app


@pytest.fixture(autouse=True)
def _isolated_browse_state(tmp_path, monkeypatch):
    monkeypatch.setattr(
        browse_service,
        "_servers_path",
        lambda: str(tmp_path / "media_servers.json"),
    )
    browse_service._SERVER_METRICS.clear()
    browse_service._SERIES_GROUPS.clear()
    browse_service._CUSTOM_JF_SESSIONS.clear()
    yield
    browse_service._SERVER_METRICS.clear()
    browse_service._SERIES_GROUPS.clear()
    browse_service._CUSTOM_JF_SESSIONS.clear()


def test_multi_server_crud_and_latency_sorting(monkeypatch) -> None:
    monkeypatch.setattr(state, "get_settings", lambda: {})
    monkeypatch.setattr(
        browse_service.jellyfin_receiver,
        "status",
        lambda: {"enabled": False},
    )
    monkeypatch.setattr(
        browse_service.plex_auth.auth_manager,
        "status",
        lambda: {"linked": False},
    )

    def _fake_probe(server: dict) -> dict:
        sid = server["id"]
        url = server["server_url"]
        lat = 18.0 if "slow" in url else 4.2
        browse_service._record_latency(sid, lat, connected=True, server_name=server["name"])
        return browse_service._public_server_entry(server)

    monkeypatch.setattr(browse_service, "probe_single_server", _fake_probe)

    client = TestClient(create_app(testing=True))

    r1 = client.post(
        "/browse/servers",
        json={
            "type": "jellyfin",
            "name": "Slow Jellyfin",
            "server_url": "http://slow.jf.lan:8096",
            "api_key": "secret-1",
            "user_id": "u-1",
        },
    )
    assert r1.status_code == 200
    s1 = r1.json()["server"]
    assert s1["name"] == "Slow Jellyfin"
    assert s1["latency_ms"] == 18.0
    assert s1["has_api_key"] is True
    assert "api_key" not in s1

    r2 = client.post(
        "/browse/servers",
        json={
            "type": "plex",
            "name": "Fast Plex",
            "server_url": "http://fast.plex.lan:32400",
            "token": "plex-tok",
        },
    )
    assert r2.status_code == 200
    s2 = r2.json()["server"]
    assert s2["latency_ms"] == 4.2

    r3 = client.post(
        "/browse/servers",
        json={
            "type": "emby",
            "name": "Backup Emby",
            "server_url": "http://slow-emby.lan:8096",
            "api_key": "emby-key",
        },
    )
    assert r3.status_code == 200
    s3 = r3.json()["server"]

    listed = client.get("/browse/servers").json()
    assert listed["connected_count"] == 3
    # Sorted by lowest latency first
    assert listed["servers"][0]["id"] == s2["id"]
    assert listed["servers"][0]["name"] == "Fast Plex"

    # Delete Backup Emby
    deleted = client.delete(f"/browse/servers/{s3['id']}")
    assert deleted.status_code == 200
    remaining_ids = [s["id"] for s in deleted.json()["servers"]]
    assert s3["id"] not in remaining_ids


def test_deduplicate_items_selects_lowest_latency_server_and_merges_resume() -> None:
    browse_service._record_latency("srv_jf_slow", 42.5, connected=True, server_name="NAS Jellyfin")
    browse_service._record_latency("srv_plex_fast", 5.1, connected=True, server_name="NVMe Plex")
    browse_service._record_latency("srv_emby_mid", 14.0, connected=True, server_name="Room Emby")

    items = [
        {
            "item_id": browse_service.encode_item_id("srv_jf_slow", "jf-101"),
            "raw_item_id": "jf-101",
            "server_id": "srv_jf_slow",
            "server_name": "NAS Jellyfin",
            "server_type": "jellyfin",
            "type": "movie",
            "title": "Dune: Part Two",
            "year": "2024",
            "tmdb_id": "693134",
            "resume_pos": 1800.0,
            "progress_percent": 30.0,
            "overview": "Paul Atreides unites with Chani and the Fremen.",
        },
        {
            "item_id": browse_service.encode_item_id("srv_plex_fast", "plex-555"),
            "raw_item_id": "plex-555",
            "server_id": "srv_plex_fast",
            "server_name": "NVMe Plex",
            "server_type": "plex",
            "type": "movie",
            "title": "Dune Part Two",
            "year": "2024",
            "tmdb_id": "693134",
            "resume_pos": 600.0,
            "progress_percent": 10.0,
            "overview": "",
        },
        {
            "item_id": browse_service.encode_item_id("srv_emby_mid", "emby-999"),
            "raw_item_id": "emby-999",
            "server_id": "srv_emby_mid",
            "server_name": "Room Emby",
            "server_type": "emby",
            "type": "movie",
            "title": "Dune: Part Two (2024)",
            "year": "2024",
            "resume_pos": 0.0,
            "progress_percent": 0.0,
            "overview": "",
        },
    ]

    deduped = browse_service.deduplicate_items(items)
    assert len(deduped) == 1
    winner = deduped[0]
    # Lowest latency server (srv_plex_fast @ 5.1ms) wins playback identity
    assert winner["server_id"] == "srv_plex_fast"
    assert winner["item_id"] == browse_service.encode_item_id("srv_plex_fast", "plex-555")
    assert winner["latency_ms"] == 5.1
    assert winner["duplicate_count"] == 3
    # Furthest resume position and richer metadata from slower server are preserved
    assert winner["resume_pos"] == 1800.0
    assert winner["progress_percent"] == 30.0
    assert "Paul Atreides" in winner["overview"]


def test_unified_home_and_combined_movies_tv_across_services(monkeypatch) -> None:
    servers = [
        {
            "id": "jf_primary",
            "name": "Jellyfin",
            "type": "jellyfin",
            "origin": "primary",
            "server_url": "http://jf.lan:8096",
            "enabled": True,
            "connected": True,
        },
        {
            "id": "plex_primary",
            "name": "Plex",
            "type": "plex",
            "origin": "primary",
            "server_url": "http://plex.lan:32400",
            "enabled": True,
            "connected": True,
        },
    ]
    monkeypatch.setattr(
        browse_service,
        "_active_servers",
        lambda: list(servers),
    )
    monkeypatch.setattr(
        browse_service,
        "_collect_configured_servers",
        lambda **_kw: list(servers),
    )
    browse_service._record_latency("jf_primary", 22.0, connected=True, server_name="Jellyfin")
    browse_service._record_latency("plex_primary", 6.0, connected=True, server_name="Plex")

    def _fake_home_buckets(server: dict, *, limit: int = 24, refresh: bool = False):
        if server["id"] == "jf_primary":
            return {
                "continue_watching_movies": [
                    {
                        "item_id": "ms~jf_primary~m1",
                        "raw_item_id": "m1",
                        "server_id": "jf_primary",
                        "server_name": "Jellyfin",
                        "server_type": "jellyfin",
                        "type": "movie",
                        "title": "Blade Runner 2049",
                        "year": "2017",
                        "resume_pos": 2400.0,
                    }
                ],
                "continue_watching_tv": [
                    {
                        "item_id": "ms~jf_primary~e1",
                        "raw_item_id": "e1",
                        "server_id": "jf_primary",
                        "server_name": "Jellyfin",
                        "server_type": "jellyfin",
                        "type": "episode",
                        "title": "Severance",
                        "series_name": "Severance",
                        "season_number": 1,
                        "episode_number": 4,
                        "resume_pos": 900.0,
                    }
                ],
                "recently_added_movies": [
                    {
                        "item_id": "ms~jf_primary~m1",
                        "raw_item_id": "m1",
                        "server_id": "jf_primary",
                        "server_name": "Jellyfin",
                        "server_type": "jellyfin",
                        "type": "movie",
                        "title": "Blade Runner 2049",
                        "year": "2017",
                    }
                ],
                "recently_added_tv": [
                    {
                        "item_id": "ms~jf_primary~s1",
                        "raw_item_id": "s1",
                        "server_id": "jf_primary",
                        "server_name": "Jellyfin",
                        "server_type": "jellyfin",
                        "type": "series",
                        "title": "Severance",
                        "year": "2022",
                    }
                ],
            }
        return {
            "continue_watching_movies": [
                {
                    "item_id": "ms~plex_primary~pm1",
                    "raw_item_id": "pm1",
                    "server_id": "plex_primary",
                    "server_name": "Plex",
                    "server_type": "plex",
                    "type": "movie",
                    "title": "Blade Runner 2049",
                    "year": "2017",
                    "resume_pos": 1200.0,
                }
            ],
            "continue_watching_tv": [],
            "recently_added_movies": [
                {
                    "item_id": "ms~plex_primary~pm1",
                    "raw_item_id": "pm1",
                    "server_id": "plex_primary",
                    "server_name": "Plex",
                    "server_type": "plex",
                    "type": "movie",
                    "title": "Blade Runner 2049",
                    "year": "2017",
                },
                {
                    "item_id": "ms~plex_primary~pm2",
                    "raw_item_id": "pm2",
                    "server_id": "plex_primary",
                    "server_name": "Plex",
                    "server_type": "plex",
                    "type": "movie",
                    "title": "Arrival",
                    "year": "2016",
                },
            ],
            "recently_added_tv": [
                {
                    "item_id": "ms~plex_primary~ps2",
                    "raw_item_id": "ps2",
                    "server_id": "plex_primary",
                    "server_name": "Plex",
                    "server_type": "plex",
                    "type": "series",
                    "title": "Andor",
                    "year": "2022",
                }
            ],
        }

    def _fake_catalog_items(
        server: dict,
        *,
        kind: str,
        sort: str,
        limit: int,
        start: int,
        starts_with: str,
        refresh: bool,
    ):
        buckets = _fake_home_buckets(server)
        if kind == "movie":
            items = buckets["recently_added_movies"]
        else:
            items = buckets["recently_added_tv"]
        return items, len(items)

    monkeypatch.setattr(browse_service, "_fetch_server_home_buckets", _fake_home_buckets)
    monkeypatch.setattr(browse_service, "_fetch_server_catalog_items", _fake_catalog_items)

    client = TestClient(create_app(testing=True))
    res = client.get("/browse/home")
    assert res.status_code == 200
    body = res.json()
    assert body["connected"] is True
    rows_by_id = {r["id"]: r for r in body["rows"]}
    assert list(rows_by_id.keys()) == [
        "continue_watching_movies",
        "continue_watching_tv",
        "recently_added_movies",
        "recently_added_tv",
    ]
    assert rows_by_id["continue_watching_movies"]["title"] == "Continue Watching Movies"
    assert rows_by_id["continue_watching_tv"]["title"] == "Continue Watching TV"
    assert rows_by_id["recently_added_movies"]["title"] == "Recently Added Movies"
    assert rows_by_id["recently_added_tv"]["title"] == "Recently Added TV"

    # Blade Runner 2049 is on both Jellyfin (22ms) and Plex (6ms); Plex wins with Jellyfin's 2400s resume_pos
    cw_movies = rows_by_id["continue_watching_movies"]["items"]
    assert len(cw_movies) == 1
    assert cw_movies[0]["server_id"] == "plex_primary"
    assert cw_movies[0]["resume_pos"] == 2400.0
    assert cw_movies[0]["duplicate_count"] == 2

    # Recently Added Movies has deduplicated Blade Runner 2049 + Arrival = 2 movies
    ra_movies = rows_by_id["recently_added_movies"]["items"]
    assert len(ra_movies) == 2

    # Recently Added TV has Severance (Jellyfin) + Andor (Plex) = 2 shows
    ra_tv = rows_by_id["recently_added_tv"]["items"]
    assert len(ra_tv) == 2

    # Combined Movies and Series tabs also deduplicate across services
    mov_res = client.get("/browse/movies").json()
    assert mov_res["count"] == 2
    tv_res = client.get("/browse/tv/series").json()
    assert tv_res["count"] == 2


def test_custom_plex_ids_reach_detail_and_series_routes(monkeypatch) -> None:
    server = {"id": "srv_plex", "type": "plex", "name": "Custom Plex"}
    monkeypatch.setattr(browse_service, "_find_server_by_id", lambda sid: server)
    monkeypatch.setattr(
        browse_service, "status_summary", lambda: {"connected": True, "active_server_count": 1}
    )

    class PlexClient:
        def get(self, path, *, query=None):
            if path == "/library/metadata/123":
                return {"MediaContainer": {"Metadata": [
                    {"key": path, "type": "show", "title": "Example"}
                ]}}
            if path == "/library/metadata/123/children":
                return {"MediaContainer": {"Metadata": [
                    {"key": "/library/metadata/124", "type": "season", "index": 1}
                ]}}
            if path == "/library/metadata/124/children":
                return {"MediaContainer": {"Metadata": [
                    {"key": "/library/metadata/125", "type": "episode", "title": "Pilot",
                     "parentIndex": 1, "index": 1}
                ]}}
            pytest.fail(f"Unexpected Plex path: {path}")

    monkeypatch.setattr(browse_service, "_custom_plex_client", lambda _: PlexClient())
    item_id = browse_service.encode_item_id(server["id"], "/library/metadata/123")
    assert browse_service.decode_item_id(item_id) == (server["id"], "/library/metadata/123")
    assert browse_service.decode_item_id("ms~srv_plex~/library/metadata/123") == (
        server["id"], "/library/metadata/123"
    )
    # Follow the same encoded path used by jellyfin.js; ASGI decodes it before routing.
    from urllib.parse import quote

    client = TestClient(create_app(testing=True))
    detail = client.get(f"/browse/item/{quote(item_id, safe='')}")
    assert detail.status_code == 200
    assert detail.json()["item"]["title"] == "Example"
    seasons = client.get(f"/browse/tv/series/{quote(item_id, safe='')}/seasons")
    assert seasons.status_code == 200
    assert seasons.json()["seasons"][0]["season_number"] == 1
    episodes = client.get(f"/browse/tv/series/{quote(item_id, safe='')}/episodes?season_number=1")
    assert episodes.status_code == 200
    assert episodes.json()["episodes"][0]["episode_number"] == 1


@pytest.mark.parametrize("source", ["jf_primary", "plex_primary", "srv_plex"])
def test_catalog_scroll_reaches_all_upstream_pages(monkeypatch, source) -> None:
    server = {"id": source, "type": "jellyfin" if source == "jf_primary" else "plex"}
    monkeypatch.setattr(browse_service, "_active_servers", lambda: [server])
    monkeypatch.setattr(
        browse_service, "status_summary", lambda: {"connected": True, "active_server_count": 1}
    )
    calls = []

    def jf_page(*, sort, limit, start_index, starts_with, refresh):
        calls.append(start_index)
        rows = [{"item_id": str(i), "title": f"Movie {i:03}", "type": "movie"}
                for i in range(start_index, min(300, start_index + limit))]
        return {"items": rows, "count": 300}

    def plex_page(_library_id, *, start, limit, sort):
        calls.append(start)
        rows = [{"id": str(i), "title": f"Movie {i:03}", "type": "movie"}
                for i in range(start, min(300, start + limit))]
        return {"items": rows, "count": 300,
                "next_start": start + len(rows) if start + len(rows) < 300 else None}

    class PlexClient:
        def get(self, path, *, query=None):
            if path == "/library/sections":
                return {"MediaContainer": {"Directory": [{"key": "1", "type": "movie"}]}}
            assert path == "/library/sections/1/all"
            start = query["X-Plex-Container-Start"]
            limit = query["X-Plex-Container-Size"]
            page = plex_page("1", start=start, limit=limit, sort="title")
            return {"MediaContainer": {"totalSize": 300, "Metadata": [
                {"key": f"/library/metadata/{row['id']}", "title": row["title"], "type": "movie"}
                for row in page["items"]
            ]}}

    monkeypatch.setattr(browse_service.jellyfin_receiver, "list_movies", jf_page)
    monkeypatch.setattr(browse_service.plex_service.catalog_service, "libraries",
                        lambda: {"libraries": [{"id": "1", "type": "movie"}]})
    monkeypatch.setattr(browse_service.plex_service.catalog_service, "library_items", plex_page)
    monkeypatch.setattr(browse_service, "_custom_plex_client", lambda _: PlexClient())
    client = TestClient(create_app(testing=True))
    start = 0
    titles = []
    for _ in range(10):
        response = client.get(f"/browse/movies?limit=60&start={start}&sort=title_asc")
        assert response.status_code == 200
        page = response.json()
        assert page["count"] == 300
        titles.extend(item["title"] for item in page["items"])
        start = page["next_start_index"]
        if start is None:
            break
    else:
        pytest.fail("Catalog pagination never terminated")
    assert titles == [f"Movie {i:03}" for i in range(300)]
    if source != "jf_primary":
        assert 100 in calls and 200 in calls


def test_status_includes_custom_only_browse_connection(monkeypatch) -> None:
    monkeypatch.setattr(state, "get_settings", lambda: {})
    monkeypatch.setattr(browse_service.jellyfin_receiver, "status", lambda: {"enabled": False})
    monkeypatch.setattr(browse_service.plex_auth.auth_manager, "status", lambda: {"linked": False})
    browse_service.save_servers_store({"servers": [
        {"id": "srv_custom", "type": "jellyfin", "server_url": "http://custom:8096",
         "enabled": True}
    ]})
    browse_service._record_latency("srv_custom", 12.0, connected=True)
    result = TestClient(create_app(testing=True)).get("/status")
    assert result.status_code == 200
    body = result.json()
    assert body["browse_connected"] is True
    assert body["browse_enabled"] is True
    assert body["browse_server_count"] == 1
    assert body["browse_connected_count"] == 1
    assert body["browse_best_latency_ms"] == 12.0


@pytest.mark.parametrize("resume_pos,expected", [(2400.0, 2400.0), (None, 600.0)])
def test_primary_plex_resume_uses_merged_position(monkeypatch, resume_pos, expected) -> None:
    monkeypatch.setattr(browse_service, "_find_server_by_id",
                        lambda _: {"id": "plex_primary", "type": "plex"})
    monkeypatch.setattr(browse_service.plex_service.catalog_service, "durable_item",
                        lambda *args, **kwargs: {"resume_pos": 600.0})
    starts = []

    def play_now(item, **kwargs):
        starts.append(kwargs["start_pos"])
        return {"title": "Example"}

    monkeypatch.setattr(browse_service.playback_service, "play_now", play_now)
    result = TestClient(create_app(testing=True)).post("/browse/action", json={
        "item_id": browse_service.encode_item_id("plex_primary", "opaque-reference"),
        "command": "resume", "resume_pos": resume_pos,
    })
    assert result.status_code == 200
    assert starts == [expected]
