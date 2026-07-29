# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import os
import socket
import threading
from . import config, device_identity
from .config import env_bool as _env_bool

try:
    from zeroconf import ServiceInfo, Zeroconf
except Exception:  # pragma: no cover - dependency may be optional in some envs
    ServiceInfo = None
    Zeroconf = None

_LOCK = threading.Lock()
_ZEROCONF = None
_SERVICE_INFO = None
_LAST_ERROR: str | None = None
_START_THREAD: threading.Thread | None = None


def _enabled() -> bool:
    return _env_bool("RELAYTV_MDNS_ENABLED", True)


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
        return {
            "enabled": _enabled(),
            "active": _SERVICE_INFO is not None and _ZEROCONF is not None,
            "service_type": _service_type(),
            "instance_name": _instance_name(),
            "port": _service_port(),
            "ip": _detect_ipv4(),
            "last_error": _LAST_ERROR,
        }


def start() -> dict[str, object]:
    global _ZEROCONF, _SERVICE_INFO, _LAST_ERROR
    with _LOCK:
        if not _enabled():
            pass
        elif _SERVICE_INFO is not None and _ZEROCONF is not None:
            pass
        elif Zeroconf is None or ServiceInfo is None:
            _LAST_ERROR = "zeroconf dependency unavailable"
        else:
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
                _ZEROCONF = zc
                _SERVICE_INFO = info
                _LAST_ERROR = None
            except Exception as e:
                _LAST_ERROR = str(e)
                try:
                    if _ZEROCONF is not None:
                        _ZEROCONF.close()
                except Exception:
                    pass
                _ZEROCONF = None
                _SERVICE_INFO = None
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
    with _LOCK:
        zc = _ZEROCONF
        info = _SERVICE_INFO
        _ZEROCONF = None
        _SERVICE_INFO = None
    try:
        if zc is not None and info is not None:
            zc.unregister_service(info)
    except Exception:
        pass
    try:
        if zc is not None:
            zc.close()
    except Exception:
        pass
    return status()
