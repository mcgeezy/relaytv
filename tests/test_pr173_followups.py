# SPDX-License-Identifier: GPL-3.0-only
"""Exercise PR #173's boundaries at connection and persistence time."""

import http.server
import socket
import stat
import threading
import urllib.error
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from relaytv_app import state, thumb_cache, url_boundary
from relaytv_app.main import create_app
from relaytv_app.routes import queue


@pytest.mark.parametrize("thumbnail", [False, True])
@pytest.mark.parametrize("redirect", [False, True])
def test_rebinding_cannot_connect_to_forbidden_address(monkeypatch, tmp_path, thumbnail, redirect):
    target = "rebind.attacker.invalid-tld"
    answers = []
    connections = []
    original_resolve = socket.getaddrinfo
    original_connect = socket.socket.connect

    def resolve(host, port, *args, **kwargs):
        if host != target:
            return original_resolve(host, port, *args, **kwargs)
        ip = "127.0.0.1" if not answers else "169.254.169.254"
        answers.append(ip)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 80))]

    def connect(sock, address):
        connections.append(address[0])
        if address[0] == "169.254.169.254":
            raise OSError("Review test intercepted forbidden connection")
        return original_connect(sock, address)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", f"http://{target}/image.jpg")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket.socket, "connect", connect)
    # A proxy would resolve the destination remotely, bypassing this policy.
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("no_proxy", "")
    url = (
        f"http://127.0.0.1:{server.server_port}/redirect"
        if redirect else f"http://{target}/image.jpg"
    )
    try:
        with pytest.raises(urllib.error.URLError):
            if thumbnail:
                thumb_cache._download_to(url, str(tmp_path / "image.jpg"))
            else:
                url_boundary.safe_urlopen(url, timeout=2)
        assert answers == ["127.0.0.1", "169.254.169.254"]
        assert "169.254.169.254" not in connections
        if redirect:
            assert connections == ["127.0.0.1"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_safe_https_connection_preserves_tls_hostname(monkeypatch):
    sock = MagicMock()
    context = MagicMock()
    monkeypatch.setattr(url_boundary, "_safe_create_connection", lambda *_a, **_kw: sock)
    conn = url_boundary._SafeHTTPSConnection("media.example.org", context=context)
    conn.connect()
    context.wrap_socket.assert_called_once_with(sock, server_hostname="media.example.org")


def test_safe_connection_falls_back_when_ipv6_sockets_are_unavailable(monkeypatch):
    sock = MagicMock()

    def make_socket(family, *_args):
        if family == socket.AF_INET6:
            raise OSError("IPv6 is unavailable")
        return sock

    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_kw: [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 80, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
    ])
    monkeypatch.setattr(socket, "socket", make_socket)
    assert url_boundary._safe_create_connection(("media.example.org", 80), timeout=2) is sock
    sock.connect.assert_called_once_with(("127.0.0.1", 80))


def test_history_replay_keeps_credentials_after_disk_reload(monkeypatch, tmp_path):
    url = "https://user:password@media.invalid/movie.mp4?token=replay-secret"
    monkeypatch.setattr(state, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(state, "HISTORY_STATE_FILE", "history.json")
    monkeypatch.setattr(state, "QUEUE_STATE_FILE", "queue.json")
    monkeypatch.setattr(state, "HISTORY", [{"url": url, "title": "Movie"}])
    state.persist_history()
    assert stat.S_IMODE((tmp_path / "history.json").stat().st_mode) == 0o600
    state.HISTORY.clear()
    state._load_persisted_state()
    replayed = []

    def play(payload):
        replayed.append(payload["url"])
        return {"ok": True}

    monkeypatch.setattr(queue, "_play_now_from_history", play)
    monkeypatch.setenv("RELAYTV_API_TOKEN", "")
    from relaytv_app.config import runtime_config
    runtime_config.refresh_from_env()
    client = TestClient(create_app(testing=True))
    public = client.get("/history")
    assert public.status_code == 200
    assert "replay-secret" not in public.text
    assert "password" not in public.text
    assert client.post("/history/play", json={"index": 0}).status_code == 200
    assert replayed == [url]
