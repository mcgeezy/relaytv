# SPDX-License-Identifier: GPL-3.0-only
"""Tests for shared RTSP/RTSPS link playback, security boundaries, and hygiene."""

from __future__ import annotations

import json
import subprocess

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from relaytv_app import (
    config,
    peers,
    playback_service,
    player,
    public_media,
    qt_shell_app,
    resolver,
    state,
    url_boundary,
)
from relaytv_app.config import runtime_config
from relaytv_app.integrations import jellyfin_receiver, jellyfin_service
from relaytv_app.main import create_app
from relaytv_app import routes as app_routes
from relaytv_app.routes import playback as playback_routes
from relaytv_app.routes import queue as queue_routes


@pytest.fixture(autouse=True)
def _reset_rtsp_env_config(monkeypatch):
    monkeypatch.delenv("RELAYTV_RTSP_TRANSPORT", raising=False)
    monkeypatch.delenv("MPV_ARGS", raising=False)
    monkeypatch.delenv("RELAYTV_QT_SHELL_MPV_ARGS", raising=False)
    monkeypatch.delenv("RELAYTV_QT_EXTERNAL_MPV_ARGS", raising=False)
    monkeypatch.delenv("RELAYTV_MPV_YTDL", raising=False)
    runtime_config.refresh_from_env()
    yield
    runtime_config.refresh_from_env()


# --- 1. URL extraction, validation, and provider classification --------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "Check driveway cam: rtsp://192.168.1.50:554/front_door",
            "rtsp://192.168.1.50:554/front_door",
        ),
        (
            "Protect stream <RTSPS://nvr.lan:7441/abc123?enableSrtp>",
            "RTSPS://nvr.lan:7441/abc123?enableSrtp>",
        ),
        (
            "Watch RTSP://go2rtc.lan:8554/porch.",
            "RTSP://go2rtc.lan:8554/porch.",
        ),
    ],
)
def test_extract_first_url_finds_rtsp_and_rtsps(raw: str, expected: str) -> None:
    assert resolver.extract_first_url(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("rtsp://192.168.1.50:554/stream1", "rtsp://192.168.1.50:554/stream1"),
        ("rtsps://nvr.lan:7441/front_door?enableSrtp", "rtsps://nvr.lan:7441/front_door?enableSrtp"),
        (
            "rtsp://admin:secret@192.168.1.50:554/front_door",
            "rtsp://admin:secret@192.168.1.50:554/front_door",
        ),
        ("rtsp://[fd00::10]:554/live", "rtsp://[fd00::10]:554/live"),
        (
            "RTSP://Admin:Secret@Cam.Local:554/Live?Token=AbC",
            "rtsp://Admin:Secret@Cam.Local:554/Live?Token=AbC",
        ),
        ("RTSPS://NVR.Local:7441/StreamOne", "rtsps://NVR.Local:7441/StreamOne"),
    ],
)
def test_validate_user_url_accepts_and_normalizes_rtsp_and_rtsps_scheme_case(
    raw: str, expected: str
) -> None:
    assert resolver.validate_user_url(raw) == expected


@pytest.mark.parametrize(
    "bad_url,expected_detail_fragment",
    [
        ("file:///etc/passwd", "Unsupported URL scheme (http, https, rtsp, or rtsps only)"),
        ("edl://%12%file:///etc/passwd", "Unsupported URL scheme (http, https, rtsp, or rtsps only)"),
        ("av://lavfi/testsrc", "Unsupported URL scheme (http, https, rtsp, or rtsps only)"),
        ("av://lavfi:testsrc", "Invalid url"),
        ("rtmp://192.168.1.50/live/stream", "Unsupported URL scheme (http, https, rtsp, or rtsps only)"),
        ("rtsp://169.254.169.254/latest/meta-data", "Unsupported URL host"),
        ("rtsp://0.0.0.0:554/live", "Unsupported URL host"),
        ("rtsp://224.0.0.1:554/multicast", "Unsupported URL host"),
        ("rtsp://cam.lan:99999/live", "Invalid url"),
        ("rtsp://cam.lan:notaport/live", "Invalid url"),
    ],
)
def test_validate_user_url_still_rejects_unsafe_schemes_hosts_and_ports(
    bad_url: str, expected_detail_fragment: str
) -> None:
    with pytest.raises(HTTPException) as excinfo:
        resolver.validate_user_url(bad_url)
    assert excinfo.value.status_code == 400
    assert expected_detail_fragment in str(excinfo.value.detail)


@pytest.mark.parametrize(
    "url",
    [
        "rtsp://192.168.1.50:554/stream1",
        "rtsps://nvr.lan:7441/front_door",
        "RTSP://go2rtc.lan:8554/cam",
    ],
)
def test_resolved_stream_and_outbound_http_boundaries_still_reject_rtsp(url: str) -> None:
    """Revert-proof boundary check: external resolvers and HTTP probes cannot pivot to RTSP."""
    with pytest.raises(HTTPException) as excinfo:
        resolver.validate_resolved_stream_url(url)
    assert excinfo.value.status_code == 400
    assert url_boundary.is_allowed_outbound_http_url(url) is False


