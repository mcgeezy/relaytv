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

from relaytv_app import state
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


def _session(deadline_sec=3.0):
    """A session whose stop flag trips after a short deadline."""
    s = jellyfin_ws._Session(("http://jf.lan:8096", "relaytv-den", "fp"))
    stop_at = time.monotonic() + deadline_sec
    s.stop.is_set = lambda: time.monotonic() > stop_at  # type: ignore[method-assign]
    return s


def test_read_loop_answers_force_keepalive() -> None:
    ws = FakeSocket([json.dumps({"MessageType": "ForceKeepAlive", "Data": 2})])
    jellyfin_ws._read_loop(ws, _session())

    assert ws.sent, "no KeepAlive was ever sent"
    assert all(json.loads(m)["MessageType"] == "KeepAlive" for m in ws.sent)
    assert jellyfin_ws.status()["keepalive_sec"] == pytest.approx(1.0)


def test_backoff_grows_and_is_capped(monkeypatch) -> None:
    monkeypatch.setenv("RELAYTV_JELLYFIN_WS_RETRY_BASE_SEC", "3")
    monkeypatch.setenv("RELAYTV_JELLYFIN_WS_RETRY_MAX_SEC", "60")
    assert [jellyfin_ws.backoff_sec(n) for n in (1, 2, 3, 4, 5)] == [3.0, 6.0, 12.0, 24.0, 48.0]
    assert jellyfin_ws.backoff_sec(50) == 60.0


def test_backoff_survives_a_very_long_outage() -> None:
    """The failure count keeps climbing while a server stays down.

    Capping only the result still evaluates 2**(n-1) first, which overflows the
    float multiply around failure 1025 — roughly 17 hours in at the default
    delay — and the OverflowError took the socket worker down with it.
    """
    for failures in (1024, 1025, 100_000, 2**31):
        assert jellyfin_ws.backoff_sec(failures) == 60.0


def test_a_slow_command_does_not_stall_keepalives() -> None:
    """Starting mpv takes seconds; the reader must not wait for it.

    If commands ran inline, this ForceKeepAlive would go unanswered for the
    length of the play and the server would drop the session mid-handoff.
    """
    started = threading.Event()
    release = threading.Event()

    def _slow_sink(action, payload, **kw):
        started.set()
        release.wait(5.0)
        return {"ok": True}

    jellyfin_receiver.register_command_sink(_slow_sink)
    session = _session()
    try:
        worker = threading.Thread(target=jellyfin_ws._command_worker, args=(session,), daemon=True)
        worker.start()

        ws = FakeSocket(
            [
                json.dumps({"MessageType": "ForceKeepAlive", "Data": 2}),
                json.dumps({"MessageType": "Play", "Data": {"ItemIds": ["x"]}}),
            ]
        )
        jellyfin_ws._read_loop(ws, session)

        assert started.is_set(), "command never reached the worker"
        assert ws.sent, "keepalive was starved by the in-flight command"
    finally:
        release.set()
        jellyfin_receiver.register_command_sink(None)


def test_command_backlog_is_bounded() -> None:
    """A burst while playback is starting is dropped, not replayed later."""
    session = jellyfin_ws._Session(("u", "d", "f"))
    session.commands = jellyfin_ws.queue.Queue(maxsize=2)
    for _ in range(5):
        jellyfin_ws._submit(session, "play", {})
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


