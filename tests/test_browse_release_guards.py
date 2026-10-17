# SPDX-License-Identifier: GPL-3.0-only
"""Public credential boundaries and Clear versus real in-flight Browse I/O."""

import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from relaytv_app import playback_service, player, state
from relaytv_app.config import runtime_config
from relaytv_app.integrations import browse_service as browse
from relaytv_app.main import create_app


@pytest.fixture(params=["jellyfin", "emby"])
def media_server(request, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    mode = {"image": "normal", "block_episode": False}
    calls = []
    secret = "synthetic-media-server-credential"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            calls.append((self.path, self.headers.get("X-Emby-Token")))
            if "/Images/" in path:
                if mode["image"] == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "/redirect-target")
                    self.end_headers()
                    return
                body = b"test-png-image"
                content_type = "text/html" if mode["image"] == "html" else "image/png"
            else:
                blocked_id = "ep1" if mode["block_episode"] == "first" else "ep2"
                if mode["block_episode"] and path.endswith("/Items/" + blocked_id):
                    entered.set()
                    assert release.wait(5)
                item = {"Id": path.rsplit("/", 1)[-1], "Name": "Example", "Type": "Movie",
                        "ImageTags": {"Primary": "poster-tag"}, "BackdropImageTags": ["backdrop-tag"]}
                if "/Shows/" in path:
                    data = {"Items": [{"Id": "season1", "IndexNumber": 1, "Type": "Season"}]}
                elif path.endswith(("/Items/movie", "/Items/ep1", "/Items/ep2")):
                    data = item
                else:
                    data = {"Items": [{**item, "Id": "movie"}], "TotalRecordCount": 1}
                body = json.dumps(data).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=upstream.serve_forever, daemon=True)
    worker.start()
    server = {"id": "srv_review", "type": request.param, "origin": "custom", "enabled": True,
              "server_url": f"http://127.0.0.1:{upstream.server_port}", "api_key": secret, "user_id": "u"}
    monkeypatch.setattr(browse, "_collect_configured_servers", lambda **kw: [server])
    monkeypatch.setattr(browse, "_CUSTOM_JF_SESSIONS", {})
    monkeypatch.setattr(browse, "_SERVER_METRICS", {})
    monkeypatch.setattr(browse, "_SERIES_GROUPS", {})
    monkeypatch.setattr(browse, "_DEDUPED_ITEMS", {})
    monkeypatch.setattr(browse, "_DEDUPE_KEY_TO_ITEM_IDS", {})
    monkeypatch.setattr(browse.jellyfin_receiver, "_attach_thumb", lambda item: item)
    monkeypatch.setattr(browse, "attach_local_thumbnail", lambda item: item)
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    yield server, secret, calls, mode, entered, release
    release.set()
    upstream.shutdown()
    upstream.server_close()
    worker.join(5)


def _artwork_urls(value):
    if isinstance(value, list):
        return [url for entry in value for url in _artwork_urls(entry)]
    if isinstance(value, dict):
        return [url for entry in value.values() for url in _artwork_urls(entry)]
    return [value] if isinstance(value, str) and value.startswith("/browse/artwork/") else []


@pytest.mark.parametrize("surface", ["item", "movies", "home", "search", "seasons"])
def test_public_browse_artwork_uses_server_auth_without_exposing_it(media_server, monkeypatch, surface):
    server, secret, calls, *_ = media_server
    monkeypatch.setenv("RELAYTV_API_TOKEN", "synthetic-relay-token")
    runtime_config.refresh_from_env()
    paths = {
        "item": "/browse/item/" + browse.encode_item_id(server["id"], "movie"),
        "movies": "/browse/movies", "home": "/browse/home", "search": "/browse/search?q=Example",
        "seasons": "/browse/tv/series/" + browse.encode_item_id(server["id"], "show") + "/seasons",
    }
    client = TestClient(create_app(testing=True))
    response = client.get(paths[surface])  # Public reads intentionally have no bearer token.
    assert response.status_code == 200, response.text
    assert secret not in response.text and "api_key=" not in response.text
    artwork = _artwork_urls(response.json())
    assert artwork, "The response must contain real, usable artwork references"
    for url in set(artwork):
        result = client.get(url)
        assert result.status_code == 200, result.text
        assert result.content == b"test-png-image"
        assert result.headers["content-type"] == "image/png"
        assert secret not in str(result.headers)
    image_calls = [(path, token) for path, token in calls if "/Images/" in path]
    assert image_calls and all(token == secret and secret not in path for path, token in image_calls)


