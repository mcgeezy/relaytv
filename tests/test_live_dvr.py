# SPDX-License-Identifier: GPL-3.0-only
"""Cache boundaries and real mpv seek behavior for live DVR."""
import json
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from relaytv_app import player, routes
from relaytv_app.main import create_app


@pytest.mark.parametrize("ranges", [None, [], {}, 5, [{"start": 20, "end": 10}],
                                    [{"start": 0, "end": float("inf")}],
                                    [{"start": 5, "end": 5}]])
def test_duration_and_cache_end_do_not_create_seekable_dvr(ranges):
    dvr = player.extract_dvr_status(
        {"seekable-ranges": ranges, "cache-end": 300}, pos=100, dur=400, is_live=True,
    )
    assert dvr["seekable"] is False
    assert dvr["buffer_start"] is None
    assert dvr["live_edge"] is None
    assert dvr["window_sec"] == 0


def test_dvr_uses_latest_contiguous_range_without_rounding_outward():
    dvr = player.extract_dvr_status({
        "seekable-ranges": [
            {"start": 275, "end": 300.006},
            {"start": 100, "end": 150},
            {"start": 250.004, "end": 280},
        ],
        "cache-end": 350,
    }, pos=290, dur=400, is_live=True)
    assert dvr["seekable"] is True
    assert dvr["buffer_start"] == 250.004
    assert dvr["buffer_end"] == dvr["live_edge"] == 300.006
    # Every scrubber position must remain within the latest contiguous range.
    for pct in [0, .25, .5, .75, 1]:
        target = dvr["buffer_start"] + pct * (dvr["live_edge"] - dvr["buffer_start"])
        assert 250.004 <= target <= 300.006


@pytest.mark.parametrize(("path", "body", "target"), [
    ("/seek_abs", {"sec": 125}, 250),  # Old cached range or stale UI bounds.
    ("/seek_abs", {"sec": 200}, 250),  # Gap between ranges.
    ("/seek_abs", {"sec": 400}, 299.5),  # Duration exceeds decodable cache.
    ("/seek", {"sec": -200}, 250),
    ("/seek", {"sec": 200}, 299.5),
    ("/playback/live", {}, 299.5),
])
def test_live_seek_routes_stay_inside_current_cache(monkeypatch, path, body, target):
    commands = []
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(player, "_mark_playback_transition", lambda *_: None)
    monkeypatch.setattr(routes.state, "NOW_PLAYING", {"is_live": True})
    monkeypatch.setattr(player, "mpv_get_many", lambda props: {
        "time-pos": 275, "duration": 400,
        "demuxer-cache-state": {"seekable-ranges": [
            {"start": 100, "end": 150}, {"start": 250, "end": 300},
        ], "cache-end": 350},
    })
    monkeypatch.setattr(player, "mpv_command", lambda cmd: commands.append(cmd) or {"error": "success"})
    response = TestClient(create_app(testing=True)).post(path, json=body)
    assert response.status_code == 200
    assert commands == [["seek", target, "absolute+exact"]]


@pytest.mark.parametrize(("path", "body"), [
    ("/playback/live", {}), ("/seek_abs", {"live": True}),
    ("/seek_abs", {"sec": 25}), ("/seek", {"sec": 15}),
])
def test_live_seek_without_cache_returns_conflict(monkeypatch, path, body):
    commands = []
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(player, "_mark_playback_transition", lambda *_: None)
    monkeypatch.setattr(routes.state, "NOW_PLAYING", {"is_live": True})
    monkeypatch.setattr(player, "mpv_get_many", lambda props: {"time-pos": 20, "duration": 100})
    monkeypatch.setattr(player, "mpv_command", lambda cmd: commands.append(cmd) or {"error": "success"})
    response = TestClient(create_app(testing=True)).post(path, json=body)
    assert response.status_code == 409
    assert commands == []


@pytest.mark.parametrize("position", [None, "unavailable", float("nan"), float("inf")])
@pytest.mark.parametrize("has_cache", [False, True])
@pytest.mark.parametrize("delta", [-60, 60])
def test_relative_live_seek_requires_position(monkeypatch, position, has_cache, delta):
    commands = []
    props = {"time-pos": position}
    if has_cache:
        props["demuxer-cache-state"] = {
            "seekable-ranges": [{"start": 100.0, "end": 200.0}],
        }
    monkeypatch.setattr(routes.state, "NOW_PLAYING", {"is_live": True})
    monkeypatch.setattr(routes.state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0)
    monkeypatch.setattr(player, "_mark_playback_transition", lambda *_: None)
    monkeypatch.setattr(player, "mpv_get_many", lambda _: props)
    monkeypatch.setattr(
        player, "mpv_command", lambda cmd: commands.append(cmd) or {"error": "success"}
    )

    response = TestClient(create_app(testing=True)).post("/seek", json={"sec": delta})

    assert response.status_code == 409
    assert "position" in response.json()["detail"].lower()
    assert commands == []


def test_relative_live_seek_rejects_failed_position_query(monkeypatch):
    commands = []

    def failed_query(_):
        raise RuntimeError("player telemetry unavailable")

    monkeypatch.setattr(routes.state, "NOW_PLAYING", {"provider": "iptv"})
    monkeypatch.setattr(routes.state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0)
    monkeypatch.setattr(player, "_mark_playback_transition", lambda *_: None)
    monkeypatch.setattr(player, "mpv_get_many", failed_query)
    monkeypatch.setattr(
        player, "mpv_command", lambda cmd: commands.append(cmd) or {"error": "success"}
    )

    response = TestClient(create_app(testing=True)).post("/seek", json={"sec": -60})

    assert response.status_code == 409
    assert commands == []


