# SPDX-License-Identifier: GPL-3.0-only
"""Jellyfin/Emby control socket.

Holding a WebSocket open on ``/socket`` is what turns a registered session into
a *cast target*. The server computes ``SupportsRemoteControl`` as "media
control advertised **and** a live socket on this session", so without this
module RelayTV is listed but every command the server sends is dropped on the
floor.

Transport only, in keeping with ``jellyfin_receiver``: inbound messages are
normalized to ``(action, payload)`` and handed to the command sink the routes
package registers. No playback logic lives here.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from urllib.parse import quote, urlsplit

from ..config import env_bool as _env_bool
from ..config import env_float as _env_float
from ..debug import get_logger
from . import jellyfin_receiver

try:
    from websockets.sync.client import connect as _ws_connect
except Exception:  # pragma: no cover - dependency may be optional in some envs
    _ws_connect = None

logger = get_logger("jellyfin_ws")

# The server tells us its keepalive period on connect (``ForceKeepAlive``);
# this is only what we assume until it does.
_DEFAULT_KEEPALIVE_SEC = 60.0

# Bound the command backlog. A burst of commands while mpv is still starting
# should be dropped loudly rather than queued into a stale avalanche that
# replays minutes later.
_COMMAND_QUEUE_MAX = 32

_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_WORKER: threading.Thread | None = None
_STOP = threading.Event()
_COMMANDS: queue.Queue | None = None
_STATE: dict[str, object] = {
    "connected": False,
    "last_connect_ts": None,
    "last_error": None,
    "reconnects": 0,
    "keepalive_sec": None,
    "commands_received": 0,
    "commands_dropped": 0,
}


def _reset_state() -> None:
    with _LOCK:
        _STATE["connected"] = False
        _STATE["last_connect_ts"] = None
        _STATE["last_error"] = None
        _STATE["reconnects"] = 0
        _STATE["keepalive_sec"] = None
        _STATE["commands_received"] = 0
        _STATE["commands_dropped"] = 0


def _mark_error(msg: object) -> None:
    # Everything routes through the receiver's sanitizer: the socket URL carries
    # the Jellyfin access token as ``api_key``, and a failed handshake reports
    # the URL it tried.
    text = jellyfin_receiver._sanitize_error_text(msg)
    with _LOCK:
        _STATE["last_error"] = text or None


def enabled() -> bool:
    return _env_bool("RELAYTV_JELLYFIN_WS_ENABLED", True)


def status() -> dict[str, object]:
    """Socket health, for ``/integrations/jellyfin/status``. Never a token."""
    with _LOCK:
        out = dict(_STATE)
    out["enabled"] = enabled()
    out["available"] = _ws_connect is not None
    if _ws_connect is None:
        out["last_error"] = "websockets package not installed"
    return out


def socket_url(*, server_url: str, token: str, device_id: str) -> str:
    """Build the control-socket URL. The token rides as ``api_key``."""
    base = str(server_url or "").strip().rstrip("/")
    if not base or not token or not device_id:
        return ""
    parts = urlsplit(base)
    scheme = "wss" if parts.scheme == "https" else "ws"
    path = (parts.path or "").rstrip("/")
    return f"{scheme}://{parts.netloc}{path}/socket?api_key={quote(token)}&deviceId={quote(device_id)}"


def normalize_message(raw: object) -> tuple[str, dict[str, object]] | None:
    """Map one inbound socket message to a command ingress call.

    Returns ``None`` for everything RelayTV does not act on — ``KeepAlive``,
    ``Sessions``, ``UserDataChanged``, library refreshes and the rest of the
    server's chatter all arrive on this socket too.
    """
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            envelope = json.loads(raw)
        except Exception:
            return None
    else:
        envelope = raw
    if not isinstance(envelope, dict):
        return None

    message_type = str(envelope.get("MessageType") or "").strip()
    data = envelope.get("Data")
    payload: dict[str, object] = dict(data) if isinstance(data, dict) else {}

    if message_type == "Play":
        action = "play"
    elif message_type == "Playstate":
        # ``normalize_action`` reads payload["Command"] (Pause/Unpause/Seek/...).
        action = ""
    elif message_type == "GeneralCommand":
        # ``normalize_action`` reads payload["Name"]; Arguments are flattened so
        # extract_volume finds Arguments["Volume"] where it already looks.
        args = payload.get("Arguments")
        if isinstance(args, dict):
            for key, value in args.items():
                payload.setdefault(str(key), value)
        action = ""
    else:
        return None

    # MessageId is unique per message and is already read by
    # extract_command_id, so a message the server repeats is deduped for free.
    message_id = str(envelope.get("MessageId") or "").strip()
    if message_id:
        payload.setdefault("MessageId", message_id)
    return action, payload


def _keepalive_interval(force_keepalive_data: object) -> float:
    """Half the server's timeout, which is the interval it expects."""
    try:
        seconds = float(force_keepalive_data)
    except Exception:
        seconds = _DEFAULT_KEEPALIVE_SEC
    if seconds <= 0:
        seconds = _DEFAULT_KEEPALIVE_SEC
    return max(1.0, seconds / 2.0)


def backoff_sec(failures: int) -> float:
    base = max(0.5, _env_float("RELAYTV_JELLYFIN_WS_RETRY_BASE_SEC", 3.0))
    cap = max(base, _env_float("RELAYTV_JELLYFIN_WS_RETRY_MAX_SEC", 60.0))
    return min(cap, base * (2 ** max(0, int(failures) - 1)))


