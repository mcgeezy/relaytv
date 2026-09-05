# SPDX-License-Identifier: GPL-3.0-only
"""Peer device registry, transfer payload, and queue import contracts."""
from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from relaytv_app import device_identity, discovery_mdns, peers, state
from relaytv_app.config import runtime_config
from relaytv_app.main import create_app


@pytest.fixture
def peers_file(monkeypatch, tmp_path):
    path = tmp_path / "peers.json"
    monkeypatch.setenv("RELAYTV_PEERS_FILE", str(path))
    monkeypatch.setenv("RELAYTV_DEVICE_ID", "selfdevice")
    runtime_config.refresh_from_env()
    device_identity.reset_cache_for_tests()
    yield path
    device_identity.reset_cache_for_tests()


@pytest.fixture
def client(peers_file):
    # No lifespan context: these tests exercise route behavior, not startup
    # workers, and the app's lifespan wants a writable /data.
    return TestClient(create_app(testing=True))


@pytest.fixture
def stub_identity(monkeypatch):
    """Answer identity probes without touching the network."""
    calls: list[dict[str, str]] = []

    def _probe(base_url: str, *, token: str = "") -> dict[str, str]:
        calls.append({"base_url": base_url, "token": token})
        return {
            "device_id": "peerdevice",
            "device_name": "Bedroom TV",
            "version": "0.8.0",
            "base_url": peers.normalize_base_url(base_url),
        }

    monkeypatch.setattr(peers, "probe_identity", _probe)
    return calls


def test_base_url_normalization_rejects_unsafe_addresses() -> None:
    assert peers.normalize_base_url("192.168.1.42:8787") == "http://192.168.1.42:8787"
    assert peers.normalize_base_url("http://tv.local:8787/") == "http://tv.local:8787"
    assert peers.normalize_base_url("https://tv.local/relay/") == "https://tv.local/relay"

    # A mistyped or out-of-range port must come back as an operator-facing
    # PeerError like every other bad address. urlsplit parses the port lazily,
    # so these raise ValueError on attribute access rather than at parse time.
    for bad in (
        "",
        "ftp://tv.local",
        "http://",
        "http://user:pass@tv.local",
        "http://tv.local:notaport",
        "http://tv.local:99999",
        "http://tv.local:-1",
    ):
        with pytest.raises(peers.PeerError):
            peers.normalize_base_url(bad)


def test_peer_token_is_persisted_but_never_returned(peers_file, stub_identity) -> None:
    created = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom", token="s3cret")

    assert created["has_token"] is True
    assert "token" not in created
    assert "s3cret" not in json.dumps(created)

    # The secret reaches disk (so sends keep working) but not the API surface.
    on_disk = json.loads(peers_file.read_text())
    assert on_disk["peers"][0]["token"] == "s3cret"
    assert oct(peers_file.stat().st_mode)[-3:] == "600"

    listed = peers.list_peers()
    assert listed == [created]
    assert "s3cret" not in json.dumps(listed)


def test_adding_self_or_duplicate_is_rejected(peers_file, monkeypatch) -> None:
    def _self_probe(base_url: str, *, token: str = "") -> dict[str, str]:
        return {
            "device_id": device_identity.device_id(),
            "device_name": "Living Room",
            "version": "",
            "base_url": peers.normalize_base_url(base_url),
        }

    monkeypatch.setattr(peers, "probe_identity", _self_probe)
    with pytest.raises(peers.PeerError, match="this device"):
        peers.add_peer(base_url="http://127.0.0.1:8787")


def test_duplicate_peer_is_rejected(peers_file, stub_identity) -> None:
    peers.add_peer(base_url="http://tv.local:8787")
    with pytest.raises(peers.PeerError, match="already added"):
        peers.add_peer(base_url="http://tv.local:8787")


def test_update_and_remove_peer(peers_file, stub_identity) -> None:
    created = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom", token="s3cret")

    renamed = peers.update_peer(created["id"], name="Bedroom TV")
    assert renamed["name"] == "Bedroom TV"
    assert renamed["has_token"] is True

    cleared = peers.update_peer(created["id"], token="")
    assert cleared["has_token"] is False

    assert peers.remove_peer(created["id"]) is True
    assert peers.remove_peer(created["id"]) is False
    assert peers.list_peers() == []


def test_wire_items_strip_resolved_streams_and_report_iptv_skips(peers_file, monkeypatch) -> None:
    monkeypatch.setattr(device_identity, "local_base_url", lambda: "http://192.168.1.5:8787")

    entries, skipped = peers.wire_items(
        [
            {
                "url": "https://example.com/video",
                "title": "Portable",
                "_resolved_stream": "https://cdn.example/secret.m3u8",
                "headers": {"Authorization": "Bearer nope"},
            },
            {"provider": "iptv", "name": "Channel One", "url": "https://stream.example/one.m3u8"},
            {"url": "/media/uploads/u_1/clip.mp4", "title": "Local file", "provider": "upload"},
        ]
    )

    assert [entry["url"] for entry in entries] == [
        "https://example.com/video",
        "http://192.168.1.5:8787/media/uploads/u_1/clip.mp4",
    ]
    payload = json.dumps(entries)
    assert "secret.m3u8" not in payload
    assert "Authorization" not in payload
    assert [entry["reason"] for entry in skipped] == ["iptv_channels_stay_on_this_device"]