@pytest.mark.parametrize("delta", ["1e999", "-1e999"])
def test_relative_live_seek_rejects_nonfinite_offset(monkeypatch, delta):
    commands = []
    monkeypatch.setattr(routes.state, "NOW_PLAYING", {"is_live": True})
    monkeypatch.setattr(routes.state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0)
    monkeypatch.setattr(player, "_mark_playback_transition", lambda *_: None)
    monkeypatch.setattr(
        player, "mpv_command", lambda cmd: commands.append(cmd) or {"error": "success"}
    )

    response = TestClient(create_app(testing=True)).post(
        "/seek", content='{"sec":' + delta + '}', headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert commands == []


@pytest.mark.parametrize("delta", [-60, 60])
def test_relative_vod_seek_keeps_positionless_fallback(monkeypatch, delta):
    commands = []
    monkeypatch.setattr(routes.state, "NOW_PLAYING", {"url": "https://example.test/movie.mp4"})
    monkeypatch.setattr(routes.state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0)
    monkeypatch.setattr(player, "_mark_playback_transition", lambda *_: None)
    monkeypatch.setattr(player, "mpv_get_many", lambda _: {})
    monkeypatch.setattr(
        player, "mpv_command", lambda cmd: commands.append(cmd) or {"error": "success"}
    )

    response = TestClient(create_app(testing=True)).post("/seek", json={"sec": delta})

    assert response.status_code == 200
    assert commands == [["seek", delta, "relative"]]


@pytest.mark.skipif(not shutil.which("mpv") or not shutil.which("ffmpeg"),
                    reason="live transport integration needs mpv and ffmpeg")
def test_live_route_seeks_real_http_mpegts_cache(monkeypatch, tmp_path):
    """A successful IPC ack alone does not prove mpv reached the live edge."""
    ff = subprocess.Popen([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi",
        "-i", "testsrc2=size=160x90:rate=10", "-c:v", "mpeg2video", "-g", "10",
        "-f", "mpegts", "-",
    ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "video/MP2T")
            self.end_headers()
            try:
                while chunk := ff.stdout.read(1316):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ipc = str(tmp_path / "mpv.sock")
    process = None
    sock = None
    wire = None
    try:
        process = subprocess.Popen([
            "mpv", "--no-config", "--vo=null", "--ao=null", "--idle=yes",
            "--cache=yes", "--cache-pause=no", "--force-seekable=yes",
            "--demuxer-seekable-cache=yes", "--demuxer-max-back-bytes=256MiB",
            "--input-ipc-server=" + ipc,
            f"http://127.0.0.1:{server.server_port}/live.ts",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def wait_for(check, timeout=15):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                value = check()
                if value:
                    return value
                assert process.poll() is None, "mpv exited before completing the seek"
                time.sleep(.05)
            pytest.fail("mpv did not reach the expected playback position")

        wait_for(lambda: (tmp_path / "mpv.sock").exists())
        sock = socket.socket(socket.AF_UNIX)
        sock.settimeout(5)
        sock.connect(ipc)
        wire = sock.makefile("rwb", buffering=0)
        command_lock = threading.Lock()
        sequence = 0

        def command(args):
            nonlocal sequence
            with command_lock:
                sequence += 1
                wire.write((json.dumps({"command": args, "request_id": sequence}) + "\n").encode())
                while True:
                    response = json.loads(wire.readline())
                    if response.get("request_id") == sequence:
                        return response

        def props(names):
            return {name: command(["get_property", name]).get("data") for name in names}

        def position():
            return props(["time-pos"])["time-pos"] or 0

        wait_for(lambda: position() >= 7)
        monkeypatch.setattr(player, "mpv_command", command)
        monkeypatch.setattr(player, "mpv_get_many", props)
        monkeypatch.setattr(player, "is_playing", lambda: True)
        monkeypatch.setattr(player, "_mark_playback_transition", lambda *_: None)
        monkeypatch.setattr(routes.state, "NOW_PLAYING", {"is_live": True})
        client = TestClient(create_app(testing=True))
        for paused in (True, False):
            assert command(["set_property", "pause", paused])["error"] == "success"
            assert client.post("/seek_abs", json={"sec": 2}).status_code == 200
            wait_for(lambda: 1.9 <= position() < 4)
            cache = props(["demuxer-cache-state"])["demuxer-cache-state"]
            edge = max(r["end"] for r in cache["seekable-ranges"])
            low_level_seeks = cache["debug-low-level-seeks"]
            assert client.post("/playback/live").status_code == 200
            wait_for(lambda: position() >= edge - .75, timeout=3)
            # Allow queued seek processing to finish; the broken percentage seek
            # briefly reported the end before falling back to the beginning.
            time.sleep(.5)
            assert position() >= edge - .75
            after = props(["demuxer-cache-state"])["demuxer-cache-state"]
            assert after["debug-low-level-seeks"] == low_level_seeks
    finally:
        if wire:
            wire.close()
        if sock:
            sock.close()
        for child in (process, ff):
            if child is not None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        ff.stdout.close()
