# SPDX-License-Identifier: GPL-3.0-only
"""Revert-proof hardening tests for integrations & routes review fixes."""

from __future__ import annotations

import ipaddress
import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from relaytv_app import playback_service, player, state
from relaytv_app.config import runtime_config
from relaytv_app.integrations import (
    browse_service,
    iptv_service,
    jellyfin_receiver,
    jellyfin_service,
    jellyfin_ws,
    plex_auth,
    plex_client,
    seerr_client,
)
from relaytv_app.integrations.iptv_store import IptvStore
from relaytv_app.integrations.plex_client import (
    PlexClient,
    PlexClientIdentity,
    PlexError,
)
from relaytv_app.integrations.seerr_client import SeerrClient, SeerrError
from relaytv_app.main import create_app


@pytest.fixture(autouse=True)
def _restore_receiver_and_runtime_config():
    with jellyfin_receiver._LOCK:
        saved_status = dict(jellyfin_receiver._STATUS)
        saved_api_key = jellyfin_receiver._API_KEY
        saved_auth_mode = jellyfin_receiver._AUTH_MODE
        saved_user = jellyfin_receiver._AUTH_USERNAME
        saved_pass = jellyfin_receiver._AUTH_PASSWORD
        saved_access = jellyfin_receiver._ACCESS_TOKEN
        saved_uid = jellyfin_receiver._AUTH_USER_ID
        saved_sid = jellyfin_receiver._AUTH_SESSION_ID
    try:
        yield
    finally:
        with jellyfin_receiver._LOCK:
            jellyfin_receiver._STATUS.clear()
            jellyfin_receiver._STATUS.update(saved_status)
            jellyfin_receiver._API_KEY = saved_api_key
            jellyfin_receiver._AUTH_MODE = saved_auth_mode
            jellyfin_receiver._AUTH_USERNAME = saved_user
            jellyfin_receiver._AUTH_PASSWORD = saved_pass
            jellyfin_receiver._ACCESS_TOKEN = saved_access
            jellyfin_receiver._AUTH_USER_ID = saved_uid
            jellyfin_receiver._AUTH_SESSION_ID = saved_sid
        jellyfin_receiver.clear_catalog_cache()
        runtime_config.refresh_from_env()


@contextmanager
def _local_http_server(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _mock_dns(monkeypatch, mapping: dict[str, str]):
    orig_getaddrinfo = socket.getaddrinfo

    def _fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        key = str(host or "").strip().strip("[]").lower()
        if key in mapping:
            ip_str = mapping[key]
            ip_obj = ipaddress.ip_address(ip_str)
            sock_port = int(port or 80) if isinstance(port, (int, str)) and str(port).isdigit() else 80
            if isinstance(ip_obj, ipaddress.IPv6Address):
                return [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip_str, sock_port, 0, 0))]
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip_str, sock_port))]
        return orig_getaddrinfo(host, port, family, type, proto, flags)

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)


# ===========================================================================
# Fix 1: Cross-Origin Jellyfin Credential Isolation, Connect/Settings & 401
# ===========================================================================


def test_smart_item_from_url_isolates_foreign_origins_from_primary_credentials(
    monkeypatch,
) -> None:
    primary_url = "http://jf-primary.lan:8096"
    foreign_url = "http://jf-foreign.lan:8096"
    item_id = "0123456789abcdef0123456789abcdef"

    monkeypatch.setattr(
        jellyfin_receiver,
        "status",
        lambda: {"enabled": True, "running": True, "server_url": primary_url},
    )
    monkeypatch.setattr(jellyfin_service, "catalog_token", lambda: "primary-secret-token")
    monkeypatch.setattr(state, "get_settings", lambda: {"jellyfin_playback_mode": "direct"})

    pref_calls: list[tuple[str, str]] = []
    select_calls: list[tuple[str, str]] = []
    meta_calls: list[dict[str, str]] = []

    monkeypatch.setattr(
        jellyfin_service,
        "_preferred_indices_for_item",
        lambda iid, **kw: pref_calls.append((iid, str(kw.get("user_id_override") or "")))
        or ("1", "2"),
    )
    monkeypatch.setattr(
        jellyfin_service,
        "select_playback_url",
        lambda **kw: select_calls.append((str(kw.get("server_url")), str(kw.get("api_key"))))
        or {
            "url": str(kw.get("source_url") or ""),
            "mode": "direct",
            "reason": "direct_mode",
            "media_source_id": str(kw.get("media_source_id") or ""),
        },
    )
    monkeypatch.setattr(
        jellyfin_receiver,
        "get_item_metadata",
        lambda iid, **kw: meta_calls.append(
            {
                "iid": iid,
                "token": str(kw.get("token_override") or ""),
                "server_url": str(kw.get("server_url_override") or ""),
                "user_id": str(kw.get("user_id_override") or ""),
            }
        )
        or {"title": "Resolved Title"},
    )

    # 1) Foreign origin without api_key= must NEVER receive primary token, user_id, or metadata probe.
    foreign_shared = f"{foreign_url}/Videos/{item_id}/stream?static=true"
    foreign_item = jellyfin_service.smart_item_from_url(
        foreign_shared, user_id_override="primary-user-42"
    )
    assert foreign_item is not None
    assert meta_calls == []
    assert select_calls == []
    assert pref_calls == []
    assert "_jellyfin_metadata_user_id" not in foreign_item
    assert "primary-secret-token" not in str(foreign_item.get("url") or "")
    assert "api_key=" not in str(foreign_item.get("url") or "")
    assert foreign_item.get("browse_server_id") == "external"

    # 2) Same-origin and relative URLs DO attach primary catalog_token() and user_id_override.
    rel_item = jellyfin_service.smart_item_from_url(
        f"/Videos/{item_id}/stream?static=true", user_id_override="primary-user-42"
    )
    assert rel_item is not None
    assert "api_key=primary-secret-token" in str(rel_item.get("url") or "")
    assert rel_item.get("_jellyfin_metadata_user_id") == "primary-user-42"
    assert "browse_server_id" not in rel_item
    assert len(pref_calls) == 1
    assert len(select_calls) == 1
    assert len(meta_calls) == 1
    assert meta_calls[0]["token"] == "primary-secret-token"


