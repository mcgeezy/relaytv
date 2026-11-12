# SPDX-License-Identifier: GPL-3.0-only
"""Regression coverage for core security, auth/CSRF, thumbnail, and lifecycle fixes."""

from __future__ import annotations

import errno
import http.server
import os
import shutil
import socket
import stat
import subprocess
import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from relaytv_app import (
    api_auth,
    public_media,
    playback_service,
    player,
    postlive_relay,
    qt_shell_app,
    resolver,
    routes,
    state,
    thumb_cache,
    url_boundary,
)
from relaytv_app.config import SETTINGS_BUS_VARS, runtime_config
from relaytv_app.main import create_app


@pytest.fixture(autouse=True)
def _isolate_global_runtime_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(player, "MPV_PROC", None)
    monkeypatch.setattr(player, "_PLAYBACK_INTENT", 0)
    monkeypatch.setattr(player, "_PLAYBACK_LOAD_STARTED_INTENT", None)
    monkeypatch.setattr(player, "_PLAYBACK_PUBLISHED_INTENT", None)
    yield
    runtime_config.refresh_from_env()


def test_youtube_cookies_upload_ignores_mutable_path_and_writes_0600(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    state_dir = tmp_path / "data"
    state_dir.mkdir()
    evil_target = tmp_path / "evil_overwrite.py"

    monkeypatch.delenv("RELAYTV_YTDLP_COOKIES_UPLOAD_PATH", raising=False)
    monkeypatch.delenv("YTDLP_COOKIES", raising=False)
    monkeypatch.setattr(state, "STATE_DIR", str(state_dir), raising=False)
    runtime_config.set_value("RELAYTV_YTDLP_COOKIES", str(evil_target))

    stored: dict[str, object] = {}
    monkeypatch.setattr(state, "get_settings", lambda: dict(stored))
    monkeypatch.setattr(
        state,
        "update_settings",
        lambda patch: stored.update(patch) or dict(stored),
    )
    monkeypatch.setattr(player, "is_playing", lambda: False)

    client = TestClient(create_app(testing=True))

    rejected = client.post("/settings", json={"youtube_cookies_path": str(evil_target)})
    assert rejected.status_code == 400

    rejected_traversal = client.post(
        "/settings", json={"youtube_cookies_path": f"{state_dir}/../evil.txt"}
    )
    assert rejected_traversal.status_code == 400

    cookie_body = (
        "# Netscape HTTP Cookie File\n"
        "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tsecret-cookie\n"
    )
    uploaded = client.post("/settings/youtube/cookies", json={"cookies_text": cookie_body})
    assert uploaded.status_code == 200
    assert not evil_target.exists()

    expected_cookie_file = state_dir / "cookies.txt"
    assert expected_cookie_file.is_file()
    assert stat.S_IMODE(expected_cookie_file.stat().st_mode) == 0o600
    assert "secret-cookie" in expected_cookie_file.read_text(encoding="utf-8")

    cleared = client.post("/settings/youtube/cookies/clear")
    assert cleared.status_code == 200
    assert not expected_cookie_file.exists()


def test_youtube_cookies_upload_validates_strict_netscape_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    target = tmp_path / "cookies.txt"
    monkeypatch.setenv("RELAYTV_YTDLP_COOKIES_UPLOAD_PATH", str(target))
    monkeypatch.setattr(state, "update_settings", lambda patch: dict(patch))

    client = TestClient(create_app(testing=True))

    for bad_payload in (
        "# comment only\n",
        "import os\ndef exploit():\tos.system('id')\n",
        ".youtube.com\tMAYBE\t/\tTRUE\t0\tSID\tval\n",
        ".youtube.com\tTRUE\trelative\tTRUE\t0\tSID\tval\n",
        ".youtube.com\tTRUE\t/\tTRUE\tnotanint\tSID\tval\n",
        ".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tval\x00bad\n",
    ):
        resp = client.post("/settings/youtube/cookies", json={"cookies_text": bad_payload})
        assert resp.status_code == 400, bad_payload
        assert not target.exists()


def test_play_now_validates_resolved_stream_and_mpv_end_of_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        routes.player,
        "play_item",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("unsafe play_now must not reach play_item")),
    )
    client = TestClient(create_app(testing=True))

    for unsafe_stream in (
        "file:///etc/passwd",
        "edl://!no_clip;https://example.com/a.mp4",
        "--script=/tmp/evil.lua",
        "http://169.254.169.254/latest/meta-data/",
    ):
        resp = client.post(
            "/play_now",
            json={
                "url": "https://example.com/video.mp4",
                "resolved_stream": unsafe_stream,
                "resolved_at": time.time(),
            },
        )
        assert resp.status_code == 400, unsafe_stream

    future_resp = client.post(
        "/play_now",
        json={
            "url": "https://example.com/video.mp4",
            "resolved_stream": "https://cdn.example.com/video.mp4",
            "resolved_at": time.time() + 7200.0,
        },
    )
    assert future_resp.status_code == 400

    assert (
        player._fresh_prefetched_stream(
            {
                "url": "https://example.com/v.mp4",
                "_resolved_source_url": "https://example.com/v.mp4",
                "_resolved_stream": "file:///etc/passwd",
                "_resolved_at": time.time(),
            }
        )
        is None
    )
    assert (
        player._fresh_prefetched_stream(
            {
                "url": "https://example.com/v.mp4",
                "_resolved_source_url": "https://example.com/v.mp4",
                "_resolved_stream": "https://cdn.example.com/v.mp4",
                "_resolved_at": time.time() + 3600.0,
            }
        )
        is None
    )

    monkeypatch.setattr(player.state, "get_settings", lambda: {"volume": 80})
    monkeypatch.setattr(player, "_effective_audio_device", lambda settings=None: "")
    monkeypatch.setattr(player, "_x11_mode_active", lambda selected_mode=None: False)
    monkeypatch.setattr(player, "_x11_overlay_enabled", lambda: False)
    monkeypatch.setattr(player, "_provider_hint_for_stream", lambda *_a, **_k: "generic")
    monkeypatch.setattr(player, "_should_force_ytdl_off", lambda *_a, **_k: True)
    monkeypatch.setattr(player, "_effective_ytdl_format", lambda *_a, **_k: "")

    plain_args = player._build_mpv_args("--script=/tmp/evil.lua", None, "x11")
    assert plain_args[-2:] == ["--", "--script=/tmp/evil.lua"]
    ext_args = player._build_qt_external_mpv_args("--script=/tmp/evil.lua", None)
    assert ext_args[-2:] == ["--", "--script=/tmp/evil.lua"]
    qt_args = qt_shell_app._build_mpv_args("--script=/tmp/evil.lua", 42)
    assert qt_args[-2:] == ["--", "--script=/tmp/evil.lua"]


