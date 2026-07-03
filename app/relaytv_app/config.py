# SPDX-License-Identifier: GPL-3.0-only
"""Shared typed environment parsing for app configuration.

Consolidates the per-module ``_env_*`` helpers (Phase 2, see
docs/ARCHITECTURE_PHASE_2_ROADMAP.md). Parsing semantics are preserved
exactly: ``env_choice`` keeps the two historical spelling sets behind the
``extended`` flag because the route-side copies accepted "enable(d)" /
"disable(d)" while the child-process copies did not.
"""
from __future__ import annotations

import os

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