def _submit(action: str, payload: dict[str, object]) -> None:
    q = _COMMANDS
    if q is None:
        return
    try:
        q.put_nowait((action, payload))
        with _LOCK:
            _STATE["commands_received"] = int(_STATE.get("commands_received") or 0) + 1
    except queue.Full:
        with _LOCK:
            _STATE["commands_dropped"] = int(_STATE.get("commands_dropped") or 0) + 1
        logger.warning("jellyfin_ws_command_dropped backlog_full action=%s", action or "playstate")


def _command_worker() -> None:
    """Run commands off the reader thread.

    Starting mpv takes several seconds (6-12s on a Pi). Executing a Play on the
    reader would stall keepalives long enough for the server to drop the
    session mid-handoff, so the reader only enqueues.
    """
    while not _STOP.is_set():
        q = _COMMANDS
        if q is None:
            _STOP.wait(0.2)
            continue
        try:
            item = q.get(timeout=0.5)
        except queue.Empty:
            continue
        action, payload = item
        try:
            jellyfin_receiver.dispatch_command(action, payload)
        except Exception as e:
            _mark_error(e)
            logger.warning("jellyfin_ws_command_failed action=%s err=%s", action or "playstate", jellyfin_receiver._sanitize_error_text(e))


def _read_loop(ws) -> None:
    keepalive = _DEFAULT_KEEPALIVE_SEC / 2.0
    next_keepalive = time.monotonic() + keepalive
    while not _STOP.is_set():
        timeout = max(0.2, min(5.0, next_keepalive - time.monotonic()))
        try:
            raw = ws.recv(timeout=timeout)
        except TimeoutError:
            raw = None
        if raw is not None:
            envelope = None
            try:
                envelope = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
            except Exception:
                envelope = None
            if isinstance(envelope, dict) and str(envelope.get("MessageType") or "") == "ForceKeepAlive":
                keepalive = _keepalive_interval(envelope.get("Data"))
                next_keepalive = time.monotonic() + keepalive
                with _LOCK:
                    _STATE["keepalive_sec"] = keepalive
            else:
                routed = normalize_message(envelope)
                if routed is not None:
                    _submit(routed[0], routed[1])
        if time.monotonic() >= next_keepalive:
            ws.send(json.dumps({"MessageType": "KeepAlive"}))
            next_keepalive = time.monotonic() + keepalive


def _connect_once() -> None:
    st = jellyfin_receiver.status()
    url = socket_url(
        server_url=str(st.get("server_url") or ""),
        token=jellyfin_receiver.active_token(),
        device_id=str(st.get("device_id") or ""),
    )
    if not url:
        raise RuntimeError("jellyfin socket not configured")
    open_timeout = _env_float("RELAYTV_JELLYFIN_WS_CONNECT_TIMEOUT_SEC", 8.0)
    # proxy=None: websockets 15+ honours HTTP_PROXY by default, which would send
    # a LAN media-server connection through an unrelated proxy.
    with _ws_connect(url, open_timeout=open_timeout, proxy=None, max_size=2**20) as ws:
        with _LOCK:
            _STATE["connected"] = True
            _STATE["last_connect_ts"] = int(time.time())
            _STATE["last_error"] = None
        logger.info("jellyfin_ws_connected device_id=%s", st.get("device_id"))
        try:
            _read_loop(ws)
        finally:
            with _LOCK:
                _STATE["connected"] = False


def _socket_worker() -> None:
    failures = 0
    while not _STOP.is_set():
        if not _ready():
            _STOP.wait(2.0)
            continue
        try:
            _connect_once()
            failures = 0
        except Exception as e:
            failures += 1
            _mark_error(e)
            with _LOCK:
                _STATE["reconnects"] = int(_STATE.get("reconnects") or 0) + 1
            logger.warning("jellyfin_ws_disconnected failures=%d err=%s", failures, jellyfin_receiver._sanitize_error_text(e))
        if _STOP.is_set():
            break
        _STOP.wait(backoff_sec(failures) if failures else 1.0)


def _ready() -> bool:
    """Only dial once there is a session worth attaching a socket to."""
    st = jellyfin_receiver.status()
    if not bool(st.get("enabled")) or not bool(st.get("running")):
        return False
    if not str(st.get("server_url") or "").strip():
        return False
    if not str(st.get("device_id") or "").strip():
        return False
    if not jellyfin_receiver.active_token():
        return False
    return jellyfin_receiver.command_sink_registered()


def ensure_running() -> None:
    """Start the socket threads if they should be up and are not."""
    global _THREAD, _WORKER, _COMMANDS
    if not enabled() or _ws_connect is None:
        return
    if not _ready():
        return
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP.clear()
        _COMMANDS = queue.Queue(maxsize=_COMMAND_QUEUE_MAX)
        _WORKER = threading.Thread(target=_command_worker, daemon=True, name="relaytv-jellyfin-ws-commands")
        _WORKER.start()
        _THREAD = threading.Thread(target=_socket_worker, daemon=True, name="relaytv-jellyfin-ws")
        _THREAD.start()


def stop() -> None:
    global _THREAD, _WORKER, _COMMANDS
    _STOP.set()
    with _LOCK:
        thread, worker = _THREAD, _WORKER
        _THREAD = None
        _WORKER = None
        _COMMANDS = None
    for t in (thread, worker):
        if t is not None and t.is_alive():
            t.join(timeout=1.0)
    _reset_state()