def test_postlive_relay_passes_end_of_options_before_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    captured_cmds: list[list[str]] = []

    class _FakeStdout:
        def fileno(self) -> int:
            return 3

        def close(self) -> None:
            return None

        def read(self, _n: int = -1) -> bytes:
            return b""

        def __iter__(self):
            return iter(())

    class _FakeProc:
        def __init__(self, cmd):
            captured_cmds.append(list(cmd))
            self.stdout = _FakeStdout()
            self.stderr = _FakeStdout()

        def poll(self):
            return 0

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(postlive_relay.state, "STATE_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(postlive_relay.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(postlive_relay.subprocess, "Popen", lambda cmd, **_kw: _FakeProc(cmd))
    try:
        token = postlive_relay.create_session("--exec=id", ytdl_format="best")
        assert token
        ytdlp_cmd = captured_cmds[0]
        assert ytdlp_cmd[-2:] == ["--", "--exec=id"]
    finally:
        postlive_relay.close_all(reason="test cleanup")


def test_mpv_loadfile_escapes_comma_bearing_audio_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    injected_audio = "https://cdn.example.com/audio.m4a?sparams=id,itag,ytdl=yes,script=/tmp/pwn.lua"
    expected_opt = (
        f"audio-files-append=%{len(injected_audio.encode('utf-8'))}%{injected_audio}"
    )

    captured_ipc: list[list[object]] = []
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: False)
    monkeypatch.setattr(player, "_qt_shell_runtime_accepts_mpv_commands", lambda: False)
    monkeypatch.setattr(
        player,
        "MPV_PROC",
        type("_P", (), {"poll": staticmethod(lambda: None)})(),
    )
    monkeypatch.setattr(player.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(player.youtube_stream, "proxy_url", lambda u: u)
    monkeypatch.setattr(
        player,
        "mpv_command",
        lambda cmd: captured_ipc.append(list(cmd)) or {"error": "success"},
    )

    assert player._load_stream_in_existing_mpv(
        "https://cdn.example.com/video.mp4",
        audio_url=injected_audio,
        start_pos=42.0,
    )
    assert captured_ipc[-1] == [
        "loadfile",
        "https://cdn.example.com/video.mp4",
        "replace",
        "-1",
        f"start=42,{expected_opt}",
    ]

    libmpv_cmds: list[tuple[str, ...]] = []
    fake_libmpv = qt_shell_app._QtLibMpvPlayer()
    monkeypatch.setattr(fake_libmpv, "_command", lambda *parts: libmpv_cmds.append(parts))
    fake_libmpv.load_stream("https://cdn.example.com/video.mp4", injected_audio, 15.0)
    assert libmpv_cmds[-1] == (
        "loadfile",
        "https://cdn.example.com/video.mp4",
        "replace",
        "-1",
        f"start=15,{expected_opt}",
    )


def test_url_boundary_blocks_non_canonical_ips_dns_rebinding_and_invidious_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for forbidden_host in (
        "2852039166",
        "0xa9fea9fe",
        "0251.0376.0251.0376",
        "169.254.43518",
        "0",
        "[64:ff9b::a9fe:a9fe]",
        "[2002:a9fe:a9fe::1]",
        "[::169.254.169.254]",
    ):
        assert url_boundary.is_forbidden_ip_literal(forbidden_host) is True, forbidden_host
        with pytest.raises(HTTPException) as exc_info:
            resolver.validate_user_url(f"http://{forbidden_host}/stream.mp4")
        assert exc_info.value.status_code == 400

    monkeypatch.setattr(
        url_boundary.socket,
        "getaddrinfo",
        lambda host, *_a, **_kw: (
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]
            if host == "rebind.attacker.invalid-tld"
            else []
        ),
    )
    with pytest.raises(HTTPException) as rebind_exc:
        resolver.validate_user_url("http://rebind.attacker.invalid-tld/video.mp4")
    assert rebind_exc.value.status_code == 400
    assert (
        thumb_cache._download_to("http://rebind.attacker.invalid-tld/thumb.jpg", "/tmp/unused")
        is False
    )

    class _RedirectToMetadataHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()

        def log_message(self, *_args):
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), _RedirectToMetadataHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(HTTPException):
            resolver.resolve_streams_invidious(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                base=f"http://127.0.0.1:{port}",
            )
    finally:
        server.shutdown()
        server.server_close()


