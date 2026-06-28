# SPDX-License-Identifier: GPL-3.0-only
"""
RelayTV X11 overlay process manager.

Starts a transparent always-on-top overlay window (WebKitGTK) only when:
- XDG_SESSION_TYPE is x11 (not wayland)
- DISPLAY is set
- RELAYTV_X11_OVERLAY=1 (or true/yes/on)

The overlay loads a RelayTV-served page and subscribes to SSE notifications.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

_OVERLAY_LOCK = threading.Lock()
_OVERLAY_PROC: Optional[subprocess.Popen] = None

def _xauthority_file(env: dict[str, str]) -> str | None:
    path = env.get("XAUTHORITY")
    if path and Path(path).is_file():
        return path
    runtime_dir = env.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        return None
    try:
        candidates = sorted(
            Path(runtime_dir).glob(".mutter-Xwaylandauth.*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        return None
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except Exception:
            continue
    return None

def x11_session() -> bool:
    # DISPLAY is the useful signal here: on Wayland hosts the container may
    # still have Xwayland available, which gives us the click-through overlay
    # semantics we need without keeping the Qt shell's black window alive.
    return bool(os.getenv("DISPLAY"))

def overlay_enabled() -> bool:
    explicit = os.getenv("RELAYTV_X11_OVERLAY")
    if explicit is not None and explicit.strip().lower() in ("1", "true", "yes", "on"):
        return True
    raw = (os.getenv("RELAYTV_IDLE_NOTIFICATIONS_ENABLED") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    try:
        from . import state
        settings = state.get_settings() if hasattr(state, "get_settings") else {}
        if isinstance(settings, dict) and settings.get("idle_notifications_enabled") is False:
            return False
    except Exception:
        pass
    return True

def overlay_running() -> bool:
    global _OVERLAY_PROC
    return _OVERLAY_PROC is not None and _OVERLAY_PROC.poll() is None

def start_overlay() -> None:
    """Start overlay if enabled and X11 is available."""
    global _OVERLAY_PROC
    if not overlay_enabled() or not x11_session():
        return
    with _OVERLAY_LOCK:
        if overlay_running():
            return
        try:
            env = os.environ.copy()
            env.setdefault("RELAYTV_OVERLAY_CLICKTHROUGH", "1")
            # The main Qt shell may run on Wayland, but this overlay relies on
            # X11/Xwayland semantics for transparent, click-through desktop use.
            if env.get("DISPLAY"):
                env["QT_QPA_PLATFORM"] = "xcb"
                env["XDG_SESSION_TYPE"] = "x11"
                env.pop("WAYLAND_DISPLAY", None)
                xauthority = _xauthority_file(env)
                if xauthority:
                    env["XAUTHORITY"] = xauthority
            log_path = Path(env.get("RELAYTV_OVERLAY_LOG", "/tmp/relaytv-overlay.log"))
            try:
                log_handle = log_path.open("ab")
            except Exception:
                log_handle = subprocess.DEVNULL
            # Prefer module execution so it works in editable installs and within the package.
            _OVERLAY_PROC = subprocess.Popen(
                [sys.executable, "-m", "relaytv_app.overlay_app"],
                stdout=log_handle,
                stderr=log_handle,
                env=env,
            )
        except Exception:
            _OVERLAY_PROC = None

def stop_overlay() -> None:
    global _OVERLAY_PROC
    with _OVERLAY_LOCK:
        if not overlay_running():
            _OVERLAY_PROC = None
            return
        try:
            _OVERLAY_PROC.terminate()
            _OVERLAY_PROC.wait(timeout=2)
        except Exception:
            try:
                _OVERLAY_PROC.kill()
            except Exception:
                pass
        _OVERLAY_PROC = None
