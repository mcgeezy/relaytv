# SPDX-License-Identifier: GPL-3.0-only
"""Durable state must land in order, and failures must be visible (F04).

Payloads are built under a store lock and written outside it, so two mutations
could reach the disk out of order: the older writer won the race to os.replace
and the newer mutation was lost, reappearing missing after a restart. Separately,
_atomic_write_json swallowed every failure into a log line, so a full or
read-only disk produced a successful API response.
"""
import json
import os
import threading

import pytest

from relaytv_app import playback_service, player, state


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", str(tmp_path), raising=False)
    state.reset_persistence_health_for_tests()
    yield tmp_path
    state.reset_persistence_health_for_tests()


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


# --- ordering ----------------------------------------------------------------


def test_an_older_snapshot_cannot_replace_a_newer_one(state_dir) -> None:
    """Reverse the completion order deterministically and check what survives."""
    publisher = state._Publisher("demo", lambda: os.path.join(str(state_dir), "demo.json"))

    older = publisher.reserve()
    newer = publisher.reserve()

    # The newer mutation completes its write first.
    assert publisher.publish(newer, {"value": "newer"}) is True
    # The older one then finishes. Without versioning it would win, because
    # os.replace is last-write-wins.
    assert publisher.publish(older, {"value": "older"}) is False

    assert _read(os.path.join(str(state_dir), "demo.json")) == {"value": "newer"}


def test_writes_still_apply_in_forward_order(state_dir) -> None:
    publisher = state._Publisher("demo", lambda: os.path.join(str(state_dir), "demo.json"))

    first = publisher.reserve()
    assert publisher.publish(first, {"value": "first"}) is True
    second = publisher.reserve()
    assert publisher.publish(second, {"value": "second"}) is True

    assert _read(os.path.join(str(state_dir), "demo.json")) == {"value": "second"}


def test_concurrent_publishers_leave_the_newest_on_disk(state_dir) -> None:
    publisher = state._Publisher("demo", lambda: os.path.join(str(state_dir), "demo.json"))
    release_older = threading.Event()
    versions = [publisher.reserve() for _ in range(2)]

    def _older():
        release_older.wait(5.0)
        publisher.publish(versions[0], {"value": "older"})

    thread = threading.Thread(target=_older, daemon=True)
    thread.start()
    publisher.publish(versions[1], {"value": "newer"})
    release_older.set()
    thread.join(timeout=5.0)

    assert _read(os.path.join(str(state_dir), "demo.json")) == {"value": "newer"}


def test_queue_and_history_do_not_share_a_version_line(state_dir) -> None:
    """Independent coordinators: a queue write must not suppress a history one."""
    assert state._QUEUE_PUBLISHER is not state._HISTORY_PUBLISHER
    assert state._SESSION_PUBLISHER is not state._SETTINGS_PUBLISHER

    for _ in range(5):
        state._QUEUE_PUBLISHER.reserve()

    assert state.persist_history() is True
    assert os.path.exists(os.path.join(str(state_dir), state.HISTORY_STATE_FILE))


def test_concurrent_settings_updates_leave_the_latest_on_disk(state_dir, monkeypatch) -> None:
    """Exercise update_settings itself, not only the publisher primitive."""
    entered = threading.Event()
    release_older = threading.Event()
    writes: list[float] = []
    monkeypatch.setattr(state, "SETTINGS", {"volume": 100.0}, raising=False)

    def _ordered_write(_path, payload):
        value = float(payload["volume"])
        if value == 10.0:
            entered.set()
            release_older.wait(5.0)
        writes.append(value)
        return True

    monkeypatch.setattr(state, "_atomic_write_json", _ordered_write)

    older = threading.Thread(target=state.update_settings, args=({"volume": 10},), daemon=True)
    newer = threading.Thread(target=state.update_settings, args=({"volume": 20},), daemon=True)
    older.start()
    assert entered.wait(1.0), "older settings write never entered persistence"
    newer.start()
    for _ in range(100):
        if state.get_settings().get("volume") == 20.0:
            break
        threading.Event().wait(0.01)
    assert state.get_settings()["volume"] == 20.0
    release_older.set()
    older.join(timeout=2.0)
    newer.join(timeout=2.0)

    assert not older.is_alive() and not newer.is_alive()
    assert writes[-1] == 20.0, writes