def test_receiver_status_reports_shared_control_and_catalog_sources(monkeypatch) -> None:
    monkeypatch.setitem(jellyfin_receiver._STATUS, "enabled", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "running", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_url", "http://jf.local:8096")
    monkeypatch.setitem(jellyfin_receiver._STATUS, "api_key_configured", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "authenticated", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "last_register_ok", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "media_control_verified", True)
    monkeypatch.setattr(
        jellyfin_receiver,
        "_control_socket_status",
        lambda: {"enabled": True, "available": True, "connected": True},
    )

    st = jellyfin_receiver.status()

    assert st["control_auth_source"] == "api_key"
    assert st["cast_target_scope"] == "shared"
    assert st["cast_target_ready"] is True
    assert st["catalog_auth_source"] == "user_session"
    assert st["catalog_ready"] is True


def test_receiver_status_reports_legacy_login_as_user_scoped(monkeypatch) -> None:
    monkeypatch.setitem(jellyfin_receiver._STATUS, "enabled", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "running", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_url", "http://jf.local:8096")
    monkeypatch.setitem(jellyfin_receiver._STATUS, "api_key_configured", False)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "authenticated", True)

    st = jellyfin_receiver.status()

    assert st["control_auth_source"] == "user_session"
    assert st["cast_target_scope"] == "user_scoped"
    assert st["catalog_auth_source"] == "user_session"
    assert st["catalog_ready"] is True


# --- dispatch seam ---------------------------------------------------------


def test_dispatch_requires_a_registered_sink() -> None:
    jellyfin_receiver.register_command_sink(None)
    assert jellyfin_receiver.command_sink_registered() is False
    with pytest.raises(RuntimeError):
        jellyfin_receiver.dispatch_command("play", {})


def test_dispatch_reaches_the_registered_sink() -> None:
    seen: list[tuple[str, dict]] = []
    jellyfin_receiver.register_command_sink(lambda action, payload, **kw: seen.append((action, payload)))
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
    monkeypatch.setattr(jellyfin_receiver, "_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(
        jellyfin_receiver,
        "register_receiver_once",
        lambda **kwargs: calls.append(1) or {"ok": True},
    )

    # Healthy: nothing to do.
    jellyfin_receiver._ensure_registration()
    assert calls == []

    jellyfin_receiver.invalidate_registration("socket_connected")
    jellyfin_receiver._ensure_registration()
    assert calls == [1]


# --- session lifetime ------------------------------------------------------


def test_proxy_is_only_passed_when_the_library_accepts_it() -> None:
    """connect() gained proxy in websockets 15.0.

    On 13.x and 14.x passing it is a TypeError on every single connection, so
    a build that lands an older websockets must degrade, not fail closed.
    """
    import inspect as _inspect

    from websockets.sync.client import connect

    supported = "proxy" in _inspect.signature(connect).parameters
    assert jellyfin_ws._PROXY_KWARG_SUPPORTED is supported


def test_connect_omits_proxy_on_an_older_websockets(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class _Conn:
        def recv(self, timeout=None):
            raise TimeoutError

        def send(self, message):
            pass

        def close(self):
            pass

    def _fake_connect(url, **kwargs):
        if "proxy" in kwargs:
            raise TypeError("create_connection() got an unexpected keyword argument 'proxy'")
        seen.update(kwargs)
        return _Conn()

    monkeypatch.setattr(jellyfin_ws, "_ws_connect", _fake_connect)
    monkeypatch.setattr(jellyfin_ws, "_PROXY_KWARG_SUPPORTED", False)
    monkeypatch.setattr(
        jellyfin_receiver, "status", lambda: {"server_url": "http://jf.lan:8096", "device_id": "relaytv-den"}
    )
    monkeypatch.setattr(jellyfin_receiver, "control_token", lambda: "tok")
    monkeypatch.setattr(jellyfin_receiver, "invalidate_registration", lambda reason="": None)

    session = _session(deadline_sec=0.0)
    jellyfin_ws._connect_once(session)

    assert "proxy" not in seen
    assert seen["max_size"] == 2**20


def test_each_session_owns_its_stop_flag() -> None:
    """A reader parked in recv() can outlive stop().

    With one module-level event, the next start would clear the flag out from
    under the survivor and it would carry on dispatching from a server nobody
    is talking to any more. A generation's flag can only ever be set.
    """
    first = jellyfin_ws._Session(("u", "d", "f1"))
    first.stop.set()
    second = jellyfin_ws._Session(("u", "d", "f2"))

    assert second.stop.is_set() is False
    assert first.stop.is_set() is True, "starting a new session un-stopped the old one"


def test_stop_closes_the_live_connection() -> None:
    """Closing is what makes stop() prompt; joining alone waits out recv."""
    closed = threading.Event()

    class _Conn:
        def close(self):
            closed.set()

    session = jellyfin_ws._Session(("u", "d", "f"))
    session.ws = _Conn()
    jellyfin_ws._CURRENT = session

    jellyfin_ws.stop()

    assert closed.is_set()
    assert session.stop.is_set()
    assert jellyfin_ws._CURRENT is None


def test_a_stopped_session_drops_queued_commands() -> None:
    """Commands queued before a server switch belong to the old server."""
    ran: list[str] = []
    jellyfin_receiver.register_command_sink(lambda action, payload, **kw: ran.append(action))
    try:
        session = jellyfin_ws._Session(("u", "d", "f"))
        session.commands.put_nowait(("play", {}))
        session.stop.set()
        jellyfin_ws._command_worker(session)
        assert ran == []
    finally:
        jellyfin_receiver.register_command_sink(None)


def test_identity_change_restarts_the_socket(monkeypatch) -> None:
    """Switching server or credentials must not leave the old socket attached.

    ensure_running() used to return on "thread is alive", so the previous
    Jellyfin server kept a live command channel to this device while the new
    session had nothing listening.
    """
    identity = {"value": ("http://old.lan:8096", "relaytv-den", "fp-old")}
    started: list[tuple[str, str, str]] = []
    stopped: list[int] = []

    monkeypatch.setattr(jellyfin_ws, "_identity", lambda: identity["value"])
    monkeypatch.setattr(jellyfin_ws, "_start", lambda ident: started.append(ident))
    monkeypatch.setattr(jellyfin_ws, "stop", lambda: stopped.append(1))

    jellyfin_ws.ensure_running()
    assert started == [("http://old.lan:8096", "relaytv-den", "fp-old")]

    # Pretend that session is now live.
    live = jellyfin_ws._Session(identity["value"])
    live.reader = threading.Thread(target=lambda: time.sleep(2.0), daemon=True)
    live.reader.start()
    jellyfin_ws._CURRENT = live
    try:
        jellyfin_ws.ensure_running()
        assert stopped == [] and len(started) == 1, "restarted an unchanged session"

        identity["value"] = ("http://new.lan:8096", "relaytv-den", "fp-new")
        jellyfin_ws.ensure_running()
        assert stopped == [1], "old socket was left attached to the old server"
        assert started[-1] == ("http://new.lan:8096", "relaytv-den", "fp-new")
    finally:
        jellyfin_ws._CURRENT = None


def test_identity_fingerprints_the_token() -> None:
    """The bound identity is compared, never displayed — keep the token out."""
    from relaytv_app.integrations import jellyfin_ws as mod

    def _fake_status():
        return {"enabled": True, "running": True, "server_url": "http://jf.lan:8096", "device_id": "relaytv-den"}

    real_status, real_token, real_sink = (
        jellyfin_receiver.status,
        jellyfin_receiver.control_token,
        jellyfin_receiver.command_sink_registered,
    )
    jellyfin_receiver.status = _fake_status
    jellyfin_receiver.control_token = lambda: "super-secret-token"
    jellyfin_receiver.command_sink_registered = lambda: True
    try:
        identity = mod._identity()
    finally:
        jellyfin_receiver.status = real_status
        jellyfin_receiver.control_token = real_token
        jellyfin_receiver.command_sink_registered = real_sink

    assert identity is not None
    assert "super-secret-token" not in "".join(identity)


def test_every_path_derives_the_same_device_id(monkeypatch) -> None:
    """Startup, connect(), and rename must agree, or one TV becomes two."""
    monkeypatch.delenv("RELAYTV_JELLYFIN_DEVICE_ID", raising=False)
    expected = jellyfin_receiver.derive_device_id()

    jellyfin_receiver.set_device_identity("Living Room")
    assert jellyfin_receiver._STATUS["device_id"] == expected
    assert jellyfin_receiver._read_config()["device_id"] == expected


# --- review round two ------------------------------------------------------


def test_device_id_survives_a_rename(monkeypatch) -> None:
    """Jellyfin keys history on DeviceId, so a rename must not mint a new one.

    Deriving it from device_name meant renaming "Living Room" to "Den" created
    a second Jellyfin session and left the first as a duplicate cast target.
    """
    monkeypatch.delenv("RELAYTV_JELLYFIN_DEVICE_ID", raising=False)

    jellyfin_receiver.set_device_identity("Living Room")
    first = jellyfin_receiver._STATUS["device_id"]
    jellyfin_receiver.set_device_identity("Den")
    second = jellyfin_receiver._STATUS["device_id"]

    assert first == second, "renaming the device changed its Jellyfin identity"
    assert jellyfin_receiver._STATUS["device_name"] == "Den", "the display name must still change"

    monkeypatch.setenv("RELAYTV_JELLYFIN_DEVICE_ID", "pinned-id")
    assert jellyfin_receiver.derive_device_id() == "pinned-id"


def test_device_id_follows_the_persisted_install_identity(monkeypatch) -> None:
    from relaytv_app import device_identity

    monkeypatch.delenv("RELAYTV_JELLYFIN_DEVICE_ID", raising=False)
    monkeypatch.setattr(device_identity, "device_id", lambda: "stable123")
    assert jellyfin_receiver.derive_device_id() == "relaytv-stable123"


def test_a_late_handshake_cannot_hijack_the_live_session(monkeypatch) -> None:
    """The dial can outlast the session that started it.

    session.ws is None for the whole handshake, so stop() cannot reach the
    connection and gives up on the join. If the socket then landed anyway it
    would publish "connected" and re-assert registration for a generation that
    no longer owns either.
    """
    invalidated: list[str] = []
    closed = threading.Event()

    class _Conn:
        def recv(self, timeout=None):
            raise AssertionError("a retired session must never read")

        def close(self):
            closed.set()

    monkeypatch.setattr(jellyfin_ws, "_ws_connect", lambda url, **kw: _Conn())
    monkeypatch.setattr(
        jellyfin_receiver, "status", lambda: {"server_url": "http://jf.lan:8096", "device_id": "relaytv-den"}
    )
    monkeypatch.setattr(jellyfin_receiver, "control_token", lambda: "tok")
    monkeypatch.setattr(jellyfin_receiver, "invalidate_registration", lambda reason="": invalidated.append(reason))

    retired = jellyfin_ws._Session(("u", "d", "f"))
    live = jellyfin_ws._Session(("u", "d", "f"))
    jellyfin_ws._CURRENT = live  # someone else is the live generation now
    jellyfin_ws._STATE["connected"] = False
    try:
        jellyfin_ws._connect_once(retired)

        assert closed.is_set(), "the orphaned socket was left open"
        assert invalidated == [], "a retired session re-asserted registration"
        assert jellyfin_ws._STATE["connected"] is False, "a retired session published its status"
    finally:
        jellyfin_ws._CURRENT = None


def test_the_live_session_still_publishes_and_reasserts(monkeypatch) -> None:
    """The guard must not break the normal path."""
    invalidated: list[str] = []

    class _Conn:
        def recv(self, timeout=None):
            raise TimeoutError

        def send(self, message):
            pass

        def close(self):
            pass

    monkeypatch.setattr(jellyfin_ws, "_ws_connect", lambda url, **kw: _Conn())
    monkeypatch.setattr(
        jellyfin_receiver, "status", lambda: {"server_url": "http://jf.lan:8096", "device_id": "relaytv-den"}
    )
    monkeypatch.setattr(jellyfin_receiver, "control_token", lambda: "tok")
    monkeypatch.setattr(jellyfin_receiver, "invalidate_registration", lambda reason="": invalidated.append(reason))

    session = _session(deadline_sec=0.5)
    jellyfin_ws._CURRENT = session
    try:
        jellyfin_ws._connect_once(session)
        assert invalidated == ["socket_connected"]
        assert jellyfin_ws._STATE["last_connect_ts"] is not None
    finally:
        jellyfin_ws._CURRENT = None


def test_connect_bounds_the_closing_handshake(monkeypatch) -> None:
    """websockets defaults to a 10s close; stop() runs on settings-save paths."""
    seen: dict[str, object] = {}

    class _Conn:
        def recv(self, timeout=None):
            raise TimeoutError

        def send(self, message):
            pass

        def close(self):
            pass

    def _fake_connect(url, **kwargs):
        seen.update(kwargs)
        return _Conn()

    monkeypatch.setattr(jellyfin_ws, "_ws_connect", _fake_connect)
    monkeypatch.setattr(
        jellyfin_receiver, "status", lambda: {"server_url": "http://jf.lan:8096", "device_id": "relaytv-den"}
    )
    monkeypatch.setattr(jellyfin_receiver, "control_token", lambda: "tok")
    monkeypatch.setattr(jellyfin_receiver, "invalidate_registration", lambda reason="": None)

    session = _session(deadline_sec=0.0)
    jellyfin_ws._CURRENT = session
    try:
        jellyfin_ws._connect_once(session)
    finally:
        jellyfin_ws._CURRENT = None

    assert seen["close_timeout"] == jellyfin_ws._CLOSE_TIMEOUT_SEC
    assert seen["close_timeout"] < 10


def test_stop_does_not_wait_on_the_closing_handshake() -> None:
    """A server that has gone away must not stall a settings save."""

    class _SlowConn:
        def close(self):
            time.sleep(5.0)

    session = jellyfin_ws._Session(("u", "d", "f"))
    session.ws = _SlowConn()
    jellyfin_ws._CURRENT = session

    t0 = time.monotonic()
    jellyfin_ws.stop()
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0, f"stop() blocked {elapsed:.1f}s on the close handshake"
    assert session.stop.is_set()


# --- review round three ----------------------------------------------------


def _parked_session(bound=("http://old.lan:8096", "relaytv-abc", "fp-old")):
    """A session whose threads outlive stop()'s join budget."""
    s = jellyfin_ws._Session(bound)
    s.reader = threading.Thread(target=lambda: time.sleep(10), daemon=True)
    s.worker = threading.Thread(target=lambda: time.sleep(10), daemon=True)
    s.reader.start()
    s.worker.start()
    return s


def test_stop_and_start_are_serialized(monkeypatch) -> None:
    """stop() leaves _CURRENT None for seconds while it joins.

    The heartbeat used to walk into that gap, start a replacement, and have its
    shared state reset by the stop() still in flight.
    """
    old = ("http://old.lan:8096", "relaytv-abc", "fp-old")
    monkeypatch.setattr(jellyfin_ws, "enabled", lambda: True)
    monkeypatch.setattr(jellyfin_ws, "_ws_connect", object())
    monkeypatch.setattr(jellyfin_ws, "_identity", lambda: old)

    victim = _parked_session(old)
    jellyfin_ws._CURRENT = victim
    order: list[str] = []

    def _heartbeat():
        time.sleep(0.3)
        jellyfin_ws.ensure_running()
        order.append("ensure_running")

    t = threading.Thread(target=_heartbeat, daemon=True)
    t.start()
    try:
        jellyfin_ws.stop()
        order.append("stop")
        t.join(timeout=10)
    finally:
        jellyfin_ws._CURRENT = None

    assert order == ["stop", "ensure_running"], f"interleaved: {order}"


def test_no_socket_opens_while_configuration_is_changing(monkeypatch) -> None:
    """Stopping then rewriting settings is not enough on its own.

    A heartbeat firing between the two opened a socket to the server being
    replaced, which then kept taking commands until a later heartbeat noticed.
    """
    old = ("http://old.lan:8096", "relaytv-abc", "fp-old")
    started: list[tuple[str, str, str]] = []
    monkeypatch.setattr(jellyfin_ws, "enabled", lambda: True)
    monkeypatch.setattr(jellyfin_ws, "_ws_connect", object())
    monkeypatch.setattr(jellyfin_ws, "_identity", lambda: old)
    monkeypatch.setattr(jellyfin_ws, "_start", lambda ident: started.append(ident))

    jellyfin_ws._CURRENT = _parked_session(old)
    try:
        with jellyfin_ws.suspended():
            for _ in range(5):
                jellyfin_ws.ensure_running()
            assert started == [], "a socket was opened mid-transaction"
        # Normal service resumes once the transaction closes.
        jellyfin_ws.ensure_running()
        assert started == [old]
    finally:
        jellyfin_ws._CURRENT = None


def test_suspension_is_released_even_if_the_body_raises() -> None:
    """A failed settings save must not wedge the socket down forever."""
    with pytest.raises(RuntimeError):
        with jellyfin_ws.suspended():
            raise RuntimeError("settings write failed")
    assert jellyfin_ws._SUSPENDED == 0


def test_a_retired_dial_failure_does_not_report_on_the_live_session(monkeypatch) -> None:
    """The success path was guarded; the failure path was not.

    A handshake that fails after its generation was retired would pin the
    replacement's ws_last_error to an address nobody is talking to.
    """
    live = jellyfin_ws._Session(("http://new.lan:8096", "relaytv-abc", "fp-new"))
    jellyfin_ws._CURRENT = live
    with jellyfin_ws._LOCK:
        jellyfin_ws._STATE["last_error"] = None
        jellyfin_ws._STATE["reconnects"] = 0

    retired = jellyfin_ws._Session(("http://old.lan:8096", "relaytv-abc", "fp-old"))

    def _dial(session):
        # stop() lands while this handshake is in flight, as it does when
        # settings are saved mid-dial.
        session.stop.set()
        raise RuntimeError("old server refused the handshake at http://old.lan:8096")

    monkeypatch.setattr(jellyfin_ws, "_connect_once", _dial)
    monkeypatch.setattr(jellyfin_ws, "_ready", lambda: True)
    try:
        jellyfin_ws._socket_worker(retired)
        assert jellyfin_ws.status()["last_error"] is None
        assert jellyfin_ws.status()["reconnects"] == 0
    finally:
        jellyfin_ws._CURRENT = None


def test_the_live_session_still_reports_its_own_failures(monkeypatch) -> None:
    """The guard must not silence the generation that actually owns the state."""
    live = jellyfin_ws._Session(("http://jf.lan:8096", "relaytv-abc", "fp"))
    jellyfin_ws._CURRENT = live
    with jellyfin_ws._LOCK:
        jellyfin_ws._STATE["last_error"] = None
        jellyfin_ws._STATE["reconnects"] = 0

    calls = {"n": 0}

    def _dial(session):
        calls["n"] += 1
        if calls["n"] >= 2:
            session.stop.set()
        raise RuntimeError("connection refused")

    monkeypatch.setattr(jellyfin_ws, "_connect_once", _dial)
    monkeypatch.setattr(jellyfin_ws, "_ready", lambda: True)
    monkeypatch.setattr(jellyfin_ws, "backoff_sec", lambda failures: 0.01)
    try:
        jellyfin_ws._socket_worker(live)
        assert "connection refused" in str(jellyfin_ws.status()["last_error"])
        assert jellyfin_ws.status()["reconnects"] >= 1
    finally:
        jellyfin_ws._CURRENT = None


def test_disconnect_cannot_leave_a_socket_behind(monkeypatch) -> None:
    """Teardown must be a transaction, not a sequence.

    _stop_worker gives the heartbeat one second to notice; a heartbeat parked
    in a network call outlives that. It only has to reach ensure_running() once
    while ``running`` is still true to leave a socket connected forever —
    nothing retires it, because the heartbeat that would is already stopped.
    """
    started: list[tuple[str, str, str]] = []
    monkeypatch.setattr(jellyfin_ws, "enabled", lambda: True)
    monkeypatch.setattr(jellyfin_ws, "_ws_connect", object())
    monkeypatch.setattr(jellyfin_ws, "_start", lambda ident: started.append(ident))

    for key, value in (
        ("enabled", True), ("running", True),
        ("server_url", "http://jf.lan:8096"), ("device_id", "relaytv-abc"),
    ):
        monkeypatch.setitem(jellyfin_receiver._STATUS, key, value)
    monkeypatch.setattr(jellyfin_receiver, "control_token", lambda: "tok")
    monkeypatch.setattr(jellyfin_receiver, "command_sink_registered", lambda: True)
    # The real join waits a second for a parked heartbeat and then gives up.
    monkeypatch.setattr(jellyfin_receiver, "_stop_worker", lambda: time.sleep(1.0))

    jellyfin_ws._CURRENT = _parked_session(("http://jf.lan:8096", "relaytv-abc", "fp"))

    def _late_heartbeat():
        time.sleep(0.2)  # blocks on the lifecycle lock the teardown is holding
        jellyfin_receiver._ensure_control_socket()

    hb = threading.Thread(target=_late_heartbeat, daemon=True)
    hb.start()
    try:
        jellyfin_receiver.disconnect()
        hb.join(timeout=10)
        assert jellyfin_receiver._STATUS["running"] is False
        assert started == [], "a late heartbeat opened a socket nothing will retire"
    finally:
        jellyfin_ws._CURRENT = None


def test_concurrent_server_switches_do_not_interleave(monkeypatch) -> None:
    """The connect endpoints are sync defs, so FastAPI runs them on real threads.

    Two overlapping switches each installed their own URL and token and then
    raced their detection probes; the slower one landing last left the new
    server's address beside the old server's identity.
    """
    monkeypatch.setattr(jellyfin_ws, "enabled", lambda: False)
    monkeypatch.setattr(jellyfin_receiver, "_start_worker", lambda: None)
    monkeypatch.setattr(jellyfin_receiver, "_stop_worker", lambda: None)
    monkeypatch.setattr(jellyfin_receiver, "authenticate_once", lambda **kwargs: {"ok": False})
    monkeypatch.setattr(jellyfin_receiver, "_catalog_cache_clear", lambda: None)

    def _detect(server_url, timeout_sec=3.0):
        if "a.local" in server_url:
            time.sleep(1.0)  # the distant or struggling server
            return {"ok": True, "server_type": "jellyfin", "product_name": "Server A"}
        return {"ok": True, "server_type": "emby", "product_name": "Server B"}

    monkeypatch.setattr(jellyfin_receiver, "detect_server_type", _detect)
    monkeypatch.setattr(
        jellyfin_receiver,
        "_persist_server_type",
        lambda st, pn="", **kw: jellyfin_receiver._STATUS.update({"server_type": st, "server_product_name": pn}),
    )

    threads = [
        threading.Thread(target=jellyfin_receiver.connect, kwargs={"server_url": "http://a.local:8096"}, daemon=True),
        threading.Thread(target=jellyfin_receiver.connect, kwargs={"server_url": "http://b.local:8096"}, daemon=True),
    ]
    threads[0].start()
    time.sleep(0.2)  # B arrives while A is still probing
    threads[1].start()
    for t in threads:
        t.join(timeout=20)

    url = str(jellyfin_receiver._STATUS["server_url"])
    product = str(jellyfin_receiver._STATUS["server_product_name"])
    expected = "Server B" if "b.local" in url else "Server A"
    assert product == expected, f"torn state: {url} reported as {product}"


# --- heartbeat generations -------------------------------------------------


def test_a_retired_heartbeat_generation_stays_retired(monkeypatch) -> None:
    """The heartbeat had the same reusable-stop-event bug as the socket.

    _stop_worker gives it one second; a worker inside a network call outlives
    that, and the next start cleared the shared flag out from under it. Two
    generations then authenticated, registered, posted progress, and probed
    server identity in parallel.
    """
    seen: dict[int, bool] = {}
    lock = threading.Lock()

    def _probe(stop):
        me = threading.get_ident()
        time.sleep(1.2)  # parked in a network call
        with lock:
            seen[me] = not stop.is_set()

    monkeypatch.setitem(jellyfin_receiver._STATUS, "enabled", True)
    monkeypatch.setattr(jellyfin_receiver, "_heartbeat_worker", _probe)

    jellyfin_receiver._start_worker()
    first = jellyfin_receiver._THREAD
    time.sleep(0.1)
    jellyfin_receiver._stop_worker()  # join times out; `first` is still sleeping
    jellyfin_receiver._start_worker()  # rapid reconnect
    second = jellyfin_receiver._THREAD
    try:
        assert first is not second
        time.sleep(2.0)
        running = sum(1 for v in seen.values() if v)
        assert len(seen) == 2, "both generations should have reported"
        assert running == 1, f"{running} generations think they are live"
    finally:
        jellyfin_receiver._stop_worker()


def test_an_in_flight_probe_cannot_publish_over_new_settings(monkeypatch) -> None:
    """Serializing transactions does not stop work that started before one.

    A detection probe of the outgoing server takes seconds; landing late left
    the new server's address labelled with the old server's identity.
    """
    monkeypatch.setattr(jellyfin_ws, "enabled", lambda: False)
    monkeypatch.setattr(jellyfin_receiver, "_start_worker", lambda: None)
    monkeypatch.setattr(jellyfin_receiver, "_stop_worker", lambda: None)
    monkeypatch.setattr(jellyfin_receiver, "authenticate_once", lambda **kwargs: {"ok": False})
    monkeypatch.setattr(jellyfin_receiver, "_catalog_cache_clear", lambda: None)
    for key, value in (
        ("enabled", True), ("running", True), ("authenticated", True),
        ("server_url", "http://old.local:8096"), ("server_type", "emby"),
        ("server_product_name", "Old Server"), ("last_detect_ok", False), ("last_detect_ts", None),
    ):
        monkeypatch.setitem(jellyfin_receiver._STATUS, key, value)

    def _detect(server_url, timeout_sec=3.0):
        if "old.local" in server_url:
            time.sleep(1.2)
            return {"ok": True, "server_type": "emby", "product_name": "Old Server"}
        return {"ok": True, "server_type": "jellyfin", "product_name": "New Server"}

    monkeypatch.setattr(jellyfin_receiver, "detect_server_type", _detect)
    monkeypatch.setattr(
        jellyfin_receiver,
        "_persist_server_type",
        lambda st, pn="", **kw: jellyfin_receiver._STATUS.update({"server_type": st, "server_product_name": pn}),
    )

    probe = threading.Thread(target=jellyfin_receiver._maybe_retry_detection, daemon=True)
    probe.start()
    time.sleep(0.3)  # the probe is in flight when settings change
    jellyfin_receiver.connect(server_url="http://new.local:8096")
    probe.join(timeout=15)

    assert "new.local" in str(jellyfin_receiver._STATUS["server_url"])
    assert jellyfin_receiver._STATUS["server_product_name"] == "New Server"


def test_config_generation_advances_on_entry_and_on_commit() -> None:
    """Both ends matter.

    Entering invalidates work that started under the previous configuration.
    Leaving invalidates work that started *mid*-transaction, which would
    otherwise hold a generation that was current while the settings it read
    were only half-installed.
    """
    before = jellyfin_receiver.config_generation()
    with jellyfin_receiver._control_socket_suspended():
        during = jellyfin_receiver.config_generation()
        assert during == before + 1
    after = jellyfin_receiver.config_generation()

    assert after == during + 1
    assert jellyfin_receiver._config_generation_current(before) is False
    assert jellyfin_receiver._config_generation_current(during) is False
    assert jellyfin_receiver._config_generation_current(after) is True


def test_work_started_mid_transaction_is_also_invalidated(monkeypatch) -> None:
    """The generation must advance on commit, not only on entry.

    Bumping only on the way in leaves a window: work that captures the new
    generation while the transaction body is still installing settings read the
    *old* URL and then published as though it were current. Driven
    deterministically — the real window is microseconds wide, so racing it with
    a sleep would prove nothing either way.
    """
    monkeypatch.setattr(jellyfin_ws, "enabled", lambda: False)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_type", "emby")
    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_product_name", "Old Server")
    monkeypatch.setattr(
        jellyfin_receiver,
        "detect_server_type",
        lambda url, timeout_sec=3.0: {"ok": True, "server_type": "emby", "product_name": "Old Server"},
    )

    # A probe that captured its generation inside a transaction, before the
    # settings it read had finished being installed.
    with jellyfin_receiver._control_socket_suspended():
        captured = jellyfin_receiver.config_generation()

    result = jellyfin_receiver._run_detection("http://old.local:8096", generation=captured)

    assert result == {"ok": False, "reason": "config_changed"}
    assert jellyfin_receiver._STATUS["server_product_name"] == "Old Server"


def test_an_in_flight_progress_post_cannot_land_after_disconnect(monkeypatch) -> None:
    """Progress posts take up to three seconds.

    One completing after a disconnect set connected=True while running was
    already false — a session reported live that nothing was driving.
    """
    monkeypatch.setattr(jellyfin_ws, "enabled", lambda: False)
    monkeypatch.setattr(jellyfin_receiver, "_start_worker", lambda: None)
    monkeypatch.setattr(jellyfin_receiver, "_stop_worker", lambda: None)
    monkeypatch.setattr(jellyfin_receiver, "_catalog_cache_clear", lambda: None)
    for key, value in (
        ("enabled", True), ("running", True), ("connected", False),
        ("server_url", "http://jf.lan:8096"),
    ):
        monkeypatch.setitem(jellyfin_receiver._STATUS, key, value)
    monkeypatch.setattr(
        jellyfin_receiver,
        "_post_json_for",
        lambda context, path, payload, timeout=3.0: time.sleep(1.0),
    )

    done = threading.Event()

    def _post():
        jellyfin_receiver.send_progress_payload_once({"ItemId": "x"})
        done.set()

    t = threading.Thread(target=_post, daemon=True)
    t.start()
    time.sleep(0.2)  # the post is in flight
    jellyfin_receiver.disconnect()
    assert done.wait(10)

    assert jellyfin_receiver._STATUS["running"] is False
    assert jellyfin_receiver._STATUS["connected"] is False, "a retired post reported the session live"


def test_authentication_captures_its_inputs_with_its_generation(monkeypatch) -> None:
    """Reading the URL and stamping the generation separately is its own race."""
    context = jellyfin_receiver._request_context()
    assert isinstance(context.generation, int)
    assert isinstance(context.server_url, str)
    assert repr(context).startswith("<relaytv_app.integrations.jellyfin_receiver._RequestContext object at ")
    # The snapshot is taken under one lock, so it cannot straddle a transaction.
    before = context.generation
    with jellyfin_receiver._control_socket_suspended():
        pass
    assert jellyfin_receiver._request_context().generation != before


def test_request_context_separates_shared_control_from_catalog_login(monkeypatch) -> None:
    monkeypatch.setattr(jellyfin_receiver, "_API_KEY", "shared-api-key")
    monkeypatch.setattr(jellyfin_receiver, "_ACCESS_TOKEN", "catalog-user-token")

    context = jellyfin_receiver._request_context()

    assert context.control_token == "shared-api-key"
    assert context.catalog_token == "catalog-user-token"
    assert jellyfin_receiver.control_token() == "shared-api-key"
    assert jellyfin_receiver.catalog_token() == "catalog-user-token"


def test_catalog_login_does_not_change_shared_socket_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        jellyfin_receiver,
        "status",
        lambda: {
            "enabled": True,
            "running": True,
            "server_url": "http://jf.local:8096",
            "device_id": "relaytv-device",
        },
    )
    monkeypatch.setattr(jellyfin_receiver, "command_sink_registered", lambda: True)
    monkeypatch.setattr(jellyfin_receiver, "_API_KEY", "shared-api-key")
    monkeypatch.setattr(jellyfin_receiver, "_ACCESS_TOKEN", "")
    before = jellyfin_ws._identity()

    monkeypatch.setattr(jellyfin_receiver, "_ACCESS_TOKEN", "catalog-user-token")

    assert jellyfin_ws._identity() == before


def test_publish_is_a_single_critical_section() -> None:
    """Check and write must not be separable, or a transaction slips between."""
    applied: list[int] = []
    gen = jellyfin_receiver.config_generation()
    assert jellyfin_receiver._publish(gen, lambda: applied.append(1)) is True
    assert applied == [1]
    assert jellyfin_receiver._publish(gen - 1, lambda: applied.append(2)) is False
    assert applied == [1], "a stale generation was allowed to write"


def test_authentication_cannot_publish_after_its_snapshot_is_disconnected(monkeypatch) -> None:
    """The enabled/running decision belongs to the same snapshot as the token."""
    monkeypatch.setattr(jellyfin_ws, "enabled", lambda: False)
    monkeypatch.setattr(jellyfin_receiver, "_stop_worker", lambda: None)
    monkeypatch.setattr(jellyfin_receiver, "_catalog_cache_clear", lambda: None)
    for key, value in (
        ("enabled", True),
        ("running", True),
        ("authenticated", False),
        ("server_url", "http://old.local:8096"),
        ("auth_user_configured", True),
    ):
        monkeypatch.setitem(jellyfin_receiver._STATUS, key, value)
    monkeypatch.setattr(jellyfin_receiver, "_AUTH_USERNAME", "old-user")
    monkeypatch.setattr(jellyfin_receiver, "_AUTH_PASSWORD", "old-password")
    monkeypatch.setattr(jellyfin_receiver, "_ACCESS_TOKEN", "")

    original_context = jellyfin_receiver._request_context

    def _capture_then_disconnect():
        context = original_context()
        jellyfin_receiver.disconnect()
        return context

    monkeypatch.setattr(jellyfin_receiver, "_request_context", _capture_then_disconnect)

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "AccessToken": "old-token",
                    "User": {"Id": "old-user-id"},
                    "SessionInfo": {"Id": "old-session"},
                }
            ).encode()

    monkeypatch.setattr(jellyfin_receiver._urlrequest, "urlopen", lambda req, timeout=5: _Response())

    result = jellyfin_receiver.authenticate_once()

    assert result == {"ok": False, "reason": "config_changed"}
    assert jellyfin_receiver._STATUS["running"] is False
    assert jellyfin_receiver._STATUS["authenticated"] is False
    assert jellyfin_receiver._ACCESS_TOKEN == ""


