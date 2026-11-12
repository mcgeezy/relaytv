# SPDX-License-Identifier: GPL-3.0-only
"""Regression tests for integrations & routes security, metadata, and lifecycle fixes."""

from __future__ import annotations

import ast
import importlib
import json
import pathlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from fastapi.testclient import TestClient

from relaytv_app import peers, playback_service, player, public_media, state
from relaytv_app.config import runtime_config
from relaytv_app.integrations import (
    browse_service,
    jellyfin_receiver,
    jellyfin_service,
    plex_auth,
    plex_service,
)
from relaytv_app.integrations.plex_client import (
    PlexError,
    PlexStreamResponse,
)
from relaytv_app.main import create_app


# ---------------------------------------------------------------------------
# Group 1: Public Credential & Capability Stream URL Redaction
# ---------------------------------------------------------------------------


def test_public_media_redacts_capability_stream_urls_and_postlive_fallback() -> None:
    assert (
        public_media.sanitize_public_url(
            "http://127.0.0.1:8787/plex/stream/cap-secret-123?part=1"
        )
        == "https://plex.invalid/item"
    )
    assert (
        public_media.sanitize_public_url("/plex/stream/cap-secret-123")
        == "https://plex.invalid/item"
    )
    assert (
        public_media.sanitize_public_url(
            "http://127.0.0.1:8787/browse/stream/srv_1?path=%2Flibrary%2Fparts%2F1%2Ffile.mkv&exp=999&sig=deadbeef"
        )
        == "https://plex.invalid/item"
    )
    assert (
        public_media.sanitize_public_url(
            "/browse/stream/srv_1?path=%2Flibrary%2Fparts%2F1%2Ffile.mkv&exp=999&sig=deadbeef"
        )
        == "https://plex.invalid/item"
    )
    assert (
        public_media.sanitize_public_url("http://127.0.0.1:8787/postlive/secret-tok/index.m3u8")
        == ""
    )
    assert public_media.sanitize_public_url("/postlive/secret-tok/index.m3u8") == ""

    # /postlive/ item falls back to sanitized _resolved_source_url when present.
    postlive_item = {
        "title": "/postlive/secret-tok/index.m3u8",
        "url": "http://127.0.0.1:8787/postlive/secret-tok/index.m3u8",
        "_resolved_source_url": "https://video.example/live/watch?token=topsecret&v=42",
    }
    redacted_postlive = public_media.public_media_item(postlive_item)
    assert redacted_postlive == {"url": "https://video.example/live/watch?v=42"}
    assert "secret-tok" not in json.dumps(redacted_postlive)

    # Custom Plex /browse/stream item stays persistable via https://plex.invalid/item
    # without leaking capability query params or signatures.
    custom_plex_item = {
        "provider": "plex",
        "plex_item_id": "srv_custom::/library/metadata/42",
        "title": "/browse/stream/srv_custom?path=/library/parts/9/a.mkv&sig=leak",
        "url": "http://127.0.0.1:8787/browse/stream/srv_custom?path=%2Flibrary%2Fparts%2F9%2Fa.mkv&exp=1999999999&sig=secret-hmac",
    }
    redacted_plex = public_media.public_media_item(custom_plex_item)
    assert redacted_plex["url"] == "https://plex.invalid/item"
    assert redacted_plex["title"] == "https://plex.invalid/item"
    assert "secret-hmac" not in json.dumps(redacted_plex)
    persisted = state._persistable_queue_item(custom_plex_item)
    assert persisted is not None
    assert persisted["url"] == "https://plex.invalid/item"
    assert "secret-hmac" not in json.dumps(persisted)


def test_jellyfin_routes_strip_api_key_and_rewrite_artwork(monkeypatch) -> None:
    jf_base = "http://127.0.0.1:18096"
    jf_secret = "jf-super-secret-api-key"

    def _raw_item(iid: str = "item-1", item_type: str = "movie") -> dict[str, object]:
        return {
            "item_id": iid,
            "type": item_type,
            "title": f"Title {iid}",
            "url": f"{jf_base}/Videos/{iid}/stream?static=true&api_key={jf_secret}",
            "poster": f"{jf_base}/Items/{iid}/Images/Primary?api_key={jf_secret}&tag=ptag",
            "thumbnail": f"{jf_base}/Items/{iid}/Images/Primary?api_key={jf_secret}&tag=ttag",
            "backdrop": f"{jf_base}/Items/{iid}/Images/Backdrop/0?api_key={jf_secret}&tag=btag",
        }

    ready_status = {
        "enabled": True,
        "running": True,
        "connected": True,
        "catalog_ready": True,
        "server_url": jf_base,
        "last_error": None,
    }
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    monkeypatch.setattr(jellyfin_receiver, "status", lambda: dict(ready_status))
    monkeypatch.setattr(
        browse_service,
        "_collect_configured_servers",
        lambda **kw: [
            {
                "id": "jf_primary",
                "type": "jellyfin",
                "origin": "primary",
                "enabled": True,
                "server_url": jf_base,
                "api_key": jf_secret,
            }
        ],
    )
    monkeypatch.setattr(
        jellyfin_receiver,
        "get_home_rows",
        lambda **kw: {
            "rows": [
                {
                    "id": "continue_watching",
                    "title": "Continue Watching",
                    "items": [_raw_item("cw-1", "episode")],
                },
                {
                    "id": "latest_movies",
                    "title": "Latest Movies",
                    "items": [_raw_item("mov-1", "movie")],
                },
            ],
            "generated_ts": 1700000000,
        },
    )
    monkeypatch.setattr(
        jellyfin_receiver,
        "search_catalog",
        lambda *a, **kw: {"items": [_raw_item("srch-1")], "total_count": 1},
    )
    monkeypatch.setattr(
        jellyfin_receiver,
        "list_movies",
        lambda **kw: {"items": [_raw_item("mov-1")], "total_count": 1},
    )
    monkeypatch.setattr(
        jellyfin_receiver,
        "list_series",
        lambda **kw: {"items": [_raw_item("ser-1", "series")], "total_count": 1},
    )
    monkeypatch.setattr(
        jellyfin_receiver,
        "list_series_seasons",
        lambda sid, **kw: {
            "series": _raw_item(sid, "series"),
            "seasons": [_raw_item("sea-1", "season")],
        },
    )
    monkeypatch.setattr(
        jellyfin_receiver,
        "list_series_episodes",
        lambda sid, **kw: {
            "series": _raw_item(sid, "series"),
            "seasons": [_raw_item("sea-1", "season")],
            "episodes": [_raw_item("ep-1", "episode")],
            "total_count": 1,
        },
    )
    monkeypatch.setattr(
        jellyfin_receiver,
        "get_item_detail",
        lambda iid, **kw: _raw_item(iid),
    )
    monkeypatch.setattr(
        jellyfin_receiver,
        "get_adjacent_episodes",
        lambda iid, **kw: {
            "prev": _raw_item("ep-prev", "episode"),
            "next": _raw_item("ep-next", "episode"),
        },
    )
    monkeypatch.setattr(
        jellyfin_service,
        "handle_command",
        lambda *a, **kw: {
            "ok": True,
            "action": "play",
            "queued_count": 0,
            "now_playing": {
                **_raw_item("ep-1", "episode"),
                "_resolved_stream": f"{jf_base}/Videos/ep-1/master.m3u8?api_key={jf_secret}",
            },
        },
    )

    client = TestClient(create_app(testing=True))
    checks = [
        ("GET", "/jellyfin/home", None),
        ("GET", "/jellyfin/search?q=Title", None),
        ("GET", "/jellyfin/movies", None),
        ("GET", "/jellyfin/tv/series", None),
        ("GET", "/jellyfin/tv/series/ser-1/seasons", None),
        ("GET", "/jellyfin/tv/series/ser-1/episodes", None),
        ("GET", "/jellyfin/item/item-1", None),
        ("GET", "/jellyfin/item/ep-1/adjacent", None),
        ("POST", "/jellyfin/tv/series/ser-1/play_all", None),
        ("POST", "/jellyfin/action", {"item_id": "item-1", "command": "play_now"}),
        (
            "POST",
            "/integrations/jellyfin/command",
            {"action": "Play", "payload": {"ItemIds": ["ep-1"], "PlayCommand": "PlayNow"}},
        ),
    ]

    for method, path, body in checks:
        resp = client.request(method, path, json=body) if body is not None else client.request(method, path)
        assert resp.status_code == 200, (path, resp.text)
        assert jf_secret not in resp.text, f"Leaked api_key on {method} {path}: {resp.text}"
        assert "api_key=" not in resp.text, f"Leaked api_key param on {method} {path}: {resp.text}"
        assert "/browse/artwork/jf_primary?path=" in resp.text, (
            f"Expected rewritten Jellyfin artwork proxy URL on {method} {path}: {resp.text}"
        )