def test_qt_libmpv_player_disables_load_scripts_unless_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opts: list[tuple[str, str]] = []

    class _FakeLib:
        def mpv_create(self):
            return 12345

        def mpv_initialize(self, _handle):
            return 0

    def _run(extra_env: str | None) -> list[tuple[str, str]]:
        opts.clear()
        if extra_env is None:
            monkeypatch.delenv("RELAYTV_QT_SHELL_MPV_ARGS", raising=False)
        else:
            monkeypatch.setenv("RELAYTV_QT_SHELL_MPV_ARGS", extra_env)
        monkeypatch.delenv("MPV_ARGS", raising=False)
        instance = qt_shell_app._QtLibMpvPlayer()
        instance._lib = _FakeLib()
        monkeypatch.setattr(instance, "_bind_api", lambda: None)
        monkeypatch.setattr(instance, "_set_opt", lambda k, v: opts.append((k, v)))
        monkeypatch.setattr(instance, "_set_opt_best_effort", lambda k, v: opts.append((k, v)))
        instance.start(
            stream="https://example.com/v.mp4",
            audio=None,
            start_pos=None,
            ipc_path=None,
            audio_device=None,
            sub_lang=None,
            volume=80.0,
            ytdl_enabled=False,
            ytdl_path=None,
            ytdl_format=None,
            ytdl_raw_options=None,
            user_agent=None,
            referrer=None,
        )
        return list(opts)

    default_opts = _run(None)
    assert ("load-scripts", "no") in default_opts

    overridden_opts = _run("--load-scripts=yes")
    assert ("load-scripts", "no") not in overridden_opts
    assert ("load-scripts", "yes") in overridden_opts


