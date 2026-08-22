# SPDX-License-Identifier: GPL-3.0-only
"""Versioned realtime protocol primitives shared by routes and clients.

The first protocol is deliberately server-to-client only. Playback and other
mutating commands remain authenticated HTTP requests.
"""

from __future__ import annotations

from dataclasses import dataclass
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
        payload = dict(data or {})
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