def test_catalog_auth_failure_does_not_degrade_shared_control(monkeypatch) -> None:
    for key, value in (
        ("enabled", True),
        ("running", True),
        ("connected", True),
        ("server_url", "http://jf.local:8096"),
        ("api_key_configured", True),
        ("authenticated", False),
        ("last_register_ok", True),
        ("media_control_verified", True),
        ("last_error", "control-plane-marker"),
    ):
        monkeypatch.setitem(jellyfin_receiver._STATUS, key, value)
    monkeypatch.setattr(jellyfin_receiver, "_API_KEY", "shared-api-key")
    monkeypatch.setattr(jellyfin_receiver, "_ACCESS_TOKEN", "")
    monkeypatch.setattr(jellyfin_receiver, "_AUTH_USERNAME", "catalog-user")
    monkeypatch.setattr(jellyfin_receiver, "_AUTH_PASSWORD", "bad-password")
    monkeypatch.setattr(
        jellyfin_receiver._urlrequest,
        "urlopen",
        lambda req, timeout=5: (_ for _ in ()).throw(OSError("catalog login rejected")),
    )

    result = jellyfin_receiver.authenticate_once()
    st = jellyfin_receiver.status()

    assert result["reason"] == "auth_failed"
    assert st["connected"] is True
    assert st["sync_health"] == "ok"
    assert st["control_auth_source"] == "api_key"
    assert st["cast_target_scope"] == "shared"
    assert st["last_error"] == "control-plane-marker"
    assert "catalog login rejected" in str(st["last_auth_error"])