def test_plex_action_sanitizes_item_and_now_playing(monkeypatch) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    service = plex_service.PlexCatalogService(object())
    monkeypatch.setattr(
        service,
        "durable_item",
        lambda item_id, **kw: {
            "provider": "plex",
            "plex_item_id": item_id,
            "title": "Secret Plex Movie",
            "url": "http://127.0.0.1:8787/plex/stream/cap-secret-stream-token",
            "plex_token": "raw-plex-secret",
            "_resolved_stream": "https://plex.example:32400/library/parts/1/file.mkv?X-Plex-Token=raw-plex-secret",
        },
    )
    monkeypatch.setattr(
        playback_service,
        "play_now",
        lambda item, **kw: {
            **item,
            "url": "http://127.0.0.1:8787/plex/stream/cap-secret-stream-token",
            "stream": "https://plex.example:32400/video?X-Plex-Token=raw-plex-secret",
        },
    )
    monkeypatch.setattr(
        playback_service,
        "queue_item_next",
        lambda item: (1, [item]),
    )
    monkeypatch.setattr(plex_service, "catalog_service", service)

    client = TestClient(create_app(testing=True))
    item_ref = "plex-opaque-item-101"

    play_resp = client.post(
        "/plex/items/action",
        json={"item_id": item_ref, "command": "play_now"},
    )
    assert play_resp.status_code == 200
    play_body = play_resp.json()
    assert "cap-secret-stream-token" not in play_resp.text
    assert "raw-plex-secret" not in play_resp.text
    assert play_body["now_playing"]["url"] == "https://plex.invalid/item"
    assert "stream" not in play_body["now_playing"]

    queue_resp = client.post(
        "/plex/items/action",
        json={"item_id": item_ref, "command": "play_next"},
    )
    assert queue_resp.status_code == 200
    queue_body = queue_resp.json()
    assert "cap-secret-stream-token" not in queue_resp.text
    assert "raw-plex-secret" not in queue_resp.text
    assert queue_body["item"]["url"] == "https://plex.invalid/item"


def test_plex_transcode_stream_rejects_duplicate_open_without_killing_active_transcode() -> None:
    open_calls: list[str] = []
    stop_calls: list[str] = []

    class _FakeUpstreamResponse:
        def __init__(self):
            self._chunks = [b"ts-segment", b""]

        def read(self, _size: int) -> bytes:
            return self._chunks.pop(0)

        def close(self) -> None:
            pass

    class _FakePlexClient:
        def open_stream(self, path, *, range_header="", query=None, on_close=None, allow_incomplete_read=False):
            open_calls.append(path)
            resp = _FakeUpstreamResponse()
            return PlexStreamResponse(
                status_code=200,
                headers={"Content-Type": "video/mp2t"},
                _response=resp,
                _on_close=on_close,
            )

        def request_no_content(self, method, path, *, query=None, auth=True):
            stop_calls.append(f"{method} {path} {query}")

    session = plex_auth.PlexServerSession(
        client=_FakePlexClient(),
        account_id="7",
        machine_id="mach-1",
        generation=3,
        reference_key=b"k" * 32,
    )

    class _FakeAuth:
        def selected_server_session(self):
            return session

        def assert_server_session_current(self, candidate):
            assert candidate is session

    service = plex_service.PlexCatalogService(_FakeAuth())
    tx_uuid = "11111111-2222-3333-4444-555555555555"
    stream_id = service._reference(
        session,
        "transcode_stream",
        f"{plex_service.TRANSCODE_START_PATH}?session={tx_uuid}",
        "media",
    )
    with plex_service._TRANSCODE_LOCK:
        plex_service._TRANSCODE_SESSIONS[stream_id] = plex_service._TranscodeEntry(
            session=session,
            session_id=tx_uuid,
            created_at=time.time(),
        )

    first_stream = service.media_stream(stream_id, range_header="")
    assert len(open_calls) == 1
    assert stop_calls == []

    # A second probe/open against the active transcode stream must fail BEFORE
    # opening a duplicate upstream stream whose on_close would kill the active transcode.
    with pytest.raises(PlexError) as exc_info:
        service.media_stream(stream_id, range_header="")
    assert exc_info.value.code == "plex_stream_expired"
    assert exc_info.value.status_code == 404
    assert len(open_calls) == 1
    assert stop_calls == []

    # Drain & close the legitimate first stream; now transcode stop runs once.
    chunks = list(first_stream.iter_bytes())
    assert chunks == [b"ts-segment"]
    assert len(stop_calls) == 1


def test_plex_and_browse_stream_routes_reject_non_loopback_clients(monkeypatch) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    plex_calls: list[str] = []
    browse_calls: list[tuple[str, str]] = []

    class _MemResp:
        def __init__(self):
            self.items = [b"ok-bytes", b""]

        def read(self, _n: int) -> bytes:
            return self.items.pop(0)

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        plex_service.catalog_service,
        "media_stream",
        lambda sid, **kw: plex_calls.append(sid)
        or PlexStreamResponse(status_code=200, headers={"Content-Type": "video/mp4"}, _response=_MemResp()),
    )
    monkeypatch.setattr(
        browse_service,
        "open_custom_plex_stream",
        lambda sid, path, **kw: browse_calls.append((sid, path))
        or PlexStreamResponse(status_code=200, headers={"Content-Type": "video/mp4"}, _response=_MemResp()),
    )

    app = create_app(testing=True)
    lan_client = TestClient(app, client=("192.168.1.55", 52000))
    r_plex_lan = lan_client.get("/plex/stream/tok-1")
    r_browse_lan = lan_client.get("/browse/stream/srv_1?path=/library/parts/1/a.mp4&exp=9999999999&sig=abc")
    assert r_plex_lan.status_code == 403
    assert r_plex_lan.json()["detail"] == "Stream proxy is local-only"
    assert r_browse_lan.status_code == 403
    assert r_browse_lan.json()["detail"] == "Stream proxy is local-only"
    assert plex_calls == []
    assert browse_calls == []

    for host in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        local_client = TestClient(app, client=(host, 52000))
        r1 = local_client.get("/plex/stream/tok-1")
        r2 = local_client.get("/browse/stream/srv_1?path=/library/parts/1/a.mp4&exp=9999999999&sig=abc")
        assert r1.status_code == 200
        assert r1.content == b"ok-bytes"
        assert r2.status_code == 200
        assert r2.content == b"ok-bytes"


