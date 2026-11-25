# SPDX-License-Identifier: GPL-3.0-only
"""Regression coverage for security track hardening fixes."""

from __future__ import annotations

import asyncio
import builtins
from collections import OrderedDict
import http.server
import os
import socket
import stat
import subprocess
import threading
import time
import urllib.request

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from relaytv_app import (
    api_auth,
    device_identity,
    peers,
    player,
    resolver,
    routes,
    state,
    thumb_cache,
    upload_store,
    url_boundary,
    youtube_stream,
)
from relaytv_app.config import runtime_config
from relaytv_app.main import create_app
from relaytv_app.routes import assets as assets_routes
from relaytv_app.routes import snapshots as snapshots_routes
from relaytv_app.routes import uploads as uploads_routes


@pytest.fixture(autouse=True)
def _restore_runtime_config():
    yield
    runtime_config.refresh_from_env()


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# 1. Cross-origin port-confusion bypass & fail-closed missing/malformed Host
# ---------------------------------------------------------------------------


def test_cross_site_rejects_port_confusion_on_omitted_standard_host_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    monkeypatch.setattr(player, "is_playing", lambda: False)
    runtime_config.refresh_from_env()
    client = TestClient(create_app(testing=True))

    # Foreign port on same IP/host when browser omits :80 / :443 on Host must be rejected.
    for headers in (
        {"Host": "192.168.1.50", "Origin": "http://192.168.1.50:3000"},
        {"Host": "tv.example.com", "Origin": "https://tv.example.com:8443"},
        {"Host": "192.168.1.50", "Referer": "http://192.168.1.50:3000/evil"},
        {"Host": "tv.example.com", "Referer": "https://tv.example.com:8443/evil"},
        {"Host": "192.168.1.50:8787", "Origin": "http://192.168.1.50"},
        {"Host": "192.168.1.50:8787", "Referer": "http://192.168.1.50/evil"},
    ):
        post_resp = client.post("/clear", headers=headers)
        assert post_resp.status_code == 403, headers
        snap_resp = client.get("/snapshot", headers=headers)
        assert snap_resp.status_code == 403, headers

    # Genuine same-origin standard-port requests remain allowed.
    for ok_headers in (
        {"Host": "192.168.1.50", "Origin": "http://192.168.1.50"},
        {"Host": "tv.example.com", "Origin": "https://tv.example.com"},
        {"Host": "192.168.1.50", "Referer": "http://192.168.1.50/ui"},
        {"Host": "tv.example.com", "Referer": "https://tv.example.com/ui"},
        {"Host": "192.168.1.50:8787", "Origin": "http://192.168.1.50:8787"},
    ):
        assert client.post("/clear", headers=ok_headers).status_code == 200, ok_headers
        assert client.get("/snapshot", headers=ok_headers).status_code == 409, ok_headers


def test_cross_site_fails_closed_when_host_missing_or_malformed_with_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    monkeypatch.setattr(player, "is_playing", lambda: False)
    runtime_config.refresh_from_env()

    for bad_host in ("", "   ", "bad:99999", "host1, host2", "user@192.168.1.50", "192.168.1.50/x"):
        assert (
            api_auth._is_cross_site(origin="http://192.168.1.50", host=bad_host) is True
        ), bad_host
        assert (
            api_auth._is_cross_site(referer="http://192.168.1.50/ui", host=bad_host) is True
        ), bad_host

    # Non-http(s) Origin/Referer or malformed Referer fails closed.
    assert (
        api_auth._is_cross_site(origin="chrome-extension://192.168.1.50", host="192.168.1.50")
        is True
    )
    assert api_auth._is_cross_site(referer="http://:99999/ui", host="192.168.1.50") is True

    # Headerless non-browser LAN clients still pass even with an empty Host.
    assert api_auth._is_cross_site(sec_fetch_site=None, origin=None, referer=None, host="") is False

    client = TestClient(create_app(testing=True))
    rejected = client.post(
        "/clear",
        headers={"Host": "bad:99999", "Referer": "https://attacker.example/csrf"},
    )
    assert rejected.status_code == 403
    assert client.post("/clear").status_code == 200


# ---------------------------------------------------------------------------
# 2. Outbound HTTP / DNS rebinding hardening (peers.py & youtube_stream.py)
# ---------------------------------------------------------------------------


