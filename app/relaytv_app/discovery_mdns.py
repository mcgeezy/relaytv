# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import os
import queue
import socket
import threading
import time
from . import config, device_identity
from .config import env_bool as _env_bool
from .config import env_int as _env_int
from .debug import get_logger

try:
    from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf
except Exception:  # pragma: no cover - dependency may be optional in some envs
    ServiceBrowser = None
    ServiceInfo = None
    ServiceStateChange = None
    Zeroconf = None

logger = get_logger("discovery_mdns")

_LOCK = threading.Lock()
_ZEROCONF = None
_SERVICE_INFO = None
_LAST_ERROR: str | None = None
_START_THREAD: threading.Thread | None = None
# Registration talks to the network, so it cannot hold _LOCK throughout without
# making stop() wait on it. Instead each attempt claims a generation, does its
# network work unlocked, and re-checks ownership before publishing. stop()
# claims a generation too, which is what retires an attempt already in flight.
_GENERATION = 0

# Browsing state. The browser callbacks must not block the zeroconf event
# thread, so they only enqueue names and a resolver thread does the lookups.
_BROWSE_LOCK = threading.RLock()
_BROWSE_ZEROCONF = None
_BROWSE_BROWSER = None
_BROWSE_LAST_ERROR: str | None = None
_BROWSE_THREAD: threading.Thread | None = None
_BROWSE_QUEUE: queue.Queue | None = None
_DISCOVERED: dict[str, dict[str, object]] = {}
# The browse worker's stop flag used to be a module global. start_browse
# cleared it, so restarting while the previous worker was still shutting down
# un-stopped that worker and left two resolvers running against whichever
# Zeroconf happened to be current. Each session now owns its own.
_BROWSE_SESSION: "_BrowseSession | None" = None
_BROWSE_GENERATION = 0


class _BrowseSession:
    """One browse generation: its own stop flag, queue, and Zeroconf.

    Owning these per generation is what keeps a retired worker from consuming
    its replacement's queue or writing its replacement's globals.
    """

    __slots__ = ("generation", "stop", "queue", "zeroconf", "browser", "thread")

    def __init__(self, generation: int) -> None:
        self.generation = generation
        self.stop = threading.Event()
        self.queue: queue.Queue = queue.Queue(maxsize=256)
        self.zeroconf = None
        self.browser = None
        self.thread: threading.Thread | None = None


def _enabled() -> bool:
    return _env_bool("RELAYTV_MDNS_ENABLED", True)


def _browse_enabled() -> bool:
    """Browsing rides on mDNS being enabled, with its own opt-out."""
    return _enabled() and _env_bool("RELAYTV_MDNS_BROWSE_ENABLED", True)


def _browse_ttl_sec() -> int:
    """How long a service stays listed after it was last seen.

    zeroconf reports removals, but a device that is unplugged (or a bridged
    container that stops receiving multicast) never announces goodbye, so
    entries also age out.
    """
    return _env_int("RELAYTV_MDNS_BROWSE_TTL_SEC", 300, minimum=30, maximum=3600)


def _browse_refresh_sec() -> int:
    """How often known services are re-resolved.

    State-change callbacks alone are not enough to keep entries fresh: a device
    that stays advertised may not produce another callback before the TTL above
    expires, which would drop a peer that is still right there. The sweep also
    catches devices that vanished without announcing goodbye.
    """
    return _env_int("RELAYTV_MDNS_BROWSE_REFRESH_SEC", 60, minimum=10, maximum=600)


def _service_type() -> str:
    st = (os.getenv("RELAYTV_MDNS_SERVICE_TYPE") or "_relaytv._tcp.local.").strip()
    if not st.endswith("."):
        st = f"{st}."
    if not st.startswith("_"):
        st = f"_{st}"
    return st


def _service_port() -> int:
    return config.server_port()


def _detect_ipv4() -> str:
    return device_identity.local_ipv4()


def _device_name() -> str:
    # mDNS instance names are limited to 63 bytes, tighter than the 80-char
    # display name identity allows.
    name = device_identity.device_name()
    if len(name) > 63:
        name = name[:63].strip() or "RelayTV"
    return name


def _instance_name() -> str:
    suffix = (os.getenv("RELAYTV_MDNS_INSTANCE_SUFFIX") or "").strip()
    base = _device_name()
    if suffix:
        return f"{base} {suffix}"
    return base


