# SPDX-License-Identifier: GPL-3.0-only
"""Versioned realtime protocol primitives shared by routes and clients.

The first protocol is deliberately server-to-client only. Playback and other
mutating commands remain authenticated HTTP requests.
"""

from __future__ import annotations

import asyncio
from collections import deque
import copy
from dataclasses import dataclass
import itertools
import threading
import time
from typing import Any
from urllib.parse import urlsplit


PROTOCOL_VERSION = 1
SUBPROTOCOL = "relaytv.realtime.v1"
UI_WEBSOCKET_PATH = "/ui/ws"
OVERLAY_WEBSOCKET_PATH = "/x11/overlay/ws"
UI_SSE_PATH = "/ui/events"
OVERLAY_SSE_PATH = "/x11/overlay/events"
HEARTBEAT_SEC = 5
UI_CHANNEL = "ui"
OVERLAY_CHANNEL = "overlay"
_COALESCED_EVENTS = frozenset({"playback", "status"})


@dataclass(frozen=True, slots=True)
class RealtimeEvent:
    """One event in the versioned WebSocket protocol."""

    event: str
    sequence: int
    data: dict[str, Any]
    timestamp: float

    @classmethod
    def create(
        cls,
        event: str,
        sequence: int,
        data: dict[str, Any] | None = None,
        *,
        timestamp: float | None = None,
    ) -> "RealtimeEvent":
        name = str(event or "").strip()
        if not name:
            raise ValueError("realtime event name is required")
        number = int(sequence)
        if number < 0:
            raise ValueError("realtime sequence must be non-negative")
        payload = copy.deepcopy(dict(data or {}))
        emitted_at = float(time.time() if timestamp is None else timestamp)
        return cls(event=name, sequence=number, data=payload, timestamp=emitted_at)

    def envelope(self) -> dict[str, Any]:
        """Return the stable JSON-compatible WebSocket envelope."""
        return {
            "version": PROTOCOL_VERSION,
            "event": self.event,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "data": dict(self.data),
        }


class RealtimeSubscriptionClosed(RuntimeError):
    """Raised when a consumer waits on a retired subscription."""


class RealtimeSubscription:
    """Event-loop-owned bounded inbox registered with :class:`RealtimeHub`."""

    def __init__(
        self,
        *,
        hub: "RealtimeHub",
        subscription_id: int,
        channel: str,
        transport: str,
        loop: asyncio.AbstractEventLoop,
        maxsize: int,
    ) -> None:
        self._hub = hub
        self.subscription_id = subscription_id
        self.channel = channel
        self.transport = transport
        self.loop = loop
        self.maxsize = max(1, int(maxsize))
        self._items: deque[RealtimeEvent] = deque()
        self._available = asyncio.Event()
        self._closed = False
        self.offered = 0
        self.dropped = 0
        self.coalesced = 0

    @property
    def closed(self) -> bool:
        return self._closed

    def _offer_on_loop(self, event: RealtimeEvent) -> None:
        if self._closed:
            return
        self.offered += 1
        if event.event in _COALESCED_EVENTS:
            for index in range(len(self._items) - 1, -1, -1):
                if self._items[index].event == event.event:
                    del self._items[index]
                    self.coalesced += 1
                    break
        if len(self._items) >= self.maxsize:
            self._items.popleft()
            self.dropped += 1
        self._items.append(event)
        self._available.set()

    def _close_on_loop(self) -> None:
        self._closed = True
        self._items.clear()
        self._available.set()

    def schedule(self, event: RealtimeEvent) -> bool:
        """Schedule delivery without touching asyncio state from this thread."""
        if self._closed:
            return False
        try:
            self.loop.call_soon_threadsafe(self._offer_on_loop, event)
            return True
        except RuntimeError:
            self._hub.unsubscribe(self)
            return False

    async def get(self) -> RealtimeEvent:
        while True:
            if self._items:
                item = self._items.popleft()
                if not self._items:
                    self._available.clear()
                return item
            if self._closed:
                raise RealtimeSubscriptionClosed
            self._available.clear()
            await self._available.wait()

    def get_nowait(self) -> RealtimeEvent:
        if self._items:
            item = self._items.popleft()
            if not self._items:
                self._available.clear()
            return item
        if self._closed:
            raise RealtimeSubscriptionClosed
        raise asyncio.QueueEmpty

    def close(self) -> None:
        self._hub.unsubscribe(self)


