# SPDX-License-Identifier: GPL-3.0-only
"""Peer device registry, transfer payload, and queue import contracts."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from relaytv_app import device_identity, peers, state
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

    for bad in ("", "ftp://tv.local", "http://", "http://user:pass@tv.local"):
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
