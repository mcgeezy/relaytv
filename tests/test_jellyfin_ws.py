# SPDX-License-Identifier: GPL-3.0-only
"""Jellyfin control-socket tests (docs/JELLYFIN_OPERATIONS.md, "Cast target").

Everything here runs against a fake socket. The point of the module under test
is that RelayTV shows up as a cast target and obeys the remote, so the coverage
is: what the server sends maps to the right command, keepalives keep the
session alive, a slow command cannot stall them, and the access token never
escapes into status or logs.
"""
import json
import logging
import threading
import time

import pytest

from relaytv_app.integrations import jellyfin_receiver, jellyfin_ws


@pytest.fixture(autouse=True)
def _clean_socket_state():
    """Leave the process-wide socket and sink exactly as they were found.

    routes/jellyfin.py registers the real sink at import time; a test that
    clears it must not leak that into the rest of the suite.
    """
    sink = jellyfin_receiver._COMMAND_SINK
    jellyfin_ws.stop()
    yield
    jellyfin_ws.stop()
    jellyfin_receiver.register_command_sink(sink)


class FakeSocket:
    """Minimal stand-in for websockets' sync ClientConnection."""

    def __init__(self, inbound=()):
        self._inbound = list(inbound)
        self.sent: list[str] = []
        self.closed = False
        self._lock = threading.Lock()

    def recv(self, timeout=None):
        with self._lock:
            if self._inbound:
                return self._inbound.pop(0)
        raise TimeoutError

    def send(self, message):
        with self._lock:
            self.sent.append(message)

    def push(self, message):
        with self._lock:
            self._inbound.append(message)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False


# --- message routing -------------------------------------------------------


def test_play_message_becomes_a_play_command() -> None:
    routed = jellyfin_ws.normalize_message(
        json.dumps(
            {
                "MessageId": "m-1",
                "MessageType": "Play",
                "Data": {"ItemIds": ["abc"], "PlayCommand": "PlayNow", "StartPositionTicks": 90_000_000},
            }
        )
    )
    assert routed is not None
    action, payload = routed
    assert action == "play"
    assert payload["ItemIds"] == ["abc"]
    assert payload["PlayCommand"] == "PlayNow"
    # Carried so extract_command_id dedupes a message the server repeats.
    assert payload["MessageId"] == "m-1"


def test_playstate_leaves_the_action_for_the_normalizer() -> None:
    """Playstate carries its verb in the body, which normalize_action reads."""
    from relaytv_app.integrations import jellyfin_service

    for command, expected in (
        ("Pause", "pause"),
        ("Unpause", "resume"),
        ("Stop", "stop"),
        ("Seek", "seek"),
        ("NextTrack", "next"),
        ("PreviousTrack", "previous"),
        ("PlayPause", "play_pause"),
    ):
        routed = jellyfin_ws.normalize_message({"MessageType": "Playstate", "Data": {"Command": command}})
        assert routed is not None, command
        action, payload = routed
        assert action == ""
        assert jellyfin_service.normalize_action(action or None, payload) == expected


def test_general_command_flattens_arguments_for_volume() -> None:
    routed = jellyfin_ws.normalize_message(
        {"MessageType": "GeneralCommand", "Data": {"Name": "SetVolume", "Arguments": {"Volume": "42"}}}
    )
    assert routed is not None
    action, payload = routed
    assert payload["Volume"] == "42"

    from relaytv_app.integrations import jellyfin_service

    assert jellyfin_service.normalize_action(action or None, payload) == "set_volume"

    class _Req:
        def __init__(self, payload):
            self.payload = payload
            self.start_pos = None

    assert jellyfin_service.extract_volume(_Req(payload)) == pytest.approx(42.0)


def test_server_chatter_is_ignored() -> None:
    """The control socket carries library and session events too."""
    for message in (
        {"MessageType": "Sessions", "Data": []},
        {"MessageType": "UserDataChanged", "Data": {}},
        {"MessageType": "KeepAlive"},
        {"MessageType": "LibraryChanged", "Data": {}},
        "not json at all",
        {"no": "message type"},
    ):
        assert jellyfin_ws.normalize_message(message) is None


