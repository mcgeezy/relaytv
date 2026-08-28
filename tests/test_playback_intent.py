# SPDX-License-Identifier: GPL-3.0-only
"""Latest playback intent wins (F01).

Resolving takes seconds and deliberately runs outside MPV_LOCK, so a newer Play
can supersede a slow older one. Nothing tracked *which* play was still wanted,
so whichever resolve finished last won: a Play issued during a slow resolve
could be overwritten by the older one, and a Stop issued during a resolve was
undone the moment that resolve completed.
"""
import inspect
import threading

import pytest

from relaytv_app import player, state


@pytest.fixture(autouse=True)
def _isolated_playback(monkeypatch):
    monkeypatch.setattr(state, "NOW_PLAYING", None, raising=False)
    monkeypatch.setattr(state, "SESSION_STATE", "idle", raising=False)
    monkeypatch.setattr(state, "QUEUE", [], raising=False)
    monkeypatch.setattr(state, "set_now_playing", lambda value: setattr(state, "NOW_PLAYING", value))
    monkeypatch.setattr(state, "set_session_state", lambda value: setattr(state, "SESSION_STATE", value))
    monkeypatch.setattr(state, "set_pause_reason", lambda value: None)
    monkeypatch.setattr(state, "set_session_position", lambda value: None)
    monkeypatch.setattr(state, "persist_queue", lambda: True)
    monkeypatch.setattr(state, "update_history_progress", lambda *a, **k: None, raising=False)
    yield


@pytest.fixture
def harness(monkeypatch):
    """Wire play_item onto fakes, with a resolver we can block on demand."""
    loaded: list[str] = []
    history: list[dict] = []
    watchdogs: list[dict] = []
    gate = threading.Event()
    resolving = threading.Event()

    class _Result:
        stream = "https://cdn.test/stream.m3u8"
        audio = None
        live_status = ""
        ytdl_format = ""
        ytdlp_args = ()

    def _slow_resolve(url):
        resolving.set()
        gate.wait(10.0)
        return _Result()

    monkeypatch.setattr(player, "resolve_streams", _slow_resolve)
    monkeypatch.setattr(player, "update_history_progress", lambda *a, **k: None)
    monkeypatch.setattr(player, "_mark_playback_transition", lambda *a, **k: None)
    monkeypatch.setattr(player, "_wait_for_resolved_media_availability", lambda item: None)
    monkeypatch.setattr(player, "_add_history_entry", lambda now: history.append(now))
    monkeypatch.setattr(player, "_arm_playback_start_watchdog", lambda now: watchdogs.append(now))
    monkeypatch.setattr(player, "_prime_mpv_up_next_from_queue", lambda **k: None)
    monkeypatch.setattr(player, "_load_stream_in_existing_mpv", lambda *a, **k: loaded.append(a[0]) or True)
    monkeypatch.setattr(player, "start_mpv", lambda *a, **k: loaded.append(a[0]))
    monkeypatch.setattr(player, "cec_auto_on_switch", lambda cec: False)
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: False)
    monkeypatch.setattr(player, "note_playback_started", lambda pos: None)
    monkeypatch.setattr(
        player, "_resolved_playback_source", lambda item, raw, result: (result.stream, None, None, None)
    )
    monkeypatch.setattr(player, "_fresh_prefetched_stream", lambda item: None)
    monkeypatch.setattr(player, "_providers_forced_to_resolve", lambda: {"youtube"})

    return {
        "loaded": loaded,
        "history": history,
        "watchdogs": watchdogs,
        "gate": gate,
        "resolving": resolving,
    }