def test_postlive_route_requires_loopback_and_state_persists_0600_sanitized(
    tmp_path,
) -> None:
    lan_client = TestClient(create_app(testing=False), client=("192.168.1.77", 51234))
    forbidden = lan_client.get("/postlive/0123456789abcdef0123456789abcdef.mkv")
    assert forbidden.status_code == 403

    loop_client = TestClient(create_app(testing=False), client=("127.0.0.1", 51234))
    missing = loop_client.get("/postlive/0123456789abcdef0123456789abcdef.mkv")
    assert missing.status_code == 404

    secret_json = tmp_path / "settings.json"
    assert state._atomic_write_json(str(secret_json), {"secret": "value"}) is True
    assert stat.S_IMODE(secret_json.stat().st_mode) == 0o600

    cleaned_hist = state._persistable_history_item(
        {
            "id": "h1",
            "url": "https://user:pass@jellyfin.example/Videos/1?api_key=leak-key&v=1",
            "thumbnail": "https://jellyfin.example/Items/1/Images/Primary?X-Plex-Token=plex-leak",
            "title": "Demo",
        }
    )
    assert cleaned_hist is not None
    assert "leak-key" in cleaned_hist["url"]
    public_hist = public_media.public_media_item(cleaned_hist)
    assert "leak-key" not in public_hist["url"]
    assert "user:pass" not in public_hist["url"]
    assert "plex-leak" not in str(public_hist.get("thumbnail") or "")


def test_settings_server_repointing_clears_stored_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: dict[str, object] = {
        "jellyfin_enabled": True,
        "jellyfin_server_url": "https://jf.example:8096",
        "jellyfin_api_key": "jf-secret-key",
        "jellyfin_password": "jf-secret-password",
        "seerr_enabled": True,
        "seerr_server_url": "https://seerr.example",
        "seerr_api_key": "seerr-secret-key",
    }
    monkeypatch.setattr(routes.state, "get_settings", lambda: dict(stored))
    monkeypatch.setattr(
        routes.state,
        "update_settings",
        lambda patch: stored.update(patch) or dict(stored),
    )
    monkeypatch.setattr(routes.player, "is_playing", lambda: False)
    monkeypatch.setattr(routes.jellyfin_receiver, "connect", lambda **_kw: None)
    monkeypatch.setattr(routes.jellyfin_receiver, "disconnect", lambda: None)
    monkeypatch.setattr(routes.jellyfin_receiver, "mark_error", lambda _r: None)

    client = TestClient(create_app(testing=True))

    repointed = client.post(
        "/settings",
        json={
            "jellyfin_server_url": "https://other-jf.example:8096",
            "seerr_server_url": "https://other-seerr.example",
        },
    )
    assert repointed.status_code == 200
    assert stored["jellyfin_api_key"] == ""
    assert stored["jellyfin_password"] == ""
    assert stored["seerr_api_key"] == ""
    assert runtime_config.snapshot().raw("RELAYTV_JELLYFIN_API_KEY") == ""
    assert runtime_config.snapshot().raw("RELAYTV_JELLYFIN_PASSWORD") == ""
    assert runtime_config.snapshot().raw("RELAYTV_SEERR_API_KEY") == ""

    bad_jf = client.post("/settings", json={"jellyfin_server_url": "http://169.254.169.254:8096"})
    assert bad_jf.status_code == 400
    bad_seerr = client.post("/settings", json={"seerr_server_url": "http://169.254.169.254:5055"})
    assert bad_seerr.status_code == 400


