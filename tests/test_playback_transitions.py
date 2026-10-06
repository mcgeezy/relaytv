# SPDX-License-Identifier: GPL-3.0-only
"""Playback transition scenario guardrails (docs/ARCHITECTURE.md).

Home for the review-mandated transition scenarios that lacked coverage at
phase start. The other four scenarios are guarded in
tests/test_playback_routes.py and tests/test_smoke.py; see the coverage
baseline in docs/TRANSITION_INVENTORY.md.
"""
import json

import pytest

from relaytv_app import routes, state


def test_app_restart_restores_resumable_closed_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A closed session persisted before shutdown is resumable after restart.

    Mirrors the startup path: load_state_from_disk() reads session.json from
    the state dir, and /status must then report the closed session with
    resume_available so the UI can offer resume.
    """
    persisted_now = {
        "url": "https://example.com/movie.mp4",
        "title": "Persisted Movie",
        "provider": "direct",
        "closed": True,
    }
    (tmp_path / "session.json").write_text(
        json.dumps(
            {
                "session_state": "closed",
                "session_position": 321.5,
                "now_playing": persisted_now,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(state, "STATE_DIR", str(tmp_path))
    # Fresh-process baseline: nothing in memory before the disk load.
    monkeypatch.setattr(state, "SESSION_STATE", "idle", raising=False)
    monkeypatch.setattr(state, "SESSION_POSITION", None, raising=False)
    monkeypatch.setattr(state, "NOW_PLAYING", None, raising=False)
    monkeypatch.setattr(state, "QUEUE", [], raising=False)

    state._load_persisted_session()

    assert state.SESSION_STATE == "closed"
    assert state.SESSION_POSITION == 321.5
    assert state.NOW_PLAYING == persisted_now

    # The public contract: status reports the restored session as resumable.
    monkeypatch.setattr(routes.player, "is_playing", lambda: False)
    monkeypatch.setattr(routes.player, "_qt_shell_backend_enabled", lambda: True)
    monkeypatch.setattr(routes.player, "playback_transitioning", lambda: False)
    monkeypatch.setattr(routes.player, "auto_next_transitioning", lambda: False)
    monkeypatch.setattr(routes.player, "_qt_runtime_active", lambda **_: False)
    monkeypatch.setattr(routes.player, "_qt_shell_running", lambda: False)
    monkeypatch.setattr(routes.player, "get_mpv_log_tail", lambda lines=40: [])
    monkeypatch.setattr(routes.player, "_effective_ytdl_format", lambda s=None: "")
    monkeypatch.setattr(routes.player, "IPC_PATH", "/tmp/test-mpv.sock", raising=False)
    monkeypatch.setattr(routes.player, "mpv_get_many", lambda props: {})

    payload = routes.status()

    assert payload["state"] == "closed"
    assert payload["playing"] is False
    assert payload["resume_available"] is True
    assert payload["now_playing"] == persisted_now
    # Live position is None while nothing plays; the persisted position is
    # consumed by resume_session (guarded in test_smoke.py).
