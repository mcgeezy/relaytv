# SPDX-License-Identifier: GPL-3.0-only
"""Shared typed environment parsing and the runtime configuration service.

Consolidates the per-module ``_env_*`` helpers and owns the settings-driven
runtime configuration (docs/ARCHITECTURE.md).
Parsing semantics are preserved exactly: ``env_choice`` keeps the two
historical spelling sets behind the ``extended`` flag because the route-side
copies accepted "enable(d)" / "disable(d)" while the child-process copies did
not.

Every settings-bus mutation goes through ``RuntimeConfig.set_value``, which
updates an immutable ``SettingsSnapshot`` and mirrors to ``os.environ`` only
for the pinned subprocess contract (``MIRRORED_TO_ENV``). In-process
consumers read snapshots; the process environment is an input at startup
(``refresh_from_env``) and an output boundary for child processes, not an
in-process config bus.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

_TRUE_SPELLINGS = ("1", "true", "yes", "on")
_FALSE_SPELLINGS = ("0", "false", "no", "off")
_TRUE_SPELLINGS_EXTENDED = (*_TRUE_SPELLINGS, "enable", "enabled")
_FALSE_SPELLINGS_EXTENDED = (*_FALSE_SPELLINGS, "disable", "disabled")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in _TRUE_SPELLINGS


def env_choice(name: str, *, extended: bool = False) -> bool | None:
    value = os.getenv(name)
    if value is None:
        return None
    text = value.strip().lower()
    if text in (_TRUE_SPELLINGS_EXTENDED if extended else _TRUE_SPELLINGS):
        return True
    if text in (_FALSE_SPELLINGS_EXTENDED if extended else _FALSE_SPELLINGS):
        return False
    return None


def env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except Exception:
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def env_float(
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except Exception:
        value = float(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


def server_port() -> int:
    """The TCP port the HTTP server listens on.

    ``RELAYTV_PORT`` is the documented knob (entrypoint, mDNS, Flatpak
    launcher); ``PORT`` stays as a legacy fallback for existing deployments.
    """
    raw = (os.getenv("RELAYTV_PORT") or os.getenv("PORT") or "8787").strip()
    try:
        port = int(float(raw or "8787"))
    except Exception:
        port = 8787
    return max(1, min(65535, port))


# The settings bus: variables the app itself writes to os.environ at runtime
# (startup sync in main.py, settings apply in routes/settings.py, Jellyfin
# track preference in routes/jellyfin.py, video-mode restart in player.py).
# Kept in sync with docs/ENV_INVENTORY.md.
SETTINGS_BUS_VARS = frozenset(
    {
        "INVIDIOUS_BASE",
        "MPV_AUDIO_DEVICE",
        "RELAYTV_API_TOKEN",
        "RELAYTV_CEC",
        "RELAYTV_CEC_ENABLED",
        "RELAYTV_DEVICE_NAME",
        "RELAYTV_DRM_CONNECTOR",
        "RELAYTV_IDLE_DASHBOARD_ENABLED",
        "RELAYTV_IDLE_NOTIFICATIONS_ENABLED",
        "RELAYTV_IDLE_QR_ENABLED",
        "RELAYTV_IDLE_QR_SIZE",
        "RELAYTV_IPTV_ENABLED",
        "RELAYTV_JELLYFIN_API_KEY",
        "RELAYTV_JELLYFIN_AUDIO_LANG",
        "RELAYTV_JELLYFIN_AUTH_ENABLED",
        "RELAYTV_JELLYFIN_CLIENT_NAME",
        "RELAYTV_JELLYFIN_DEVICE_NAME",
        "RELAYTV_JELLYFIN_ENABLED",
        "RELAYTV_JELLYFIN_PASSWORD",
        "RELAYTV_JELLYFIN_PLAYBACK_MODE",
        "RELAYTV_JELLYFIN_SERVER_TYPE",
        "RELAYTV_JELLYFIN_SERVER_URL",
        "RELAYTV_JELLYFIN_SUB_LANG",
        "RELAYTV_JELLYFIN_USERNAME",
        "RELAYTV_JELLYFIN_USER_ID",
        "RELAYTV_QUALITY_CAP",
        "RELAYTV_QUALITY_MODE",
        "RELAYTV_SUB_LANG",
        "RELAYTV_UPLOAD_MAX_SIZE_GB",
        "RELAYTV_UPLOAD_RETENTION_HOURS",
        "RELAYTV_VIDEO_MODE",
        "RELAYTV_YTDLP_COOKIES",
        "USE_INVIDIOUS",
        "YTDLP_FORMAT",
    }
)

# Subprocess mirroring contract (docs/ENV_INVENTORY.md):
# settings-bus variables still mirrored to os.environ because a runtime child
# process reads them. qt_shell_app falls back to RELAYTV_DEVICE_NAME when
# persisted settings are unavailable. tests/test_env_inventory.py pins the
# child-reader set; extend this only together with that contract.
MIRRORED_TO_ENV = frozenset({"RELAYTV_DEVICE_NAME"})

_EMPTY_VALUES: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True)
class SettingsSnapshot:
    """Immutable point-in-time view of the settings-driven configuration.

    Accessors mirror the env parsing helpers so migrated consumers keep their
    per-site defaults: ``flag`` matches ``env_bool``, ``integer`` matches
    ``env_int``, ``number`` matches ``env_float``, and ``text`` matches
    ``env_str``.
    """

    values: Mapping[str, str] = field(default_factory=lambda: _EMPTY_VALUES)

    def raw(self, name: str, default: str | None = None) -> str | None:
        return self.values.get(name, default)

    def text(self, name: str, default: str = "") -> str:
        value = self.values.get(name)
        if value is None:
            return default
        return value.strip()

    def flag(self, name: str, default: bool = False) -> bool:
        value = self.values.get(name)
        if value is None:
            return default
        return value.strip().lower() in _TRUE_SPELLINGS

    def integer(
        self,
        name: str,
        default: int,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        try:
            value = int(str(self.values.get(name, str(default))).strip())
        except Exception:
            value = int(default)
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

    def number(
        self,
        name: str,
        default: float,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float:
        try:
            value = float(self.values.get(name, str(default)))
        except Exception:
            value = float(default)
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value


class RuntimeConfig:
    """Single source of truth for settings-driven runtime configuration."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, str] = {}
        self._snapshot = SettingsSnapshot()

    def refresh_from_env(self) -> SettingsSnapshot:
        """Capture the current process environment for all settings-bus vars.

        Startup-only in production (before the persisted-settings sync):
        calling this after ``set_value`` writes discards them, because applied
        values are no longer mirrored to the environment.
        """
        with self._lock:
            self._values = {
                name: os.environ[name] for name in SETTINGS_BUS_VARS if name in os.environ
            }
            self._snapshot = SettingsSnapshot(MappingProxyType(dict(self._values)))
            return self._snapshot

    def set_value(self, name: str, value: str) -> None:
        """Write one settings-bus variable to the snapshot.

        Mirrors to ``os.environ`` only for the pinned subprocess contract in
        ``MIRRORED_TO_ENV``.
        """
        text = str(value)
        if name in MIRRORED_TO_ENV:
            os.environ[name] = text
        with self._lock:
            self._values[name] = text
            self._snapshot = SettingsSnapshot(MappingProxyType(dict(self._values)))

    def snapshot(self) -> SettingsSnapshot:
        with self._lock:
            return self._snapshot


runtime_config = RuntimeConfig()
