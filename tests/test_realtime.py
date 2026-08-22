# SPDX-License-Identifier: GPL-3.0-only
import asyncio

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from relaytv_app import upload_store
from relaytv_app import routes
from relaytv_app.main import create_app
from relaytv_app.realtime import (
    OVERLAY_CHANNEL,
    UI_CHANNEL,
    RealtimeHub,
    RealtimeEvent,
    RealtimeSubscriptionClosed,
    realtime_capabilities_payload,
    realtime_hub,
    websocket_origin_allowed,
)


@pytest.fixture
def realtime_client(monkeypatch):
    monkeypatch.setattr(upload_store, "cleanup_uploads", lambda _settings: None)
    with TestClient(create_app(testing=True)) as client:
        yield client


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
    assert response.json()["preferred_transport"] == "websocket"
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


def test_realtime_hub_replays_shared_snapshots_only_while_channel_is_active() -> None:
    async def scenario() -> None:
        hub = RealtimeHub()
        first = hub.subscribe(UI_CHANNEL, transport="sse", maxsize=10, replay_latest=True)
        hub.publish(UI_CHANNEL, "playback", {"position": 10})
        hub.publish(UI_CHANNEL, "status", {"state": "playing"})
        await asyncio.sleep(0)

        second = hub.subscribe(UI_CHANNEL, transport="websocket", maxsize=10, replay_latest=True)
        await asyncio.sleep(0)
        assert second.get_nowait().data == {"position": 10}
        assert second.get_nowait().data == {"state": "playing"}

        first.close()
        second.close()
        third = hub.subscribe(UI_CHANNEL, transport="sse", maxsize=10, replay_latest=True)
        await asyncio.sleep(0)
        with pytest.raises(asyncio.QueueEmpty):
            third.get_nowait()
        third.close()

    asyncio.run(scenario())


def test_ui_snapshot_sampler_computes_once_for_multiple_subscribers(monkeypatch) -> None:
    calls = {"fast": 0, "status": 0}

    def fast_snapshot():
        calls["fast"] += 1
        return {"playing": True, "has_now_playing": True, "queue_length": 0, "position": 1}

    def full_snapshot():
        calls["status"] += 1
        return {"playing": True, "state": "playing", "queue": []}

    monkeypatch.setattr(routes, "_playback_state_fast_snapshot", fast_snapshot)
    monkeypatch.setattr(routes, "_status_payload", full_snapshot)

    async def scenario() -> None:
        first = realtime_hub.subscribe(UI_CHANNEL, transport="sse", maxsize=10)
        second = realtime_hub.subscribe(UI_CHANNEL, transport="websocket", maxsize=10)
        stop_event = asyncio.Event()
        task = asyncio.create_task(routes._ui_snapshot_sampler(stop_event))
        try:
            first_events = {
                (await asyncio.wait_for(first.get(), timeout=1)).event,
                (await asyncio.wait_for(first.get(), timeout=1)).event,
            }
            second_events = {
                (await asyncio.wait_for(second.get(), timeout=1)).event,
                (await asyncio.wait_for(second.get(), timeout=1)).event,
            }
            assert first_events == {"playback", "status"}
            assert second_events == {"playback", "status"}
        finally:
            stop_event.set()
            await task
            first.close()
            second.close()

    asyncio.run(scenario())
    assert calls == {"fast": 1, "status": 1}


def test_ui_websocket_negotiates_protocol_and_receives_published_event(realtime_client) -> None:
    with realtime_client.websocket_connect(
        "/ui/ws",
        subprotocols=["relaytv.realtime.v1"],
        headers={"origin": "http://testserver"},
    ) as websocket:
        hello = websocket.receive_json()
        assert hello["version"] == 1
        assert hello["event"] == "hello"
        assert hello["sequence"] == 0
        assert hello["data"] == {
            "protocol_version": 1,
            "heartbeat_sec": 5,
            "replay": False,
        }
        assert websocket.accepted_subprotocol == "relaytv.realtime.v1"

        realtime_hub.publish(UI_CHANNEL, "queue", {"queue_length": 3})
        while True:
            event = websocket.receive_json()
            if event["event"] == "queue":
                break
        assert event["data"] == {"queue_length": 3}


