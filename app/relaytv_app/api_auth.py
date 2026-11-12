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
import secrets
from urllib.parse import urlsplit

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

OVERLAY_TOKEN_HEADER = "x-relaytv-overlay-token"
_OVERLAY_CLIENT_TOKEN = secrets.token_urlsafe(24)
_LOOPBACK_CLIENT_HOSTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"})


def overlay_client_token() -> str:
    """Return the per-process token embedded in ``/x11/overlay`` for local heartbeats."""
    return _OVERLAY_CLIENT_TOKEN


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


def _request_authority(value: str, *, absolute: bool) -> tuple[str, int | None] | None:
    """Return a comparable host/port pair without trusting malformed headers."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw if absolute else f"//{raw}")
        host = str(parsed.hostname or "").strip().lower().rstrip(".")
        if not host:
            return None
        port = parsed.port
        if port is None and absolute:
            if parsed.scheme.lower() == "http":
                port = 80
            elif parsed.scheme.lower() == "https":
                port = 443
        return host, port
    except (TypeError, ValueError):
        return None


def _is_cross_site(
    sec_fetch_site: str | None = None,
    referer: str | None = None,
    host: str | None = None,
    origin: str | None = None,
) -> bool:
    """Return True if the request headers indicate a cross-site browser context."""
    fetch_site = str(sec_fetch_site or "").strip().lower()
    if fetch_site in {"cross-site", "same-site"}:
        return True

    raw_origin = str(origin or "").strip()
    if raw_origin.lower() == "null":
        return True
    target = _request_authority(str(host or ""), absolute=False)
    if raw_origin and target is not None:
        origin_auth = _request_authority(raw_origin, absolute=True)
        if origin_auth is None:
            return True
        cmp_target = (target[0], origin_auth[1]) if target[1] is None else target
        if origin_auth != cmp_target:
            return True

    if fetch_site in {"same-origin", "none"}:
        return False

    source = _request_authority(str(referer or ""), absolute=True)
    if source is None or target is None:
        # Headerless non-browser API clients retain the local-first behavior.
        return False
    # Host has no scheme, so normalize an omitted port to the Referer's scheme
    # default before comparing.
    if target[1] is None:
        target = (target[0], source[1])
    return source != target


def cross_site_write_request(
    method: str,
    path: str,
    *,
    sec_fetch_site: str | None = None,
    origin: str | None = None,
    referer: str | None = None,
    host: str | None = None,
) -> bool:
    """Reject cross-site browser write requests without changing headerless LAN clients."""
    if not is_write_request(method, path):
        return False
    return _is_cross_site(
        sec_fetch_site=sec_fetch_site,
        referer=referer,
        host=host,
        origin=origin,
    )


def cross_site_mutating_get(
    method: str,
    path: str,
    *,
    sec_fetch_site: str | None = None,
    referer: str | None = None,
    host: str | None = None,
    origin: str | None = None,
) -> bool:
    """Reject browser-forgeable write aliases without changing local API use."""
    if str(method or "").strip().upper() != "GET":
        return False
    if _normalized_path(path) not in MUTATING_GET_PATHS:
        return False

    return _is_cross_site(
        sec_fetch_site=sec_fetch_site,
        referer=referer,
        host=host,
        origin=origin,
    )


def overlay_client_state_allowed(
    method: str,
    path: str,
    *,
    overlay_token: str | None = None,
    client_host: str | None = None,
    testing: bool = False,
) -> bool:
    """Allow local ``/x11/overlay`` heartbeats without exposing ``RELAYTV_API_TOKEN``."""
    if str(method or "").strip().upper() != "POST":
        return False
    if _normalized_path(path) != "/x11/overlay/client_state":
        return False
    presented = str(overlay_token or "").strip()
    if not presented:
        return False
    host = str(client_host or "").strip().lower()
    if host not in _LOOPBACK_CLIENT_HOSTS and not (testing and host == "testclient"):
        return False
    return hmac.compare_digest(
        presented.encode("utf-8"),
        _OVERLAY_CLIENT_TOKEN.encode("utf-8"),
    )


def write_request_allowed(
    method: str,
    authorization: str | None,
    *,
    path: str = "",
    overlay_token: str | None = None,
    client_host: str | None = None,
    testing: bool = False,
) -> bool:
    """Return True when a request may proceed under the token policy."""
    token = configured_api_token()
    if not token:
        return True
    if not is_write_request(method, path):
        return True
    presented = bearer_token_from_header(authorization)
    if presented and hmac.compare_digest(presented.encode("utf-8"), token.encode("utf-8")):
        return True
    return overlay_client_state_allowed(
        method,
        path,
        overlay_token=overlay_token,
        client_host=client_host,
        testing=testing,
    )