def test_browse_play_response_redacts_private_playback_data(media_server, monkeypatch):
    server, secret, *_ = media_server
    captured = []

    def play(item, **kwargs):
        captured.append(item)
        return {**item, "stream": item["url"], "_resolved_stream": item["url"]}

    monkeypatch.setattr(playback_service, "play_now", play)
    response = TestClient(create_app(testing=True)).post("/browse/action", json={
        "item_id": browse.encode_item_id(server["id"], "movie"),
    })
    assert response.status_code == 200
    assert captured and secret in captured[0]["url"], "Playback must retain its internal credential"
    assert secret not in response.text and "stream" not in response.json()["now_playing"]
    assert _artwork_urls(response.json())


@pytest.mark.parametrize("mode", ["redirect", "html", "oversize"])
def test_artwork_proxy_rejects_redirects_non_images_and_oversized_responses(media_server, monkeypatch, mode):
    _, secret, calls, settings, *_ = media_server
    settings["image"] = mode
    if mode == "oversize":
        monkeypatch.setattr(browse, "MAX_BINARY_RESPONSE_BYTES", 4)
    response = TestClient(create_app(testing=True)).get(
        "/browse/artwork/srv_review", params={"path": "/Items/movie/Images/Primary"},
    )
    assert response.status_code == 502
    assert secret not in response.text
    assert not any("redirect-target" in path for path, _ in calls)


@pytest.mark.parametrize("path", [
    "https://[", "//other.invalid/image", "/Users", "/Items/../Images/Primary",
    "/Items/movie/Images/Primary?api_key=caller-key",
])
def test_artwork_proxy_restricts_upstream_paths(media_server, path):
    _, _, calls, *_ = media_server
    response = TestClient(create_app(testing=True), raise_server_exceptions=False).get(
        "/browse/artwork/srv_review", params={"path": path},
    )
    assert response.status_code == 400
    assert not calls


def test_clear_cancels_play_all_during_real_episode_lookup(media_server, monkeypatch):
    server, _, _, mode, entered, release = media_server
    mode["block_episode"] = True
    ids = [browse.encode_item_id(server["id"], name) for name in ("ep1", "ep2", "ep3")]
    monkeypatch.setattr(browse, "list_unified_series_episodes", lambda *a, **kw: {
        "episodes": [{"item_id": item_id} for item_id in ids],
    })
    monkeypatch.setattr(playback_service, "play_now", lambda item, **kw: item)
    monkeypatch.setattr(state, "QUEUE", state._RevisionedQueue())
    monkeypatch.setattr(state, "persist_queue", lambda: True)
    monkeypatch.setattr(player, "prefetch_queue_item_stream", lambda *a, **kw: None)
    monkeypatch.setattr(player, "prime_mpv_up_next_from_queue", lambda **kw: None)
    queue_routes = importlib.import_module("relaytv_app.routes.queue")
    monkeypatch.setattr(queue_routes, "_ui_event_push_queue", lambda *a, **kw: None)
    client = TestClient(create_app(testing=True))
    results = []
    worker = threading.Thread(target=lambda: results.append(client.post(
        "/browse/tv/series/" + browse.encode_item_id(server["id"], "show") + "/play_all",
    )), daemon=True)
    worker.start()
    try:
        assert entered.wait(5), "Second episode metadata was never in flight"
        assert client.post("/clear").status_code == 200
        newer = {"url": "https://example.test/newer.mp4"}
        playback_service.queue_item(newer)
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert results and results[0].status_code == 200, results
    assert results[0].json()["cancelled"] is True
    assert results[0].json()["queued_count"] == 0
    assert state.QUEUE == [newer], "Retired Play all work overwrote a newer queue decision"
    # A fresh batch remains usable after Clear and after the scoped guard exits.
    with playback_service.queue_import():
        playback_service.queue_item({"url": "https://example.test/fresh.mp4"})
    assert len(state.QUEUE) == 2