class _FakeServiceInfo:
    """Minimal stand-in for zeroconf.ServiceInfo."""

    def __init__(self, addresses, port, properties):
        self._addresses = list(addresses)
        self.port = port
        self.properties = dict(properties)

    def parsed_addresses(self):
        return list(self._addresses)


def _service_props(device_id: str, name: str = "Bedroom TV", app: str = "0.9.0") -> dict:
    return {b"id": device_id.encode(), b"name": name.encode(), b"app": app.encode(), b"service": b"relaytv"}


def test_discovered_record_prefers_ipv4_and_reads_txt(peers_file) -> None:
    record = discovery_mdns.discovered_record_from_service(
        "Bedroom TV._relaytv._tcp.local.",
        _FakeServiceInfo(["fe80::1", "192.168.1.42"], 8787, _service_props("peerdevice")),
    )

    assert record["base_url"] == "http://192.168.1.42:8787"
    assert record["device_id"] == "peerdevice"
    assert record["device_name"] == "Bedroom TV"
    assert record["version"] == "0.9.0"


def test_discovered_record_skips_self_and_unusable_services(peers_file) -> None:
    own = discovery_mdns.discovered_record_from_service(
        "Living Room._relaytv._tcp.local.",
        _FakeServiceInfo(["192.168.1.5"], 8787, _service_props("selfdevice", name="Living Room")),
    )
    assert own is None

    assert discovery_mdns.discovered_record_from_service("x._relaytv._tcp.local.", None) is None
    no_port = discovery_mdns.discovered_record_from_service(
        "x._relaytv._tcp.local.",
        _FakeServiceInfo(["192.168.1.9"], 0, _service_props("otherdevice")),
    )
    assert no_port is None
    no_address = discovery_mdns.discovered_record_from_service(
        "x._relaytv._tcp.local.",
        _FakeServiceInfo([], 8787, _service_props("otherdevice")),
    )
    assert no_address is None


def test_discovered_record_falls_back_to_instance_name(peers_file) -> None:
    record = discovery_mdns.discovered_record_from_service(
        "Old Build._relaytv._tcp.local.",
        _FakeServiceInfo(["192.168.1.7"], 8787, {b"service": b"relaytv"}),
    )
    # A peer on a build without the id/name TXT records still shows up.
    assert record["device_name"] == "Old Build"
    assert record["device_id"] == ""
    assert record["base_url"] == "http://192.168.1.7:8787"


def test_discovered_entries_expire(peers_file, monkeypatch) -> None:
    discovery_mdns.reset_browse_for_tests()
    monkeypatch.setenv("RELAYTV_MDNS_BROWSE_TTL_SEC", "60")
    fresh = {
        "service_name": "fresh._relaytv._tcp.local.",
        "device_id": "freshdevice",
        "device_name": "Fresh",
        "base_url": "http://192.168.1.8:8787",
        "last_seen_at": time.time(),
    }
    stale = {
        "service_name": "stale._relaytv._tcp.local.",
        "device_id": "staledevice",
        "device_name": "Stale",
        "base_url": "http://192.168.1.9:8787",
        "last_seen_at": time.time() - 600,
    }
    discovery_mdns._remember_service(fresh)
    discovery_mdns._remember_service(stale)

    assert [r["device_id"] for r in discovery_mdns.discovered()] == ["freshdevice"]
    discovery_mdns.reset_browse_for_tests()


class _FakeZeroconf:
    """Resolves service names from a fixed map, like a warm zeroconf cache."""

    def __init__(self, services):
        self.services = dict(services)
        self.lookups: list[str] = []

    def get_service_info(self, type_, name, timeout=0):
        self.lookups.append(name)
        return self.services.get(name)


def test_refresh_keeps_live_services_and_drops_vanished(peers_file) -> None:
    """A device that stays advertised must not age out of the list.

    zeroconf only calls back on state changes, so a peer that keeps advertising
    without another callback would expire on TTL alone. The refresh sweep
    re-resolves known services, and drops the ones that no longer answer.
    """
    discovery_mdns.reset_browse_for_tests()
    name = "Bedroom TV._relaytv._tcp.local."
    stale_seen = time.time() - 600
    discovery_mdns._remember_service(
        {
            "service_name": name,
            "device_id": "peerdevice",
            "device_name": "Bedroom TV",
            "base_url": "http://192.168.1.42:8787",
            "last_seen_at": stale_seen,
        }
    )
    live = _FakeZeroconf({name: _FakeServiceInfo(["192.168.1.42"], 8787, _service_props("peerdevice"))})

    discovery_mdns._refresh_known_services(live, "_relaytv._tcp.local.")

    assert live.lookups == [name]
    found = discovery_mdns.discovered()
    assert [r["device_id"] for r in found] == ["peerdevice"]
    assert found[0]["last_seen_at"] > stale_seen

    # The same sweep removes a device that stopped answering.
    discovery_mdns._refresh_known_services(_FakeZeroconf({}), "_relaytv._tcp.local.")
    assert discovery_mdns.discovered() == []
    discovery_mdns.reset_browse_for_tests()