def test_peers_blocks_dns_rebinding_in_normalize_connect_and_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_getaddrinfo_rebind(host, port=None, *args, **kwargs):
        if host == "metadata-rebind.lan":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port or 80))]
        if host == "offline-peer.local":
            raise socket.gaierror("Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.42", port or 80))]

    monkeypatch.setattr(url_boundary.socket, "getaddrinfo", _fake_getaddrinfo_rebind)

    with pytest.raises(peers.PeerError, match="forbidden device address"):
        peers.normalize_base_url("http://metadata-rebind.lan:8787")

    # Unresolvable LAN/mDNS hostnames remain accepted at normalization time.
    assert peers.normalize_base_url("http://offline-peer.local:8787") == "http://offline-peer.local:8787"

    # Simulate TOCTOU DNS rebinding: normalization resolutions (probe_identity + _request)
    # return a LAN IP, then connect-time resolution inside _open_peer_request returns 169.254.169.254.
    connect_attempts: list[object] = []
    lookup_count = 0
    real_connect = socket.socket.connect

    def _toctou_getaddrinfo(host, port=None, *args, **kwargs):
        nonlocal lookup_count
        lookup_count += 1
        ip = "192.168.1.42" if lookup_count <= 2 else "169.254.169.254"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 8787))]

    monkeypatch.setattr(url_boundary.socket, "getaddrinfo", _toctou_getaddrinfo)
    monkeypatch.setattr(
        url_boundary.socket.socket,
        "connect",
        lambda self, addr: connect_attempts.append(addr),
    )

    with pytest.raises(peers.PeerError) as exc_info:
        peers.probe_identity("http://toctou-peer.lan:8787")
    assert exc_info.value.status_code == 502
    assert lookup_count == 3
    assert connect_attempts == [], "rebinding target must be blocked before socket.connect()"

    # Same-origin redirect where DNS flips to a forbidden IP on the second hop is blocked.
    redirect_hop = {"count": 0}

    class _RebindRedirectHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            redirect_hop["count"] += 1
            self.send_response(302)
            self.send_header("Location", "/rebound-identity")
            self.end_headers()

        def log_message(self, *_args):
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), _RebindRedirectHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def _redirect_rebind_getaddrinfo(host, req_port=None, *args, **kwargs):
            if host == "same-origin-rebind.lan":
                ip = "169.254.169.254" if redirect_hop["count"] >= 1 else "127.0.0.1"
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, req_port or port))]
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", req_port or port))]

        monkeypatch.setattr(url_boundary.socket, "getaddrinfo", _redirect_rebind_getaddrinfo)
        monkeypatch.setattr(url_boundary.socket.socket, "connect", real_connect)
        with pytest.raises(peers.PeerError) as redir_exc:
            peers.probe_identity(f"http://same-origin-rebind.lan:{port}")
        assert redir_exc.value.status_code == 502
        assert redirect_hop["count"] == 1
    finally:
        server.shutdown()
        server.server_close()

    # SafeHttpRedirectHandler must not echo secret tokens from blocked redirect targets.
    handler = url_boundary.SafeHttpRedirectHandler()
    req = urllib.request.Request("https://example.com/start")
    with pytest.raises(urllib.error.URLError) as redir_url_err:
        handler.redirect_request(
            req,
            None,
            302,
            "Found",
            {},
            "http://169.254.169.254/latest/meta-data/?api_key=REDIRECT_SECRET_TOKEN",
        )
    assert "REDIRECT_SECRET_TOKEN" not in str(redir_url_err.value)
    assert "169.254.169.254" not in str(redir_url_err.value)

    # Unit-test monkeypatching of urllib.request.urlopen is preserved.
    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, _n=-1):
            return b'{"device_id":"peer-123","device_name":"Mock TV","version":"1.0"}'

    monkeypatch.setattr(url_boundary.socket, "getaddrinfo", _fake_getaddrinfo_rebind)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: _FakeResp())
    ident = peers.probe_identity("http://192.168.1.42:8787")
    assert ident["device_id"] == "peer-123"


def test_youtube_stream_uses_safe_http_opener_and_blocks_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(youtube_stream, "_SOURCES", OrderedDict())
    connect_attempts: list[object] = []

    monkeypatch.setattr(
        url_boundary.socket,
        "getaddrinfo",
        lambda host, port=None, *_a, **_kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port or 443))
        ],
    )
    monkeypatch.setattr(
        url_boundary.socket.socket,
        "connect",
        lambda self, addr: connect_attempts.append(addr),
    )

    source = youtube_stream.StreamSource(
        url="https://rr1.googlevideo.com/videoplayback?mime=audio%2Fwebm&clen=1024&sig=secret",
        size=1024,
        media_type="audio/webm",
        expires_at=time.monotonic() + 60.0,
    )
    with pytest.raises(youtube_stream.StreamProxyError):
        youtube_stream.fetch_chunk(source, 0, 15)
    assert connect_attempts == [], "youtube_stream must block forbidden resolved IP before connect()"


