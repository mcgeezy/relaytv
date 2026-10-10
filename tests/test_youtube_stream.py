# SPDX-License-Identifier: GPL-3.0-only
import asyncio
from collections import OrderedDict
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest

from relaytv_app import player, youtube_stream as media
from relaytv_app.main import create_app


SOURCE_URL = "https://rr1.googlevideo.com/videoplayback?mime=audio%2Fwebm&clen=800000&sig=secret"
VIDEO_URL = SOURCE_URL.replace("audio%2Fwebm", "video%2Fmp4")


@pytest.fixture(autouse=True)
def isolated_sources(monkeypatch):
    monkeypatch.setattr(media, "_SOURCES", OrderedDict())


@pytest.fixture(params=["audio%2Fwebm", "video%2Fmp4"])
def upstream(monkeypatch, request):
    state = SimpleNamespace(
        data=bytes(range(251)) * 11000 + b"end",
        requests=[],
        mode="normal",
        entered=threading.Event(),
        release=threading.Event(),
        completed=threading.Event(),
        block=False,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            header = self.headers.get("Range", "")
            state.requests.append(header)
            state.entered.set()
            if state.block:
                state.release.wait(3)
            match = re.fullmatch(r"bytes=(\d+)-(\d+)", header)
            if not match:
                # Reproduce the CDN boundary: an unbounded request cannot
                # deliver media. Reverting just the bounded header fails tests.
                self.send_error(429)
                return
            start, end = map(int, match.groups())
            assert end - start + 1 <= media.CHUNK_BYTES
            body = state.data[start : end + 1]
            if state.mode == "redirect":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/other")
                self.end_headers()
                return
            self.send_response(200 if state.mode == "ignored" else 206)
            offset = 1 if state.mode == "wrong-range" else 0
            self.send_header("Content-Range", f"bytes {start + offset}-{end}/{len(state.data)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body[:-1] if state.mode == "short" else body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                state.completed.set()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Mint through the production boundary, then point the registered source
    # at a real local HTTP server. The route and downloader remain unmocked.
    source_url = SOURCE_URL.replace("audio%2Fwebm", request.param)
    local = media.proxy_url(source_url.replace("800000", str(len(state.data))))
    state.path = urlsplit(local).path
    token = state.path.rsplit("/", 1)[-1]
    media._SOURCES[token] = replace(
        media.get_source(token), url=f"http://127.0.0.1:{server.server_port}/audio"
    )
    yield state
    state.release.set()
    server.shutdown()
    server.server_close()
    thread.join(2)


def request(path, *, method="GET", headers=None, host="127.0.0.1"):
    async def run():
        transport = httpx.ASGITransport(app=create_app(testing=True), client=(host, 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://relaytv") as client:
            return await client.request(method, path, headers=headers)

    return asyncio.run(run())


@pytest.mark.parametrize("range_header", [None, "bytes=0-", "bytes=262140-524300", "bytes=-19"])
def test_stream_download_and_seeks_use_bounded_upstream_requests(upstream, range_header):
    headers = {"Range": range_header} if range_header else None
    response = request(upstream.path, headers=headers)
    start, end = media.byte_range(range_header, len(upstream.data))
    assert response.status_code == (206 if range_header else 200)
    assert response.content == upstream.data[start : end + 1]
    assert response.headers["content-length"] == str(end - start + 1)
    assert response.headers["accept-ranges"] == "bytes"
    if range_header:
        assert response.headers["content-range"] == f"bytes {start}-{end}/{len(upstream.data)}"
    assert upstream.requests == [
        f"bytes={offset}-{min(offset + media.CHUNK_BYTES - 1, end)}"
        for offset in range(start, end + 1, media.CHUNK_BYTES)
    ]


@pytest.mark.parametrize(
    "header", ["bytes=999999999-", "bytes=5-4", "bytes=-0", "bytes=-", "bytes=0-1,4-5"]
)
def test_invalid_range_does_not_contact_upstream(upstream, header):
    response = request(upstream.path, headers={"Range": header})
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(upstream.data)}"
    assert upstream.requests == []


def test_head_and_capability_boundary(upstream):
    response = request(upstream.path, method="HEAD")
    assert response.status_code == 200
    assert response.content == b""
    assert int(response.headers["content-length"]) == len(upstream.data)
    assert request(upstream.path, host="192.0.2.1").status_code == 404
    assert request("/youtube-stream/unknown").status_code == 404
    assert upstream.requests == []


@pytest.mark.parametrize("mode", ["ignored", "wrong-range", "short", "redirect"])
def test_bad_upstream_response_is_not_served_or_redirected(upstream, mode):
    upstream.mode = mode
    response = request(upstream.path)
    assert response.status_code == 502
    assert response.json() == {"detail": "upstream media unavailable"}
    assert len(upstream.requests) == 1


def test_slow_stream_request_does_not_block_health(upstream):
    upstream.block = True

    async def run():
        transport = httpx.ASGITransport(app=create_app(testing=True), client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://relaytv") as client:
            pending = asyncio.create_task(client.get(upstream.path, headers={"Range": "bytes=0-9"}))
            try:
                assert await asyncio.to_thread(upstream.entered.wait, 2)
                assert not upstream.release.is_set()
                assert not upstream.completed.is_set()
                health = await asyncio.wait_for(client.get("/health"), timeout=0.5)
                assert health.status_code == 200
                assert not upstream.completed.is_set()
                assert not pending.done()
            finally:
                upstream.release.set()
            assert (await pending).content == upstream.data[:10]

    asyncio.run(run())


@pytest.mark.parametrize(
    "url",
    [
        None,
        "https://example.com/media.webm",
        "https://rr.googlevideo.com.evil/videoplayback?mime=audio/webm&clen=1",
        SOURCE_URL.replace("https:", "http:"),
        SOURCE_URL.replace("mime=audio%2Fwebm", "mime=text%2Fplain"),
        SOURCE_URL.replace("clen=800000", "clen=0"),
        SOURCE_URL.replace("/videoplayback", "/other"),
        SOURCE_URL.replace("rr1.googlevideo.com", "user@rr1.googlevideo.com"),
        SOURCE_URL.replace("rr1.googlevideo.com", "rr1.googlevideo.com:444"),
    ],
)
def test_only_finite_youtube_stream_is_wrapped(url):
    assert media.proxy_url(url) == url
    assert not media._SOURCES


def test_tokens_are_reused_bounded_and_expire(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(media.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(media, "MAX_SOURCES", 2)
    monkeypatch.setattr(media.config, "server_port", lambda: 8790)
    first = media.proxy_url(SOURCE_URL)
    assert first.startswith("http://127.0.0.1:8790/youtube-stream/")
    assert media.proxy_url(SOURCE_URL) == first
    assert media.proxy_url(first) == first
    token = first.rsplit("/", 1)[-1]
    assert "secret" not in repr(media.get_source(token))
    media.proxy_url(SOURCE_URL + "&n=2")
    media.proxy_url(SOURCE_URL + "&n=3")
    assert len(media._SOURCES) == 2
    assert media.get_source(token) is None
    token = next(iter(media._SOURCES))
    now[0] += media.SOURCE_TTL
    assert media.get_source(token) is None


@pytest.mark.parametrize("backend", ["qt", "external", "x11"])
def test_cold_start_passes_proxy_audio_to_each_backend(monkeypatch, backend):
    captured = []
    for name in (
        "_mark_playback_transition",
        "stop_splash_screen",
        "_reset_mpv_up_next_state",
        "_cleanup_ipc_socket",
        "_apply_startup_mpv_runtime_settings",
        "_recover_audio_output_if_needed",
    ):
        monkeypatch.setattr(player, name, lambda *a, **kw: None)
    monkeypatch.setattr(player, "stop_mpv", lambda **kw: None)
    monkeypatch.setattr(player, "_set_mpv_process_start_option_active", lambda value: None)
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: backend != "x11")
    monkeypatch.setattr(player, "_qt_runtime_uses_external_mpv", lambda: backend == "external")
    monkeypatch.setattr(player, "wait_for_ipc_ready", lambda **kw: True)
    monkeypatch.setattr(player, "_qt_external_video_healthy_with_grace", lambda: True)
    monkeypatch.setattr(player, "_record_qt_external_video_health", lambda value: None)
    monkeypatch.setattr(player.state, "get_settings", lambda: {"video_mode": "x11"})
    monkeypatch.setattr(player, "MPV_PROC", None)
    monkeypatch.setattr(player, "_ACTIVE_HTTP_HEADERS", {})
    from relaytv_app import x11_overlay

    monkeypatch.setattr(x11_overlay, "stop_overlay", lambda: None)
    monkeypatch.setattr(
        player, "_start_qt_shell", lambda stream, **kw: captured.append((stream, kw["audio_url"]))
    )
    monkeypatch.setattr(
        player,
        "_start_qt_external_mpv",
        lambda stream, **kw: captured.append((stream, kw["audio_url"])),
    )
    monkeypatch.setattr(
        player,
        "_build_mpv_args",
        lambda stream, audio, *a, **kw: captured.append((stream, audio)) or [],
    )
    monkeypatch.setattr(player.subprocess, "Popen", lambda *a, **kw: None)
    monkeypatch.setattr(player, "refresh_display_credentials", lambda env: env)
    player.start_mpv(VIDEO_URL, audio_url=SOURCE_URL, start_pos=42)
    assert captured == [(media.proxy_url(VIDEO_URL), media.proxy_url(SOURCE_URL))]


@pytest.mark.parametrize("qt_control", [False, True])
def test_seamless_resume_passes_proxy_audio(monkeypatch, tmp_path, qt_control):
    ipc = tmp_path / "mpv.sock"
    ipc.touch()
    captured = []
    monkeypatch.setattr(player, "IPC_PATH", str(ipc))
    monkeypatch.setenv("RELAYTV_MPV_SEAMLESS_REPLACE", "1")
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: False)
    monkeypatch.setattr(player, "MPV_PROC", SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(player, "_ACTIVE_HTTP_HEADERS", {})
    monkeypatch.setattr(player, "_qt_shell_runtime_accepts_mpv_commands", lambda: qt_control)
    monkeypatch.setattr(player, "_reset_mpv_up_next_state", lambda: None)
    monkeypatch.setattr(
        player, "mpv_command", lambda cmd: captured.append(cmd) or {"error": "success"}
    )
    monkeypatch.setattr(
        player,
        "_qt_shell_runtime_load_stream",
        lambda stream, audio, **kw: captured.append((stream, audio)) or {"error": "success"},
    )
    assert player._load_stream_in_existing_mpv(VIDEO_URL, SOURCE_URL, start_pos=42)
    local = media.proxy_url(SOURCE_URL)
    assert captured == (
        [(media.proxy_url(VIDEO_URL), local)]
        if qt_control
        else [
            [
                "loadfile",
                media.proxy_url(VIDEO_URL),
                "replace",
                "-1",
                f"start=42,audio-files-append={local}",
            ]
        ]
    )


def test_prefetched_queue_audio_uses_proxy_without_persisting_token(monkeypatch):
    monkeypatch.setattr(player, "_MPV_PROCESS_START_OPTION_ACTIVE", False)
    monkeypatch.setattr(player, "_mpv_up_next_eligible_item", lambda item: False)
    item = {
        "url": "https://youtube.com/watch?v=abc",
        "_resolved_source_url": "https://youtube.com/watch?v=abc",
        "_resolved_stream": VIDEO_URL,
        "_resolved_audio": SOURCE_URL,
        "_resolved_at": time.time(),
    }
    command, armed_url = player._mpv_up_next_load_target(item)
    assert command[1] == armed_url == media.proxy_url(VIDEO_URL)
    assert command[-1] == f"audio-files-append={media.proxy_url(SOURCE_URL)}"
    assert item["_resolved_audio"] == SOURCE_URL
    assert item["_resolved_stream"] == VIDEO_URL
