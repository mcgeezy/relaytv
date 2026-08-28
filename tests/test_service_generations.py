# SPDX-License-Identifier: GPL-3.0-only
"""Background services must own their generation (F05, F06, F12).

Three services shared the same shape of defect: a module-level stop event that
a restart cleared out from under the previous worker, a publish that did not
check whether it still owned the slot, and a failed startup that closed the
wrong object. The pattern they now follow is the one jellyfin_ws.py already
uses — per-generation stop flags, and a publish that re-checks ownership.
"""
import threading
import time

import pytest

from relaytv_app import discovery_mdns, postlive_relay
from relaytv_app.integrations import iptv_service


class _FakeZeroconf:
    """Records whether it was closed, which is the whole point of the leak test."""

    def __init__(self, *, register_raises: bool = False, browser_raises: bool = False):
        self.closed = False
        self.registered: list[object] = []
        self.unregistered: list[object] = []
        self._register_raises = register_raises
        self.browser_raises = browser_raises

    def register_service(self, info):
        if self._register_raises:
            raise RuntimeError("register failed")
        self.registered.append(info)

    def unregister_service(self, info):
        self.unregistered.append(info)

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _quiet_mdns(monkeypatch):
    monkeypatch.setattr(discovery_mdns, "_enabled", lambda: True)
    monkeypatch.setattr(discovery_mdns, "_browse_enabled", lambda: True)
    monkeypatch.setattr(discovery_mdns, "ServiceInfo", lambda **kw: object())
    monkeypatch.setattr(discovery_mdns, "_detect_ipv4", lambda: "127.0.0.1")
    monkeypatch.setattr(discovery_mdns, "_ZEROCONF", None, raising=False)
    monkeypatch.setattr(discovery_mdns, "_SERVICE_INFO", None, raising=False)
    monkeypatch.setattr(discovery_mdns, "_BROWSE_SESSION", None, raising=False)
    monkeypatch.setattr(discovery_mdns, "_BROWSE_ZEROCONF", None, raising=False)
    monkeypatch.setattr(discovery_mdns, "_BROWSE_BROWSER", None, raising=False)
    monkeypatch.setattr(discovery_mdns, "_BROWSE_THREAD", None, raising=False)
    yield
    discovery_mdns.stop()


# --- F05: mDNS advertisement -------------------------------------------------


def test_failed_registration_closes_the_zeroconf_it_built(monkeypatch) -> None:
    """The audited leak: the except branch closed the global, not the local.

    _ZEROCONF is only assigned after register_service succeeds, so on failure
    it is still None and the close was a no-op — leaking an open Zeroconf with
    its sockets and threads on every retry.
    """
    built: list[_FakeZeroconf] = []

    def _make():
        zc = _FakeZeroconf(register_raises=True)
        built.append(zc)
        return zc

    monkeypatch.setattr(discovery_mdns, "Zeroconf", _make)

    discovery_mdns.start()

    assert len(built) == 1
    assert built[0].closed is True, "a failed registration leaked its Zeroconf"
    assert discovery_mdns._ZEROCONF is None


def test_registration_that_finishes_after_stop_does_not_publish(monkeypatch) -> None:
    """A start_async in flight must not advertise a service stop() retired."""
    released = threading.Event()
    built: list[_FakeZeroconf] = []

    def _slow_zeroconf():
        zc = _FakeZeroconf()
        built.append(zc)
        released.wait(5.0)
        return zc

    monkeypatch.setattr(discovery_mdns, "Zeroconf", _slow_zeroconf)

    worker = threading.Thread(target=discovery_mdns.start, daemon=True)
    worker.start()
    # Wait until the registration is genuinely in flight.
    for _ in range(200):
        if built:
            break
        time.sleep(0.01)
    assert built, "registration never started"

    discovery_mdns.stop()
    released.set()
    worker.join(timeout=5.0)
    assert not worker.is_alive()

    assert discovery_mdns._ZEROCONF is None
    assert discovery_mdns._SERVICE_INFO is None
    # It also cleans up what it built rather than abandoning it.
    assert built[0].closed is True


def test_successful_registration_publishes(monkeypatch) -> None:
    zc = _FakeZeroconf()
    monkeypatch.setattr(discovery_mdns, "Zeroconf", lambda: zc)

    discovery_mdns.start()

    assert discovery_mdns._ZEROCONF is zc
    assert len(zc.registered) == 1
    assert zc.closed is False


def test_stop_unregisters_and_closes(monkeypatch) -> None:
    zc = _FakeZeroconf()
    monkeypatch.setattr(discovery_mdns, "Zeroconf", lambda: zc)
    discovery_mdns.start()

    discovery_mdns.stop()

    assert len(zc.unregistered) == 1
    assert zc.closed is True


