# SPDX-License-Identifier: GPL-3.0-only
"""Regression tests for resolved-stream, subprocess, IP-literal, and thumbnail boundaries."""

from __future__ import annotations

import http.server
import io
import json
import os
import queue
import subprocess
import threading
import urllib.request

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from relaytv_app import player, qt_shell_app, resolver, thumb_cache, url_boundary
from relaytv_app.main import create_app


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "https://169.254.10.20:8443/stream.m3u8",
        "http://[fe80::1]:8080/live.ts",
        "http://[::ffff:169.254.169.254]/meta",
        "http://0.0.0.0:8787/status",
        "http://0.1.2.3/stream.mp4",
        "http://[::]:8787/status",
        "http://[::ffff:0.0.0.0]/stream.mp4",
        "http://224.0.0.1:1234/stream.ts",
        "http://[ff02::1]:1234/stream.ts",
    ],
)
def test_validate_user_url_rejects_link_local_unspecified_and_multicast_ips(url: str) -> None:
    with pytest.raises(HTTPException) as excinfo:
        resolver.validate_user_url(url)
    assert excinfo.value.status_code == 400


@pytest.mark.parametrize(
    "url",
    [
        "http://10.55.55.2:8096/Videos/1/stream",
        "http://172.16.4.10:32400/library/parts/1/file.mkv",
        "http://192.168.1.50:5004/auto/v2.1",
        "http://127.0.0.1:8787/postlive/tok.mkv",
        "http://[::1]:8080/a",
        "https://media.lan:8443/movie.mp4",
    ],
)
def test_validate_user_url_preserves_lan_and_loopback_media_hosts(url: str) -> None:
    assert resolver.validate_user_url(url) == url
    parsed = url_boundary.parse_url(url)
    assert parsed is not None
    assert url_boundary.is_forbidden_ip_literal(parsed.hostname) is False


def test_ytdlp_provider_command_passes_end_of_options_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[list[str]] = []

    def _fake_run(argv, **kwargs):
        recorded.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "Title\n", "")

    monkeypatch.setattr(resolver, "run", _fake_run)
    url = "https://example.com/watch?v=1"
    resolver._run_ytdlp_provider_command(
        url,
        ["yt-dlp", "--no-playlist"],
        ["--no-check-formats", "--print", "%(title)s"],
        timeout=10,
    )

    assert len(recorded) == 1
    assert recorded[0][-2:] == ["--", url]


@pytest.mark.parametrize(
    "stdout",
    [
        "file:///etc/passwd\n",
        "edl://!new_stream;/etc/hosts\n",
        "av://lavfi:aevalsrc=0\n",
        "--script=/tmp/evil.lua\n",
        "http://169.254.169.254/latest/meta-data/\n",
        "https://cdn.example/video.mp4\nfile:///etc/shadow\n",
        "https://cdn.example/video.mp4\nhttp://169.254.169.254/audio.m4a\n",
    ],
)
def test_resolve_streams_ytdlp_rejects_unsafe_resolved_urls(
    monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    monkeypatch.setattr("relaytv_app.state.get_settings", lambda: {})
    monkeypatch.setattr("relaytv_app.video_profile.get_profile", lambda: {})
    monkeypatch.setattr(resolver.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        resolver,
        "run",
        lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, stdout, ""),
    )

    with pytest.raises(HTTPException) as excinfo:
        resolver.resolve_streams_ytdlp("https://example.com/video")
    assert excinfo.value.status_code == 400


def test_resolve_streams_invidious_normalizes_relative_and_rejects_unsafe_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload: dict[str, object] = {
        "formatStreams": [
            {"url": "/videoplayback?id=abc&itag=18", "mimeType": "video/mp4", "qualityLabel": "360p"}
        ]
    }

    class _FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.close()

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda _url, timeout=15: _FakeResp(json.dumps(payload).encode("utf-8")),
    )

    stream, audio = resolver.resolve_streams_invidious(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        base="https://invid.example.org",
    )
    assert stream == "https://invid.example.org/videoplayback?id=abc&itag=18"
    assert audio is None

    for unsafe in (
        "file:///etc/passwd",
        "edl://!no_clip;https://x.test/a.mp4",
        "//evil.example.org/video.mp4",
        "http://169.254.169.254/video.mp4",
    ):
        payload = {
            "formatStreams": [{"url": unsafe, "mimeType": "video/mp4", "qualityLabel": "720p"}]
        }
        with pytest.raises(HTTPException) as excinfo:
            resolver.resolve_streams_invidious(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                base="https://invid.example.org",
            )
        assert excinfo.value.status_code == 400