def test_disabled_servers_blocked_across_browse_and_dedupe_caches_cleared(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    servers_file = tmp_path / "media_servers.json"
    monkeypatch.setattr(browse_service, "_servers_path", lambda: str(servers_file))
    monkeypatch.setattr(browse_service, "probe_single_server", lambda rec, **kw: rec)

    disabled_srv = {
        "id": "srv_disabled",
        "name": "Disabled Plex",
        "type": "plex",
        "origin": "custom",
        "enabled": False,
        "server_url": "http://127.0.0.1:32400",
        "token": "disabled-secret",
    }
    monkeypatch.setattr(
        browse_service,
        "_collect_configured_servers",
        lambda include_disabled=True: [disabled_srv] if include_disabled else [],
    )

    assert browse_service._find_server_by_id("srv_disabled") is None
    assert browse_service._find_server_by_id("srv_disabled", include_disabled=True) == disabled_srv

    # _series_member_sources must skip disabled member servers.
    canonical_series_id = browse_service.encode_item_id("srv_disabled", "/library/metadata/5")
    browse_service._SERIES_GROUPS[canonical_series_id] = [
        ("srv_disabled", "/library/metadata/5", "Show", "2024")
    ]
    assert browse_service._series_member_sources(canonical_series_id) == []

    client = TestClient(create_app(testing=True))
    encoded = browse_service.encode_item_id("srv_disabled", "/library/metadata/5")
    assert client.get(f"/browse/item/{encoded}").status_code == 404
    assert client.post("/browse/action", json={"item_id": encoded, "command": "play_now"}).status_code == 400
    assert (
        client.get("/browse/artwork/srv_disabled", params={"path": "/library/metadata/5/thumb/1"}).status_code
        == 404
    )
    assert (
        client.get(
            "/browse/stream/srv_disabled",
            params={"path": "/library/parts/5/file.mp4", "exp": "9999999999", "sig": "x"},
        ).status_code
        == 404
    )

    # Verify upsert_custom_server and delete_custom_server clear all three dedupe caches.
    browse_service._SERIES_GROUPS["grp_1"] = {"id": "grp_1"}
    browse_service._DEDUPED_ITEMS["itm_1"] = {"id": "itm_1"}
    browse_service._DEDUPE_KEY_TO_ITEM_IDS["key_1"] = ["itm_1"]

    created = browse_service.upsert_custom_server(
        {"type": "jellyfin", "name": "Temp JF", "server_url": "http://192.168.1.20:8096"}
    )
    assert browse_service._SERIES_GROUPS == {}
    assert browse_service._DEDUPED_ITEMS == {}
    assert browse_service._DEDUPE_KEY_TO_ITEM_IDS == {}

    browse_service._SERIES_GROUPS["grp_2"] = {"id": "grp_2"}
    browse_service._DEDUPED_ITEMS["itm_2"] = {"id": "itm_2"}
    browse_service._DEDUPE_KEY_TO_ITEM_IDS["key_2"] = ["itm_2"]

    assert browse_service.delete_custom_server(created["id"]) is True
    assert browse_service._SERIES_GROUPS == {}
    assert browse_service._DEDUPED_ITEMS == {}
    assert browse_service._DEDUPE_KEY_TO_ITEM_IDS == {}


def test_custom_plex_artwork_restricts_paths_content_type_and_size(monkeypatch) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    upstream_calls: list[str] = []
    mode = {"content_type": "image/jpeg", "body": b"\xff\xd8\xff\xe0jpeg-bytes"}

    class _PlexHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            upstream_calls.append(self.path)
            body = mode["body"]
            self.send_response(200)
            self.send_header("Content-Type", mode["content_type"])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _PlexHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        server = {
            "id": "srv_plex",
            "type": "plex",
            "origin": "custom",
            "enabled": True,
            "server_url": f"http://127.0.0.1:{httpd.server_port}",
            "token": "plex-art-secret",
        }
        monkeypatch.setattr(browse_service, "_collect_configured_servers", lambda **kw: [server])
        client = TestClient(create_app(testing=True))

        for bad_path in (
            "/photo/:/transcode?url=http://169.254.169.254/latest/meta-data/",
            "/photo/:/transcode",
            "/status/sessions",
            "/library/sections/1/all",
            "/library/metadata/123/thumb?X-Plex-Token=attacker",
            "/library/metadata/notanumber/thumb",
            "/library/metadata/123/evil",
            "http://evil.example/library/metadata/123/thumb",
        ):
            res = client.get("/browse/artwork/srv_plex", params={"path": bad_path})
            assert res.status_code == 400, (bad_path, res.text)
        assert upstream_calls == []

        for valid_path in (
            "/library/metadata/123/thumb",
            "/library/metadata/123/thumb/456789",
            "/library/metadata/123/art/99",
            "/library/metadata/123/banner/1",
            "/library/metadata/123/clearLogo",
            "/library/arts/42",
            "/library/thumbs/77",
        ):
            res = client.get("/browse/artwork/srv_plex", params={"path": valid_path})
            assert res.status_code == 200, (valid_path, res.text)
            assert res.content == b"\xff\xd8\xff\xe0jpeg-bytes"

        # Non-image Content-Type -> 502 browse_artwork_unavailable
        mode["content_type"] = "text/html; charset=utf-8"
        with pytest.raises(PlexError) as exc_html:
            browse_service.fetch_custom_plex_artwork("srv_plex", "/library/metadata/123/thumb/1")
        assert exc_html.value.code == "browse_artwork_unavailable"
        assert exc_html.value.status_code == 502
        assert (
            client.get("/browse/artwork/srv_plex", params={"path": "/library/metadata/123/thumb/1"}).status_code
            == 502
        )

        # Oversized image body -> 502 browse_artwork_unavailable
        mode["content_type"] = "image/png"
        mode["body"] = b"x" * 32
        monkeypatch.setattr(browse_service, "MAX_BINARY_RESPONSE_BYTES", 8)
        with pytest.raises(PlexError) as exc_big:
            browse_service.fetch_custom_plex_artwork("srv_plex", "/library/metadata/123/thumb/1")
        assert exc_big.value.code == "browse_artwork_unavailable"
        assert exc_big.value.status_code == 502
        assert (
            client.get("/browse/artwork/srv_plex", params={"path": "/library/metadata/123/thumb/1"}).status_code
            == 502
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(5)


def test_custom_plex_stream_hmac_capability_signing_and_tamper_rejection(monkeypatch) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    stream_hits: list[str] = []

    class _CustomPlexStreamHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == "/library/metadata/55":
                payload = {
                    "MediaContainer": {
                        "Metadata": [
                            {
                                "ratingKey": "55",
                                "type": "movie",
                                "title": "Signed Stream Movie",
                                "duration": 120000,
                                "Media": [{"Part": [{"key": "/library/parts/88/12345/file.mkv"}]}],
                            }
                        ]
                    }
                }
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            stream_hits.append(parsed.path)
            body = b"signed-mkv-payload"
            self.send_response(200)
            self.send_header("Content-Type", "video/x-matroska")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _CustomPlexStreamHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        server = {
            "id": "srv_cplex",
            "type": "plex",
            "origin": "custom",
            "enabled": True,
            "server_url": f"http://127.0.0.1:{httpd.server_port}",
            "token": "cplex-token-v1",
        }
        monkeypatch.setattr(browse_service, "_collect_configured_servers", lambda **kw: [server])

        resolved = browse_service.resolve_custom_plex_playback_item(
            {
                "provider": "plex",
                "url": "https://plex.invalid/item",
                "plex_item_id": browse_service.encode_item_id("srv_cplex", "/library/metadata/55"),
            }
        )
        parsed_url = urlsplit(resolved["url"])
        assert parsed_url.path == "/browse/stream/srv_cplex"
        qs = parse_qs(parsed_url.query)
        assert qs["path"] == ["/library/parts/88/12345/file.mkv"]
        assert qs["exp"][0].isdigit()
        assert len(qs["sig"][0]) == 64

        client = TestClient(create_app(testing=True), client=("127.0.0.1", 51000))
        ok_resp = client.get(f"{parsed_url.path}?{parsed_url.query}")
        assert ok_resp.status_code == 200
        assert ok_resp.content == b"signed-mkv-payload"
        assert stream_hits == ["/library/parts/88/12345/file.mkv"]
        stream_hits.clear()

        # Unsigned request -> 403
        r_unsigned = client.get(
            "/browse/stream/srv_cplex",
            params={"path": "/library/parts/88/12345/file.mkv"},
        )
        assert r_unsigned.status_code == 403

        # Tampered path with valid sig -> 403
        r_tampered_path = client.get(
            "/browse/stream/srv_cplex",
            params={
                "path": "/library/parts/999/other.mkv",
                "exp": qs["exp"][0],
                "sig": qs["sig"][0],
            },
        )
        assert r_tampered_path.status_code == 403

        # Tampered signature -> 403
        bad_sig = ("0" if qs["sig"][0][0] != "0" else "1") + qs["sig"][0][1:]
        r_tampered_sig = client.get(
            "/browse/stream/srv_cplex",
            params={
                "path": qs["path"][0],
                "exp": qs["exp"][0],
                "sig": bad_sig,
            },
        )
        assert r_tampered_sig.status_code == 403

        # Expired capability token -> 403
        expired_exp = int(time.time()) - 10
        expired_sig = browse_service._custom_plex_stream_signature(
            server, qs["path"][0], expired_exp
        )
        r_expired = client.get(
            "/browse/stream/srv_cplex",
            params={
                "path": qs["path"][0],
                "exp": str(expired_exp),
                "sig": expired_sig,
            },
        )
        assert r_expired.status_code == 403

        # Rotating server token invalidates previously issued capability URL -> 403
        server["token"] = "cplex-token-v2"
        r_rotated = client.get(f"{parsed_url.path}?{parsed_url.query}")
        assert r_rotated.status_code == 403
        assert stream_hits == []
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(5)


# ---------------------------------------------------------------------------
# Group 2: Stored Secret Exfiltration on URL Repointing & Cross-Origin Redirects
# ---------------------------------------------------------------------------


def test_peers_reject_forbidden_ips_and_clear_token_on_origin_repoint(
    tmp_path, monkeypatch
) -> None:
    peers_file = tmp_path / "peers.json"
    monkeypatch.setenv("RELAYTV_PEERS_FILE", str(peers_file))
    monkeypatch.setenv("RELAYTV_DEVICE_ID", "local-relaytv-id")
    runtime_config.refresh_from_env()

    for forbidden in (
        "http://169.254.169.254:8787",
        "http://0.0.0.0:8787",
        "http://[fe80::1]:8787",
        "http://[::]:8787",
    ):
        with pytest.raises(peers.PeerError, match="invalid_peer_url"):
            peers.normalize_base_url(forbidden)

    probed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        peers,
        "probe_identity",
        lambda base_url, *, token="": probed.append((base_url, token))
        or {
            "device_id": "remote-peer-1",
            "device_name": "Kitchen TV",
            "version": "0.10.0",
            "base_url": peers.normalize_base_url(base_url),
        },
    )

    created = peers.add_peer(
        base_url="http://192.168.1.30:8787",
        name="Kitchen TV",
        token="secret-peer-bearer",
    )
    assert created["has_token"] is True

    # Same-origin rename preserves token.
    same_origin = peers.update_peer(created["id"], name="Kitchen Display")
    assert same_origin["has_token"] is True
    assert json.loads(peers_file.read_text())["peers"][0]["token"] == "secret-peer-bearer"

    # Repointing base_url to a new origin without passing token clears stored token.
    repointed = peers.update_peer(created["id"], base_url="http://192.168.1.99:8787")
    assert repointed["has_token"] is False
    assert json.loads(peers_file.read_text())["peers"][0]["token"] == ""

    # Explicit token replacement on origin change stores the new token.
    explicit = peers.update_peer(
        created["id"],
        base_url="http://192.168.1.100:8787",
        token="new-explicit-token",
    )
    assert explicit["has_token"] is True
    assert json.loads(peers_file.read_text())["peers"][0]["token"] == "new-explicit-token"


def test_peers_request_blocks_cross_origin_redirect_without_leaking_bearer() -> None:
    attacker_headers: list[dict[str, str]] = []

    class _AttackerHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            attacker_headers.append(dict(self.headers))
            body = b'{"device_id":"evil"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    attacker = ThreadingHTTPServer(("127.0.0.1", 0), _AttackerHandler)
    attacker_thread = threading.Thread(target=attacker.serve_forever, daemon=True)
    attacker_thread.start()

    class _RedirectingPeerHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{attacker.server_port}/api/identity")
            self.end_headers()

    origin = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectingPeerHandler)
    origin_thread = threading.Thread(target=origin.serve_forever, daemon=True)
    origin_thread.start()

    try:
        with pytest.raises(peers.PeerError):
            peers.probe_identity(
                f"http://127.0.0.1:{origin.server_port}",
                token="do-not-exfiltrate-bearer",
            )
        assert attacker_headers == [], "Bearer token was leaked across cross-origin redirect!"
    finally:
        origin.shutdown()
        origin.server_close()
        attacker.shutdown()
        attacker.server_close()
        origin_thread.join(5)
        attacker_thread.join(5)