def test_provider_classification_and_display_name_for_rtsp() -> None:
    assert resolver.provider_from_url("rtsp://192.168.1.50:554/front_door") == "rtsp"
    assert resolver.provider_from_url("rtsps://nvr.lan:7441/cam1") == "rtsp"
    assert resolver.provider_from_url("RTSP://youtube.com/live") == "rtsp"
    assert resolver._provider_display_name("rtsp") == "RTSP"


def test_make_item_rtsp_skips_ytdlp_and_builds_clean_live_item(monkeypatch) -> None:
    calls: list[object] = []

    def _forbid_subprocess(*args, **kwargs):
        calls.append(args)
        raise AssertionError("yt-dlp/subprocess must never run for RTSP items")

    monkeypatch.setattr(subprocess, "run", _forbid_subprocess)

    item = resolver.make_item("rtsp://admin:secret@192.168.1.50:554/front_door")
    assert calls == []
    assert item["provider"] == "rtsp"
    assert item["is_live"] is True
    assert "_metadata_lightweight" not in item
    assert item["url"] == "rtsp://admin:secret@192.168.1.50:554/front_door"
    assert "admin" not in item["title"]
    assert "secret" not in item["title"]
    assert item["title"] == "front door · 192.168.1.50"

    generic = resolver.make_item("rtsp://admin:secret@192.168.1.50:554/stream1", lightweight=True)
    assert generic["title"] == "RTSP · 192.168.1.50"
    assert generic["is_live"] is True

    nested_generic = resolver.make_item("rtsp://192.168.1.50:554/backyard/live")
    assert nested_generic["title"] == "backyard · 192.168.1.50"

    query_auth = resolver.make_item(
        "rtsp://192.168.1.50:554/?user=admin&password=secret"
    )
    assert query_auth["title"] == "RTSP · 192.168.1.50"
    assert "admin" not in query_auth["title"]
    assert "secret" not in query_auth["title"]

    assert resolver.enrich_item_metadata(dict(item)) is False
    assert resolver.resolve_title(item["url"]) == "front door · 192.168.1.50"
    assert calls == []


# --- 2. Public redaction, log hygiene, toast hygiene, and peer safety --------


def test_public_media_redacts_rtsp_userinfo_query_auth_and_string_fields() -> None:
    raw = "rtsp://admin:s3cr3t@192.168.1.50:554/cam1?user=camu&password=camp&pwd=p2&channel=1"
    sanitized = public_media.sanitize_public_url(raw)
    assert sanitized == "rtsp://192.168.1.50:554/cam1?channel=1"
    for secret in ("admin", "s3cr3t", "camu", "camp", "p2"):
        assert secret not in sanitized

    item = {
        "provider": "rtsp",
        "url": raw,
        "input": raw,
        "title": "rtsps://u:pass123@nvr.lan:7441/live?usr=bob&passwd=pw9&subtype=0",
        "is_live": True,
    }
    pub = public_media.public_media_item(item)
    assert pub["url"] == "rtsp://192.168.1.50:554/cam1?channel=1"
    assert pub["input"] == "rtsp://192.168.1.50:554/cam1?channel=1"
    assert pub["title"] == "rtsps://nvr.lan:7441/live?subtype=0"
    serialized = json.dumps(pub)
    for secret in ("s3cr3t", "pass123", "bob", "pw9", "camu", "camp"):
        assert secret not in serialized


def test_credential_detection_helper_avoids_false_positives() -> None:
    assert public_media.url_has_credentials("rtsp://admin:secret@192.168.1.50:554/live") is True
    assert public_media.url_has_credentials("rtsp://192.168.1.50:554/live?user=admin") is True
    assert public_media.url_has_credentials("rtsp://192.168.1.50:554/live?password=abc") is True
    assert public_media.url_has_credentials("rtsp://go2rtc.lan:8554/front_door") is False
    assert (
        public_media.url_has_credentials("RTSP://GO2RTC.LAN:8554/front_door?channel=1&subtype=0")
        is False
    )


def test_redacted_mpv_args_redacts_rtsp_and_rtsps_urls() -> None:
    args = [
        "mpv",
        "--rtsp-transport=tcp",
        "--audio-file=rtsp://admin:secret@192.168.1.50:554/audio",
        "--",
        "rtsps://admin:secret@nvr.lan:7441/front_door?password=pw",
    ]
    out = player._redacted_mpv_args(args)
    assert "admin" not in out
    assert "secret" not in out
    assert "password" not in out
    assert "<media-url>" in out
    assert "--audio-file=<media-url>" in out
    assert "--rtsp-transport=tcp" in out


def test_peers_block_credentialed_rtsp_and_allow_lan_relay() -> None:
    cred_userinfo = {
        "provider": "rtsp",
        "url": "rtsp://admin:secret@192.168.1.50:554/front_door",
    }
    cred_query = {
        "provider": "rtsp",
        "title": "Backyard",
        "url": "rtsp://192.168.1.50:554/cam?username=u&password=pw",
    }
    lan_relay = {
        "provider": "rtsp",
        "title": "front door · go2rtc.lan",
        "url": "rtsp://go2rtc.lan:8554/front_door",
        "is_live": True,
    }

    assert peers.wire_item(cred_userinfo) is None
    assert peers.wire_item(cred_query) is None
    wired_relay = peers.wire_item(lan_relay)
    assert wired_relay is not None
    assert wired_relay["url"] == "rtsp://go2rtc.lan:8554/front_door"

    entries, skipped, sources = peers.wire_entries([cred_userinfo, cred_query, lan_relay])
    assert [e["url"] for e in entries] == ["rtsp://go2rtc.lan:8554/front_door"]
    assert sources == [lan_relay]
    assert len(skipped) == 2
    assert all(s["reason"] == "rtsp_credentials_stay_on_this_device" for s in skipped)
    assert "secret" not in json.dumps(skipped)
    assert "admin" not in json.dumps(skipped)


