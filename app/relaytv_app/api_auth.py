# SPDX-License-Identifier: GPL-3.0-only
"""Optional bearer-token guard for write endpoints.

The API is local-first and unauthenticated by default (trusted LAN,
docs/ARCHITECTURE.md, API Trust Boundary). Setting ``RELAYTV_API_TOKEN``
turns on bearer auth for write requests (``POST``/``PUT``/``PATCH``/
``DELETE``) across the whole app via middleware, so future write routes
are covered by default. Reads (``GET``/``HEAD``/``OPTIONS``) — health,
status, ``/ui``, static assets — are never guarded.

Guarding by method is the right default because a GET is *supposed* to be
safe, but a handful of compatibility aliases predate the guard and are
not. Those are named in ``MUTATING_GET_PATHS`` and classified as writes.

Method is not the whole story for these routes. A browser will not send a
JSON ``POST`` to another origin without a CORS preflight this app never
answers, but it will happily issue a GET from an ``<img>`` or a link
prefetch on any page the operator visits, and the side effect lands even
though the response is blocked. So a mutating GET is reachable
cross-origin whether or not a token is set. Classifying one here closes
the token half of that; the other half is closed by the route not
mutating at all, which is why ``GET /share`` now redirects into ``/ui``
instead of starting playback.

The token is env-only: read through runtime config snapshots, never
persisted with settings, never returned by ``/settings``, never logged.
"""
import hmac

from .config import runtime_config

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# GET routes that change server state despite the method. Keep this as small as
# it can be: prefer removing the side effect over adding a name here.
# ``tests/test_route_inventory.py`` fails the build when a GET handler reaches a
# mutating helper without being listed.
#
# ``/snapshot`` stays because relaytv-ha still carries a GET fallback for
# servers that predate ``POST /snapshot``; it leads with the POST and sends the
# bearer token on both, so classifying the GET cannot degrade the integration.
MUTATING_GET_PATHS = frozenset({"/snapshot"})


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


def is_write_request(method: str, path: str = "") -> bool:
    """Return True when a request should be treated as a write."""
    verb = str(method or "").strip().upper()
    if verb in WRITE_METHODS:
        return True
    if verb != "GET":
        return False
    return _normalized_path(path) in MUTATING_GET_PATHS


def _normalized_path(path: str) -> str:
    """Normalize a request path for classification lookups."""
    value = str(path or "").strip()
    if not value:
        return ""
    # Starlette hands us the decoded path; trailing slashes are equivalent to
    # the app's declared routes, which never carry one.
    if len(value) > 1 and value.endswith("/"):
        value = value.rstrip("/") or "/"
    return value


def write_request_allowed(
    method: str, authorization: str | None, *, path: str = ""
) -> bool:
    """Return True when a request may proceed under the token policy."""
    token = configured_api_token()
    if not token:
        return True
    if not is_write_request(method, path):
        return True
    presented = bearer_token_from_header(authorization)
    if not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), token.encode("utf-8"))