def test_custom_server_upsert_rejects_forbidden_ips_and_clears_secrets_on_repoint(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    servers_file = tmp_path / "media_servers.json"
    monkeypatch.setattr(browse_service, "_servers_path", lambda: str(servers_file))
    monkeypatch.setattr(browse_service, "probe_single_server", lambda rec, **kw: rec)

    for bad_url in (
        "http://169.254.169.254:8096",
        "http://0.0.0.0:32400",
        "http://[fe80::1]:8096",
    ):
        with pytest.raises(ValueError, match="Forbidden"):
            browse_service.upsert_custom_server({"type": "jellyfin", "server_url": bad_url})

    client = TestClient(create_app(testing=True))
    resp_bad = client.post(
        "/browse/servers",
        json={"type": "jellyfin", "server_url": "http://169.254.169.254:8096"},
    )
    assert resp_bad.status_code == 400

    created = browse_service.upsert_custom_server(
        {
            "id": "srv_sec",
            "type": "jellyfin",
            "name": "Original JF",
            "server_url": "http://192.168.1.40:8096",
            "username": "alice",
            "password": "alice-password",
            "api_key": "alice-api-key",
            "token": "alice-plex-token",
        }
    )
    sid = created["id"]
    browse_service._CUSTOM_JF_SESSIONS[sid] = ("cached-tok", "uid-1", time.monotonic())

    # Same-origin update preserves stored secrets when omitted.
    browse_service.upsert_custom_server(
        {
            "id": sid,
            "type": "jellyfin",
            "name": "Renamed Same Origin",
            "server_url": "http://192.168.1.40:8096/",
        }
    )
    stored_same = browse_service.load_servers_store()["servers"][0]
    assert stored_same["password"] == "alice-password"
    assert stored_same["api_key"] == "alice-api-key"
    assert stored_same["token"] == "alice-plex-token"

    # Cross-origin repoint without new credentials clears password, api_key, token, and session cache.
    browse_service._CUSTOM_JF_SESSIONS[sid] = ("cached-tok", "uid-1", time.monotonic())
    repointed = browse_service.upsert_custom_server(
        {
            "id": sid,
            "type": "jellyfin",
            "name": "Repointed Host",
            "server_url": "http://192.168.1.250:8096",
        }
    )
    assert repointed["has_password"] is False
    assert repointed["has_api_key"] is False
    assert repointed["has_token"] is False
    stored_repointed = browse_service.load_servers_store()["servers"][0]
    assert stored_repointed["password"] == ""
    assert stored_repointed["api_key"] == ""
    assert stored_repointed["token"] == ""
    assert sid not in browse_service._CUSTOM_JF_SESSIONS


def test_custom_jf_requests_block_cross_origin_redirects_without_leaking_credentials(
    monkeypatch,
) -> None:
    evil_requests: list[tuple[str, str, dict[str, str], bytes]] = []

    class _EvilTargetHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            evil_requests.append(("GET", self.path, dict(self.headers), b""))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"[]")

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length > 0 else b""
            evil_requests.append(("POST", self.path, dict(self.headers), body))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"AccessToken":"evil"}')

    evil = ThreadingHTTPServer(("127.0.0.1", 0), _EvilTargetHandler)
    evil_thread = threading.Thread(target=evil.serve_forever, daemon=True)
    evil_thread.start()

    class _RedirectJfHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{evil.server_port}{self.path}")
            self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > 0:
                self.rfile.read(length)
            self.send_response(307)
            self.send_header("Location", f"http://127.0.0.1:{evil.server_port}{self.path}")
            self.end_headers()

    jf = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectJfHandler)
    jf_thread = threading.Thread(target=jf.serve_forever, daemon=True)
    jf_thread.start()

    try:
        monkeypatch.setattr(browse_service, "_CUSTOM_JF_SESSIONS", {})
        with pytest.raises(Exception):
            browse_service._custom_jf_get_json(
                f"http://127.0.0.1:{jf.server_port}/Users/Public",
                token="custom-jf-secret-token",
            )

        _base, tok, _uid = browse_service._resolve_custom_jf_auth(
            {
                "id": "srv_redir",
                "type": "jellyfin",
                "server_url": f"http://127.0.0.1:{jf.server_port}",
                "username": "operator",
                "password": "super-secret-password",
            }
        )
        assert tok == ""
        assert evil_requests == [], f"Credentials leaked to cross-origin redirect target: {evil_requests}"
    finally:
        jf.shutdown()
        jf.server_close()
        evil.shutdown()
        evil.server_close()
        jf_thread.join(5)
        evil_thread.join(5)