def test_get_item_metadata_never_falls_back_to_primary_token_for_cross_origin_override(
    monkeypatch,
) -> None:
    jellyfin_receiver.clear_catalog_cache()
    monkeypatch.setattr(
        jellyfin_receiver,
        "_request_context",
        lambda: jellyfin_receiver._RequestContext(
            generation=1,
            enabled=True,
            running=True,
            server_url="http://jf-primary.lan:8096",
            device_id="dev-1",
            device_name="RelayTV",
            client_name="RelayTV",
            client_version="1.0",
            username="",
            password="",
            auth_mode="shared_api_key",
            control_token="primary-secret-key",
            catalog_token="primary-secret-key",
            catalog_user_id="primary-uid-7",
            authenticated=True,
            connected=True,
            last_register_ok=True,
            register_retry_failures=0,
            next_register_retry_ts=0.0,
            last_detect_ok=True,
            last_detect_ts=0.0,
        ),
    )

    seen_requests: list[tuple[str, dict[str, str]]] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"Name": "Public Foreign Item"}'

    def _fake_urlopen(req, timeout=5.0):
        headers = {k.lower(): v for k, v in req.header_items()}
        seen_requests.append((req.full_url, headers))
        return _Resp()

    monkeypatch.setattr(jellyfin_receiver._urlrequest, "urlopen", _fake_urlopen)

    meta = jellyfin_receiver.get_item_metadata(
        "foreign-item-1",
        server_url_override="http://jf-foreign.lan:8096",
    )
    assert meta.get("title") == "Public Foreign Item"
    assert len(seen_requests) == 1
    req_url, req_headers = seen_requests[0]
    assert req_url == "http://jf-foreign.lan:8096/Items/foreign-item-1"
    assert "primary-uid-7" not in req_url
    assert "x-emby-token" not in req_headers
    assert "primary-secret-key" not in json.dumps(req_headers)


def test_jellyfin_connect_validates_url_and_clears_repointed_credentials(monkeypatch) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    _mock_dns(
        monkeypatch,
        {
            "rebind-jf.internal": "169.254.169.254",
            "old-jf.internal": "192.168.10.20",
            "new-jf.internal": "192.168.10.21",
        },
    )
    monkeypatch.setattr(jellyfin_receiver, "_start_worker", lambda: None)
    monkeypatch.setattr(jellyfin_receiver, "_ensure_control_socket", lambda: None)
    monkeypatch.setattr(jellyfin_receiver, "_stop_control_socket", lambda: None)
    monkeypatch.setattr(
        jellyfin_receiver,
        "detect_server_type",
        lambda url, **kw: {"ok": True, "server_type": "jellyfin", "server_name": "JF", "version": "10.9"},
    )

    saved_settings: dict[str, object] = {
        "jellyfin_server_url": "http://old-jf.internal:8096",
        "jellyfin_api_key": "old-api-key",
        "jellyfin_username": "alice",
        "jellyfin_password": "old-password",
        "jellyfin_user_id": "old-uid",
    }
    monkeypatch.setattr(state, "get_settings", lambda: dict(saved_settings))
    monkeypatch.setattr(
        state,
        "update_settings",
        lambda patch: saved_settings.update(patch) or dict(saved_settings),
    )

    runtime_config.set_value("RELAYTV_JELLYFIN_SERVER_URL", "http://old-jf.internal:8096")
    runtime_config.set_value("RELAYTV_JELLYFIN_API_KEY", "old-api-key")
    runtime_config.set_value("RELAYTV_JELLYFIN_USERNAME", "alice")
    runtime_config.set_value("RELAYTV_JELLYFIN_PASSWORD", "old-password")
    runtime_config.set_value("RELAYTV_JELLYFIN_USER_ID", "old-uid")

    jellyfin_receiver.connect(
        server_url="http://old-jf.internal:8096",
        api_key="old-api-key",
        auth_mode="user_login",
    )
    with jellyfin_receiver._LOCK:
        jellyfin_receiver._ACCESS_TOKEN = "old-access-token"
        jellyfin_receiver._AUTH_USER_ID = "old-uid"

    client = TestClient(create_app(testing=True))

    # Forbidden literal, non-http scheme, credentials in URL, and DNS-rebound metadata IP -> 400.
    for bad_url in (
        "http://169.254.169.254:8096",
        "http://0.0.0.0:8096",
        "ftp://new-jf.internal:8096",
        "http://user:pass@new-jf.internal:8096",
        "http://rebind-jf.internal:8096",
    ):
        bad_resp = client.post("/integrations/jellyfin/connect", json={"server_url": bad_url})
        assert bad_resp.status_code == 400, (bad_url, bad_resp.text)

    # Direct receiver connect also rejects forbidden/rebound addresses with ValueError.
    with pytest.raises(ValueError, match="forbidden"):
        jellyfin_receiver.connect(server_url="http://rebind-jf.internal:8096")

    # Repointing to a new server origin without supplying fresh credentials clears stored secrets.
    ok_resp = client.post(
        "/integrations/jellyfin/connect",
        json={"server_url": "http://new-jf.internal:8096"},
    )
    assert ok_resp.status_code == 200, ok_resp.text
    snap = runtime_config.snapshot()
    assert snap.raw("RELAYTV_JELLYFIN_USERNAME") == ""
    assert snap.raw("RELAYTV_JELLYFIN_PASSWORD") == ""
    assert snap.raw("RELAYTV_JELLYFIN_USER_ID") == ""
    assert snap.raw("RELAYTV_JELLYFIN_API_KEY") == ""
    assert saved_settings["jellyfin_username"] == ""
    assert saved_settings["jellyfin_password"] == ""
    assert saved_settings["jellyfin_api_key"] == ""
    with jellyfin_receiver._LOCK:
        assert jellyfin_receiver._API_KEY == ""
        assert jellyfin_receiver._ACCESS_TOKEN == ""
        assert jellyfin_receiver._AUTH_USER_ID == ""
        assert jellyfin_receiver._AUTH_USERNAME == ""
        assert jellyfin_receiver._AUTH_PASSWORD == ""
    jellyfin_receiver.disconnect()


def test_settings_repointing_without_credentials_disconnects_and_blocks_dns_rebinding(
    monkeypatch,
) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    _mock_dns(
        monkeypatch,
        {
            "rebind-settings.internal": "169.254.169.254",
            "jf-one.internal": "192.168.10.30",
            "jf-two.internal": "192.168.10.31",
        },
    )

    store_data: dict[str, object] = {
        "jellyfin_enabled": True,
        "jellyfin_server_url": "https://jf-one.internal",
        "jellyfin_auth_mode": "shared_api_key",
        "jellyfin_api_key": "secret-one",
        "youtube_invidious_base": "https://inv.internal",
    }
    monkeypatch.setattr(state, "get_settings", lambda: dict(store_data))
    monkeypatch.setattr(
        state,
        "update_settings",
        lambda patch: store_data.update(patch) or dict(store_data),
    )

    disconnect_calls: list[bool] = []
    monkeypatch.setattr(
        jellyfin_receiver,
        "disconnect",
        lambda: disconnect_calls.append(True) or {"ok": True, "enabled": False},
    )
    monkeypatch.setattr(jellyfin_receiver, "mark_error", lambda _err: None)

    client = TestClient(create_app(testing=True))

    # DNS-rebound Jellyfin server URL rejected with 400.
    rebind_resp = client.post(
        "/settings",
        json={"jellyfin_server_url": "http://rebind-settings.internal:8096"},
    )
    assert rebind_resp.status_code == 400

    # DNS-rebound youtube_invidious_base normalized to empty string.
    inv_resp = client.post(
        "/settings",
        json={"youtube_invidious_base": "http://rebind-settings.internal"},
    )
    assert inv_resp.status_code == 200
    assert store_data.get("youtube_invidious_base") == ""

    # Repointing jellyfin_server_url without supplying new credentials clears old api_key
    # AND immediately disconnects the receiver + websocket from the old server.
    repoint_resp = client.post(
        "/settings",
        json={"jellyfin_server_url": "https://jf-two.internal"},
    )
    assert repoint_resp.status_code == 200
    assert store_data["jellyfin_api_key"] == ""
    assert disconnect_calls == [True]