# ---------------------------------------------------------------------------
# 3. Atomic 0600 temp file creation (peers.py & device_identity.py)
# ---------------------------------------------------------------------------


def test_peers_and_device_identity_create_0600_tempfiles_atomically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    peers_path = tmp_path / "peers.json"
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("RELAYTV_PEERS_FILE", str(peers_path))
    monkeypatch.delenv("RELAYTV_DEVICE_ID", raising=False)
    monkeypatch.setattr(state, "STATE_DIR", str(state_dir), raising=False)
    device_identity.reset_cache_for_tests()

    observed_modes_before_replace: list[tuple[str, int]] = []
    real_replace = os.replace

    def _inspect_replace(src: str, dst: str) -> None:
        st_mode = stat.S_IMODE(os.stat(src, follow_symlinks=False).st_mode)
        observed_modes_before_replace.append((os.path.basename(dst), st_mode))
        real_replace(src, dst)

    real_mkstemp = peers.tempfile.mkstemp
    mkstemp_calls: list[str] = []

    def _spy_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        mkstemp_calls.append(path)
        return fd, path

    monkeypatch.setattr(os, "replace", _inspect_replace)
    monkeypatch.setattr(peers.tempfile, "mkstemp", _spy_mkstemp)

    old_umask = os.umask(0o022)
    try:
        peers._save_payload([{"id": "p_1", "token": "top-secret-peer-token"}])
        generated_id = device_identity.device_id()
    finally:
        os.umask(old_umask)
        device_identity.reset_cache_for_tests()

    assert generated_id
    assert len(mkstemp_calls) == 2
    assert ("peers.json", 0o600) in observed_modes_before_replace
    assert ("device_id", 0o600) in observed_modes_before_replace
    assert stat.S_IMODE(peers_path.stat().st_mode) == 0o600
    assert stat.S_IMODE((state_dir / "device_id").stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []
    assert [p.name for p in state_dir.iterdir() if p.name.endswith(".tmp")] == []


# ---------------------------------------------------------------------------
# 4. Scrub yt-dlp / resolver diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "stderr", "expected_phrase"),
    [
        (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            (
                "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you're not a bot. "
                "Use --cookies-from-browser or --cookies /data/cookies.txt for the authentication. "
                "Redirected to https://accounts.google.com/ServiceLogin?continue=https://yt.example/cb&token=BOT_SECRET_123"
            ),
            "YouTube requires anti-bot verification/cookies",
        ),
        (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            (
                "ERROR: [youtube] dQw4w9WgXcQ: Private video. "
                "Failed loading '/home/mark/.config/yt-dlp/cookies.txt' for https://rr1.googlevideo.com/videoplayback?sig=UNAVAIL_SIG_456&api_key=KEY_789"
            ),
            "Private video",
        ),
        (
            "https://rumble.com/v1abcd-clip.html",
            (
                "ERROR: [rumble] v1abcd: HTTP Error 403: Forbidden for "
                "https://1a-1791.com/video/s8/2/secret.mp4?hdnts=RUMBLE_TOK_999 "
                "using --cookies=/var/lib/relaytv/cookies.txt Authorization: Bearer BEARER_SECRET_321"
            ),
            "Rumble's HTTP challenge",
        ),
        (
            "https://example.com/live",
            (
                "ERROR: [generic] live: HTTP Error 403: Forbidden when fetching "
                "http://192.168.1.99:8096/Items/42/Download?api_key=JF_SECRET_654 "
                "from /tmp/ytdlp-cache/session.txt?token=QUERY_SECRET_111"
            ),
            "HTTP Error 403: Forbidden",
        ),
    ],
)
def test_resolver_scrubs_urls_tokens_and_paths_from_errors_and_status(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    stderr: str,
    expected_phrase: str,
) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    monkeypatch.setattr(player, "is_playing", lambda: False)
    runtime_config.refresh_from_env()
    monkeypatch.setattr("relaytv_app.state.get_settings", lambda: {})
    monkeypatch.setattr("relaytv_app.video_profile.get_profile", lambda: {})
    monkeypatch.setattr(resolver.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        resolver,
        "run",
        lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 1, "", stderr),
    )

    with pytest.raises(HTTPException) as exc_info:
        resolver.resolve_streams_ytdlp(url)

    detail = str(exc_info.value.detail)
    last_error = str(resolver.get_resolver_runtime_state().get("last_error") or "")
    status_body = TestClient(create_app(testing=True)).get("/status").text

    assert expected_phrase in detail
    for leaked in (
        "https://accounts.google.com",
        "https://rr1.googlevideo.com",
        "https://1a-1791.com",
        "http://192.168.1.99",
        "BOT_SECRET_123",
        "UNAVAIL_SIG_456",
        "KEY_789",
        "RUMBLE_TOK_999",
        "BEARER_SECRET_321",
        "JF_SECRET_654",
        "QUERY_SECRET_111",
        "/data/cookies.txt",
        "/home/mark/.config/yt-dlp/cookies.txt",
        "/var/lib/relaytv/cookies.txt",
        "/tmp/ytdlp-cache/session.txt",
    ):
        assert leaked not in detail, (leaked, detail)
        assert leaked not in last_error, (leaked, last_error)
        assert leaked not in status_body, (leaked, status_body)