def test_idle_and_overlay_pages_escape_untrusted_text_and_javascript_links() -> None:
    client = TestClient(create_app(testing=True))
    idle_html = client.get("/idle").text
    assert "escIdleHtml(String(wset.location_name" in idle_html

    overlay_html = client.get("/x11/overlay").text
    assert "const safeLinkUrl = /^(https?:\\/\\/|\\/)/i.test(rawLinkUrl)" in overlay_html
    assert "__OVERLAY_CLIENT_TOKEN__" not in overlay_html


def test_superseded_playback_failure_and_ipc_timeout_reap_orphaned_mpv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started_mpv = threading.Event()
    release_first_start = threading.Event()
    second_claimed = threading.Event()
    stop_calls: list[bool] = []
    runtime = {"playing": False}

    session_updates: list[dict[str, object]] = []
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: False)
    monkeypatch.setattr(player, "_load_stream_in_existing_mpv", lambda *_a, **_kw: False)
    monkeypatch.setattr(player, "is_playing", lambda: runtime["playing"])
    monkeypatch.setattr(
        player.state,
        "NOW_PLAYING",
        {"url": "https://youtube.com/watch?v=old00000000"},
        raising=False,
    )
    monkeypatch.setattr(player.state, "SESSION_STATE", "playing", raising=False)
    monkeypatch.setattr(
        player.state,
        "update_session",
        lambda **kw: session_updates.append(dict(kw)),
    )

    def _fake_start_mpv(stream_url, *_a, **_kw):
        runtime["playing"] = True
        started_mpv.set()
        assert release_first_start.wait(timeout=5.0)

    def _fake_stop_mpv(*, restart_splash: bool = True):
        runtime["playing"] = False
        stop_calls.append(restart_splash)

    monkeypatch.setattr(player, "start_mpv", _fake_start_mpv)
    monkeypatch.setattr(player, "stop_mpv", _fake_stop_mpv)

    def _fake_resolve(url: str):
        if "failing" in url:
            second_claimed.set()
            release_first_start.set()
            time.sleep(0.05)
            raise HTTPException(status_code=400, detail="resolver failed")
        return ("https://cdn.example.com/good.mp4", None)

    monkeypatch.setattr(player, "resolve_streams", _fake_resolve)

    first_result: list[object] = []
    first_thread = threading.Thread(
        target=lambda: first_result.append(
            player.play_item("https://youtube.com/watch?v=first111111", True, False, False, "play")
        ),
        daemon=True,
    )
    first_thread.start()
    assert started_mpv.wait(timeout=5.0)

    with pytest.raises(HTTPException):
        player.play_item("https://youtube.com/watch?v=failing1111", True, False, False, "play")

    first_thread.join(timeout=5.0)
    assert second_claimed.is_set()
    assert runtime["playing"] is False
    assert stop_calls, "orphaned mpv from superseded play must be stopped when successor fails"

    # Also verify repair_orphan_runtime_playback stops an orphan even when QUEUE is non-empty.
    stop_calls.clear()
    monkeypatch.setattr(player, "playback_transitioning", lambda: False)
    monkeypatch.setattr(player, "auto_next_transitioning", lambda: False)
    monkeypatch.setattr(player, "natural_idle_reset_holding", lambda: False)
    monkeypatch.setattr(player.state, "NOW_PLAYING", None, raising=False)
    monkeypatch.setattr(player.state, "QUEUE", [{"url": "https://example.com/queued.mp4"}])
    try:
        assert (
            player._repair_orphan_runtime_playback(
                {"path": "https://cdn.example.com/orphan.mp4", "core-idle": False, "eof-reached": False}
            )
            is True
        )
        assert stop_calls == [True]
    finally:
        player.state.QUEUE.clear()