def test_jellyfin_receiver_safe_opener_blocks_cross_origin_redirect_and_reauths_on_401(
    monkeypatch,
) -> None:
    attacker_hits: list[dict[str, str]] = []
    auth_calls = {"count": 0}
    progress_tokens_seen: list[str] = []

    class _AttackerHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            attacker_hits.append({k.lower(): v for k, v in self.headers.items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"stolen": true}')

        def log_message(self, *_args):
            return

    with _local_http_server(_AttackerHandler) as (attacker_base, _):

        class _PrimaryHandler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path == "/System/Info/Public":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ProductName":"Jellyfin Server","Version":"10.9.0"}')
                    return
                if self.path == "/Items/redirect-cross":
                    self.send_response(302)
                    self.send_header("Location", f"{attacker_base}/steal")
                    self.end_headers()
                    return
                self.send_response(404)
                self.end_headers()

            def do_POST(self):  # noqa: N802
                if self.path == "/Users/AuthenticateByName":
                    length = int(self.headers.get("Content-Length") or "0")
                    _ = self.rfile.read(length)
                    auth_calls["count"] += 1
                    tok = f"access-tok-{auth_calls['count']}"
                    body = json.dumps(
                        {
                            "AccessToken": tok,
                            "User": {"Id": "uid-1"},
                            "SessionInfo": {"Id": f"sess-{auth_calls['count']}"},
                        }
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path in {"/Sessions/Playing", "/Sessions/Playing/Progress"}:
                    length = int(self.headers.get("Content-Length") or "0")
                    _ = self.rfile.read(length)
                    tok = str(self.headers.get("X-Emby-Token") or "")
                    progress_tokens_seen.append(tok)
                    if tok == "access-tok-1":
                        self.send_response(401)
                        self.end_headers()
                        self.wfile.write(b"expired token")
                        return
                    self.send_response(204)
                    self.end_headers()
                    return
                self.send_response(404)
                self.end_headers()

            def log_message(self, *_args):
                return

        with _local_http_server(_PrimaryHandler) as (primary_base, _):
            # 1) Cross-origin redirect must be rejected before Authorization/X-Emby-Token reaches attacker.
            with pytest.raises(urlerror.HTTPError) as redir_exc:
                jellyfin_receiver._get_json(
                    f"{primary_base}/Items/redirect-cross",
                    timeout=3.0,
                    token="jf-secret-token",
                )
            assert redir_exc.value.code == 302
            assert attacker_hits == []

            # Also verify https -> http scheme downgrade on same host is rejected.
            downgrade_handler = jellyfin_receiver._SameOriginJellyfinRedirectHandler()
            https_req = urlrequest.Request("https://jf.example/Items/1")
            with pytest.raises(urlerror.HTTPError):
                downgrade_handler.redirect_request(
                    https_req, None, 302, "Found", {}, "http://jf.example/Items/1"
                )

            # 2) HTTP 401 in user_login mode clears _ACCESS_TOKEN and marks authenticated=False
            #    so _ensure_authentication() re-authenticates instead of wedging permanently.
            monkeypatch.setattr(jellyfin_receiver, "_start_worker", lambda: None)
            monkeypatch.setattr(jellyfin_receiver, "_ensure_control_socket", lambda: None)
            monkeypatch.setattr(jellyfin_receiver, "_stop_control_socket", lambda: None)
            runtime_config.set_value("RELAYTV_JELLYFIN_AUTH_ENABLED", "1")
            runtime_config.set_value("RELAYTV_JELLYFIN_SERVER_URL", primary_base)
            runtime_config.set_value("RELAYTV_JELLYFIN_USERNAME", "mark")
            runtime_config.set_value("RELAYTV_JELLYFIN_PASSWORD", "hunter2")

            try:
                jellyfin_receiver.connect(
                    server_url=primary_base,
                    api_key="",
                    auth_mode="user_login",
                )
                jellyfin_receiver._ensure_authentication()
                assert jellyfin_receiver.status()["authenticated"] is True
                assert jellyfin_receiver.control_token() == "access-tok-1"

                first_prog = jellyfin_receiver.send_playback_start_once(
                    {"ItemId": "item-1", "PositionTicks": 10_000_000}
                )
                assert first_prog["ok"] is False
                assert jellyfin_receiver.status()["authenticated"] is False
                assert jellyfin_receiver.control_token() == ""

                # Next tick re-authenticates and succeeds with access-tok-2.
                jellyfin_receiver._ensure_authentication()
                assert jellyfin_receiver.status()["authenticated"] is True
                assert jellyfin_receiver.control_token() == "access-tok-2"

                second_prog = jellyfin_receiver.send_playback_start_once(
                    {"ItemId": "item-1", "PositionTicks": 20_000_000}
                )
                assert second_prog["ok"] is True
                assert progress_tokens_seen == ["access-tok-1", "access-tok-2"]
            finally:
                jellyfin_receiver.disconnect()


# ===========================================================================
# Fix 2: Outbound SSRF Hardening in IPTV, Browse, Plex, Seerr
# ===========================================================================


def test_iptv_ssrf_blocks_forbidden_with_token_cgnat_without_token_and_relative_redirects(
    monkeypatch,
) -> None:
    _mock_dns(
        monkeypatch,
        {
            "meta-rebind.internal": "169.254.169.254",
            "lan-box.internal": "192.168.1.50",
            "tailscale-peer.internal": "100.100.100.200",
            "cgnat.internal": "100.64.0.1",
            "v6-mapped-meta.internal": "::ffff:169.254.169.254",
            "nat64-meta.internal": "64:ff9b::a9fe:a9fe",
            "sixtofour-private.internal": "2002:0a00:0001::1",
            "public-iptv.example": "93.184.216.34",
        },
    )

    # 1) Even WITH RELAYTV_API_TOKEN set, non-http(s), metadata, 0.0.0.0, and DNS-rebound forbidden IPs are blocked.
    monkeypatch.setenv("RELAYTV_API_TOKEN", "operator-secret-token")
    runtime_config.refresh_from_env()
    for blocked in (
        "ftp://public-iptv.example/list.m3u",
        "file:///etc/passwd",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/list.m3u",
        "http://[fe80::1]/list.m3u",
        "http://meta-rebind.internal/list.m3u",
    ):
        with pytest.raises(ValueError):
            iptv_service._assert_fetch_target_allowed(blocked)

    # Operator with API token CAN reach legitimate private LAN host.
    iptv_service._assert_fetch_target_allowed("http://lan-box.internal/list.m3u")

    # 2) Without RELAYTV_API_TOKEN, LAN, CGNAT (100.64.0.0/10), NAT64, 6to4, and IPv4-mapped non-public IPs are blocked.
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    for non_public in (
        "http://lan-box.internal/list.m3u",
        "http://tailscale-peer.internal/list.m3u",
        "http://cgnat.internal/list.m3u",
        "http://v6-mapped-meta.internal/list.m3u",
        "http://nat64-meta.internal/list.m3u",
        "http://sixtofour-private.internal/list.m3u",
    ):
        with pytest.raises(ValueError):
            iptv_service._assert_fetch_target_allowed(non_public)
    iptv_service._assert_fetch_target_allowed("https://public-iptv.example/list.m3u")

    # 3) _PublicOnlyRedirectHandler resolves relative redirects via urljoin before checking target.
    monkeypatch.setenv("RELAYTV_API_TOKEN", "operator-secret-token")
    runtime_config.refresh_from_env()

    class _RedirectPlaylistHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/rel-safe":
                self.send_response(302)
                self.send_header("Location", "/final.m3u")
                self.end_headers()
                return
            if self.path == "/rel-ssrf":
                self.send_response(302)
                self.send_header("Location", "//169.254.169.254/latest/meta-data/")
                self.end_headers()
                return
            if self.path == "/final.m3u":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"#EXTM3U\n#EXTINF:-1,Ch 1\nhttp://93.184.216.34/live.m3u8\n")
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_args):
            return

    with _local_http_server(_RedirectPlaylistHandler) as (base_url, _):
        body, _, _, not_mod, _ = iptv_service._fetch_source({"location": f"{base_url}/rel-safe"})
        assert not_mod is False
        assert "#EXTM3U" in body

        with pytest.raises((HTTPException, ValueError)):
            iptv_service._fetch_source({"location": f"{base_url}/rel-ssrf"})