def test_context_http_request_never_reloads_a_new_servers_token(monkeypatch) -> None:
    """A URL and token captured together stay together even after a switch."""
    monkeypatch.setattr(jellyfin_ws, "enabled", lambda: False)
    monkeypatch.setattr(jellyfin_receiver, "_API_KEY", "old-control-token")
    monkeypatch.setattr(jellyfin_receiver, "_ACCESS_TOKEN", "old-catalog-token")
    with jellyfin_receiver._LOCK:
        jellyfin_receiver._STATUS.update(
            {
                "enabled": True,
                "running": True,
                "server_url": "http://old.local:8096",
                "device_id": "old-device",
            }
        )
    context = jellyfin_receiver._request_context()

    with jellyfin_receiver._control_socket_suspended():
        with jellyfin_receiver._LOCK:
            jellyfin_receiver._STATUS["server_url"] = "http://new.local:8096"
            jellyfin_receiver._STATUS["device_id"] = "new-device"
            jellyfin_receiver._API_KEY = "new-control-token"
            jellyfin_receiver._ACCESS_TOKEN = "new-catalog-token"

    requests: list[tuple[str, str | None]] = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def _capture(req, timeout=3.0):
        requests.append((req.full_url, req.get_header("X-emby-token")))
        return _Response()

    monkeypatch.setattr(jellyfin_receiver._urlrequest, "urlopen", _capture)
    jellyfin_receiver._post_json_for(context, "/Sessions/Playing/Progress", {"ItemId": "x"})

    assert requests == [("http://old.local:8096/Sessions/Playing/Progress", "old-control-token")]


