# SPDX-License-Identifier: GPL-3.0-only
from fastapi.testclient import TestClient
import pytest

from relaytv_app.main import create_app
from relaytv_app.realtime import (
    RealtimeEvent,
    realtime_capabilities_payload,
    websocket_origin_allowed,
)


def test_realtime_capabilities_select_only_available_transport() -> None:
    payload = realtime_capabilities_payload(websocket_enabled=False)

    assert payload == {
        "protocol_version": 1,
        "preferred_transport": "sse",
        "websocket": {
            "enabled": False,
            "ui": "/ui/ws",
            "overlay": "/x11/overlay/ws",
            "subprotocol": "relaytv.realtime.v1",
        },
        "sse": {
            "enabled": True,
            "ui": "/ui/events",
            "overlay": "/x11/overlay/events",
        },
        "heartbeat_sec": 5,
        "replay": False,
    }


def test_realtime_capabilities_route_is_open_and_not_cached(monkeypatch) -> None:
    monkeypatch.setenv("RELAYTV_API_TOKEN", "operator-secret")
    client = TestClient(create_app(testing=True))

    response = client.get("/realtime/capabilities")

    assert response.status_code == 200
    assert response.json()["preferred_transport"] == "sse"
    assert response.headers["cache-control"] == "no-store"
    assert "operator-secret" not in response.text


def test_realtime_event_builds_stable_versioned_envelope() -> None:
    event = RealtimeEvent.create(
        "playback",
        42,
        {"playing": True},
        timestamp=1234.5,
    )

    assert event.envelope() == {
        "version": 1,
        "event": "playback",
        "sequence": 42,
        "timestamp": 1234.5,
        "data": {"playing": True},
    }


@pytest.mark.parametrize("event,sequence", [("", 0), ("   ", 1), ("status", -1)])
def test_realtime_event_rejects_invalid_identity(event: str, sequence: int) -> None:
    with pytest.raises(ValueError):
        RealtimeEvent.create(event, sequence)


@pytest.mark.parametrize(
    ("origin", "host", "scheme"),
    [
        (None, "relaytv.local:8787", "ws"),
        ("", "relaytv.local:8787", "ws"),
        ("http://relaytv.local:8787", "relaytv.local:8787", "ws"),
        ("https://relay.example", "relay.example", "wss"),
        ("https://[2001:db8::1]:8787", "[2001:db8::1]:8787", "wss"),
    ],
)
def test_websocket_origin_accepts_same_origin_and_native_clients(
    origin: str | None,
    host: str,
    scheme: str,
) -> None:
    assert websocket_origin_allowed(origin=origin, host=host, websocket_scheme=scheme)


@pytest.mark.parametrize(
    ("origin", "host", "scheme"),
    [
        ("https://evil.example", "relay.example", "wss"),
        ("http://relay.example", "relay.example", "wss"),
        ("https://relay.example:444", "relay.example", "wss"),
        ("file://relay.example", "relay.example", "wss"),
        ("https://user:pass@relay.example", "relay.example", "wss"),
        ("https://relay.example/path", "relay.example", "wss"),
        ("https://relay.example", "relay.example, evil.example", "wss"),
    ],
)
def test_websocket_origin_rejects_foreign_or_malformed_browser_origins(
    origin: str,
    host: str,
    scheme: str,
) -> None:
    assert not websocket_origin_allowed(origin=origin, host=host, websocket_scheme=scheme)