def _props() -> dict[bytes, bytes]:
    """TXT records advertised with the service.

    ``id`` and ``name`` let a browsing peer filter itself out of its own
    results and de-duplicate a manually added device against the same box found
    over mDNS. Peers on older builds simply omit them and fall back to
    host:port identity, so adding records stays backward compatible.
    """
    return {
        b"path": b"/ui",
        b"service": b"relaytv",
        b"version": b"1",
        b"id": device_identity.device_id().encode("utf-8", "ignore"),
        b"name": _device_name().encode("utf-8", "ignore"),
        b"app": device_identity.app_version().encode("utf-8", "ignore"),
    }


def status() -> dict[str, object]:
    with _LOCK:
        advertise = {
            "enabled": _enabled(),
            "active": _SERVICE_INFO is not None and _ZEROCONF is not None,
            "service_type": _service_type(),
            "instance_name": _instance_name(),
            "port": _service_port(),
            "ip": _detect_ipv4(),
            "last_error": _LAST_ERROR,
        }
    advertise["browse"] = browse_status()
    return advertise


def _claim_generation() -> int:
    """Take ownership of the advertisement slot. Caller must hold _LOCK."""
    global _GENERATION
    _GENERATION += 1
    return _GENERATION


def _owns(generation: int) -> bool:
    """Caller must hold _LOCK."""
    return generation == _GENERATION


def _close_zeroconf(zc) -> None:
    if zc is None:
        return
    try:
        zc.close()
    except Exception:
        pass


def _teardown(zc, info) -> None:
    """Unregister and close, tolerating a partially built pair."""
    try:
        if zc is not None and info is not None:
            zc.unregister_service(info)
    except Exception:
        pass
    _close_zeroconf(zc)


def start() -> dict[str, object]:
    global _ZEROCONF, _SERVICE_INFO, _LAST_ERROR
    with _LOCK:
        if not _enabled():
            return status()
        if _SERVICE_INFO is not None and _ZEROCONF is not None:
            return status()
        if Zeroconf is None or ServiceInfo is None:
            _LAST_ERROR = "zeroconf dependency unavailable"
            return status()
        generation = _claim_generation()

    zc = None
    info = None
    try:
        stype = _service_type()
        name = _instance_name()
        ip = _detect_ipv4()
        info = ServiceInfo(
            type_=stype,
            name=f"{name}.{stype}",
            addresses=[socket.inet_aton(ip)],
            port=_service_port(),
            properties=_props(),
            server=(os.getenv("RELAYTV_MDNS_SERVER") or f"{socket.gethostname()}.local.").strip(),
        )
        zc = Zeroconf()
        zc.register_service(info)
    except Exception as e:
        # Close the Zeroconf *we* built. The previous version closed the global
        # _ZEROCONF, which is still None here because it is only assigned after
        # register_service succeeds — so a failed registration leaked an open
        # Zeroconf, with its sockets and threads, on every retry.
        _teardown(zc, info)
        with _LOCK:
            if _owns(generation):
                _LAST_ERROR = str(e)
                _ZEROCONF = None
                _SERVICE_INFO = None
        return status()

    with _LOCK:
        published = _owns(generation)
        if published:
            _ZEROCONF = zc
            _SERVICE_INFO = info
            _LAST_ERROR = None
    if not published:
        # stop() ran while we were registering. Publishing now would advertise
        # a service the app believes it retired, and nothing would ever
        # unregister it.
        logger.info("mdns_registration_retired generation=%d", generation)
        _teardown(zc, info)
    return status()


def start_async() -> dict[str, object]:
    """Start mDNS registration in a background thread so app startup cannot block."""
    global _START_THREAD
    with _LOCK:
        if _START_THREAD is not None and _START_THREAD.is_alive():
            return status()
        t = threading.Thread(target=start, daemon=True, name="relaytv-mdns-start")
        _START_THREAD = t
        t.start()
    return status()


def stop() -> dict[str, object]:
    global _ZEROCONF, _SERVICE_INFO
    stop_browse()
    with _LOCK:
        # Retires any registration still in flight: it will find it no longer
        # owns the slot and tear down what it built instead of publishing.
        _claim_generation()
        zc = _ZEROCONF
        info = _SERVICE_INFO
        _ZEROCONF = None
        _SERVICE_INFO = None
    _teardown(zc, info)
    return status()


# =========================
# Browsing (finding other RelayTV devices)
# =========================


def _text_property(properties: object, key: str) -> str:
    """Read a TXT record value, which zeroconf hands back as bytes."""
    if not isinstance(properties, dict):
        return ""
    value = properties.get(key.encode("utf-8")) if isinstance(key, str) else None
    if value is None:
        value = properties.get(key)
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "ignore").strip()
        except Exception:
            return ""
    if value is None:
        return ""
    return str(value).strip()


