# SPDX-License-Identifier: GPL-3.0-only
import pytest

from relaytv_app.config import runtime_config


@pytest.fixture(autouse=True)
def _runtime_config_env_lockstep():
    """Keep the global RuntimeConfig snapshot in lockstep with the test env.

    Production guarantees env/snapshot lockstep because every settings-bus
    write goes through RuntimeConfig.set_value. Tests mutate os.environ directly
    (monkeypatch), so re-sync the snapshot from env at test start (discarding
    leftovers from earlier tests) and after teardown (once monkeypatch has
    restored the environment). Tests that change a settings-bus variable
    mid-test and exercise a snapshot-reading consumer must call
    runtime_config.refresh_from_env() themselves after the change.
    """
    runtime_config.refresh_from_env()
    yield
    runtime_config.refresh_from_env()


_YTDLP_PROVIDER_KEYS = (
    "YTDLP_FORMAT_YOUTUBE",
    "YTDLP_FORMAT_TWITCH",
    "YTDLP_FORMAT_TIKTOK",
    "YTDLP_FORMAT_RUMBLE",
    "YTDLP_FORMAT_BITCHUTE",
)


@pytest.fixture
def disable_arm_safe_ytdl(monkeypatch):
    monkeypatch.setenv("RELAYTV_ARM_ENFORCE_SAFE_YTDL_FORMAT", "0")


@pytest.fixture
def ytdlp_format_best(monkeypatch):
    monkeypatch.setenv("YTDLP_FORMAT", "best")
    for key in _YTDLP_PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def ytdlp_format_unset(monkeypatch):
    monkeypatch.delenv("YTDLP_FORMAT", raising=False)
    for key in _YTDLP_PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def stalled_qt_playback(monkeypatch):
    """Drive the supervisor with a fresh, stationary native VOD sample."""
    from relaytv_app import player, state

    now = {
        "history_id": "supervisor-stall",
        "url": "https://example.test/movie.mp4",
        "stream": "https://example.test/movie.mp4",
        "started": 1,
        "resume_pos": 120.0,
        "duration_sec": 7200.0,
    }
    monkeypatch.setattr(state, "NOW_PLAYING", now)
    monkeypatch.setattr(state, "SESSION_STATE", "playing")
    monkeypatch.setattr(state, "_persist_session_payload", lambda *a: True)
    monkeypatch.setattr(player, "_QT_SHELL_PLAYBACK_PROGRESS_STATE", {})
    monkeypatch.setattr(player, "_QT_SHELL_SUPERVISOR_STATE", {})
    monkeypatch.setattr(player, "_QT_SHELL_SUPERVISOR_LAST_RESTART_MONOTONIC", 0.0)
    for name in (
        "_qt_shell_supervisor_enabled",
        "_qt_shell_backend_enabled",
        "_qt_shell_running",
        "_is_playing",
    ):
        monkeypatch.setattr(player, name, lambda: True)
    monkeypatch.setattr(player, "_qt_shell_display_stable", lambda *a: True)
    for name in (
        "_qt_runtime_uses_external_mpv",
        "playback_transitioning",
        "auto_next_transitioning",
    ):
        monkeypatch.setattr(player, name, lambda: False)
    monkeypatch.setattr(player, "_mark_playback_transition", lambda *a, **kw: None)
    monkeypatch.setattr(player, "qt_shell_runtime_telemetry", lambda **kw: {"freshness": "fresh"})
    monkeypatch.setattr(
        player,
        "_qt_shell_runtime_output_state",
        lambda **kw: {
            "path": now["stream"],
            "current_vo": "libmpv",
            "current_ao": "alsa",
            "paused": False,
            "playback_active": True,
            "core_idle": False,
            "eof_reached": False,
            "time_pos": 120.0,
            "duration": 7200.0,
        },
    )
    monkeypatch.setattr(
        player,
        "mpv_get",
        lambda prop: {
            "time-pos": 120.0,
            "pause": False,
            "duration": 7200.0,
        }.get(prop),
    )
    return now