@pytest.mark.parametrize("early_return", ["disabled", "active", "dependency_missing"])
def test_registration_early_returns_do_not_self_deadlock(monkeypatch, early_return) -> None:
    """status() must be called only after the advertisement lock is released."""
    if early_return == "disabled":
        monkeypatch.setattr(discovery_mdns, "_enabled", lambda: False)
    elif early_return == "active":
        monkeypatch.setattr(discovery_mdns, "_ZEROCONF", object(), raising=False)
        monkeypatch.setattr(discovery_mdns, "_SERVICE_INFO", object(), raising=False)
    else:
        monkeypatch.setattr(discovery_mdns, "Zeroconf", None)

    worker = threading.Thread(target=discovery_mdns.start, daemon=True)
    worker.start()
    worker.join(timeout=1.0)
    if worker.is_alive():
        # Keep a reverted implementation from wedging fixture teardown after
        # this assertion records the regression.
        discovery_mdns._LOCK = threading.Lock()
    assert not worker.is_alive(), f"start deadlocked on {early_return}"


def test_duplicate_async_start_does_not_self_deadlock(monkeypatch) -> None:
    class _AliveThread:
        @staticmethod
        def is_alive() -> bool:
            return True

    monkeypatch.setattr(discovery_mdns, "_START_THREAD", _AliveThread(), raising=False)
    worker = threading.Thread(target=discovery_mdns.start_async, daemon=True)
    worker.start()
    worker.join(timeout=1.0)
    if worker.is_alive():
        discovery_mdns._LOCK = threading.Lock()
    assert not worker.is_alive(), "a duplicate start_async call deadlocked"


# --- F05: mDNS browse --------------------------------------------------------


def test_browse_stop_flag_is_per_generation(monkeypatch) -> None:
    """Restarting must not un-stop the worker that is still shutting down."""
    monkeypatch.setattr(discovery_mdns, "Zeroconf", lambda: _FakeZeroconf())
    monkeypatch.setattr(discovery_mdns, "ServiceBrowser", lambda *a, **k: object())

    discovery_mdns.start_browse()
    first = discovery_mdns._BROWSE_SESSION
    assert first is not None

    discovery_mdns.stop_browse()
    assert first.stop.is_set()

    discovery_mdns.start_browse()
    second = discovery_mdns._BROWSE_SESSION
    assert second is not None
    assert second is not first
    assert second.generation != first.generation
    # The retired generation stays retired.
    assert first.stop.is_set()
    assert not second.stop.is_set()
    # And they do not share a queue, so the old worker cannot steal work.
    assert first.queue is not second.queue

    discovery_mdns.stop_browse()


def test_failed_browser_start_closes_its_zeroconf(monkeypatch) -> None:
    built: list[_FakeZeroconf] = []

    def _make():
        zc = _FakeZeroconf()
        built.append(zc)
        return zc

    def _boom(*args, **kwargs):
        raise RuntimeError("browser failed")

    monkeypatch.setattr(discovery_mdns, "Zeroconf", _make)
    monkeypatch.setattr(discovery_mdns, "ServiceBrowser", _boom)

    discovery_mdns.start_browse()

    assert built and built[0].closed is True
    assert discovery_mdns._BROWSE_ZEROCONF is None


def test_retired_session_callback_does_not_reach_the_live_queue(monkeypatch) -> None:
    monkeypatch.setattr(discovery_mdns, "Zeroconf", lambda: _FakeZeroconf())
    monkeypatch.setattr(discovery_mdns, "ServiceBrowser", lambda *a, **k: object())

    discovery_mdns.start_browse()
    old = discovery_mdns._BROWSE_SESSION
    discovery_mdns.stop_browse()
    discovery_mdns.start_browse()
    new = discovery_mdns._BROWSE_SESSION

    discovery_mdns._handle_service_state_change(old, "_relaytv._tcp.local.", "ghost", None)

    assert new.queue.qsize() == 0
    discovery_mdns.stop_browse()


# --- F06: IPTV refresh worker ------------------------------------------------


def test_iptv_restart_retires_the_previous_worker(monkeypatch) -> None:
    iptv_service.stop_worker()

    iptv_service.start_worker()
    first_stop = iptv_service._WORKER_STOP
    first_thread = iptv_service._WORKER_THREAD
    assert first_stop is not None

    iptv_service.stop_worker()
    assert first_stop.is_set()

    iptv_service.start_worker()
    second_stop = iptv_service._WORKER_STOP
    assert second_stop is not None
    assert second_stop is not first_stop
    # The old worker stays stopped: start no longer clears a shared flag.
    assert first_stop.is_set()
    assert not second_stop.is_set()
    assert iptv_service._WORKER_THREAD is not first_thread

    iptv_service.stop_worker()


