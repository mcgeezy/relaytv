# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from relaytv_app import container_entrypoint, resolver

ROOT_DIR = Path(__file__).resolve().parents[1]


def _clear_ytdlp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "YTDLP_ARGS",
        "YTDLP_FORMAT",
        "YTDLP_FORMAT_YOUTUBE",
        "RELAYTV_YTDLP_JS_RUNTIME",
        "YTDLP_JS_RUNTIME",
        "RELAYTV_YTDLP_USE_NODE",
        "RELAYTV_YTDLP_COOKIES",
        "YTDLP_COOKIES",
        "RELAYTV_YTDLP_COOKIES_FROM_BROWSER",
        "YTDLP_COOKIES_FROM_BROWSER",
        "RELAYTV_YOUTUBE_PROGRESSIVE_FIRST",
        "RELAYTV_ARM_ENFORCE_SAFE_YTDL_FORMAT",
    ):
        monkeypatch.delenv(key, raising=False)


def test_youtube_av1_allowed_preserves_display_cap_format_before_auto_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AV1-capable hosts must still honor height/fps caps on the primary strategy."""
    _clear_ytdlp_env(monkeypatch)
    monkeypatch.setattr("relaytv_app.state.get_settings", lambda: {"quality_mode": "auto_profile"})
    monkeypatch.setattr(
        "relaytv_app.video_profile.get_profile",
        lambda: {"decode_profile": "intel_amd64_qsv", "display_cap_height": 1080, "av1_allowed": True},
    )
    monkeypatch.setattr(resolver.shutil, "which", lambda name: "/usr/local/bin/deno" if name == "deno" else None)

    seen_cmds: list[list[str]] = []
    seen_timeouts: list[int | None] = []

    def fake_run(cmd, check=False, timeout=None):
        seen_cmds.append(list(cmd))
        seen_timeouts.append(timeout)
        return subprocess.CompletedProcess(
            cmd,
            0,
            "https://rr1---sn.googlevideo.com/videoplayback?mime=video/mp4\n"
            "https://rr1---sn.googlevideo.com/videoplayback?mime=audio/webm\n"
            "not_live\n",
            "",
        )

    monkeypatch.setattr(resolver, "run", fake_run)

    result = resolver.resolve_streams_ytdlp("https://www.youtube.com/watch?v=capcheck123")

    assert result.stream.startswith("https://rr1---sn.googlevideo.com/")
    assert len(seen_cmds) == 1
    cmd = seen_cmds[0]
    assert "-f" in cmd
    assert "height<=1080" in cmd[cmd.index("-f") + 1]
    assert "--remote-components" not in cmd
    assert "--socket-timeout" in cmd
    assert cmd[cmd.index("--socket-timeout") + 1] == str(resolver._YTDLP_SOCKET_TIMEOUT_SEC)
    assert seen_timeouts == [resolver._YTDLP_RESOLVE_TIMEOUT_SEC]


def test_youtube_remote_components_used_only_as_fallback_and_handed_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bundled yt-dlp-ejs runs first; remote ejs:github activates only on challenge failure."""
    _clear_ytdlp_env(monkeypatch)
    monkeypatch.setattr("relaytv_app.state.get_settings", lambda: {})
    monkeypatch.setattr(
        "relaytv_app.video_profile.get_profile",
        lambda: {"decode_profile": "default", "display_cap_height": 1080, "av1_allowed": False},
    )
    monkeypatch.setattr(resolver.shutil, "which", lambda name: "/usr/local/bin/deno" if name == "deno" else None)

    seen_cmds: list[list[str]] = []

    def fake_run(cmd, check=False, timeout=None):
        seen_cmds.append(list(cmd))
        if "--remote-components" not in cmd:
            return subprocess.CompletedProcess(
                cmd,
                1,
                "",
                "ERROR: [youtube] Remote components are disabled; challenge solving failed",
            )
        return subprocess.CompletedProcess(
            cmd,
            0,
            "https://segments.example/videoplayback?a=1\nis_live\n",
            "",
        )

    monkeypatch.setattr(resolver, "run", fake_run)

    result = resolver.resolve_streams_ytdlp("https://www.youtube.com/watch?v=fallback123")

    assert len(seen_cmds) == 2
    assert "--remote-components" not in seen_cmds[0]
    assert "--remote-components" in seen_cmds[1]
    assert result.transport == "mpv_ytdl"
    assert "remote-components=ejs:github" in result.ytdl_raw_options
    assert "ignore-config" not in result.ytdl_raw_options


def test_youtube_strategies_never_emit_legacy_android_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(resolver, "_preferred_js_runtime_spec", lambda: "deno")

    strategies = resolver._build_youtube_strategies(
        ["yt-dlp", "--ignore-config", "--js-runtimes", "deno", "--no-playlist"],
        ["bestvideo[height<=1080]+bestaudio/best", "best", ""],
    )

    assert strategies
    assert all("youtube:player_client=android" not in args for args, _cands in strategies)
    assert strategies[0][1][0] == "bestvideo[height<=1080]+bestaudio/best"