def test_invidious_resolver_failure_scrubs_urls_and_tokens_in_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("relaytv_app.state.get_settings", lambda: {"youtube_use_invidious": True, "youtube_invidious_base": "https://invid.example.org"})
    monkeypatch.setattr(
        url_boundary,
        "safe_urlopen",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            RuntimeError("upstream error at https://invid-internal.lan/api?token=INVID_SECRET_777")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        resolver.resolve_streams("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert "INVID_SECRET_777" not in str(exc_info.value.detail)
    assert "https://invid-internal.lan" not in str(exc_info.value.detail)
    last_error = str(resolver.get_resolver_runtime_state().get("last_error") or "")
    assert "INVID_SECRET_777" not in last_error
    assert "https://invid-internal.lan" not in last_error


# ---------------------------------------------------------------------------
# 5A. Uploads active-ownership race, orphan dir cleanup, and off-loop open/close
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ingest_media_enqueue_keeps_upload_active_across_concurrent_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir))
    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: True)
    monkeypatch.setattr(routes.player, "prefetch_queue_item_stream", lambda _item: None)
    monkeypatch.setattr(routes.player, "prime_mpv_up_next_from_queue", lambda force=False: None)
    monkeypatch.setattr(routes, "_push_queue_added_toast_async", lambda _item, _label: None)
    monkeypatch.setattr(routes, "_ui_event_push_queue", lambda *_a, **_kw: None)

    # Force a tiny capacity budget so cleanup_uploads() evicts every non-active upload!
    monkeypatch.setattr(upload_store, "max_upload_bytes", lambda _settings=None: 1024)

    # Seed an older unprotected upload that pushes total_bytes above max_bytes.
    old_id = upload_store.new_upload_id()
    old_dir = uploads_dir / old_id
    old_dir.mkdir()
    (old_dir / "old.mp4").write_bytes(b"o" * 900)
    upload_store.write_metadata(
        old_id,
        {
            "id": old_id,
            "filename": "old.mp4",
            "public_name": "old.mp4",
            "stored_name": "old.mp4",
            "mime_type": "video/mp4",
            "size_bytes": 900,
            "created_unix": time.time() - 60.0,
        },
    )

    entered_enqueue = threading.Event()
    cleanup_finished = threading.Event()
    orig_enqueue = uploads_routes._enqueue_uploaded_media_url

    def _interleaved_enqueue(media_url: str) -> dict:
        entered_enqueue.set()
        assert cleanup_finished.wait(timeout=5.0)
        return orig_enqueue(media_url)

    monkeypatch.setattr(uploads_routes, "_enqueue_uploaded_media_url", _interleaved_enqueue)

    def _concurrent_cleanup() -> dict[str, int]:
        assert entered_enqueue.wait(timeout=5.0)
        # At this exact instant ingest_media() has ALREADY returned and _enqueue_uploaded_media_url
        # has not yet read meta.json! With total_bytes (900 + 512 = 1412) > max_bytes (1024),
        # cleanup_uploads() will delete the new upload too unless it is still registered active
        # (even if max_bytes is forced to 1 byte during this sweep!).
        res = upload_store.cleanup_uploads()
        cleanup_finished.set()
        return res

    # Even harsher: make concurrent cleanup enforce a 1-byte cap so ANY unprotected upload is deleted!
    orig_max_bytes = upload_store.max_upload_bytes

    def _cap_bytes(settings_payload=None):
        if entered_enqueue.is_set() and not cleanup_finished.is_set():
            return 1
        return orig_max_bytes(settings_payload)

    monkeypatch.setattr(upload_store, "max_upload_bytes", _cap_bytes)

    cleanup_thread = threading.Thread(target=_concurrent_cleanup, daemon=True)
    cleanup_thread.start()

    transport = httpx.ASGITransport(app=create_app(testing=True))
    async with httpx.AsyncClient(transport=transport, base_url="http://tv.local") as client:
        resp = await client.post(
            "/ingest/media/enqueue",
            data={"title": "Race Protected"},
            files={"file": ("fresh.mp4", b"v" * 512, "video/mp4")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        media_path = body["media_path"]
        served = await client.get(media_path)
        assert served.status_code == 200
        assert served.content == b"v" * 512

    cleanup_thread.join(timeout=5.0)
    assert not upload_store._upload_is_active(body["media_id"])
    assert not (uploads_dir / old_id).exists()


def test_failed_ingest_media_removes_orphan_upload_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir))
    monkeypatch.setattr(upload_store, "max_upload_bytes", lambda _settings=None: 128)

    client = TestClient(create_app(testing=True))

    # Oversized upload (413)
    oversized = client.post(
        "/ingest/media",
        files={"file": ("big.mp4", b"x" * 512, "video/mp4")},
    )
    assert oversized.status_code == 413
    assert list(uploads_dir.iterdir()) == []

    # Empty upload (400)
    empty = client.post(
        "/ingest/media",
        files={"file": ("empty.mp4", b"", "video/mp4")},
    )
    assert empty.status_code == 400
    assert list(uploads_dir.iterdir()) == []
    assert upload_store._ACTIVE_UPLOADS == set()