def test_mpv_builders_disable_auto_user_scripts_unless_explicitly_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MPV_ARGS", raising=False)
    monkeypatch.delenv("RELAYTV_QT_SHELL_MPV_ARGS", raising=False)
    monkeypatch.setattr(player.state, "get_settings", lambda: {"volume": 80})
    monkeypatch.setattr(player, "_effective_audio_device", lambda settings=None: "")
    monkeypatch.setattr(player, "_x11_mode_active", lambda selected_mode=None: False)
    monkeypatch.setattr(player, "_x11_overlay_enabled", lambda: False)
    monkeypatch.setattr(player, "_provider_hint_for_stream", lambda *_a, **_k: "youtube")
    monkeypatch.setattr(player, "_should_force_ytdl_off", lambda *_a, **_k: False)
    monkeypatch.setattr(player, "_effective_ytdl_format", lambda *_a, **_k: "")

    player_args = player._build_mpv_args("https://example.com/v.mp4", None, "x11")
    qt_args = qt_shell_app._build_mpv_args("https://example.com/v.mp4", 101)
    assert "--load-scripts=no" in player_args
    assert "--ytdl=yes" in player_args
    assert "--load-scripts=no" in qt_args
    assert "--ytdl=yes" in qt_args

    monkeypatch.setenv("MPV_ARGS", "--load-scripts=yes")
    monkeypatch.setenv("RELAYTV_QT_SHELL_MPV_ARGS", "--load-scripts=yes")
    overridden_player = player._build_mpv_args("https://example.com/v.mp4", None, "x11")
    overridden_qt = qt_shell_app._build_mpv_args("https://example.com/v.mp4", 101)
    assert "--load-scripts=no" not in overridden_player
    assert "--load-scripts=yes" in overridden_player
    assert "--load-scripts=no" not in overridden_qt
    assert "--load-scripts=yes" in overridden_qt


def test_thumb_cache_blocks_redirect_to_link_local_and_file_schemes(tmp_path) -> None:
    target_holder = {"location": "http://169.254.169.254/latest/meta-data/"}

    class _RedirectHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", target_holder["location"])
            self.end_headers()

        def log_message(self, *_args):
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), _RedirectHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        out = tmp_path / "thumb.bin"
        assert thumb_cache._download_to("http://169.254.169.254/direct.jpg", str(out)) is False
        assert thumb_cache._download_to("file:///etc/passwd", str(out)) is False

        with pytest.raises(urllib.error.URLError):
            thumb_cache._download_to(f"http://127.0.0.1:{port}/thumb.jpg", str(out))

        target_holder["location"] = "file:///etc/passwd"
        with pytest.raises(urllib.error.URLError):
            thumb_cache._download_to(f"http://127.0.0.1:{port}/thumb2.jpg", str(out))
    finally:
        server.shutdown()
        server.server_close()


def test_thumb_cache_rejects_non_image_fallback_and_serves_nosniff(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thumb_dir = str(tmp_path / "thumbs")
    os.makedirs(thumb_dir, exist_ok=True)
    monkeypatch.setattr(thumb_cache, "THUMB_DIR", thumb_dir, raising=False)
    monkeypatch.setattr("relaytv_app.routes.assets.THUMB_DIR", thumb_dir, raising=False)
    thumb_cache.reset_thumb_tracking_for_tests()
    monkeypatch.setattr(thumb_cache, "_normalize_to_jpg", lambda _src, _dst: False)

    payloads = {
        "https://cdn.test/error.html": b"<!doctype html><script>alert(1)</script>",
        "https://cdn.test/vector.svg": b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        "https://cdn.test/valid.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
    }

    def _fake_download(url: str, fp: str) -> bool:
        body = payloads.get(url)
        if body is None:
            return False
        with open(fp, "wb") as handle:
            handle.write(body)
        return True

    monkeypatch.setattr(thumb_cache, "_download_to", _fake_download)

    for rejected_url in ("https://cdn.test/error.html", "https://cdn.test/vector.svg"):
        tid = thumb_cache.thumb_id(rejected_url)
        thumb_cache._remember_src(tid, rejected_url)
        assert thumb_cache.ensure_cached_sync(tid, timeout_s=0) is False
        assert not os.path.exists(thumb_cache.local_abs_path(tid))

    # Also exercise the queue worker fallback path on non-image bytes.
    worker_url = "https://cdn.test/error.html"
    worker_tid = thumb_cache.thumb_id(worker_url)
    thumb_cache.reset_thumb_tracking_for_tests()
    q: queue.Queue[tuple[str, str]] = queue.Queue()
    q.put((worker_url, worker_tid))

    def _stop_after_one(*_a, **_kw):
        raise SystemExit

    monkeypatch.setattr(thumb_cache, "_Q", q)
    with pytest.raises(SystemExit):
        orig_release = thumb_cache._release_thumb

        def _release_and_stop(tid_val: str, *, failed: bool) -> None:
            orig_release(tid_val, failed=failed)
            _stop_after_one()

        monkeypatch.setattr(thumb_cache, "_release_thumb", _release_and_stop)
        thumb_cache._worker()

    assert not os.path.exists(thumb_cache.local_abs_path(worker_tid))
    assert worker_tid in thumb_cache._FAILED_AT

    # Verified raster headers still materialize when ffmpeg is unavailable, and
    # /thumbs serves X-Content-Type-Options: nosniff.
    thumb_cache.reset_thumb_tracking_for_tests()
    monkeypatch.setattr(thumb_cache, "_release_thumb", orig_release)
    png_url = "https://cdn.test/valid.png"
    png_tid = thumb_cache.thumb_id(png_url)
    thumb_cache._remember_src(png_tid, png_url)
    client = TestClient(create_app(testing=True))
    res = client.get(f"/thumbs/{png_tid}.jpg")
    assert res.status_code == 200
    assert res.headers.get("x-content-type-options") == "nosniff"