def test_handoff_snapshot_blocks_credentialed_rtsp_and_allows_unauthenticated(monkeypatch) -> None:
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(player, "mpv_get", lambda prop: 15.0 if prop == "time-pos" else None)

    monkeypatch.setattr(
        state,
        "NOW_PLAYING",
        {
            "provider": "rtsp",
            "title": "front door · 192.168.1.50",
            "url": "rtsp://admin:secret@192.168.1.50:554/front_door",
        },
        raising=False,
    )
    assert playback_service.handoff_snapshot() is None

    monkeypatch.setattr(
        state,
        "NOW_PLAYING",
        {
            "provider": "rtsp",
            "title": "front door · go2rtc.lan",
            "url": "rtsp://go2rtc.lan:8554/front_door",
        },
        raising=False,
    )
    snap = playback_service.handoff_snapshot()
    assert snap is not None
    assert snap["item"]["url"] == "rtsp://go2rtc.lan:8554/front_door"
    assert snap["position"] is None


# --- 3. Config, player lifecycle, seamless loadfile, and stall guards --------


def test_rtsp_transport_config_and_mpv_arg_overrides(monkeypatch) -> None:
    assert config.normalize_rtsp_transport(None) == "tcp"
    args_default = player._build_mpv_args("rtsp://192.168.1.50:554/front_door", None, "x11")
    assert "--rtsp-transport=tcp" in args_default
    assert "--ytdl=yes" in args_default
    assert args_default[-4:] == ["--{", "--ytdl=no", "rtsp://192.168.1.50:554/front_door", "--}"]

    qt_args_default = qt_shell_app._build_mpv_args(
        "rtsp://192.168.1.50:554/front_door", wid=1, ytdl_enabled=True
    )
    assert "--rtsp-transport=tcp" in qt_args_default
    assert "--ytdl=yes" in qt_args_default
    assert qt_args_default[-4:] == ["--{", "--ytdl=no", "rtsp://192.168.1.50:554/front_door", "--}"]

    monkeypatch.setenv("RELAYTV_RTSP_TRANSPORT", "udp")
    assert config.normalize_rtsp_transport("udp") == "udp"
    assert "--rtsp-transport=udp" in player._build_mpv_args(
        "rtsp://192.168.1.50:554/front_door", None, "x11"
    )
    assert "--rtsp-transport=udp" in qt_shell_app._build_mpv_args(
        "rtsp://192.168.1.50:554/front_door", wid=1
    )

    # Empty string defers to mpv's built-in default.
    monkeypatch.setenv("RELAYTV_RTSP_TRANSPORT", "")
    assert config.normalize_rtsp_transport("") == ""
    assert not any(
        a.startswith("--rtsp-transport")
        for a in player._build_mpv_args("rtsp://192.168.1.50:554/front_door", None, "x11")
    )
    assert not any(
        a.startswith("--rtsp-transport")
        for a in qt_shell_app._build_mpv_args("rtsp://192.168.1.50:554/front_door", wid=1)
    )

    # Unrecognized value falls back safely to tcp.
    monkeypatch.setenv("RELAYTV_RTSP_TRANSPORT", "bogus")
    assert config.normalize_rtsp_transport("bogus") == "tcp"

    # Explicit MPV_ARGS wins over RELAYTV_RTSP_TRANSPORT.
    monkeypatch.setenv("RELAYTV_RTSP_TRANSPORT", "tcp")
    monkeypatch.setenv("MPV_ARGS", "--rtsp-transport=http")
    args_override = player._build_mpv_args("rtsp://192.168.1.50:554/front_door", None, "x11")
    assert "--rtsp-transport=http" in args_override
    assert "--rtsp-transport=tcp" not in args_override
    qt_args_override = qt_shell_app._build_mpv_args(
        "rtsp://192.168.1.50:554/front_door", wid=1
    )
    assert "--rtsp-transport=http" in qt_args_override
    assert "--rtsp-transport=tcp" not in qt_args_override

    # Explicit RELAYTV_QT_EXTERNAL_MPV_ARGS override wins over RELAYTV_RTSP_TRANSPORT.
    monkeypatch.delenv("MPV_ARGS", raising=False)
    monkeypatch.setenv("RELAYTV_RTSP_TRANSPORT", "tcp")
    monkeypatch.setenv("RELAYTV_QT_EXTERNAL_MPV_ARGS", "--rtsp-transport=udp --vo=gpu")
    ext_args = player._build_qt_external_mpv_args("rtsp://192.168.1.50:554/front_door", None)
    assert "--rtsp-transport=udp" in ext_args
    assert "--rtsp-transport=tcp" not in ext_args
    assert ext_args[-4:] == ["--{", "--ytdl=no", "rtsp://192.168.1.50:554/front_door", "--}"]