def test_iptv_stop_joins_the_worker() -> None:
    iptv_service.start_worker()
    thread = iptv_service._WORKER_THREAD
    assert thread is not None

    iptv_service.stop_worker()

    assert not thread.is_alive(), "stop_worker returned while the worker was still running"
    assert iptv_service._WORKER_THREAD is None


def test_iptv_double_start_does_not_stack_workers() -> None:
    iptv_service.stop_worker()
    iptv_service.start_worker()
    first = iptv_service._WORKER_THREAD
    iptv_service.start_worker()

    assert iptv_service._WORKER_THREAD is first

    iptv_service.stop_worker()


# --- F12: post-live relay ----------------------------------------------------


def test_concurrent_session_creation_leaves_exactly_one(monkeypatch) -> None:
    """Both callers used to spawn, both see an empty map, and both publish."""
    spawned: list[str] = []
    closed: list[str] = []
    barrier = threading.Barrier(2, timeout=5.0)

    class _Session:
        def __init__(self, token):
            self.token = token

    def _fake_create(page_url, ytdl_format, ytdlp_args=()):
        token = f"t{len(spawned)}"
        spawned.append(token)
        # Both callers reach the spawn at the same moment when unserialized.
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        with postlive_relay._LOCK:
            stale = list(postlive_relay._SESSIONS.keys())
        for token_stale in stale:
            closed.append(token_stale)
            with postlive_relay._LOCK:
                postlive_relay._SESSIONS.pop(token_stale, None)
        session = _Session(token)
        with postlive_relay._LOCK:
            postlive_relay._SESSIONS[token] = session
        return session

    monkeypatch.setattr(postlive_relay, "_create_session_locked", _fake_create)
    monkeypatch.setattr(postlive_relay, "_SESSIONS", {}, raising=False)

    errors: list[BaseException] = []

    def _run():
        try:
            postlive_relay.create_session("https://x.test/a", "best")
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)

    assert not errors, errors
    assert len(spawned) == 2, "both callers should still spawn; ordering is preserved"
    # But only one survives: the second superseded the first.
    assert len(postlive_relay._SESSIONS) == 1, postlive_relay._SESSIONS
    assert closed == [spawned[0]]


def test_create_session_still_supersedes_after_spawning(monkeypatch) -> None:
    """Restart-in-place depends on the old session outliving a failed spawn."""
    order: list[str] = []

    def _failing_create(page_url, ytdl_format, ytdlp_args=()):
        order.append("spawn")
        raise postlive_relay.RelayError("spawn failed")

    monkeypatch.setattr(postlive_relay, "_create_session_locked", _failing_create)
    monkeypatch.setattr(postlive_relay, "_SESSIONS", {"existing": object()}, raising=False)

    with pytest.raises(postlive_relay.RelayError):
        postlive_relay.create_session("https://x.test/a", "best")

    assert order == ["spawn"]
    # The session that was playing is untouched by the failure.
    assert "existing" in postlive_relay._SESSIONS


def test_close_all_waits_for_an_inflight_creation(monkeypatch) -> None:
    """Shutdown must retire a session whose spawn was already in progress."""
    entered = threading.Event()
    release = threading.Event()
    closed: list[str] = []

    class _Session:
        token = "late"

    def _slow_create(page_url, ytdl_format, ytdlp_args=()):
        entered.set()
        release.wait(5.0)
        session = _Session()
        with postlive_relay._LOCK:
            postlive_relay._SESSIONS[session.token] = session
        return session

    def _close(token, *, reason=""):
        closed.append(token)
        with postlive_relay._LOCK:
            postlive_relay._SESSIONS.pop(token, None)

    monkeypatch.setattr(postlive_relay, "_create_session_locked", _slow_create)
    monkeypatch.setattr(postlive_relay, "close_session", _close)
    monkeypatch.setattr(postlive_relay, "_prune_completed_spools", lambda **kwargs: None)
    monkeypatch.setattr(postlive_relay, "_SESSIONS", {}, raising=False)

    creator = threading.Thread(
        target=postlive_relay.create_session,
        args=("https://x.test/a", "best"),
        daemon=True,
    )
    creator.start()
    assert entered.wait(1.0), "session creation never entered its transaction"

    closer = threading.Thread(target=postlive_relay.close_all, daemon=True)
    closer.start()
    closer.join(timeout=0.1)
    assert closer.is_alive(), "close_all bypassed the in-flight creation"

    release.set()
    creator.join(timeout=2.0)
    closer.join(timeout=2.0)

    assert not creator.is_alive()
    assert not closer.is_alive()
    assert closed == ["late"]
    assert postlive_relay._SESSIONS == {}