# ---------------------------------------------------------------------------
# Group 3: Jellyfin Stream Metadata, Cancellation & Architecture Boundary
# ---------------------------------------------------------------------------


def test_integrations_package_never_imports_routes() -> None:
    integrations_dir = (
        pathlib.Path(__file__).resolve().parents[1] / "app" / "relaytv_app" / "integrations"
    )
    files = sorted(integrations_dir.glob("*.py"))
    assert files, "Expected integrations modules to exist"

    violations: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "relaytv_app.routes" or alias.name.startswith("relaytv_app.routes."):
                        violations.append(f"{path.name}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if (
                    mod == "relaytv_app.routes"
                    or mod.startswith("relaytv_app.routes.")
                    or (node.level >= 2 and (mod == "routes" or mod.startswith("routes.")))
                ):
                    violations.append(f"{path.name}:{node.lineno} from {'.' * node.level}{mod}")
                if node.level >= 2 and mod == "":
                    for alias in node.names:
                        if alias.name == "routes":
                            violations.append(f"{path.name}:{node.lineno} from .. import routes")

    assert violations == []


def test_browse_action_forwards_version_audio_and_subtitle_for_primary_and_custom_jf(
    monkeypatch,
) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()

    jf_primary = {
        "id": "jf_primary",
        "type": "jellyfin",
        "origin": "primary",
        "enabled": True,
        "server_url": "http://127.0.0.1:18096",
        "api_key": "primary-key",
    }
    jf_custom = {
        "id": "srv_emby",
        "type": "emby",
        "origin": "custom",
        "enabled": True,
        "server_url": "http://127.0.0.1:18097",
        "api_key": "custom-emby-key",
        "user_id": "u-1",
    }
    monkeypatch.setattr(
        browse_service,
        "_collect_configured_servers",
        lambda **kw: [jf_primary, jf_custom],
    )
    monkeypatch.setattr(
        jellyfin_receiver,
        "status",
        lambda: {
            "enabled": True,
            "running": True,
            "connected": True,
            "catalog_ready": True,
            "server_url": "http://127.0.0.1:18096",
        },
    )

    dispatched: list[tuple[str, dict[str, object]]] = []

    def _capture_dispatch(action: str, payload: dict[str, object], *, guard=None, cast_scope=True):
        dispatched.append((action, dict(payload)))
        return {"ok": True, "action": "play", "now_playing": {"title": "Primary Movie", "url": "http://127.0.0.1:18096/Videos/m1/stream?static=true&api_key=primary-key"}}

    monkeypatch.setattr(jellyfin_receiver, "dispatch_command", _capture_dispatch)
    monkeypatch.setattr(
        browse_service,
        "get_unified_item_detail",
        lambda iid, **kw: {"item_id": iid, "type": "movie", "title": "Custom Movie"},
    )
    played_custom: list[dict[str, object]] = []
    monkeypatch.setattr(
        playback_service,
        "play_now",
        lambda entry, **kw: played_custom.append(dict(entry)) or dict(entry),
    )

    client = TestClient(create_app(testing=True))

    # 1) Primary Jellyfin action forwards version_id, audio_id, subtitle_id, resume_pos via jellyfin_service.
    r_primary = client.post(
        "/browse/action",
        json={
            "item_id": browse_service.encode_item_id("jf_primary", "m1"),
            "command": "resume",
            "resume_pos": 95.5,
            "version_id": "ms-director-cut",
            "audio_id": "3",
            "subtitle_id": "6",
        },
    )
    assert r_primary.status_code == 200, r_primary.text
    assert "primary-key" not in r_primary.text
    assert len(dispatched) == 1
    act, pl = dispatched[0]
    assert act == "Play"
    assert pl["ItemId"] == "m1"
    assert pl["PlayCommand"] == "Resume"
    assert pl["MediaSourceId"] == "ms-director-cut"
    assert pl["AudioStreamIndex"] == 3
    assert pl["SubtitleStreamIndex"] == 6
    assert pl["_start_pos"] == 95.5

    # 2) Custom Jellyfin/Emby action forwards version_id, audio_id, subtitle_id into stream URL & metadata.
    r_custom = client.post(
        "/browse/action",
        json={
            "item_id": browse_service.encode_item_id("srv_emby", "c99"),
            "command": "play_now",
            "version_id": "ms-4k",
            "audio_id": "2",
            "subtitle_id": "-1",
        },
    )
    assert r_custom.status_code == 200, r_custom.text
    assert "custom-emby-key" not in r_custom.text
    assert len(played_custom) == 1
    entry = played_custom[0]
    parsed_qs = parse_qs(urlsplit(str(entry["url"])).query)
    assert parsed_qs["mediaSourceId"] == ["ms-4k"]
    assert parsed_qs["audioStreamIndex"] == ["2"]
    assert parsed_qs["subtitleStreamIndex"] == ["-1"]
    assert entry["jellyfin_media_source_id"] == "ms-4k"
    assert entry["jellyfin_audio_stream_index"] == "2"
    assert entry["jellyfin_subtitle_stream_index"] == "-1"