def test_qt_libmpv_load_stream_and_rtsp_transport_option(monkeypatch) -> None:
    lib_player = qt_shell_app._QtLibMpvPlayer(debug=False)
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(lib_player, "_command", lambda *parts: commands.append(parts))

    lib_player.load_stream("rtsp://192.168.1.50:554/front_door")
    assert commands[-1] == ("loadfile", "rtsp://192.168.1.50:554/front_door", "replace", "-1", "ytdl=no")

    lib_player.load_stream("rtsps://nvr.lan:7441/cam")
    assert commands[-1] == ("loadfile", "rtsps://nvr.lan:7441/cam", "replace", "-1", "ytdl=no")

    lib_player.load_stream("https://example.com/video.mp4")
    assert commands[-1] == ("loadfile", "https://example.com/video.mp4", "replace")


def test_qt_libmpv_start_sets_rtsp_transport_and_honors_extra_override(monkeypatch) -> None:
    class _FakeLib:
        def mpv_create(self):
            return 1234

        def mpv_initialize(self, _handle):
            return 0

    def _run_start() -> list[tuple[str, str]]:
        opts: list[tuple[str, str]] = []
        lib_player = qt_shell_app._QtLibMpvPlayer(debug=False)
        monkeypatch.setattr(lib_player, "_bind_api", lambda: setattr(lib_player, "_lib", _FakeLib()))
        monkeypatch.setattr(lib_player, "_set_opt", lambda k, v: opts.append((k, v)))
        monkeypatch.setattr(lib_player, "_set_opt_best_effort", lambda k, v: opts.append((k, v)))
        lib_player.start(
            stream="rtsp://192.168.1.50:554/front_door",
            audio=None,
            start_pos=None,
            ipc_path=None,
            audio_device=None,
            sub_lang=None,
            volume=100.0,
            ytdl_enabled=False,
            ytdl_path=None,
            ytdl_format=None,
            ytdl_raw_options=None,
            user_agent=None,
            referrer=None,
        )
        return opts

    # Default tcp
    opts_default = _run_start()
    assert ("rtsp-transport", "tcp") in opts_default

    # Empty string omits option
    monkeypatch.setenv("RELAYTV_RTSP_TRANSPORT", "")
    runtime_config.refresh_from_env()
    opts_empty = _run_start()
    assert not any(k == "rtsp-transport" for k, _ in opts_empty)

    # Extra MPV_ARGS override applied after default so it wins
    monkeypatch.setenv("RELAYTV_RTSP_TRANSPORT", "tcp")
    monkeypatch.setenv("MPV_ARGS", "--rtsp-transport=udp")
    runtime_config.refresh_from_env()
    opts_override = _run_start()
    rtsp_vals = [v for k, v in opts_override if k == "rtsp-transport"]
    assert rtsp_vals == ["tcp", "udp"]


def test_play_item_rtsp_bypasses_resolver_drops_start_pos_and_sanitizes_logs(monkeypatch) -> None:
    monkeypatch.setenv("RELAYTV_MPV_YTDL", "0")
    resolve_calls: list[str] = []
    start_calls: list[dict[str, object]] = []
    debug_messages: list[str] = []

    monkeypatch.setattr(
        player, "resolve_streams", lambda url: resolve_calls.append(url) or (url, None)
    )
    monkeypatch.setattr(player, "_load_stream_in_existing_mpv", lambda *a, **kw: False)
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: False)
    monkeypatch.setattr(
        player,
        "start_mpv",
        lambda stream, audio_url=None, start_pos=None, **kw: start_calls.append(
            {"stream": stream, "audio_url": audio_url, "start_pos": start_pos, **kw}
        ),
    )
    monkeypatch.setattr(player, "_arm_playback_start_watchdog", lambda *a, **kw: None)
    monkeypatch.setattr(player, "_add_history_entry", lambda *a, **kw: None)
    monkeypatch.setattr(player, "update_history_progress", lambda *a, **kw: None)
    monkeypatch.setattr(player, "debug_log", lambda cat, msg: debug_messages.append(msg))
    monkeypatch.setattr(state, "persist_queue", lambda: None)
    monkeypatch.setattr(state, "update_session", lambda **kw: None)

    url = "rtsp://admin:supersecret@192.168.1.50:554/front_door?password=qsecret"
    now = player.play_item(
        url,
        use_resolver=True,
        cec=False,
        clear_queue=False,
        mode="play",
        start_pos=45.0,
    )

    assert resolve_calls == []
    assert len(start_calls) == 1
    assert start_calls[0]["stream"] == url
    assert start_calls[0]["start_pos"] is None
    assert now["provider"] == "rtsp"
    assert now["is_live"] is True
    assert "resume_pos" not in now
    assert now["_playback_started_pos"] == 0.0
    assert "supersecret" not in "\n".join(debug_messages)
    assert "qsecret" not in "\n".join(debug_messages)


def test_seamless_replace_passes_per_file_ytdl_no_for_rtsp(monkeypatch) -> None:
    sent_cmds: list[list[object]] = []

    class _AliveProc:
        def poll(self):
            return None

    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: False)
    monkeypatch.setattr(player, "_qt_shell_runtime_accepts_mpv_commands", lambda: False)
    monkeypatch.setattr(player, "MPV_PROC", _AliveProc(), raising=False)
    monkeypatch.setattr(player.os.path, "exists", lambda path: True)
    monkeypatch.setattr(
        player, "mpv_command", lambda cmd: sent_cmds.append(list(cmd)) or {"error": "success"}
    )

    ok = player._load_stream_in_existing_mpv("rtsp://192.168.1.50:554/front_door")
    assert ok is True
    assert sent_cmds[-1] == [
        "loadfile",
        "rtsp://192.168.1.50:554/front_door",
        "replace",
        "-1",
        "ytdl=no",
    ]