# --- keepalive and reconnect ----------------------------------------------


def test_keepalive_interval_is_half_the_server_timeout() -> None:
    assert jellyfin_ws._keepalive_interval(60) == pytest.approx(30.0)
    assert jellyfin_ws._keepalive_interval("30") == pytest.approx(15.0)
    # A nonsense or zero timeout must not turn into a busy loop.
    assert jellyfin_ws._keepalive_interval(0) == pytest.approx(30.0)
    assert jellyfin_ws._keepalive_interval("nonsense") == pytest.approx(30.0)


def test_read_loop_answers_force_keepalive(monkeypatch) -> None:
    ws = FakeSocket([json.dumps({"MessageType": "ForceKeepAlive", "Data": 2})])
    stop_at = time.monotonic() + 3.0

    def _fake_is_set():
        return time.monotonic() > stop_at

    monkeypatch.setattr(jellyfin_ws._STOP, "is_set", _fake_is_set)
    jellyfin_ws._read_loop(ws)

    assert ws.sent, "no KeepAlive was ever sent"
    assert all(json.loads(m)["MessageType"] == "KeepAlive" for m in ws.sent)
    assert jellyfin_ws.status()["keepalive_sec"] == pytest.approx(1.0)


def test_backoff_grows_and_is_capped(monkeypatch) -> None:
    monkeypatch.setenv("RELAYTV_JELLYFIN_WS_RETRY_BASE_SEC", "3")
    monkeypatch.setenv("RELAYTV_JELLYFIN_WS_RETRY_MAX_SEC", "60")
    assert [jellyfin_ws.backoff_sec(n) for n in (1, 2, 3, 4, 5)] == [3.0, 6.0, 12.0, 24.0, 48.0]
    assert jellyfin_ws.backoff_sec(50) == 60.0


def test_a_slow_command_does_not_stall_keepalives(monkeypatch) -> None:
    """Starting mpv takes seconds; the reader must not wait for it.

    If commands ran inline, this ForceKeepAlive would go unanswered for the
    length of the play and the server would drop the session mid-handoff.
    """
    started = threading.Event()
    release = threading.Event()

    def _slow_sink(action, payload):
        started.set()
        release.wait(5.0)
        return {"ok": True}

    jellyfin_receiver.register_command_sink(_slow_sink)
    try:
        jellyfin_ws._COMMANDS = jellyfin_ws.queue.Queue(maxsize=8)
        worker = threading.Thread(target=jellyfin_ws._command_worker, daemon=True)
        jellyfin_ws._STOP.clear()
        worker.start()

        ws = FakeSocket(
            [
                json.dumps({"MessageType": "ForceKeepAlive", "Data": 2}),
                json.dumps({"MessageType": "Play", "Data": {"ItemIds": ["x"]}}),
            ]
        )
        stop_at = time.monotonic() + 3.0
        monkeypatch.setattr(jellyfin_ws._STOP, "is_set", lambda: time.monotonic() > stop_at)
        jellyfin_ws._read_loop(ws)

        assert started.is_set(), "command never reached the worker"
        assert ws.sent, "keepalive was starved by the in-flight command"
    finally:
        release.set()
        jellyfin_receiver.register_command_sink(None)


def test_command_backlog_is_bounded(monkeypatch) -> None:
    """A burst while playback is starting is dropped, not replayed later."""
    jellyfin_ws._COMMANDS = jellyfin_ws.queue.Queue(maxsize=2)
    for _ in range(5):
        jellyfin_ws._submit("play", {})
    assert jellyfin_ws.status()["commands_dropped"] == 3


# --- secret handling -------------------------------------------------------