def test_stale_queue_payload_cannot_replace_current_queue(state_dir) -> None:
    newer = {"url": "https://example.com/newer.mp4", "title": "newer"}
    state.QUEUE = [newer]

    state.persist_queue_payload(
        {"queue": [{"url": "https://example.com/older.mp4"}], "saved_at": 1}
    )

    payload = _read(os.path.join(str(state_dir), state.QUEUE_STATE_FILE))
    assert payload["queue"] == [state._persistable_queue_item(newer)]


# --- composite session mutation ----------------------------------------------


def test_composite_session_change_persists_once(state_dir, monkeypatch) -> None:
    writes: list[dict] = []
    monkeypatch.setattr(
        state, "_persist_session_payload", lambda payload, version=None: writes.append(payload) or True
    )

    state.update_session(
        now_playing={"title": "x"}, session_state="playing", pause_reason=None
    )

    assert len(writes) == 1
    assert writes[0]["now_playing"] == {"title": "x"}
    assert writes[0]["session_state"] == "playing"


def test_no_intermediate_combination_is_ever_written(state_dir, monkeypatch) -> None:
    """The three-setter form wrote now_playing while the state still said idle."""
    writes: list[dict] = []
    monkeypatch.setattr(
        state, "_persist_session_payload", lambda payload, version=None: writes.append(payload) or True
    )
    monkeypatch.setattr(state, "SESSION_STATE", "idle", raising=False)
    monkeypatch.setattr(state, "NOW_PLAYING", None, raising=False)

    state.update_session(now_playing={"title": "x"}, session_state="playing")

    for payload in writes:
        incoherent = payload["now_playing"] is not None and payload["session_state"] == "idle"
        assert not incoherent, f"persisted an intermediate combination: {payload}"


def test_individual_setters_still_work(state_dir, monkeypatch) -> None:
    monkeypatch.setattr(state, "_persist_session_payload", lambda payload, version=None: True)

    state.set_session_state("paused")
    state.set_pause_reason("user")
    state.set_session_position(12.5)
    state.set_now_playing({"title": "y"})

    assert state.SESSION_STATE == "paused"
    assert state.SESSION_PAUSE_REASON == "user"
    assert state.SESSION_POSITION == 12.5
    assert state.NOW_PLAYING == {"title": "y"}


def test_update_session_can_skip_persistence(state_dir, monkeypatch) -> None:
    writes: list[dict] = []
    monkeypatch.setattr(
        state, "_persist_session_payload", lambda payload, version=None: writes.append(payload) or True
    )

    state.update_session(session_state="playing", persist=False)

    assert state.SESSION_STATE == "playing"
    assert writes == []