@pytest.mark.anyio
async def test_upload_file_open_and_close_run_off_event_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir))

    real_open = builtins.open
    real_fdopen = os.fdopen
    open_calls = 0
    close_calls = 0

    class _SlowCloseWrapper:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            nonlocal close_calls
            close_calls += 1
            time.sleep(0.25)
            return self._inner.close()

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()
            return False

    def _slow_open(file, mode="r", *args, **kwargs):
        nonlocal open_calls
        handle = real_open(file, mode, *args, **kwargs)
        if "wb" in str(mode) and str(uploads_dir) in str(file):
            open_calls += 1
            time.sleep(0.25)
            return _SlowCloseWrapper(handle)
        return handle

    def _slow_fdopen(fd, mode="r", *args, **kwargs):
        nonlocal open_calls
        handle = real_fdopen(fd, mode, *args, **kwargs)
        if "wb" in str(mode):
            open_calls += 1
            time.sleep(0.25)
            return _SlowCloseWrapper(handle)
        return handle

    monkeypatch.setattr(builtins, "open", _slow_open)
    monkeypatch.setattr(uploads_routes.os, "fdopen", _slow_fdopen)

    gaps: list[float] = []
    ticking = True

    async def _ticker() -> None:
        prev = time.monotonic()
        while ticking:
            await asyncio.sleep(0.02)
            now = time.monotonic()
            gaps.append(now - prev)
            prev = now

    transport = httpx.ASGITransport(app=create_app(testing=True))
    async with httpx.AsyncClient(transport=transport, base_url="http://tv.local") as client:
        ticker_task = asyncio.create_task(_ticker())
        try:
            for endpoint in ("/ingest/media", "/ingest/media/play"):
                monkeypatch.setattr(
                    uploads_routes,
                    "_play_uploaded_item",
                    lambda item, *, mode: asyncio.sleep(0, result=dict(item)),
                )
                resp = await client.post(
                    endpoint,
                    files={"file": ("clip.mp4", b"a" * 1024, "video/mp4")},
                )
                assert resp.status_code == 200, resp.text
        finally:
            ticking = False
            await ticker_task

    assert open_calls >= 2
    assert close_calls >= 2
    assert gaps
    assert max(gaps) < 0.18, f"event loop stalled during upload open/close: max gap {max(gaps):.3f}s"


# ---------------------------------------------------------------------------
# 5B. Snapshots retention pruning & off-loop os.path.isfile in snapshots/thumbs
# ---------------------------------------------------------------------------