def test_ui_websocket_accepts_originless_native_client(realtime_client) -> None:
    with realtime_client.websocket_connect(
        "/ui/ws",
        subprotocols=["relaytv.realtime.v1"],
    ) as websocket:
        assert websocket.receive_json()["event"] == "hello"


@pytest.mark.parametrize(
    ("subprotocols", "headers", "close_code"),
    [
        ([], {"origin": "http://testserver"}, 1002),
        (["relaytv.realtime.v1"], {"origin": "https://evil.example"}, 1008),
    ],
)
def test_ui_websocket_rejects_unsupported_protocol_or_foreign_origin(
    realtime_client,
    subprotocols: list[str],
    headers: dict[str, str],
    close_code: int,
) -> None:
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with realtime_client.websocket_connect(
            "/ui/ws",
            subprotocols=subprotocols,
            headers=headers,
        ):
            pass
    assert exc_info.value.code == close_code


def test_ui_websocket_rejects_application_messages(realtime_client) -> None:
    with realtime_client.websocket_connect(
        "/ui/ws",
        subprotocols=["relaytv.realtime.v1"],
        headers={"origin": "http://testserver"},
    ) as websocket:
        assert websocket.receive_json()["event"] == "hello"
        websocket.send_json({"command": "pause"})
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()
    assert exc_info.value.code == 1008


def test_overlay_websocket_negotiates_and_receives_toast(realtime_client) -> None:
    with realtime_client.websocket_connect(
        "/x11/overlay/ws",
        subprotocols=["relaytv.realtime.v1"],
        headers={"origin": "http://testserver"},
    ) as websocket:
        hello = websocket.receive_json()
        assert hello["event"] == "hello"
        assert hello["sequence"] == 0
        assert websocket.accepted_subprotocol == "relaytv.realtime.v1"

        realtime_hub.publish(OVERLAY_CHANNEL, "toast", {"type": "toast", "text": "Ready"})
        toast = websocket.receive_json()
        assert toast["event"] == "toast"
        assert toast["data"] == {"type": "toast", "text": "Ready"}

    assert realtime_hub.subscriber_count(OVERLAY_CHANNEL, transport="websocket") == 0


def test_overlay_websocket_rejects_foreign_browser_origin(realtime_client) -> None:
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with realtime_client.websocket_connect(
            "/x11/overlay/ws",
            subprotocols=["relaytv.realtime.v1"],
            headers={"origin": "https://evil.example"},
        ):
            pass
    assert exc_info.value.code == 1008


def test_x11_overlay_page_prefers_websocket_and_retains_sse_fallback(realtime_client) -> None:
    response = realtime_client.get("/x11/overlay")

    assert response.status_code == 200
    assert "new WebSocket" in response.text
    assert "new EventSource" in response.text
    assert "/realtime/capabilities" in response.text
    assert "/x11/overlay/ws" in response.text
    assert "/x11/overlay/events" in response.text


def test_sse_adapters_preserve_legacy_wire_framing() -> None:
    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    async def scenario() -> None:
        ui_response = await routes._ui_events_sse(ConnectedRequest())
        ui_stream = ui_response.body_iterator
        overlay_response = await routes._x11_overlay_sse()
        overlay_stream = overlay_response.body_iterator
        try:
            ui_hello = await anext(ui_stream)
            overlay_hello = await anext(overlay_stream)
            assert ui_hello.startswith("event: hello\ndata: {")
            assert '"type":"hello"' in ui_hello
            assert overlay_hello.startswith('data: {"type": "hello", "ts": ')

            realtime_hub.publish(UI_CHANNEL, "queue", {"queue_length": 2})
            realtime_hub.publish(
                OVERLAY_CHANNEL,
                "toast",
                {"type": "toast", "text": "Ready"},
            )
            assert await asyncio.wait_for(anext(ui_stream), timeout=1) == (
                'event: queue\ndata: {"queue_length":2}\n\n'
            )
            assert await asyncio.wait_for(anext(overlay_stream), timeout=1) == (
                'data: {"type":"toast","text":"Ready"}\n\n'
            )
        finally:
            await ui_stream.aclose()
            await overlay_stream.aclose()

    asyncio.run(scenario())