def test_discovery_candidates_hide_saved_devices(peers_file, stub_identity) -> None:
    discovery_mdns.reset_browse_for_tests()
    saved = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom")
    discovery_mdns._remember_service(
        {
            "service_name": "saved._relaytv._tcp.local.",
            "device_id": saved["device_id"],
            "device_name": "Bedroom TV",
            "base_url": "http://192.168.1.42:8787",
            "last_seen_at": time.time(),
        }
    )
    discovery_mdns._remember_service(
        {
            "service_name": "new._relaytv._tcp.local.",
            "device_id": "kitchendevice",
            "device_name": "Kitchen Pi",
            "base_url": "http://192.168.1.43:8787",
            "last_seen_at": time.time(),
        }
    )

    candidates = peers.discovered_candidates()
    assert [c["device_name"] for c in candidates] == ["Kitchen Pi"]
    assert candidates[0]["source"] == "mdns"
    discovery_mdns.reset_browse_for_tests()


def test_peers_endpoint_reports_discovery_state(client) -> None:
    discovery_mdns.reset_browse_for_tests()
    discovery_mdns._remember_service(
        {
            "service_name": "nearby._relaytv._tcp.local.",
            "device_id": "kitchendevice",
            "device_name": "Kitchen Pi",
            "base_url": "http://192.168.1.43:8787",
            "last_seen_at": time.time(),
        }
    )

    payload = client.get("/peers").json()
    assert [c["device_name"] for c in payload["discovered"]] == ["Kitchen Pi"]
    assert payload["discovery"]["enabled"] is True
    # Browsing is not started in tests, so the UI can explain the difference
    # between "nothing found" and "discovery is not running".
    assert payload["discovery"]["active"] is False
    discovery_mdns.reset_browse_for_tests()


def test_browse_disabled_reports_state_without_starting(peers_file, monkeypatch) -> None:
    monkeypatch.setenv("RELAYTV_MDNS_BROWSE_ENABLED", "0")
    status = discovery_mdns.start_browse()
    assert status["enabled"] is False
    assert status["active"] is False


def test_identity_endpoint_advertises_stable_id(client) -> None:
    payload = client.get("/peers/identity").json()
    assert payload["device_id"] == "selfdevice"
    assert payload["base_url"].startswith("http://")
    assert "token" not in payload


def test_peers_endpoint_reports_registry_and_discovery_shape(client, stub_identity) -> None:
    added = client.post("/peers", json={"base_url": "http://tv.local:8787", "name": "Bedroom"})
    assert added.status_code == 200

    listing = client.get("/peers").json()
    assert listing["device"]["device_id"] == "selfdevice"
    assert [peer["name"] for peer in listing["peers"]] == ["Bedroom"]
    assert listing["discovered"] == []


def test_add_peer_surfaces_probe_failure_without_persisting(client, monkeypatch) -> None:
    def _fail(base_url: str, *, token: str = "") -> dict[str, str]:
        raise peers.PeerError("device is unreachable", status_code=502)

    monkeypatch.setattr(peers, "probe_identity", _fail)
    response = client.post("/peers", json={"base_url": "http://tv.local:8787"})
    assert response.status_code == 502
    assert response.json()["detail"] == "device is unreachable"
    assert client.get("/peers").json()["peers"] == []