def test_iptv_m3u_sanitizes_logos_drops_forbidden_streams_and_gates_mutating_routes(
    tmp_path, monkeypatch
) -> None:
    _mock_dns(
        monkeypatch,
        {
            "rebind-stream.internal": "169.254.169.254",
            "cdn.internal": "93.184.216.34",
        },
    )
    m3u_text = "\n".join(
        [
            "#EXTM3U",
            '#EXTINF:-1 tvg-logo="javascript:alert(1)",JS Logo Valid Stream',
            "https://cdn.internal/live/one.m3u8",
            '#EXTINF:-1 tvg-logo="http://169.254.169.254/secret.png",Meta Logo Valid Stream',
            "https://cdn.internal/live/two.m3u8",
            '#EXTINF:-1 tvg-logo="https://cdn.internal/logo.png",Clean Channel',
            "https://cdn.internal/live/three.m3u8",
            "#EXTINF:-1,Metadata Stream Blocked",
            "http://169.254.169.254/latest/meta-data/",
            "#EXTINF:-1,Unspecified IP Stream Blocked",
            "http://0.0.0.0:8080/live.ts",
            "#EXTINF:-1,DNS Rebound Stream Blocked",
            "http://rebind-stream.internal/live.m3u8",
        ]
    )
    parsed = iptv_service.parse_m3u(m3u_text)
    assert [ch["name"] for ch in parsed] == [
        "JS Logo Valid Stream",
        "Meta Logo Valid Stream",
        "Clean Channel",
    ]
    assert parsed[0]["logo_url"] == ""
    assert parsed[1]["logo_url"] == ""
    assert parsed[2]["logo_url"] == "https://cdn.internal/logo.png"

    # Stored channel with a forbidden stream_url is rejected by _playable_item / channel_action.
    db_path = str(tmp_path / "iptv.db")
    monkeypatch.setenv("RELAYTV_IPTV_DB_PATH", db_path)
    monkeypatch.setenv("RELAYTV_IPTV_ENABLED", "1")
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    store = IptvStore(db_path)
    monkeypatch.setattr(iptv_service, "store", lambda: store)
    monkeypatch.setattr(
        store,
        "get_channel",
        lambda sid, cid: {
            "id": cid,
            "source_id": sid,
            "name": "Bad Stream",
            "group_title": "News",
            "logo_url": "",
            "stream_url": "http://169.254.169.254/live.m3u8",
            "user_agent": "",
            "referrer": "",
            "active": True,
        },
    )
    with pytest.raises(HTTPException) as play_exc:
        iptv_service.channel_action(source_id="src-test", channel_id="ch-forbidden", command="play_now")
    assert play_exc.value.status_code == 400

    # When IPTV is disabled, all mutating/network IPTV routes return 503.
    monkeypatch.setenv("RELAYTV_IPTV_ENABLED", "0")
    runtime_config.refresh_from_env()
    client = TestClient(create_app(testing=True))
    gated_calls = [
        ("POST", "/iptv/directory/iptv-org-us/add", None),
        ("POST", "/iptv/sources", {"name": "S", "location": "https://cdn.example/a.m3u"}),
        ("PATCH", "/iptv/sources/src-test", {"name": "Renamed"}),
        ("DELETE", "/iptv/sources/src-test", None),
        ("POST", "/iptv/sources/src-test/refresh", None),
        ("PATCH", "/iptv/channels/ch-1", {"source_id": "src-test", "hidden": True}),
        ("POST", "/iptv/channels/visibility", {"source_id": "src-test", "group": "News", "hidden": True}),
        ("POST", "/iptv/channels/reorder", {"source_id": "src-test", "channel_id": "ch-1", "direction": "up"}),
        ("POST", "/iptv/channels/remove-unavailable", {"source_id": "src-test"}),
    ]
    for method, path, payload in gated_calls:
        resp = client.request(method, path, json=payload) if payload is not None else client.request(method, path)
        assert resp.status_code == 503, (method, path, resp.status_code, resp.text)