def test_clear_during_first_episode_lookup_prevents_late_playback(media_server, monkeypatch):
    server, _, _, mode, entered, release = media_server
    mode["block_episode"] = "first"
    monkeypatch.setattr(browse, "list_unified_series_episodes", lambda *a, **kw: {
        "episodes": [{"item_id": browse.encode_item_id(server["id"], "ep1")}],
    })
    monkeypatch.setattr(state, "QUEUE", state._RevisionedQueue())
    monkeypatch.setattr(state, "persist_queue", lambda: True)
    monkeypatch.setattr(player, "prime_mpv_up_next_from_queue", lambda **kw: None)
    monkeypatch.setattr(player, "play_item", lambda *a, **kw: pytest.fail("Retired batch started playback"))
    qr = importlib.import_module("relaytv_app.routes.queue")
    monkeypatch.setattr(qr, "_ui_event_push_queue", lambda *a, **kw: None)
    client = TestClient(create_app(testing=True), raise_server_exceptions=False)
    results = []
    worker = threading.Thread(target=lambda: results.append(client.post(
        "/browse/tv/series/" + browse.encode_item_id(server["id"], "show") + "/play_all",
    )), daemon=True)
    worker.start()
    try:
        assert entered.wait(5)
        assert client.post("/clear").status_code == 200
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert results and results[0].status_code == 409, results
    assert state.QUEUE == []


@pytest.mark.parametrize("source", ["jellyfin", "plex"])
def test_primary_server_queue_commit_obeys_import_clear(monkeypatch, source):
    from relaytv_app.integrations import jellyfin_service, plex_service

    entered, release = threading.Event(), threading.Event()
    errors = []

    def lookup(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {"url": "https://example.test/episode.mp4", "title": "Episode"}

    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    monkeypatch.setattr(state, "QUEUE", state._RevisionedQueue())
    monkeypatch.setattr(state, "NOW_PLAYING", {"url": "https://example.test/current.mp4"})
    monkeypatch.setattr(state, "get_settings", lambda: {"jellyfin_playback_mode": "direct"})
    monkeypatch.setattr(state, "persist_queue", lambda: True)
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(player, "prime_mpv_up_next_from_queue", lambda **kw: None)
    monkeypatch.setattr(player, "prefetch_queue_item_stream", lambda *a, **kw: None)
    monkeypatch.setattr(player, "recent_jellyfin_stop_matches", lambda **kw: False)
    monkeypatch.setattr(plex_service.catalog_service, "durable_item", lookup)
    monkeypatch.setattr(jellyfin_service, "smart_item_from_url", lookup)
    monkeypatch.setattr(jellyfin_service, "catalog_token", lambda: "")
    monkeypatch.setattr(jellyfin_service, "should_suppress_duplicate_play", lambda *a: False)
    monkeypatch.setattr(jellyfin_service, "is_duplicate_command", lambda *a: False)
    monkeypatch.setattr(browse.jellyfin_receiver, "status", lambda: {"enabled": True})
    monkeypatch.setattr(browse.jellyfin_receiver, "mark_command", lambda *a: None)
    monkeypatch.setattr(browse.jellyfin_receiver, "mark_heartbeat", lambda: None)
    qr = importlib.import_module("relaytv_app.routes.queue")
    monkeypatch.setattr(qr, "_ui_event_push_queue", lambda *a, **kw: None)

    def run():
        try:
            with playback_service.queue_import():
                if source == "plex":
                    plex_service.catalog_service.action("reference", "play_last")
                else:
                    jellyfin_service.handle_command(SimpleNamespace(
                        action="Play", url="https://example.test/episode.mp4", start_pos=None,
                        use_ytdlp=False, payload={"PlayCommand": "PlayLast"},
                    ), controls={}, ui={})
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        assert entered.wait(5), "Primary-server lookup never began"
        assert TestClient(create_app(testing=True)).post("/clear").status_code == 200
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], playback_service.QueueImportCancelled), errors
    assert state.QUEUE == []