def test_start_mpv_ipc_timeout_stops_spawned_process(monkeypatch: pytest.MonkeyPatch) -> None:
    stopped: list[bool] = []
    fake_proc = type("_P", (), {"poll": staticmethod(lambda: None)})()

    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: False)
    monkeypatch.setattr(player, "stop_splash_screen", lambda: None)
    monkeypatch.setattr(player, "_cleanup_ipc_socket", lambda: None)
    monkeypatch.setattr(player.state, "get_settings", lambda: {"video_mode": "x11"})
    monkeypatch.setattr(player.subprocess, "Popen", lambda *_a, **_kw: fake_proc)
    monkeypatch.setattr(player, "wait_for_ipc_ready", lambda timeout=5.0: False)
    monkeypatch.setattr(
        player,
        "stop_mpv",
        lambda *, restart_splash=True: stopped.append(restart_splash),
    )

    with pytest.raises(HTTPException) as exc_info:
        player.start_mpv("https://example.com/video.mp4")
    assert exc_info.value.status_code == 500
    assert stopped[-1] is False
    assert len(stopped) >= 2  # once pre-spawn cleanup, once on IPC timeout


def test_thumb_cache_atomic_exdev_commit_and_dot_path_404(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    src_dir = tmp_path / "tmp_src"
    dst_dir = tmp_path / "thumbs"
    src_dir.mkdir()
    dst_dir.mkdir()
    src_file = src_dir / "out.jpg"
    dst_file = dst_dir / "abc123.jpg"
    src_file.write_bytes(b"\xff\xd8\xff\xe0test-jpeg-bytes")

    real_replace = os.replace
    replace_calls: list[tuple[str, str]] = []

    def _exdev_cross_dir(src: str, dst: str) -> None:
        replace_calls.append((src, dst))
        if os.path.dirname(src) != os.path.dirname(dst):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        real_replace(src, dst)

    def _forbid_direct_copy_to_dst(src: str, dst: str, *_a, **_kw):
        assert dst != str(dst_file), "must not copy directly to live destination path"
        return shutil.copyfile.__wrapped__(src, dst) if hasattr(shutil.copyfile, "__wrapped__") else None

    orig_copyfile = shutil.copyfile
    monkeypatch.setattr(thumb_cache.os, "replace", _exdev_cross_dir)
    monkeypatch.setattr(
        thumb_cache.shutil,
        "copyfile",
        lambda s, d: (
            (_ for _ in ()).throw(AssertionError("direct non-atomic copy to dst"))
            if d == str(dst_file)
            else orig_copyfile(s, d)
        ),
    )

    assert thumb_cache._commit_file(str(src_file), str(dst_file)) is True
    assert dst_file.read_bytes() == b"\xff\xd8\xff\xe0test-jpeg-bytes"
    assert os.path.dirname(replace_calls[-1][0]) == str(dst_dir)
    assert replace_calls[-1][1] == str(dst_file)

    # Verify _normalize_to_jpg skips ffmpeg on non-image HTML and passes -frames:v 1 on GIF.
    ffmpeg_cmds: list[list[str]] = []
    html_in = src_dir / "err.html"
    html_in.write_bytes(b"<html>not an image</html>")
    monkeypatch.setattr(
        thumb_cache.subprocess,
        "run",
        lambda cmd, **_kw: ffmpeg_cmds.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0),
    )
    assert thumb_cache._normalize_to_jpg(str(html_in), str(src_dir / "never.jpg")) is False
    assert ffmpeg_cmds == []

    gif_in = src_dir / "anim.gif"
    gif_out = src_dir / "norm.jpg"
    gif_in.write_bytes(b"GIF89a\x01\x00\x01\x00\x00\x00\x00;")

    def _fake_ffmpeg(cmd, **kw):
        ffmpeg_cmds.append(list(cmd))
        assert kw.get("timeout") == 15
        gif_out.write_bytes(b"\xff\xd8\xff\xd9")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(thumb_cache.subprocess, "run", _fake_ffmpeg)
    assert thumb_cache._normalize_to_jpg(str(gif_in), str(gif_out)) is True
    assert "-frames:v" in ffmpeg_cmds[-1]
    assert ffmpeg_cmds[-1][ffmpeg_cmds[-1].index("-frames:v") + 1] == "1"

    # /thumbs/%2e and /snapshots/%2e must never raise 500 IsADirectoryError.
    monkeypatch.setattr("relaytv_app.routes.assets.THUMB_DIR", str(dst_dir), raising=False)
    monkeypatch.setenv("RELAYTV_SNAPSHOT_DIR", str(dst_dir))
    client = TestClient(create_app(testing=True))
    assert client.get("/thumbs/%2e").status_code in (400, 404)
    assert client.get("/snapshots/%2e").status_code in (400, 404)