def test_browse_plex_and_seerr_reject_dns_rebinding_and_cross_origin_redirects(
    monkeypatch,
) -> None:
    _mock_dns(
        monkeypatch,
        {
            "rebind-ssrf.internal": "169.254.169.254",
            "valid-lan.internal": "192.168.1.99",
        },
    )

    with pytest.raises(ValueError):
        browse_service._normalize_custom_server_url_and_origin("http://rebind-ssrf.internal:8096")
    with pytest.raises(ValueError):
        plex_client.normalize_server_url("http://rebind-ssrf.internal:32400")
    with pytest.raises(ValueError):
        seerr_client.normalize_server_url("http://rebind-ssrf.internal:5055")

    # plex_auth._connection_candidates filters out metadata, unspecified, link-local IPv6, and DNS-rebound URIs.
    candidates = plex_auth.PlexAuthManager._connection_candidates(
        {
            "connections": [
                {"uri": "http://169.254.169.254:32400", "local": True, "relay": False},
                {"uri": "http://0.0.0.0:32400", "local": True, "relay": False},
                {"uri": "http://[fe80::1]:32400", "local": True, "relay": False},
                {"uri": "http://rebind-ssrf.internal:32400", "local": True, "relay": False},
                {"uri": "https://valid-lan.internal:32400", "local": True, "relay": False},
            ]
        }
    )
    assert [c["uri"] for c in candidates] == ["https://valid-lan.internal:32400"]

    # Real HTTP redirect check across browse_service, PlexClient, and SeerrClient:
    # cross-origin redirect never forwards secret tokens to the second server.
    leaked_requests: list[tuple[str, dict[str, str]]] = []

    class _LeakTarget(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            leaked_requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_args):
            return

    with _local_http_server(_LeakTarget) as (leak_base, _):

        class _RedirectOrigin(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(302)
                self.send_header("Location", f"{leak_base}/capture")
                self.end_headers()

            def log_message(self, *_args):
                return

        with _local_http_server(_RedirectOrigin) as (origin_base, _):
            jf_req = urlrequest.Request(
                f"{origin_base}/Items",
                headers={"X-Emby-Token": "custom-jf-secret"},
            )
            with pytest.raises(urlerror.HTTPError):
                browse_service._open_custom_jf_request(jf_req, timeout=2.0)

            pclient = PlexClient(
                origin_base,
                token="plex-secret-tok",
                identity=PlexClientIdentity("cid-1", device_name="RelayTV"),
            )
            with pytest.raises(PlexError):
                pclient.get("/library/sections")

            sclient = SeerrClient(
                seerr_client.SeerrConfig(
                    enabled=True,
                    server_url=origin_base,
                    api_key="seerr-secret-key",
                    shared_requests_enabled=True,
                    request_user_id=None,
                    request_mode="shared_admin",
                )
            )
            with pytest.raises(SeerrError):
                sclient.get("/status")

            assert leaked_requests == []


# ===========================================================================
# Fix 3: Custom Browse Isolation, Custom Plex Versions, Bounded starts_with,
#        Non-ASCII Headers, Jellyfin Track/Transcode/Duration/WS, & /previous
# ===========================================================================


def test_custom_jellyfin_browse_isolation_from_primary_snapshots_and_queue_retarget(
    monkeypatch,
) -> None:
    custom_srv = {
        "id": "srv_custom_emby",
        "name": "Friend Emby",
        "type": "emby",
        "origin": "custom",
        "enabled": True,
        "server_url": "http://192.168.50.10:8096",
        "api_key": "friend-api-key",
    }
    monkeypatch.setattr(
        browse_service,
        "_collect_configured_servers",
        lambda **kw: [custom_srv],
    )
    monkeypatch.setattr(
        browse_service,
        "get_unified_item_detail",
        lambda iid, **kw: {
            "item_id": iid,
            "title": "Indie Film",
            "type": "movie",
            "poster": "/browse/artwork/srv_custom_emby?path=%2FItems%2Fm1%2FImages%2FPrimary",
        },
    )

    queued_items: list[dict[str, object]] = []
    monkeypatch.setattr(
        playback_service,
        "queue_item",
        lambda entry: queued_items.append(dict(entry)) or (1, [dict(entry)]),
    )

    res = browse_service.perform_unified_action(
        browse_service.encode_item_id("srv_custom_emby", "mov-77"),
        "play_last",
    )
    assert res["ok"] is True
    assert len(queued_items) == 1
    custom_item = queued_items[0]
    assert custom_item["browse_server_id"] == "srv_custom_emby"
    assert custom_item["browse_server_type"] == "emby"
    assert "friend-api-key" in str(custom_item["url"])

    # Primary Jellyfin must NOT claim custom_item for progress, stopped reports, or queue retargeting.
    monkeypatch.setattr(
        jellyfin_receiver,
        "status",
        lambda: {"enabled": True, "running": True, "server_url": "http://192.168.1.10:8096"},
    )
    monkeypatch.setattr(jellyfin_service, "catalog_token", lambda: "primary-jf-secret")
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(player, "mpv_get", lambda prop: 42.0 if prop == "time-pos" else 120.0)
    monkeypatch.setattr(state, "NOW_PLAYING", dict(custom_item), raising=False)

    assert jellyfin_service.progress_snapshot() is None
    assert jellyfin_service.stopped_snapshot_from_now(custom_item, position_sec=42.0) is None

    monkeypatch.setattr(state, "QUEUE", state._RevisionedQueue([dict(custom_item)]))
    assert jellyfin_service.retarget_queue_stream_preferences() == 0
    assert "primary-jf-secret" not in str(state.QUEUE[0]["url"])


def test_custom_plex_versions_stream_selection_bounded_starts_with_and_safe_browse_502(
    monkeypatch,
) -> None:
    custom_plex_srv = {
        "id": "srv_plex_nas",
        "name": "NAS Plex",
        "type": "plex",
        "origin": "custom",
        "enabled": True,
        "server_url": "http://192.168.50.20:32400",
        "api_key": "nas-plex-secret",
    }
    raw_metadata = {
        "ratingKey": "555",
        "key": "/library/metadata/555",
        "type": "movie",
        "title": "The Blade Runner",
        "titleSort": "Blade Runner",
        "year": 1982,
        "duration": 7_020_000,
        "Media": [
            {
                "id": "1001",
                "videoResolution": "4k",
                "container": "mkv",
                "videoCodec": "hevc",
                "audioCodec": "truehd",
                "width": 3840,
                "height": 2160,
                "Part": [
                    {
                        "id": "2001",
                        "key": "/library/parts/2001/111/movie-4k.mkv",
                        "container": "mkv",
                        "Stream": [
                            {"id": "301", "streamType": 2, "languageCode": "eng", "codec": "truehd", "default": 1},
                        ],
                    }
                ],
            },
            {
                "id": "1002",
                "videoResolution": "1080",
                "container": "mp4",
                "videoCodec": "h264",
                "audioCodec": "aac",
                "width": 1920,
                "height": 1080,
                "Part": [
                    {
                        "id": "2002",
                        "key": "/library/parts/2002/222/movie-1080p.mp4",
                        "container": "mp4",
                        "Stream": [
                            {"id": "401", "streamType": 2, "languageCode": "eng", "codec": "aac", "default": 1},
                            {"id": "402", "streamType": 2, "languageCode": "jpn", "codec": "aac"},
                            {"id": "501", "streamType": 3, "languageCode": "eng", "codec": "srt"},
                        ],
                    }
                ],
            },
        ],
    }

    put_calls: list[tuple[str, dict[str, object]]] = []
    section_page_calls = {"count": 0}

    class _FakeCustomPlexClient:
        def get(self, path: str, *, query=None, auth: bool = True):
            if path == "/library/metadata/555":
                return {"MediaContainer": {"Metadata": [raw_metadata]}}
            if path == "/library/sections":
                return {"MediaContainer": {"Directory": [{"key": "1", "type": "movie"}]}}
            if path == "/library/sections/1/all":
                section_page_calls["count"] += 1
                start = int((query or {}).get("X-Plex-Container-Start") or 0)
                # Simulate an enormous 50,000-item catalog sorted past 'B' on page 2.
                if start == 0:
                    recs = [raw_metadata] * 100
                else:
                    recs = [
                        {
                            "ratingKey": str(start + i),
                            "key": f"/library/metadata/{start + i}",
                            "type": "movie",
                            "title": f"Charlie {i}",
                            "titleSort": f"Charlie {i}",
                        }
                        for i in range(100)
                    ]
                return {"MediaContainer": {"totalSize": 50_000, "Metadata": recs}}
            raise AssertionError(path)

        def request_no_content(self, method: str, path: str, *, query=None):
            put_calls.append((f"{method} {path}", dict(query or {})))

    monkeypatch.setattr(browse_service, "_collect_configured_servers", lambda **kw: [custom_plex_srv])
    monkeypatch.setattr(browse_service, "_custom_plex_client", lambda _srv: _FakeCustomPlexClient())

    # 1) Detail exposes versions & tracks; action + resolve_custom_plex_playback_item honor version_id & stream IDs.
    encoded_id = browse_service.encode_item_id("srv_plex_nas", "/library/metadata/555")
    detail = browse_service.get_unified_item_detail(encoded_id, refresh=True)
    assert detail is not None
    versions = detail["versions"]
    assert len(versions) == 2
    v2_id = versions[1]["id"]
    assert [t["id"] for t in versions[1]["audio_tracks"]] == ["401", "402"]
    assert [t["id"] for t in versions[1]["subtitle_tracks"]] == ["501"]

    played_entries: list[dict[str, object]] = []
    monkeypatch.setattr(
        playback_service,
        "play_now",
        lambda entry, **kw: played_entries.append(dict(entry)) or dict(entry),
    )
    browse_service.perform_unified_action(
        encoded_id,
        "play_now",
        version_id=v2_id,
        audio_id="402",
        subtitle_id="501",
    )
    assert len(played_entries) == 1
    durable = played_entries[0]
    assert durable["plex_part_id"] == v2_id
    assert durable["plex_audio_id"] == "402"
    assert durable["plex_subtitle_id"] == "501"

    resolved = browse_service.resolve_custom_plex_playback_item(durable)
    assert "movie-1080p.mp4" in parse_qs(urlsplit(str(resolved["url"])).query)["path"][0]
    assert resolved["plex_container"] == "mp4"
    assert resolved["audio_language"] == "jpn"
    assert resolved["subtitle_language"] == "eng"
    assert put_calls == [
        ("PUT /library/parts/2002", {"allParts": 1, "audioStreamID": "402", "subtitleStreamID": "501"})
    ]

    # 2) Bounded Plex starts_with pagination stops early instead of scanning all 50,000 items,
    #    and matches "The Blade Runner" under letter 'B' via sort title.
    catalog_res = browse_service.list_unified_movies(
        starts_with="B",
        sort="title_asc",
        refresh=True,
    )
    assert section_page_calls["count"] == 2
    assert len(catalog_res["items"]) >= 1
    assert catalog_res["items"][0]["title"] == "The Blade Runner"

    # 3) routes/browse.py returns generic 502 messages without leaking raw exception secrets.
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    monkeypatch.setattr(
        browse_service,
        "get_unified_item_detail",
        lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("upstream crash url=http://jf.lan/Items?api_key=leak-secret-999")
        ),
    )
    monkeypatch.setattr(
        browse_service,
        "perform_unified_action",
        lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("action crash token=leak-secret-999")
        ),
    )
    client = TestClient(create_app(testing=True))
    det_resp = client.get(f"/browse/item/{encoded_id}")
    assert det_resp.status_code == 502
    assert det_resp.json()["detail"] == "Failed to load media item details"
    assert "leak-secret-999" not in det_resp.text

    act_resp = client.post(
        "/browse/action",
        json={"item_id": encoded_id, "command": "play_now"},
    )
    assert act_resp.status_code == 502
    assert act_resp.json()["detail"] == "Failed to execute media action"
    assert "leak-secret-999" not in act_resp.text


