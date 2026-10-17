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
    browse_service._DEDUPED_ITEMS.clear()
    browse_service._DEDUPE_KEY_TO_ITEM_IDS.clear()
    browse_service._CUSTOM_JF_SESSIONS.clear()
    yield
    browse_service._SERVER_METRICS.clear()
    browse_service._SERIES_GROUPS.clear()
    browse_service._DEDUPED_ITEMS.clear()
    browse_service._DEDUPE_KEY_TO_ITEM_IDS.clear()
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
        "recently_added_movies",
        "recently_added_tv",
        "continue_watching_movies",
        "continue_watching_tv",
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
                    {"key": path + "/children", "type": "show", "title": "Example"}
                ]}}
            if path == "/library/metadata/123/children":
                return {"MediaContainer": {"Metadata": [
                    {"key": "/library/metadata/124/children", "type": "season", "index": 1}
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
    assert detail.json()["item"]["item_id"] == item_id
    seasons = client.get(f"/browse/tv/series/{quote(item_id, safe='')}/seasons")
    assert seasons.status_code == 200
    assert seasons.json()["seasons"][0]["season_number"] == 1
    assert browse_service.decode_item_id(seasons.json()["seasons"][0]["season_id"]) == (
        "srv_plex", "/library/metadata/124"
    )
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


def test_deduped_detail_overlay_and_adjacent_episode_navigation(monkeypatch) -> None:
    browse_service._record_latency("srv_jf_slow", 40.0, connected=True, server_name="Slow JF")
    browse_service._record_latency("plex_primary", 5.0, connected=True, server_name="Fast Plex")

    deduped = browse_service.deduplicate_items(
        [
            {
                "item_id": browse_service.encode_item_id("srv_jf_slow", "jf-dune"),
                "raw_item_id": "jf-dune",
                "server_id": "srv_jf_slow",
                "server_name": "Slow JF",
                "server_type": "jellyfin",
                "type": "movie",
                "title": "Dune: Part Two",
                "year": "2024",
                "resume_pos": 1950.0,
                "progress_percent": 35.0,
                "overview": "Merged overview from slower server.",
            },
            {
                "item_id": browse_service.encode_item_id("plex_primary", "plex-dune"),
                "raw_item_id": "plex-dune",
                "server_id": "plex_primary",
                "server_name": "Fast Plex",
                "server_type": "plex",
                "type": "movie",
                "title": "Dune: Part Two",
                "year": "2024",
                "resume_pos": 300.0,
                "progress_percent": 5.0,
                "overview": "",
            },
        ]
    )
    assert len(deduped) == 1
    win_id = deduped[0]["item_id"]

    monkeypatch.setattr(
        browse_service,
        "_find_server_by_id",
        lambda sid: {"id": sid, "type": "plex" if "plex" in sid else "jellyfin", "name": "Fast Plex"},
    )
    monkeypatch.setattr(
        browse_service.plex_service.catalog_service,
        "item_detail",
        lambda _raw: {
            "item": {
                "id": "plex-dune",
                "type": "movie",
                "title": "Dune: Part Two",
                "year": 2024,
                "duration_ms": 6000000,
                "view_offset_ms": 300000,
                "progress": 5.0,
                "summary": "",
            }
        },
    )

    from urllib.parse import quote

    client = TestClient(create_app(testing=True))
    detail_res = client.get(f"/browse/item/{quote(win_id, safe='')}")
    assert detail_res.status_code == 200
    detail_item = detail_res.json()["item"]
    assert detail_item["resume_pos"] == 1950.0
    assert detail_item["duplicate_count"] == 2
    assert detail_item["overview"] == "Merged overview from slower server."

    # Adjacent episode lookup for non-jf_primary episodes falls back to series episode list
    ep1_id = browse_service.encode_item_id("plex_primary", "ep-1")
    ep2_id = browse_service.encode_item_id("plex_primary", "ep-2")
    ep3_id = browse_service.encode_item_id("plex_primary", "ep-3")
    series_id = browse_service.encode_item_id("plex_primary", "show-1")

    monkeypatch.setattr(
        browse_service,
        "get_unified_item_detail",
        lambda iid, refresh=False: {
            "item_id": ep2_id,
            "type": "episode",
            "series_id": series_id,
            "season_number": 1,
            "episode_number": 2,
        },
    )
    monkeypatch.setattr(
        browse_service,
        "list_unified_series_episodes",
        lambda sid, **kw: {
            "episodes": [
                {"item_id": ep1_id, "season_number": 1, "episode_number": 1, "title": "Ep 1"},
                {"item_id": ep2_id, "season_number": 1, "episode_number": 2, "title": "Ep 2"},
                {"item_id": ep3_id, "season_number": 1, "episode_number": 3, "title": "Ep 3"},
            ]
        },
    )
    adj_res = client.get(f"/browse/item/{quote(ep2_id, safe='')}/adjacent")
    assert adj_res.status_code == 200
    adj = adj_res.json()
    assert adj["prev"]["item_id"] == ep1_id
    assert adj["next"]["item_id"] == ep3_id


@pytest.fixture
def normalized_plex(monkeypatch):
    """Keep Plex encryption, detail normalization, and child pagination real."""
    import os
    from types import SimpleNamespace
    from relaytv_app.integrations import plex_service

    records = {
        "/library/metadata/movie": {
            "key": "/library/metadata/movie", "type": "movie", "title": "Example",
            "year": 2024, "duration": 6000000, "viewOffset": 300000,
        },
        "/library/metadata/show": {
            "key": "/library/metadata/show/children", "type": "show", "title": "Example Show",
        },
        "/library/metadata/season": {
            "key": "/library/metadata/season/children", "type": "season", "index": 1,
        },
    }
    children = {"/library/metadata/show": [records["/library/metadata/season"]]}
    calls = []

    class PMSClient:
        def get(self, path, *, query=None):
            calls.append((path, dict(query or {})))
            if path.endswith('/children'):
                rows = children[path.removesuffix('/children')]
                start = query['X-Plex-Container-Start']
                limit = query['X-Plex-Container-Size']
                return {"MediaContainer": {
                    "Metadata": rows[start:start + limit], "totalSize": len(rows), "offset": start,
                }}
            assert path in records, path
            return {"MediaContainer": {"Metadata": [records[path]]}}

    session = SimpleNamespace(account_id="account", machine_id="machine",
                              reference_key=os.urandom(32), client=PMSClient())
    manager = SimpleNamespace(selected_server_session=lambda: session,
                              assert_server_session_current=lambda _: None)
    catalog = plex_service.PlexCatalogService(manager)
    monkeypatch.setattr(plex_service, "catalog_service", catalog)
    monkeypatch.setattr(browse_service, "_find_server_by_id",
                        lambda sid: {"id": sid, "type": "plex", "name": "Plex"})
    monkeypatch.setattr(browse_service, "status_summary", lambda: {"connected": True})

    def reference(path):
        raw = catalog._item(session, records[path])
        return browse_service.encode_item_id("plex_primary", raw["id"])

    return SimpleNamespace(records=records, children=children, calls=calls,
                           reference=reference, session=session, catalog=catalog)


def test_plex_detail_keeps_overlay_with_real_randomized_references(normalized_plex) -> None:
    from urllib.parse import quote

    pms = normalized_plex
    iid = pms.reference("/library/metadata/movie")
    assert pms.reference("/library/metadata/movie") != iid
    winner = {
        "item_id": iid, "source_id": "plex_primary", "type": "movie", "title": "Example",
        "year": 2024, "source_latency_ms": 1, "resume_pos": 300,
    }
    slower = {**winner, "item_id": browse_service.encode_item_id("srv_slow", "movie"),
              "source_id": "srv_slow", "source_latency_ms": 40, "resume_pos": 1950}
    browse_service.deduplicate_items([winner, slower])
    client = TestClient(create_app(testing=True))
    for _ in range(2):
        response = client.get(f"/browse/item/{quote(iid, safe='')}?refresh=true")
        assert response.status_code == 200
        item = response.json()["item"]
        assert item["item_id"] == iid
        assert item["resume_pos"] == 1950
        assert item["duplicate_count"] == 2


@pytest.mark.parametrize("season_number", [0, 1])
def test_plex_adjacent_episodes_through_real_detail_and_children(normalized_plex, season_number):
    pms = normalized_plex
    season = pms.records["/library/metadata/season"]
    season["index"] = season_number
    episodes = []
    for index in range(1, 4):
        path = f"/library/metadata/ep{index}"
        raw = {"key": path, "type": "episode", "title": f"Episode {index}",
               "grandparentTitle": "Example Show", "grandparentKey": "/library/metadata/show",
               "parentIndex": season_number, "index": index}
        pms.records[path] = raw
        episodes.append(raw)
    pms.children["/library/metadata/season"] = episodes
    iid = pms.reference("/library/metadata/ep2")
    client = TestClient(create_app(testing=True))
    response = client.get(f"/browse/item/{iid}/adjacent")
    assert response.status_code == 200
    nav = response.json()
    assert nav["prev"]["episode_number"] == 1
    assert nav["next"]["episode_number"] == 3
    assert nav["next"]["season_number"] == season_number
    # Confirm that the actual Plex child path was reached with a decoded series reference.
    assert any(path == "/library/metadata/show/children" for path, _ in pms.calls)


def test_plex_full_season_and_play_all_follow_real_child_cursors(normalized_plex, monkeypatch):
    pms = normalized_plex
    pms.children["/library/metadata/season"] = [
        {"key": f"/library/metadata/ep{i}", "type": "episode", "title": f"Episode {i}",
         "grandparentTitle": "Example Show", "parentIndex": 1, "index": i}
        for i in range(1, 151)
    ]
    iid = pms.reference("/library/metadata/show")
    client = TestClient(create_app(testing=True))
    response = client.get(f"/browse/tv/series/{iid}/episodes?season_number=1")
    assert response.status_code == 200
    episodes = response.json()["episodes"]
    assert [ep["episode_number"] for ep in episodes] == list(range(1, 151))
    assert any(path == "/library/metadata/season/children" and query['X-Plex-Container-Start'] == 100
               for path, query in pms.calls)
    actions = []
    monkeypatch.setattr(browse_service, "perform_unified_action",
                        lambda iid, cmd: actions.append((iid, cmd)) or {"now_playing": {}})
    response = client.post(f"/browse/tv/series/{iid}/play_all")
    assert response.status_code == 200
    assert response.json()["queued_count"] == 149
    assert len(actions) == 150
    assert actions[0][1] == "play_now"
    assert all(command == "play_last" for _, command in actions[1:])


def test_singleton_refresh_invalidates_prior_duplicate_overlay(monkeypatch, normalized_plex):
    iid = browse_service.encode_item_id("jf_primary", "movie")
    winner = {"item_id": iid, "source_id": "jf_primary", "type": "movie", "title": "Example",
              "source_latency_ms": 1, "resume_pos": 0, "progress_percent": 0}
    other = {**winner, "item_id": browse_service.encode_item_id("srv_old", "other"),
             "source_id": "srv_old", "source_latency_ms": 40, "resume_pos": 1950}
    browse_service.deduplicate_items([winner, other])
    assert iid in browse_service._DEDUPED_ITEMS
    browse_service.deduplicate_items([winner])
    monkeypatch.setattr(browse_service, "_find_server_by_id",
                        lambda _: {"id": "jf_primary", "type": "jellyfin"})
    monkeypatch.setattr(browse_service.jellyfin_receiver, "get_item_detail",
                        lambda *args, **kwargs: {**winner, "item_id": "movie"})
    response = TestClient(create_app(testing=True)).get(f"/browse/item/{iid}?refresh=true")
    assert response.status_code == 200
    item = response.json()["item"]
    assert item["resume_pos"] == 0
    assert item.get("duplicate_count", 1) == 1
    assert not item.get("alternate_sources")

    # Also verify that when plex_primary issues a newly randomized reference on singleton refresh,
    # the prior encrypted reference is evicted from _DEDUPED_ITEMS via _DEDUPE_KEY_TO_ITEM_IDS.
    pms = normalized_plex
    plex_iid_a = pms.reference("/library/metadata/movie")
    plex_iid_b = pms.reference("/library/metadata/movie")
    assert plex_iid_a != plex_iid_b
    plex_winner_a = {
        "item_id": plex_iid_a, "source_id": "plex_primary", "type": "movie", "title": "Example",
        "year": 2024, "source_latency_ms": 1, "resume_pos": 300,
    }
    plex_winner_b = {**plex_winner_a, "item_id": plex_iid_b}
    browse_service.deduplicate_items([plex_winner_a, other])
    assert plex_iid_a in browse_service._DEDUPED_ITEMS
    browse_service.deduplicate_items([plex_winner_b])
    assert plex_iid_a not in browse_service._DEDUPED_ITEMS
    assert plex_iid_b not in browse_service._DEDUPED_ITEMS


def test_custom_plex_child_pagination_and_season_id_filter(monkeypatch):
    from urllib.parse import quote

    server = {"id": "srv_plex", "type": "plex", "name": "Custom Plex"}
    monkeypatch.setattr(browse_service, "_find_server_by_id", lambda _: server)
    monkeypatch.setattr(
        browse_service, "status_summary", lambda: {"connected": True, "active_server_count": 1}
    )
    all_eps = [
        {"key": f"/library/metadata/ep{i}", "type": "episode", "title": f"Ep {i}",
         "grandparentTitle": "Big Show", "parentIndex": 1, "index": i}
        for i in range(1, 136)
    ]
    calls = []

    class PagedCustomPlexClient:
        def get(self, path, *, query=None):
            calls.append((path, dict(query or {})))
            if path == "/library/metadata/show99/children":
                return {"MediaContainer": {"Metadata": [
                    {"key": "/library/metadata/season99/children", "type": "season", "index": 1}
                ], "totalSize": 1}}
            if path == "/library/metadata/season99/children":
                start = int((query or {}).get("X-Plex-Container-Start", 0))
                size = int((query or {}).get("X-Plex-Container-Size", 100))
                return {"MediaContainer": {
                    "Metadata": all_eps[start:start + size],
                    "totalSize": len(all_eps),
                }}
            pytest.fail(f"Unexpected custom Plex path: {path}")

    monkeypatch.setattr(browse_service, "_custom_plex_client", lambda _: PagedCustomPlexClient())
    series_id = browse_service.encode_item_id("srv_plex", "/library/metadata/show99/children")
    season_id = browse_service.encode_item_id("srv_plex", "/library/metadata/season99")
    client = TestClient(create_app(testing=True))
    res = client.get(
        f"/browse/tv/series/{quote(series_id, safe='')}/episodes?season_id={quote(season_id, safe='')}"
    )
    assert res.status_code == 200
    eps = res.json()["episodes"]
    assert [ep["episode_number"] for ep in eps] == list(range(1, 136))
    assert any(
        path == "/library/metadata/season99/children" and q.get("X-Plex-Container-Start") == 100
        for path, q in calls
    )


@pytest.mark.parametrize("server_id", [None, "srv_good", "srv_bad"])
def test_probe_endpoint_matches_ui_result_contract(monkeypatch, server_id):
    servers = [
        {"id": "srv_good", "type": "plex", "enabled": True},
        {"id": "srv_bad", "type": "jellyfin", "enabled": True},
        {"id": "srv_disabled", "type": "plex", "enabled": False},
    ]
    monkeypatch.setattr(browse_service, "_collect_configured_servers", lambda **_: servers)

    def probe(server):
        connected = server['id'] == "srv_good"
        browse_service._record_latency(server['id'], 5 if connected else None,
                                       connected=connected, error='' if connected else 'offline')
        return browse_service._public_server_entry(server)

    monkeypatch.setattr(browse_service, "probe_single_server", probe)
    client = TestClient(create_app(testing=True))
    response = client.post('/browse/servers/probe', json={"server_id": server_id})
    assert response.status_code == 200
    body = response.json()
    assert len(body['servers']) == 3  # Preserve the existing response for other clients.
    expected_ids = {server_id} if server_id else {"srv_good", "srv_bad"}
    assert {server['id'] for server in body['results']} == expected_ids
    if server_id:
        assert body['result']['id'] == server_id
        assert body['result']['connected'] is (server_id == 'srv_good')
    else:
        assert sum(1 for server in body['results'] if server['connected']) == 1


@pytest.mark.parametrize('source', ['plex_primary', 'srv_custom'])
@pytest.mark.parametrize('sort', ['title_desc', 'year_asc'])
def test_plex_catalog_sort_direction_before_paging(monkeypatch, source, sort):
    from types import SimpleNamespace
    from relaytv_app.integrations import plex_service

    rows = [{'key': f'/library/metadata/{i}', 'type': 'movie',
             'title': f'Movie {i:03}', 'year': 1950 + i} for i in range(150)]
    calls = []

    class PMS:
        def get(self, path, *, query=None):
            if path == '/library/sections':
                return {'MediaContainer': {'Directory': [{'key': '1', 'type': 'movie'}]}}
            assert path == '/library/sections/1/all'
            calls.append(query)
            key = 'year' if query['sort'].startswith('year') else 'title'
            ordered = sorted(rows, key=lambda row: row[key], reverse=query['sort'].endswith('desc'))
            offset, size = query['X-Plex-Container-Start'], query['X-Plex-Container-Size']
            return {'MediaContainer': {'Metadata': ordered[offset:offset + size],
                                       'totalSize': len(rows), 'offset': offset}}

    server = {'id': source, 'type': 'plex'}
    monkeypatch.setattr(browse_service, '_active_servers', lambda: [server])
    monkeypatch.setattr(browse_service, 'status_summary', lambda: {})
    monkeypatch.setattr(browse_service, '_custom_plex_client', lambda _: PMS())
    # Exercise the actual primary Plex library adapter and its sort-to-query mapping.
    session = SimpleNamespace(account_id='account', machine_id='machine',
                              reference_key=b'x' * 32, client=PMS())
    manager = SimpleNamespace(selected_server_session=lambda: session,
                              assert_server_session_current=lambda _: None)
    catalog = plex_service.PlexCatalogService(manager)
    monkeypatch.setattr(plex_service, 'catalog_service', catalog)
    ref = catalog._reference(session, 'section', '/library/sections/1/all', 'movie')
    monkeypatch.setattr(catalog, 'libraries', lambda: {'libraries': [{'id': ref, 'type': 'movie'}]})
    client = TestClient(create_app(testing=True))
    titles = []
    start = 0
    while start is not None:
        response = client.get('/browse/movies', params={'sort': sort, 'start': start, 'limit': 60})
        assert response.status_code == 200
        page = response.json()
        titles.extend(item['title'] for item in page['items'])
        start = page['next_start_index']
        assert len(titles) <= len(rows)
    expected = sorted(rows, key=lambda row: row['title' if sort == 'title_desc' else 'year'],
                      reverse=sort == 'title_desc')
    assert titles == [row['title'] for row in expected]
    assert {call['sort'] for call in calls} == (
        {'titleSort:desc'} if sort == 'title_desc' else {'year:asc'}
    )


def test_primary_plex_season_id_matches_decoded_identity(normalized_plex):
    pms = normalized_plex
    pms.children['/library/metadata/season'] = [
        {'key': f'/library/metadata/ep{i}', 'type': 'episode', 'title': f'Episode {i}',
         'grandparentTitle': 'Example Show', 'parentIndex': 1, 'index': i}
        for i in range(1, 4)
    ]
    client = TestClient(create_app(testing=True))
    series_id = pms.reference('/library/metadata/show')
    seasons = client.get(f'/browse/tv/series/{series_id}/seasons').json()['seasons']
    response = client.get(f'/browse/tv/series/{series_id}/episodes',
                          params={'season_id': seasons[0]['season_id']})
    assert response.status_code == 200
    assert [ep['episode_number'] for ep in response.json()['episodes']] == [1, 2, 3]


def test_all_overlay_aliases_invalidated_and_cache_bounded(normalized_plex, monkeypatch):
    pms = normalized_plex
    monkeypatch.setattr(browse_service, 'MAX_DEDUPED_ITEMS', 4)
    slow = {'item_id': browse_service.encode_item_id('srv_slow', 'movie'),
            'source_id': 'srv_slow', 'type': 'movie', 'title': 'Example', 'year': 2024,
            'resume_pos': 1950, 'source_latency_ms': 40}
    refs = [pms.reference('/library/metadata/movie') for _ in range(12)]
    for iid in refs:
        fast = {**slow, 'item_id': iid, 'source_id': 'plex_primary',
                'resume_pos': 300, 'source_latency_ms': 1}
        browse_service.deduplicate_items([fast, slow])
    assert len(browse_service._DEDUPED_ITEMS) == 4
    assert sum(map(len, browse_service._DEDUPE_KEY_TO_ITEM_IDS.values())) == 4
    assert refs[-2] in browse_service._DEDUPED_ITEMS
    fresh = {**fast, 'item_id': pms.reference('/library/metadata/movie')}
    browse_service.deduplicate_items([fresh])
    assert not browse_service._DEDUPED_ITEMS
    assert not browse_service._DEDUPE_KEY_TO_ITEM_IDS
    response = TestClient(create_app(testing=True)).get('/browse/item/' + refs[-2])
    assert response.status_code == 200
    assert response.json()['item']['resume_pos'] == 300
    assert response.json()['item'].get('duplicate_count', 1) == 1
    for index, iid in enumerate(refs):
        fast = {**fast, 'item_id': iid, 'title': f'Example {index}'}
        browse_service.deduplicate_items([fast, {**slow, 'title': fast['title']}])
    assert len(browse_service._DEDUPED_ITEMS) == 4
    assert len(browse_service._DEDUPE_KEY_TO_ITEM_IDS) == 4


def test_custom_plex_action_survives_player_lifecycle_and_reports_to_source(monkeypatch, tmp_path):
    import threading
    from relaytv_app import player
    from relaytv_app.integrations import plex_service

    server = {'id': 'srv_custom', 'type': 'plex', 'enabled': True}
    raw = {'key': '/library/metadata/movie', 'type': 'movie', 'title': 'Example',
           'duration': 6000000, 'Media': [{'container': 'mp4', 'Part': [
               {'key': '/library/parts/movie/file.mp4', 'accessible': True, 'exists': True}
           ]}]}
    reports = []
    reported = threading.Event()

    class PMS:
        def get(self, path, *, query=None):
            assert path == '/library/metadata/movie'
            return {'MediaContainer': {'Metadata': [raw]}}

        def request_no_content(self, method, path, *, query):
            assert (method, path) == ('POST', '/:/timeline')
            reports.append(dict(query))
            reported.set()

    monkeypatch.setattr(browse_service, '_find_server_by_id', lambda _: server)
    monkeypatch.setattr(browse_service, '_custom_plex_client', lambda _: PMS())
    monkeypatch.setattr(plex_service.catalog_service.auth_manager, 'selected_server_session',
                        lambda: pytest.fail('Custom playback accessed the primary server session'))
    monkeypatch.setattr(player, 'update_history_progress', lambda *a, **k: None)
    monkeypatch.setattr(player, '_mark_playback_transition', lambda *a, **k: None)
    monkeypatch.setattr(player, 'cec_auto_on_switch', lambda _: False)
    monkeypatch.setattr(player, '_load_stream_in_existing_mpv', lambda *a, **k: False)
    streams = []
    monkeypatch.setattr(player, 'start_mpv', lambda stream, **kw: streams.append(stream))
    monkeypatch.setattr(player, 'mpv_set', lambda *a, **k: None)
    monkeypatch.setattr(player, '_add_history_entry', lambda _: None)
    monkeypatch.setattr(player, '_prime_mpv_up_next_from_queue', lambda **kw: False)
    monkeypatch.setattr(state, 'NOW_PLAYING', None)
    monkeypatch.setattr(state, 'QUEUE', [])
    monkeypatch.setattr(state, 'get_tv_state', lambda: {})
    monkeypatch.setattr(state, 'update_session', lambda **kw: True)
    monkeypatch.setattr(state, '_state_path', lambda name: str(tmp_path / name))
    with plex_service._TIMELINE_LOCK:
        plex_service._TIMELINE_LAST.clear()
    iid = browse_service.encode_item_id('srv_custom', '/library/metadata/movie')
    response = TestClient(create_app(testing=True)).post('/browse/action', json={
        'item_id': iid, 'command': 'resume', 'resume_pos': 100,
    })
    assert response.status_code == 200, response.text
    now = response.json()['now_playing']
    assert now['plex_item_id'] == iid
    assert streams and '/browse/stream/srv_custom' in streams[0]
    assert reported.wait(2), 'Real player start did not emit a custom Plex timeline report'
    assert reports[-1]['ratingKey'] == 'movie'
    assert reports[-1]['state'] == 'playing'
    assert reports[-1]['time'] == 100000
    # Round-trip through the actual persisted queue/session formatter and resolver.
    persisted = state._persistable_queue_item(now)
    assert persisted['plex_item_id'] == iid
    replayed = plex_service.catalog_service.resolve_playback_item(persisted)
    assert '/browse/stream/srv_custom' in replayed['url']
    reported.clear()
    player._emit_plex_timeline_from_now(now, 'stopped', position_sec=125, duration_sec=6000)
    assert reported.wait(2), 'Real stop adapter did not report to custom Plex'
    assert reports[-1]['time'] == 125000
    assert reports[-1]['duration'] == 6000000
    assert reports[-1]['state'] == 'stopped'


def test_disabled_jellyfin_integration_never_reports_connected_in_browse_servers(
    monkeypatch,
) -> None:
    browse_service._record_latency(
        "jf_primary", 3.5, connected=True, server_name="Jellyfin Server"
    )
    monkeypatch.setattr(
        state,
        "get_settings",
        lambda: {
            "jellyfin_enabled": False,
            "jellyfin_server_url": "http://10.55.55.2:8096",
        },
    )
    monkeypatch.setattr(
        browse_service.jellyfin_receiver,
        "status",
        lambda: {
            "enabled": True,
            "running": False,
            "connected": False,
            "authenticated": False,
            "catalog_ready": False,
            "server_url": "http://10.55.55.2:8096",
            "server_product_name": "Jellyfin Server",
        },
    )
    monkeypatch.setattr(
        browse_service.plex_auth.auth_manager,
        "status",
        lambda: {"linked": False},
    )

    listed = browse_service.list_servers_public()
    assert listed["enabled_count"] == 0
    assert listed["connected_count"] == 0
    jf = next(s for s in listed["servers"] if s["id"] == "jf_primary")
    assert jf["enabled"] is False
    assert jf["connected"] is False
