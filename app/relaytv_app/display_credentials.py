# SPDX-License-Identifier: GPL-3.0-only
"""Resolve session-scoped display credentials without persisting their names."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def _readable_file(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.R_OK)
    except OSError:
        return False


def resolve_xauthority(env: Mapping[str, str]) -> str | None:
    """Return the best current Xauthority file visible to this process.

    Display managers give the cookie a session-scoped filename: mutter writes
    ``.mutter-Xwaylandauth.*``, sddm writes ``xauth_*``, and some compositors
    keep a plain ``Xauthority`` in the runtime directory. Consider an
    explicitly configured file and credentials in the mounted XDG runtime
    directory, then select the newest readable candidate.
    """
    candidates: list[Path] = []
    explicit = (env.get("XAUTHORITY") or "").strip()
    if explicit:
        candidates.append(Path(explicit))

    runtime_dir = (env.get("XDG_RUNTIME_DIR") or "").strip()
    if runtime_dir:
        root = Path(runtime_dir)
        for pattern in (".mutter-Xwaylandauth.*", "xauth_*"):
            try:
                candidates.extend(root.glob(pattern))
            except OSError:
                pass
        candidates.append(root / "Xauthority")
        candidates.append(root / "gdm" / "Xauthority")

    try:
        candidates.sort(
            key=lambda path: path.stat().st_mtime if _readable_file(path) else -1.0,
            reverse=True,
        )
    except OSError:
        pass
    for candidate in candidates:
        if _readable_file(candidate):
            return str(candidate)

    home = (env.get("HOME") or "").strip()
    if home:
        candidate = Path(home) / ".Xauthority"
        if _readable_file(candidate):
            return str(candidate)
    return None


def refresh_display_credentials(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy an environment and replace stale Xauthority with the current file."""
    refreshed = dict(env if env is not None else os.environ)
    if not (refreshed.get("DISPLAY") or "").strip():
        return refreshed
    xauthority = resolve_xauthority(refreshed)
    if xauthority:
        refreshed["XAUTHORITY"] = xauthority
    else:
        refreshed.pop("XAUTHORITY", None)
    return refreshed