def test_non_ascii_and_quoted_device_name_headers_succeed_over_real_http(
    monkeypatch,
) -> None:
    captured_headers: list[dict[str, str]] = []

    class _HeaderEchoHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            captured_headers.append({k: v for k, v in self.headers.items()})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"MediaContainer": {}}')

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or "0")
            _ = self.rfile.read(length)
            captured_headers.append({k: v for k, v in self.headers.items()})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                b'{"AccessToken":"tok-utf8","User":{"Id":"u1"},"SessionInfo":{"Id":"s1"}}'
            )

        def log_message(self, *_args):
            return

    with _local_http_server(_HeaderEchoHandler) as (base_url, _):
        # 1) PlexClient with curly apostrophe, quotes, emoji, and pure CJK never raises UnicodeEncodeError.
        for raw_name, expected_name in (
            ("Mark\u2019s Living Room \U0001f4fa", "Mark's Living Room"),
            ("\u30ea\u30d3\u30f3\u30b0\u30c6\u30ec\u30d3", "RelayTV"),
        ):
            pclient = PlexClient(
                base_url,
                token="plex-tok",
                identity=PlexClientIdentity("cid-utf8", device_name=raw_name),
            )
            pclient.get("/library/sections")
            assert captured_headers[-1]["X-Plex-Device-Name"] == expected_name

        # 2) Jellyfin _get_json and authenticate_once escape quotes/backslashes and percent-encode UTF-8,
        #    while keeping ordinary ASCII spaces intact.
        living_headers = jellyfin_receiver._emby_identity_headers(
            token="tok-1",
            client_name="RelayTV",
            device_name="Living Room",
            device_id="dev-1",
            client_version="1.0",
        )
        assert 'Device="Living Room"' in living_headers["Authorization"]

        tricky_name = 'Mark\u2019s "Den" \\ TV \U0001f4fa'
        monkeypatch.setattr(jellyfin_receiver, "_start_worker", lambda: None)
        monkeypatch.setattr(jellyfin_receiver, "_ensure_control_socket", lambda: None)
        monkeypatch.setattr(jellyfin_receiver, "_stop_control_socket", lambda: None)
        runtime_config.set_value("RELAYTV_JELLYFIN_SERVER_URL", base_url)
        runtime_config.set_value("RELAYTV_JELLYFIN_USERNAME", "mark")
        runtime_config.set_value("RELAYTV_JELLYFIN_PASSWORD", "pass")
        try:
            jellyfin_receiver.connect(
                server_url=base_url,
                api_key="",
                auth_mode="user_login",
                device_name=tricky_name,
            )
            auth_out = jellyfin_receiver.authenticate_once()
            assert auth_out["ok"] is True
            sent_auth = captured_headers[-1]["Authorization"]
            # Must contain exactly 8 quotes across the 4 key="value" pairs (no quote breakout).
            assert sent_auth.count('"') == 8
            assert "%22Den%22" in sent_auth
            assert "%5C" in sent_auth
            ws_headers = jellyfin_receiver.control_socket_identity_headers()
            assert ws_headers["Authorization"].encode("latin-1")
            assert "%22Den%22" in ws_headers["Authorization"]
        finally:
            jellyfin_receiver.disconnect()