def _preferred_address(addresses: list[str] | None) -> str:
    """Pick the address a peer should be reached on.

    IPv4 first: RelayTV base URLs are typed and shared by humans, and link-local
    IPv6 needs a scope id that does not survive being stored as a plain URL.
    """
    for address in list(addresses or []):
        text = str(address or "").strip()
        if text and ":" not in text:
            return text
    for address in list(addresses or []):
        text = str(address or "").strip()
        if text and not text.lower().startswith("fe80:"):
            return f"[{text}]"
    return ""


def discovered_record_from_service(name: str, info: object) -> dict[str, object] | None:
    """Map a resolved mDNS service into a discovery candidate.

    Returns None for anything that cannot be turned into a reachable RelayTV
    base URL, including this device's own advertisement.
    """
    if info is None:
        return None
    try:
        addresses = list(info.parsed_addresses())
    except Exception:
        addresses = []
    address = _preferred_address(addresses)
    port = 0
    try:
        port = int(info.port or 0)
    except Exception:
        port = 0
    if not address or port <= 0:
        return None

    properties = getattr(info, "properties", None)
    device_id = _text_property(properties, "id")
    if device_id and device_id == device_identity.device_id():
        return None
    instance = str(name or "").split(".")[0].strip()
    device_name = _text_property(properties, "name") or instance or "RelayTV"
    return {
        "service_name": str(name or ""),
        "device_id": device_id,
        "device_name": device_name,
        "base_url": f"http://{address}:{port}",
        "version": _text_property(properties, "app"),
        "last_seen_at": time.time(),
    }


def _forget_service(name: str) -> None:
    with _BROWSE_LOCK:
        _DISCOVERED.pop(str(name or ""), None)


def _remember_service(record: dict[str, object]) -> None:
    with _BROWSE_LOCK:
        _DISCOVERED[str(record.get("service_name") or "")] = record


def discovered() -> list[dict[str, object]]:
    """Currently visible RelayTV devices, newest announcement first."""
    cutoff = time.time() - float(_browse_ttl_sec())
    with _BROWSE_LOCK:
        fresh = {
            name: record
            for name, record in _DISCOVERED.items()
            if float(record.get("last_seen_at") or 0.0) >= cutoff
        }
        if len(fresh) != len(_DISCOVERED):
            _DISCOVERED.clear()
            _DISCOVERED.update(fresh)
        records = [dict(record) for record in fresh.values()]
    # Drop this device if its id changed after the record was cached.
    own = device_identity.device_id()
    records = [r for r in records if str(r.get("device_id") or "") != own]
    records.sort(key=lambda r: float(r.get("last_seen_at") or 0.0), reverse=True)
    return records


def _resolve_service(zc, service_type: str, name: str, *, timeout_ms: int) -> bool:
    """Resolve one service into the cache. Returns True when it is still there."""
    try:
        info = zc.get_service_info(service_type, name, timeout=timeout_ms)
    except Exception as exc:
        logger.debug("mdns_resolve_failed name=%s error=%s", name, exc)
        return False
    record = discovered_record_from_service(name, info)
    if record is None:
        _forget_service(name)
        return False
    with _BROWSE_LOCK:
        known = str(name or "") in _DISCOVERED
    _remember_service(record)
    if not known:
        logger.info(
            "mdns_discovered name=%s device_id=%s base_url=%s",
            record.get("device_name"),
            record.get("device_id"),
            record.get("base_url"),
        )
    return True


def _refresh_known_services(zc, service_type: str, stop: threading.Event | None = None) -> None:
    with _BROWSE_LOCK:
        names = list(_DISCOVERED.keys())
    for name in names:
        if stop is not None and stop.is_set():
            return
        _resolve_service(zc, service_type, name, timeout_ms=1500)


def _resolve_worker(session: "_BrowseSession") -> None:
    """Resolve queued service names off the zeroconf callback thread.

    Reads only its own session, so a retired generation cannot pick work off
    its replacement's queue or resolve against its replacement's Zeroconf.
    """
    service_type = _service_type()
    next_sweep = time.time() + float(_browse_refresh_sec())
    while not session.stop.is_set():
        try:
            item = session.queue.get(timeout=0.5)
        except Exception:
            item = None
        zc = session.zeroconf
        if zc is None:
            continue
        if session.stop.is_set():
            return
        if item is not None:
            queued_type, name = item
            _resolve_service(zc, queued_type or service_type, name, timeout_ms=2000)
        if time.time() >= next_sweep:
            _refresh_known_services(zc, service_type, stop=session.stop)
            next_sweep = time.time() + float(_browse_refresh_sec())