def _play_in_thread(url, results, errors):
    def _run():
        try:
            results.append(player.play_item(url, use_resolver=True, cec=False, clear_queue=False, mode="test"))
        except BaseException as exc:  # noqa: BLE001 - surfaced by the caller
            errors.append(exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


# --- the primitives ----------------------------------------------------------


def test_claiming_supersedes_the_previous_intent() -> None:
    first = player.claim_playback_intent()
    assert player.playback_intent_current(first) is True

    second = player.claim_playback_intent()
    assert second > first
    assert player.playback_intent_current(second) is True
    assert player.playback_intent_current(first) is False


def test_retiring_invalidates_every_outstanding_intent() -> None:
    intent = player.claim_playback_intent()
    player.retire_playback_intents("test")
    assert player.playback_intent_current(intent) is False


# --- the races ---------------------------------------------------------------


def test_stop_during_resolve_prevents_load_and_publish(harness) -> None:
    """The audited symptom: playback restarting seconds after Stop."""
    results: list[dict] = []
    errors: list[BaseException] = []
    thread = _play_in_thread("https://youtu.be/slow", results, errors)

    assert harness["resolving"].wait(5.0), "resolver never started"
    player.retire_playback_intents("stop")
    harness["gate"].set()
    thread.join(timeout=10.0)

    assert not errors, errors
    assert not thread.is_alive()
    assert harness["loaded"] == [], "a retired play loaded media into mpv"
    assert harness["history"] == [], "a retired play wrote history"
    assert harness["watchdogs"] == [], "a retired play armed the watchdog"
    assert state.NOW_PLAYING is None
    assert state.SESSION_STATE == "idle"


def test_newer_play_supersedes_a_slower_older_one(harness) -> None:
    results: list[dict] = []
    errors: list[BaseException] = []
    thread = _play_in_thread("https://youtu.be/older", results, errors)
    assert harness["resolving"].wait(5.0)

    # A second play claims the intent while the first is still resolving.
    harness["resolving"].clear()
    newer_intent = player.claim_playback_intent()

    harness["gate"].set()
    thread.join(timeout=10.0)

    assert not errors, errors
    assert harness["loaded"] == [], "the older play loaded after being superseded"
    assert player.playback_intent_current(newer_intent) is True
    # The superseded call reports what actually owns playback rather than its
    # own stale intention.
    assert results and not results[0]


def test_a_play_that_is_never_superseded_publishes(harness) -> None:
    harness["gate"].set()

    now = player.play_item(
        "https://youtu.be/fine", use_resolver=True, cec=False, clear_queue=False, mode="test"
    )

    assert len(harness["loaded"]) == 1
    assert len(harness["history"]) == 1
    assert len(harness["watchdogs"]) == 1
    assert now["url"] == "https://youtu.be/fine"
    assert state.SESSION_STATE == "playing"


def test_superseded_play_reports_the_winner(harness, monkeypatch) -> None:
    results: list[dict] = []
    errors: list[BaseException] = []
    thread = _play_in_thread("https://youtu.be/older", results, errors)
    assert harness["resolving"].wait(5.0)

    winner = {"url": "https://youtu.be/newer", "title": "newer"}
    player.claim_playback_intent()
    state.NOW_PLAYING = winner

    harness["gate"].set()
    thread.join(timeout=10.0)

    assert not errors, errors
    assert results == [winner]


def test_retired_play_closes_the_relay_it_prepared(harness, monkeypatch) -> None:
    """Retired work must release private resources it had already spawned."""
    closed: list[str] = []

    class _PostLiveResult:
        stream = "https://cdn.test/postlive"
        audio = None
        live_status = "post_live"
        ytdl_format = ""
        ytdlp_args = ()

    def _slow_postlive(url):
        harness["resolving"].set()
        harness["gate"].wait(10.0)
        return _PostLiveResult()

    monkeypatch.setattr(player, "resolve_streams", _slow_postlive)
    monkeypatch.setattr(player, "_post_live_relay_source", lambda item, result: "http://127.0.0.1:9/relay/tok123")
    monkeypatch.setattr(player, "_relay_token_from_url", lambda url: "tok123")
    monkeypatch.setattr(player, "_clear_prefetched_stream", lambda item: None)
    monkeypatch.setattr(
        player.postlive_relay, "close_session", lambda token, reason: closed.append(token)
    )

    results: list[dict] = []
    errors: list[BaseException] = []
    thread = _play_in_thread("https://youtu.be/postlive", results, errors)
    assert harness["resolving"].wait(5.0)

    player.retire_playback_intents("stop")
    harness["gate"].set()
    thread.join(timeout=10.0)

    assert not errors, errors
    assert closed == ["tok123"], "a retired play left its relay pipeline running"
    assert harness["loaded"] == []


# --- the fix must not serialize playback behind the resolver ------------------


def test_mpv_lock_is_not_held_across_resolution() -> None:
    """A newer intent must be able to supersede a slow older one.

    Widening MPV_LOCK to cover the resolver would make "latest wins" true by
    making everything sequential, at the cost of every play waiting out the
    previous one's resolve.
    """
    source = inspect.getsource(player._play_item_owned)
    lock_at = source.index("with MPV_LOCK:")
    resolve_at = source.index("resolve_streams(raw)")
    assert resolve_at < lock_at, "resolve_streams must run before MPV_LOCK is taken"


def test_stop_paths_retire_intents() -> None:
    for name in ("stop_mpv", "stop_playback_keep_qt_shell"):
        source = inspect.getsource(getattr(player, name))
        assert "retire_playback_intents" in source, f"{name} does not retire in-flight plays"