def test_rtsp_live_prefetch_up_next_and_stall_guards(monkeypatch) -> None:
    rtsp_item = {
        "provider": "rtsp",
        "url": "rtsp://192.168.1.50:554/front_door",
        "title": "front door · 192.168.1.50",
        "is_live": True,
        "started": 1000,
        "resume_pos": 10.0,
    }
    bare_rtsp_item = {"url": "rtsp://192.168.1.50:554"}
    yt_live_item = {
        "provider": "youtube",
        "url": "https://www.youtube.com/watch?v=live123",
        "title": "Live Show",
        "is_live": True,
        "live_status": "is_live",
        "started": 1000,
        "resume_pos": 10.0,
    }

    assert player._item_looks_like_live_stream(rtsp_item) is True
    assert player._item_looks_like_live_stream(bare_rtsp_item) is True
    assert player._item_supports_metadata_prefetch(rtsp_item) is False
    assert player._mpv_up_next_eligible_item(rtsp_item) is False
    assert player._item_is_completable_live_stream(rtsp_item) is False
    assert player._item_is_completable_live_stream(bare_rtsp_item) is False
    assert player._item_is_completable_live_stream(yt_live_item) is True

    # Revert-proof stall check: identical core-idle stall past the stall
    # threshold triggers _live_stream_end_stalled() for YouTube live, but must
    # NOT trigger for RTSP.
    fake_now = [1000.0]
    stop_cmds: list[list[object]] = []
    monkeypatch.setattr(player.time, "monotonic", lambda: fake_now[0])
    monkeypatch.setattr(player, "playback_transitioning", lambda: False)
    monkeypatch.setattr(player, "auto_next_transitioning", lambda: False)
    monkeypatch.setattr(
        player, "mpv_command", lambda cmd: stop_cmds.append(list(cmd)) or {"error": "success"}
    )
    stalled_props = {
        "core-idle": True,
        "eof-reached": False,
        "pause": False,
        "path": "rtsp://192.168.1.50:554/front_door",
        "time-pos": 10.0,
    }

    monkeypatch.setattr(state, "NOW_PLAYING", dict(rtsp_item), raising=False)
    player._reset_live_stream_stall_state()
    assert player._live_stream_end_stalled(rtsp_item, stalled_props) is False
    fake_now[0] += player._LIVE_STREAM_END_STALL_SEC + 5.0
    assert player._live_stream_end_stalled(rtsp_item, stalled_props) is False
    assert stop_cmds == []

    # Control: verify the same stall sequence DOES trigger on a completable live stream.
    monkeypatch.setattr(state, "NOW_PLAYING", dict(yt_live_item), raising=False)
    player._reset_live_stream_stall_state()
    yt_props = dict(stalled_props, path="https://www.youtube.com/watch?v=live123")
    assert player._live_stream_end_stalled(yt_live_item, yt_props) is False
    fake_now[0] += player._LIVE_STREAM_END_STALL_SEC + 5.0
    assert player._live_stream_end_stalled(yt_live_item, yt_props) is True
    assert stop_cmds == [["stop"]]
    player._reset_live_stream_stall_state()


def test_rtsp_preserve_resume_and_temporary_restore_drop_resume_position(monkeypatch) -> None:
    rtsp_now = {
        "provider": "rtsp",
        "url": "rtsp://192.168.1.50:554/front_door",
        "stream": "rtsp://192.168.1.50:554/front_door",
        "title": "front door · 192.168.1.50",
        "is_live": True,
        "resume_pos": 88.0,
    }
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(player, "mpv_get", lambda prop: 88.0 if prop == "time-pos" else None)
    monkeypatch.setattr(player, "update_history_progress", lambda *a, **kw: None)
    monkeypatch.setattr(state, "NOW_PLAYING", dict(rtsp_now), raising=False)

    preserved = playback_service.preserve_current_to_queue_front(capture_only=True)
    assert preserved is not None
    assert "resume_pos" not in preserved

    # resume_session must pass start_pos=None for RTSP.
    resume_loads: list[dict[str, object]] = []
    monkeypatch.setattr(player, "retire_playback_intents", lambda *a, **kw: None)
    monkeypatch.setattr(player, "note_playback_started", lambda *a, **kw: None)
    monkeypatch.setattr(
        player,
        "_load_stream_in_existing_mpv",
        lambda stream, audio_url=None, start_pos=None, **kw: resume_loads.append(
            {"stream": stream, "start_pos": start_pos}
        )
        or True,
    )
    monkeypatch.setattr(state, "update_session", lambda **kw: None)
    monkeypatch.setattr(player, "_arm_playback_start_watchdog", lambda *a, **kw: None)

    resumed, _ack = playback_service.resume_session()
    assert resume_loads == [{"stream": "rtsp://192.168.1.50:554/front_door", "start_pos": None}]
    assert resumed["_playback_started_pos"] == 0.0

    # restore_playback_state (play_temporary restore) drops snapshot position for RTSP.
    restored_calls: list[dict[str, object]] = []
    monkeypatch.setattr(state, "persist_queue", lambda: None)
    monkeypatch.setattr(
        playback_service,
        "play_now",
        lambda item, **kw: restored_calls.append({"item": item, **kw}) or dict(item),
    )
    playback_service.restore_playback_state(
        {"now_playing": dict(rtsp_now), "position": 55.0, "paused": False, "queue": []}
    )
    assert restored_calls[-1]["start_pos"] is None

    # restart_current drops time-pos for RTSP.
    restart_calls: list[dict[str, object]] = []
    monkeypatch.setattr(state, "SESSION_STATE", "playing", raising=False)
    monkeypatch.setattr(state, "NOW_PLAYING", dict(rtsp_now), raising=False)
    monkeypatch.setattr(player, "stop_mpv", lambda *a, **kw: None)
    monkeypatch.setattr(
        player,
        "play_item",
        lambda target, *a, **kw: restart_calls.append({"target": target, **kw}) or dict(rtsp_now),
    )
    player.restart_current()
    assert restart_calls[-1]["start_pos"] is None