def test_registration_post_and_readback_share_one_context(monkeypatch) -> None:
    seen: list[tuple[str, int, str, str]] = []
    for key, value in (
        ("enabled", True),
        ("running", True),
        ("server_url", "http://jf.local:8096"),
        ("device_id", "relaytv-device"),
    ):
        monkeypatch.setitem(jellyfin_receiver._STATUS, key, value)
    monkeypatch.setattr(jellyfin_receiver, "_API_KEY", "shared-api-key")
    monkeypatch.setattr(jellyfin_receiver, "_ACCESS_TOKEN", "session-token")

    def _post(context, path, payload, timeout=3.0):
        seen.append(("post", id(context), context.server_url, context.control_token))

    def _readback(context, device_id, timeout=3.0):
        seen.append(("readback", id(context), context.server_url, context.control_token))
        return {"DeviceId": device_id, "SupportsMediaControl": True}

    monkeypatch.setattr(jellyfin_receiver, "_post_json_for", _post)
    monkeypatch.setattr(jellyfin_receiver, "_read_session_capabilities_for", _readback)

    result = jellyfin_receiver.register_receiver_once()

    assert result["ok"] is True
    assert seen == [
        ("post", seen[0][1], "http://jf.local:8096", "shared-api-key"),
        ("readback", seen[0][1], "http://jf.local:8096", "shared-api-key"),
    ]