def test_preferred_js_runtime_rejects_unsupported_node_and_accepts_node22_or_quickjs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_ytdlp_env(monkeypatch)
    resolver._NODE_SUPPORT_CACHE.clear()

    old_node = tmp_path / "node20"
    old_node.write_text("#!/bin/sh\n", encoding="utf-8")
    new_node = tmp_path / "node22"
    new_node.write_text("#!/bin/sh\n", encoding="utf-8")

    def fake_subproc_run(argv, **_kwargs):
        version = "v20.19.2\n" if argv[0] == str(old_node) else "v22.12.0\n"
        return subprocess.CompletedProcess(argv, 0, version, "")

    monkeypatch.setattr(resolver.subprocess, "run", fake_subproc_run)

    # Node 20 is rejected both in auto discovery and with RELAYTV_YTDLP_USE_NODE=1.
    monkeypatch.setattr(
        resolver.shutil, "which", lambda name: str(old_node) if name == "node" else None
    )
    assert resolver._preferred_js_runtime_spec() == ""
    monkeypatch.setenv("RELAYTV_YTDLP_USE_NODE", "1")
    assert resolver._preferred_js_runtime_spec() == ""

    # QuickJS is selected ahead of unsupported Node when Deno is absent.
    monkeypatch.delenv("RELAYTV_YTDLP_USE_NODE", raising=False)
    monkeypatch.setattr(
        resolver.shutil,
        "which",
        lambda name: "/usr/bin/qjs" if name == "qjs" else (str(old_node) if name == "node" else None),
    )
    assert resolver._preferred_js_runtime_spec() == "quickjs"

    # Node 22+ is accepted when requested or as auto fallback.
    monkeypatch.setattr(
        resolver.shutil, "which", lambda name: str(new_node) if name == "node" else None
    )
    assert resolver._preferred_js_runtime_spec() == "node"


def test_build_ytdlp_base_args_adds_ignore_config_unless_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_ytdlp_env(monkeypatch)
    monkeypatch.setattr(resolver, "_preferred_js_runtime_spec", lambda: "deno")
    monkeypatch.setattr(resolver, "_runtime_settings", lambda: {})

    args = resolver.build_ytdlp_base_args()
    assert "--ignore-config" in args

    monkeypatch.setenv("YTDLP_ARGS", "--config-location /data/yt-dlp.conf")
    explicit = resolver.build_ytdlp_base_args()
    assert "--ignore-config" not in explicit
    assert "--config-location" in explicit


def test_persisted_ytdlp_pruning_removes_companion_ejs_and_curl_cffi_only(
    tmp_path: Path,
) -> None:
    """Pruning must clear yt-dlp-ejs and curl_cffi alongside yt_dlp without touching user data."""
    data = tmp_path / "data"
    (data / "bin").mkdir(parents=True)
    (data / "bin" / "yt-dlp").write_text("#!/bin/sh\n", encoding="utf-8")
    site = data / "lib" / "python3.13" / "site-packages"
    site.mkdir(parents=True)
    for pkg in (
        "yt_dlp",
        "yt_dlp-2026.10.26.dist-info",
        "yt_dlp_ejs",
        "yt_dlp_ejs-0.8.0.dist-info",
        "curl_cffi",
        "curl_cffi-0.17.0.dist-info",
        "curl_cffi.libs",
        "unrelated_pkg",
    ):
        (site / pkg).mkdir()
    (data / "settings.json").write_text("{}", encoding="utf-8")

    container_entrypoint._discard_persisted_ytdlp(data, "image newer")

    for removed in (
        data / "bin" / "yt-dlp",
        site / "yt_dlp",
        site / "yt_dlp-2026.10.26.dist-info",
        site / "yt_dlp_ejs",
        site / "yt_dlp_ejs-0.8.0.dist-info",
        site / "curl_cffi",
        site / "curl_cffi-0.17.0.dist-info",
        site / "curl_cffi.libs",
    ):
        assert not removed.exists(), f"{removed.name} should have been pruned"
    assert (site / "unrelated_pkg").exists()
    assert (data / "settings.json").read_text(encoding="utf-8") == "{}"


def test_image_and_compose_default_deno_only_without_unsupported_node() -> None:
    dockerfile = (ROOT_DIR / "app/Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT_DIR / "docker-compose.yml").read_text(encoding="utf-8")

    assert "ARG RELAYTV_INSTALL_DENO=1" in dockerfile
    assert "ARG RELAYTV_INSTALL_NODE=0" in dockerfile
    assert "RELAYTV_INSTALL_NODE: ${RELAYTV_INSTALL_NODE:-0}" in compose