def test_socket_url_carries_the_token_and_device() -> None:
    url = jellyfin_ws.socket_url(server_url="http://jf.lan:8096", token="tok-123", device_id="relaytv-den")
    assert url == "ws://jf.lan:8096/socket?api_key=tok-123&deviceId=relaytv-den"
    assert jellyfin_ws.socket_url(server_url="https://jf.example/emby", token="t", device_id="d").startswith(
        "wss://jf.example/emby/socket?"
    )
    # Nothing to dial without a session.
    assert jellyfin_ws.socket_url(server_url="http://jf.lan:8096", token="", device_id="d") == ""


def test_the_access_token_never_reaches_status_or_logs(caplog) -> None:
    """A failed handshake reports the URL it tried, and that URL holds the token."""
    token = "super-secret-token"
    url = jellyfin_ws.socket_url(server_url="http://jf.lan:8096", token=token, device_id="relaytv-den")
    with caplog.at_level(logging.WARNING):
        jellyfin_ws._mark_error(f"failed to connect to {url}")

    reported = str(jellyfin_ws.status().get("last_error") or "")
    assert token not in reported
    assert "<redacted>" in reported
    assert token not in caplog.text
    assert all(token not in str(value) for value in jellyfin_ws.status().values())


def test_receiver_status_reports_socket_health_without_secrets() -> None:
    st = jellyfin_receiver.status()
    for key in ("ws_enabled", "ws_connected", "ws_reconnects", "cast_target_ready"):
        assert key in st
    assert st["cast_target_ready"] is False


# --- dispatch seam ---------------------------------------------------------


def test_dispatch_requires_a_registered_sink() -> None:
    jellyfin_receiver.register_command_sink(None)
    assert jellyfin_receiver.command_sink_registered() is False
    with pytest.raises(RuntimeError):
        jellyfin_receiver.dispatch_command("play", {})


def test_dispatch_reaches_the_registered_sink() -> None:
    seen: list[tuple[str, dict]] = []
    jellyfin_receiver.register_command_sink(lambda action, payload: seen.append((action, payload)))
    try:
        jellyfin_receiver.dispatch_command("play", {"ItemIds": ["a"]})
        assert seen == [("play", {"ItemIds": ["a"]})]
    finally:
        jellyfin_receiver.register_command_sink(None)


def test_socket_does_not_dial_before_a_sink_exists() -> None:
    """No point holding a socket we cannot act on."""
    jellyfin_receiver.register_command_sink(None)
    assert jellyfin_ws._ready() is False


# --- session re-assertion after a server restart ---------------------------


def test_invalidating_registration_forces_a_fresh_verify() -> None:
    """A Jellyfin restart drops capabilities from its session state.

    Registration short-circuits on its own last success, so without this the
    device keeps reporting itself castable while the server offers nothing.
    """
    jellyfin_receiver._STATUS["connected"] = True
    jellyfin_receiver._STATUS["last_register_ok"] = True
    jellyfin_receiver._STATUS["media_control_verified"] = True

    jellyfin_receiver.invalidate_registration("socket_connected")

    assert jellyfin_receiver._STATUS["connected"] is False
    assert jellyfin_receiver._STATUS["last_register_ok"] is None
    assert jellyfin_receiver._STATUS["media_control_verified"] is None
    assert jellyfin_receiver._STATUS["last_register_reason"] == "socket_connected"
    # Backoff must not delay the re-register.
    assert jellyfin_receiver._NEXT_REGISTER_RETRY_TS == 0.0
    assert jellyfin_receiver.status()["cast_target_ready"] is False


def test_ensure_registration_reregisters_after_invalidation(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setitem(jellyfin_receiver._STATUS, "enabled", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "running", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_url", "http://jf.lan:8096")
    monkeypatch.setitem(jellyfin_receiver._STATUS, "authenticated", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "connected", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "last_register_ok", True)
    monkeypatch.setattr(jellyfin_receiver, "register_receiver_once", lambda: calls.append(1) or {"ok": True})

    # Healthy: nothing to do.
    jellyfin_receiver._ensure_registration()
    assert calls == []

    jellyfin_receiver.invalidate_registration("socket_connected")
    jellyfin_receiver._ensure_registration()
    assert calls == [1]