class RealtimeHub:
    """Process-local, thread-safe publication hub for realtime transports."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscriptions: dict[int, RealtimeSubscription] = {}
        self._sequences: dict[str, int] = {}
        self._latest_snapshots: dict[str, dict[str, RealtimeEvent]] = {}
        self._ids = itertools.count(1)

    def subscribe(
        self,
        channel: str,
        *,
        transport: str,
        maxsize: int,
        replay_latest: bool = False,
    ) -> RealtimeSubscription:
        name = str(channel or "").strip()
        transport_name = str(transport or "").strip()
        if not name or not transport_name:
            raise ValueError("realtime channel and transport are required")
        loop = asyncio.get_running_loop()
        with self._lock:
            existing_channel_subscribers = any(
                item.channel == name for item in self._subscriptions.values()
            )
            subscription = RealtimeSubscription(
                hub=self,
                subscription_id=next(self._ids),
                channel=name,
                transport=transport_name,
                loop=loop,
                maxsize=maxsize,
            )
            self._subscriptions[subscription.subscription_id] = subscription
            retained = (
                sorted(
                    self._latest_snapshots.get(name, {}).values(),
                    key=lambda item: item.sequence,
                )
                if replay_latest and existing_channel_subscribers
                else []
            )
            # Schedule retained snapshots before a concurrent publisher can
            # allocate and hand off a newer channel sequence.
            for event in retained:
                subscription.schedule(event)
        return subscription

    def unsubscribe(self, subscription: RealtimeSubscription) -> None:
        with self._lock:
            existing = self._subscriptions.get(subscription.subscription_id)
            if existing is not subscription:
                return
            self._subscriptions.pop(subscription.subscription_id, None)
            if not any(
                item.channel == subscription.channel for item in self._subscriptions.values()
            ):
                self._latest_snapshots.pop(subscription.channel, None)
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is subscription.loop:
            subscription._close_on_loop()
            return
        try:
            subscription.loop.call_soon_threadsafe(subscription._close_on_loop)
        except RuntimeError:
            # A closed loop cannot have a waiter left to wake.
            subscription._closed = True

    def publish(self, channel: str, event: str, data: dict[str, Any]) -> int:
        """Publish from any thread and return the number of scheduled clients."""
        name = str(channel or "").strip()
        if not name:
            raise ValueError("realtime channel is required")
        with self._lock:
            sequence = self._sequences.get(name, 0) + 1
            self._sequences[name] = sequence
            message = RealtimeEvent.create(event, sequence, data)
            if message.event in _COALESCED_EVENTS:
                self._latest_snapshots.setdefault(name, {})[message.event] = message
            targets = [
                subscription
                for subscription in self._subscriptions.values()
                if subscription.channel == name
            ]
            # Keep the event-loop handoff in the same critical section as
            # sequence allocation. Otherwise concurrent publishers can
            # schedule sequence N+1 before sequence N after releasing the
            # hub lock.
            return sum(1 for subscription in targets if subscription.schedule(message))

    def current_sequence(self, channel: str) -> int:
        with self._lock:
            return self._sequences.get(channel, 0)

    def subscriber_count(self, channel: str, *, transport: str | None = None) -> int:
        with self._lock:
            return sum(
                1
                for subscription in self._subscriptions.values()
                if subscription.channel == channel
                and (transport is None or subscription.transport == transport)
            )

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            subscriptions = list(self._subscriptions.values())
            sequences = dict(self._sequences)
        by_channel: dict[str, int] = {}
        by_transport: dict[str, int] = {}
        for subscription in subscriptions:
            by_channel[subscription.channel] = by_channel.get(subscription.channel, 0) + 1
            by_transport[subscription.transport] = by_transport.get(subscription.transport, 0) + 1
        return {
            "subscribers": len(subscriptions),
            "subscribers_by_channel": by_channel,
            "subscribers_by_transport": by_transport,
            "sequences": sequences,
            "offered": sum(subscription.offered for subscription in subscriptions),
            "dropped": sum(subscription.dropped for subscription in subscriptions),
            "coalesced": sum(subscription.coalesced for subscription in subscriptions),
        }


realtime_hub = RealtimeHub()


def realtime_capabilities_payload(*, websocket_enabled: bool) -> dict[str, Any]:
    """Describe only transports implemented by this server generation."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "preferred_transport": "websocket" if websocket_enabled else "sse",
        "websocket": {
            "enabled": bool(websocket_enabled),
            "ui": UI_WEBSOCKET_PATH,
            "overlay": OVERLAY_WEBSOCKET_PATH,
            "subprotocol": SUBPROTOCOL,
        },
        "sse": {
            "enabled": True,
            "ui": UI_SSE_PATH,
            "overlay": OVERLAY_SSE_PATH,
        },
        "heartbeat_sec": HEARTBEAT_SEC,
        "replay": False,
    }


def _default_port(scheme: str) -> int | None:
    if scheme in {"http", "ws"}:
        return 80
    if scheme in {"https", "wss"}:
        return 443
    return None


def _host_authority(host: str, scheme: str) -> tuple[str, int | None] | None:
    value = str(host or "").strip()
    if not value or "," in value or any(ch.isspace() for ch in value):
        return None
    try:
        parsed = urlsplit(f"//{value}")
        hostname = str(parsed.hostname or "").rstrip(".").lower()
        if not hostname or parsed.username is not None or parsed.password is not None:
            return None
        return hostname, parsed.port if parsed.port is not None else _default_port(scheme)
    except (TypeError, ValueError):
        return None


def websocket_origin_allowed(
    *,
    origin: str | None,
    host: str | None,
    websocket_scheme: str,
) -> bool:
    """Validate browser origin while allowing origin-less native read clients.

    Uvicorn's trusted proxy handling is responsible for placing the effective
    public scheme in the ASGI scope, while the reverse proxy must preserve the
    public Host header. Untrusted forwarded headers are intentionally ignored
    here.
    """
    value = str(origin or "").strip()
    if not value:
        return True

    socket_scheme = str(websocket_scheme or "").strip().lower()
    expected_origin_scheme = "https" if socket_scheme in {"wss", "https"} else "http"
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"}:
            return False
        if parsed.scheme.lower() != expected_origin_scheme:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return False
        origin_host = str(parsed.hostname or "").rstrip(".").lower()
        if not origin_host:
            return False
        origin_authority = (
            origin_host,
            parsed.port if parsed.port is not None else _default_port(parsed.scheme.lower()),
        )
    except (TypeError, ValueError):
        return False

    expected_authority = _host_authority(str(host or ""), expected_origin_scheme)
    return expected_authority is not None and origin_authority == expected_authority