def test_playback_routes_redact_rtsp_credentials_in_toasts_and_responses(monkeypatch) -> None:
    toasts: list[str] = []
    monkeypatch.setattr(
        playback_routes,
        "_push_overlay_toast",
        lambda **kw: toasts.append(str(kw.get("text") or "")),
    )
    monkeypatch.setattr(playback_service, "suppress_auto_next", lambda *a, **kw: None)
    monkeypatch.setattr(
        playback_service,
        "play_now",
        lambda item, **kw: {
            "provider": "rtsp",
            "url": "rtsp://admin:topsecret@192.168.1.50:554/front_door",
            "title": "",
            "is_live": True,
        },
    )
    monkeypatch.setattr(playback_routes, "_ui_event_push_queue", lambda *a, **kw: None)
    monkeypatch.setattr(playback_routes, "_capture_current_playback_state", lambda: None)
    monkeypatch.setattr(playback_routes, "_temporary_watchdog", lambda *a, **kw: None)

    client = TestClient(create_app(testing=True))
    secret_url = "rtsp://admin:topsecret@192.168.1.50:554/front_door"

    res_play_now = client.post("/play_now", json={"url": secret_url, "preserve_current": False})
    assert res_play_now.status_code == 200
    assert "topsecret" not in res_play_now.text
    assert "admin" not in res_play_now.text

    res_temp = client.post("/play_temporary", json={"url": secret_url, "timeout_sec": 5})
    assert res_temp.status_code == 200
    assert "topsecret" not in res_temp.text

    assert len(toasts) == 2
    for toast in toasts:
        assert "topsecret" not in toast
        assert "admin" not in toast
        assert "rtsp://192.168.1.50:554/front_door" in toast

    res_share_get = client.get(
        "/share", params={"url": "rtsp://192.168.1.50:554/front_door"}, follow_redirects=False
    )
    assert res_share_get.status_code == 303
    assert res_share_get.headers["location"].startswith("/ui?share=rtsp%3A%2F%2F")


# --- 4. Review regression tests (Jellyfin, embedded URLs, per-file ytdl, logs, close) ---


def test_jellyfin_url_detection_never_hijacks_rtsp_streams(monkeypatch) -> None:
    monkeypatch.setattr(
        jellyfin_receiver,
        "status",
        lambda: {"server_url": "http://192.168.1.10:8096", "enabled": True},
    )
    monkeypatch.setattr(
        jellyfin_receiver,
        "get_item_metadata",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("Jellyfin API must not be called")),
    )

    rtsp_urls = [
        "rtsp://admin:secret@192.168.1.50:554/videos/front_door",
        "rtsp://admin:secret@192.168.1.50:554/items/cam1",
        "rtsp://admin:secret@192.168.1.50:554/stream?id=1",
        "rtsps://admin:secret@jellyfin.lan:7441/live",
        "Check cam: rtsp://admin:secret@192.168.1.50:554/videos/front_door",
    ]
    for raw in rtsp_urls:
        assert jellyfin_receiver.extract_item_id_from_url(raw) == ""
        assert jellyfin_service.extract_item_id_from_url(raw) == ""
        assert jellyfin_service.extract_item_id_from_url_raw(raw) == ""
        assert jellyfin_service.looks_like_media_url(raw) is False
        assert jellyfin_service.url_origin(raw) == ""

        item = jellyfin_service.smart_item_from_url(raw)
        assert item["provider"] == "rtsp"
        assert item["is_live"] is True
        pub = public_media.public_media_item(item)
        assert "secret" not in json.dumps(pub)
        assert "admin" not in json.dumps(pub)

    # Control: HTTP and schemeless relative Jellyfin paths continue working.
    assert jellyfin_receiver.extract_item_id_from_url("http://192.168.1.10:8096/Videos/abc123/stream") == "abc123"
    assert jellyfin_receiver.extract_item_id_from_url("/Videos/abc123/stream") == "abc123"
    assert jellyfin_service.looks_like_media_url("http://192.168.1.10:8096/Videos/abc123/stream") is True
    assert jellyfin_service.url_origin("http://192.168.1.10:8096/Videos/abc123/stream") == "http://192.168.1.10:8096"