def test_jellyfin_series_play_all_cancels_cleanly_on_clear_during_remaining_episodes(
    monkeypatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()

    jellyfin_service.reset_command_state()
    monkeypatch.setattr(
        jellyfin_receiver,
        "status",
        lambda: {
            "enabled": True,
            "running": True,
            "connected": True,
            "catalog_ready": True,
            "server_url": "http://127.0.0.1:18096",
        },
    )
    monkeypatch.setattr(jellyfin_service, "catalog_token", lambda: "jf-sec")
    monkeypatch.setattr(
        jellyfin_receiver,
        "list_series_episodes",
        lambda sid, **kw: {
            "episodes": [
                {"item_id": "ep1", "title": "Ep 1", "series_name": "Test Show"},
                {"item_id": "ep2", "title": "Ep 2", "series_name": "Test Show"},
                {"item_id": "ep3", "title": "Ep 3", "series_name": "Test Show"},
            ]
        },
    )
    monkeypatch.setattr(
        jellyfin_service,
        "resolve_playable_item",
        lambda iid, **kw: {"item_id": iid, "media_source_id": "", "detail": {"item_id": iid}},
    )

    def _detail_lookup(iid: str, **kw):
        if iid == "ep2":
            entered.set()
            assert release.wait(5)
        return {"item_id": iid, "title": f"Title {iid}", "video_codec": "h264"}

    monkeypatch.setattr(jellyfin_receiver, "get_item_detail", _detail_lookup)
    monkeypatch.setattr(jellyfin_receiver, "get_item_metadata", lambda iid, **kw: _detail_lookup(iid))
    monkeypatch.setattr(playback_service, "play_now", lambda item, **kw: dict(item))
    monkeypatch.setattr(state, "QUEUE", state._RevisionedQueue())
    monkeypatch.setattr(state, "persist_queue", lambda: True)
    monkeypatch.setattr(player, "prefetch_queue_item_stream", lambda *a, **kw: None)
    monkeypatch.setattr(player, "prime_mpv_up_next_from_queue", lambda **kw: None)
    queue_routes = importlib.import_module("relaytv_app.routes.queue")
    monkeypatch.setattr(queue_routes, "_ui_event_push_queue", lambda *a, **kw: None)

    client = TestClient(create_app(testing=True))
    outcomes: list = []
    worker = threading.Thread(
        target=lambda: outcomes.append(client.post("/jellyfin/tv/series/show-1/play_all")),
        daemon=True,
    )
    worker.start()
    try:
        assert entered.wait(5), "ep2 resolution never entered flight"
        assert client.post("/clear").status_code == 200
        newer = {"url": "https://example.test/after-clear.mp4", "title": "Newer"}
        playback_service.queue_item(newer)
    finally:
        release.set()
        worker.join(5)

    assert not worker.is_alive()
    assert outcomes and outcomes[0].status_code == 200, outcomes
    body = outcomes[0].json()
    assert body["cancelled"] is True
    assert body["queued_count"] == 0
    assert "jf-sec" not in outcomes[0].text
    assert state.QUEUE == [newer]


@pytest.mark.parametrize("cancel_kind", ["clear", "disconnect"])
def test_jellyfin_series_play_all_first_episode_cancel_prevents_late_playback(
    monkeypatch, cancel_kind: str
) -> None:
    entered = threading.Event()
    release = threading.Event()
    gen = {"value": 10}
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()

    jellyfin_service.reset_command_state()
    monkeypatch.setattr(jellyfin_receiver, "config_generation", lambda: gen["value"])
    monkeypatch.setattr(
        jellyfin_receiver,
        "status",
        lambda: {
            "enabled": True,
            "running": True,
            "connected": True,
            "catalog_ready": True,
            "server_url": "http://127.0.0.1:18096",
        },
    )
    monkeypatch.setattr(
        jellyfin_receiver,
        "list_series_episodes",
        lambda sid, **kw: {"episodes": [{"item_id": "ep1", "title": "Ep 1"}]},
    )

    def _resolve_ep1(iid: str, **kw):
        entered.set()
        assert release.wait(5)
        return {
            "url": "http://127.0.0.1:18096/Videos/ep1/stream?static=true",
            "title": "Ep 1",
            "provider": "jellyfin",
            "jellyfin_item_id": "ep1",
        }

    monkeypatch.setattr(jellyfin_service, "resolve_playable_item", _resolve_ep1)
    monkeypatch.setattr(
        playback_service,
        "play_now",
        lambda *a, **kw: pytest.fail("Cancelled series play_all must not start playback!"),
    )
    monkeypatch.setattr(state, "QUEUE", state._RevisionedQueue())
    monkeypatch.setattr(state, "persist_queue", lambda: True)
    monkeypatch.setattr(player, "prime_mpv_up_next_from_queue", lambda **kw: None)
    queue_routes = importlib.import_module("relaytv_app.routes.queue")
    monkeypatch.setattr(queue_routes, "_ui_event_push_queue", lambda *a, **kw: None)

    client = TestClient(create_app(testing=True), raise_server_exceptions=False)
    outcomes: list = []
    worker = threading.Thread(
        target=lambda: outcomes.append(client.post("/jellyfin/tv/series/show-1/play_all")),
        daemon=True,
    )
    worker.start()
    try:
        assert entered.wait(5)
        if cancel_kind == "clear":
            assert client.post("/clear").status_code == 200
        else:
            gen["value"] += 1
    finally:
        release.set()
        worker.join(5)

    assert not worker.is_alive()
    assert outcomes and outcomes[0].status_code == 409, outcomes
    assert state.QUEUE == []


def test_jellyfin_get_item_detail_exposes_media_sources_and_projects_requested_source(
    monkeypatch,
) -> None:
    raw_item = {
        "Id": "multi-ver-1",
        "Name": "Blade Runner",
        "Type": "Movie",
        "MediaSources": [
            {
                "Id": "ms-1080p",
                "Name": "1080p Theatrical",
                "Container": "mp4",
                "Bitrate": 8_000_000,
                "MediaStreams": [
                    {"Type": "Video", "Index": 0, "Codec": "h264", "Width": 1920, "Height": 1080, "BitDepth": 8},
                    {"Type": "Audio", "Index": 1, "Language": "eng", "Codec": "aac", "IsDefault": True},
                    {"Type": "Subtitle", "Index": 2, "Language": "eng", "Codec": "subrip"},
                ],
            },
            {
                "Id": "ms-4k",
                "Name": "4K Final Cut",
                "Container": "mkv",
                "Bitrate": 45_000_000,
                "MediaStreams": [
                    {"Type": "Video", "Index": 0, "Codec": "hevc", "Width": 3840, "Height": 2160, "BitDepth": 10},
                    {"Type": "Audio", "Index": 4, "Language": "jpn", "Codec": "truehd", "IsDefault": True},
                    {"Type": "Subtitle", "Index": 7, "Language": "jpn", "Codec": "pgssub", "IsExternal": False},
                ],
            },
        ],
    }

    class _JfDetailHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = json.dumps(raw_item).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _JfDetailHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        base_url = f"http://127.0.0.1:{httpd.server_port}"
        monkeypatch.setattr(
            jellyfin_receiver,
            "_catalog_base_token_user",
            lambda: (base_url, "jf-tok", "user-1"),
        )
        monkeypatch.setattr(jellyfin_receiver, "_attach_thumb", lambda item: item)
        jellyfin_receiver._CATALOG_CACHE.clear()

        default_detail = jellyfin_receiver.get_item_detail("multi-ver-1", refresh=True)
        assert default_detail["media_source_id"] == "ms-1080p"
        assert len(default_detail["media_sources"]) == 2
        assert [s["index"] for s in default_detail["audio_streams"]] == [1]
        assert default_detail["video_codec"] == "h264"

        # Requesting secondary media_source_id projects its stream indices & video metadata (even from cache).
        secondary = jellyfin_receiver.get_item_detail("multi-ver-1", media_source_id="ms-4k")
        assert secondary["media_source_id"] == "ms-4k"
        assert [s["index"] for s in secondary["audio_streams"]] == [4]
        assert [s["index"] for s in secondary["subtitle_streams"]] == [7]
        assert secondary["video_codec"] == "hevc"
        assert secondary["video_height"] == 2160
        assert secondary["video_bit_depth"] == 10
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(5)


def test_jellyfin_retarget_and_restart_renegotiate_transcode_on_static_streams(
    monkeypatch,
) -> None:
    server_url = "http://127.0.0.1:18096"
    monkeypatch.setattr(
        jellyfin_receiver,
        "status",
        lambda: {"enabled": True, "running": True, "server_url": server_url},
    )
    monkeypatch.setattr(jellyfin_service, "catalog_token", lambda: "jf-tok")
    monkeypatch.setattr(
        state,
        "get_settings",
        lambda: {
            "jellyfin_playback_mode": "auto",
            "jellyfin_audio_lang": "jpn",
            "jellyfin_sub_lang": "eng",
        },
    )
    monkeypatch.setattr(state, "persist_queue", lambda: True)
    monkeypatch.setattr(player, "prime_mpv_up_next_from_queue", lambda **kw: None)

    detail_with_pgs = {
        "item_id": "item-pgs",
        "media_source_id": "ms-1",
        "video_codec": "h264",
        "video_height": 1080,
        "video_bit_depth": 8,
        "audio_streams": [
            {"index": 1, "language": "eng", "codec": "aac"},
            {"index": 2, "language": "jpn", "codec": "aac"},
        ],
        "subtitle_streams": [
            {"index": 5, "language": "eng", "codec": "pgssub", "is_external": False},
        ],
    }

    monkeypatch.setattr(
        jellyfin_receiver,
        "get_item_detail",
        lambda iid, **kw: dict(detail_with_pgs) if iid == "item-pgs" else {},
    )
    monkeypatch.setattr(
        jellyfin_receiver,
        "resolve_playback_url",
        lambda iid, **kw: {
            "url": (
                f"{server_url}/Videos/{iid}/master.m3u8"
                f"?MediaSourceId={kw.get('media_source_id') or 'ms-1'}"
                f"&AudioStreamIndex={kw.get('audio_stream_index')}"
                f"&SubtitleStreamIndex={kw.get('subtitle_stream_index')}&api_key=jf-tok"
            ),
            "method": "transcode",
            "media_source_id": kw.get("media_source_id") or "ms-1",
        },
    )

    # 1) retarget_queue_stream_preferences upgrades static=true to transcode when PGS subtitle burn-in is needed.
    queued_static = {
        "url": f"{server_url}/Videos/item-pgs/stream?static=true&MediaSourceId=ms-1&api_key=jf-tok",
        "provider": "jellyfin",
        "jellyfin_item_id": "item-pgs",
        "jellyfin_media_source_id": "ms-1",
        "jellyfin_stream_mode": "direct",
    }
    monkeypatch.setattr(state, "QUEUE", state._RevisionedQueue([queued_static]))
    changed = jellyfin_service.retarget_queue_stream_preferences()
    assert changed == 1
    updated_q = state.QUEUE[0]
    assert "master.m3u8" in updated_q["url"]
    assert "static=true" not in updated_q["url"]
    assert updated_q["jellyfin_stream_mode"] == "transcode"
    assert updated_q["jellyfin_audio_stream_index"] == "2"
    assert updated_q["jellyfin_subtitle_stream_index"] == "5"

    # 2) Offline fallback: when metadata lookup is empty but _preferred_indices_for_item yields indices,
    # fast query-param update still works without failing.
    offline_item = {
        "url": f"{server_url}/Videos/offline-1/stream?static=true&api_key=jf-tok",
        "provider": "jellyfin",
        "jellyfin_item_id": "offline-1",
    }
    monkeypatch.setattr(state, "QUEUE", state._RevisionedQueue([offline_item]))
    monkeypatch.setattr(
        jellyfin_service,
        "_preferred_indices_for_item",
        lambda iid, **kw: ("3", "4"),
    )
    changed_offline = jellyfin_service.retarget_queue_stream_preferences()
    assert changed_offline == 1
    assert "audioStreamIndex=3" in state.QUEUE[0]["url"]
    assert "subtitleStreamIndex=4" in state.QUEUE[0]["url"]

    # 3) _restart_with_stream_params renegotiates static=true stream to transcode when switching tracks.
    restarted_payloads: list[dict[str, object]] = []
    monkeypatch.setattr(playback_service, "suppress_auto_next", lambda _sec: None)
    monkeypatch.setattr(
        playback_service,
        "play_now",
        lambda payload, **kw: restarted_payloads.append(dict(payload)) or dict(payload),
    )
    detail_h264 = {
        "item_id": "item-pgs",
        "media_source_id": "ms-1",
        "video_codec": "h264",
        "video_height": 1080,
        "video_bit_depth": 8,
        "audio_streams": [
            {"index": 1, "language": "eng", "codec": "aac"},
            {"index": 2, "language": "jpn", "codec": "aac"},
        ],
        "subtitle_streams": [{"index": 6, "language": "eng", "codec": "subrip"}],
    }
    monkeypatch.setattr(jellyfin_receiver, "get_item_detail", lambda iid, **kw: dict(detail_h264))
    now_out = jellyfin_service._restart_with_stream_params(
        now={
            "url": f"{server_url}/Videos/item-pgs/stream?static=true&MediaSourceId=ms-1&api_key=jf-tok",
            "title": "Movie",
            "provider": "jellyfin",
            "jellyfin_item_id": "item-pgs",
        },
        detail=detail_h264,
        item_id="item-pgs",
        server_url=server_url,
        media_source_id="ms-1",
        audio_stream_index="2",
        subtitle_stream_index="6",
        start_pos=33.0,
        mode="jellyfin_audio_switch",
    )
    assert len(restarted_payloads) == 1
    assert "master.m3u8" in str(restarted_payloads[0]["url"])
    assert now_out["jellyfin_stream_mode"] == "transcode"


def test_jellyfin_apply_startup_stream_tracks_applies_subtitles_on_video_only_item(
    monkeypatch,
) -> None:
    now_item = {
        "provider": "jellyfin",
        "title": "Silent Film (1927)",
        "url": "http://127.0.0.1:18096/Videos/silent-1/stream?static=true",
        "jellyfin_item_id": "silent-1",
        "jellyfin_subtitle_stream_index": "2",
        "audio_streams": [],
        "subtitle_streams": [{"index": 2, "language": "eng", "display": "English Intertitles"}],
    }
    monkeypatch.setattr(state, "NOW_PLAYING", dict(now_item), raising=False)
    monkeypatch.setattr(player, "playback_intent_current", lambda _intent: True)
    monkeypatch.setattr(player, "apply_playback_intent", lambda _intent, fn: fn() or True)

    mpv_sets: list[tuple[str, object]] = []
    selected_sub = {"value": False}

    def _mpv_get(prop: str):
        if prop == "track-list":
            return [
                {"id": 1, "type": "video", "selected": True},
                {
                    "id": 2,
                    "type": "sub",
                    "ff-index": 2,
                    "lang": "eng",
                    "title": "English Intertitles",
                    "selected": selected_sub["value"],
                },
            ]
        return None

    def _mpv_set(prop: str, val: object):
        mpv_sets.append((prop, val))
        if prop == "sid" and val == 2:
            selected_sub["value"] = True

    monkeypatch.setattr(player, "mpv_get", _mpv_get)
    monkeypatch.setattr(player, "mpv_set", _mpv_set)
    monkeypatch.setattr(
        playback_service,
        "update_now_playing",
        lambda updated: monkeypatch.setattr(state, "NOW_PLAYING", dict(updated), raising=False),
    )

    jellyfin_service.apply_startup_stream_tracks(
        subtitle_stream_index="2",
        playback_intent=1,
        max_retries=2,
        delay_sec=0.0,
        run_async=False,
    )

    assert ("sid", 2) in mpv_sets, f"Expected subtitle track sid=2 to be applied on 0-audio video, got {mpv_sets}"


# ---------------------------------------------------------------------------
# Group 4: Plex Link Flow Locking & Transient Error Resilience
# ---------------------------------------------------------------------------


def _make_plex_jwt() -> str:
    return jwt.encode(
        {"sub": "42", "username": "mark", "exp": int(time.time()) + 7 * 86400},
        "test-only-plex-key",
        algorithm="HS256",
    )


class _ResilientLinkClient:
    def __init__(self, factory, base_url: str, token: str):
        self.factory = factory
        self.base_url = base_url
        self.token = token

    def get(self, path: str, *, query=None, auth: bool = True):
        return self.factory.handle("GET", path, query, self.token)

    def post(self, path: str, *, query=None, body=None, auth: bool = True):
        return self.factory.handle("POST", path, body, self.token)


class _ResilientLinkFactory:
    def __init__(self, account_token: str):
        self.account_token = account_token
        self.pin_calls = 0
        self.fail_stage: str | None = None
        self.resources_entered = threading.Event()
        self.resources_release = threading.Event()
        self.block_resources = False

    def __call__(self, base_url: str, token: str):
        return _ResilientLinkClient(self, base_url, token)

    def handle(self, method: str, path: str, payload, token: str):
        if method == "POST" and path == "/api/v2/pins":
            return {"id": 777, "code": "pin-777", "expiresIn": 1800}
        if method == "GET" and path == "/api/v2/pins/777":
            self.pin_calls += 1
            if self.fail_stage == "pin":
                raise PlexError("plex_unavailable", "Plex cloud temporary 503", status_code=502)
            return {"authToken": self.account_token}
        if method == "GET" and path == "/api/v2/user":
            if self.fail_stage == "user":
                raise PlexError("plex_unavailable", "Plex user endpoint temporary 502", status_code=502)
            return {"id": 42, "username": "mark", "friendlyName": "Mark"}
        if method == "GET" and path == "/api/v2/resources":
            if self.block_resources:
                self.resources_entered.set()
                assert self.resources_release.wait(5)
            if self.fail_stage == "resources":
                raise PlexError("plex_unavailable", "Plex resources temporary 504", status_code=502)
            return [
                {
                    "name": "Living Room Plex",
                    "clientIdentifier": "srv-living",
                    "provides": "server",
                    "owned": True,
                    "presence": True,
                    "accessToken": "srv-secret-tok",
                    "connections": [
                        {
                            "uri": "https://192-168-1-10.example.plex.direct:32400",
                            "local": True,
                            "relay": False,
                        }
                    ],
                }
            ]
        raise AssertionError((method, path, payload, token))


def test_plex_poll_link_blocks_during_active_playback_and_recovers_after_stop(
    tmp_path, monkeypatch
) -> None:
    account_token = _make_plex_jwt()
    factory = _ResilientLinkFactory(account_token)
    monkeypatch.setattr(plex_auth.state, "update_settings", lambda patch: dict(patch))
    monkeypatch.setattr(plex_auth.state, "get_settings", lambda: {"plex_enabled": True})
    auth = plex_auth.PlexAuthManager(
        store=plex_auth.PlexAuthStore(str(tmp_path / "plex_auth.json")),
        client_identity=plex_auth.PlexClientIdentity("relaytv-dev"),
        client_factory=factory,
    )
    monkeypatch.setattr(plex_auth, "auth_manager", auth)
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()

    client = TestClient(create_app(testing=True))
    started = client.post("/integrations/plex/auth/start")
    assert started.status_code == 200
    flow_id = started.json()["flow_id"]

    # Active Plex playback blocks poll_link with 409 without retiring flow_id or cookie.
    monkeypatch.setattr(state, "SESSION_STATE", "playing", raising=False)
    monkeypatch.setattr(
        state,
        "NOW_PLAYING",
        {"provider": "plex", "plex_item_id": "opaque-1"},
        raising=False,
    )
    blocked = client.post("/integrations/plex/auth/poll", json={"flow_id": flow_id})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "plex_playback_active"
    assert auth.store.load()["account"] == {}

    # Once playback stops, the same flow_id completes cleanly.
    monkeypatch.setattr(state, "SESSION_STATE", "idle", raising=False)
    monkeypatch.setattr(state, "NOW_PLAYING", None, raising=False)
    completed = client.post("/integrations/plex/auth/poll", json={"flow_id": flow_id})
    assert completed.status_code == 200
    assert completed.json()["linked"] is True
    assert completed.json()["servers"][0]["machine_id"] == "srv-living"


def test_plex_poll_link_survives_transient_errors_across_pin_user_and_resources(
    tmp_path, monkeypatch
) -> None:
    account_token = _make_plex_jwt()
    factory = _ResilientLinkFactory(account_token)
    monkeypatch.setattr(plex_auth.state, "update_settings", lambda patch: dict(patch))
    monkeypatch.setattr(plex_auth.state, "get_settings", lambda: {"plex_enabled": True})
    monkeypatch.setattr(state, "SESSION_STATE", "idle", raising=False)
    monkeypatch.setattr(state, "NOW_PLAYING", None, raising=False)

    auth = plex_auth.PlexAuthManager(
        store=plex_auth.PlexAuthStore(str(tmp_path / "plex_auth.json")),
        client_identity=plex_auth.PlexClientIdentity("relaytv-dev"),
        client_factory=factory,
    )
    started = auth.start_link("browser-secret-1")
    flow_id = started["flow_id"]

    # 1) Transient error during PIN claim keeps flow alive.
    factory.fail_stage = "pin"
    with pytest.raises(PlexError) as exc_pin:
        auth.poll_link(flow_id, "browser-secret-1")
    assert exc_pin.value.code == "plex_unavailable"
    assert auth.store.load()["account"] == {}

    # 2) PIN claim succeeds, then /api/v2/user fails transiently -> flow stays alive AND caches claimed_token.
    factory.fail_stage = "user"
    with pytest.raises(PlexError) as exc_user:
        auth.poll_link(flow_id, "browser-secret-1")
    assert exc_user.value.code == "plex_unavailable"
    assert auth.store.load()["account"] == {}
    pin_calls_after_claim = factory.pin_calls
    assert pin_calls_after_claim == 2

    # 3) /api/v2/user succeeds, then /api/v2/resources fails transiently -> store still untouched, flow still alive!
    factory.fail_stage = "resources"
    with pytest.raises(PlexError) as exc_res:
        auth.poll_link(flow_id, "browser-secret-1")
    assert exc_res.value.code == "plex_unavailable"
    assert auth.store.load()["account"] == {}
    assert factory.pin_calls == pin_calls_after_claim, "Cached PIN token avoided redundant PIN claim"

    # 4) Transient error clears -> retry with the same flow_id succeeds and persists account.
    factory.fail_stage = None
    linked = auth.poll_link(flow_id, "browser-secret-1")
    assert linked["linked"] is True
    assert linked["servers"][0]["machine_id"] == "srv-living"
    assert auth.store.load()["account"]["auth_token"] == account_token


@pytest.mark.parametrize("cancel_action", ["disconnect", "cancel_link"])
def test_plex_poll_link_serializes_commit_against_concurrent_disconnect_or_cancel(
    tmp_path, monkeypatch, cancel_action: str
) -> None:
    account_token = _make_plex_jwt()
    factory = _ResilientLinkFactory(account_token)
    factory.block_resources = True
    monkeypatch.setattr(plex_auth.state, "update_settings", lambda patch: dict(patch))
    monkeypatch.setattr(plex_auth.state, "get_settings", lambda: {"plex_enabled": True})
    monkeypatch.setattr(state, "SESSION_STATE", "idle", raising=False)
    monkeypatch.setattr(state, "NOW_PLAYING", None, raising=False)

    auth = plex_auth.PlexAuthManager(
        store=plex_auth.PlexAuthStore(str(tmp_path / "plex_auth.json")),
        client_identity=plex_auth.PlexClientIdentity("relaytv-dev"),
        client_factory=factory,
    )
    started = auth.start_link("browser-secret-race")
    flow_id = started["flow_id"]
    errors: list[Exception] = []

    def _run_poll():
        try:
            auth.poll_link(flow_id, "browser-secret-race")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    worker = threading.Thread(target=_run_poll, daemon=True)
    worker.start()
    try:
        assert factory.resources_entered.wait(5), "poll_link never reached /api/v2/resources"
        if cancel_action == "disconnect":
            auth.disconnect()
        else:
            auth.cancel_link(flow_id, "browser-secret-race")
    finally:
        factory.resources_release.set()
        worker.join(5)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], plex_auth.PlexAuthError)
    assert errors[0].code == "plex_flow_cancelled"
    assert auth.store.load()["account"] == {}
    assert auth.status()["linked"] is False