def test_registration_switch_discards_the_old_context_result(monkeypatch) -> None:
    monkeypatch.setattr(jellyfin_ws, "enabled", lambda: False)
    for key, value in (
        ("enabled", True),
        ("running", True),
        ("connected", False),
        ("server_url", "http://old.local:8096"),
        ("device_id", "old-device"),
        ("media_control_verified", None),
    ):
        monkeypatch.setitem(jellyfin_receiver._STATUS, key, value)
    monkeypatch.setattr(jellyfin_receiver, "_API_KEY", "")
    monkeypatch.setattr(jellyfin_receiver, "_ACCESS_TOKEN", "old-token")
    posted: list[tuple[str, str, str]] = []

    def _post_then_switch(context, path, payload, timeout=3.0):
        posted.append((_context_url(context, path), context.device_id, context.control_token))
        with jellyfin_receiver._control_socket_suspended():
            with jellyfin_receiver._LOCK:
                jellyfin_receiver._STATUS["server_url"] = "http://new.local:8096"
                jellyfin_receiver._STATUS["device_id"] = "new-device"
                jellyfin_receiver._ACCESS_TOKEN = "new-token"

    def _context_url(context, path):
        return jellyfin_receiver._context_url(context, path)

    monkeypatch.setattr(jellyfin_receiver, "_post_json_for", _post_then_switch)
    monkeypatch.setattr(
        jellyfin_receiver,
        "_read_session_capabilities_for",
        lambda context, device_id, timeout=3.0: {
            "DeviceId": device_id,
            "SupportsMediaControl": True,
        },
    )

    result = jellyfin_receiver.register_receiver_once()

    assert result == {"ok": False, "reason": "config_changed"}
    assert posted == [("http://old.local:8096/Sessions/Capabilities/Full", "old-device", "old-token")]
    assert jellyfin_receiver._STATUS["server_url"] == "http://new.local:8096"
    assert jellyfin_receiver._STATUS["connected"] is False
    assert jellyfin_receiver._STATUS["media_control_verified"] is None