def test_csrf_rejects_cross_site_posts_and_overlay_heartbeat_works_with_api_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    client = TestClient(create_app(testing=True))

    assert client.post("/clear", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert (
        client.post(
            "/clear",
            headers={"Origin": "https://attacker.example", "Host": "relaytv.lan:8787"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/clear",
            headers={"Origin": "null", "Host": "relaytv.lan:8787"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/clear",
            headers={
                "Sec-Fetch-Site": "same-origin",
                "Origin": "http://testserver",
                "Host": "testserver",
            },
        ).status_code
        == 200
    )
    assert client.post("/clear").status_code == 200

    # Enable RELAYTV_API_TOKEN and verify /x11/overlay/client_state works from local overlay
    # without exposing RELAYTV_API_TOKEN in /x11/overlay HTML.
    secret_token = "super-secret-operator-token"
    monkeypatch.setenv("RELAYTV_API_TOKEN", secret_token)
    runtime_config.refresh_from_env()

    overlay_page = client.get("/x11/overlay")
    assert overlay_page.status_code == 200
    assert secret_token not in overlay_page.text

    unauth_state = client.post("/x11/overlay/client_state", json={"state": "connected"})
    assert unauth_state.status_code == 401

    auth_overlay_state = client.post(
        "/x11/overlay/client_state",
        headers={"X-RelayTV-Overlay-Token": api_auth.overlay_client_token()},
        json={"state": "connected"},
    )
    assert auth_overlay_state.status_code == 200
    assert auth_overlay_state.json()["ok"] is True

    # Remote LAN caller with the overlay token is still rejected when RELAYTV_API_TOKEN is set.
    lan_client = TestClient(create_app(testing=False), client=("192.168.1.99", 54321))
    remote_attempt = lan_client.post(
        "/x11/overlay/client_state",
        headers={"X-RelayTV-Overlay-Token": api_auth.overlay_client_token()},
        json={"state": "connected"},
    )
    assert remote_attempt.status_code == 401


def test_resume_session_reresolves_dead_postlive_url_and_auto_update_in_settings_bus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "RELAYTV_YTDLP_AUTO_UPDATE" in SETTINGS_BUS_VARS

    play_now_calls: list[dict[str, object]] = []
    monkeypatch.setattr(routes.player, "is_playing", lambda: False)
    monkeypatch.setattr(routes.state, "SESSION_STATE", "closed", raising=False)
    monkeypatch.setattr(
        routes.state,
        "NOW_PLAYING",
        {
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "stream": "http://127.0.0.1:8787/postlive/0123456789abcdef0123456789abcdef.mkv",
            "_resolved_stream": "http://127.0.0.1:8787/postlive/0123456789abcdef0123456789abcdef.mkv",
            "resume_pos": 15.0,
            "closed": True,
        },
        raising=False,
    )

    def _fake_play_now(item, **kwargs):
        play_now_calls.append({"item": dict(item), **kwargs})
        resumed = {"url": item["url"], "stream": "https://cdn.example.com/fresh.mp4", "closed": False}
        routes.state.NOW_PLAYING = resumed
        routes.state.SESSION_STATE = "playing"
        return resumed

    monkeypatch.setattr(playback_service, "play_now", _fake_play_now)
    monkeypatch.setattr(
        routes.player,
        "start_mpv",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("must not start mpv with dead postlive relay URL")
        ),
    )

    client = TestClient(create_app(testing=True))
    resp = client.post("/playback/play")
    assert resp.status_code == 200
    assert resp.json()["action"] == "resume_session"
    assert len(play_now_calls) == 1
    assert "stream" not in play_now_calls[0]["item"]
    assert "_resolved_stream" not in play_now_calls[0]["item"]