def test_prose_and_angle_wrapped_rtsp_urls_never_leak_credentials_across_routes(monkeypatch) -> None:
    toasts: list[str] = []
    monkeypatch.setattr(
        app_routes,
        "_push_overlay_toast",
        lambda **kw: toasts.append(str(kw.get("text") or "")),
    )
    monkeypatch.setattr(
        playback_routes,
        "_push_overlay_toast",
        lambda **kw: toasts.append(str(kw.get("text") or "")),
    )
    monkeypatch.setattr(playback_routes, "_ui_event_push_queue", lambda *a, **kw: None)
    monkeypatch.setattr(queue_routes, "_ui_event_push_queue", lambda *a, **kw: None)
    monkeypatch.setattr(playback_routes, "_capture_current_playback_state", lambda: None)
    monkeypatch.setattr(playback_routes, "_temporary_watchdog", lambda *a, **kw: None)
    monkeypatch.setattr(player, "_load_stream_in_existing_mpv", lambda *a, **kw: False)
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: False)
    monkeypatch.setattr(player, "start_mpv", lambda *a, **kw: None)
    monkeypatch.setattr(player, "is_playing", lambda: False)
    monkeypatch.setattr(player, "_arm_playback_start_watchdog", lambda *a, **kw: None)
    monkeypatch.setattr(player, "_add_history_entry", lambda *a, **kw: None)
    monkeypatch.setattr(player, "update_history_progress", lambda *a, **kw: None)
    monkeypatch.setattr(player, "prefetch_queue_item_stream", lambda *a, **kw: False)
    monkeypatch.setattr(player, "prime_mpv_up_next_from_queue", lambda *a, **kw: None)
    monkeypatch.setattr(state, "persist_queue", lambda: None)
    monkeypatch.setattr(state, "persist_queue_payload", lambda *a, **kw: None)
    monkeypatch.setattr(state, "QUEUE", [], raising=False)
    monkeypatch.setattr(state, "NOW_PLAYING", None, raising=False)
    monkeypatch.setattr(state, "HISTORY", [], raising=False)
    monkeypatch.setattr(state, "SESSION_STATE", "idle", raising=False)

    # Run queue toast synchronously so we capture its exact payload.
    monkeypatch.setattr(
        queue_routes,
        "_push_queue_added_toast_async",
        lambda item, fallback: app_routes._push_queue_added_toast(item, fallback),
    )

    class _NoopThread:
        def __init__(self, target=None, daemon=True, **_kw):
            self._target = target

        def start(self):
            return None

    monkeypatch.setattr(
        playback_routes,
        "_threading_module",
        lambda: type("_T", (), {"Thread": _NoopThread})(),
    )

    client = TestClient(create_app(testing=True))
    prose_url = "Front door: rtsp://admin:supersecret@192.168.1.50:554/front_door?password=qsec"
    angle_url = "<rtsps://admin:supersecret@nvr.lan:7441/porch?pwd=qsec>"

    r_play_now = client.post("/play_now", json={"url": prose_url, "preserve_current": False})
    assert r_play_now.status_code == 200
    assert "supersecret" not in r_play_now.text
    assert "qsec" not in r_play_now.text
    assert state.NOW_PLAYING["input"] == "rtsp://admin:supersecret@192.168.1.50:554/front_door?password=qsec"

    r_status = client.get("/status")
    assert r_status.status_code == 200
    assert "supersecret" not in r_status.text
    assert "qsec" not in r_status.text
    assert r_status.json()["now_playing"]["input"] == "rtsp://192.168.1.50:554/front_door"

    r_temp = client.post("/play_temporary", json={"url": angle_url, "timeout_sec": 5})
    assert r_temp.status_code == 200
    assert "supersecret" not in r_temp.text
    assert "qsec" not in r_temp.text

    r_enq = client.post("/enqueue", json={"url": prose_url})
    assert r_enq.status_code == 200
    assert "supersecret" not in r_enq.text
    assert "qsec" not in r_enq.text

    r_play_at = client.post("/play_at", json={"url": angle_url, "start_at": 9999999999.0})
    assert r_play_at.status_code == 200
    assert r_play_at.json()["url"] == "rtsps://nvr.lan:7441/porch"
    assert "supersecret" not in r_play_at.text
    assert "qsec" not in r_play_at.text

    # Also test fallback toast sanitization when item has no title and raw prose/URL fallback.
    label, _thumb = app_routes._queue_toast_payload({"url": prose_url}, prose_url)
    assert "supersecret" not in label
    assert "qsec" not in label
    assert "rtsp://192.168.1.50:554/front_door" in label

    assert toasts
    for toast in toasts:
        assert "supersecret" not in toast
        assert "qsec" not in toast
        assert "admin" not in toast


