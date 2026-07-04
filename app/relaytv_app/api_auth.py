# SPDX-License-Identifier: GPL-3.0-only
"""Optional bearer-token guard for write endpoints.

The API is local-first and unauthenticated by default (trusted LAN,
docs/ARCHITECTURE_REVIEW.md Finding 8). Setting ``RELAYTV_API_TOKEN``
turns on bearer auth for write requests (``POST``/``PUT``/``PATCH``/
``DELETE``) across the whole app via middleware, so future write routes
are covered by default. Reads (``GET``/``HEAD``/``OPTIONS``) — health,
status, ``/ui``, static assets — are never guarded.

The token is env-only: read through runtime config snapshots, never
persisted with settings, never returned by ``/settings``, never logged.
"""
import hmac

from .config import runtime_config

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def configured_api_token() -> str:
    """Return the operator-configured API token ("" when auth is disabled)."""
    return str(runtime_config.snapshot().raw("RELAYTV_API_TOKEN", "") or "").strip()


def bearer_token_from_header(authorization: str | None) -> str:
    """Extract the credentials from an ``Authorization: Bearer`` header."""
    value = str(authorization or "").strip()
    if not value:
        return ""
    scheme, _, credentials = value.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return credentials.strip()


def write_request_allowed(method: str, authorization: str | None) -> bool:
    """Return True when a request may proceed under the token policy."""
    token = configured_api_token()
    if not token:
        return True
    if str(method or "").upper() not in WRITE_METHODS:
        return True
    presented = bearer_token_from_header(authorization)
    if not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), token.encode("utf-8"))
