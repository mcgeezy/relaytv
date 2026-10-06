# SPDX-License-Identifier: GPL-3.0-only
"""Snapshot capture must not report a frame it does not have.

Capture used to discard mpv's result and return ``ok: true`` with an
``image_url`` before any file existed, so a client could be handed a URL that
404s immediately or stays empty forever.
"""
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from relaytv_app import player
from relaytv_app.main import create_app
from relaytv_app.routes import snapshots


@pytest.fixture
def snap_dir(tmp_path, monkeypatch):
    path = tmp_path / "snapshots"
    monkeypatch.setenv("RELAYTV_SNAPSHOT_DIR", str(path))
    monkeypatch.setattr(player, "is_playing", lambda: True)
    return path


@pytest.fixture
def client():
    return TestClient(create_app(testing=True))


def _written_files(snap_dir) -> list[str]:
    if not snap_dir.exists():
        return []
    return sorted(p.name for p in snap_dir.iterdir())


# --- the command itself ------------------------------------------------------


def test_mpv_command_success_predicate() -> None:
    assert snapshots._mpv_command_succeeded({"error": "success"}) is True
    assert snapshots._mpv_command_succeeded({"error": "property not found"}) is False
    assert snapshots._mpv_command_succeeded(True) is True
    assert snapshots._mpv_command_succeeded(False) is False
    # A dropped command returns None; that is not a capture.
    assert snapshots._mpv_command_succeeded(None) is False
    assert snapshots._mpv_command_succeeded("success") is False


def test_rejected_command_is_not_reported_as_success(snap_dir, client, monkeypatch) -> None:
    monkeypatch.setattr(player, "mpv_command", lambda cmd: {"error": "property not found"})

    response = client.post("/snapshot")

    assert response.status_code == 502
    assert "rejected" in response.json()["detail"].lower()


def test_dropped_command_is_not_reported_as_success(snap_dir, client, monkeypatch) -> None:
    monkeypatch.setattr(player, "mpv_command", lambda cmd: None)

    assert client.post("/snapshot").status_code == 502


# --- waiting for the frame ---------------------------------------------------


def test_success_requires_a_frame_on_disk(snap_dir, client, monkeypatch) -> None:
    def _write(cmd):
        target = cmd[1]
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(b"\xff\xd8jpegbytes")
        return {"error": "success"}

    monkeypatch.setattr(player, "mpv_command", _write)

    response = client.post("/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    served = client.get(payload["image_url"])
    assert served.status_code == 200
    assert served.content == b"\xff\xd8jpegbytes"


def test_delayed_frame_still_succeeds(snap_dir, client, monkeypatch) -> None:
    """mpv can land the file just after the command returns."""

    def _write_late(cmd):
        target = cmd[1]

        def _later():
            time.sleep(0.15)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(b"late-but-real")

        threading.Thread(target=_later, daemon=True).start()
        return {"error": "success"}

    monkeypatch.setattr(player, "mpv_command", _write_late)

    response = client.post("/snapshot")

    assert response.status_code == 200
    assert client.get(response.json()["image_url"]).content == b"late-but-real"


def test_frame_that_never_arrives_times_out(snap_dir, client, monkeypatch) -> None:
    monkeypatch.setenv("RELAYTV_SNAPSHOT_TIMEOUT_SEC", "0.2")
    monkeypatch.setattr(player, "mpv_command", lambda cmd: {"error": "success"})

    started = time.monotonic()
    response = client.post("/snapshot")
    elapsed = time.monotonic() - started

    assert response.status_code == 504
    # Bounded: it must not hang the request thread waiting forever.
    assert elapsed < 5.0
    assert _written_files(snap_dir) == []


def test_empty_frame_is_treated_as_missing(snap_dir, client, monkeypatch) -> None:
    """A zero-byte file is what a failed write leaves behind."""
    monkeypatch.setenv("RELAYTV_SNAPSHOT_TIMEOUT_SEC", "0.2")

    def _touch_empty(cmd):
        target = cmd[1]
        os.makedirs(os.path.dirname(target), exist_ok=True)
        open(target, "wb").close()
        return {"error": "success"}

    monkeypatch.setattr(player, "mpv_command", _touch_empty)

    assert client.post("/snapshot").status_code == 504
    # The stub is removed so /snapshots never serves an empty image.
    assert _written_files(snap_dir) == []


# --- unchanged behavior ------------------------------------------------------


def test_idle_playback_still_conflicts(snap_dir, client, monkeypatch) -> None:
    monkeypatch.setattr(player, "is_playing", lambda: False)
    assert client.post("/snapshot").status_code == 409


def test_get_alias_behaves_the_same(snap_dir, client, monkeypatch) -> None:
    monkeypatch.setattr(player, "mpv_command", lambda cmd: {"error": "property not found"})
    assert client.get("/snapshot").status_code == 502


def test_timeout_setting_falls_back_on_garbage(monkeypatch) -> None:
    for raw in ("", "abc", "0", "-1", None):
        monkeypatch.setenv("RELAYTV_SNAPSHOT_TIMEOUT_SEC", "" if raw is None else str(raw))
        assert snapshots._snapshot_timeout_sec() == snapshots._DEFAULT_SNAPSHOT_TIMEOUT_SEC

    monkeypatch.setenv("RELAYTV_SNAPSHOT_TIMEOUT_SEC", "900")
    # Clamped: an operator typo must not turn into a hung request thread.
    assert snapshots._snapshot_timeout_sec() == 30.0
