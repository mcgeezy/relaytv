# SPDX-License-Identifier: GPL-3.0-only
"""Stable identity for this RelayTV install.

``device_name`` is a display string an operator can rename at any time, so it
cannot identify a device. Multi-device features need something stable:
discovery must filter this device out of its own browse results, a manually
added peer must de-duplicate against the same box found over mDNS, and queue
import must recognize its own items to avoid forwarding loops.

The id is generated once and persisted next to the other state files. It is
deliberately not part of ``settings.json`` so that it survives a settings
reset and is not user-editable. ``RELAYTV_DEVICE_ID`` lets an operator pin it
(useful for cloned images and tests).
"""
from __future__ import annotations

import os
import re
import socket
import threading
import uuid

from . import config, state
from .config import env_str, runtime_config
from .debug import get_logger


DEVICE_ID_FILE = "device_id"

_LOCK = threading.Lock()
_CACHED_ID = ""
_INVALID_ID_CHARS = re.compile(r"[^A-Za-z0-9._:-]")

logger = get_logger("device_identity")


def _normalize_device_id(value: object) -> str:
    text = _INVALID_ID_CHARS.sub("", str(value or "").strip())
    return text[:64]


def _identity_path() -> str:
    return state._state_path(DEVICE_ID_FILE)


def _read_persisted_id() -> str:
    try:
        with open(_identity_path(), "r", encoding="utf-8") as handle:
            return _normalize_device_id(handle.read())
    except Exception:
        return ""


def _write_persisted_id(value: str) -> None:
    path = _identity_path()
    try:
        state._ensure_state_dir()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(value)
        os.chmod(path, 0o600)
    except Exception as exc:
        logger.warning("device_id_write_failed path=%s error=%s", path, exc)


def device_id() -> str:
    """Return this install's stable device id, generating it on first use."""
    override = _normalize_device_id(env_str("RELAYTV_DEVICE_ID", ""))
    if override:
        return override
    global _CACHED_ID
    with _LOCK:
        if _CACHED_ID:
            return _CACHED_ID
        persisted = _read_persisted_id()
        if persisted:
            _CACHED_ID = persisted
            return _CACHED_ID
        generated = uuid.uuid4().hex
        _write_persisted_id(generated)
        # Persistence is best effort: an unwritable state dir must not break
        # playback, so keep the process-local id and re-generate on restart.
        _CACHED_ID = generated
        return _CACHED_ID


def reset_cache_for_tests() -> None:
    global _CACHED_ID
    with _LOCK:
        _CACHED_ID = ""


def device_name() -> str:
    """Return the operator-facing display name for this device."""
    try:
        settings = state.get_settings() if hasattr(state, "get_settings") else {}
    except Exception:
        settings = {}
    name = str((settings or {}).get("device_name") or "").strip()
    if not name:
        name = runtime_config.snapshot().text("RELAYTV_DEVICE_NAME") or "RelayTV"
    return name[:80].strip() or "RelayTV"


def local_ipv4() -> str:
    """Best-effort LAN address other devices on the network can reach."""
    host_override = env_str("RELAYTV_MDNS_HOST", "").strip()
    if host_override:
        return host_override
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except Exception:
        return "127.0.0.1"
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        try:
            sock.close()
        except Exception:
            pass


def local_base_url() -> str:
    """Return the base URL a peer should use to reach this device."""
    return f"http://{local_ipv4()}:{config.server_port()}"


def app_version() -> str:
    return str(env_str("RELAYTV_IMAGE_VERSION", "") or "").strip() or "local"


def identity_payload() -> dict[str, str]:
    """Public identity advertised to peers (no secrets, safe for anonymous GET)."""
    return {
        "device_id": device_id(),
        "device_name": device_name(),
        "base_url": local_base_url(),
        "version": app_version(),
    }
