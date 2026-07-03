# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import os
import socket
import threading
from .config import env_bool as _env_bool
from .config import runtime_config

try:
    from zeroconf import ServiceInfo, Zeroconf
except Exception:  # pragma: no cover - dependency may be optional in some envs
    ServiceInfo = None
    Zeroconf = None

from . import state

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
    try:
        p = int(float(os.getenv("RELAYTV_PORT") or "8787"))
    except Exception:
        p = 8787
    return max(1, min(65535, p))


def _detect_ipv4() -> str:
    host_override = (os.getenv("RELAYTV_MDNS_HOST") or "").strip()
    if host_override:
        return host_override
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except Exception:
        return "127.0.0.1"
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        try:
            s.close()
        except Exception:
            pass
    return ip


def _device_name() -> str:
    try:
        settings = state.get_settings() if hasattr(state, "get_settings") else {}
    except Exception:
        settings = {}
    name = str((settings or {}).get("device_name") or "").strip()
    if not name:
        name = runtime_config.snapshot().text("RELAYTV_DEVICE_NAME") or "RelayTV"
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
    return {
        b"path": b"/ui",
        b"service": b"relaytv",
        b"version": b"1",
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