@pytest.mark.parametrize("transition", ["stop_current", "close_current"])
def test_terminal_transition_publishes_one_composite_session(
    state_dir,
    monkeypatch,
    transition,
) -> None:
    updates: list[dict[str, object]] = []
    now = {"title": "Movie", "resume_pos": 1.0}
    monkeypatch.setattr(state, "NOW_PLAYING", now, raising=False)
    monkeypatch.setattr(state, "SESSION_STATE", "playing", raising=False)
    monkeypatch.setattr(state, "SESSION_POSITION", 1.0, raising=False)
    monkeypatch.setattr(state, "update_session", lambda **values: updates.append(values) or True)
    monkeypatch.setattr(player, "native_qt_playback_explicitly_ended", lambda: False)
    monkeypatch.setattr(player, "mpv_get", lambda prop: 42.0 if prop == "time-pos" else 100.0)
    monkeypatch.setattr(player, "update_history_progress", lambda *args, **kwargs: None)
    monkeypatch.setattr(playback_service, "can_preserve_closed_session", lambda: True)
    monkeypatch.setattr(playback_service, "suppress_auto_next", lambda seconds: None)
    monkeypatch.setattr(playback_service, "discard_interrupted_playback_state", lambda reason: None)
    monkeypatch.setattr(playback_service, "stop_all", lambda **kwargs: None)

    if transition == "close_current":
        playback_service.close_current(
            idle_surface_enabled=False,
            keep_shell_allowed=False,
        )
    else:
        playback_service.stop_current()

    assert len(updates) == 1
    assert updates[0]["session_state"] == "closed"
    assert updates[0]["session_position"] == 42.0
    assert updates[0]["now_playing"]["resume_pos"] == 42.0
    assert updates[0]["now_playing"]["closed"] is True


# --- failure is observable ---------------------------------------------------


def test_write_failure_is_reported_not_swallowed(state_dir, monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(state.os, "replace", _boom)

    assert state.persist_settings() is False

    health = state.persistence_health()
    assert health["ok"] is False
    assert state.SETTINGS_STATE_FILE in health["failing"]
    assert "No space left" in health["failing"][state.SETTINGS_STATE_FILE]["last_error"]


def test_update_settings_does_not_report_success_when_disk_write_fails(state_dir, monkeypatch) -> None:
    monkeypatch.setattr(state, "_atomic_write_json", lambda *args, **kwargs: False)

    with pytest.raises(RuntimeError, match="settings persistence failed"):
        state.update_settings({"volume": 42})


def test_health_recovers_after_a_successful_write(state_dir, monkeypatch) -> None:
    real_replace = state.os.replace
    failing = {"on": True}

    def _maybe_boom(src, dst):
        if failing["on"]:
            raise OSError(28, "No space left on device")
        return real_replace(src, dst)

    monkeypatch.setattr(state.os, "replace", _maybe_boom)
    assert state.persist_settings() is False
    assert state.persistence_health()["ok"] is False

    failing["on"] = False
    assert state.persist_settings() is True
    assert state.persistence_health()["ok"] is True


def test_health_is_clean_by_default(state_dir) -> None:
    assert state.persistence_health() == {"ok": True, "failing": {}}


def test_persist_helpers_report_success(state_dir) -> None:
    assert state.persist_queue() is True
    assert state.persist_history() is True
    assert state.persist_session() is True
    assert state.persist_settings() is True


# --- no state lock is held across disk I/O -----------------------------------


def test_queue_lock_is_not_held_while_writing(state_dir, monkeypatch) -> None:
    """A slow disk must not block every reader of the queue."""
    observed: list[bool] = []
    real_write = state._atomic_write_json

    def _slow_write(path, obj):
        # If the queue lock were still held, this would deadlock rather than
        # report False.
        observed.append(state.QUEUE_LOCK.acquire(blocking=False))
        if observed[-1]:
            state.QUEUE_LOCK.release()
        return real_write(path, obj)

    monkeypatch.setattr(state, "_atomic_write_json", _slow_write)

    state.persist_queue()

    assert observed == [True], "QUEUE_LOCK was held across the disk write"


def test_status_reports_a_failing_disk(state_dir, monkeypatch) -> None:
    """The failure has to reach somewhere an operator will actually look."""
    from fastapi.testclient import TestClient

    from relaytv_app.main import create_app

    client = TestClient(create_app(testing=True))
    assert "persistence" not in client.get("/status").json()

    def _boom(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(state.os, "replace", _boom)
    state.persist_settings()

    payload = client.get("/status").json()
    assert payload["persistence"]["ok"] is False
    assert state.SETTINGS_STATE_FILE in payload["persistence"]["failing"]