def test_manual_server_type_change_invalidates_an_in_flight_detection(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(jellyfin_ws, "enabled", lambda: False)
    monkeypatch.setattr(jellyfin_receiver.runtime_config, "set_value", lambda key, value: None)
    monkeypatch.setattr(state, "update_settings", lambda values: None)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_type", "jellyfin")

    def _detect(url, timeout_sec=3.0):
        started.set()
        assert release.wait(5)
        return {"ok": True, "server_type": "jellyfin", "product_name": "Jellyfin Server"}

    monkeypatch.setattr(jellyfin_receiver, "detect_server_type", _detect)
    result: list[dict[str, object]] = []
    probe = threading.Thread(
        target=lambda: result.append(jellyfin_receiver._run_detection("http://jf.local:8096")),
        daemon=True,
    )
    probe.start()
    assert started.wait(5)
    jellyfin_receiver.set_server_type("emby")
    release.set()
    probe.join(timeout=5)

    assert result == [{"ok": False, "reason": "config_changed"}]
    assert jellyfin_receiver._STATUS["server_type"] == "emby"


def test_configuration_transaction_advances_generation_when_body_raises() -> None:
    before = jellyfin_receiver.config_generation()
    with pytest.raises(RuntimeError):
        with jellyfin_receiver._configuration_transaction(suspend_socket=False):
            during = jellyfin_receiver.config_generation()
            raise RuntimeError("settings failed")

    assert during == before + 1
    assert jellyfin_receiver.config_generation() == before + 2


def test_retired_registration_does_not_schedule_retry_state(monkeypatch) -> None:
    monkeypatch.setitem(jellyfin_receiver._STATUS, "enabled", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "running", True)
    monkeypatch.setitem(jellyfin_receiver._STATUS, "server_url", "http://old.local:8096")
    monkeypatch.setitem(jellyfin_receiver._STATUS, "connected", False)
    monkeypatch.setattr(jellyfin_receiver, "_ACCESS_TOKEN", "old-token")
    monkeypatch.setattr(jellyfin_receiver, "_REGISTER_RETRY_FAILURES", 0)
    monkeypatch.setattr(jellyfin_receiver, "_NEXT_REGISTER_RETRY_TS", 0.0)

    def _retire_while_registering(*, _context=None):
        with jellyfin_receiver._configuration_transaction(suspend_socket=False):
            pass
        return {"ok": False, "reason": "config_changed"}

    monkeypatch.setattr(jellyfin_receiver, "register_receiver_once", _retire_while_registering)

    jellyfin_receiver._ensure_registration(now_ts=100.0)

    assert jellyfin_receiver._REGISTER_RETRY_FAILURES == 0
    assert jellyfin_receiver._NEXT_REGISTER_RETRY_TS == 0.0


def test_stale_retry_scheduling_is_rejected_at_the_publish() -> None:
    """The guard on _schedule_register_retry covers its own window.

    test_retired_registration_does_not_schedule_retry_state exercises the
    earlier `config_changed` return, so it passes with or without this guard.
    The window this closes is narrower: registration fails for an ordinary
    reason and settings change before the backoff is recorded, which would
    otherwise pin a retry deadline belonging to the previous server.
    """
    stale = jellyfin_receiver.config_generation() - 1
    before_failures = jellyfin_receiver._REGISTER_RETRY_FAILURES
    before_ts = jellyfin_receiver._NEXT_REGISTER_RETRY_TS

    assert jellyfin_receiver._schedule_register_retry(100.0, 5, 30.0, generation=stale) is False
    assert jellyfin_receiver._REGISTER_RETRY_FAILURES == before_failures
    assert jellyfin_receiver._NEXT_REGISTER_RETRY_TS == before_ts

    assert jellyfin_receiver._clear_register_retry_state(generation=stale) is False

    # The current generation still writes.
    current = jellyfin_receiver.config_generation()
    assert jellyfin_receiver._schedule_register_retry(100.0, 5, 30.0, generation=current) is True
    assert jellyfin_receiver._REGISTER_RETRY_FAILURES == 5
    jellyfin_receiver._clear_register_retry_state()


def test_teardown_does_not_hang_on_a_heartbeat_blocked_in_a_publish(monkeypatch) -> None:
    """The one interleaving that looks like a cycle.

    Teardown holds _TRANSACTION and then joins the heartbeat, while the
    heartbeat wants _TRANSACTION to publish a detection result. The join can
    never succeed, so it must stay bounded — an unbounded join here is a
    deadlock, and nothing else in the design would catch it.
    """
    monkeypatch.setattr(jellyfin_ws, "enabled", lambda: False)
    monkeypatch.setattr(jellyfin_receiver, "_catalog_cache_clear", lambda: None)
    for key, value in (
        ("enabled", True), ("running", True), ("authenticated", True),
        ("server_url", "http://jf.lan:8096"), ("server_type", "emby"),
        ("server_product_name", "Old Server"),
        ("last_detect_ok", False), ("last_detect_ts", None),
    ):
        monkeypatch.setitem(jellyfin_receiver._STATUS, key, value)

    teardown_holds_lock = threading.Event()
    heartbeat_started = threading.Event()
    outcome: dict = {}

    def _detect(url, timeout_sec=3.0):
        # Finish the probe only once teardown owns the transaction, so the
        # publish that follows is guaranteed to block on it.
        teardown_holds_lock.wait(10)
        return {"ok": True, "server_type": "emby", "product_name": "Old Server"}

    monkeypatch.setattr(jellyfin_receiver, "detect_server_type", _detect)
    monkeypatch.setattr(
        jellyfin_receiver,
        "_persist_server_type",
        lambda st, pn="", **kw: jellyfin_receiver._STATUS.update(
            {"server_type": st, "server_product_name": pn}
        ),
    )

    def _heartbeat():
        gen = jellyfin_receiver.config_generation()
        heartbeat_started.set()
        outcome["detection"] = jellyfin_receiver._run_detection("http://jf.lan:8096", generation=gen)

    hb = threading.Thread(target=_heartbeat, daemon=True)
    hb.start()
    assert heartbeat_started.wait(5)

    def _stop_worker():
        teardown_holds_lock.set()
        time.sleep(0.2)  # let the heartbeat reach the blocked publish
        hb.join(timeout=1.0)

    monkeypatch.setattr(jellyfin_receiver, "_stop_worker", _stop_worker)

    started = time.monotonic()
    jellyfin_receiver.disconnect()
    elapsed = time.monotonic() - started
    hb.join(timeout=10)

    assert elapsed < 6.0, f"teardown took {elapsed:.1f}s — the join is no longer bounded"
    assert hb.is_alive() is False
    assert jellyfin_receiver._STATUS["running"] is False
    # The heartbeat wakes after teardown releases and discards its own result.
    assert outcome["detection"] == {"ok": False, "reason": "config_changed"}
    assert jellyfin_receiver._STATUS["server_product_name"] == "Old Server"


def test_a_retired_session_cannot_start_playback(monkeypatch) -> None:
    """Setting session.stop cannot cancel a command already running.

    Starting mpv takes 6-12s on a Pi and stop() only joins for two, so a
    disconnect or server switch mid-Play used to let the retired command drive
    the player anyway — content from a server we had already left.
    """
    effects: list[str] = []
    started = threading.Event()

    def _slow_ingress(action, payload, *, guard=None):
        started.set()
        time.sleep(1.5)  # resolution + mpv startup, uncancellable
        if guard is not None and not guard():
            return {"ok": False, "reason": "session_retired"}
        effects.append(str(payload.get("server")))
        return {"ok": True}

    jellyfin_receiver.register_command_sink(_slow_ingress)
    session = jellyfin_ws._Session(("http://old.lan:8096", "relaytv-abc", "fp-old"))
    jellyfin_ws._CURRENT = session
    session.worker = threading.Thread(target=jellyfin_ws._command_worker, args=(session,), daemon=True)
    session.reader = threading.Thread(target=lambda: session.stop.wait(30), daemon=True)
    session.worker.start()
    session.reader.start()

    session.commands.put_nowait(("play", {"server": "OLD"}))
    assert started.wait(5), "command never reached the worker"

    jellyfin_ws.stop()  # server switch lands mid-Play
    time.sleep(2.5)

    assert effects == [], "a retired session drove the player"


def test_the_live_session_still_starts_playback() -> None:
    """The guard must not block the ordinary path."""
    effects: list[str] = []
    done = threading.Event()

    def _ingress(action, payload, *, guard=None):
        if guard is not None and not guard():
            done.set()
            return {"ok": False, "reason": "session_retired"}
        effects.append(str(payload.get("server")))
        done.set()
        return {"ok": True}

    jellyfin_receiver.register_command_sink(_ingress)
    session = jellyfin_ws._Session(("http://jf.lan:8096", "relaytv-abc", "fp"))
    jellyfin_ws._CURRENT = session
    session.worker = threading.Thread(target=jellyfin_ws._command_worker, args=(session,), daemon=True)
    session.worker.start()
    try:
        session.commands.put_nowait(("play", {"server": "LIVE"}))
        assert done.wait(5)
        assert effects == ["LIVE"]
    finally:
        session.stop.set()
        jellyfin_ws._CURRENT = None


def test_handle_command_rechecks_ownership_before_touching_the_player(monkeypatch) -> None:
    """The service layer is where the guard has to bite.

    Everything before playback_service.play_now is network work that cannot be
    interrupted, so the check happens immediately before the transition.
    """
    from relaytv_app.integrations import jellyfin_service

    dispatched: list[str] = []
    controls = {
        name: (lambda *a, _n=name, **kw: dispatched.append(_n))
        for name in ("stop", "pause", "resume", "seek", "seek_relative", "next", "previous", "set_volume", "mute")
    }
    monkeypatch.setitem(jellyfin_receiver._STATUS, "enabled", True)
    monkeypatch.setattr(jellyfin_service, "emit_progress_hint", lambda: None)

    class _Req:
        action = "pause"
        url = None
        start_pos = None
        use_ytdlp = True
        payload: dict = {}

    ui = {
        "toast": lambda **kw: None,
        "notification_display_sec": lambda: 4.0,
        "queue_event": lambda event, **kw: None,
        "jellyfin_event": lambda event, **kw: None,
    }

    out = jellyfin_service.handle_command(_Req(), controls=controls, ui=ui, guard=lambda: False)
    assert out["reason"] == "session_retired"
    assert dispatched == [], "a retired command reached the player"

    out = jellyfin_service.handle_command(_Req(), controls=controls, ui=ui, guard=lambda: True)
    assert out["ok"] is True
    assert dispatched == ["pause"]
