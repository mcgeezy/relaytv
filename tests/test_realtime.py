# SPDX-License-Identifier: GPL-3.0-only
import asyncio

from fastapi.testclient import TestClient
import pytest

from relaytv_app.main import create_app
from relaytv_app.realtime import (
    OVERLAY_CHANNEL,
    UI_CHANNEL,
    RealtimeHub,
    RealtimeEvent,
    RealtimeSubscriptionClosed,
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


def test_realtime_hub_hands_thread_publication_to_subscriber_loop() -> None:
    async def scenario() -> None:
        hub = RealtimeHub()
        subscription = hub.subscribe(UI_CHANNEL, transport="sse", maxsize=10)

        scheduled = await asyncio.to_thread(
            hub.publish,
            UI_CHANNEL,
            "queue",
            {"queue_length": 2},
        )
        message = await subscription.get()

        assert scheduled == 1
        assert message.event == "queue"
        assert message.sequence == 1
        assert message.data == {"queue_length": 2}
        subscription.close()

    asyncio.run(scenario())


def test_realtime_hub_copies_payload_before_late_delivery() -> None:
    async def scenario() -> None:
        hub = RealtimeHub()
        subscription = hub.subscribe(UI_CHANNEL, transport="websocket", maxsize=10)
        payload = {"queue": [{"title": "Original"}]}

        hub.publish(UI_CHANNEL, "queue", payload)
        payload["queue"][0]["title"] = "Mutated"
        message = await subscription.get()

        assert message.data == {"queue": [{"title": "Original"}]}
        subscription.close()

    asyncio.run(scenario())


def test_realtime_hub_coalesces_pending_snapshots() -> None:
    async def scenario() -> None:
        hub = RealtimeHub()
        subscription = hub.subscribe(UI_CHANNEL, transport="websocket", maxsize=10)

        hub.publish(UI_CHANNEL, "playback", {"position": 1})
        hub.publish(UI_CHANNEL, "playback", {"position": 2})
        await asyncio.sleep(0)

        message = subscription.get_nowait()
        assert message.sequence == 2
        assert message.data == {"position": 2}
        with pytest.raises(asyncio.QueueEmpty):
            subscription.get_nowait()
        assert subscription.coalesced == 1
        subscription.close()

    asyncio.run(scenario())


def test_realtime_hub_drops_oldest_non_snapshot_when_bounded() -> None:
    async def scenario() -> None:
        hub = RealtimeHub()
        subscription = hub.subscribe(OVERLAY_CHANNEL, transport="sse", maxsize=2)

        for number in range(3):
            hub.publish(OVERLAY_CHANNEL, "toast", {"number": number})
        await asyncio.sleep(0)

        assert subscription.get_nowait().data == {"number": 1}
        assert subscription.get_nowait().data == {"number": 2}
        assert subscription.dropped == 1
        subscription.close()

    asyncio.run(scenario())


def test_realtime_hub_tracks_transport_counts_and_closes_waiters() -> None:
    async def scenario() -> None:
        hub = RealtimeHub()
        ui = hub.subscribe(UI_CHANNEL, transport="sse", maxsize=2)
        overlay = hub.subscribe(OVERLAY_CHANNEL, transport="websocket", maxsize=2)

        assert hub.subscriber_count(UI_CHANNEL) == 1
        assert hub.subscriber_count(OVERLAY_CHANNEL, transport="websocket") == 1
        assert hub.metrics()["subscribers_by_transport"] == {"sse": 1, "websocket": 1}

        ui.close()
        overlay.close()
        with pytest.raises(RealtimeSubscriptionClosed):
            await ui.get()

    asyncio.run(scenario())