def _on_service_state_change(zeroconf, service_type, name, state_change) -> None:
    """Module-level handler, kept for the current session's queue."""
    _handle_service_state_change(_BROWSE_SESSION, service_type, name, state_change)


def _handle_service_state_change(session, service_type, name, state_change) -> None:
    if ServiceStateChange is not None and state_change == ServiceStateChange.Removed:
        _forget_service(name)
        return
    if session is None or session.stop.is_set():
        return
    try:
        session.queue.put_nowait((service_type, name))
    except Exception:
        pass


def _session_handler(session: "_BrowseSession"):
    """Bind the callback to one generation.

    ServiceBrowser holds this for the life of the browser, so binding it to the
    session keeps a retired browser's callbacks out of the live queue.
    """

    def _handler(zeroconf, service_type, name, state_change) -> None:
        _handle_service_state_change(session, service_type, name, state_change)

    return _handler


def browse_status() -> dict[str, object]:
    with _BROWSE_LOCK:
        active = _BROWSE_BROWSER is not None and _BROWSE_ZEROCONF is not None
        last_error = _BROWSE_LAST_ERROR
    return {
        "enabled": _browse_enabled(),
        "active": active,
        "ttl_sec": _browse_ttl_sec(),
        "found": len(discovered()),
        "last_error": last_error,
    }


def start_browse() -> dict[str, object]:
    """Begin browsing for other RelayTV devices on the local network."""
    global _BROWSE_ZEROCONF, _BROWSE_BROWSER, _BROWSE_LAST_ERROR, _BROWSE_THREAD
    global _BROWSE_QUEUE, _BROWSE_SESSION, _BROWSE_GENERATION
    with _BROWSE_LOCK:
        if not _browse_enabled():
            return browse_status()
        if _BROWSE_BROWSER is not None and _BROWSE_ZEROCONF is not None:
            return browse_status()
        if Zeroconf is None or ServiceBrowser is None:
            _BROWSE_LAST_ERROR = "zeroconf dependency unavailable"
            return browse_status()

        _BROWSE_GENERATION += 1
        session = _BrowseSession(_BROWSE_GENERATION)
        zc = None
        try:
            zc = Zeroconf()
            session.zeroconf = zc
            # Bind the handler to this session before the browser can fire it.
            session.browser = ServiceBrowser(
                zc, _service_type(), handlers=[_session_handler(session)]
            )
        except Exception as exc:
            _BROWSE_LAST_ERROR = str(exc)
            # Close the Zeroconf we built, not the global: the global is still
            # None here, because it is only assigned once the browser exists.
            _close_zeroconf(zc)
            return browse_status()

        _BROWSE_SESSION = session
        _BROWSE_ZEROCONF = zc
        _BROWSE_BROWSER = session.browser
        _BROWSE_QUEUE = session.queue
        _BROWSE_LAST_ERROR = None
        session.thread = threading.Thread(
            target=_resolve_worker,
            args=(session,),
            daemon=True,
            name=f"relaytv-mdns-browse-{session.generation}",
        )
        _BROWSE_THREAD = session.thread
        session.thread.start()
    return browse_status()


def start_browse_async() -> dict[str, object]:
    """Start browsing in a background thread so startup cannot block on it."""
    threading.Thread(target=start_browse, daemon=True, name="relaytv-mdns-browse-start").start()
    return browse_status()


def stop_browse() -> dict[str, object]:
    global _BROWSE_ZEROCONF, _BROWSE_BROWSER, _BROWSE_THREAD, _BROWSE_QUEUE, _BROWSE_SESSION
    with _BROWSE_LOCK:
        session = _BROWSE_SESSION
        browser = _BROWSE_BROWSER
        zc = _BROWSE_ZEROCONF
        thread = _BROWSE_THREAD
        _BROWSE_SESSION = None
        _BROWSE_BROWSER = None
        _BROWSE_ZEROCONF = None
        _BROWSE_QUEUE = None
        _BROWSE_THREAD = None
        _DISCOVERED.clear()
        # Signal inside the lock so a concurrent start cannot observe a live
        # session that is already being torn down.
        if session is not None:
            session.stop.set()

    # Network teardown and the join stay outside the lock and stay bounded.
    try:
        if browser is not None:
            browser.cancel()
    except Exception:
        pass
    _close_zeroconf(zc)
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)
        if thread.is_alive():
            logger.warning("mdns_browse_worker_did_not_exit name=%s", thread.name)
    return browse_status()


def reset_browse_for_tests() -> None:
    global _BROWSE_LAST_ERROR
    with _BROWSE_LOCK:
        _DISCOVERED.clear()
        _BROWSE_LAST_ERROR = None