def test_queue_import_rebuilds_items_and_reports_rejections(client, monkeypatch) -> None:
    toasts: list[str] = []
    monkeypatch.setattr(
        "relaytv_app.routes.queue._push_overlay_toast",
        lambda **kwargs: toasts.append(str(kwargs.get("text") or "")),
    )
    with state.QUEUE_LOCK:
        state.QUEUE.clear()

    response = client.post(
        "/queue/import",
        json={
            "mode": "append",
            "from": {"device_id": "peerdevice", "name": "Living Room"},
            "items": [
                {"url": "https://example.com/a", "title": "Sent A"},
                {"url": "file:///etc/passwd", "title": "Blocked"},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 2
    assert body["accepted"] == 1
    assert [entry["accepted"] for entry in body["results"]] == [True, False]
    assert "http/https" in body["results"][1]["reason"]

    with state.QUEUE_LOCK:
        queued = list(state.QUEUE)
    assert [item["title"] for item in queued] == ["Sent A"]
    assert queued[0]["peer_origin"] == {"device_id": "peerdevice", "name": "Living Room"}
    assert toasts == ["Living Room sent 1 item"]

    with state.QUEUE_LOCK:
        state.QUEUE.clear()


def test_queue_import_keeps_peer_hosted_uploads_playable(client) -> None:
    """A peer's upload URL must not be resolved against our own upload store.

    Upload URLs are shaped ``/media/uploads/<id>/<file>`` regardless of which
    device hosts them, so local resolution would report a peer's file as
    expired and the queue tile would render it as unavailable.
    """
    with state.QUEUE_LOCK:
        state.QUEUE.clear()

    response = client.post(
        "/queue/import",
        json={
            "from": {"device_id": "peerdevice", "name": "Living Room"},
            "items": [
                {
                    "url": "http://192.168.1.5:8787/media/uploads/u_notours/clip.mp4",
                    "title": "Local Clip",
                    "provider": "upload",
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 1
    item = body["queue"][0]
    assert item["title"] == "Local Clip"
    assert item["provider"] == "other"
    assert item["peer_hosted"] is True
    assert item.get("available") is not False

    with state.QUEUE_LOCK:
        state.QUEUE.clear()


def test_queue_import_replace_clears_existing_queue(client) -> None:
    with state.QUEUE_LOCK:
        state.QUEUE.clear()
        state.QUEUE.append({"url": "https://example.com/old", "title": "Old"})

    response = client.post(
        "/queue/import",
        json={"mode": "replace", "items": [{"url": "https://example.com/new", "title": "New"}]},
    )
    assert response.status_code == 200

    with state.QUEUE_LOCK:
        titles = [item.get("title") for item in state.QUEUE]
        state.QUEUE.clear()
    assert titles == ["New"]


def test_send_queue_posts_wire_items_and_summarizes_result(client, peers_file, stub_identity, monkeypatch) -> None:
    peer = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom", token="s3cret")
    sent: dict[str, object] = {}

    def _request(base_url, path, *, token="", payload=None, timeout=0.0):
        sent.update({"base_url": base_url, "path": path, "token": token, "payload": payload})
        return {
            "status": "imported",
            "accepted": 1,
            "queue_length": 4,
            "results": [
                {"url": "https://example.com/a", "title": "Sent A", "accepted": True},
                {"url": "https://example.com/b", "title": "Sent B", "accepted": False, "reason": "nope"},
            ],
        }

    monkeypatch.setattr(peers, "_request", _request)
    with state.QUEUE_LOCK:
        state.QUEUE.clear()
        state.QUEUE.append({"url": "https://example.com/a", "title": "Sent A"})
        state.QUEUE.append({"url": "https://example.com/b", "title": "Sent B"})

    response = client.post(f"/peers/{peer['id']}/send", json={"mode": "append"})
    assert response.status_code == 200
    body = response.json()

    assert sent["path"] == "/queue/import"
    assert sent["token"] == "s3cret"
    assert sent["payload"]["mode"] == "append"
    assert sent["payload"]["from"]["device_id"] == "selfdevice"
    assert [entry["title"] for entry in sent["payload"]["items"]] == ["Sent A", "Sent B"]

    assert body["sent"] == 2
    assert body["accepted"] == 1
    assert body["rejected"] == [{"title": "Sent B", "reason": "nope"}]
    assert body["peer"]["has_token"] is True

    with state.QUEUE_LOCK:
        state.QUEUE.clear()


def test_move_clears_local_queue_only_after_confirmed_receipt(
    client, peers_file, stub_identity, monkeypatch
) -> None:
    peer = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom")

    def _fail(*args, **kwargs):
        raise peers.PeerError("device is unreachable", status_code=502)

    monkeypatch.setattr(peers, "_request", _fail)
    with state.QUEUE_LOCK:
        state.QUEUE.clear()
        state.QUEUE.append({"url": "https://example.com/a", "title": "First"})
        state.QUEUE.append({"url": "https://example.com/b", "title": "Second"})

    failed = client.post(f"/peers/{peer['id']}/send", json={"mode": "move"})
    assert failed.status_code == 502
    # A move that never landed must not lose the queue.
    with state.QUEUE_LOCK:
        assert len(state.QUEUE) == 2

    monkeypatch.setattr(
        peers,
        "_request",
        lambda *a, **k: {"accepted": 2, "queue_length": 2, "results": []},
    )
    moved = client.post(f"/peers/{peer['id']}/send", json={"mode": "move"})
    assert moved.status_code == 200
    body = moved.json()
    assert body["moved"] is True
    assert body["local_queue_length"] == 0
    with state.QUEUE_LOCK:
        assert list(state.QUEUE) == []


def test_move_of_one_item_removes_just_that_item(client, peers_file, stub_identity, monkeypatch) -> None:
    peer = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom")
    monkeypatch.setattr(
        peers,
        "_request",
        lambda *a, **k: {"accepted": 1, "queue_length": 1, "results": []},
    )
    with state.QUEUE_LOCK:
        state.QUEUE.clear()
        state.QUEUE.append({"url": "https://example.com/a", "title": "First"})
        state.QUEUE.append({"url": "https://example.com/b", "title": "Second"})

    moved = client.post(f"/peers/{peer['id']}/send", json={"mode": "move", "index": 0})
    assert moved.status_code == 200
    assert moved.json()["local_queue_length"] == 1
    with state.QUEUE_LOCK:
        titles = [item.get("title") for item in state.QUEUE]
        state.QUEUE.clear()
    assert titles == ["Second"]


def test_handoff_requires_playback_and_stops_locally_after_success(
    client, peers_file, stub_identity, monkeypatch
) -> None:
    peer = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom")

    from relaytv_app import playback_service
    from relaytv_app.routes import peers as peers_routes

    monkeypatch.setattr(playback_service, "handoff_snapshot", lambda: None)
    idle = client.post(f"/peers/{peer['id']}/handoff", json={})
    assert idle.status_code == 409
    assert "nothing is playing" in idle.json()["detail"]

    sent: dict[str, object] = {}
    monkeypatch.setattr(
        playback_service,
        "handoff_snapshot",
        lambda: {
            "item": {"url": "https://example.com/movie", "title": "Movie", "_resolved_stream": "https://cdn/secret"},
            "position": 812.5,
            "duration": 3600.0,
        },
    )

    def _request(base_url, path, *, token="", payload=None, timeout=0.0):
        sent.update({"path": path, "payload": payload})
        return {"status": "handed_off", "playing": True, "accepted": 1, "queue_length": 1, "results": []}

    monkeypatch.setattr(peers, "_request", _request)
    calls: list[str] = []
    monkeypatch.setattr(peers_routes, "_remove_local_queue_items", lambda items: calls.append("clear_queue") or 0)
    monkeypatch.setattr(
        "relaytv_app.playback_service.complete_peer_handoff",
        lambda snapshot, **kw: calls.append("clear_now_playing") or True,
    )

    with state.QUEUE_LOCK:
        state.QUEUE.clear()
        state.QUEUE.append({"url": "https://example.com/next", "title": "Next up"})

    response = client.post(f"/peers/{peer['id']}/handoff", json={})
    assert response.status_code == 200
    body = response.json()

    assert sent["path"] == "/queue/handoff"
    assert sent["payload"]["resume_pos"] == 812.5
    assert sent["payload"]["now_playing"]["title"] == "Movie"
    # The resolved stream is sender-scoped and must not travel.
    assert "secret" not in json.dumps(sent["payload"])
    assert [entry["title"] for entry in sent["payload"]["items"]] == ["Next up"]

    assert body["status"] == "handed_off"
    assert body["playing"] is True
    assert body["local_stopped"] is True
    # The session moved to the peer, so this device clears now-playing rather
    # than closing it: a preserved session would show the item it gave away and
    # offer to resume it while the peer is playing it. The queue is dropped
    # first, or clearing would advance into the next queued item instead of
    # going idle.
    assert calls == ["clear_queue", "clear_now_playing"]

    with state.QUEUE_LOCK:
        state.QUEUE.clear()


def test_handoff_failure_leaves_local_playback_alone(client, peers_file, stub_identity, monkeypatch) -> None:
    peer = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom")
    from relaytv_app import playback_service
    from relaytv_app.routes import peers as peers_routes

    monkeypatch.setattr(
        playback_service,
        "handoff_snapshot",
        lambda: {"item": {"url": "https://example.com/movie", "title": "Movie"}, "position": 10.0, "duration": 60.0},
    )

    def _fail(*args, **kwargs):
        raise peers.PeerError("device is unreachable", status_code=502)

    monkeypatch.setattr(peers, "_request", _fail)
    stopped: list[bool] = []
    monkeypatch.setattr(
        "relaytv_app.playback_service.complete_peer_handoff",
        lambda snapshot, **kw: stopped.append(True) or True,
    )
    cleared: list[bool] = []
    monkeypatch.setattr(peers_routes, "_remove_local_queue_items", lambda items: cleared.append(True) or 0)

    response = client.post(f"/peers/{peer['id']}/handoff", json={})
    assert response.status_code == 502
    # Nothing local changed: the user keeps watching what they were watching.
    assert stopped == []
    assert cleared == []


def test_queue_handoff_receiver_plays_with_resume_position(client, monkeypatch) -> None:
    played: dict[str, object] = {}
    monkeypatch.setattr(
        "relaytv_app.routes.queue._play_now_from_history",
        lambda payload: played.update(payload) or {"status": "playing"},
    )
    toasts: list[str] = []
    monkeypatch.setattr(
        "relaytv_app.routes.queue._push_overlay_toast",
        lambda **kwargs: toasts.append(str(kwargs.get("text") or "")),
    )
    with state.QUEUE_LOCK:
        state.QUEUE.clear()

    response = client.post(
        "/queue/handoff",
        json={
            "now_playing": {"url": "https://example.com/movie", "title": "Movie"},
            "resume_pos": 812.5,
            "items": [{"url": "https://example.com/next", "title": "Next up"}],
            "from": {"device_id": "peerdevice", "name": "Living Room"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "handed_off"
    assert body["playing"] is True
    assert body["accepted"] == 1

    assert played["resume_pos"] == 812.5
    assert played["reason"] == "peer_handoff"
    # The receiver's own session is preserved rather than discarded.
    assert played["preserve_current"] is True
    assert played["preserve_to"] == "queue_front"
    assert toasts == ["Continuing from Living Room"]

    with state.QUEUE_LOCK:
        titles = [item.get("title") for item in state.QUEUE]
        state.QUEUE.clear()
    assert titles == ["Next up"]


def test_send_single_index_and_unreachable_peer(client, peers_file, stub_identity, monkeypatch) -> None:
    peer = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom")
    captured: dict[str, object] = {}

    def _request(base_url, path, *, token="", payload=None, timeout=0.0):
        captured.update({"payload": payload})
        return {"accepted": 1, "queue_length": 1, "results": []}

    monkeypatch.setattr(peers, "_request", _request)
    with state.QUEUE_LOCK:
        state.QUEUE.clear()
        state.QUEUE.append({"url": "https://example.com/a", "title": "First"})
        state.QUEUE.append({"url": "https://example.com/b", "title": "Second"})

    ok = client.post(f"/peers/{peer['id']}/send", json={"index": 1})
    assert ok.status_code == 200
    assert [entry["title"] for entry in captured["payload"]["items"]] == ["Second"]

    out_of_range = client.post(f"/peers/{peer['id']}/send", json={"index": 9})
    assert out_of_range.status_code == 400

    def _unreachable(*args, **kwargs):
        raise peers.PeerError("device is unreachable", status_code=502)

    monkeypatch.setattr(peers, "_request", _unreachable)
    failed = client.post(f"/peers/{peer['id']}/send", json={})
    assert failed.status_code == 502
    assert failed.json()["detail"] == "device is unreachable"
    # The failure is recorded on the peer so the UI can explain the outage.
    assert peers.get_record(peer["id"])["last_error"] == "device is unreachable"

    missing = client.post("/peers/p_missing/send", json={})
    assert missing.status_code == 404

    with state.QUEUE_LOCK:
        state.QUEUE.clear()


def test_send_single_queue_id_survives_index_shift(client, peers_file, stub_identity, monkeypatch) -> None:
    peer = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom")
    captured: dict[str, object] = {}

    def _request(base_url, path, *, token="", payload=None, timeout=0.0):
        captured["payload"] = payload
        return {"accepted": 1, "queue_length": 1, "results": []}

    monkeypatch.setattr(peers, "_request", _request)
    with state.QUEUE_LOCK:
        state.QUEUE.clear()
        state.QUEUE.extend(
            [
                {"url": "https://example.com/a", "title": "First"},
                {"url": "https://example.com/b", "title": "Second"},
                {"url": "https://example.com/c", "title": "Third"},
            ]
        )
        state.ensure_queue_item_ids(state.QUEUE)
        selected_id = state.queue_item_id(state.QUEUE[1])
        state.QUEUE.pop(0)

    response = client.post(f"/peers/{peer['id']}/send", json={"index": 1, "queue_id": selected_id})

    assert response.status_code == 200
    assert [entry["title"] for entry in captured["payload"]["items"]] == ["Second"]
    with state.QUEUE_LOCK:
        state.QUEUE.clear()


def _seed_queue(*titles: str) -> None:
    with state.QUEUE_LOCK:
        state.QUEUE.clear()
        for title in titles:
            state.QUEUE.append({"url": f"https://example.com/{title.lower()}", "title": title})


@pytest.mark.parametrize("replacement", ["duplicate", "new_item"])
def test_transfer_cleanup_preserves_unsent_instances(client, stub_identity, monkeypatch, replacement):
    from relaytv_app import routes, player

    peer = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom")
    monkeypatch.setattr(state, "QUEUE", state._RevisionedQueue())
    monkeypatch.setattr(state, "persist_queue", lambda: None)
    monkeypatch.setattr(state, "persist_queue_payload", lambda payload: None)
    monkeypatch.setattr(player, "prime_mpv_up_next_from_queue", lambda **kw: None)
    monkeypatch.setattr(routes, "_ui_event_push_queue", lambda *a, **kw: None)
    _seed_queue("Sent")
    selected_id = state.queue_item_id(state.QUEUE[0])
    entered, release = threading.Event(), threading.Event()
    responses = []

    def _blocked_request(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {"accepted": 1, "queue_length": 1, "results": []}

    monkeypatch.setattr(peers, "_request", _blocked_request)
    worker = threading.Thread(target=lambda: responses.append(client.post(
        f"/peers/{peer['id']}/send", json={"mode": "move", "queue_ids": [selected_id]}
    )), daemon=True)
    worker.start()
    try:
        assert entered.wait(5)
        if replacement == "duplicate":
            assert client.post("/queue/remove", json={"queue_id": selected_id}).status_code == 200
        with state.QUEUE_LOCK:
            state.QUEUE.append({"url": "https://example.com/sent", "title": "Unsent"})
        unsent_id = state.queue_item_id(state.QUEUE[-1])
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert responses and responses[0].status_code == 200
    assert _queue_titles() == ["Unsent"]
    assert state.queue_item_id(state.QUEUE[0]) == unsent_id


def _queue_titles() -> list[str]:
    with state.QUEUE_LOCK:
        return [str(item.get("title") or "") for item in state.QUEUE]


@pytest.mark.parametrize("replacement", [False, True])
def test_handoff_completion_owns_only_the_captured_playback(client, stub_identity, monkeypatch, replacement):
    from relaytv_app import playback_service, player
    from relaytv_app.routes import playback as playback_routes

    peer = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom")
    monkeypatch.setattr(state, "QUEUE", state._RevisionedQueue([
        {"url": "https://example.com/remaining", "title": "Remaining"},
    ]))
    monkeypatch.setattr(state, "NOW_PLAYING", {"url": "https://example.com/a", "title": "A"})
    monkeypatch.setattr(state, "SESSION_STATE", "playing")
    monkeypatch.setattr(state, "AUTO_NEXT_SUPPRESS_UNTIL", 12345678900.0)
    monkeypatch.setattr(state, "_persist_session_payload", lambda *a: True)
    monkeypatch.setattr(state, "persist_queue", lambda: True)
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(player, "mpv_get", lambda prop: 10)
    monkeypatch.setattr(playback_routes, "_idle_visual_surface_enabled_for_player", lambda: False)
    stopped = []
    monkeypatch.setattr(player, "stop_mpv", lambda **kw: stopped.append(state.NOW_PLAYING["title"]))
    entered, release = threading.Event(), threading.Event()
    payloads = []

    def request(*args, payload=None, **kwargs):
        payloads.append(payload)
        entered.set()
        assert release.wait(5)
        return {"playing": True, "accepted": 0, "results": [], "queue_length": 0}

    monkeypatch.setattr(peers, "_request", request)
    responses = []
    worker = threading.Thread(target=lambda: responses.append(client.post(
        f"/peers/{peer['id']}/handoff", json={"queue_ids": []},
    )), daemon=True)
    worker.start()
    try:
        assert entered.wait(5)
        if replacement:
            player.claim_playback_intent()
            state.NOW_PLAYING = {"url": "https://example.com/b", "title": "B"}
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert responses and responses[0].status_code == 200
    assert payloads[0]["now_playing"]["title"] == "A"
    assert "playback_intent" not in payloads[0]
    assert responses[0].json()["local_stopped"] is not replacement
    assert _queue_titles() == ["Remaining"]
    if replacement:
        assert stopped == []
        assert state.NOW_PLAYING["title"] == "B"
        assert state.AUTO_NEXT_SUPPRESS_UNTIL == 12345678900.0
    else:
        assert stopped == ["A"]
        assert state.NOW_PLAYING is None
        assert state.SESSION_STATE == "idle"
        assert state.AUTO_NEXT_SUPPRESS_UNTIL == 0
        # Remaining items are eligible for the existing autoplay worker.
        advances = []
        monkeypatch.setattr(playback_service, "advance_queue", lambda **kw: advances.append(kw) or {})
        playback_service.natural_end()
        assert len(advances) == 1


def test_move_of_a_selection_removes_only_the_sent_items(client, peers_file, stub_identity, monkeypatch) -> None:
    peer = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom")
    captured: dict[str, object] = {}

    def _request(base_url, path, *, token="", payload=None, timeout=0.0):
        captured.update({"payload": payload})
        return {"accepted": 2, "queue_length": 2, "results": []}

    monkeypatch.setattr(peers, "_request", _request)
    _seed_queue("First", "Second", "Third")

    moved = client.post(f"/peers/{peer['id']}/send", json={"mode": "move", "indexes": [2, 0]})
    assert moved.status_code == 200
    # Selection travels in queue order regardless of how it was clicked.
    assert [entry["title"] for entry in captured["payload"]["items"]] == ["First", "Third"]
    assert moved.json()["local_queue_length"] == 1
    # Removing high index first keeps the lower ones where the selection found
    # them; the unselected item survives untouched.
    assert _queue_titles() == ["Second"]

    with state.QUEUE_LOCK:
        state.QUEUE.clear()


def test_send_rejects_an_empty_or_out_of_range_selection(client, peers_file, stub_identity, monkeypatch) -> None:
    peer = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom")
    calls: list[str] = []
    monkeypatch.setattr(peers, "_request", lambda *a, **k: calls.append("sent") or {"accepted": 0})
    _seed_queue("First", "Second")

    empty = client.post(f"/peers/{peer['id']}/send", json={"indexes": []})
    assert empty.status_code == 400
    assert empty.json()["detail"] == "nothing selected to send"

    out_of_range = client.post(f"/peers/{peer['id']}/send", json={"indexes": [0, 7]})
    assert out_of_range.status_code == 400

    # Neither rejection reached the peer, and neither touched the local queue.
    assert calls == []
    assert _queue_titles() == ["First", "Second"]

    with state.QUEUE_LOCK:
        state.QUEUE.clear()


def test_copy_sends_the_session_but_keeps_playing_here(client, peers_file, stub_identity, monkeypatch) -> None:
    peer = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom")

    from relaytv_app import playback_service
    from relaytv_app.routes import peers as peers_routes

    monkeypatch.setattr(
        playback_service,
        "handoff_snapshot",
        lambda: {"item": {"url": "https://example.com/movie", "title": "Movie"}, "position": 30.0, "duration": 60.0},
    )
    sent: dict[str, object] = {}

    def _request(base_url, path, *, token="", payload=None, timeout=0.0):
        sent.update({"path": path, "payload": payload})
        return {"status": "handed_off", "playing": True, "accepted": 1, "queue_length": 1, "results": []}

    monkeypatch.setattr(peers, "_request", _request)
    stopped: list[bool] = []
    cleared: list[bool] = []
    monkeypatch.setattr(
        "relaytv_app.playback_service.complete_peer_handoff",
        lambda snapshot, **kw: stopped.append(True) or True,
    )
    monkeypatch.setattr(peers_routes, "_remove_local_queue_items", lambda items: cleared.append(True) or 0)
    _seed_queue("Next up")

    response = client.post(f"/peers/{peer['id']}/handoff", json={"keep_local": True})
    assert response.status_code == 200
    body = response.json()

    # The peer receives exactly what a handoff sends, including the position, so
    # both rooms are playing the same moment.
    assert sent["path"] == "/queue/handoff"
    assert sent["payload"]["resume_pos"] == 30.0
    assert [entry["title"] for entry in sent["payload"]["items"]] == ["Next up"]

    assert body["kept_local"] is True
    assert body["local_stopped"] is False
    assert body["local_queue_length"] == 1
    # Copy is defined by what it does not do locally: nothing here is torn down.
    assert stopped == []
    assert cleared == []
    assert _queue_titles() == ["Next up"]

    with state.QUEUE_LOCK:
        state.QUEUE.clear()


def test_move_keeps_items_the_peer_rejected(client, peers_file, stub_identity, monkeypatch) -> None:
    peer = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom")
    monkeypatch.setattr(
        peers,
        "_request",
        lambda *a, **k: {
            "accepted": 1,
            "queue_length": 1,
            "results": [
                {"url": "https://example.com/keep", "title": "Keep", "accepted": True},
                {
                    "url": "https://example.com/reject",
                    "title": "Reject",
                    "accepted": False,
                    "reason": "provider_not_configured",
                },
            ],
        },
    )
    with state.QUEUE_LOCK:
        state.QUEUE.clear()
        state.QUEUE.append({"url": "https://example.com/keep", "title": "Keep"})
        state.QUEUE.append({"url": "https://example.com/reject", "title": "Reject"})

    moved = client.post(f"/peers/{peer['id']}/send", json={"mode": "move"})
    assert moved.status_code == 200
    assert [entry["reason"] for entry in moved.json()["rejected"]] == ["provider_not_configured"]
    # It never landed on the peer, so dropping it here would destroy it.
    assert _queue_titles() == ["Reject"]

    with state.QUEUE_LOCK:
        state.QUEUE.clear()


def test_move_keeps_items_that_could_not_travel_at_all(client, peers_file, stub_identity, monkeypatch) -> None:
    peer = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom")
    monkeypatch.setattr(peers, "_request", lambda *a, **k: {"accepted": 1, "queue_length": 1, "results": []})
    with state.QUEUE_LOCK:
        state.QUEUE.clear()
        state.QUEUE.append({"url": "https://example.com/ok", "title": "Portable"})
        state.QUEUE.append({"url": "http://iptv.example/live/u/p/9.ts", "title": "CNN", "provider": "iptv"})

    moved = client.post(f"/peers/{peer['id']}/send", json={"mode": "move"})
    assert moved.status_code == 200
    assert [entry["reason"] for entry in moved.json()["rejected"]] == ["iptv_channels_stay_on_this_device"]
    # A live channel has no shareable URL, so it was never offered to the peer.
    assert _queue_titles() == ["CNN"]

    with state.QUEUE_LOCK:
        state.QUEUE.clear()


def test_move_drops_the_sent_item_even_if_the_queue_shifted(client, peers_file, stub_identity, monkeypatch) -> None:
    peer = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom")

    def _request_while_the_queue_moves(*args, **kwargs):
        # A send can take tens of seconds; auto-next consuming the head in that
        # window shifts every index below it.
        with state.QUEUE_LOCK:
            state.QUEUE.pop(0)
        return {"accepted": 1, "queue_length": 1, "results": []}

    monkeypatch.setattr(peers, "_request", _request_while_the_queue_moves)
    _seed_queue("Head", "Sent", "Innocent")

    moved = client.post(f"/peers/{peer['id']}/send", json={"mode": "move", "indexes": [1]})
    assert moved.status_code == 200
    # The item that travelled is the item that goes, whatever position it now
    # holds. Reusing index 1 would have destroyed "Innocent", which was never
    # sent anywhere.
    assert _queue_titles() == ["Innocent"]

    with state.QUEUE_LOCK:
        state.QUEUE.clear()


def test_move_keeps_everything_when_the_peer_reports_nothing(client, peers_file, stub_identity, monkeypatch) -> None:
    peer = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom")
    # No per-item results and a count that does not cover what was sent: there
    # is no evidence of what landed, so nothing is given up.
    monkeypatch.setattr(peers, "_request", lambda *a, **k: {"accepted": 0, "queue_length": 0})
    _seed_queue("First", "Second")

    moved = client.post(f"/peers/{peer['id']}/send", json={"mode": "move"})
    assert moved.status_code == 200
    assert moved.json()["local_queue_length"] == 2
    assert _queue_titles() == ["First", "Second"]

    with state.QUEUE_LOCK:
        state.QUEUE.clear()


def test_handoff_of_a_selection_leaves_the_unselected_items_here(
    client, peers_file, stub_identity, monkeypatch
) -> None:
    peer = peers.add_peer(base_url="http://tv.local:8787", name="Bedroom")

    from relaytv_app import playback_service

    monkeypatch.setattr(
        playback_service,
        "handoff_snapshot",
        lambda: {"item": {"url": "https://example.com/movie", "title": "Movie"}, "position": 5.0, "duration": 60.0},
    )
    sent: dict[str, object] = {}

    def _request(base_url, path, *, token="", payload=None, timeout=0.0):
        sent.update({"payload": payload})
        return {"status": "handed_off", "playing": True, "accepted": 1, "queue_length": 1, "results": []}

    monkeypatch.setattr(peers, "_request", _request)
    monkeypatch.setattr(
        "relaytv_app.playback_service.complete_peer_handoff",
        lambda snapshot, **kw: True,
    )
    _seed_queue("Keep me", "Take me")

    response = client.post(f"/peers/{peer['id']}/handoff", json={"indexes": [1]})
    assert response.status_code == 200
    assert [entry["title"] for entry in sent["payload"]["items"]] == ["Take me"]
    assert response.json()["local_queue_length"] == 1
    # The session moved, but this device keeps what was held back and will
    # advance into it rather than going idle.
    assert _queue_titles() == ["Keep me"]

    with state.QUEUE_LOCK:
        state.QUEUE.clear()