def test_jellyfin_duration_sec_audio_switch_redaction_transcode_nocache_and_ws_bound(
    monkeypatch,
) -> None:
    primary_url = "http://192.168.1.10:8096"
    monkeypatch.setattr(
        jellyfin_receiver,
        "status",
        lambda: {"enabled": True, "running": True, "server_url": primary_url, "device_id": "dev-1"},
    )

    # 1) stopped_snapshot_from_now & progress_snapshot read duration_sec when duration is absent.
    now_item = {
        "url": f"{primary_url}/Videos/item-dur/master.m3u8?PlaySessionId=ps-99",
        "provider": "jellyfin",
        "jellyfin_item_id": "item-dur",
        "duration_sec": 100.0,
    }
    stopped = jellyfin_service.stopped_snapshot_from_now(now_item, position_sec=98.0)
    assert stopped is not None
    assert stopped["RunTimeTicks"] == 100 * 10_000_000
    assert stopped["PositionTicks"] == 100 * 10_000_000
    assert stopped["PlayedPercentage"] == 100.0
    assert stopped["PlaySessionId"] == "ps-99"

    monkeypatch.setattr(state, "NOW_PLAYING", dict(now_item), raising=False)
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(
        player,
        "mpv_get",
        lambda prop: 25.0 if prop == "time-pos" else (False if prop == "pause" else None),
    )
    prog = jellyfin_service.progress_snapshot()
    assert prog is not None
    assert prog["RunTimeTicks"] == 100 * 10_000_000
    assert prog["PlaySessionId"] == "ps-99"

    # 2) switch_audio_track passes preferred_stream_index=requested_idx to try_set_mpv_audio_track.
    seen_preferred: list[object] = []
    monkeypatch.setattr(
        jellyfin_receiver,
        "get_item_detail",
        lambda iid, **kw: {
            "item_id": iid,
            "audio_streams": [
                {"index": 1, "language": "eng", "display": "English Stereo"},
                {"index": 3, "language": "eng", "display": "English Commentary"},
            ],
            "subtitle_streams": [],
        },
    )
    monkeypatch.setattr(
        jellyfin_service,
        "try_set_mpv_audio_track",
        lambda *, language="", display="", preferred_stream_index=None: seen_preferred.append(
            preferred_stream_index
        )
        or True,
    )
    monkeypatch.setattr(playback_service, "update_now_playing", lambda updated: None)
    monkeypatch.setattr(jellyfin_receiver, "send_progress_once", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr(state, "update_settings", lambda patch: dict(patch))

    sw_res = jellyfin_service.switch_audio_track(3, server_status={"server_url": primary_url})
    assert sw_res["ok"] is True
    assert seen_preferred == [3]

    # 3) handle_command redacts secrets in 500 HTTPException detail.
    monkeypatch.setattr(jellyfin_receiver, "mark_heartbeat", lambda: None)
    monkeypatch.setattr(jellyfin_receiver, "mark_error", lambda _msg: None)
    with pytest.raises(HTTPException) as cmd_exc:
        jellyfin_service.handle_command(
            SimpleNamespace(action="stop", url="", command_id="cmd-err-1", payload={}),
            controls={
                "stop": lambda: (_ for _ in ()).throw(
                    RuntimeError("mpv failed on http://jf.lan/stream?api_key=top-secret-jf-key")
                )
            },
            ui={},
        )
    assert cmd_exc.value.status_code == 500
    assert "top-secret-jf-key" not in str(cmd_exc.value.detail)
    assert "<redacted>" in str(cmd_exc.value.detail)

    # 4) resolve_playback_url matches mid, attaches PlaySessionId, and NEVER caches transcode URLs.
    jellyfin_receiver.clear_catalog_cache()
    playback_info_calls = {"count": 0}

    class _PbResp:
        def __init__(self, payload: dict):
            self._raw = json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self._raw

    def _fake_pb_urlopen(req, timeout=5.0):
        playback_info_calls["count"] += 1
        n = playback_info_calls["count"]
        return _PbResp(
            {
                "PlaySessionId": f"ps-live-{n}",
                "MediaSources": [
                    {
                        "Id": "ms-first",
                        "TranscodingUrl": "/Videos/item-x/master.m3u8?MediaSourceId=ms-first&api_key=tok",
                    },
                    {
                        "Id": "ms-second",
                        "TranscodingUrl": "/Videos/item-x/master.m3u8?MediaSourceId=ms-second&api_key=tok",
                    },
                ],
            }
        )

    monkeypatch.setattr(
        jellyfin_receiver,
        "_catalog_base_token_user",
        lambda: (primary_url, "tok", "uid-1"),
    )
    monkeypatch.setattr(jellyfin_receiver._urlrequest, "urlopen", _fake_pb_urlopen)

    first_pb = jellyfin_receiver.resolve_playback_url(
        "item-x",
        prefer_transcode=True,
        media_source_id="ms-second",
    )
    second_pb = jellyfin_receiver.resolve_playback_url(
        "item-x",
        prefer_transcode=True,
        media_source_id="ms-second",
    )
    assert playback_info_calls["count"] == 2
    assert first_pb["media_source_id"] == "ms-second"
    assert "MediaSourceId=ms-second" in str(first_pb["url"])
    assert first_pb["play_session_id"] == "ps-live-1"
    assert "PlaySessionId=ps-live-1" in str(first_pb["url"])
    assert second_pb["play_session_id"] == "ps-live-2"
    assert "PlaySessionId=ps-live-2" in str(second_pb["url"])

    # 5) jellyfin_ws._connect_once aborts if identity drifted and dials using session.bound when valid.
    dialed: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(jellyfin_receiver, "control_token", lambda: "ws-tok-1")
    monkeypatch.setattr(jellyfin_receiver, "command_sink_registered", lambda: True)
    fp = jellyfin_ws._token_fingerprint("ws-tok-1")

    drifted_session = jellyfin_ws._Session(bound=("http://old-jf.lan:8096", "dev-1", fp))
    monkeypatch.setattr(
        jellyfin_ws,
        "_ws_connect",
        lambda url, **kw: dialed.append((url, dict(kw.get("additional_headers") or {}))),
    )
    jellyfin_ws._connect_once(drifted_session)
    assert dialed == []

    valid_session = jellyfin_ws._Session(bound=(primary_url, "dev-1", fp))
    valid_session.stop.set()  # Close cleanly immediately after connect handshake
    monkeypatch.setattr(
        jellyfin_ws,
        "_ws_connect",
        lambda url, **kw: dialed.append((url, dict(kw.get("additional_headers") or {})))
        or SimpleNamespace(close=lambda: None),
    )
    jellyfin_ws._connect_once(valid_session)
    assert len(dialed) == 1
    assert dialed[0][0].startswith("ws://192.168.1.10:8096/socket?")


def test_playback_previous_plays_iptv_and_plex_items_and_restores_history_on_failure(
    monkeypatch,
) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    monkeypatch.setattr(state, "persist_history", lambda: None)
    monkeypatch.setattr(player, "is_playing", lambda: False)

    play_now_calls: list[tuple[object, dict[str, object]]] = []

    def _fake_play_now(item, **kw):
        play_now_calls.append((item, dict(kw)))
        return dict(item) if isinstance(item, dict) else {"url": str(item)}

    monkeypatch.setattr(playback_service, "play_now", _fake_play_now)

    client = TestClient(create_app(testing=True))

    # 1) IPTV history item: resolves via iptv_service.resolve_queue_item with use_resolver=False,
    #    and restores history entry if resolution fails.
    iptv_hist_entry = {
        "url": "https://iptv.invalid/src-live/ch-news",
        "title": "World News HD",
        "provider": "iptv",
        "iptv_source_id": "src-live",
        "iptv_channel_id": "ch-news",
    }
    monkeypatch.setattr(
        state,
        "NOW_PLAYING",
        {"url": "https://video.example/current.mp4", "provider": "direct"},
        raising=False,
    )
    monkeypatch.setattr(state, "HISTORY", [dict(iptv_hist_entry)], raising=False)
    monkeypatch.setattr(
        iptv_service,
        "resolve_queue_item",
        lambda _it: (_ for _ in ()).throw(HTTPException(status_code=404, detail="channel gone")),
    )
    fail_iptv = client.post("/previous")
    assert fail_iptv.status_code == 404
    assert len(state.HISTORY) == 1
    assert state.HISTORY[0]["iptv_channel_id"] == "ch-news"

    monkeypatch.setattr(
        iptv_service,
        "resolve_queue_item",
        lambda it: {
            **it,
            "url": "https://cdn.example/live/news.m3u8?token=ephemeral",
            "title": "World News HD",
        },
    )
    ok_iptv = client.post("/previous")
    assert ok_iptv.status_code == 200
    assert state.HISTORY == []
    assert len(play_now_calls) == 1
    played_iptv, iptv_kw = play_now_calls.pop()
    assert isinstance(played_iptv, dict)
    assert played_iptv["url"] == "https://cdn.example/live/news.m3u8?token=ephemeral"
    assert iptv_kw["use_resolver"] is False

    # 2) Plex history item: skips currently-playing Plex item even though both share
    #    url="https://plex.invalid/item", preserves plex_* fields + resume_pos, and restores on PlexError.
    cur_plex = {
        "url": "https://plex.invalid/item",
        "title": "Current Plex Episode",
        "provider": "plex",
        "plex_item_id": "plex-cur-2",
    }
    prev_plex = {
        "url": "https://plex.invalid/item",
        "title": "Previous Plex Episode",
        "provider": "plex",
        "plex_item_id": "plex-prev-1",
        "plex_part_id": "part-9",
        "plex_audio_id": "aud-2",
        "plex_subtitle_id": "sub-3",
        "resume_pos": 145.5,
    }
    monkeypatch.setattr(state, "NOW_PLAYING", dict(cur_plex), raising=False)
    monkeypatch.setattr(state, "HISTORY", [dict(cur_plex), dict(prev_plex)], raising=False)

    monkeypatch.setattr(
        playback_service,
        "play_now",
        lambda _it, **kw: (_ for _ in ()).throw(
            PlexError("plex_unavailable", "Plex server offline", status_code=502)
        ),
    )
    fail_plex = client.post("/previous")
    assert fail_plex.status_code == 502
    assert [h["plex_item_id"] for h in state.HISTORY] == ["plex-cur-2", "plex-prev-1"]

    monkeypatch.setattr(playback_service, "play_now", _fake_play_now)
    ok_plex = client.post("/previous")
    assert ok_plex.status_code == 200
    assert [h["plex_item_id"] for h in state.HISTORY] == ["plex-cur-2"]
    assert len(play_now_calls) == 1
    played_plex, plex_kw = play_now_calls.pop()
    assert isinstance(played_plex, dict)
    assert played_plex["plex_item_id"] == "plex-prev-1"
    assert played_plex["plex_part_id"] == "part-9"
    assert played_plex["plex_audio_id"] == "aud-2"
    assert played_plex["plex_subtitle_id"] == "sub-3"
    assert plex_kw["use_resolver"] is False
    assert plex_kw["start_pos"] == 145.5


def test_player_and_playback_service_isolate_custom_browse_and_foreign_jellyfin_items(
    monkeypatch,
):
    from relaytv_app import player

    # 1) _hydrate_jellyfin_resume_metadata must never send primary credentials to foreign origins
    #    and must preserve custom Emby browse items without contacting jf_primary.
    monkeypatch.setattr(
        jellyfin_receiver,
        "status",
        lambda: {"enabled": True, "server_url": "http://192.168.1.50:8096"},
    )
    monkeypatch.setattr(jellyfin_receiver, "session_token", lambda: "primary-session-secret")
    monkeypatch.setattr(jellyfin_receiver, "api_key", lambda: "primary-api-secret")

    metadata_calls: list[tuple[str, str, str]] = []

    def _spy_get_item_metadata(
        item_id: str,
        *,
        token_override: str = "",
        server_url_override: str = "",
        user_id_override: str = "",
    ):
        metadata_calls.append((item_id, token_override, server_url_override))
        return {"title": "Should Not Fetch"}

    monkeypatch.setattr(jellyfin_receiver, "get_item_metadata", _spy_get_item_metadata)

    foreign_hydrated = player._hydrate_jellyfin_resume_metadata(
        {
            "url": "https://attacker.example/Items/deadbeef12345678/stream.mp4",
            "title": "stream",
        }
    )
    assert metadata_calls == []
    assert foreign_hydrated["title"] == "stream"

    custom_emby_hydrated = player._hydrate_jellyfin_resume_metadata(
        {
            "url": "http://192.168.1.60:8096/Videos/125/stream?static=true&api_key=emby-tok",
            "title": "Custom Emby Movie",
            "provider": "emby",
            "jellyfin_item_id": "125",
            "browse_server_id": "srv_emby_custom",
            "browse_server_type": "emby",
        }
    )
    assert metadata_calls == []
    assert custom_emby_hydrated["provider"] == "emby"

    # 2) _jellyfin_stopped_payload_from_now must ignore custom browse & foreign items
    assert (
        player._jellyfin_stopped_payload_from_now(
            {
                "url": "http://192.168.1.60:8096/Videos/125/stream?static=true",
                "jellyfin_item_id": "125",
                "browse_server_id": "srv_emby_custom",
                "resume_pos": 42.0,
                "duration_sec": 100.0,
            }
        )
        is None
    )
    primary_stopped = player._jellyfin_stopped_payload_from_now(
        {
            "url": "http://192.168.1.50:8096/Videos/125/stream?static=true",
            "jellyfin_item_id": "125",
            "resume_pos": 99.0,
            "duration_sec": 100.0,
        }
    )
    assert isinstance(primary_stopped, dict)
    assert primary_stopped["PositionTicks"] == 1_000_000_000
    assert primary_stopped["RunTimeTicks"] == 1_000_000_000

    # 3) same_playback_item and state._sanitize_persisted_item preserve browse_server_id isolation
    primary_item = {"url": "http://192.168.1.50:8096/Videos/125/stream", "jellyfin_item_id": "125"}
    custom_item = {
        "url": "http://192.168.1.60:8096/Videos/125/stream",
        "jellyfin_item_id": "125",
        "browse_server_id": "srv_emby_custom",
        "browse_server_type": "emby",
    }
    assert playback_service.same_playback_item(primary_item, custom_item) is False
    assert playback_service.same_playback_item(custom_item, dict(custom_item)) is True

    persisted = state._persistable_queue_item(custom_item)
    assert persisted is not None
    assert persisted["browse_server_id"] == "srv_emby_custom"
    assert persisted["browse_server_type"] == "emby"