def test_rtsp_cold_start_keeps_process_ytdl_enabled_for_subsequent_seamless_loadfile(
    monkeypatch,
) -> None:
    spawned_qt_args: list[list[str]] = []

    class _DummyProc:
        pid = 9999

        def poll(self):
            return None

    monkeypatch.setattr(player, "_stop_qt_shell", lambda: None)
    monkeypatch.setattr(player, "_effective_audio_device", lambda _s=None: "")
    monkeypatch.setattr(
        player.subprocess,
        "Popen",
        lambda argv, **kw: spawned_qt_args.append(list(argv)) or _DummyProc(),
    )

    player._start_qt_shell(stream_url="rtsp://192.168.1.50:554/front_door")
    assert len(spawned_qt_args) == 1
    assert "--ytdl-enabled=1" in spawned_qt_args[0]

    # Seamless transition from RTSP back to a ytdl_hook-backed stream omits ytdl=no.
    sent_cmds: list[list[object]] = []
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: False)
    monkeypatch.setattr(player, "_qt_shell_runtime_accepts_mpv_commands", lambda: False)
    monkeypatch.setattr(player, "MPV_PROC", _DummyProc(), raising=False)
    monkeypatch.setattr(player.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(
        player, "mpv_command", lambda cmd: sent_cmds.append(list(cmd)) or {"error": "success"}
    )

    assert player._load_stream_in_existing_mpv("rtsp://192.168.1.50:554/front_door") is True
    assert sent_cmds[-1] == [
        "loadfile",
        "rtsp://192.168.1.50:554/front_door",
        "replace",
        "-1",
        "ytdl=no",
    ]

    assert player._load_stream_in_existing_mpv("https://www.youtube.com/watch?v=live123") is True
    assert sent_cmds[-1] == ["loadfile", "https://www.youtube.com/watch?v=live123", "replace"]


def test_get_mpv_log_tail_redacts_embedded_rtsp_and_http_credentials(monkeypatch, tmp_path) -> None:
    log_file = tmp_path / "mpv.log"
    log_file.write_text(
        "Playing: rtsp://admin:secret@192.168.1.50:554/stream1?password=pw\n"
        "Opening https://user:pass@media.example/video.m3u8?api_key=tok&x=1\n"
        "VO: [gpu] 1920x1080\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MPV_LOG_FILE", str(log_file))

    lines = player.get_mpv_log_tail(10)
    assert lines == [
        "Playing: <url>",
        "Opening <url>",
        "VO: [gpu] 1920x1080",
    ]
    joined = "\n".join(lines)
    for secret in ("admin", "secret", "pw", "pass", "tok"):
        assert secret not in joined


def test_close_current_and_resume_session_clear_stale_resume_pos_for_rtsp(monkeypatch) -> None:
    rtsp_now = {
        "provider": "rtsp",
        "url": "rtsp://192.168.1.50:554/front_door",
        "stream": "rtsp://192.168.1.50:554/front_door",
        "title": "front door · 192.168.1.50",
        "is_live": True,
        "resume_pos": 42.0,
    }
    updates: list[dict[str, object]] = []
    monkeypatch.setattr(state, "NOW_PLAYING", dict(rtsp_now), raising=False)
    monkeypatch.setattr(state, "SESSION_STATE", "playing", raising=False)
    monkeypatch.setattr(state, "update_session", lambda **kw: updates.append(dict(kw)))
    monkeypatch.setattr(player, "retire_playback_intents", lambda *a, **kw: None)
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(player, "mpv_get", lambda prop: 123.4 if prop == "time-pos" else None)
    monkeypatch.setattr(player, "stop_mpv", lambda *a, **kw: None)
    monkeypatch.setattr(player, "update_history_progress", lambda *a, **kw: None)
    monkeypatch.setattr(player, "_emit_plex_timeline_from_now", lambda *a, **kw: None)

    result = playback_service.close_current(idle_surface_enabled=False, keep_shell_allowed=False)
    assert result["preserve_resume"] is True
    assert result["position"] is None
    assert updates[-1]["session_position"] is None
    assert "resume_pos" not in updates[-1]["now_playing"]
    assert updates[-1]["now_playing"]["closed"] is True

    # Even if an RTSP item on disk carried a stale resume_pos, resume_session clears it.
    monkeypatch.setattr(state, "NOW_PLAYING", dict(rtsp_now), raising=False)
    monkeypatch.setattr(player, "note_playback_started", lambda *a, **kw: None)
    monkeypatch.setattr(player, "_load_stream_in_existing_mpv", lambda *a, **kw: True)
    monkeypatch.setattr(player, "_arm_playback_start_watchdog", lambda *a, **kw: None)

    resumed, _ack = playback_service.resume_session()
    assert "resume_pos" not in resumed
    assert resumed["_playback_started_pos"] == 0.0


def test_hostless_urls_and_svg_data_uris_do_not_recurse_or_crash_routes() -> None:
    assert public_media.sanitize_public_url("rtsp:///stream1") == ""
    assert public_media.sanitize_public_url("http:///path") == ""
    assert public_media.sanitize_public_url("<rtsp:///stream1>") == "<>"
    assert public_media.sanitize_public_url("text http:///path") == "text "
    assert (
        public_media.sanitize_embedded_urls("Cam1: rtsp://admin:secret@192.168.1.50:554/live")
        == "Cam1: rtsp://192.168.1.50:554/live"
    )
    svg_uri = 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    assert public_media.sanitize_public_url(svg_uri) == svg_uri
    assert public_media.public_media_item({"title": "Cam", "thumbnail": svg_uri})["thumbnail"] == svg_uri

    client = TestClient(create_app(testing=True))
    resp = client.post("/play_temporary", json={"url": "rtsp:///stream1", "timeout_sec": 5})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid url host"