def test_snapshots_prune_oldest_by_file_count_and_bytes_preserving_latest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    (snap_dir / "keep.txt").write_text("unrelated", encoding="utf-8")

    monkeypatch.setenv("RELAYTV_SNAPSHOT_DIR", str(snap_dir))
    monkeypatch.setenv("RELAYTV_SNAPSHOT_MAX_FILES", "3")
    monkeypatch.setenv("RELAYTV_SNAPSHOT_MAX_BYTES", "1000000")
    monkeypatch.setattr(player, "is_playing", lambda: True)

    payload_holder = {"data": b"\xff\xd8\xff" + b"a" * 97}

    def _write_frame(cmd):
        target = cmd[1]
        with open(target, "wb") as fh:
            fh.write(payload_holder["data"])
        return {"error": "success"}

    monkeypatch.setattr(player, "mpv_command", _write_frame)
    client = TestClient(create_app(testing=True))

    urls: list[str] = []
    for idx in range(5):
        payload_holder["data"] = b"\xff\xd8\xff" + f"{idx:04d}".encode("ascii") + b"x" * 90
        resp = client.post("/snapshot")
        assert resp.status_code == 200
        urls.append(resp.json()["image_url"])

    remaining = sorted(p.name for p in snap_dir.iterdir() if p.name.startswith("snapshot-"))
    assert len(remaining) == 3
    assert (snap_dir / "keep.txt").is_file()
    assert client.get(urls[0]).status_code == 404
    assert client.get(urls[1]).status_code == 404
    assert client.get(urls[-1]).status_code == 200

    # Enforce byte budget (250 bytes -> keeps at most 2 ~100B snapshots), and verify an oversized
    # newly captured frame (> max_bytes) still preserves itself while evicting older snapshots.
    monkeypatch.setenv("RELAYTV_SNAPSHOT_MAX_FILES", "50")
    monkeypatch.setenv("RELAYTV_SNAPSHOT_MAX_BYTES", "250")

    payload_holder["data"] = b"\xff\xd8\xff" + b"b" * 97  # 100 bytes
    r_small = client.post("/snapshot")
    assert r_small.status_code == 200
    snaps_after_small = [p for p in snap_dir.iterdir() if p.name.startswith("snapshot-")]
    assert sum(p.stat().st_size for p in snaps_after_small) <= 250
    assert len(snaps_after_small) == 2

    payload_holder["data"] = b"\xff\xd8\xff" + b"L" * 500  # 503 bytes > 250 budget
    r_large = client.post("/snapshot")
    assert r_large.status_code == 200
    snaps_after_large = [p.name for p in snap_dir.iterdir() if p.name.startswith("snapshot-")]
    assert snaps_after_large == [r_large.json()["image_url"].rsplit("/", 1)[-1]]
    assert client.get(r_large.json()["image_url"]).content == payload_holder["data"]


@pytest.mark.anyio
async def test_snapshot_and_thumb_isfile_checks_do_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    snap_dir = tmp_path / "snapshots"
    thumbs_dir = tmp_path / "thumbs"
    snap_dir.mkdir()
    thumbs_dir.mkdir()
    monkeypatch.setenv("RELAYTV_SNAPSHOT_DIR", str(snap_dir))
    monkeypatch.setattr(thumb_cache, "THUMB_DIR", str(thumbs_dir), raising=False)
    monkeypatch.setattr(assets_routes, "THUMB_DIR", str(thumbs_dir), raising=False)
    monkeypatch.setattr(assets_routes, "ensure_cached_sync", lambda _tid: False)

    real_isfile = os.path.isfile
    isfile_calls: list[str] = []

    def _slow_isfile(path):
        p = str(path)
        if str(snap_dir) in p or str(thumbs_dir) in p:
            isfile_calls.append(p)
            time.sleep(0.3)
        return real_isfile(path)

    monkeypatch.setattr(snapshots_routes.os.path, "isfile", _slow_isfile)

    transport = httpx.ASGITransport(app=create_app(testing=True))
    async with httpx.AsyncClient(transport=transport, base_url="http://tv.local") as client:
        for target_url in ("/snapshots/missing.jpg", "/thumbs/0123456789abcdef0123.jpg"):
            gaps: list[float] = []
            ticking = True

            async def _ticker() -> None:
                prev = time.monotonic()
                while ticking:
                    await asyncio.sleep(0.02)
                    now = time.monotonic()
                    gaps.append(now - prev)
                    prev = now

            ticker_task = asyncio.create_task(_ticker())
            try:
                resp = await client.get(target_url)
                assert resp.status_code == 404
            finally:
                ticking = False
                await ticker_task
            assert gaps
            assert max(gaps) < 0.18, f"{target_url} stalled event loop for {max(gaps):.3f}s"
    assert len(isfile_calls) >= 2
